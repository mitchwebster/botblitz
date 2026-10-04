# Claude Code Documentation — BotBlitz

Context and rules for working in this repo (human + agentic). These instructions take
precedence over default behavior.

## 1. What this repo is

A bot-vs-bot **fantasy football engine**. Human-written Python "bots" draft players and make
weekly roster decisions; a Go engine orchestrates them, each running sandboxed in its own
Docker container. It has always been a fantasy-football project — a brief NBA exploration was
abandoned and left only stray artifacts (not the repo's origin).

Season evolution: **2024 = draft only. 2025 = added season gameplay + add/drop**, and moved
the draft/scoring data model to **SQLite**.

## 2. Languages & ownership

- **Go (`pkg/`)** — the engine: orchestration, Docker container lifecycle, game state /
  SQLite, draft + weekly-fantasy + playoff logic, Google Sheets output.
- **Python (`blitz_env/`, `py_grpc_server/`, `bots/`)** — the runtime bot SDK (`blitz_env`),
  the gRPC server that runs inside each container, stats-collection scripts, and user bots.
- **Python (`harness/`)** — local testing/simulation (NOT shipped to the container).
- **R (`fetch_ranks.R`, `fetch_projections.R`, `fetch_stats.R`, `fetch_injuries.R`,
  `fetch_playerids.R`, `fetch_ffanalytics_projections.R`)** — all network sourcing for
  the bootstrap pipeline (draftable pool, projections, actual stats, injuries, ID
  crosswalk, multi-source projections), via the ffverse (`ffpros`, `nflreadr`) and
  `ffanalytics`. This is the one place that logic lives; Python only shells out to
  these scripts and handles the SQLite upsert.
- **JS/React (`ux/`)** — a season.db viewer (create-react-app). Loads a season.db
  directly in the browser via `sql.js` (WASM SQLite) — either the local copy at
  `ux/public/season.db` (`npm run start:local`, which copies it in first) or, on a
  deployed build, `data/game_states/2026/season.db` fetched from a given git branch
  (`?branch=` URL param, defaults to `main`). Tabs: Current/Last Week, Matchup
  Details, Leaderboard, Rosters, Projections, and Players (searchable, sortable,
  actual-vs-projected toggle, click a player for a full weekly profile —
  points/stats/opponent/injury side by side with every projection source).

## 3. ⚠️ Guardrails (read before refactoring)

- **`blitz_env` is the public runtime SDK / wheel API.** It is built into a wheel
  (`blitz_env-0.1.0-py3-none-any.whl`) baked into the `py_grpc_server` Docker image, and bots
  import it at runtime (some historical bots fetch source from pinned GitHub raw URLs).
  Renaming/removing a top-level name is a breaking change.
- **Keep `blitz_env` lean.** `blitz_env/__init__.py` must never import `matplotlib`, anything
  under `bots/`, or simulation code — it is loaded inside the container where no `bots`
  package exists. `is_drafted` lives in `blitz_env/player_utils.py` (protobuf, dependency-light)
  precisely so `import blitz_env` stays light; `py_grpc_server/bot.py` depends on it.
- **Single data backend: sqlite via `models.DatabaseManager` (`init_database`).** The legacy
  CSV/in-memory backend was removed in the 2025 consolidation; `load_players` and `is_drafted`
  are retained helpers.
- **Runtime SDK vs harness.** Local simulation/testing (`simulate_draft`, `visualize_draft_board`,
  `score_game`) lives in the top-level **`harness/`** package. The dependency is one-way
  (`harness` → `blitz_env`), and `harness/` is **not** in the wheel. `harness/simulate_draft.py`
  pulls in heavy deps (matplotlib, nfl_data_py, pandas) so it must stay out of the runtime SDK.
- **Don't edit user bot files** (`bots/nfl2025/*.py`, `bots/archive/**`) to satisfy a refactor.

## 4. Runtime model (how a bot reaches a container)

1. The Go engine writes the selected bot's source to host `/tmp/bot.py`
   (`pkg/engine/ContainerHandler.go`, const `botFileRelativePath = /tmp/bot.py`).
2. It bind-mounts host `/tmp` → container `/botblitz`.
3. `py_grpc_server/bootstrap.sh` runs `cp -r /botblitz/* /app/py_grpc_server` then
   `python3 -u server.py`.
4. `server.py` / `isolate_action.py` do `from bot import draft_player,
   perform_weekly_fantasy_actions` and `import blitz_env`.

So the user's `bot.py` overwrites the default `py_grpc_server/bot.py` at runtime, and every
bot runs against the current wheel.

## 5. Build & codegen

- `make gen` — regenerates proto for **both** Go (`pkg/common/agent*.pb.go`) and Python.
- `make gen-python-only` — Python stubs only; copies them into `py_grpc_server/`.
- `make build-py-module` — copies root `player_ranks_*.csv` into `blitz_env/`, builds the
  wheel into `dist/`.
- `make build-docker` — runs `gen-python-only` → `build-py-module` → `docker build`.
- **Generated, NOT tracked:** `blitz_env/agent_pb2*.py`, `py_grpc_server/agent_pb2*.py`,
  `blitz_env/player_ranks_*.csv`. **Exception:** the Go proto `pkg/common/agent*.pb.go` **is**
  tracked — and `make clean` deletes it, so after `make clean` you must `make gen` or
  `git checkout` to restore.

## 6. Running the engine

Entry: `pkg/cmd/engine_bootstrap.go` (module name is `pkg/runner`, not `pkg/cmd`).

Game modes (`pkg/engine/GameMode.go`): `Draft`, `PerformWeeklyFantasyActions`,
`UpdateWeeklyScores`, `FinishPreviousWeek`. Makefile wrappers: `run-draft`,
`run-weekly-fantasy`, `update-scores`, `run-finish-week`.

Flags: `-game_mode`, `-year` (default 2025), `-enable_google_sheets` (default true),
`-enable_verbose_logging`, `-is_running_on_github`.

- The bot roster is **hardcoded** in `engine_bootstrap.go`: it loads bot **source** from
  `bots/nfl2025/*.py` and **env vars** from `bots/nfl/envs/*.env`.
- `bots/nfl/envs/*.env` is **gitignored** (per-bot secrets). A full local draft needs **every**
  configured bot's env file or it exits — an env-setup limitation, not a code bug.
- Requires a running **Docker daemon**; the engine expects the `py_grpc_server` image to
  already exist (`make build-docker`).

## 7. Local simulation / harness

Evaluation and running of bots go through the **Go engine** (the production path) so eval logic can't drift from what runs weekly; the Python CLI (`bootstrap_data`) is for **data bootstrapping only**.

`harness/` + `SimulateDraft.ipynb` are the local way to test a bot's draft logic without the
full engine. `make launch-simulator` builds the wheel, installs it, and opens the notebook +
a Datasette browser on a dev snapshot. The harness imports `blitz_env` and takes the bot's
`draft_player` as a parameter; other teams use `default_draft_strategy`.

### Evaluating a bot (engine-driven, authoritative)
`make evaluate-bot BOT=bots/nfl2025/<bot>.py YEAR=2025 RUNS=3` builds the
`py_grpc_server` image, then runs the bot through the **real engine** over a full
historical season — draft, weekly waivers + scoring, playoffs — against a baseline
field of containerized `standard-bot` opponents, and prints where it finished. Each
run copies `season.db` to a scratch year (2999, gitignored) so the tracked DB is
never mutated. This is the source of truth for "is my bot good"; the Python `harness/`
(`simulate_draft`, `score_game`, `SimulateDraft.ipynb`) remains only an interactive dev
aid, not an evaluator. Entry: `pkg/cmd/evaluate/main.go`; season loop + standings:
`pkg/engine/SeasonReplayHandler.go` (`ReplaySeason`, `FinalStandings`).

#### Fast evaluation mode (local iteration only)

`make evaluate-bot-fast BOT=bots/nfl2025/<bot>.py YEAR=2025 RUNS=3` enables two
speed optimizations that together cut a single-run evaluation from ~6 min to ~1-2 min:

| Flag | What it does | Trade-off |
|---|---|---|
| `--optimize-by-reusing-containers-wont-match-prod` | One container set shared across all phases and runs instead of rebuilding per phase/run | A stateful bot carries in-memory state across phase boundaries and across runs that are meant to be independent seasons |
| `BOTBLITZ_EVAL_INLINE=1` (set automatically) | The container's gRPC server calls the bot's functions in-process rather than forking a fresh `python3` subprocess per pick/waiver claim | A crashing bot kills the container's gRPC server; no subprocess isolation |

**When to use fast mode:**
- Rapid iteration on bot logic (tight feedback loop, many runs)
- Profiling where time goes (`⏱` timing lines are emitted at phase, week, and step granularity)

**When NOT to use fast mode:**
- Final validation before a season — use `make evaluate-bot` (prod-matching container lifecycle)
- Any bot that intentionally persists state across calls (the in-process path reuses the imported module, so module-level globals survive between picks/waiver rounds)
- Multi-run averages meant to be trustworthy — cross-run state bleed can skew results

### Running a mock draft (fast, visual, no Docker)

`python3 -m harness.cli_draft --bot bots/nfl2026/<bot>.py --year 2025` is the quickest
way to see how a bot drafts: it runs the bot's `draft_player` against 12
`default_draft_strategy` opponents for `--year`'s league settings, then prints the
full draft board + the bot's roster in the terminal. Useful flags:

- `--bot-name NAME` — which league bot slot to assign your bot to (default: derived
  from the filename, e.g. `chris_bot.py` → `Chris`).
- `--simulate-season` — after the draft, also simulates the season on the drafted
  rosters (see below) and prints a week-by-week matchup log + final standings.
- `--no-ui` — skip writing to `mock_draft_picks` (see below); useful for a quick
  terminal-only check.
- `--html out.html` — also export a static HTML grid draft board instead of printing
  the round-by-round terminal tables.

**Season simulation (`--simulate-season`, `harness/simulate_season.py`):** rosters are
frozen exactly as the mock draft left them — no add/drops — and only weeks with real
`weekly_stats` for `--year` are simulated (e.g. 3 weeks for the current in-progress
season, all played weeks for a finished one). The matchup schedule and best-lineup
logic are 1:1 ports of the Go engine's authoritative versions, so results match what
the real engine would produce for the same roster/scores:
`generate_schedule` ← `pkg/gamestate/handler.go`'s `generateSchedule` (circle-method
round robin, same bye handling for an odd team count); `best_lineup` ←
`pkg/engine/EndOfWeekHandler.go`'s `scoreTeam`/`convertSlotToPositionMap` (players
filled into slots highest-score-first, narrowest-eligibility slot first).

