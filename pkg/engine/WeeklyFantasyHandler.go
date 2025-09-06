package engine

import (
	"context"
	"fmt"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"github.com/mitchwebster/botblitz/pkg/gamestate"
)

const MaxAddDropsPerRun = 10
const TransactionLogFullPath = "/tmp/weekly_transaction_log.txt"

func (e *BotEngine) runWeeklyFantasy(ctx context.Context) error {

	println("Running Weekly Fantasy")

	currentFantasyWeekInSaveData, err := e.gameStateHandler.GetCurrentFantasyWeek()
	if err != nil {
		return err
	}

	println("Current Fantasy Week in Save Data:", currentFantasyWeekInSaveData)

	// On Wednesdays run add/drop
	println("Running add/drop for current week")
	err = e.performFAABAddDrop(ctx)
	return err
}

func (e *BotEngine) performFAABAddDrop(ctx context.Context) error {
	// Fetch bots in a random order
	bots, err := e.gameStateHandler.GetBots()
	if err != nil {
		return err
	}

	// bot_id -> []AddDropSelection
	botSelectionMap := e.fetchAddDropSubmissions(ctx, bots)
	winningClaims := performFAABAddDropInternal(bots, botSelectionMap)

	// Process winning claims
	for player, claims := range winningClaims {
		for bot, claim := range claims {
			fmt.Printf("Bot %s won claim for player %s with bid %d\n", bot, player, claim.BidAmount)
			err := e.gameStateHandler.PerformAddDrop(bot, claim.PlayerToAddId, claim.PlayerToDropId, int(claim.BidAmount))
			if err != nil {
				return err
			}
		}
	}

	return nil
}

