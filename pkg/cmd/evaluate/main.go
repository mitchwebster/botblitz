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
	botPath      = flag.String("bot", "bots/nfl2025/standard-bot.py", "path to the bot under test")
	baselinePath = flag.String("baseline", "bots/nfl2025/standard-bot.py", "path to the baseline opponent bot")
	dataYear     = flag.Int("year", 2025, "season whose data (season.db) to evaluate against")
	numTeams     = flag.Int("teams", 14, "number of teams in the league")
	runs         = flag.Int("runs", 1, "number of independent season simulations")
	// Deliberately verbose and unflattering: this trades production fidelity for speed,
	// and the name should make any user who passes it aware of that. Off by default so
	// the evaluator's container lifecycle matches the real engine (fresh containers per
	// phase and per run).
	reuseContainers = flag.Bool("optimize-by-reusing-containers-wont-match-prod", false,
		"reuse ONE container set across all draft/season phases and all runs instead of rebuilding per phase/run. "+
			"Much faster for local iteration, but DIVERGES from production: a stateful bot will carry in-memory state "+
			"across phases and across runs that are meant to be independent seasons. Use only to speed up local runs.")
)

// scratchYear is a throwaway season the engine drafts/replays against so the tracked
// season.db is never mutated. weekly_stats are matched by week (not year), so copying the
// real data here preserves scoring.
const scratchYear = uint32(2999)

const botUnderTestID = "0"

func main() {
	flag.Parse()
	defer cleanupScratch()

	// In the default (prod-matching) mode sharedContainers stays nil and each run/phase
	// builds and tears down its own containers, exactly like the real engine. When the
	// opt-in flag is set, one container set is created lazily on the first run and reused
	// for every subsequent phase and run, then torn down once here after all runs.
	var sharedContainers map[string]*engine.BotContainerInfo
	if *reuseContainers {
		fmt.Println("⚠️  --optimize-by-reusing-containers-wont-match-prod is ON: one container set is reused " +
			"across all phases and runs. Faster, but does NOT match the production container lifecycle.")
		sharedContainers = make(map[string]*engine.BotContainerInfo)
		defer cleanupContainers()
	}

	finishes := make([]int, 0, *runs)
	champs := 0
	playoffApps := 0

	for run := 1; run <= *runs; run++ {
		fmt.Printf("\n######## EVALUATION RUN %d/%d ########\n", run, *runs)
		standings, err := runOneSeason(context.Background(), sharedContainers)
		if err != nil {
			fmt.Printf("run %d failed: %v\n", run, err)
			// os.Exit skips deferred cleanup, so tear down explicitly here.
			cleanupContainers()
			cleanupScratch()
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
	fmt.Printf("avg finish     : %.2f  (regular-season rank; neutral = %.2f)\n", float64(sum)/float64(n), float64(*numTeams+1)/2)
	fmt.Printf("championships  : %d/%d\n", champs, n)
	fmt.Printf("playoff apps   : %d/%d\n", playoffApps, n)
	fmt.Printf("worst finish   : #%d\n", worst)
}

// runOneSeason drafts and replays one full season.
//
// sharedContainers controls the container lifecycle:
//   - nil (default): each phase builds and tears down its own containers, matching the
//     production engine's lifecycle.
//   - non-nil (opt-in via --optimize-by-reusing-containers-wont-match-prod): both engines
//     reuse this evaluation-wide set, so the draft→season boundary and every run after the
//     first reuse already-running containers instead of rebuilding the field. Safe across
//     runs because the scratch season.db is reset in place (same inode), so a live
//     container's read-only DB mount sees the fresh, undrafted state each run; teardown
//     happens once, in main, after all runs complete.
func runOneSeason(ctx context.Context, sharedContainers map[string]*engine.BotContainerInfo) ([]engine.Standing, error) {
	defer engine.LogElapsed("season run (total)")()

	reuse := sharedContainers != nil

	if err := resetScratchSeasonDB(uint32(*dataYear)); err != nil {
		return nil, err
	}

	bots, srcMap := buildLeague()
	settings := engine.BuildDefaultLeagueSettings(scratchYear, uint32(*numTeams))

	// ---- Draft phase (containers) ----
	draftPhaseDone := engine.LogElapsed("draft phase (setup + draft + teardown)")
	draftHandler, err := gamestate.NewGameStateHandlerForDraft(bots, settings)
	if err != nil {
		return nil, err
	}
	// Load-bearing despite the discarded result: this seeds the handler's cached bot
	// order that runDraft reads, so the draft order is randomized each run. Without it
	// the draft falls back to id-ascending and bot "0" would always pick first.
	if _, err := draftHandler.GetBotsInRandomOrder(); err != nil {
		return nil, err
	}
	draftEngine := engine.NewBotEngine(
		draftHandler,
		engine.BotEngineSettings{GameMode: engine.Draft, InlineExecution: reuse},
		nil, srcMap, map[string][]string{},
	)
	if reuse {
		draftEngine.UseSharedContainers(sharedContainers)
	}
	if err := draftEngine.Run(ctx); err != nil {
		if !reuse {
			draftEngine.CleanupAllPyGrpcServerContainers()
		}
		return nil, err
	}
	if !reuse {
		if err := draftEngine.CleanupAllPyGrpcServerContainers(); err != nil {
			return nil, err
		}
	}
	if sqlDB, derr := draftHandler.GetDB().DB(); derr == nil {
		sqlDB.Close()
	}
	draftPhaseDone()

	// ---- Season replay phase (containers for waivers + engine scoring) ----
	seasonPhaseDone := engine.LogElapsed("season replay phase (setup + replay + teardown)")
	defer seasonPhaseDone()

	seasonHandler, err := gamestate.LoadGameStateForWeeklyFantasy(scratchYear)
	if err != nil {
		return nil, err
	}
	seasonEngine := engine.NewBotEngine(
		seasonHandler,
		engine.BotEngineSettings{GameMode: engine.PerformWeeklyFantasyActions, InlineExecution: reuse},
		nil, srcMap, map[string][]string{},
	)
	if reuse {
		seasonEngine.UseSharedContainers(sharedContainers)
	}
	replayErr := seasonEngine.ReplaySeason(ctx)
	if !reuse {
		seasonEngine.CleanupAllPyGrpcServerContainers()
	}
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
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	return out.Close()
}

// cleanupContainers tears down every py_grpc_server container once, after all runs.
// CleanupAllPyGrpcServerContainers is image-wide and needs no engine state, so a
// throwaway engine is fine here.
func cleanupContainers() {
	e := engine.NewBotEngine(nil, engine.BotEngineSettings{}, nil, nil, nil)
	if err := e.CleanupAllPyGrpcServerContainers(); err != nil {
		fmt.Printf("warning: container cleanup failed: %v\n", err)
	}
}

// cleanupScratch removes the scratch-year directory created for isolation.
func cleanupScratch() {
	dir, err := common.BuildLocalAbsolutePath(fmt.Sprintf("/data/game_states/%d", scratchYear))
	if err != nil {
		return
	}
	_ = os.RemoveAll(dir)
}
