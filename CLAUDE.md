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
`bootstrap_data scrape` phase (FantasyPros stats/projections + NFL.com injuries).
**Bots never read this file** — it is a build input only. Retained in git as the
cache for rebuilds.

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
Scraped from NFL.com; fuzzy-matched (rapidfuzz) on `(year, week, player_name, position)` to
FantasyPros IDs. Fields: `player_name`, `team`, `position`, `injury`, `practice_status`,
`game_status`, `fantasypros_id`, `gsis_id`, `sleeper_id`.

### Archived dev snapshots
`data/archive/{year}/` holds old snapshots used only by `make launch-simulator`, not
production. For 2025 this includes the pre-consolidation `gs-draft.db` / `gs-season.db`
(the old per-phase layout) plus `stats.db` / `gamestate.db`.

### Historical / full rebuild
```bash
python3 -m blitz_env.bootstrap_data scrape --year 2025          # -> data/stats/2025/stats.db
python3 -m blitz_env.bootstrap_data build-season --year 2025    # -> data/game_states/2025/season.db
```
The `scrape` phase makes ~180 HTTP requests to NFL.com for injury data; 5–10 min,
possible rate limiting.

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
