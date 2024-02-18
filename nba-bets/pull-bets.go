package main

import (
    "database/sql"
    "fmt"
    "log"
    "os"

    _ "github.com/mattn/go-sqlite3"
    "google.golang.org/protobuf/types/known/structpb"

    "path/to/your/protobuf/package" // Replace with the actual import path of your generated protobuf go package
)

func main() {
    // Get the database file name from the environment variable
    dbName := os.Getenv("DB")
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

        // Convert database types to PROTO types where necessary
        var betTypeProto package.Bet_Type
        switch betType {
        case "OVER":
            betTypeProto = package.Bet_OVER
        case "UNDER":
            betTypeProto = package.Bet_UNDER
        default:
            log.Fatalf("Unknown bet type: %v", betType)
        }

        // Assuming Player data needs to be fetched or constructed separately
        player := &package.Player{
            FullName: playerName,
            // Populate other fields as necessary
        }

        // Create a Bet instance
        bet := &package.Bet{
            ProfessionalHomeTeam: homeTeam,
            ProfessionalAwayTeam: awayTeam,
            Player:               player,
            Type:                 betTypeProto,
            Points:               float32(points),
            Price:                float32(price),
        }

        // Do something with the bet instance, e.g., print it or add it to a list
        fmt.Println(bet)
    }

    if err := rows.Err(); err != nil {
        log.Fatal(err)
    }
}
