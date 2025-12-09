package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"sort"

	"github.com/mitchwebster/botblitz/pkg/gamestate"
)

var year = flag.Int("year", 2025, "The year of the season")

func main() {
	flag.Parse()

	fmt.Println("Starting backfill of weekly lineups for weeks 1-14...")

	yearUint := uint32(*year)
	gameStateHandler, err := gamestate.LoadGameStateForWeeklyFantasy(yearUint)
	if err != nil {
		fmt.Println("Failed to load game state for weekly fantasy")
		fmt.Println(err)
		os.Exit(1)
	}

	// Get current week so we know the range
	currentWeek, err := gameStateHandler.GetCurrentFantasyWeek()
	if err != nil {
		fmt.Println("Failed to get current fantasy week")
		fmt.Println(err)
		os.Exit(1)
	}

	fmt.Printf("Current fantasy week: %d\n", currentWeek)

	// Ensure weekly_lineups table exists
	fmt.Println("Ensuring weekly_lineups table exists...")
	if !gameStateHandler.GetDB().Migrator().HasTable(&gamestate.WeeklyLineup{}) {
		err = gameStateHandler.GetDB().AutoMigrate(&gamestate.WeeklyLineup{})
		if err != nil {
			fmt.Println("Failed to create weekly_lineups table")
			fmt.Println(err)
			os.Exit(1)
		}
		fmt.Println("Created weekly_lineups table")
	} else {
		fmt.Println("weekly_lineups table already exists")
	}

	// Clear existing lineups
	fmt.Println("Clearing existing weekly_lineups data...")
	err = gameStateHandler.GetDB().Exec("DELETE FROM weekly_lineups").Error
	if err != nil {
		fmt.Println("Failed to clear weekly_lineups table")
		fmt.Println(err)
		os.Exit(1)
	}

	fmt.Println("Backfilling weeks 14 down to 1 (working backwards)...")

	// Get all transactions once
	var allTransactions []gamestate.Transaction
	err = gameStateHandler.GetDB().Order("week DESC, id DESC").Find(&allTransactions).Error
	if err != nil {
		fmt.Println("Failed to load transactions")
		fmt.Println(err)
		os.Exit(1)
	}

	fmt.Printf("Loaded %d transactions\n", len(allTransactions))

	// Get current rosters for all bots
	bots, err := gameStateHandler.GetBots()
	if err != nil {
		fmt.Println("Failed to get bots")
		fmt.Println(err)
		os.Exit(1)
	}

	// Build current roster map: bot_id -> []player_id
	currentRosters := make(map[string]map[string]bool)
	for _, bot := range bots {
		currentRosters[bot.ID] = make(map[string]bool)

		var players []gamestate.Player
		err = gameStateHandler.GetDB().Where("current_bot_id = ?", bot.ID).Find(&players).Error
		if err != nil {
			fmt.Printf("Failed to get players for bot %s: %v\n", bot.ID, err)
			os.Exit(1)
		}

		for _, player := range players {
			currentRosters[bot.ID][player.ID] = true
		}
	}

	// Work backwards from week 14 to week 1
	for week := 14; week >= 1; week-- {
		fmt.Printf("\n=== Processing Week %d ===\n", week)

		// For this week, we need to reverse all transactions AFTER this week
		// Build roster for this week by reversing future transactions
		weekRosters := make(map[string]map[string]bool)
		for botID := range currentRosters {
			weekRosters[botID] = make(map[string]bool)
			for playerID := range currentRosters[botID] {
				weekRosters[botID][playerID] = true
			}
		}

		// Reverse transactions from weeks > current week
		for _, txn := range allTransactions {
			if txn.Week > week {
				// Reverse this transaction:
				// - Remove the added player
				// - Add back the dropped player
				delete(weekRosters[txn.BotID], txn.Added)
				weekRosters[txn.BotID][txn.Dropped] = true
			}
		}

		err = backfillWeek(gameStateHandler, week, weekRosters, bots)
		if err != nil {
			fmt.Printf("Failed to backfill week %d: %v\n", week, err)
			os.Exit(1)
		}
	}

	fmt.Println("\n=== Validating against matchups table ===")
	err = validateAgainstMatchups(gameStateHandler)
	if err != nil {
		fmt.Printf("Validation failed: %v\n", err)
		os.Exit(1)
	}

	fmt.Println("\n=== Backfill Complete and Validated! ===")
	fmt.Printf("Current fantasy week remains: %d\n", currentWeek)
}

