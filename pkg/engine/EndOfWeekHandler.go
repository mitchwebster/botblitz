package engine

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"

	"github.com/mitchwebster/botblitz/pkg/gamestate"
)

const NonePlayerName = "None"

func (e *BotEngine) updateWeeklyScores(ctx context.Context, finishWeek bool) error {
	err := e.gameStateHandler.RefreshWeeklyStats()
	if err != nil {
		return err
	}

	playerScores, currentFantasyWeek, err := e.gameStateHandler.GetPlayerScoresForCurrentWeek()
	if err != nil {
		return err
	}

	bots, err := e.gameStateHandler.GetBots()
	if err != nil {
		return err
	}

	botMap := make(map[string]gamestate.Bot)
	for _, bot := range bots {
		botMap[bot.ID] = bot
	}

	scoresPerTeam, err := e.getScoresPerTeam(playerScores, botMap)
	if err != nil {
		return err
	}

	matchups, err := e.gameStateHandler.GetMatchupsForWeek(currentFantasyWeek)
	if err != nil {
		return err
	}

	err = e.updateMatchupResults(botMap, scoresPerTeam, currentFantasyWeek, matchups, finishWeek)
	if err != nil {
		return err
	}

	if finishWeek {
		err = e.gameStateHandler.IncrementFantasyWeek()
		if err != nil {
			return err
		}
	}

	return nil
}

func (e *BotEngine) updateMatchupResults(botMap map[string]gamestate.Bot, scoresPerTeam map[string]float64, currentFantasyWeek int, matchups []gamestate.Matchup, finishWeek bool) error {
	var builder strings.Builder
	var err error

	if finishWeek {
		builder.WriteString(fmt.Sprintf("Fantasy Results for Week %d:\n", currentFantasyWeek))
	}

	for _, match := range matchups {
		homeScore := scoresPerTeam[match.HomeBotID]
		visitorScore := scoresPerTeam[match.VisitorBotID]

		if finishWeek {
			homeBot := botMap[match.HomeBotID]
			visitorBot := botMap[match.VisitorBotID]

			var winnerName string
			var winningBotId string
			if homeScore >= visitorScore {
				winnerName = homeBot.Name
				winningBotId = match.HomeBotID
			} else {
				winnerName = visitorBot.Name
				winningBotId = match.VisitorBotID
			}

			builder.WriteString(fmt.Sprintf("Matchup: %-20s (%.2f) vs %-20s (%.2f) | Winner: %s\n",
				homeBot.Name, homeScore, visitorBot.Name, visitorScore, winnerName))

			err = e.gameStateHandler.SetMatchResult(match.ID, homeScore, visitorScore, winningBotId)
		} else {
			err = e.gameStateHandler.UpdateMatchScore(match.ID, homeScore, visitorScore)
		}

		if err != nil {
			return err
		}
	}

	summaryStr := builder.String()
	println(summaryStr)
	err = e.saveTransactionLogToFile(summaryStr)
	if err != nil {
		return err
	}

	return nil
}

