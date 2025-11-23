package engine

import (
	"testing"

	"github.com/mitchwebster/botblitz/pkg/gamestate"
)

func TestGeneratePlayoffs_6Teams_Top2Byes(t *testing.T) {
	numPlayoffTeams := uint32(6)
	teamsReceivingByes := uint32(2)

	rankings := map[uint32]string{
		1: "T1",
		2: "T2",
		3: "T3",
		4: "T4",
		5: "T5",
		6: "T6",
	}

	matchups, err := generatePlayoffMatchupsInternal(15, numPlayoffTeams, teamsReceivingByes, rankings)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// -------------------------------------------
	// First-round matchups = (num + byes) / 2
	// (6 + 2) / 2 = 4 matchups
	// -------------------------------------------
	expectedFirstRound := int((numPlayoffTeams + teamsReceivingByes) / 2)
	if len(matchups) < expectedFirstRound {
		t.Fatalf("expected %d first-round matchups, got %d",
			expectedFirstRound, len(matchups))
	}

	// Check first round matchups
	expectedRound1 := [][2]string{
		{"T1", gamestate.ByeId}, // bye matchup
		{"T2", gamestate.ByeId}, // bye matchup
		{"T3", "T6"},
		{"T4", "T5"},
	}

	for i, exp := range expectedRound1 {
		m := matchups[i]
		if m.HomeBotID != exp[0] || m.VisitorBotID != exp[1] {
			t.Errorf("round 1 matchup %d expected %s vs %s, got %s vs %s",
				i, exp[0], exp[1], m.HomeBotID, m.VisitorBotID)
		}
	}

	// -------------------------------------------
	// Total matchups for 6-team bracket:
	// round1 = 4, round2 = 2, finals = 1 → 7 total
	// -------------------------------------------
	if len(matchups) != 7 {
		t.Errorf("expected 7 total matchups for 6-team playoff, got %d", len(matchups))
	}
}

func TestGeneratePlayoffs_8Teams_NoByes(t *testing.T) {
	numPlayoffTeams := uint32(8)
	teamsReceivingByes := uint32(0)

	rankings := map[uint32]string{
		1: "T1", 2: "T2", 3: "T3", 4: "T4",
		5: "T5", 6: "T6", 7: "T7", 8: "T8",
	}

	matchups, err := generatePlayoffMatchupsInternal(15, numPlayoffTeams, teamsReceivingByes, rankings)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Round 1 expected: 1v8, 2v7, 3v6, 4v5
	expectedRound1 := [][2]string{
		{"T1", "T8"},
		{"T2", "T7"},
		{"T3", "T6"},
		{"T4", "T5"},
	}

	for i, exp := range expectedRound1 {
		if matchups[i].HomeBotID != exp[0] || matchups[i].VisitorBotID != exp[1] {
			t.Errorf("round 1 matchup %d expected %s vs %s, got %s vs %s",
				i, exp[0], exp[1], matchups[i].HomeBotID, matchups[i].VisitorBotID)
		}
	}

	// Total should be 7 matchups
	if len(matchups) != 7 {
		t.Errorf("expected 7 total matchups for 8-team playoff, got %d", len(matchups))
	}
}

func TestGeneratePlayoffs_AllMatchesPlayoffFlag(t *testing.T) {
	numPlayoffTeams := uint32(6)
	teamsReceivingByes := uint32(2)

	rankings := map[uint32]string{
		1: "T1", 2: "T2", 3: "T3",
		4: "T4", 5: "T5", 6: "T6",
	}

	matchups, err := generatePlayoffMatchupsInternal(15, numPlayoffTeams, teamsReceivingByes, rankings)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	for i, m := range matchups {
		if !m.IsPlayoffMatchup {
			t.Errorf("matchup %d was not marked as a playoff matchup", i)
		}
	}
}

func TestGeneratePlayoffs_6Teams_Top2Byes_WithReferences(t *testing.T) {
	numPlayoffTeams := uint32(6)
	teamsReceivingByes := uint32(2)

	rankings := map[uint32]string{
		1: "T1", 2: "T2", 3: "T3", 4: "T4", 5: "T5", 6: "T6",
	}

	matchups, err := generatePlayoffMatchupsInternal(15, numPlayoffTeams, teamsReceivingByes, rankings)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// -------------------------
	// Check first-round matchups
	// -------------------------
	expectedRound1 := [][2]string{
		{"T1", gamestate.ByeId}, // bye matchup
		{"T2", gamestate.ByeId}, // bye matchup
		{"T3", "T6"},
		{"T4", "T5"},
	}

	for i, exp := range expectedRound1 {
		m := matchups[i]
		if m.HomeBotID != exp[0] || m.VisitorBotID != exp[1] {
			t.Errorf("round 1 matchup %d expected %s vs %s, got %s vs %s",
				i, exp[0], exp[1], m.HomeBotID, m.VisitorBotID)
		}
		if m.HomePlayInMatchupID != nil || m.VisitorPlayInMatchupID != nil {
			t.Errorf("round 1 matchup %d should not reference earlier matchups", i)
		}
	}

	// -------------------------
	// Check second-round matchups
	// -------------------------
	// In a 6-team bracket: round2 = 2 matchups
	round2Start := 4
	round2End := 5
	for i := round2Start; i <= round2End; i++ {
		m := matchups[i]
		if m.HomePlayInMatchupID == nil || m.VisitorPlayInMatchupID == nil {
			t.Errorf("round 2 matchup %d does not reference previous matchups", i)
		} else {
			homeIdx := *m.HomePlayInMatchupID - uint(playoffMatchupStartingIndex)
			visitorIdx := *m.VisitorPlayInMatchupID - uint(playoffMatchupStartingIndex)

			if homeIdx < 0 || homeIdx >= uint(len(matchups)) || visitorIdx < 0 || visitorIdx >= uint(len(matchups)) {
				t.Errorf("round 2 matchup %d references invalid previous matchup", i)
			}
		}
	}

	// -------------------------
	// Check finals matchup
	// -------------------------
	finalMatchup := matchups[len(matchups)-1]
	expectedHome := matchups[4].ID
	expectedVisitor := matchups[5].ID

	if finalMatchup.HomePlayInMatchupID == nil || *finalMatchup.HomePlayInMatchupID != expectedHome {
		t.Errorf("final matchup home should reference matchup %d, got %v", 4, finalMatchup.HomePlayInMatchupID)
	}
	if finalMatchup.VisitorPlayInMatchupID == nil || *finalMatchup.VisitorPlayInMatchupID != expectedVisitor {
		t.Errorf("final matchup visitor should reference matchup %d, got %v", 5, finalMatchup.VisitorPlayInMatchupID)
	}
}