**Visualizing in `ux/` (the Draft Board / Matchup Details / Leaderboard tabs):** by
default (unless `--no-ui`), the draft (and the season sim, if run) is written into a
**gitignored scratch copy** of that year's season.db —
`data/mock_drafts/{year}/season.db` (`harness/mock_draft_store.py`) — never the
tracked `data/game_states/{year}/season.db`. The scratch copy gets `mock_draft_picks`
(what the Draft Board tab reads) plus `bots`/`league_settings`/`game_statuses` always,
and `matchups`/`weekly_lineups` too when `--simulate-season` was passed (so Matchup
Details/Current Week/Last Week/Leaderboard have something to show — those tables
don't exist in the tracked season.db at all, see §9's "League state" note). The
command prints the exact next step, e.g.:
```
Wrote mock_draft_picks to a scratch copy: data/mock_drafts/2025/season.db
To view it: cd ux && npm run start:local -- ../data/mock_drafts/2025/season.db
```
Run that `npm run start:local -- <path>` command (path relative to `ux/`) to open the
dev server pointed at that scratch db — it copies the file to `ux/public/season.db`
and starts `react-scripts start`. Omit the path (just `npm run start:local`) to view
the live tracked `data/game_states/2026/season.db` instead. The page title
(`Botblitz - {path} ({year})`) and every query's year are both computed from the
loaded db itself (`league_settings.year`, falling back to `MAX(year)` over
stats/projections tables), not hardcoded — so this works for any year's db, mock or
real.

