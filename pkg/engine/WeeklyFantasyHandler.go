package engine

import (
	"context"
	"fmt"
	"math/rand"
	"sort"

	common "github.com/mitchwebster/botblitz/pkg/common"
)

const AvailableMapKey = "AVAILABLE"
const AllowedAddsPerRun = 3

func (e *BotEngine) runWeeklyFantasy(ctx context.Context) error {
	err := performWeeklyFantasyActions(e)
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

func performWeeklyFantasyActions(e *BotEngine) error {
	fmt.Println("Playing Weekly Fantasy!!")

	makePreviouslyOnHoldPlayersAvailable(e.gameState)

	err := setupInitialWaiverPriorityIfNeeded(e.gameState)
	if err != nil {
		return err
	}

	// Collect the desired actions from the bots

	// Go through them in the waiver priority order
	sort.Slice(e.gameState.Teams, func(i, j int) bool {
		return e.gameState.Teams[i].CurrentWaiverPriority < e.gameState.Teams[j].CurrentWaiverPriority
	})

	for _, team := range e.gameState.Teams {
		fmt.Println(team.Id)
		fmt.Println(team.Name)
		fmt.Println(team.CurrentWaiverPriority)
	}

	// playerMap := buildTeamMap(e.gameState)
	return nil
}

func makePreviouslyOnHoldPlayersAvailable(gameState *common.GameState) {
	for _, player := range gameState.Players {
		if player.Status.Availability == common.PlayerStatus_ON_HOLD {
			player.Status.Availability = common.PlayerStatus_AVAILABLE
			fmt.Println(player.FullName + " is now available!")
		}
	}
}

func setupInitialWaiverPriorityIfNeeded(gameState *common.GameState) error {
	// check if all of the teams are missing a waiver priority
	for _, team := range gameState.Teams {
		if team.CurrentWaiverPriority != 0 {
			return nil
		}
	}

	fmt.Println("Initializing team waiver priorities")
	n := len(gameState.Teams)
	indices := make([]int, 0, n)

	for i := 0; i < n; i++ {
		indices = append(indices, i)
	}

	rand.Shuffle(len(indices), func(i, j int) {
		indices[i], indices[j] = indices[j], indices[i]
	})

	for i := 0; i < n; i++ {
		curTeam := gameState.Teams[i]
		curTeam.CurrentWaiverPriority = uint32(indices[i])
	}

	return nil
}

// func buildTeamMap(gameState *common.GameState) map[string][]*common.Player {
// 	playerMap := make(map[string][]*common.Player)

// 	for _, player := range gameState.Players {
// 		teamKey := player.Status.CurrentFantasyTeamId
// 		if teamKey == "" && player.Status.Availability == common.PlayerStatus_AVAILABLE {
// 			teamKey = AvailableMapKey
// 		}

// 		value, exists := playerMap[teamKey]
// 		if exists {
// 			appendedList := append(value, player)
// 			playerMap[teamKey] = appendedList
// 		} else {
// 			playerMap[teamKey] = []*common.Player{player}
// 		}
// 	}

// 	for team, playerList := range playerMap {
// 		if team == AvailableMapKey {
// 			continue
// 		}
// 		fmt.Printf("Key: %s, Value: %s\n", team, playerList)
// 	}

// 	return playerMap
// }
