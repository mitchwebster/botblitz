package bets

import (
	"database/sql"
	"fmt"
	"log"
	"time"

	_ "github.com/mattn/go-sqlite3"
	common "github.com/mitchwebster/botblitz/pkg/common"
	"google.golang.org/protobuf/types/known/timestamppb"
)

// GetDB returns the default DB. Should immediately run defer db.Close()
func GetDB() (*sql.DB, error) {
	dbPath := "../../nba-bets/nbastat.db"
	// Open the SQLite database
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		return nil, fmt.Errorf("Error opening database: %v", err)
	}
	return db, nil
}

// GetAllBets returns all bets in a DB
func GetAllBets(db *sql.DB) ([]*common.Bet, error) {
	bets := []*common.Bet{}

	// Query the bets table
	rows, err := db.Query(`SELECT id, homeTeam, awayTeam, playerName, type, points, price, commenceTime FROM bets`)
	if err != nil {
		return bets, fmt.Errorf("Error querying database: %v", err)
	}
	defer rows.Close()

	// Iterate through the query results
	for rows.Next() {
		var id int
		var homeTeam, awayTeam, playerName, betType, commenceTimeStr string
		var points, price float64

		err := rows.Scan(&id, &homeTeam, &awayTeam, &playerName, &betType, &points, &price, &commenceTimeStr)
		if err != nil {
			return bets, err
		}

		commenceTime, err := time.Parse("2006-01-02 15:04:05Z07:00", commenceTimeStr)
		if err != nil {
			return bets, fmt.Errorf("Failed to parse commenceTime '%s': %v", commenceTimeStr, err)
		}

		var betTypeProto common.Bet_Type
		switch betType {
		case "Over":
			betTypeProto = common.Bet_OVER
		case "Under":
			betTypeProto = common.Bet_UNDER
		default:
			return nil, fmt.Errorf("Unknown bet type: %v", betType)
		}

		player := &common.Player{
			FullName: playerName,
		}

		bet := &common.Bet{
			ProfessionalHomeTeam: homeTeam,
			ProfessionalAwayTeam: awayTeam,
			Player:               player,
			Type:                 betTypeProto,
			Points:               float32(points),
			Price:                float32(price),
			StartTime:            timestamppb.New(commenceTime),
		}
		bets = append(bets, bet)
	}

	if err := rows.Err(); err != nil {
		return bets, err
	}
	return bets, nil
}

func GetGameLogsBeforeBet(db *sql.DB, bet *common.Bet) ([]*common.GameLog, error) {
	var gameLogs []*common.GameLog

	startTimeStr := bet.StartTime.AsTime().Format("2006-01-02")

	query := `SELECT SEASON_YEAR, PLAYER_ID, PLAYER_NAME, NICKNAME, TEAM_ID, TEAM_ABBREVIATION, TEAM_NAME, GAME_ID, GAME_DATE, MATCHUP, WL, MIN, FGM, FGA, FG_PCT, FG3M, FG3A, FG3_PCT, FTM, FTA, FT_PCT, OREB, DREB, REB, AST, TOV, STL, BLK, BLKA, PF, PFD, PTS, PLUS_MINUS, NBA_FANTASY_PTS, DD2, TD3, WNBA_FANTASY_PTS, GP_RANK, W_RANK, L_RANK, W_PCT_RANK, MIN_RANK, FGM_RANK, FGA_RANK, FG_PCT_RANK, FG3M_RANK, FG3A_RANK, FG3_PCT_RANK, FTM_RANK, FTA_RANK, FT_PCT_RANK, OREB_RANK, DREB_RANK, REB_RANK, AST_RANK, TOV_RANK, STL_RANK, BLK_RANK, BLKA_RANK, PF_RANK, PFD_RANK, PTS_RANK, PLUS_MINUS_RANK, NBA_FANTASY_PTS_RANK, DD2_RANK, TD3_RANK, WNBA_FANTASY_PTS_RANK, AVAILABLE_FLAG FROM game_logs WHERE strftime('%Y-%m-%d', game_date) < ?`
	rows, err := db.Query(query, startTimeStr)
	if err != nil {
		return nil, fmt.Errorf("error querying database: %v", err)
	}
	defer rows.Close()

	for rows.Next() {
		var (
			gl          common.GameLog
			gameDateStr string
		)

		err := rows.Scan(&gl.SeasonYear, &gl.PlayerId, &gl.PlayerName, &gl.Nickname, &gl.TeamId, &gl.TeamAbbreviation, &gl.TeamName, &gl.GameId, &gameDateStr, &gl.Matchup, &gl.Wl, &gl.Min, &gl.Fgm, &gl.Fga, &gl.FgPct, &gl.Fg3M, &gl.Fg3A, &gl.Fg3Pct, &gl.Ftm, &gl.Fta, &gl.FtPct, &gl.Oreb, &gl.Dreb, &gl.Reb, &gl.Ast, &gl.Tov, &gl.Stl, &gl.Blk, &gl.Blka, &gl.Pf, &gl.Pfd, &gl.Pts, &gl.PlusMinus, &gl.NbaFantasyPts, &gl.Dd2, &gl.Td3, &gl.WnbaFantasyPts, &gl.GpRank, &gl.WRank, &gl.LRank, &gl.WPctRank, &gl.MinRank, &gl.FgmRank, &gl.FgaRank, &gl.FgPctRank, &gl.Fg3MRank, &gl.Fg3ARank, &gl.Fg3PctRank, &gl.FtmRank, &gl.FtaRank, &gl.FtPctRank, &gl.OrebRank, &gl.DrebRank, &gl.RebRank, &gl.AstRank, &gl.TovRank, &gl.StlRank, &gl.BlkRank, &gl.BlkaRank, &gl.PfRank, &gl.PfdRank, &gl.PtsRank, &gl.PlusMinusRank, &gl.NbaFantasyPtsRank, &gl.Dd2Rank, &gl.Td3Rank, &gl.WnbaFantasyPtsRank, &gl.AvailableFlag)
		if err != nil {
			log.Fatal(err)
		}

		// Parse the gameDate from string to time.Time
		gameDate, err := time.Parse("2006-01-02T15:04:05", gameDateStr)
		if err != nil {
			log.Fatalf("Failed to parse gameDate '%s': %v", gameDateStr, err)
		}
		gl.GameDate = timestamppb.New(gameDate)

		gameLogs = append(gameLogs, &gl)
	}

	if err := rows.Err(); err != nil {
		return nil, err
	}

	return gameLogs, nil
}