## 8. Verification (how to actually prove a change works)

- **Build ≠ proof.** Building the wheel/image does not exercise the runtime path.
- Real proof = run the engine, or exercise the container directly:
  - `docker run --rm py_grpc_server python3 -c "import blitz_env; from blitz_env import
    is_drafted; from blitz_env.models import DatabaseManager"`
  - `docker run --rm py_grpc_server sh -c "cd /app/py_grpc_server && python3 -c 'import bot'"`
- Go tests live only in `pkg/engine` (`go test ./...` there). `pkg/gamestate` has its own
  `go.mod` but is **not** in `go.work`; running `./...` from it errors and it has no tests.

## 9. Data & source of truth

### One per-season DB: `data/game_states/{year}/season.db` ✅
The single SQLite file the engine, harness, and bots all read/write. It holds three
data tiers in one place (no more stats duplication, no `gs-draft → gs-season`
handoff):

- **Reference, frozen:** `season_stats`, `preseason_projections` (all historical years).
- **Reference, rolling:** `weekly_stats`, `weekly_projections`, `weekly_injuries`
  (the in-progress season, appended week over week by `update-scores`).
- **Player pool:** `players` (draftable universe + draft status).
- **League state:** `bots`, `league_settings`, `game_statuses`, `matchups`,
  `transactions`, `weekly_lineups` (created by the engine/harness at run time, NOT
  shipped in the prebuilt DB).

