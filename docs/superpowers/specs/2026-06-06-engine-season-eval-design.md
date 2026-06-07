# Engine-driven full-season bot evaluation

Date: 2026-06-06
Status: Approved (design) — pending spec review

## Why

Evaluating `claude_bot` required writing `harness/evaluate_bot.py`, which re-implements
draft order (`simulate_draft`), lineup scoring (`score_game`), and a finish metric — a
**parallel implementation** of logic the Go engine already runs for real every week. A bot
that looks good against that Python model may still behave wrong in the engine. The fix
(owner's direction): **evaluation runs through the Go engine**, the Python CLI is for data
**bootstrapping only**, and the parallel Python eval/scoring is retired.

A full season is not just a draft — rosters change weekly via **waiver add/drops**. So the
replay must run the bots' weekly actions too, which means the eval exercises BOTH
`draft_player` and `perform_weekly_fantasy_actions` through the real engine.

## What the engine already provides (reused as-is)

- **Draft**: `runDraft` runs each bot in its container, validates picks, writes rosters to
  `season.db` (`DraftHandler.go`, `ContainerHandler.go`).
- **Weekly actions**: `performWeeklyFantasyActions` → `performFAABAddDrop` runs each bot's
  container and applies waiver claims (`WeeklyFantasyHandler.go`).
- **Scoring**: `updateWeeklyScores(finishWeek)` reads that week's `weekly_stats`, computes the
  best-possible lineup per team (`scoreTeam`, greedy most-specific-slot-first — identical to
  what the Python `score_game` duplicated), sets match results, advances the week
  (`IncrementFantasyWeek`), and builds/advances playoffs (`EndOfWeekHandler.go`,
  `PlayoffHandler.go`).
- **Standings**: `getLeaderboard(bots, pastMatchups)` → W/L + total points ranking;
  `initSeason` builds a 14-week round-robin; playoffs run weeks 15–17 (`NumPlayoffTeams=6`,
  2 byes); the final playoff matchup winner is the champion.

The engine drives one week per invocation in production (via scheduled CI). The eval simply
**loops those same handlers** over the historical weeks already present in `season.db`.

## Design

### Components

1. **`pkg/engine/SeasonReplayHandler.go`** (new)
   - `func (e *BotEngine) ReplaySeason(ctx) error` — for week = current..final:
     `performWeeklyFantasyActions(ctx)` then `updateWeeklyScores(ctx, finishWeek=true)`. Stops
     after the playoff final (week 17). Pure orchestration over existing methods.
   - `func (e *BotEngine) FinalStandings() ([]Standing, error)` — regular-season leaderboard
     (`getLeaderboard` over weeks 1..14) plus playoff result (champion = winner of the last
     playoff matchup). `Standing{BotID, Name, Rank, Wins, Losses, Points, MadePlayoffs,
     PlayoffResult}`.

2. **`pkg/cmd/evaluate/main.go`** (new, separate `main` package so it doesn't collide with
   `engine_bootstrap.go`)
   - Flags: `-bot` (path to the bot-under-test source, e.g. `bots/nfl2025/claude_bot.py`),
     `-year` (default 2025), `-runs` (default 1), `-baseline` (path to the baseline opponent
     source, default `bots/nfl2025/standard-bot.py`), `-slot` (optional fixed draft slot;
     default randomized as the engine already does).
   - Builds the league: 1 bot-under-test + (NumTeams-1) bots all pointing at the baseline
     source; **no env files** (baseline + claude_bot need none). Sheets disabled.
   - Per run: `NewGameStateHandlerForDraft` → `runDraft` → `LoadGameStateForWeeklyFantasy`
     → `ReplaySeason` → `FinalStandings`; records the bot-under-test's finish. Cleans up
     containers (`CleanupAllPyGrpcServerContainers`).
   - Prints per-run finish + an aggregate summary over `-runs` (avg finish, championships,
     playoff rate).

3. **Baseline field**: `(NumTeams-1)` copies of `standard-bot.py`, distinct bot IDs, same
   source. Reproducible, secret-free, no other-author flakiness.

4. **Makefile**: `evaluate-bot` target — **self-contained**: first runs `build-docker`
   (so the `py_grpc_server` image the containers need is freshly built and ready), then
   `go run ./pkg/cmd/evaluate -bot=$(BOT) -year=$(YEAR) -runs=$(RUNS)`. One command does
   everything end to end.

5. **Retire the Python proxy**: delete `harness/evaluate_bot.py` and its test
   (`test_claude_beats_default_at_same_slot`); the engine path is now authoritative. Keep
   `harness/simulate_draft.py` + `SimulateDraft.ipynb` (interactive single-draft viz) and
   `score_game.py` (DB inspector) as dev aids, but they are no longer the evaluator.

6. **Docs**: CLAUDE.md §7/§8 — document `make evaluate-bot` as the way to evaluate a bot, and
   state the principle (engine = source of truth for running/evaluation; Python CLI =
   data bootstrap).

### Data flow per run

```
build league (claude_bot + 13× standard-bot, sheets off, no envs)
  -> NewGameStateHandlerForDraft -> runDraft           [containers]
  -> LoadGameStateForWeeklyFantasy (initSeason)
  -> ReplaySeason: for wk 1..17 {
         performWeeklyFantasyActions                    [containers]
         updateWeeklyScores(finishWeek=true)            [pure DB: score, results, playoffs]
     }
  -> FinalStandings -> record claude_bot's finish
  -> CleanupAllPyGrpcServerContainers
```

### Realities / decisions

- **Docker every week** (waivers). Heavier than a draft-only check, but it is the production
  path and also evaluates the weekly logic. Acceptable.
- **Field = baseline** (decided): bot-under-test + copies of `standard-bot.py`. No env files.
- **No lineup-setting action exists** in the engine — scoring is always best-possible from the
  roster — so weekly bot involvement is waivers only.
- **Randomness**: draft order (`GetBotsInRandomOrder`) and `initSeason` matchups use RANDOM,
  so each run is one sample; `-runs` aggregates. (A fixed `-slot` reduces draft-order variance
  for A/B comparisons.)
- **Future-week peeking** (known caveat, deferred): a historical `season.db` holds all 17
  weeks, so a misbehaving bot could query future `weekly_stats` mid-replay. Honest bots query
  only the current week. A future enhancement can hide weeks > current during replay.

## Decomposition (implementation order)

- **Slice 1 — Season replay core + standings (no Docker; fully Go-testable).**
  `ReplaySeason` (the `updateWeeklyScores` loop) + `FinalStandings`. Tested with a fixture
  `season.db` (pre-set rosters + multi-week `weekly_stats`, extending
  `SeasonDb_integration_test.go`): the higher-scoring roster finishes first, playoffs progress
  weeks 15–17, a champion is crowned. *(Replays scoring without the waiver step, which is what
  is unit-testable without containers.)*
- **Slice 2 — Eval orchestration + baseline field.** `pkg/cmd/evaluate/main.go` (league build,
  draft, full replay incl. `performWeeklyFantasyActions`, standings, repetitions, reporting).
- **Slice 3 — Makefile target + CLAUDE.md docs + retire Python `evaluate_bot.py`.**

## Verification

- **Go tests** (`go test ./pkg/engine/...`): Slice 1 replay/standings over the fixture DB.
- **Compile**: `go build ./...` for the new cmd.
- **End-to-end** (owner-run; builds the image itself): `make evaluate-bot
  BOT=bots/nfl2025/claude_bot.py YEAR=2025 RUNS=3` → builds `py_grpc_server`, then runs
  claude_bot's finish vs a baseline field through the real engine. *(Full e2e is not runnable
  in CI/this session — it needs the Docker build + 14 containers × 17 weeks; Slice 1 is.)*

## Out of scope

- Real-league field (kept behind a future flag), trades, hiding future weeks, parallelizing
  runs, and any change to bot-facing APIs.