func performFAABAddDropInternal(bots []gamestate.Bot, botSelectionMap map[string][]*common.AddDropSelection) map[string]map[string]*common.AddDropSelection {
	// bot_id -> remaining budget
	remainingBudgetMap := getInitialBotBudgets(bots)

	// player_id -> bot_id who dropped them
	droppedPlayers := make(map[string]string)

	// player_id -> bot_id -> add_drop_selection
	winningClaims := make(map[string]map[string]*common.AddDropSelection)

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
				_, exists = winningClaims[claims[i].PlayerToAddId]
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
					remainingBudgetMap[bot] -= int(claims[i].BidAmount)
					winningClaims[claims[i].PlayerToAddId] = make(map[string]*common.AddDropSelection)
					winningClaims[claims[i].PlayerToAddId][bot] = claims[i]
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

func getHighestBidsByPlayerMap(botSelectionMap map[string][]*common.AddDropSelection, remainingBudgets map[string]int) map[string]map[string]int {
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

func (e *BotEngine) fetchAddDropSubmissions(ctx context.Context, bots []gamestate.Bot) map[string][]*common.AddDropSelection {
	// bot_id -> AddDropSelection
	botSelectionMap := make(map[string][]*common.AddDropSelection)

	for _, bot := range bots {
		selections, err := e.startContainerAndPerformAddDropAction(ctx, &bot)
		if err != nil {
			fmt.Printf("Failed to get selections for bot %s: %s\n", bot.ID, err)
			continue
		}

		if len(selections.AddDropSelections) > MaxAddDropsPerRun || len(selections.AddDropSelections) <= 0 {
			fmt.Printf("Invalid number of add/drop selections for bot: %s\n", bot.ID)
			continue
		}

		for _, selection := range selections.AddDropSelections {
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
				validActionArray = make([]*common.AddDropSelection, 0)
			}

			validActionArray = append(validActionArray, selection)
			botSelectionMap[bot.ID] = validActionArray
		}
	}

	return botSelectionMap
}

// func (e *BotEngine) performWeeklyFantasyActions(ctx context.Context) error {
// 	fmt.Println("Playing Weekly Fantasy!!")

// 	e.makePreviouslyOnHoldPlayersAvailable()

// 	err := e.setupInitialWaiverPriorityIfNeeded()
// 	if err != nil {
// 		return err
// 	}

// 	e.sortBotsAccordingToWaiverPriority()

// 	teamIdToPlayerMap := buildTeamMap(e.gameState)

// 	for run := 0; run < AllowedAddsPerRun; run++ {
// 		fmt.Printf("Running %d round of add/drop actions\n", run)

// 		botsWhoTookActionSlice := make([]*common.Bot, 0, len(e.gameState.Bots))
// 		botsWhoTookActionMap := make(map[string]bool)

// 		for _, bot := range e.gameState.Bots {
// 			fmt.Println("Processing add/drops for: " + bot.Id)
// 			e.gameState.CurrentBotTeamId = bot.Id
// 			tookAction := e.performAddDrop(ctx, bot, teamIdToPlayerMap)
// 			if tookAction {
// 				botsWhoTookActionMap[bot.Id] = true
// 				botsWhoTookActionSlice = append(botsWhoTookActionSlice, bot)
// 			}
// 		}

// 		newBotSlice := make([]*common.Bot, 0, len(e.gameState.Bots))

// 		// Add the bots who did not take action first
// 		for _, bot := range e.gameState.Bots {
// 			_, tookAction := botsWhoTookActionMap[bot.Id]
// 			if !tookAction {
// 				newBotSlice = append(newBotSlice, bot)
// 			}
// 		}

// 		// Add the bots who did take an action
// 		newBotSlice = append(newBotSlice, botsWhoTookActionSlice...)

// 		e.gameState.Bots = newBotSlice
// 	}

// 	fmt.Fprintf(&e.weeklyFantasyTransactionLog, "[Final Bot Order]\n")

// 	// Update the waiver order for next time
// 	for i, bot := range e.gameState.Bots {
// 		bot.CurrentWaiverPriority = uint32(i)
// 		fmt.Fprintf(&e.weeklyFantasyTransactionLog, "\t"+bot.Id+":"+bot.FantasyTeamName+"\n")
// 	}

// 	transactionLogStr := e.weeklyFantasyTransactionLog.String()
// 	fmt.Println(transactionLogStr)

// 	err = e.saveTransactionLogToFile(transactionLogStr)
// 	if err != nil {
// 		return err
// 	}

// 	return nil
// }

// func (e *BotEngine) saveTransactionLogToFile(transactionLogStr string) error {
// 	file, err := os.Create(TransactionLogFullPath)
// 	if err != nil {
// 		fmt.Println("Error creating file:", err)
// 		return err
// 	}
// 	defer file.Close() // Ensure the file is closed after writing

// 	content := []byte(transactionLogStr)
// 	if _, err := file.Write(content); err != nil {
// 		return err
// 	}

// 	return nil
// }

// func (e *BotEngine) makePreviouslyOnHoldPlayersAvailable() {
// 	for _, player := range e.gameState.Players {
// 		if player.Status.Availability == common.PlayerStatus_ON_HOLD {
// 			player.Status.Availability = common.PlayerStatus_AVAILABLE
// 			fmt.Println(player.FullName + " is now available!")
// 		}
// 	}
// }

// func (e *BotEngine) setupInitialWaiverPriorityIfNeeded() error {
// 	// check if all of the teams are missing a waiver priority
// 	for _, bot := range e.gameState.Bots {
// 		if bot.CurrentWaiverPriority != 0 {
// 			return nil
// 		}
// 	}

// 	fmt.Println("Initializing team waiver priorities")
// 	n := len(e.gameState.Bots)
// 	indices := make([]int, 0, n)

// 	for i := 0; i < n; i++ {
// 		indices = append(indices, i)
// 	}

// 	rand.Shuffle(len(indices), func(i, j int) {
// 		indices[i], indices[j] = indices[j], indices[i]
// 	})

// 	for i := 0; i < n; i++ {
// 		bot := e.gameState.Bots[i]
// 		bot.CurrentWaiverPriority = uint32(indices[i])
// 	}

// 	return nil
// }

// func (e *BotEngine) sortBotsAccordingToWaiverPriority() {
// 	n := len(e.gameState.Bots)
// 	newBotList := make([]*common.Bot, n)

// 	teamIdToBotMap := make(map[string]*common.Bot)
// 	for _, bot := range e.gameState.Bots {
// 		teamIdToBotMap[bot.Id] = bot
// 	}

// 	for _, team := range e.gameState.Bots {
// 		bot := teamIdToBotMap[team.Id]
// 		newBotList[team.CurrentWaiverPriority] = bot
// 	}

// 	fmt.Fprintf(&e.weeklyFantasyTransactionLog, "[Initial Bot Order]\n")

// 	// Print the new order list for this round
// 	for _, bot := range newBotList {
// 		fmt.Println(bot.Id + ":" + bot.Id)
// 		fmt.Fprintf(&e.weeklyFantasyTransactionLog, "\t"+bot.Id+":"+bot.FantasyTeamName+"\n")
// 	}

// 	fmt.Fprintf(&e.weeklyFantasyTransactionLog, "\n")

// 	e.gameState.Bots = newBotList
// }

// func (e *BotEngine) performAddDrop(ctx context.Context, bot *common.Bot, teamIdToPlayerMap map[string][]*common.Player) bool {
// 	selection, err := e.startContainerAndPerformAddDropAction(ctx, bot)
// 	if err != nil {
// 		fmt.Println(err)
// 		return false
// 	}

// 	attemptPlayerDrop := len(selection.PlayerToDropId) > 0
// 	attemptPlayerAdd := len(selection.PlayerToAddId) > 0

// 	if attemptPlayerDrop {
// 		droppedPlayer := e.handleDropPlayer(selection.PlayerToDropId, bot, teamIdToPlayerMap)
// 		if droppedPlayer != nil {
// 			fmt.Fprintf(&e.weeklyFantasyTransactionLog, "[%s]: successfully dropped %s\n", bot.Id, droppedPlayer.FullName)
// 		} else {
// 			fmt.Fprintf(&e.weeklyFantasyTransactionLog, "[%s]: failed to drop %s\n", bot.Id, selection.PlayerToDropId)
// 		}
// 	}

// 	if attemptPlayerAdd {
// 		addedPlayer := e.handleAddPlayer(selection.PlayerToAddId, bot, teamIdToPlayerMap)
// 		if addedPlayer != nil {
// 			fmt.Fprintf(&e.weeklyFantasyTransactionLog, "[%s]: successfully added %s\n", bot.Id, addedPlayer.FullName)
// 		} else {
// 			fmt.Fprintf(&e.weeklyFantasyTransactionLog, "[%s]: failed to add %s\n", bot.Id, selection.PlayerToAddId)
// 		}
// 	}

// 	if attemptPlayerDrop || attemptPlayerAdd {
// 		return true
// 	}

// 	return false
// }

// func (e *BotEngine) handleDropPlayer(playerId string, bot *common.Bot, teamIdToPlayerMap map[string][]*common.Player) *common.Player {
// 	player, err := FindPlayerById(playerId, e.gameState)
// 	if err != nil {
// 		fmt.Println(err)
// 		return nil
// 	}

// 	if player.Status.Availability != common.PlayerStatus_DRAFTED {
// 		fmt.Println("Cannot drop a player that is not drafted to a team")
// 		return nil
// 	}

// 	if player.Status.CurrentTeamBotId != bot.Id {
// 		fmt.Println("Cannot drop a player that is not on this team already")
// 		return nil
// 	}

// 	player.Status.CurrentTeamBotId = ""                      // No team now
// 	player.Status.Availability = common.PlayerStatus_ON_HOLD // On hold until next reconciliation
// 	player.Status.PickChosen = 0                             // reset pick chosen as it is no longer true

// 	// Remove this player from the team map
// 	currentTeam := teamIdToPlayerMap[bot.Id]
// 	indexToRemove := -1
// 	for i, teamPlayer := range currentTeam {
// 		if player.Id == teamPlayer.Id {
// 			indexToRemove = i
// 			break
// 		}
// 	}

// 	resultingSlice := append(currentTeam[:indexToRemove], currentTeam[indexToRemove+1:]...)
// 	teamIdToPlayerMap[bot.Id] = resultingSlice

// 	return player
// }

// func (e *BotEngine) handleAddPlayer(playerId string, bot *common.Bot, teamIdToPlayerMap map[string][]*common.Player) *common.Player {
// 	player, err := FindPlayerById(playerId, e.gameState)
// 	if err != nil {
// 		fmt.Println(err)
// 		return nil
// 	}

// 	if !e.checkIfPlayerCanBeAddedToTeam(player, bot, teamIdToPlayerMap) {
// 		return nil
// 	}

// 	currentTeam := teamIdToPlayerMap[bot.Id]

// 	player.Status.CurrentTeamBotId = bot.Id                  // Assign new team
// 	player.Status.Availability = common.PlayerStatus_DRAFTED // Mark them as drafted
// 	player.Status.PickChosen = 0                             // Not applicable in add/drop

// 	resultingSllice := append(currentTeam, player)
// 	teamIdToPlayerMap[bot.Id] = resultingSllice
// 	return player
// }

// func buildTeamMap(gameState *common.GameState) map[string][]*common.Player {
// 	playerMap := make(map[string][]*common.Player)

// 	for _, player := range gameState.Players {
// 		teamKey := player.Status.CurrentTeamBotId
// 		if teamKey == "" {
// 			continue // do not keep track of players without a team
// 		}

// 		value, exists := playerMap[teamKey]
// 		if exists {
// 			appendedList := append(value, player)
// 			playerMap[teamKey] = appendedList
// 		} else {
// 			playerMap[teamKey] = []*common.Player{player}
// 		}
// 	}

// 	return playerMap
// }

// func (e *BotEngine) checkIfPlayerCanBeAddedToTeam(player *common.Player, bot *common.Bot, teamIdToPlayerMap map[string][]*common.Player) bool {
// 	if player.Status.Availability != common.PlayerStatus_AVAILABLE {
// 		fmt.Println("Cannot add a player that is not available")
// 		return false
// 	}

// 	// Defense in depth check
// 	if player.Status.CurrentTeamBotId != "" {
// 		fmt.Println("Cannot add a player that already belongs to a team")
// 		return false
// 	}

// 	totalTeamSize := len(e.gameState.LeagueSettings.SlotsPerTeam)
// 	currentTeam := teamIdToPlayerMap[bot.Id]
// 	if len(currentTeam)+1 > totalTeamSize {
// 		fmt.Println("Cannot add player, team would be too large")
// 		return false
// 	}

// 	// TODO: do we need to check any slot issues - it might be ok if they are just non scoring?

// 	return true
// }

// func getCurrentWeek(year uint32) int {
// 	// Weeks since first day of football: 9/5/{year} at 8am UTC (roughly when this runs)
// 	pastDate := time.Date(int(year), 9, 5, 8, 0, 0, 0, time.UTC)
// 	now := time.Now()
// 	duration := now.Sub(pastDate)
// 	return int(math.Floor(duration.Hours()/(24*7))) + 1
// }
