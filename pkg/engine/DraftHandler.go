package engine

import (
	"context"
	"fmt"
	"math/rand"
	"strconv"

	common "github.com/mitchwebster/botblitz/pkg/common"
)

func (e *BotEngine) runDraft(ctx context.Context) error {
	err := e.initializeDraftSheet()
	if err != nil {
		return err
	}

	leagueSettings, err := e.gameStateHandler.GetLeagueSettings()
	if err != nil {
		return err
	}

	bots, err := e.gameStateHandler.GetBots()
	if err != nil {
		return err
	}

	curRound := 1
	for curRound <= int(leagueSettings.TotalRounds) {
		fmt.Printf("ROUND %d HAS STARTED!\n", curRound)

		index := 0
		increment := 1
		arrayEdge := int(leagueSettings.NumTeams) - 1

		shouldUseReverseOrder := (curRound % 2) == 0
		if shouldUseReverseOrder {
			index = arrayEdge
			increment = -1
		}

		for index >= 0 && index <= arrayEdge {
			// check if ctx is canceled
			if err := ctx.Err(); err != nil {
				return err
			}

			curBot := bots[index]
			e.gameStateHandler.SetCurrentBotTeamId(curBot.Id)
			fmt.Printf("\n-----------------------------------------\n")
			err := e.performDraftAction(ctx, curBot, index)
			fmt.Printf("\n-----------------------------------------\n")
			if err != nil {
				return err
			}
			index += increment
			// TODO: store this
			// e.gameState.CurrentDraftPick += 1
		}

		curRound += 1
	}

	return nil
}

func (e *BotEngine) initializeDraftSheet() error {
	if e.sheetsClient == nil {
		if e.settings.VerboseLoggingEnabled {
			fmt.Println("Skipping Draft Sheet setup as no client was provided")
		}

		return nil // no-op if client is not created
	}

	err := e.sheetsClient.CreateNewDraftSheet()
	if err != nil {
		return err
	}

	fmt.Println("Created Draft Sheet")

	err = e.sheetsClient.WriteContentToCell(IntialRow, InitialCol, "Round / Team")
	if err != nil {
		return err
	}

	leagueSettings, err := e.gameStateHandler.GetLeagueSettings()
	if err != nil {
		return err
	}

	bots, err := e.gameStateHandler.GetBots()
	if err != nil {
		return err
	}

	for i := 1; i <= int(leagueSettings.TotalRounds); i++ {
		content := strconv.Itoa(i)
		err = e.sheetsClient.WriteContentToCell(IntialRow+i, InitialCol, content)
		if err != nil {
			return err
		}
	}

	for i := 1; i <= len(bots); i++ {
		bot := bots[i-1]
		if err != nil {
			return err
		}

		content := bot.FantasyTeamName + "(" + bot.Owner + ")"
		newCol := rune(int(InitialCol) + i)
		err = e.sheetsClient.WriteContentToCell(IntialRow, newCol, content)
		if err != nil {
			return err
		}
	}

	return nil
}

func (e *BotEngine) performDraftAction(ctx context.Context, bot *common.Bot, currentBotIndex int) error {
	// pickNum, err := e.gameStateHandler.GetCurrentDraftPick()
	// if err != nil {
	// 	return err
	// }

	// leagueSettings, err := e.gameStateHandler.GetLeagueSettings()
	// if err != nil {
	// 	return err
	// }

	// fmt.Printf("[Pick: %d] %s will choose next...", pickNum, bot.FantasyTeamName)

	// playerIdFromBot, err := e.startContainerAndPerformDraftAction(ctx, bot)
	// if err != nil {
	// 	fmt.Println("Failed to get a response from bot")
	// 	fmt.Println(err)
	// } else {
	// 	fmt.Println("Received a response from bot")
	// }

	// summary, err := validateAndMakeDraftPick(bot, playerIdFromBot, e.gameState)

	// if err != nil {
	// 	fmt.Println("Failed to run draft using bot")
	// 	fmt.Println(err)
	// 	summary, err = draftPlayerOnInvalidResponse(bot, e.gameState)
	// 	if err != nil {
	// 		return err
	// 	}
	// 	summary += string('*')
	// }

	// err = registerPickInSheets(summary, pickNum, int(leagueSettings.NumTeams), currentBotIndex, e.sheetsClient)
	// if err != nil {
	// 	fmt.Println("Failed to write content to Google Sheets")
	// 	return err
	// }

	return nil
}

func (e *BotEngine) validateDraftState() error {
	leagueSettings, err := e.gameStateHandler.GetLeagueSettings()
	if err != nil {
		return err
	}

	if !leagueSettings.IsSnakeDraft {
		return fmt.Errorf("I only know how to snake draft")
	}

	if leagueSettings.TotalRounds <= 0 {
		return fmt.Errorf("Must have at least one round")
	}

	if leagueSettings.NumTeams <= 0 {
		return fmt.Errorf("Must have have at least one team")
	}

	return nil
}

func validateAndMakeDraftPick(bot *common.Bot, playerId string, gameState *common.GameState) (string, error) {
	player, err := FindPlayerById(playerId, gameState)
	if err != nil {
		return "", err
	}

	if player.Status.Availability == common.PlayerStatus_DRAFTED {
		return "", fmt.Errorf("Cannot draft player again")
	}

	player.Status.CurrentTeamBotId = bot.Id
	player.Status.Availability = common.PlayerStatus_DRAFTED
	player.Status.PickChosen = gameState.CurrentDraftPick

	fmt.Printf("With the %d pick of the bot draft, %s (%s) has selected: %s\n", gameState.CurrentDraftPick, bot.FantasyTeamName, bot.Owner, player.FullName)

	summary := player.FullName + "(" + player.AllowedPositions[0] + ")"

	return summary, nil
}

func draftPlayerOnInvalidResponse(bot *common.Bot, gameState *common.GameState) (string, error) {
	fmt.Println("Auto-drafting due to failure")
	playerCount := len(gameState.Players)
	index := rand.Intn(playerCount)
	hasLooped := false
	for index < playerCount && !hasLooped {
		player := gameState.Players[index]
		if player.Status.Availability == common.PlayerStatus_AVAILABLE {
			summary, err := validateAndMakeDraftPick(bot, player.Id, gameState)
			return summary, err
		}

		index += 1
		if index == playerCount && !hasLooped {
			hasLooped = true
			index = 0
		}
	}

	return "", fmt.Errorf("Could not find a valid player to auto-draft")
}

func registerPickInSheets(summary string, currentDraftPick int, teamCount int, currentBotIndex int, client *SheetsClient) error {
	if client == nil {
		return nil
	}

	zero_based_round := ((currentDraftPick - 1) / teamCount) + 1
	newCol := rune(int(InitialCol) + currentBotIndex + 1)

	err := client.WriteContentToCell(IntialRow+zero_based_round, newCol, summary)
	return err
}