func (e *BotEngine) getScoresPerTeam(scores []gamestate.PlayerWeeklyScore, dbBotMap map[string]gamestate.Bot) (map[string]float64, error) {
	botMap := make(map[string][]gamestate.PlayerWeeklyScore)
	leagueSettings, err := e.gameStateHandler.GetLeagueSettings()
	if err != nil {
		return nil, err
	}

	var positionMap map[string]int
	err = json.Unmarshal([]byte(leagueSettings.PlayerSlots), &positionMap)
	if err != nil {
		return nil, err
	}

	allowedPositions, slotNames, err := convertSlotToPositionMap(positionMap)
	if err != nil {
		return nil, err
	}

	for _, score := range scores {
		_, exists := botMap[score.CurrentBotID]
		if !exists {
			botMap[score.CurrentBotID] = make([]gamestate.PlayerWeeklyScore, 0)
		}

		botMap[score.CurrentBotID] = append(botMap[score.CurrentBotID], score)
	}

	botScoreMap := make(map[string]float64)
	for botId, scores := range botMap {
		totalScore, bestPossibleTeam, err := e.scoreTeam(allowedPositions, slotNames, scores)
		botScoreMap[botId] = totalScore
		if err != nil {
			return nil, err
		}

		fmt.Printf("\n%-20s scored %-8.2f last week:\n", dbBotMap[botId].Name, totalScore)
		fmt.Println("-------------------------------------------------------------")
		fmt.Printf("%-15s | %-25s | %-8s\n", "Slot", "Player", "Score")
		fmt.Println("-------------------------------------------------------------")
		for i, bestTeamScore := range bestPossibleTeam {
			playerName := bestTeamScore.FullName
			score := bestTeamScore.FPTS
			if playerName == NonePlayerName && score == -1e9 {
				playerName = "None"
				score = 0.0
			}

			fmt.Printf("%-15s | %-25s | %-8.2f\n", slotNames[i], playerName, score)
		}
		fmt.Println("-------------------------------------------------------------")
	}

	return botScoreMap, nil
}

func convertSlotToPositionMap(playerSlotMap map[string]int) ([][]Position, []Position, error) {
	slotNames := make([]Position, 0)
	positionArray := make([][]Position, 0)
	for slotName, count := range playerSlotMap {
		slot, err := PositionFromString(slotName)
		if err != nil {
			return nil, nil, err
		}

		// Skip BENCH - does not score points
		if slot == BENCH {
			continue
		}

		positions, err := GetAllowedPlayerPositionsForSlot(slot)
		if err != nil {
			return nil, nil, err
		}

		for i := 0; i < count; i++ {
			positionArray = append(positionArray, positions)
			slotNames = append(slotNames, slot)
		}
	}

	indices := make([]int, len(positionArray))
	for i := range indices {
		indices[i] = i
	}

	sort.Slice(indices, func(i, j int) bool {
		return len(positionArray[indices[i]]) < len(positionArray[indices[j]])
	})

	sortedPositions := make([][]Position, len(positionArray))
	sortedSlotNames := make([]Position, len(slotNames))
	for i, idx := range indices {
		sortedPositions[i] = positionArray[idx]
		sortedSlotNames[i] = slotNames[idx]
	}

	return sortedPositions, sortedSlotNames, nil
}

func (e *BotEngine) scoreTeam(allowedPlayerPositions [][]Position, slotNames []Position, scores []gamestate.PlayerWeeklyScore) (float64, []gamestate.PlayerWeeklyScore, error) {

	bestPossibleTeam := make([]gamestate.PlayerWeeklyScore, len(allowedPlayerPositions))
	for i := 0; i < len(allowedPlayerPositions); i++ {
		bestPossibleTeam[i] = gamestate.PlayerWeeklyScore{
			ID:               "1",
			FullName:         NonePlayerName,
			AllowedPositions: "[]",
			FPTS:             -1e9, // use a very low float64 value as initial min
			CurrentBotID:     "",
		}
	}

	for _, score := range scores {
		// Get list of allowed positions
		var allowedPositions []string
		err := json.Unmarshal([]byte(score.AllowedPositions), &allowedPositions)
		if err != nil {
			return 0, nil, err
		}

		playerPositionArr, err := PositionFromStringArr(allowedPositions)
		if err != nil {
			return 0, nil, err
		}

		for i, availableSlotsArr := range allowedPlayerPositions {
			if len(FindIntersection(playerPositionArr, availableSlotsArr)) > 0 && bestPossibleTeam[i].FPTS < score.FPTS {
				bestPossibleTeam[i] = score
				break
			}
		}
	}

	overallScore := 0.0
	for _, bestTeamScore := range bestPossibleTeam {
		// Failed to place a player should not count negatively against the team
		if bestTeamScore.FullName == NonePlayerName && bestTeamScore.FPTS == -1e9 {
			continue
		}

		overallScore += bestTeamScore.FPTS
	}

	return overallScore, bestPossibleTeam, nil
}
