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
- **R (`fetch_ranks.R`)** — legacy ranking scraper; `player_ranks_*.csv` are the artifacts.
- **JS/React (`ux/`)** — a Datasette-backed web viewer (create-react-app).

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

`harness/` + `SimulateDraft.ipynb` are the local way to test a bot's draft logic without the
full engine. `make launch-simulator` builds the wheel, installs it, and opens the notebook +
a Datasette browser on a dev snapshot. The harness imports `blitz_env` and takes the bot's
`draft_player` as a parameter; other teams use `default_draft_strategy`.

## 8. Verification (how to actually prove a change works)

- **Build ≠ proof.** Building the wheel/image does not exercise the runtime path.
- Real proof = run the engine, or exercise the container directly:
  - `docker run --rm py_grpc_server python3 -c "import blitz_env; from blitz_env import
    is_drafted; from blitz_env.models import DatabaseManager"`
  - `docker run --rm py_grpc_server sh -c "cd /app/py_grpc_server && python3 -c 'import bot'"`
- Go tests live only in `pkg/engine` (`go test ./...` there). `pkg/gamestate` has its own
  `go.mod` but is **not** in `go.work`; running `./...` from it errors and it has no tests.

## 9. Data & source of truth

### Source of truth: `data/stats/{year}/stats.db` ✅
Raw stats & projections. Tables: `preseason_projections`, `season_stats`, `weekly_stats`,
`weekly_injuries`. Created by `python3 blitz_env/collect_stats.py --db
data/stats/{year}/stats.db --end-year {year} --years 10`; weekly updates via
`python3 -m blitz_env.collect_weekly_stats --year {year} --week {week} --db
data/stats/{year}/stats.db` and `collect_weekly_injuries`. Updated by the
`update-scores.yml` action. Tracked in git.

### Game state: `data/game_states/{year}/gs-{draft|season}.db`
The actual league state (bots, players, matchups, league_settings, game_statuses) **plus
copies** of the stats tables. When games are created/updated, Go **ATTACHES**
`data/stats/{year}/stats.db` and **COPIES** the stats tables in
(`populateStatsTables` in `pkg/gamestate/handler.go`), so each game snapshots stats at
creation time; `RefreshWeeklyStats` re-copies. Tracked in git.

### Archived dev snapshots
`data/archive/2025/{stats,gamestate}.db` — old dev snapshots, used only by
`make launch-simulator`, not production.

### Key code references
Referenced by function name (grep for them — line numbers drift, names don't). All in
`pkg/gamestate/handler.go`:
- Stats DB path: `getStatsDatabaseFilePath`
- Game state DB path: `getSaveFileName`
- Stats table population (attach + copy): `populateStatsTables`
- Weekly stats refresh: `RefreshWeeklyStats`

### Constants
```go
const saveFolderRelativePath = "data/game_states"  // Game state DBs
const statsFolderRelativePath = "data/stats"       // Stats DBs (source of truth)
const statsDatabaseFileName = "stats.db"
```

### Injury data
Scraped from NFL.com; fuzzy-matched (rapidfuzz) on `(year, week, player_name, position)` to
FantasyPros IDs. Fields: `player_name`, `team`, `position`, `injury`, `practice_status`,
`game_status`, `fantasypros_id`, `gsis_id`, `sleeper_id`.

### Historical backfill
```bash
python3 blitz_env/collect_stats.py --db data/stats/2025/stats.db --end-year 2025 \
  --years 10 --include-weekly --include-injuries --weeks 1:18
```
Warning: ~180 HTTP requests to NFL.com for injury data; 5–10 min, possible rate limiting.

## 10. CI / GitHub Actions

`.github/workflows/`: `update-scores.yml`, `weekly-fantasy.yml`, `finish-week.yml`,
`download-weekly-data.yml`, `core-validations.yml`. Several scheduled triggers are disabled
for the offseason. `update-scores` treats weekly **stats** as mission-critical (must succeed)
and **projections/injuries** as best-effort (continue-on-failure).

## 11. Caveats

- `make clean` deletes the tracked Go proto — regenerate (`make gen`) or `git checkout` after.
- Git history is **not** trustworthy for secrets: a defunct workflow's Discord webhook +
  odds-API key were committed and later removed from the tree but **remain in history** —
  rotation is the real fix. `sheets-creds.json` (live Google key) is gitignored and was never
  committed.
- ~120 MB of old binaries/DBs linger in git history (out of scope to purge; would need
  `git filter-repo` + force-push).
