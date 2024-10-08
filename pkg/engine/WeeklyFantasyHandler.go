package engine

import (
	"context"
	"fmt"
	"math/rand"

	common "github.com/mitchwebster/botblitz/pkg/common"
)

const AllowedAddsPerRun = 3

func (e *BotEngine) runWeeklyFantasy(ctx context.Context) error {
	err := e.performWeeklyFantasyActions(ctx)
	if err != nil {
		return err
	}

	err = SaveGameState(e.gameState)
	if err != nil {
		return err
	}

	err = CleanOldGameStates(e.gameState)
	if err != nil {
		return err
	}

	return nil
}

func (e *BotEngine) performWeeklyFantasyActions(ctx context.Context) error {
	fmt.Println("Playing Weekly Fantasy!!")

	e.makePreviouslyOnHoldPlayersAvailable()

	err := e.setupInitialWaiverPriorityIfNeeded()
	if err != nil {
		return err
	}

	e.sortBotsAccordingToWaiverPriority()

	teamIdToPlayerMap := buildTeamMap(e.gameState)

	for run := 0; run < AllowedAddsPerRun; run++ {
		fmt.Printf("Running %d round of add/drop actions\n", run)

		botsWhoTookActionSlice := make([]*common.Bot, 0, len(e.bots))
		botsWhoTookActionMap := make(map[string]bool)

		for _, bot := range e.bots {
			fmt.Println("Processing add/drops for: " + bot.Id)
			e.gameState.CurrentBotTeamId = bot.FantasyTeamId
			tookAction := e.performAddDrop(ctx, bot, teamIdToPlayerMap)
			if tookAction {
				botsWhoTookActionMap[bot.FantasyTeamId] = true
				botsWhoTookActionSlice = append(botsWhoTookActionSlice, bot)
			}
		}

		newBotSlice := make([]*common.Bot, 0, len(e.bots))

		// Add the bots who did not take action first
		for _, bot := range e.bots {
			_, tookAction := botsWhoTookActionMap[bot.FantasyTeamId]
			if !tookAction {
				newBotSlice = append(newBotSlice, bot)
			}
		}

		// Add the bots who did take an action
		newBotSlice = append(newBotSlice, botsWhoTookActionSlice...)

		e.bots = newBotSlice
	}

	fmt.Fprintf(&e.weeklyFantasyTransactionLog, "[Final Bot Order]\n")

	// Update the waiver order for next time
	for i, bot := range e.bots {
		team, err := FindCurrentTeamById(bot.FantasyTeamId, e.gameState)
		if err != nil {
			return err
		}

		team.CurrentWaiverPriority = uint32(i)
		fmt.Fprintf(&e.weeklyFantasyTransactionLog, "\t"+bot.FantasyTeamId+":"+bot.Id+"\n")
	}

	fmt.Println(e.weeklyFantasyTransactionLog.String())

	return nil
}

func (e *BotEngine) makePreviouslyOnHoldPlayersAvailable() {
	for _, player := range e.gameState.Players {
		if player.Status.Availability == common.PlayerStatus_ON_HOLD {
			player.Status.Availability = common.PlayerStatus_AVAILABLE
			fmt.Println(player.FullName + " is now available!")
		}
	}
}

func (e *BotEngine) setupInitialWaiverPriorityIfNeeded() error {
	// check if all of the teams are missing a waiver priority
	for _, team := range e.gameState.Teams {
		if team.CurrentWaiverPriority != 0 {
			return nil
		}
	}

	fmt.Println("Initializing team waiver priorities")
	n := len(e.gameState.Teams)
	indices := make([]int, 0, n)

	for i := 0; i < n; i++ {
		indices = append(indices, i)
	}

	rand.Shuffle(len(indices), func(i, j int) {
		indices[i], indices[j] = indices[j], indices[i]
	})

	for i := 0; i < n; i++ {
		curTeam := e.gameState.Teams[i]
		curTeam.CurrentWaiverPriority = uint32(indices[i])
	}

	return nil
}

func (e *BotEngine) sortBotsAccordingToWaiverPriority() {
	n := len(e.bots)
	newBotList := make([]*common.Bot, n)

	teamIdToBotMap := make(map[string]*common.Bot)
	for _, bot := range e.bots {
		teamIdToBotMap[bot.FantasyTeamId] = bot
	}

	for _, team := range e.gameState.Teams {
		bot := teamIdToBotMap[team.Id]
		newBotList[team.CurrentWaiverPriority] = bot
	}

	fmt.Fprintf(&e.weeklyFantasyTransactionLog, "[Initial Bot Order]\n")

	// Print the new order list for this round
	for _, bot := range newBotList {
		fmt.Println(bot.FantasyTeamId + ":" + bot.Id)
		fmt.Fprintf(&e.weeklyFantasyTransactionLog, "\t"+bot.FantasyTeamId+":"+bot.Id+"\n")
	}

	fmt.Fprintf(&e.weeklyFantasyTransactionLog, "\n")

	e.bots = newBotList
}

