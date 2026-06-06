# Single Per-Season DB — Plan 2: Go Engine Rewire Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewire the Go engine to read and write a single `data/game_states/{year}/season.db` that Plan 1's `build-season` already materializes — deleting the stats-copy machinery (`populateStatsTables` / `RefreshWeeklyStats`), collapsing the `gs-draft.db` → `gs-season.db` handoff into one file, and migrating only the league-state tables into the pre-seeded DB.

**Architecture:** Plan 1's `bootstrap_data build-season` produces `season.db` containing the reference tables (`season_stats`, `weekly_stats`, `weekly_projections`, `preseason_projections`, `weekly_injuries`) and the draftable `players` pool. After this plan, the engine **opens that existing file** for both draft and season phases, `AutoMigrate`s the league-state tables (`bots`, `game_statuses`, `league_settings`, `weekly_lineups`, `transactions`) into it, and never copies stats again. Weekly stats land in `season.db` because the `update-scores` job (Python collectors) appends directly to it; the Go engine just reads `weekly_stats`. The container mount is unchanged in shape — it already mounts whatever `GetDBSaveFilePath()` returns (now `season.db`) read-only at the in-container `gamestate.db` path.

**Tech Stack:** Go 1.24, GORM + `gorm.io/driver/sqlite`, SQLite. Tests with the standard `testing` package in `pkg/engine` (the only module in `go.work` that imports `pkg/gamestate`).

**Out of scope (other plans):**
- Plan 1 (done) — Python data foundation: `bootstrap_data` CLI, `DatabaseManager` accessors, `StatsDB`/`ProjectionsDB` removal, harness on `season.db`.
- Plan 3 — archive 2025; run `scrape 2026` + `build-season 2026` and commit the prebuilt `season.db`; flip default years / `PAST_DATE` season-start; rewrite CLAUDE.md §9; flag/migrate `StatsDB`-using bots.

---

## Background the executor needs

**How the Go modules are laid out (this bites you if you skip it):**
- `go.work` lists only `./pkg/cmd`, `./pkg/common`, `./pkg/engine`. **`pkg/gamestate` is NOT in `go.work`** — it has its own `go.mod` and is pulled in via `replace` directives in `pkg/engine/go.mod` and `pkg/cmd/go.mod`.
- Consequence: `go build ./...` / `go test ./...` from the **repo root** fails ("directory prefix . does not contain modules listed in go.work"), and from **`pkg/gamestate`** fails ("outside module roots"). You MUST run Go commands from inside `pkg/engine` or `pkg/cmd`. Editing `pkg/gamestate/handler.go` is picked up transitively when you build/test `pkg/engine`.
- Therefore the integration test for the gamestate rewire lives in **package `engine`** (`pkg/engine/`), which can import `gamestate` and exercise its handlers without Docker.

**Path resolution:** `common.BuildLocalAbsolutePath(rel)` returns `os.Getwd() + "/" + rel`. In a `pkg/engine` test the cwd is `pkg/engine/`, so `getSaveFileName(year)` resolves to `pkg/engine/data/game_states/{year}/season.db`. The test fixture creates and cleans up under that cwd-relative path using a throwaway year so it never touches real data.

**Why deleting `populateStatsTables` is safe to prove via the test:** the old `NewGameStateHandlerForDraft` called `populateStatsTables`, which requires a sibling `data/stats/{year}/stats.db` and errors if absent. The new flow has no such dependency. A draft test against a throwaway year (no `stats.db` sibling) therefore **fails on the old code and passes on the new** — a natural red/green.

**Schema compatibility (already verified):** the `players` table in `season.db` is created by Plan 1's SQLAlchemy `Player` model and includes every column the GORM `gamestate.Player` model reads/writes (`id`, `full_name`, `professional_team`, `player_bye_week`, `rank`, `tier`, `position_rank`, `position_tier`, `gsis_id`, `allowed_positions`, `availability`, `pick_chosen`, `current_bot_id`). The engine reads/updates this table but never re-creates it.

---

