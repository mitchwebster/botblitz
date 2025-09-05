package engine

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"github.com/mitchwebster/botblitz/pkg/gamestate"
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

			err = e.gameStateHandler.IncrementDraftPick()
			if err != nil {
				println("Failed to increment draft pick number")
			}
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
	pickNum, err := e.gameStateHandler.GetCurrentDraftPick()
	if err != nil {
		return err
	}

	leagueSettings, err := e.gameStateHandler.GetLeagueSettings()
	if err != nil {
		return err
	}

	fmt.Printf("[Pick: %d] %s will choose next...\n", pickNum, bot.FantasyTeamName)

	summary := ""
	playerIdFromBot, err := e.startContainerAndPerformDraftAction(ctx, bot)
	if err != nil {
		fmt.Println("Failed to get a response from bot")
		fmt.Println(err)
	}

	summary, err = e.validateAndMakeDraftPick(bot, playerIdFromBot, pickNum)

	if err != nil {
		fmt.Println("Failed to run draft using bot")
		fmt.Println(err)
		summary, err = e.draftPlayerOnInvalidResponse(bot, pickNum)
		if err != nil {
			return err
		}
		summary += string('*')
	}

	err = registerPickInSheets(summary, pickNum, int(leagueSettings.NumTeams), currentBotIndex, e.sheetsClient)
	if err != nil {
		fmt.Println("Failed to write content to Google Sheets")
		return err
	}

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

func (e *BotEngine) getPlayerAndValidateDraftEligibility(playerId string) (*gamestate.Player, error) {
	if strings.TrimSpace(playerId) == "" {
		return nil, fmt.Errorf("Cannot draft an empty player")
	}

	player, err := e.gameStateHandler.GetPlayerById(playerId)
	if err != nil {
		return nil, err
	}

	if player.Availability == gamestate.Drafted.String() {
		return nil, fmt.Errorf("Cannot draft player again")
	}

	return player, nil
}

func (e *BotEngine) validatePlayerCanBeDropped(playerId string, owningBotId string) error {
	if strings.TrimSpace(playerId) == "" {
		return fmt.Errorf("Cannot drop an empty player")
	}

	player, err := e.gameStateHandler.GetPlayerById(playerId)
	if err != nil {
		return err
	}

	if player.Availability != gamestate.Drafted.String() || player.CurrentBotID == nil {
		return fmt.Errorf("A player must be drafted before they can be dropped")
	}

	if *player.CurrentBotID != owningBotId {
		return fmt.Errorf("A player can only be dropped by their owning team")
	}

	return nil
}

func (e *BotEngine) validateAndMakeDraftPick(bot *common.Bot, playerId string, currentDraftPick int) (string, error) {
	player, err := e.getPlayerAndValidateDraftEligibility(playerId)
	if err != nil {
		return "", err
	}

	draftString := gamestate.Drafted.String()

	e.gameStateHandler.UpdatePlayer(playerId, &draftString, &currentDraftPick, &bot.Id)

	fmt.Printf("With the %d pick of the bot draft, %s (%s) has selected: %s\n", currentDraftPick, bot.FantasyTeamName, bot.Owner, player.FullName)

	positionSummary, err := player.GetPositionSummary()
	if err != nil {
		positionSummary = "?"
	}

	summary := player.FullName + "(" + positionSummary + ")"

	return summary, nil
}

func (e *BotEngine) draftPlayerOnInvalidResponse(bot *common.Bot, currentDraftPick int) (string, error) {
	fmt.Println("Auto-drafting due to failure")

	tries := 0
	for tries < 1000 {

		player, err := e.gameStateHandler.GetRandomPlayer()
		if err != nil {
			return "", err
		}

		if player.Availability == gamestate.Available.String() {
			summary, err := e.validateAndMakeDraftPick(bot, player.ID, currentDraftPick)
			return summary, err
		}

		tries += 1
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
