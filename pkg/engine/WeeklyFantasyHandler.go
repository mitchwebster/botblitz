package engine

import (
	"context"
	"fmt"
	"os"
	"strings"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"github.com/mitchwebster/botblitz/pkg/gamestate"
)

const MaxAddDropsPerRun = 10
const TransactionLogFullPath = "/tmp/weekly_transaction_log.txt"

func (e *BotEngine) performWeeklyFantasyActions(ctx context.Context) error {
	// TODO: add trades, etc.
	return e.performFAABAddDrop(ctx)
}

func (e *BotEngine) performFAABAddDrop(ctx context.Context) error {
	// Fetch bots in a random order
	bots, err := e.gameStateHandler.GetBots()
	if err != nil {
		return err
	}

	// bot_id -> []AddDropSelection
	botSelectionMap, err := e.fetchAddDropSubmissions(ctx, bots)
	if err != nil {
		return err
	}

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap)

	// Process winning claims
	for bot, claims := range winningClaims {
		for _, claim := range claims {
			fmt.Printf("Bot %s won claim for player %s with bid %d\n", bot, claim.PlayerToAddId, claim.BidAmount)
			err := e.gameStateHandler.PerformAddDrop(bot, claim.PlayerToAddId, claim.PlayerToDropId, int(claim.BidAmount))
			if err != nil {
				return err
			}
		}
	}

	e.summarizeAddDropResults(bots, botSelectionMap, winningClaims)
	return err
}

func (e *BotEngine) summarizeAddDropResults(bots []gamestate.Bot, botSelectionMap map[string][]*common.WaiverClaim, winningClaims map[string][]*common.WaiverClaim) {
	var builder strings.Builder

	botMap := make(map[string]gamestate.Bot)
	for _, bot := range bots {
		botMap[bot.ID] = bot
	}

	playerCache := make(map[string]*gamestate.Player)

	builder.WriteString("--- Add/Drop Summary --- \n")

	builder.WriteString("--- Valid claims submitted --- \n")
	for botID, claims := range botSelectionMap {
		builder.WriteString(fmt.Sprintf("Bot %s (%s):\n", botMap[botID].Name, botMap[botID].Owner))
		for _, claim := range claims {
			if _, ok := playerCache[claim.PlayerToAddId]; !ok {
				addedPlayer, err := e.gameStateHandler.GetPlayerById(claim.PlayerToAddId)
				if err != nil {
					fmt.Println("Failed to find added player by ID: ", err)
					continue
				}

				playerCache[claim.PlayerToAddId] = addedPlayer
			}

			if _, ok := playerCache[claim.PlayerToDropId]; !ok {
				droppedPlayer, err := e.gameStateHandler.GetPlayerById(claim.PlayerToDropId)
				if err != nil {
					fmt.Println("Failed to find dropped player by ID: ", err)
					continue
				}

				playerCache[claim.PlayerToDropId] = droppedPlayer
			}

			builder.WriteString(fmt.Sprintf("\t- Add: %s , Drop: %s, Bid: %d\n", playerCache[claim.PlayerToAddId].FullName, playerCache[claim.PlayerToDropId].FullName, claim.BidAmount))
		}
	}
	builder.WriteString("------ \n")

	builder.WriteString("--- Winning Claims --- \n")
	for botID, claims := range winningClaims {
		builder.WriteString(fmt.Sprintf("Bot %s (%s):\n", botMap[botID].Name, botMap[botID].Owner))
		for _, claim := range claims {
			addedPlayer := playerCache[claim.PlayerToAddId]
			droppedPlayer := playerCache[claim.PlayerToDropId]
			builder.WriteString(fmt.Sprintf("\t- Added: %s , Dropped: %s, Bid: %d\n", addedPlayer.FullName, droppedPlayer.FullName, claim.BidAmount))
		}
	}
	builder.WriteString("------ \n")

	summaryStr := builder.String()
	fmt.Println(summaryStr)
	err := e.saveTransactionLogToFile(summaryStr)
	if err != nil {
		fmt.Println("Failed to save transaction log to file: ", err)
	}
}