## File Structure

**Modify:**
- `pkg/gamestate/handler.go` — the core rewire. Constants, `getSaveFileName`, `NewGameStateHandlerForDraft`, `populateDatabase`; delete `populateStatsTables`, `RefreshWeeklyStats`, `getStatsDatabaseFilePath`, `populatePlayersTable`, `loadPlayers`, `verifyFileDoesNotExistAndCreate`; drop the `encoding/csv` import.
- `pkg/engine/EndOfWeekHandler.go` — drop the `RefreshWeeklyStats()` call from `updateWeeklyScores`.
- `.github/workflows/update-scores.yml` — point the weekly collectors and the score-update run at `season.db`; fix the PR `add-paths`.

**Create:**
- `pkg/engine/SeasonDb_integration_test.go` — Docker-free integration test exercising `gamestate.NewGameStateHandlerForDraft` + `LoadGameStateForWeeklyFantasy` against a fixture `season.db`.

**Verify-only (expected: no change):**
- `pkg/engine/ContainerHandler.go` — confirm the mount still resolves (it mounts `GetDBSaveFilePath()` → now `season.db`, read-only, target `gamestate.db`). No edit expected.

---

## Task 1: Integration test for the rewired draft + season flow (red)

This test encodes the target behavior. It will FAIL against the current code (which copies stats / refuses an existing file) and pass once Tasks 2–3 land.

**Files:**
- Create: `pkg/engine/SeasonDb_integration_test.go`

- [ ] **Step 1: Write the failing integration test**

Create `pkg/engine/SeasonDb_integration_test.go`:

```go
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
```

- [ ] **Step 2: Run it to verify it fails**

Run (from `pkg/engine`):
```bash
cd pkg/engine && go test ./... -run 'TestDraftOpensExistingSeasonDbWithoutCopyingStats|TestSeasonLoadRunsInitSeasonOnSameFile' -v
```
Expected: FAIL. The current `NewGameStateHandlerForDraft` calls `verifyFileDoesNotExistAndCreate` (errors because the fixture `season.db` already exists) and `populateStatsTables` (errors: no `data/stats/2999/stats.db`). You should see an error like `Draft file already exists` or `stats.db file does not exist`.

- [ ] **Step 3: Commit the red test**

```bash
cd /Users/mitch/Documents/botblitz
git add pkg/engine/SeasonDb_integration_test.go
git commit -m "test: integration test for season.db draft+season rewire (red)"
```

---

## Task 2: Repoint paths — `getSaveFileName` → `season.db`, draft opens the existing file

**Files:**
- Modify: `pkg/gamestate/handler.go`

- [ ] **Step 1: Collapse the filename constants**

In `pkg/gamestate/handler.go`, replace the constant block (currently lines ~19–25):

```go
const saveFolderRelativePath = "data/game_states"
const statsFolderRelativePath = "data/stats"
const filePrefix = "gs-"
const draftDesc = "draft"
const seasonDesc = "season"
const fileSuffix = ".db"
const statsDatabaseFileName = "stats.db"
```

with:

```go
const saveFolderRelativePath = "data/game_states"
const fileSuffix = ".db"
const seasonDatabaseFileName = "season" + fileSuffix
```

(`AppDatabaseName = "gamestate" + fileSuffix` on the next line is unchanged — it is the in-container target name, not the host filename.)

- [ ] **Step 2: Simplify `getSaveFileName` to resolve `season.db`**

Replace the whole `getSaveFileName` function (currently ~lines 910–924):

```go
func getSaveFileName(year uint32, description string) (string, error) {
	absFolderPath, err := getSaveFolderPath(year)
	if err != nil {
		return "", err
	}

	err = os.MkdirAll(absFolderPath, os.ModePerm)
	if err != nil {
		return "", err
	}

	// we want files named like gs-draft.db or gs-week1.db
	fileName := filePrefix + description + fileSuffix
	return absFolderPath + "/" + fileName, nil
}
```

with:

