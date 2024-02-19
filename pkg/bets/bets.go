package bets

import (
	"database/sql"
	"fmt"
	"log"

	_ "github.com/mattn/go-sqlite3"
	common "github.com/mitchwebster/botblitz/pkg/common"
)

// GetDB returns the default DB. Should immediately run defer db.close()
func GetDB() (*sql.DB, error) {
	dbPath := "../../../nba-bets/nbastat.db"
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
	rows, err := db.Query(`SELECT id, homeTeam, awayTeam, playerName, type, points, price FROM bets`)
	if err != nil {
		return bets, fmt.Errorf("Error querying database: %v", err)
	}
	defer rows.Close()

	// Iterate through the query results
	for rows.Next() {
		var id int
		var homeTeam, awayTeam, playerName, betType string
		var points, price float64

		err := rows.Scan(&id, &homeTeam, &awayTeam, &playerName, &betType, &points, &price)
		if err != nil {
			log.Fatal(err)
		}

		var betTypeProto common.Bet_Type
		switch betType {
		case "Over":
			betTypeProto = common.Bet_OVER
		case "Under":
			betTypeProto = common.Bet_UNDER
		default:
			log.Fatalf("Unknown bet type: %v", betType)
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
		}
		bets = append(bets, bet)
	}

	if err := rows.Err(); err != nil {
		return bets, err
	}
	return bets, nil
}