The engine mutates this file in place (git snapshots it at meaningful points). Draft,
season, and playoffs are phases of the same file. The harness never mutates the
tracked DB: it copies `season.db` to a gitignored scratch and resets only the
league-state tables per mock draft. The repo currently ships a complete **2025**
`season.db`; see `docs/bootstrap-2026.md` to ship a new year.

### Scrape cache (build input): `data/stats/{year}/stats.db`
The slow/network artifact that `build-season` reads offline. Created by the
`bootstrap_data scrape` phase — projections via `ffpros` (`fetch_projections.R`),
actual stats via `nflreadr` (`fetch_stats.R`), both R, both invoked by
`blitz_env/collect_stats.py`. **Bots never read this file** — it is a build input
only. Retained in git as the cache for rebuilds.

### The `bootstrap_data` CLI (`blitz_env/bootstrap_data.py`)
Two phases mirroring the user's mental model:
- `scrape --year Y` → full network pull into `data/stats/Y/stats.db` (the only step
  that hits the network). Makefile: `make bootstrap-data-scrape YEAR=Y`.
- `build-season --year Y` → materialize `data/game_states/Y/season.db` (reference
  tables copied from the cache + the `players` pool from `player_ranks_Y.csv`;
  offline, repeatable). Makefile: `make bootstrap-data-build-season YEAR=Y`.

### Weekly updates
`update-scores` scrapes the new week and **appends** its rows directly into
`season.db` (`collect_weekly_{stats,projections,injuries} --db
data/game_states/{year}/season.db`). The old Go copy step (`populateStatsTables` /
`RefreshWeeklyStats`) is gone — one write, every consumer sees it. A mid-season
rebuild = re-`scrape` + `build-season`, then restore league state from git.