```go
func getSaveFileName(year uint32) (string, error) {
	absFolderPath, err := getSaveFolderPath(year)
	if err != nil {
		return "", err
	}

	err = os.MkdirAll(absFolderPath, os.ModePerm)
	if err != nil {
		return "", err
	}

	// One canonical per-season DB: data/game_states/{year}/season.db
	return absFolderPath + "/" + seasonDatabaseFileName, nil
}
```

- [ ] **Step 3: Update the season-load caller**

In `LoadGameStateForWeeklyFantasy` (~line 428), change:

```go
	saveFileName, err := getSaveFileName(year, seasonDesc)
```

to:

```go
	saveFileName, err := getSaveFileName(year)
```

- [ ] **Step 4: Make the draft open the existing pre-seeded `season.db`**

In `NewGameStateHandlerForDraft` (~lines 565–597), change the path lookup + existence guard. Replace:

```go
	saveFileName, err := getSaveFileName(settings.Year, draftDesc)
	if err != nil {
		return nil, err
	}

	err = verifyFileDoesNotExistAndCreate(saveFileName)
	if err != nil {
		return nil, err
	}
```

with:

```go
	saveFileName, err := getSaveFileName(settings.Year)
	if err != nil {
		return nil, err
	}

	// season.db must already exist (materialized by `make bootstrap-data-build-season`);
	// the engine opens it in place rather than creating a fresh draft file.
	if !fileExists(saveFileName) {
		return nil, fmt.Errorf(
			"season.db not found at %q; run `make bootstrap-data-build-season YEAR=%d` first",
			saveFileName, settings.Year,
		)
	}
```

- [ ] **Step 5: Migrate only league-state tables; drop `Player` from the draft AutoMigrate**

Still in `NewGameStateHandlerForDraft`, the `AutoMigrate` call (~line 586) currently is:

```go
	// Auto migrate the database tables
	err = db.AutoMigrate(&Bot{}, &gameStatus{}, &LeagueSettings{}, &Player{}, &WeeklyLineup{}, &Transaction{})
	if err != nil {
		return nil, err
	}
```

Replace with (drop `&Player{}` — the pool is pre-seeded by `build-season`; the engine must not re-create or re-migrate it):

```go
	// season.db already carries the `players` pool + reference tables. The engine
	// owns only the league-state tables, so migrate just those into the existing file.
	err = db.AutoMigrate(&Bot{}, &gameStatus{}, &LeagueSettings{}, &WeeklyLineup{}, &Transaction{})
	if err != nil {
		return nil, err
	}
```

- [ ] **Step 6: Build (expect a compile error from the now-unused stats functions)**

Run (from `pkg/engine`):
```bash
cd pkg/engine && go build ./... 2>&1 | head
```
Expected: compile errors — `getSaveFileName` is still called with two args inside `populateDatabase`'s now-dead helpers, and `populateStatsTables`/`verifyFileDoesNotExistAndCreate`/`filePrefix`/`draftDesc`/`seasonDesc`/`statsFolderRelativePath`/`statsDatabaseFileName` are now undefined or unused. These are resolved in Task 3. (If the build happens to pass, that is fine; proceed.)

Do NOT commit yet — Task 3 removes the dead code so the package compiles cleanly.

---

## Task 3: Delete the stats-copy machinery and dead draft helpers

**Files:**
- Modify: `pkg/gamestate/handler.go`

- [ ] **Step 1: Stop populating players + stats during the draft**

In `populateDatabase` (~lines 599–626), remove the two trailing calls so it only seeds league state. Replace:

```go
func populateDatabase(db *gorm.DB, bots []*common.Bot, settings *common.LeagueSettings) error {
	err := populateBotsTable(db, bots)
	if err != nil {
		return err
	}

	err = populateLeagueSettingsTable(db, settings)
	if err != nil {
		return err
	}

	err = populateGameStatusTable(db, bots)
	if err != nil {
		return err
	}

	err = populatePlayersTable(db, settings.Year)
	if err != nil {
		return err
	}

	err = populateStatsTables(db, settings.Year)
	if err != nil {
		return err
	}

	return nil
}
```

