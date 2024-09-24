package engine

import (
	"context"
	"fmt"

	common "github.com/mitchwebster/botblitz/pkg/common"
)

const AvailableMapKey = "AVAILABLE"

func (e *BotEngine) runWeeklyFantasy(ctx context.Context) error {
	fmt.Println("Playing Weekly Fantasy!!")

	_ = buildTeamMap(e.gameState)

	err := SaveGameState(e.gameState)
	if err != nil {
		return err
	}

	err = CleanOldGameStates(e.gameState)
	if err != nil {
		return err
	}

	return nil
}

func buildTeamMap(gameState *common.GameState) map[string][]*common.Player {
	playerMap := make(map[string][]*common.Player)

	for _, player := range gameState.Players {
		teamKey := player.Status.CurrentFantasyTeamId
		if teamKey == "" && player.Status.Availability == common.PlayerStatus_AVAILABLE {
			teamKey = AvailableMapKey
		}

		value, exists := playerMap[teamKey]
		if exists {
			appendedList := append(value, player)
			playerMap[teamKey] = appendedList
		} else {
			playerMap[teamKey] = []*common.Player{player}
		}
	}

	for team, playerList := range playerMap {
		if team == AvailableMapKey {
			continue
		}
		fmt.Printf("Key: %s, Value: %s\n", team, playerList)
	}

	return playerMap
}