func backfillWeek(handler *gamestate.GameStateHandler, week int, weekRosters map[string]map[string]bool, bots []gamestate.Bot) error {
	// Get player scores for this specific week
	playerScores, err := getPlayerScoresForWeek(handler, week)
	if err != nil {
		return fmt.Errorf("failed to get player scores: %w", err)
	}

	// Create score map for quick lookup
	scoreMap := make(map[string]gamestate.PlayerWeeklyScore)
	for _, score := range playerScores {
		scoreMap[score.ID] = score
	}

	botMap := make(map[string]gamestate.Bot)
	for _, bot := range bots {
		botMap[bot.ID] = bot
	}

	// Get league settings for position slots
	leagueSettings, err := handler.GetLeagueSettings()
	if err != nil {
		return fmt.Errorf("failed to get league settings: %w", err)
	}

	var positionMap map[string]int
	err = json.Unmarshal([]byte(leagueSettings.PlayerSlots), &positionMap)
	if err != nil {
		return fmt.Errorf("failed to unmarshal position map: %w", err)
	}

	allowedPositions, slotNames, err := convertSlotToPositionMap(positionMap)
	if err != nil {
		return fmt.Errorf("failed to convert slot to position map: %w", err)
	}

	// Score each team using the reconstructed roster
	for botID, roster := range weekRosters {
		// Build scores array for this bot's roster, maintaining sorted order
		var botScores []gamestate.PlayerWeeklyScore
		scoredPlayerIDs := make(map[string]bool)

		// First, add all players with scores (sorted by FPTS descending)
		for _, score := range playerScores {
			if roster[score.ID] {
				botScores = append(botScores, score)
				scoredPlayerIDs[score.ID] = true
			}
		}

		// Then, add players without scores (injured, bye week, etc.) with 0.0 points
		for playerID := range roster {
			if !scoredPlayerIDs[playerID] {
				// Get player info from database
				var player gamestate.Player
				if err := handler.GetDB().First(&player, "id = ?", playerID).Error; err != nil {
					return fmt.Errorf("failed to get player %s: %w", playerID, err)
				}

				// Create a score entry with 0 points
				botScores = append(botScores, gamestate.PlayerWeeklyScore{
					ID:               player.ID,
					FullName:         player.FullName,
					AllowedPositions: player.AllowedPositions,
					CurrentBotID:     botID,
					FPTS:             0.0,
				})
			}
		}

		totalScore, bestPossibleTeam, err := scoreTeam(allowedPositions, slotNames, botScores)
		if err != nil {
			return fmt.Errorf("failed to score team %s: %w", botID, err)
		}

		// Convert slotNames (Position enum) to strings
		slotNamesStr := make([]string, len(slotNames))
		for i, slot := range slotNames {
			slotNamesStr[i] = slot.String()
		}

		// Save weekly lineup
		err = handler.SaveWeeklyLineup(week, botID, bestPossibleTeam, slotNamesStr, botScores)
		if err != nil {
			return fmt.Errorf("failed to save weekly lineup for bot %s: %w", botID, err)
		}

		fmt.Printf("  %-20s: %.2f points (%d players in roster)\n", botMap[botID].Name, totalScore, len(roster))
	}

	return nil
}

func validateAgainstMatchups(handler *gamestate.GameStateHandler) error {
	// Get all matchups
	var matchups []gamestate.Matchup
	err := handler.GetDB().Find(&matchups).Error
	if err != nil {
		return fmt.Errorf("failed to get matchups: %w", err)
	}

	allValid := true
	for _, matchup := range matchups {
		// Get computed score from weekly_lineups
		var homeLineup []gamestate.WeeklyLineup
		err = handler.GetDB().Where("week = ? AND bot_id = ? AND slot != 'BENCH'", matchup.Week, matchup.HomeBotID).Find(&homeLineup).Error
		if err != nil {
			return fmt.Errorf("failed to get home lineup: %w", err)
		}

		homeScore := 0.0
		for _, lineup := range homeLineup {
			homeScore += lineup.Points
		}

		var visitorLineup []gamestate.WeeklyLineup
		err = handler.GetDB().Where("week = ? AND bot_id = ? AND slot != 'BENCH'", matchup.Week, matchup.VisitorBotID).Find(&visitorLineup).Error
		if err != nil {
			return fmt.Errorf("failed to get visitor lineup: %w", err)
		}

		visitorScore := 0.0
		for _, lineup := range visitorLineup {
			visitorScore += lineup.Points
		}

		// Compare with matchup scores (allow small floating point difference)
		homeDiff := homeScore - matchup.HomeScore
		visitorDiff := visitorScore - matchup.VisitorScore

		if homeDiff < -0.01 || homeDiff > 0.01 || visitorDiff < -0.01 || visitorDiff > 0.01 {
			fmt.Printf("  ❌ Week %d: Home %.2f vs %.2f (expected), Visitor %.2f vs %.2f (expected)\n",
				matchup.Week, homeScore, matchup.HomeScore, visitorScore, matchup.VisitorScore)
			allValid = false
		} else {
			fmt.Printf("  ✅ Week %d: Scores match!\n", matchup.Week)
		}
	}

	if !allValid {
		return fmt.Errorf("some scores did not match")
	}

	return nil
}