with:

```go
func populateDatabase(db *gorm.DB, bots []*common.Bot, settings *common.LeagueSettings) error {
	err := populateBotsTable(db, bots)
	if err != nil {
		return err
	}

	err = populateLeagueSettingsTable(db, settings)
	if err != nil {
		return err
	}

	err = populateGameStatusTable(db, bots)
	if err != nil {
		return err
	}

	// players + reference tables already live in season.db (from build-season).
	return nil
}
```

- [ ] **Step 2: Delete `populatePlayersTable` and `loadPlayers`**

Delete the entire `populatePlayersTable` function (~lines 672–686) and the entire `loadPlayers` function (~lines 817–893). Both are now unreferenced (the engine no longer seeds the player pool). `loadPlayers` was the only consumer of the CSV reader.

- [ ] **Step 3: Delete `getStatsDatabaseFilePath`, `RefreshWeeklyStats`, `populateStatsTables`**

Delete these three functions in full:
- `getStatsDatabaseFilePath` (~lines 705–713)
- `RefreshWeeklyStats` (~lines 715–770) — its only caller is removed in Task 4
- `populateStatsTables` (~lines 772–802)

- [ ] **Step 4: Delete `verifyFileDoesNotExistAndCreate`**

Delete the `verifyFileDoesNotExistAndCreate` function (~lines 895–908). The draft now requires the file to exist (Task 2, Step 4) and uses `fileExists` instead. **Keep `fileExists`** — it is now used by the draft existence guard.

- [ ] **Step 5: Drop the unused `encoding/csv` import**

In the import block at the top of `handler.go`, remove the line:

```go
	"encoding/csv"
```

(`encoding/json`, `fmt`, `os`, `strconv`, `strings`, `time` all remain in use.)

- [ ] **Step 6: Build clean**

Run (from `pkg/engine`):
```bash
cd pkg/engine && go build ./... && go vet ./...
```
Expected: no output, exit 0. If `go vet`/build reports an unused import, function, or constant, remove exactly that symbol (it is dead per this plan) and re-run.

- [ ] **Step 7: Run the integration test (now green)**

Run (from `pkg/engine`):
```bash
cd pkg/engine && go test ./... -run 'TestDraftOpensExistingSeasonDbWithoutCopyingStats|TestSeasonLoadRunsInitSeasonOnSameFile' -v
```
Expected: both tests PASS.

- [ ] **Step 8: Commit**

```bash
cd /Users/mitch/Documents/botblitz
git add pkg/gamestate/handler.go
git commit -m "refactor: engine opens season.db; drop stats-copy + player-seed machinery"
```

---

## Task 4: Remove the `RefreshWeeklyStats` call from weekly scoring

**Files:**
- Modify: `pkg/engine/EndOfWeekHandler.go`

- [ ] **Step 1: Drop the refresh step**

In `pkg/engine/EndOfWeekHandler.go`, `updateWeeklyScores` (~lines 15–24) currently starts:

```go
func (e *BotEngine) updateWeeklyScores(ctx context.Context, finishWeek bool) error {
	err := e.gameStateHandler.RefreshWeeklyStats()
	if err != nil {
		return err
	}

	playerScores, currentFantasyWeek, err := e.gameStateHandler.GetPlayerScoresForCurrentWeek()
	if err != nil {
		return err
	}
```

Replace with (weekly stats already live in `season.db`, appended by the `update-scores` collectors — just read them):

```go
func (e *BotEngine) updateWeeklyScores(ctx context.Context, finishWeek bool) error {
	// weekly_stats are appended directly into season.db by the update-scores job,
	// so there is no longer a copy/refresh step here — read them straight through.
	playerScores, currentFantasyWeek, err := e.gameStateHandler.GetPlayerScoresForCurrentWeek()
	if err != nil {
		return err
	}
```

- [ ] **Step 2: Build + full engine test suite**

