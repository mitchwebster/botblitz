package engine

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/mitchwebster/botblitz/pkg/common"
	"github.com/mitchwebster/botblitz/pkg/gamestate"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

// testSeasonYear is a throwaway year so the fixture never collides with real
// data under data/game_states/. There is deliberately NO data/stats/<year>/stats.db
// sibling: the OLD engine (populateStatsTables) would fail here; the new engine must not.
const testSeasonYear = uint32(2999)

// buildFixtureSeasonDB creates a season.db at the exact path the engine resolves
// (cwd-relative, matching getSaveFileName), pre-seeded the way `build-season` would:
// a populated `players` table plus a `weekly_stats` reference table. It returns the
// path and registers cleanup of the whole data/game_states/<year> dir.
func buildFixtureSeasonDB(t *testing.T) string {
	t.Helper()

	cwd, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	dir := filepath.Join(cwd, "data", "game_states", "2999")
	if err := os.MkdirAll(dir, os.ModePerm); err != nil {
		t.Fatalf("mkdir fixture dir: %v", err)
	}
	t.Cleanup(func() { os.RemoveAll(filepath.Join(cwd, "data", "game_states", "2999")) })

	seasonPath := filepath.Join(dir, "season.db")
	_ = os.Remove(seasonPath)

	db, err := gorm.Open(sqlite.Open(seasonPath), &gorm.Config{})
	if err != nil {
		t.Fatalf("open fixture db: %v", err)
	}

	// players pool (as build-season materializes it)
	if err := db.AutoMigrate(&gamestate.Player{}); err != nil {
		t.Fatalf("migrate players: %v", err)
	}
	players := []gamestate.Player{
		{ID: "19788", FullName: "Ja'Marr Chase", ProfessionalTeam: "CIN", AllowedPositions: `["WR"]`, Rank: 1, Availability: "AVAILABLE"},
		{ID: "1001", FullName: "Test QB", ProfessionalTeam: "FA", AllowedPositions: `["QB"]`, Rank: 2, Availability: "AVAILABLE"},
	}
	for i := range players {
		if err := db.Create(&players[i]).Error; err != nil {
			t.Fatalf("insert player: %v", err)
		}
	}

	// reference stats table (as build-season copies from the scrape cache). Raw SQL:
	// it is not a GORM model. Columns mirror what GetPlayerScoresForCurrentWeek reads.
	if err := db.Exec(`CREATE TABLE weekly_stats (fantasypros_id TEXT, week INTEGER, FPTS REAL);`).Error; err != nil {
		t.Fatalf("create weekly_stats: %v", err)
	}
	if err := db.Exec(`INSERT INTO weekly_stats (fantasypros_id, week, FPTS) VALUES ('19788', 1, 25.0);`).Error; err != nil {
		t.Fatalf("seed weekly_stats: %v", err)
	}

	sqlDB, _ := db.DB()
	sqlDB.Close()
	return seasonPath
}

func testBotsAndSettings() ([]*common.Bot, *common.LeagueSettings) {
	bots := []*common.Bot{
		{Id: "0", FantasyTeamName: "Alpha", Owner: "A"},
		{Id: "1", FantasyTeamName: "Beta", Owner: "B"},
	}
	settings := &common.LeagueSettings{
		Year:               testSeasonYear,
		NumTeams:           2,
		TotalRounds:        2,
		IsSnakeDraft:       true,
		PointsPerReception: 1.0,
		SlotsPerTeam:       map[string]uint32{"QB": 1, "WR": 1},
	}
	return bots, settings
}

func TestDraftOpensExistingSeasonDbWithoutCopyingStats(t *testing.T) {
	seasonPath := buildFixtureSeasonDB(t)
	bots, settings := testBotsAndSettings()

	handler, err := gamestate.NewGameStateHandlerForDraft(bots, settings)
	if err != nil {
		t.Fatalf("NewGameStateHandlerForDraft: %v", err)
	}

	// It opened the SAME pre-seeded file, not a gs-draft.db.
	if handler.GetDBSaveFilePath() != seasonPath {
		t.Errorf("expected save path %q, got %q", seasonPath, handler.GetDBSaveFilePath())
	}

	db := handler.GetDB()

	// Players were NOT re-inserted: exactly the 2 fixture rows remain.
	var playerCount int64
	if err := db.Model(&gamestate.Player{}).Count(&playerCount).Error; err != nil {
		t.Fatalf("count players: %v", err)
	}
	if playerCount != 2 {
		t.Errorf("expected 2 pre-seeded players, got %d", playerCount)
	}

	// League-state tables were migrated + populated into the same file.
	gotBots, err := handler.GetBots()
	if err != nil {
		t.Fatalf("GetBots: %v", err)
	}
	if len(gotBots) != 2 {
		t.Errorf("expected 2 bots, got %d", len(gotBots))
	}
	if _, err := handler.GetLeagueSettings(); err != nil {
		t.Fatalf("GetLeagueSettings: %v", err)
	}

	// Stats were NOT copied: season_stats/preseason_projections must be absent,
	// and the pre-seeded weekly_stats must survive untouched.
	if db.Migrator().HasTable("season_stats") {
		t.Error("season_stats should not exist (engine must not copy stats)")
	}
	if !db.Migrator().HasTable("weekly_stats") {
		t.Error("pre-seeded weekly_stats table disappeared")
	}
}

func TestSeasonLoadRunsInitSeasonOnSameFile(t *testing.T) {
	_ = buildFixtureSeasonDB(t)
	bots, settings := testBotsAndSettings()

	// Draft first to populate league state (bots + game_status with week 0).
	draftHandler, err := gamestate.NewGameStateHandlerForDraft(bots, settings)
	if err != nil {
		t.Fatalf("draft handler: %v", err)
	}
	sqlDB, _ := draftHandler.GetDB().DB()
	sqlDB.Close() // release the file before re-opening for the season phase

	// Season load opens the SAME season.db, runs initSeason (matchups), advances to week 1.
	seasonHandler, err := gamestate.LoadGameStateForWeeklyFantasy(testSeasonYear)
	if err != nil {
		t.Fatalf("LoadGameStateForWeeklyFantasy: %v", err)
	}

	week, err := seasonHandler.GetCurrentFantasyWeek()
	if err != nil {
		t.Fatalf("GetCurrentFantasyWeek: %v", err)
	}
	if week != 1 {
		t.Errorf("expected initSeason to set week 1, got %d", week)
	}

	matchups, err := seasonHandler.GetMatchupsForWeek(1)
	if err != nil {
		t.Fatalf("GetMatchupsForWeek: %v", err)
	}
	if len(matchups) == 0 {
		t.Error("expected initSeason to generate week-1 matchups")
	}

	// Reads weekly_stats straight from season.db — no RefreshWeeklyStats needed.
	scores, _, err := seasonHandler.GetPlayerScoresForCurrentWeek()
	if err != nil {
		t.Fatalf("GetPlayerScoresForCurrentWeek: %v", err)
	}
	_ = scores // current week is 1; the fixture row may or may not be on a roster — call must just succeed
}
