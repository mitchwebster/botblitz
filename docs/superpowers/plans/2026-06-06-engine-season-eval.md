# Engine-driven Full-Season Bot Evaluation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evaluate a bot by running it through the *real Go engine* over a full historical season — draft, week-over-week waivers + scoring, playoffs — against a containerized baseline field, and report where it finished; reachable with one `make evaluate-bot` command.

**Architecture:** Reuse the engine's existing handlers (`runDraft`, `performWeeklyFantasyActions`, `updateWeeklyScores`, playoff/standings code). Add (1) a season-replay loop + final-standings readout in `pkg/engine`, and (2) a small `pkg/cmd/evaluate` main that builds a baseline-field league, copies the tracked `season.db` to a scratch year for isolation, runs draft → replay → standings over N runs, and prints the bot's finish. Retire the parallel Python evaluator.

**Tech Stack:** Go (engine + gorm/sqlite), the existing `py_grpc_server` Docker image, Makefile.

---

## File Structure

- **Create `pkg/engine/SeasonReplayHandler.go`** — `FinalSeasonWeek` const, `Standing` struct, `(e *BotEngine) FinalStandings()`, `(e *BotEngine) seasonChampion()`, `(e *BotEngine) ReplaySeason(ctx)`.
- **Create `pkg/engine/SeasonReplay_integration_test.go`** — Docker-free test: fixture `season.db`, replay 17 weeks via the engine's own scoring, assert standings + champion.
- **Modify `pkg/engine/Position.go` (or a new `pkg/engine/LeagueSettings.go`)** — exported `BuildDefaultLeagueSettings(year, numTeams uint32) *common.LeagueSettings` (shared by both mains, removing duplication).
- **Modify `pkg/cmd/engine_bootstrap.go`** — `fetchLeagueSettings` delegates to `engine.BuildDefaultLeagueSettings` (DRY).
- **Create `pkg/cmd/evaluate/main.go`** — eval orchestration (baseline field, scratch-year isolation, draft+replay+standings, N runs, reporting).
- **Modify `Makefile`** — `evaluate-bot` target (builds image, then runs).
- **Modify `CLAUDE.md`** — document engine-driven eval + the principle.
- **Delete `harness/evaluate_bot.py`** and its test in `tests/test_claude_bot.py` (`test_claude_beats_default_at_same_slot`).

Key facts the implementer must rely on (verified):
- Engine season/scoring is **container-free**: `updateWeeklyScores(ctx, finishWeek)` reads `weekly_stats` for the current week (`GetPlayerScoresForCurrentWeek`, filters by `week` only — not year), scores the best-possible lineup (`scoreTeam`), sets results, `IncrementFantasyWeek`, and `handlePlayoffsIfNeeded`. Only the draft + weekly *waivers* use containers.
- `initSeason` builds a **14-week** regular schedule; playoffs are weeks **15–17** (`StartingPlayoffWeek=15`, `NumPlayoffTeams=6`, `TeamsReceivingByes=2`). The week-17 playoff matchup winner is the champion.
- `getLeaderboard(bots, GetPastMatchups(StartingPlayoffWeek))` gives regular-season ranks (W/L then points).
- Draft + season paths are **nil-Sheets-safe** (`initializeDraftSheet`, `registerPickInSheets` no-op on nil). Pass `sheetsClient=nil`.
- Tests in `package engine` can call **unexported** methods directly (see `SeasonDb_integration_test.go`).
- `NewGameStateHandlerForDraft(bots, settings)` requires `data/game_states/<settings.Year>/season.db` to already exist; it seeds league-state tables and does NOT recreate players. `LoadGameStateForWeeklyFantasy(year)` reopens the same file and runs `initSeason`. Between phases, close the DB: `sqlDB, _ := handler.GetDB().DB(); sqlDB.Close()`.

---

## Task 1: Season-replay core + final standings (engine, Docker-free, fully tested)

**Files:**
- Create: `pkg/engine/SeasonReplayHandler.go`
- Test: `pkg/engine/SeasonReplay_integration_test.go`

- [ ] **Step 1: Write the failing test**