Run (from `pkg/engine`):
```bash
cd pkg/engine && go build ./... && go test ./...
```
Expected: build clean; all tests PASS (existing suite + the two new integration tests).

- [ ] **Step 3: Confirm `pkg/cmd` still builds (it imports gamestate too)**

Run (from `pkg/cmd`):
```bash
cd pkg/cmd && go build ./...
```
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
cd /Users/mitch/Documents/botblitz
git add pkg/engine/EndOfWeekHandler.go
git commit -m "refactor: weekly scoring reads weekly_stats from season.db (no RefreshWeeklyStats)"
```

---

## Task 5: Verify the container mount still resolves (no code change expected)

**Files:**
- Inspect: `pkg/engine/ContainerHandler.go`

- [ ] **Step 1: Confirm the mount points at `season.db` read-only at the in-container `gamestate.db` path**

Read `pkg/engine/ContainerHandler.go` around the `Mounts` block (~lines 230–252). Confirm all three of these still hold:
1. `databaseFilePath := appServerFolderPath + "/" + gamestate.AppDatabaseName` → `/app/py_grpc_server/gamestate.db` (the SDK's default `DatabaseManager.DB_URL` target; unchanged).
2. The second bind mount uses `Source: e.gameStateHandler.GetDBSaveFilePath()` — which now returns `data/game_states/{year}/season.db` — with `ReadOnly: true` and `Target: databaseFilePath`.
3. No literal `gs-`, `draft`, or `season.db` string is hardcoded here.

Because the source is derived from `GetDBSaveFilePath()`, repointing `getSaveFileName` (Task 2) is sufficient and **no edit to `ContainerHandler.go` is expected**.

- [ ] **Step 2: Record the verification (no commit)**

If all three hold, make no change. If — contrary to expectation — a literal old path is found, repoint its `Source` to `e.gameStateHandler.GetDBSaveFilePath()` and commit with message `fix: container mount sources season.db via GetDBSaveFilePath`. Otherwise there is nothing to commit for this task.

---

## Task 6: Repoint `update-scores` to append weekly rows into `season.db`

The weekly job currently scrapes into the old scrape cache `data/stats/2025/stats.db` and relied on the Go `RefreshWeeklyStats` copy (now deleted). Repoint the collectors to write straight into `season.db`, run the Go score-update against the same year, and commit the right file. Per the design, the new layout begins at **2026**.

**Note (deferred to Plan 3):** the `PAST_DATE` week-calculation (currently `2025-09-04`) and any default-year changes in the `Makefile` are Plan 3's concern (shipping the prebuilt 2026 `season.db` and archiving 2025). The schedule triggers remain commented out (offseason), so this workflow only runs via `workflow_dispatch`; this task's edits are verified by inspection, not a live run.

**Files:**
- Modify: `.github/workflows/update-scores.yml`

- [ ] **Step 1: Point the three weekly collectors at `season.db`**

In `.github/workflows/update-scores.yml`, in the "Calculate current week and run scripts" step, change each collector's `--db` target from the old scrape cache to the season DB, and bump the year to 2026. Replace:

```yaml
        # Get weekly stats added to stats.db (mission critical - must succeed)
        python3 -m blitz_env.collect_weekly_stats --year 2025 --week $CURRENT_WEEK --db data/stats/2025/stats.db

        # Get weekly projections added to stats.db (important but not critical - continue on failure)
        echo "Collecting weekly projections for week $(( CURRENT_WEEK + 1 ))..."
        if python3 -m blitz_env.collect_weekly_projections --year 2025 --week $(( CURRENT_WEEK + 1 )) --db data/stats/2025/stats.db; then
          echo "✓ Weekly projections collected successfully"
        else
          echo "⚠ Warning: Failed to collect weekly projections (non-critical, continuing)"
        fi

        # Get weekly injuries added to stats.db (important but not critical - continue on failure)
        echo "Collecting weekly injuries..."
        if python3 -m blitz_env.collect_weekly_injuries --year 2025 --week $CURRENT_WEEK --db data/stats/2025/stats.db; then
          echo "✓ Weekly injuries collected successfully"
        else
          echo "⚠ Warning: Failed to collect weekly injuries (non-critical, continuing)"
        fi