func copySelectionMap(botSelectionMap map[string][]*common.WaiverClaim) map[string][]*common.WaiverClaim {
	newMap := make(map[string][]*common.WaiverClaim)
	for k, v := range botSelectionMap {
		newMap[k] = make([]*common.WaiverClaim, len(v))
		copy(newMap[k], v)
	}
	return newMap
}

func performFAABAddDropInternal(bots []gamestate.Bot, originalSelectionMap map[string][]*common.WaiverClaim) map[string][]*common.WaiverClaim {
	// bot_id -> remaining budget
	remainingBudgetMap := getInitialBotBudgets(bots)

	// player_id -> bot_id who added them
	playersAlreadyAdded := make(map[string]string)

	// player_id -> bot_id who dropped them
	droppedPlayers := make(map[string]string)

	// bot_id -> waiver_claim
	winningClaims := make(map[string][]*common.WaiverClaim)

	botSelectionMap := copySelectionMap(originalSelectionMap)

	anyClaims := true

	for anyClaims {
		// player_id -> current_highest_bid (unvalidated)
		highestBidsByPlayer := getHighestBidsByPlayerMap(botSelectionMap, remainingBudgetMap)
		anyClaims = len(highestBidsByPlayer) > 0

		for i := 0; i < MaxAddDropsPerRun; i++ {
			foundWinner := false

			// go through this round of claims and see if any are winners
			for bot, claims := range botSelectionMap {
				if len(claims) == 0 || i >= len(claims) {
					continue
				}

				// Validate this bot can actually pay for the player
				if claims[i].BidAmount > uint32(remainingBudgetMap[bot]) {
					botSelectionMap[bot] = append(claims[:i], claims[i+1:]...)
					continue
				}

				// if the player for this claim has already been dropped, then skip this claim
				_, exists := droppedPlayers[claims[i].PlayerToDropId]
				if exists {
					botSelectionMap[bot] = append(claims[:i], claims[i+1:]...)
					continue
				}

				// if the player this claim is looking for has already been claimed then drop this claim
				_, exists = playersAlreadyAdded[claims[i].PlayerToAddId]
				if exists {
					botSelectionMap[bot] = append(claims[:i], claims[i+1:]...)
					continue
				}

				fmt.Printf("Validated claim successfully %s\n", claims[i])

				highestBidForThisPlayerMap := highestBidsByPlayer[claims[i].PlayerToAddId]
				winnerBot, winningBidAmount := getWinningBotAndBidAmount(highestBidForThisPlayerMap)
				println("Winning bot for this player is ", winnerBot, " with bid ", winningBidAmount)

				if uint32(winningBidAmount) == claims[i].BidAmount && winnerBot == bot {
					fmt.Printf("Found the winning bot! %s with a bid of %d\n", bot, claims[i].BidAmount)
					// We found the highest bid for this player in priority order
					foundWinner = true
					droppedPlayers[claims[i].PlayerToDropId] = bot
					playersAlreadyAdded[claims[i].PlayerToAddId] = bot
					remainingBudgetMap[bot] -= int(claims[i].BidAmount)

					_, exists := winningClaims[bot]
					if !exists {
						winningClaims[bot] = make([]*common.WaiverClaim, 0)
					}

					winningClaims[bot] = append(winningClaims[bot], claims[i])
					botSelectionMap[bot] = append(claims[:i], claims[i+1:]...)
					break
				}
			}

			if foundWinner {
				break
			}
		}
	}

	return winningClaims
}

func getWinningBotAndBidAmount(highestBidForThisPlayerMap map[string]int) (string, int) {
	bidValue := -1
	worstTeam := "" // TODO: figure out how to get the worst team
	for bot, bid := range highestBidForThisPlayerMap {
		bidValue = bid
		worstTeam = bot
	}

	return worstTeam, bidValue
}