Create `pkg/engine/SeasonReplay_integration_test.go`. It builds an 8-team fixture where bot `"0"`'s player outscores everyone every week, replays all 17 weeks using the engine's own `updateWeeklyScores`, and asserts bot `"0"` finishes #1 and wins the championship.

```go
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

// buildReplayFixture writes a season.db at the engine-resolved path for replayTestYear:
// 8 QB-only players each rostered to a distinct bot, and weekly_stats for weeks 1..17 where
// player i scores (8-i)*10 every week (so bot "0" always wins). Returns nothing; cleans up.
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
		id := string(rune('A' + i)) // "A".."H"
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
		id := string(rune('0' + i)) // bot ids "0".."7"
		bots[i] = &common.Bot{Id: id, FantasyTeamName: "T" + id, Owner: id}
	}
	settings := BuildDefaultLeagueSettings(replayTestYear, 8)
	settings.SlotsPerTeam = map[string]uint32{"QB": 1}
	settings.TotalRounds = 1

	// Seed league state (bots/settings/game_status), then assign rosters: player i -> bot i.
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

	// Replay scoring for the whole season (Docker-free: no waiver step).
	ctx := context.Background()
	for {
		week, err := seasonHandler.GetCurrentFantasyWeek()
		if err != nil {
			t.Fatalf("get week: %v", err)
		}
		if week > FinalSeasonWeek {
			break
		}
		if err := e.updateWeeklyScores(ctx, true); err != nil {
			t.Fatalf("updateWeeklyScores week %d: %v", week, err)
		}
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd pkg/engine && go test ./... -run TestReplaySeasonProducesStandingsAndChampion`
Expected: compile failure — `BuildDefaultLeagueSettings`, `FinalSeasonWeek`, `FinalStandings`, `Standing` undefined. (We add `BuildDefaultLeagueSettings` in Task 1 Step 3a so this test can compile.)

- [ ] **Step 3a: Add the shared league-settings builder**

Create `pkg/engine/LeagueSettings.go`:

```go
package engine

import common "github.com/mitchwebster/botblitz/pkg/common"

// BuildDefaultLeagueSettings returns the league configuration the project uses: a
// SUPERFLEX roster (QB, RB×2, WR×2, SUPERFLEX, FLEX, K, DST, BENCH×3), snake draft, full PPR.
func BuildDefaultLeagueSettings(year, numTeams uint32) *common.LeagueSettings {
	slots := map[string]uint32{
		QB.String():        1,
		RB.String():        2,
		WR.String():        2,
		SUPERFLEX.String(): 1,
		FLEX.String():      1,
		K.String():         1,
		DST.String():       1,
		BENCH.String():     3,
	}
	var totalRounds uint32
	for _, v := range slots {
		totalRounds += v
	}
	return &common.LeagueSettings{
		NumTeams:           numTeams,
		IsSnakeDraft:       true,
		TotalRounds:        totalRounds,
		PointsPerReception: 1.0,
		Year:               year,
		SlotsPerTeam:       slots,
	}
}
```

- [ ] **Step 3b: Implement the replay handler**

Create `pkg/engine/SeasonReplayHandler.go`:

```go
package engine

import (
	"context"
	"fmt"
	"sort"
)

// FinalSeasonWeek is the last scored week: a 14-week regular season plus the
// three playoff rounds (weeks 15, 16, 17).
const FinalSeasonWeek = 17

// Standing is one team's end-of-season result.
type Standing struct {
	BotID        string
	Name         string
	Rank         uint32 // regular-season rank (1 = best record)
	Wins         uint32
	Losses       uint32
	Points       float32
	MadePlayoffs bool
	IsChampion   bool
}

// ReplaySeason advances the loaded season to completion, running each week's waiver
// actions (bot containers) and then scoring + finishing that week (engine code).
func (e *BotEngine) ReplaySeason(ctx context.Context) error {
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		week, err := e.gameStateHandler.GetCurrentFantasyWeek()
		if err != nil {
			return err
		}
		if week > FinalSeasonWeek {
			return nil
		}
		fmt.Printf("\n===== Replaying fantasy week %d =====\n", week)
		if err := e.performWeeklyFantasyActions(ctx); err != nil {
			return err
		}
		if err := e.updateWeeklyScores(ctx, true); err != nil {
			return err
		}
	}
}

// seasonChampion returns the bot id that won the final playoff matchup, or "" if
// the playoffs have not produced a decided final.
func (e *BotEngine) seasonChampion() (string, error) {
	matchups, err := e.gameStateHandler.GetMatchupsForWeek(FinalSeasonWeek)
	if err != nil {
		return "", err
	}
	for _, m := range matchups {
		if m.IsPlayoffMatchup && m.WinningBotID != nil {
			return *m.WinningBotID, nil
		}
	}
	return "", nil
}

// FinalStandings returns every team's regular-season placement (record + points,
// via the engine's own leaderboard) plus playoff outcome.
func (e *BotEngine) FinalStandings() ([]Standing, error) {
	bots, err := e.gameStateHandler.GetBots()
	if err != nil {
		return nil, err
	}
	regularSeasonMatchups, err := e.gameStateHandler.GetPastMatchups(StartingPlayoffWeek)
	if err != nil {
		return nil, err
	}
	leaderboard := getLeaderboard(bots, regularSeasonMatchups)

	championID, err := e.seasonChampion()
	if err != nil {
		return nil, err
	}

	standings := make([]Standing, 0, len(bots))
	for _, b := range bots {
		r := leaderboard[b.ID]
		standings = append(standings, Standing{
			BotID:        b.ID,
			Name:         b.Name,
			Rank:         r.Ranking,
			Wins:         r.MatchupsWon,
			Losses:       r.MatchupsLost,
			Points:       r.TotalPoints,
			MadePlayoffs: r.Ranking <= NumPlayoffTeams,
			IsChampion:   b.ID == championID,
		})
	}
	sort.Slice(standings, func(i, j int) bool { return standings[i].Rank < standings[j].Rank })
	return standings, nil
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd pkg/engine && go test ./... -run TestReplaySeasonProducesStandingsAndChampion -v`
Expected: PASS. (Bot "0" outscores all opponents every week → 14-0 regular season → #1 seed → wins the bracket → champion.)

- [ ] **Step 5: Run the whole engine test suite (no regressions)**

Run: `cd pkg/engine && go test ./...`
Expected: PASS (existing draft/weekly/playoff/season tests still green).

- [ ] **Step 6: Commit**

```bash
git add pkg/engine/SeasonReplayHandler.go pkg/engine/LeagueSettings.go pkg/engine/SeasonReplay_integration_test.go
git commit -m "feat(engine): season replay loop + final standings

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: DRY the league settings in the existing bootstrap

**Files:**
- Modify: `pkg/cmd/engine_bootstrap.go` (function `fetchLeagueSettings`, ~lines 324-350)

- [ ] **Step 1: Replace the body of `fetchLeagueSettings` to delegate**

Replace the whole `fetchLeagueSettings` function in `pkg/cmd/engine_bootstrap.go` with:

```go
func fetchLeagueSettings(year uint32, numTeams uint32) *common.LeagueSettings {
	return engine.BuildDefaultLeagueSettings(year, numTeams)
}
```

(Leave its call sites unchanged.)

- [ ] **Step 2: Verify it builds and the engine still drafts**

Run: `go build ./...`
Expected: builds cleanly.

Run: `cd pkg/engine && go test ./...`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add pkg/cmd/engine_bootstrap.go
git commit -m "refactor(engine): share BuildDefaultLeagueSettings between mains

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Evaluation command (baseline field + scratch-year isolation + N runs)

**Files:**
- Create: `pkg/cmd/evaluate/main.go`

This is its own `package main` (separate directory so it does not collide with `engine_bootstrap.go`). It cannot run headlessly without Docker, so it is verified by `go build`/`go vet` here; the e2e is owner-run in Task 4.

- [ ] **Step 1: Write the command**

Create `pkg/cmd/evaluate/main.go`:

```go
// Command evaluate runs a bot through the real engine over a full historical season
// against a baseline field, and reports where it finished. It never mutates the tracked
// season.db: each run copies it to a scratch year the engine then drafts/replays against.
package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"github.com/mitchwebster/botblitz/pkg/engine"
	gamestate "github.com/mitchwebster/botblitz/pkg/gamestate"
)