func (e *BotEngine) performAddDrop(ctx context.Context, bot *common.Bot, teamIdToPlayerMap map[string][]*common.Player) bool {
	selection, err := e.startContainerAndPerformAddDropAction(ctx, bot)
	if err != nil {
		fmt.Println(err)
		return false
	}

	attemptPlayerDrop := len(selection.PlayerToDropId) > 0
	attemptPlayerAdd := len(selection.PlayerToAddId) > 0

	if attemptPlayerDrop {
		droppedPlayer := e.handleDropPlayer(selection.PlayerToDropId, bot, teamIdToPlayerMap)
		if droppedPlayer != nil {
			fmt.Fprintf(&e.weeklyFantasyTransactionLog, "[%s]: successfully dropped %s\n", bot.Id, droppedPlayer.FullName)
		} else {
			fmt.Fprintf(&e.weeklyFantasyTransactionLog, "[%s]: failed to drop %s\n", bot.Id, selection.PlayerToDropId)
		}
	}

	if attemptPlayerAdd {
		addedPlayer := e.handleAddPlayer(selection.PlayerToAddId, bot, teamIdToPlayerMap)
		if addedPlayer != nil {
			fmt.Fprintf(&e.weeklyFantasyTransactionLog, "[%s]: successfully added %s\n", bot.Id, addedPlayer.FullName)
		} else {
			fmt.Fprintf(&e.weeklyFantasyTransactionLog, "[%s]: failed to add %s\n", bot.Id, selection.PlayerToAddId)
		}
	}

	if attemptPlayerDrop || attemptPlayerAdd {
		return true
	}

	return false
}

func (e *BotEngine) handleDropPlayer(playerId string, bot *common.Bot, teamIdToPlayerMap map[string][]*common.Player) *common.Player {
	player, err := FindPlayerById(playerId, e.gameState)
	if err != nil {
		fmt.Println(err)
		return nil
	}

	if player.Status.Availability != common.PlayerStatus_DRAFTED {
		fmt.Println("Cannot drop a player that is not drafted to a team")
		return nil
	}

	if player.Status.CurrentFantasyTeamId != bot.FantasyTeamId {
		fmt.Println("Cannot drop a player that is not on this team already")
		return nil
	}

	player.Status.CurrentFantasyTeamId = ""                  // No team now
	player.Status.Availability = common.PlayerStatus_ON_HOLD // On hold until next reconciliation
	player.Status.PickChosen = 0                             // reset pick chosen as it is no longer true

	// Remove this player from the team map
	currentTeam := teamIdToPlayerMap[bot.FantasyTeamId]
	indexToRemove := -1
	for i, teamPlayer := range currentTeam {
		if player.Id == teamPlayer.Id {
			indexToRemove = i
			break
		}
	}

	resultingSlice := append(currentTeam[:indexToRemove], currentTeam[indexToRemove+1:]...)
	teamIdToPlayerMap[bot.FantasyTeamId] = resultingSlice

	return player
}

func (e *BotEngine) handleAddPlayer(playerId string, bot *common.Bot, teamIdToPlayerMap map[string][]*common.Player) *common.Player {
	player, err := FindPlayerById(playerId, e.gameState)
	if err != nil {
		fmt.Println(err)
		return nil
	}

	if !e.checkIfPlayerCanBeAddedToTeam(player, bot, teamIdToPlayerMap) {
		return nil
	}

	currentTeam := teamIdToPlayerMap[bot.FantasyTeamId]

	player.Status.CurrentFantasyTeamId = bot.FantasyTeamId   // Assign new team
	player.Status.Availability = common.PlayerStatus_DRAFTED // Mark them as drafted
	player.Status.PickChosen = 0                             // Not applicable in add/drop

	resultingSllice := append(currentTeam, player)
	teamIdToPlayerMap[bot.FantasyTeamId] = resultingSllice
	return player
}

func buildTeamMap(gameState *common.GameState) map[string][]*common.Player {
	playerMap := make(map[string][]*common.Player)

	for _, player := range gameState.Players {
		teamKey := player.Status.CurrentFantasyTeamId
		if teamKey == "" {
			continue // do not keep track of players without a team
		}

		value, exists := playerMap[teamKey]
		if exists {
			appendedList := append(value, player)
			playerMap[teamKey] = appendedList
		} else {
			playerMap[teamKey] = []*common.Player{player}
		}
	}

	return playerMap
}

func (e *BotEngine) checkIfPlayerCanBeAddedToTeam(player *common.Player, bot *common.Bot, teamIdToPlayerMap map[string][]*common.Player) bool {
	if player.Status.Availability != common.PlayerStatus_AVAILABLE {
		fmt.Println("Cannot add a player that is not available")
		return false
	}

	// Defense in depth check
	if player.Status.CurrentFantasyTeamId != "" {
		fmt.Println("Cannot add a player that already belongs to a team")
		return false
	}

	totalTeamSize := len(e.gameState.LeagueSettings.SlotsPerTeam)
	currentTeam := teamIdToPlayerMap[bot.FantasyTeamId]
	if len(currentTeam)+1 > totalTeamSize {
		fmt.Println("Cannot add player, team would be too large")
		return false
	}

	// TODO: do we need to check any slot issues - it might be ok if they are just non scoring?

	return true
}