func getPlayerScoresForWeek(handler *gamestate.GameStateHandler, week int) ([]gamestate.PlayerWeeklyScore, error) {
	var results []gamestate.PlayerWeeklyScore

	err := handler.GetDB().Raw(`
		SELECT
			p.id,
			p.full_name,
			p.allowed_positions,
			p.current_bot_id,
			max(w.FPTS) as FPTS
		FROM players AS p
		INNER JOIN weekly_stats AS w
		ON p.id = w.fantasypros_id AND w.week = ?
		GROUP BY p.id
		ORDER BY FPTS desc
	`, week).Scan(&results).Error

	if err != nil {
		return nil, err
	}

	return results, nil
}

// Position type and methods copied from engine package
type Position int

const (
	QB Position = iota
	RB
	WR
	K
	DEF
	TE
	SUPERFLEX
	FLEX
	BENCH
)

func (s Position) String() string {
	return [...]string{"QB", "RB", "WR", "K", "DST", "TE", "SUPERFLEX", "FLEX", "BENCH"}[s]
}

func PositionFromString(s string) (Position, error) {
	switch s {
	case "QB":
		return QB, nil
	case "RB":
		return RB, nil
	case "WR":
		return WR, nil
	case "TE":
		return TE, nil
	case "FLEX":
		return FLEX, nil
	case "SUPERFLEX":
		return SUPERFLEX, nil
	case "K":
		return K, nil
	case "DEF", "DST":
		return DEF, nil
	case "BENCH":
		return BENCH, nil
	default:
		return -1, fmt.Errorf("unknown position: %s", s)
	}
}

func PositionFromStringArr(arr []string) ([]Position, error) {
	positions := make([]Position, len(arr))
	for i, s := range arr {
		pos, err := PositionFromString(s)
		if err != nil {
			return nil, err
		}
		positions[i] = pos
	}
	return positions, nil
}

func GetAllowedPlayerPositionsForSlot(slot Position) ([]Position, error) {
	switch slot {
	case QB:
		return []Position{QB}, nil
	case RB:
		return []Position{RB}, nil
	case WR:
		return []Position{WR}, nil
	case K:
		return []Position{K}, nil
	case DEF:
		return []Position{DEF}, nil
	case TE:
		return []Position{TE}, nil
	case SUPERFLEX:
		return []Position{QB, RB, WR, TE}, nil
	case FLEX:
		return []Position{RB, WR, TE}, nil
	case BENCH:
		return []Position{QB, RB, WR, K, DEF, TE}, nil
	default:
		return nil, fmt.Errorf("unknown slot: %d", slot)
	}
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

func scoreTeam(allowedPlayerPositions [][]Position, slotNames []Position, scores []gamestate.PlayerWeeklyScore) (float64, []gamestate.PlayerWeeklyScore, error) {
	const NonePlayerName = "None"

	bestPossibleTeam := make([]gamestate.PlayerWeeklyScore, len(allowedPlayerPositions))
	for i := 0; i < len(allowedPlayerPositions); i++ {
		bestPossibleTeam[i] = gamestate.PlayerWeeklyScore{
			ID:               "1",
			FullName:         NonePlayerName,
			AllowedPositions: "[]",
			FPTS:             -1e9,
			CurrentBotID:     "",
		}
	}

	for _, score := range scores {
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
		if bestTeamScore.FullName == NonePlayerName && bestTeamScore.FPTS == -1e9 {
			continue
		}
		overallScore += bestTeamScore.FPTS
	}

	return overallScore, bestPossibleTeam, nil
}

func FindIntersection(a, b []Position) []Position {
	set := make(map[Position]bool)
	for _, v := range a {
		set[v] = true
	}

	var intersection []Position
	for _, v := range b {
		if set[v] {
			intersection = append(intersection, v)
		}
	}

	return intersection
}
