package common

import (
	"testing"

	"github.com/mitchwebster/botblitz/pkg/common/pb"
)

func TestLandscapeValidations(t *testing.T) {

	team := &pb.FantasyTeam{
		Id:    "001",
		Name:  "Test Team",
		Owner: "test@test.com",
	}

	qbSlot := &pb.PlayerSlot{
		Name:                   "QB",
		CanBeEmpty:             false,
		AllowedPlayerPositions: []string{"QB"},
	}

	settings := &pb.LeagueSettings{
		NumTeams:     1,
		SlotsPerTeam: []*pb.PlayerSlot{qbSlot},
	}

	landscape := &pb.FantasyLandscape{
		MatchNumber: 1,
		Settings:    settings,
		BotTeam:     team,
		Players:     []*pb.Player{},
	}

	if got := ValidateLandscape(landscape); got != true {
		t.Errorf("Failed to ValidateLandscape, result: {%v}", got)
	}
}
