package common

import (
	"testing"
)

func TestLandscapeValidations(t *testing.T) {

	team := &FantasyTeam{
		Id:    "001",
		Name:  "Vulcan Paradise",
		Owner: "mrspock@trek.com",
	}

	qbSlot := &PlayerSlot{
		Name:                   "Captain",
		AllowedPlayerPositions: []string{"Captain"},
	}

	settings := &LeagueSettings{
		NumTeams:     1,
		SlotsPerTeam: []*PlayerSlot{qbSlot},
	}

	landscape := &FantasyLandscape{
		MatchNumber: 1,
		Settings:    settings,
		BotTeam:     team,
		Players:     []*Player{},
	}

	if got := ValidateLandscape(landscape); got != true {
		t.Errorf("Failed to ValidateLandscape, result: {%v}", got)
	}
}