func getHighestBidsByPlayerMap(botSelectionMap map[string][]*common.WaiverClaim, remainingBudgets map[string]int) map[string]map[string]int {
	highestBids := make(map[string]map[string]int)

	for bot, selections := range botSelectionMap {
		for _, selection := range selections {
			_, exists := highestBids[selection.PlayerToAddId]
			if !exists {
				highestBids[selection.PlayerToAddId] = make(map[string]int)
			}

			bidAmount := int(selection.BidAmount)
			currentMap, _ := highestBids[selection.PlayerToAddId]

			previousMaxBid := -1
			for _, curValue := range currentMap {
				previousMaxBid = curValue
				break
			}

			// Check if the bot can afford this bid
			if bidAmount > remainingBudgets[bot] {
				continue
			}

			if bidAmount > previousMaxBid {
				// Clear the old entries, make a new one
				highestBids[selection.PlayerToAddId] = make(map[string]int)
				highestBids[selection.PlayerToAddId][bot] = bidAmount
			} else if bidAmount == previousMaxBid {
				// Add this entry
				highestBids[selection.PlayerToAddId][bot] = bidAmount
			}
		}
	}

	return highestBids
}

func getInitialBotBudgets(bots []gamestate.Bot) map[string]int {
	initialBudgets := make(map[string]int)

	for _, bot := range bots {
		initialBudgets[bot.ID] = bot.RemainingWaiverBudget
	}

	return initialBudgets
}

func (e *BotEngine) fetchAddDropSubmissions(ctx context.Context, bots []gamestate.Bot) (map[string][]*common.WaiverClaim, error) {
	// bot_id -> []WaiverClaim
	botSelectionMap := make(map[string][]*common.WaiverClaim)

	for _, bot := range bots {
		// TODO: in the future this may need to change to handle trades, etc. as well

		// Need to update current_bot_id so bots know who they are
		err := e.gameStateHandler.SetCurrentBotTeamId(bot.ID)
		if err != nil {
			return botSelectionMap, err
		}

		selections, err := e.startContainerAndPerformWeeklyFantasyActions(ctx, &bot)
		if err != nil {
			fmt.Printf("Failed to get selections for bot %s: %s\n", bot.ID, err)
			continue
		}

		if len(selections.WaiverClaims) <= 0 {
			fmt.Printf("No add drop selections submitted for bot: %s\n", bot.ID)
			continue
		}

		if len(selections.WaiverClaims) > MaxAddDropsPerRun {
			fmt.Printf("WARNING: bot (%s) submitted %d waiver claims, only considering first %d\n", bot.ID, len(selections.WaiverClaims), MaxAddDropsPerRun)
			selections.WaiverClaims = selections.WaiverClaims[:MaxAddDropsPerRun]
		}

		for _, selection := range selections.WaiverClaims {
			// validate player to drop is valid
			dropPlayerName, err := e.validatePlayerCanBeDropped(selection.PlayerToDropId, bot.ID)
			if err != nil {
				fmt.Printf("Invalid player (%s) to drop for bot %s: %s\n", selection.PlayerToDropId, bot.ID, err)
				continue
			}

			// validate player to add is valid
			playerToAdd, err := e.getPlayerAndValidateDraftEligibility(selection.PlayerToAddId)
			if err != nil {
				fmt.Printf("Invalid player (%s) to add for bot %s: %s\n", selection.PlayerToAddId, bot.ID, err)
				continue
			}

			// validate bid amount is possible
			if selection.BidAmount < 0 || selection.BidAmount > uint32(bot.RemainingWaiverBudget) {
				fmt.Printf("Invalid bid amount for bot %s: %d\n", bot.ID, selection.BidAmount)
				continue
			}

			// If we reach here, the player is valid
			fmt.Printf("Recieved add drop from bot %s -> Add %s , Drop %s. Bid: %d\n", bot.ID, playerToAdd.FullName, dropPlayerName, selection.BidAmount)

			validActionArray, exists := botSelectionMap[bot.ID]
			if !exists {
				validActionArray = make([]*common.WaiverClaim, 0)
			}

			validActionArray = append(validActionArray, selection)
			botSelectionMap[bot.ID] = validActionArray
		}
	}

	return botSelectionMap, nil
}

func (e *BotEngine) saveTransactionLogToFile(transactionLogStr string) error {
	file, err := os.Create(TransactionLogFullPath)
	if err != nil {
		fmt.Println("Error creating file:", err)
		return err
	}
	defer file.Close() // Ensure the file is closed after writing

	content := []byte(transactionLogStr)
	if _, err := file.Write(content); err != nil {
		return err
	}

	return nil
}