var (
	botPath      = flag.String("bot", "bots/nfl2025/claude_bot.py", "path to the bot under test")
	baselinePath = flag.String("baseline", "bots/nfl2025/standard-bot.py", "path to the baseline opponent bot")
	dataYear     = flag.Int("year", 2025, "season whose data (season.db) to evaluate against")
	numTeams     = flag.Int("teams", 14, "number of teams in the league")
	runs         = flag.Int("runs", 1, "number of independent season simulations")
)

// scratchYear is a throwaway season the engine drafts/replays against so the tracked
// season.db is never mutated. weekly_stats are matched by week (not year), so copying the
// real data here preserves scoring.
const scratchYear = uint32(2999)

func main() {
	flag.Parse()

	finishes := make([]int, 0, *runs)
	champs := 0
	playoffApps := 0

	for run := 1; run <= *runs; run++ {
		fmt.Printf("\n######## EVALUATION RUN %d/%d ########\n", run, *runs)
		standings, err := runOneSeason(context.Background())
		if err != nil {
			fmt.Printf("run %d failed: %v\n", run, err)
			os.Exit(1)
		}
		for i, s := range standings {
			if s.BotID == botUnderTestID {
				finishes = append(finishes, i+1)
				if s.IsChampion {
					champs++
				}
				if s.MadePlayoffs {
					playoffApps++
				}
				fmt.Printf("\n>>> %s finished #%d (%d-%d, %.1f pts, champion=%v)\n",
					*botPath, i+1, s.Wins, s.Losses, s.Points, s.IsChampion)
			}
		}
	}

	n := len(finishes)
	if n == 0 {
		fmt.Println("No finishes recorded.")
		return
	}
	sum := 0
	worst := 0
	for _, f := range finishes {
		sum += f
		if f > worst {
			worst = f
		}
	}
	fmt.Printf("\n======== %s vs %d-team baseline field over %d run(s) ========\n",
		*botPath, *numTeams, n)
	fmt.Printf("avg finish     : %.2f  (neutral = %.2f)\n", float64(sum)/float64(n), float64(*numTeams+1)/2)
	fmt.Printf("championships  : %d/%d\n", champs, n)
	fmt.Printf("playoff apps   : %d/%d\n", playoffApps, n)
	fmt.Printf("worst finish   : #%d\n", worst)
}

const botUnderTestID = "0"

func runOneSeason(ctx context.Context) ([]engine.Standing, error) {
	if err := resetScratchSeasonDB(uint32(*dataYear)); err != nil {
		return nil, err
	}

	bots, srcMap := buildLeague()
	settings := engine.BuildDefaultLeagueSettings(scratchYear, uint32(*numTeams))

	// ---- Draft phase (containers) ----
	draftHandler, err := gamestate.NewGameStateHandlerForDraft(bots, settings)
	if err != nil {
		return nil, err
	}
	if _, err := draftHandler.GetBotsInRandomOrder(); err != nil {
		return nil, err
	}
	draftEngine := engine.NewBotEngine(
		draftHandler,
		engine.BotEngineSettings{GameMode: engine.Draft},
		nil, srcMap, map[string][]string{},
	)
	if err := draftEngine.Run(ctx); err != nil {
		draftEngine.CleanupAllPyGrpcServerContainers()
		return nil, err
	}
	if err := draftEngine.CleanupAllPyGrpcServerContainers(); err != nil {
		return nil, err
	}
	if sqlDB, derr := draftHandler.GetDB().DB(); derr == nil {
		sqlDB.Close()
	}

	// ---- Season replay phase (containers for waivers + engine scoring) ----
	seasonHandler, err := gamestate.LoadGameStateForWeeklyFantasy(scratchYear)
	if err != nil {
		return nil, err
	}
	seasonEngine := engine.NewBotEngine(
		seasonHandler,
		engine.BotEngineSettings{GameMode: engine.PerformWeeklyFantasyActions},
		nil, srcMap, map[string][]string{},
	)
	replayErr := seasonEngine.ReplaySeason(ctx)
	seasonEngine.CleanupAllPyGrpcServerContainers()
	if replayErr != nil {
		return nil, replayErr
	}
	return seasonEngine.FinalStandings()
}

