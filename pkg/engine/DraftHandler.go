package engine

import (
	"context"
	"fmt"
	"math/rand"
	"strconv"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"golang.org/x/exp/slices"
)

func (e *BotEngine) runDraft(ctx context.Context) error {
	curRound := 1
	for curRound <= int(e.gameState.LeagueSettings.TotalRounds) {
		fmt.Printf("ROUND %d HAS STARTED!\n", curRound)

		index := 0
		increment := 1
		arrayEdge := len(e.gameState.Teams) - 1

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
			err := e.performDraftAction(ctx, curBot)
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

	return nil
}

func (e *BotEngine) performDraftAction(ctx context.Context, bot *common.Bot) error {
	team, err := findCurrentTeamById(bot.FantasyTeamId, e.gameState)
	if err != nil {
		return err
	}

	fmt.Printf("[Pick: %d] %s (%s) will choose next...", e.gameState.CurrentDraftPick, team.Name, team.Owner)

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

	err = registerPickInSheets(summary, int(e.gameState.CurrentDraftPick), len(e.gameState.Teams), bot.FantasyTeamId, e.settings.SheetsClient)
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

func findCurrentTeamById(fantasyTeamId string, gameState *common.GameState) (*common.FantasyTeam, error) {
	teamIdx := slices.IndexFunc(gameState.Teams, func(t *common.FantasyTeam) bool { return t.Id == fantasyTeamId })
	if teamIdx < 0 {
		return nil, fmt.Errorf("Could not find team...concerning...")
	}

	return gameState.Teams[teamIdx], nil
}

func validateAndMakeDraftPick(fantasyTeamId string, playerId string, gameState *common.GameState) (string, error) {
	if len(playerId) <= 0 {
		return "", fmt.Errorf("Cannot draft empty player id")
	}

	idx := slices.IndexFunc(gameState.Players, func(p *common.Player) bool { return p.Id == playerId })
	if idx < 0 {
		return "", fmt.Errorf("Could not find player with selected id")
	}

	player := gameState.Players[idx]

	if player.Status.Availability == common.PlayerStatus_DRAFTED {
		return "", fmt.Errorf("Cannot draft player again")
	}

	team, err := findCurrentTeamById(fantasyTeamId, gameState)
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

func registerPickInSheets(summary string, currentDraftPick int, teamCount int, fantasyTeamId string, client *SheetsClient) error {
	if client == nil {
		return nil
	}

	zero_based_round := ((currentDraftPick - 1) / teamCount) + 1
	indexOfFantasyTeam, _ := strconv.Atoi(fantasyTeamId)
	newCol := rune(int(InitialCol) + indexOfFantasyTeam + 1)

	err := WriteContentToCell(IntialRow+zero_based_round, newCol, summary, client)
	return err
}
