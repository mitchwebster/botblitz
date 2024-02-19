package bets

import (
	"regexp"
	"testing"

	"github.com/DATA-DOG/go-sqlmock"
	"github.com/stretchr/testify/assert"
)

func Test_GetAllBets(t *testing.T) {
	// Initialize the mock object
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatalf("An error '%s' was not expected when opening a stub database connection", err)
	}
	defer db.Close()

	// Define your expected rows
	rows := sqlmock.NewRows([]string{"id", "homeTeam", "awayTeam", "playerName", "type", "points", "price"}).
		AddRow(1, "Team A", "Team B", "Player 1", "Over", 200.5, 1.90).
		AddRow(2, "Team C", "Team D", "Player 2", "Under", 198.5, 1.85)

	// Set up the expectation: When there's a query to the database, return the defined rows
	mock.ExpectQuery(regexp.QuoteMeta(`SELECT id, homeTeam, awayTeam, playerName, type, points, price FROM bets`)).WillReturnRows(rows)

	// Call the function under test
	bets, err := GetAllBets(db)

	// Assertions
	assert.NoError(t, err)
	assert.Len(t, bets, 2)

	// You can add more assertions here to verify the contents of the bets

	// Ensure all expectations were met
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Errorf("There were unfulfilled expectations: %s", err)
	}
}