// buildLeague returns the bot configs (id "0" = bot under test, rest baseline) plus a
// source-code map keyed by bot id.
func buildLeague() ([]*common.Bot, map[string][]byte) {
	bots := make([]*common.Bot, *numTeams)
	srcMap := make(map[string][]byte)

	botSrc := mustRead(*botPath)
	baselineSrc := mustRead(*baselinePath)

	for i := 0; i < *numTeams; i++ {
		id := fmt.Sprintf("%d", i)
		if i == 0 {
			bots[i] = &common.Bot{Id: id, SourceType: common.Bot_LOCAL, SourcePath: enginePath(*botPath),
				Owner: "Candidate", FantasyTeamName: "Candidate"}
			srcMap[id] = botSrc
		} else {
			bots[i] = &common.Bot{Id: id, SourceType: common.Bot_LOCAL, SourcePath: enginePath(*baselinePath),
				Owner: fmt.Sprintf("Baseline%d", i), FantasyTeamName: fmt.Sprintf("Baseline%d", i)}
			srcMap[id] = baselineSrc
		}
	}
	return bots, srcMap
}

// enginePath converts a repo-relative path ("bots/nfl2025/x.py") to the leading-slash form
// the engine's BuildLocalAbsolutePath expects ("/bots/nfl2025/x.py").
func enginePath(p string) string {
	if len(p) > 0 && p[0] == '/' {
		return p
	}
	return "/" + p
}

func mustRead(p string) []byte {
	abs, err := common.BuildLocalAbsolutePath(enginePath(p))
	if err != nil {
		fmt.Printf("cannot resolve %s: %v\n", p, err)
		os.Exit(1)
	}
	b, err := os.ReadFile(abs)
	if err != nil {
		fmt.Printf("cannot read %s: %v\n", abs, err)
		os.Exit(1)
	}
	return b
}

// resetScratchSeasonDB copies data/game_states/<dataYear>/season.db to
// data/game_states/<scratchYear>/season.db so each run starts from a pristine, undrafted
// season without mutating the tracked file.
func resetScratchSeasonDB(dataYear uint32) error {
	// Resolve via BuildLocalAbsolutePath (the same repo-root join the engine uses for
	// bot SourcePath), passing leading-slash repo-relative paths.
	src, err := common.BuildLocalAbsolutePath(fmt.Sprintf("/data/game_states/%d/season.db", dataYear))
	if err != nil {
		return err
	}
	dst, err := common.BuildLocalAbsolutePath(fmt.Sprintf("/data/game_states/%d/season.db", scratchYear))
	if err != nil {
		return err
	}

	if _, err := os.Stat(src); err != nil {
		return fmt.Errorf("source season.db not found at %s (run build-season): %w", src, err)
	}
	if err := os.MkdirAll(filepath.Dir(dst), os.ModePerm); err != nil {
		return err
	}
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer out.Close()
	_, err = io.Copy(out, in)
	return err
}
```

- [ ] **Step 2: Verify it compiles and vets**

Run: `go build ./...`
Expected: builds cleanly (the new `pkg/cmd/evaluate` main + everything else).

Run: `go vet ./pkg/cmd/evaluate/...`
Expected: no findings.

- [ ] **Step 3: Commit**

```bash
git add pkg/cmd/evaluate/main.go
git commit -m "feat(eval): engine-driven season evaluation command

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Makefile target, docs, and retire the Python proxy

**Files:**
- Modify: `Makefile`
- Modify: `CLAUDE.md`
- Delete: `harness/evaluate_bot.py`
- Modify: `tests/test_claude_bot.py` (remove `test_claude_beats_default_at_same_slot`)

