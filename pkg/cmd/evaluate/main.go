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
	draftSlot    = flag.Int("draft_slot", 0, "1-based draft slot to pin the candidate bot to (0 = random order, the default)")
	draftSeed    = flag.Int64("seed", 0, "seed for the surrounding field's draft order when -draft_slot is set (reproducible)")
)

// scratchYear is a throwaway season the engine drafts/replays against so the tracked
// season.db is never mutated. weekly_stats are matched by week (not year), so copying the
// real data here preserves scoring.
const scratchYear = uint32(2999)

const botUnderTestID = "0"

func main() {
	flag.Parse()
	defer cleanupScratch()

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
	fmt.Printf("avg finish     : %.2f  (regular-season rank; neutral = %.2f)\n", float64(sum)/float64(n), float64(*numTeams+1)/2)
	fmt.Printf("championships  : %d/%d\n", champs, n)
	fmt.Printf("playoff apps   : %d/%d\n", playoffApps, n)
	fmt.Printf("worst finish   : #%d\n", worst)
}

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
	// Seed the handler's cached bot order that runDraft reads. Two paths:
	//   - draft_slot unset (0): randomized order each run (GetBotsInRandomOrder).
	//     Load-bearing despite the discarded result; without it the draft falls back to
	//     id-ascending and bot "0" would always pick first.
	//   - draft_slot set: pin the candidate to that 1-based slot, with the surrounding
	//     field seeded so the whole order is reproducible across runs.
	if *draftSlot == 0 {
		if _, err := draftHandler.GetBotsInRandomOrder(); err != nil {
			return nil, err
		}
	} else {
		botsAsc, err := draftHandler.GetBots()
		if err != nil {
			return nil, err
		}
		ordered, err := engine.OrderBotsWithPinnedSlot(botsAsc, botUnderTestID, *draftSlot, *draftSeed)
		if err != nil {
			return nil, err
		}
		draftHandler.SetCachedBotOrder(ordered)
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

// cleanupScratch removes the scratch-year directory created for isolation.
func cleanupScratch() {
	dir, err := common.BuildLocalAbsolutePath(fmt.Sprintf("/data/game_states/%d", scratchYear))
	if err != nil {
		return
	}
	_ = os.RemoveAll(dir)
}
