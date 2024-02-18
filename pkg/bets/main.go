package main

import (
	"database/sql"
	"fmt"
	"log"

	_ "github.com/mattn/go-sqlite3"
	common "github.com/mitchwebster/botblitz/pkg/common"
)

func main() {
	// Get the database file name from the environment variable
	dbName := "/Users/caltonji/Documents/Dev/2024/botblitz/nba-bets/nbastat.db"
	if dbName == "" {
		log.Fatal("DB environment variable is not set.")
	}

	// Open the SQLite database
	db, err := sql.Open("sqlite3", dbName)
	if err != nil {
		log.Fatalf("Error opening database: %v", err)
	}
	defer db.Close()

	// Query the bets table
	rows, err := db.Query(`SELECT id, homeTeam, awayTeam, playerName, type, points, price FROM bets`)
	if err != nil {
		log.Fatalf("Error querying database: %v", err)
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

		fmt.Println(bet)
	}

	if err := rows.Err(); err != nil {
		log.Fatal(err)
	}
}