- [ ] **Step 1: Add the Makefile target**

Append to `Makefile` (use a TAB-indented recipe; mirror existing targets' style):

```makefile
evaluate-bot:
	$(MAKE) build-docker
	go run ./pkg/cmd/evaluate -bot=$(BOT) -year=$(YEAR) -runs=$(RUNS)
```

With defaults so a bare `make evaluate-bot` works, add near the top of the Makefile (after any existing variable defaults):

```makefile
BOT ?= bots/nfl2025/claude_bot.py
YEAR ?= 2025
RUNS ?= 1
```

- [ ] **Step 2: Remove the Python evaluator and its test**

Run:
```bash
git rm harness/evaluate_bot.py
```

Edit `tests/test_claude_bot.py`: delete the entire `test_claude_beats_default_at_same_slot` function (it imports `harness.evaluate_bot`, which no longer exists).

Run: `PYTHONPATH=. python3 -m pytest tests/test_claude_bot.py -q`
Expected: PASS (the remaining claude_bot unit tests are unaffected).

- [ ] **Step 3: Document in CLAUDE.md**

In `CLAUDE.md` §7 (Local simulation / harness) add a subsection:

```markdown
### Evaluating a bot (engine-driven, authoritative)
`make evaluate-bot BOT=bots/nfl2025/<bot>.py YEAR=2025 RUNS=3` builds the
`py_grpc_server` image, then runs the bot through the **real engine** over a full
historical season — draft, weekly waivers + scoring, playoffs — against a baseline
field of `standard-bot` opponents (all containerized), and prints where it finished.
It copies `season.db` to a scratch year (2999) per run, so the tracked DB is never
mutated. This is the source of truth for "is my bot good"; the Python `harness/`
(`simulate_draft`, `score_game`, `SimulateDraft.ipynb`) remains only an interactive
dev aid, not an evaluator. Entry: `pkg/cmd/evaluate/main.go`;
season loop + standings: `pkg/engine/SeasonReplayHandler.go`.
```

Also add a one-line principle to §1 or §9: "Evaluation/running goes through the Go engine (the production path); the Python CLI (`bootstrap_data`) is for data bootstrapping only."

- [ ] **Step 4: Verify build + tests**

Run: `go build ./... && cd pkg/engine && go test ./...`
Expected: builds; engine tests PASS.

Run: `PYTHONPATH=. python3 -m pytest tests/test_claude_bot.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Makefile CLAUDE.md tests/test_claude_bot.py
git commit -m "feat(eval): make evaluate-bot target + docs; retire python evaluator

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Owner-run end-to-end validation (manual, documented)

**Files:** none (verification only)

- [ ] **Step 1: Run the full evaluation**

Run: `make evaluate-bot BOT=bots/nfl2025/claude_bot.py YEAR=2025 RUNS=3`
Expected: builds the image, runs 3 full seasons (draft + 17 weeks of waivers/scoring + playoffs) of claude_bot vs a 13-team `standard-bot` field, and prints per-run finishes + an aggregate (avg finish, championships, playoff apps, worst finish). This requires a working Docker daemon and is heavy (14 containers × draft + 17 weeks).

- [ ] **Step 2: Record results**

Note the aggregate in the final report. If a run errors inside a container, capture stderr (run with the engine's verbose logging if needed) and treat as a real finding (systematic-debugging), not a reason to weaken the eval.

---

## Notes for the implementer

- **Do not** modify bot-facing APIs, the proto, or other bots.
- The engine season/scoring path is container-free; only draft + weekly waivers use Docker. Task 1's test deliberately drives `updateWeeklyScores` directly (no waivers) so it needs no Docker.
- `weekly_stats` is matched by **week only**, so copying the real `season.db` to a scratch year preserves scoring — that is what keeps each eval run isolated and the tracked DB pristine.
- Keep `harness/simulate_draft.py`, `harness/score_game.py`, and `SimulateDraft.ipynb` — they are still useful interactive aids; only `harness/evaluate_bot.py` (the parallel evaluator) is retired.