```

with (collectors now append directly into `data/game_states/2026/season.db`):

```yaml
        # Append weekly stats directly into season.db (mission critical - must succeed)
        python3 -m blitz_env.collect_weekly_stats --year 2026 --week $CURRENT_WEEK --db data/game_states/2026/season.db

        # Append weekly projections into season.db (important but not critical - continue on failure)
        echo "Collecting weekly projections for week $(( CURRENT_WEEK + 1 ))..."
        if python3 -m blitz_env.collect_weekly_projections --year 2026 --week $(( CURRENT_WEEK + 1 )) --db data/game_states/2026/season.db; then
          echo "✓ Weekly projections collected successfully"
        else
          echo "⚠ Warning: Failed to collect weekly projections (non-critical, continuing)"
        fi

        # Append weekly injuries into season.db (important but not critical - continue on failure)
        echo "Collecting weekly injuries..."
        if python3 -m blitz_env.collect_weekly_injuries --year 2026 --week $CURRENT_WEEK --db data/game_states/2026/season.db; then
          echo "✓ Weekly injuries collected successfully"
        else
          echo "⚠ Warning: Failed to collect weekly injuries (non-critical, continuing)"
        fi
```

- [ ] **Step 2: Run the Go score-update against the 2026 `season.db`**

The `make update-scores` target runs the engine with its default `-year` (2025). Point this run at 2026 explicitly so it reads `data/game_states/2026/season.db`. Replace:

```yaml
    - name: Update fantasy week scores
      run: make update-scores
```

with:

```yaml
    - name: Update fantasy week scores
      run: go run pkg/cmd/engine_bootstrap.go -game_mode=UpdateWeeklyScores -year=2026
```

- [ ] **Step 3: Commit the right file in the PR**

The PR step currently adds `data/*.db` (which does NOT match `data/game_states/2026/season.db`). Replace:

```yaml
        add-paths: |
            data/*.db
```

with:

```yaml
        add-paths: |
            data/game_states/2026/season.db
```

- [ ] **Step 4: Validate the YAML parses**

Run:
```bash
cd /Users/mitch/Documents/botblitz && python3 -c "import yaml; yaml.safe_load(open('.github/workflows/update-scores.yml')); print('YAML OK')"
```
Expected: `YAML OK`.

- [ ] **Step 5: Commit**

```bash
cd /Users/mitch/Documents/botblitz
git add .github/workflows/update-scores.yml
git commit -m "ci: update-scores appends weekly rows into season.db (2026)"
```

---

## Plan 2 self-review checklist (run before handoff)

- [ ] From `pkg/engine`: `go build ./... && go vet ./... && go test ./...` — all green (includes the two new integration tests).
- [ ] From `pkg/cmd`: `go build ./...` — green.
- [ ] `grep -rn "RefreshWeeklyStats\|populateStatsTables\|getStatsDatabaseFilePath\|gs-draft\|gs-season\|filePrefix\|draftDesc\|seasonDesc" pkg/` returns nothing (all references gone).
- [ ] `grep -rn "getSaveFileName" pkg/` shows only the single-arg definition and its two callers.
- [ ] `git status` clean — no stray `pkg/engine/data/game_states/2999/` fixture left behind (the test cleans it up; verify).
- [ ] `.github/workflows/update-scores.yml` references `season.db`, not `data/stats/.../stats.db`, in all three collectors; `add-paths` matches the committed file.

## Follow-on plan (not in this plan)

- **Plan 3 — Migration & docs:** move `data/stats/2025` + `data/game_states/2025` to `data/archive/2025`; run `scrape 2026` + `build-season 2026` and commit the prebuilt `season.db`; fix the workflow `PAST_DATE` season-start and any `Makefile` default-year; rewrite CLAUDE.md §9 for the single per-season DB; flag/migrate `ryan_bot.py` and the archive bots off the removed `StatsDB`.
```