### Engine ↔ DB (Go, `pkg/gamestate/handler.go`)
Referenced by function name (grep for them — line numbers drift, names don't):
- Per-season DB path: `getSaveFileName` → `data/game_states/{year}/season.db`.
- Draft: `NewGameStateHandlerForDraft` opens the existing `season.db`, `AutoMigrate`s
  only the league-state tables, and `populateDatabase` seeds bots/settings/game_status
  (it no longer creates `players` or copies stats — those are already in `season.db`).
- Season: `LoadGameStateForWeeklyFantasy` opens the same file; `initSeason` builds
  matchups.
- Weekly scoring reads `weekly_stats` straight from `season.db`
  (`GetPlayerScoresForCurrentWeek`).

### Constants
```go
const saveFolderRelativePath = "data/game_states"          // per-season DBs
const seasonDatabaseFileName = "season" + fileSuffix       // "season.db"
```

### Injury data
Sourced from nflverse (`nflreadr::load_injuries`, via `fetch_injuries.R` — one request per
season) and exact-joined on `gsis_id` to FantasyPros IDs using the dynastyprocess ID
crosswalk (`blitz_env/load_injuries_nflverse.py`). Requires `Rscript` + the R `nflreadr`
package at scrape time (installable from the ffverse r-universe, like `ffpros`). Fields:
`player_name`, `team`, `position`, `injury`, `practice_status`, `game_status`,
`fantasypros_id`, `gsis_id`, `sleeper_id`. ~25% of rows have no `fantasypros_id` (players
outside FantasyPros' ranked pool aren't in the crosswalk) — that's an acceptable trade for
dropping the old NFL.com scraper's fuzzy name matching, which could silently mismatch players
(e.g. it once matched "Jawaan Taylor" to "Taywan Taylor" at an 80% score). The old
`blitz_env/download_injuries.py` scraper was removed in the 2026 season prep.

### Projections and actual stats (ffpros / nflreadr)
`preseason_projections`/`weekly_projections` are sourced from FantasyPros via
`ffpros::fp_projections` (`fetch_projections.R`, `blitz_env/load_projections_ffpros.py`).
`season_stats`/`weekly_stats` (actuals, including DST) are sourced from nflverse via
`nflreadr::load_player_stats`/`load_team_stats` (`fetch_stats.R`,
`blitz_env/load_stats_nflreadr.py`); DST FPTS is an approximated standard scoring
formula since nflreadr has no fantasy-points endpoint for team defenses. The old
`blitz_env/stats_db.py`/`projections_db.py` FantasyPros HTML scrapers (and the
`download_stats.py`/`download_projections.py`/`download-weekly-data.yml` S3 path
that depended on them) were removed in the 2026 season prep.

**Legacy column aliases:** both tables keep every original FantasyPros-scrape
column name (`FPTS`, `PASSING_YDS`, `RUSHING_ATT`, ...) as an alias of the new
source's native column, computed alongside (not instead of) the native lowercase
columns (`fantasy_points_ppr`, `passing_yds`, ...) — so existing bots keep working
unchanged, and new code can use the cleaner native names. A few legacy columns
with no clean equivalent (`ROST`, `Y/A`, `LG`, `20+`, `pos_rank`, `FPTS/G`, DST
`YDS AGN`) were dropped; no bot in `bots/nfl2025` reads them (verified before this
migration). Because SQLite column names are case-insensitive, a native column
that would collide with a legacy alias by case alone (e.g. ffpros' `fpts` vs.
legacy `FPTS`) is renamed to `..._native` to keep the exact-case legacy name free
— see `_free_case_collision` in both loader modules.

### Archived dev snapshots
`data/archive/{year}/` holds old snapshots used only by `make launch-simulator`, not
production. For 2025 this includes the pre-consolidation `gs-draft.db` / `gs-season.db`
(the old per-phase layout) plus `stats.db` / `gamestate.db`.

### Historical / full rebuild
```bash
python3 -m blitz_env.bootstrap_data scrape --year 2025          # -> data/stats/2025/stats.db
python3 -m blitz_env.bootstrap_data build-season --year 2025    # -> data/game_states/2025/season.db
```
Both `make bootstrap-data-scrape` and the bare CLI default to `--years 5`. The ffpros/
nflreadr-backed pipeline (one request per season/week via R, not a per-page HTML scrape)
makes this fast, but 5 years is plenty of history for evaluation purposes — a few
minutes, mostly bound by the weekly stats/projections loop.

### Multi-source projections (ffanalytics): `data/ffanalytics/{year}/projections.db`
FantasyPros' own projections pages — what `ffpros`/`fetch_projections.R` scrape for
`preseason_projections`/`weekly_projections` — cap out at ~10 rows per position
regardless of year or week (confirmed via direct HTTP checks against fantasypros.com;
it's a site-side limitation, not a scraper bug, and it affects every year in the table,
not just the current season). As a broader-coverage supplement, `fetch_ffanalytics_projections.R`
+ `blitz_env/collect_ffanalytics_projections.py` pull from the
[ffanalytics](https://github.com/FantasyFootballAnalytics/ffanalytics) R package, which
aggregates many fantasy sites. Of everywhere ffanalytics can pull from, only three
sources were confirmed (by fetching their raw HTTP responses directly, not just trusting
the R wrapper) to serve real season/week-specific data rather than always redirecting to
the live/current page: **FFToday** (draft+weekly, ~2010+), **FantasySharks** (draft+weekly,
2018+), and **ESPN** (draft 2018+, weekly 2019+ — and its 2023 preseason data is mostly
NA on ESPN's own end, not fixable locally). Every other source in ffanalytics (CBS,
FanDuel/NumberFire, RTSports, Walterfootball, and ffanalytics' own FantasyPros scrape)
was verified to ignore the season/week params entirely and always return live data —
not used here.

Storage is a **separate** sqlite file per year (`data/ffanalytics/{year}/projections.db`,
table `projections`), deliberately apart from `data/stats/{year}/stats.db` and
`data/game_states/{year}/season.db`. Rows are kept **per-source and per-raw-stat**
(`pass_yds`, `rec_tds`, `rush_att`, ...), not pre-averaged into one consensus number —
a `points` column computed under this league's PPR scoring rides alongside for
convenience, but the raw stat lines are what's kept so a different scoring system can
be recomputed later without re-scraping.

Three operations:
- `make bootstrap-data-ffanalytics-refresh YEAR=Y WEEK=W` — current week + the week
  after (plus preseason if that year has none yet). Meant to run every time weekly
  data is fetched; wired into `update-scores.yml` as a non-critical step.
- `make bootstrap-data-ffanalytics-backfill` — preseason + every played regular-season
  week, for the last 5 years. Resumable (`skip_existing`, the default): an interrupted
  run picks up where it stopped instead of redoing what's already fetched; pass
  `--full-refresh` to force everything anyway. For the current calendar year, stops at
  whatever week has actually been played (`_last_playable_week`) — the sources don't
  have real data for weeks that haven't happened yet. Occasional/manual, never on a
  schedule; ends by calling `repair` (below).
- `make bootstrap-data-ffanalytics-repair` — re-runs the season.db write-back (below)
  for any already-fetched `(year, week)` missing it, no re-scraping. Exists because
  `skip_existing` only checks the raw ffanalytics cache, not whether write-back ever
  succeeded — a week fetched before write-back existed (or where it failed) would
  otherwise stay silently missing from season.db forever. Runs automatically at the
  end of every backfill.

**season.db write-back (`external_projections` table):** a bot only ever opens one
database at runtime — the season.db the engine bind-mounts into its container — so it
can't reach `data/ffanalytics/{year}/projections.db` directly. Every fetch also
name-matches its rows against that year's `season.db` `players` table (normalized
`(name, position)`, no shared player id between the two pipelines) and upserts into a
new `external_projections` table (`fantasypros_id, year, week, position, source,
points`) in that same file — skipped for years with no season.db yet. That's the table
a bot (or `ux/`) should actually read from. See `bots/nfl2026/chris_bot.py` for an
example — it reads `external_projections` filtered to `source = 'FantasySharks'`.

### NFL schedule: `schedule` table in season.db
`fetch_schedule.R` + `blitz_env/collect_schedule.py` (`make bootstrap-data-schedule
YEAR=Y`) pull the full season schedule via `nflreadr::load_schedules()` — every week,
played or not — into a `schedule` table (`year, week, team, opponent, is_home`) in
that year's season.db. This is what lets `ux/`'s player profile show a real opponent
for *future* weeks; `weekly_stats.opponent_team` only exists for games already played.
Same team-abbreviation normalization as DST stats (`_TEAM_ABBR_TO_POOL` in
`load_stats_nflreadr.py`, e.g. nflreadr's `LA` → the pool's `LAR`). Wired into
`update-scores.yml` as a non-critical step alongside the ffanalytics refresh.

## 10. CI / GitHub Actions

`.github/workflows/`: `update-scores.yml`, `weekly-fantasy.yml`, `finish-week.yml`,
`core-validations.yml` (`download-weekly-data.yml`, the old pre-consolidation S3 pipeline,
was removed in the 2026 season prep). `update-scores.yml`'s `schedule:` trigger is enabled
(game-day cron windows, `PAST_DATE` set to the 2026-09-09 week-1 kickoff); `weekly-fantasy.yml`
and `finish-week.yml` are still `workflow_dispatch`-only (manual) — `weekly-fantasy.yml` in
particular still defaults to the engine's stale `-year 2025` flag if run without an explicit
override, a known but unfixed gap since it's not scheduled. `update-scores` treats weekly
**stats** as mission-critical (must succeed) and **projections/injuries/schedule** as
best-effort (continue-on-failure).

## 11. Caveats

- `make clean` deletes the tracked Go proto — regenerate (`make gen`) or `git checkout` after.
- Git history is **not** trustworthy for secrets: a defunct workflow's Discord webhook +
  odds-API key were committed and later removed from the tree but **remain in history** —
  rotation is the real fix. `sheets-creds.json` (live Google key) is gitignored and was never
  committed.
- ~120 MB of old binaries/DBs linger in git history (out of scope to purge; would need
  `git filter-repo` + force-push).
- **Footgun, fixed but easy to reintroduce:** any pandas `read_csv` column that's numeric
  with some missing values gets read as `float64` (can't hold `NaN` as `int`). If that
  column is an id later compared against `players.id` (a plain string like `"11687"`),
  writing the float straight to sqlite silently produces `"11687.0"` — a value that will
  never equal `"11687"` in a join, with no error anywhere. This actually happened to
  `player_id_crosswalk.py`'s `fantasypros_id`/`sleeper_id` and corrupted `fantasypros_id`
  across most of `weekly_stats`/`season_stats`/`weekly_injuries` for a while (DST was
  unaffected — it's matched a different way, straight from `players.id`). Fixed by
  formatting those columns as clean integer strings right after the crosswalk loads. Any
  new id column sourced this way needs the same treatment.
