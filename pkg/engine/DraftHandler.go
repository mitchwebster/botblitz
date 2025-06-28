package engine

import (
	"context"
	"fmt"
	"math/rand"
	"strconv"
	"time"

	common "github.com/mitchwebster/botblitz/pkg/common"
)

func (e *BotEngine) runDraft(ctx context.Context) error {
	e.latencies = make(map[string]time.Duration)
	startTime := time.Now()

	err := e.initializeDraftSheet()
	if err != nil {
		return err
	}

	setupEndTime := time.Now()
	e.latencies["Setup"] = setupEndTime.Sub(startTime)

	dataBytes, err := FetchDataBytes(int(e.gameState.LeagueSettings.Year), int(e.gameState.CurrentFantasyWeek))
	if err != nil {
		return err
	}
	e.dataBytes = dataBytes
	curRound := 1
	for curRound <= int(e.gameState.LeagueSettings.TotalRounds) {
		fmt.Printf("ROUND %d HAS STARTED!\n", curRound)

		index := 0
		increment := 1
		arrayEdge := len(e.bots) - 1

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

			curBot := e.bots[index]
			e.gameState.CurrentBotTeamId = curBot.FantasyTeamId
			fmt.Printf("\n-----------------------------------------\n")
			err := e.performDraftAction(ctx, curBot, index)
			fmt.Printf("\n-----------------------------------------\n")
			if err != nil {
				return err
			}
			index += increment
			e.gameState.CurrentDraftPick += 1
		}

		curRound += 1
	}

	SaveGameState(e.gameState)

	endTime := time.Now()
	e.latencies["TotalDraft"] = endTime.Sub(startTime)

	e.logDraftLatencies()

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

	for i := 1; i <= int(e.gameState.LeagueSettings.TotalRounds); i++ {
		content := strconv.Itoa(i)
		err = e.sheetsClient.WriteContentToCell(IntialRow+i, InitialCol, content)
		if err != nil {
			return err
		}
	}

	for i := 1; i <= len(e.bots); i++ {
		bot := e.bots[i-1]
		team, err := FindCurrentTeamById(bot.FantasyTeamId, e.gameState)
		if err != nil {
			return err
		}

		content := team.Name + "(" + team.Owner + ")"
		newCol := rune(int(InitialCol) + i)
		err = e.sheetsClient.WriteContentToCell(IntialRow, newCol, content)
		if err != nil {
			return err
		}
	}

	return nil
}

func (e *BotEngine) performDraftAction(ctx context.Context, bot *common.Bot, currentBotIndex int) error {

	fmt.Printf("[Pick: %d] Fantasy Team (%s) will choose next...", e.gameState.CurrentDraftPick, bot.FantasyTeamId)

	playerIdFromBot, err := e.startContainerAndPerformDraftAction(ctx, bot)

	if err != nil {
		fmt.Println("Failed to get a response from bot")
	} else {
		fmt.Println("Received a response from bot")
	}

	summary, err := validateAndMakeDraftPick(bot.FantasyTeamId, playerIdFromBot, e.gameState)

	if err != nil {
		fmt.Println("Failed to run draft using bot")
		fmt.Println(err)
		summary, err = draftPlayerOnInvalidResponse(bot.FantasyTeamId, e.gameState)
		if err != nil {
			return err
		}
		summary += string('*')
	}

	err = registerPickInSheets(summary, int(e.gameState.CurrentDraftPick), len(e.bots), currentBotIndex, e.sheetsClient)
	if err != nil {
		fmt.Println("Failed to write content to Google Sheets")
		return err
	}
	return nil
}

func (e *BotEngine) validateDraftState() error {
	if !e.gameState.LeagueSettings.IsSnakeDraft {
		return fmt.Errorf("I only know how to snake draft")
	}

	if e.gameState.LeagueSettings.TotalRounds <= 0 {
		return fmt.Errorf("Must have at least one round")
	}

	if len(e.gameState.Teams) <= 0 {
		return fmt.Errorf("Must have have at least one team")
	}

	if len(e.bots) <= 0 {
		return fmt.Errorf("Must have have at least one bot")
	}

	if len(e.bots) != len(e.gameState.Teams) {
		return fmt.Errorf("Must have a bot for every team")
	}

	return nil
}

func validateAndMakeDraftPick(fantasyTeamId string, playerId string, gameState *common.GameState) (string, error) {
	player, err := FindPlayerById(playerId, gameState)
	if err != nil {
		return "", err
	}

	if player.Status.Availability == common.PlayerStatus_DRAFTED {
		return "", fmt.Errorf("Cannot draft player again")
	}

	team, err := FindCurrentTeamById(fantasyTeamId, gameState)
	if err != nil {
		return "", err
	}

	player.Status.CurrentFantasyTeamId = team.Id
	player.Status.Availability = common.PlayerStatus_DRAFTED
	player.Status.PickChosen = gameState.CurrentDraftPick

	fmt.Printf("With the %d pick of the bot draft, %s (%s) has selected: %s\n", gameState.CurrentDraftPick, team.Name, team.Owner, player.FullName)

	summary := player.FullName + "(" + player.AllowedPositions[0] + ")"

	return summary, nil
}

func draftPlayerOnInvalidResponse(fantasyTeamId string, gameState *common.GameState) (string, error) {
	fmt.Println("Auto-drafting due to failure")
	playerCount := len(gameState.Players)
	index := rand.Intn(playerCount)
	hasLooped := false
	for index < playerCount && !hasLooped {
		player := gameState.Players[index]
		if player.Status.Availability == common.PlayerStatus_AVAILABLE {
			summary, err := validateAndMakeDraftPick(fantasyTeamId, player.Id, gameState)
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

func (e *BotEngine) logDraftLatencies() {
	fmt.Println("\n--- Draft Latency Debug ---")
	for key, latency := range e.latencies {
		fmt.Printf("%s: %v\n", key, latency)
	}
	fmt.Println("--- Draft Latency Debug ---")
}
