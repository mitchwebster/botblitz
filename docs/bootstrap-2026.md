# Bootstrapping a new season (the 2026 procedure)

The repo currently ships a working **2025** `data/game_states/2025/season.db`.
A real 2026 ship was deferred because it needs a `player_ranks_2026.csv` (the
draftable pool) that only the R scraper produces, plus in-season scraped data.
This is the end-to-end procedure to ship year `Y` (e.g. 2026) when the data and
toolchain are available.

## Prerequisites

- **R + the `ffpros` package** (for `fetch_ranks.R`). Not required for any other
  part of the engine; only for generating the rank CSV.
- Network access to FantasyPros and NFL.com.

## Steps

1. **Generate the draftable pool** (`player_ranks_Y.csv`) from FantasyPros draft
   rankings:

       Rscript fetch_ranks.R Y          # writes player_ranks_Y.csv at repo root

   `load_players` reads `blitz_env/player_ranks_Y.csv`; `make build-py-module`
   copies the root CSV into `blitz_env/`. (As of Plan 3, `load_players` accepts
   any year that has a CSV — no code change needed to add a year.)

2. **Scrape the reference data** into the scrape cache
   `data/stats/Y/stats.db` (network; ~180 NFL.com requests for injuries, 5–10 min,
   possible rate limiting):

       make bootstrap-data-scrape YEAR=Y

   Offseason note: season/weekly *actuals* will be empty until games are played;
   preseason projections are available pre-season. The rolling weekly tables fill
   in over the year via the `update-scores` job (step 5).

3. **Materialize the per-season DB** (offline; copies reference tables + builds the
   `players` pool):

       make bootstrap-data-build-season YEAR=Y     # -> data/game_states/Y/season.db

4. **Commit** the prebuilt artifacts so players clone with data ready:

       git add player_ranks_Y.csv blitz_env/player_ranks_Y.csv data/game_states/Y/season.db
       git commit -m "feat: ship prebuilt Y season.db"

   (`blitz_env/player_ranks_Y.csv` is normally gitignored/generated; force-add it
   only if you want it tracked. The root `player_ranks_Y.csv` is the tracked source.)

5. **Point the live weekly job at year Y.** In `.github/workflows/update-scores.yml`:
   - the three `collect_weekly_*` collectors already write `--db data/game_states/<year>/season.db`;
     bump `<year>` and `--year` to `Y`.
   - the score-update step runs `go run pkg/cmd/engine_bootstrap.go -game_mode=UpdateWeeklyScores -year=Y`.
   - the PR `add-paths` lists `data/game_states/Y/season.db`.
   - update the `PAST_DATE` week-calculation to season Y's Week-1 kickoff date.
   - re-enable the `schedule:` triggers when the season starts.

6. **(Optional) Make Y the local default.** The engine's `-year` flag defaults to
   2025 (`pkg/cmd/engine_bootstrap.go`); pass `-year=Y` to `make run-draft` /
   `run-weekly-fantasy` / `update-scores`, or change the default when Y becomes the
   active season.

## Why 2025 is the shipped default today

2025 is fully buildable offline from the tracked `data/stats/2025/stats.db` cache,
so `make run-draft` (which defaults to `-year=2025`) and the harness work against a
real, complete season immediately — no scrape, no R, no network.
