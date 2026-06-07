package engine

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/mitchwebster/botblitz/pkg/common"
	"github.com/mitchwebster/botblitz/pkg/gamestate"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

const replayTestYear = uint32(2996)

func buildReplayFixture(t *testing.T) {
	t.Helper()
	cwd, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	dir := filepath.Join(cwd, "data", "game_states", "2996")
	if err := os.MkdirAll(dir, os.ModePerm); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	seasonPath := filepath.Join(dir, "season.db")
	_ = os.Remove(seasonPath)

	db, err := gorm.Open(sqlite.Open(seasonPath), &gorm.Config{})
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	if err := db.AutoMigrate(&gamestate.Player{}); err != nil {
		t.Fatalf("migrate players: %v", err)
	}
	for i := 0; i < 8; i++ {
		id := string(rune('A' + i))
		if err := db.Create(&gamestate.Player{
			ID: id, FullName: "P" + id, ProfessionalTeam: "FA",
			AllowedPositions: `["QB"]`, Rank: i + 1, Availability: "AVAILABLE",
		}).Error; err != nil {
			t.Fatalf("insert player: %v", err)
		}
	}
	if err := db.Exec(`CREATE TABLE weekly_stats (fantasypros_id TEXT, week INTEGER, FPTS REAL);`).Error; err != nil {
		t.Fatalf("create weekly_stats: %v", err)
	}
	for i := 0; i < 8; i++ {
		id := string(rune('A' + i))
		for wk := 1; wk <= 17; wk++ {
			if err := db.Exec(`INSERT INTO weekly_stats (fantasypros_id, week, FPTS) VALUES (?, ?, ?);`,
				id, wk, float64((8-i)*10)).Error; err != nil {
				t.Fatalf("seed weekly_stats: %v", err)
			}
		}
	}
	sqlDB, _ := db.DB()
	sqlDB.Close()
}

func TestReplaySeasonProducesStandingsAndChampion(t *testing.T) {
	buildReplayFixture(t)

	bots := make([]*common.Bot, 8)
	for i := 0; i < 8; i++ {
		id := string(rune('0' + i))
		bots[i] = &common.Bot{Id: id, FantasyTeamName: "T" + id, Owner: id}
	}
	settings := BuildDefaultLeagueSettings(replayTestYear, 8)
	settings.SlotsPerTeam = map[string]uint32{"QB": 1}
	settings.TotalRounds = 1

	draftHandler, err := gamestate.NewGameStateHandlerForDraft(bots, settings)
	if err != nil {
		t.Fatalf("draft handler: %v", err)
	}
	ddb := draftHandler.GetDB()
	for i := 0; i < 8; i++ {
		playerID := string(rune('A' + i))
		botID := string(rune('0' + i))
		if err := ddb.Exec(`UPDATE players SET current_bot_id = ?, availability = 'DRAFTED' WHERE id = ?;`,
			botID, playerID).Error; err != nil {
			t.Fatalf("assign roster: %v", err)
		}
	}
	sqlDB, _ := ddb.DB()
	sqlDB.Close()

	seasonHandler, err := gamestate.LoadGameStateForWeeklyFantasy(replayTestYear)
	if err != nil {
		t.Fatalf("load season: %v", err)
	}
	e := NewBotEngine(seasonHandler, BotEngineSettings{GameMode: UpdateWeeklyScores}, nil, nil, nil)

	ctx := context.Background()
	if err := e.ReplaySeason(ctx); err != nil {
		t.Fatalf("ReplaySeason: %v", err)
	}

	standings, err := e.FinalStandings()
	if err != nil {
		t.Fatalf("FinalStandings: %v", err)
	}
	if len(standings) != 8 {
		t.Fatalf("expected 8 standings, got %d", len(standings))
	}
	top := standings[0]
	if top.BotID != "0" {
		t.Errorf("expected bot 0 to finish first, got %s", top.BotID)
	}
	if top.Rank != 1 || top.Wins != 14 {
		t.Errorf("expected bot 0 rank 1 with 14 wins, got rank %d wins %d", top.Rank, top.Wins)
	}
	if !top.MadePlayoffs {
		t.Error("expected bot 0 to make the playoffs")
	}
	if !top.IsChampion {
		t.Error("expected bot 0 to be champion")
	}
}
