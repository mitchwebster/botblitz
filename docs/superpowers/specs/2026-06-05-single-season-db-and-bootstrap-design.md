# Design: Single per-season DB, bootstrap CLI, and a unified `blitz_env` data layer

**Date:** 2026-06-05
**Status:** Approved for planning

## Background

BotBlitz is a bot-vs-bot fantasy football engine: human-written Python bots draft players
and make weekly roster decisions; a Go engine orchestrates them, each sandboxed in its own
Docker container. Bots are written against the `blitz_env` SDK and run inside that container.

Today the data is spread across **many SQLite files with overlapping contents**:

| File | Role | Notable tables |
|------|------|----------------|
| `data/stats/{year}/stats.db` | scraped source of truth | `season_stats`, `weekly_stats`, `weekly_projections`, `weekly_injuries`, `preseason_projections` |
| `data/game_states/{year}/gs-draft.db` | league state (draft) | `bots`, `players`, `league_settings`, `game_statuses` + **copies** of `season_stats`, `preseason_projections` |
| `data/game_states/{year}/gs-season.db` | league state (season) | + `matchups`, `transactions`, `weekly_lineups` + **copies** of all stats tables again |
| `data/archive/{year}/{gamestate,stats}.db` | dev snapshots | — |
| `./gamestate.db` (root) | ephemeral harness scratch | — |

The stats tables physically exist in 4+ places. The engine copies them into each game DB at
creation (`populateStatsTables`, `pkg/gamestate/handler.go`) and **re-copies weekly stats
every week** (`RefreshWeeklyStats`). The draft→season transition is a separate file
(`gs-draft.db` → `gs-season.db`) whose handoff isn't even automated in code.

Two further problems:

1. **Storage leaks into bots.** The shipped template `bots/nfl2025/standard-bot.py` reaches
   through `blitz_env` straight into storage details — `pd.read_sql("SELECT * FROM
   game_statuses", db.engine)`, knowing table names and holding `db.engine`. If tables move,
   bots break.
2. **Two parallel data classes.** `StatsDB` and `ProjectionsDB` are separate from
   `DatabaseManager`, so a bot juggles three objects (and, historically, two schemas) to read
   its data.

### Data tiers (the key constraint)

Season data is **three** tiers, not "reference vs state":

1. **Historical** (prior seasons) — frozen. Set once, never changes.
2. **Current-season, rolling** — `weekly_stats` / `weekly_projections` / `weekly_injuries`
   for the in-progress year, appended **week over week** by the `update-scores` job.
3. **League state** — bots, draft picks, matchups, transactions, lineups. Per-game mutable.

The rolling tier is why "ship one static season DB and never touch it" is insufficient, and
why copying stats into each game DB is actively harmful: every weekly update means re-copying
into N files.

## Goals

1. **One canonical DB per season** — `data/game_states/{year}/season.db` — holding all three
   tiers. Eliminate the stats duplication and the `gs-draft → gs-season` handoff.
2. **`blitz_env` is the only surface a bot touches.** Bots get one `DatabaseManager` bound to
   `season.db` and write SQL against **documented tables**. No file paths, no multiple DBs,
   no `ATTACH`. SQL stays available for complex joins.
3. **A `bootstrap_data` CLI** that does all scraping + materialization for a season, so a user
   runs it once (or never — 2026 ships prebuilt).
4. **Fold `StatsDB` / `ProjectionsDB` into `DatabaseManager`** so the bot has one data handle.

## Non-goals

- Migrating 2025 into the new layout. Existing 2025 data is **archived as-is**, not converted.
- A high-level/programmatic game API (we deliberately chose the SQL-over-documented-tables
  model — option B — for query power).
- De-duplicating the engine's hardcoded bot list vs the harness's hardcoded bot list. That
  duplication predates this work and is out of scope.
- Trimming other heavy runtime deps beyond what naturally falls out (e.g. `nfl_data_py`).

## Architecture

### 1. Storage: one DB per season

`data/game_states/{year}/season.db` is the only DB bots and the engine read/write. Tables by
group:

```
reference, frozen:    season_stats, preseason_projections
reference, rolling:   weekly_stats, weekly_projections, weekly_injuries
player pool:          players              (draftable universe + draft status)
league state:         bots, league_settings, game_statuses,
                      matchups, transactions, weekly_lineups
```

- The real engine **mutates this file in place** (git snapshots it at meaningful points, as
  today). Draft, season, and playoffs are **phases of the same file**.
- The **harness never mutates it**: it copies `season.db` to a gitignored scratch and resets
  only the league-state tables per mock draft, so repeated drafts are clean and stats are
  never re-copied.
- `data/stats/{year}/stats.db` is **retained as the scrape cache** — the slow/network
  artifact that `build-season` and rebuilds read from offline. It is a build input; **bots
  never read it.**

### 2. The `bootstrap_data` CLI

A single CLI (evolved from `collect_stats.py`), living outside the runtime import path (it
pulls the scraper deps), with two phases mirroring the user's mental model:

- `scrape --year Y [--years N]` → full pull of FantasyPros stats/projections + NFL.com
  injuries + the draftable `players` pool into **`data/stats/Y/stats.db`**. Idempotent;
  pulls all completed weeks of year Y. This is the only step that hits the network.
- `build-season --year Y` → create a fresh **`data/game_states/Y/season.db`**, copying the
  reference tables + `players` pool from the scrape cache. Fast, offline, repeatable.
  **League-state tables are NOT created here** (see §4).

Bootstrapping 2026: run `scrape --year 2026` then `build-season --year 2026`. Commit the
resulting `season.db` so players clone and immediately have data without scraping.

### 3. Weekly updates

`update-scores` (the existing weekly job) scrapes the new week and **appends** its rows
directly into `data/game_states/Y/season.db`. This **replaces** the Go
`populateStatsTables` / `RefreshWeeklyStats` copy step — one write, every consumer sees it,
no re-copy. (A rebuild mid-season is done by re-running `scrape` + `build-season`, then
restoring league state from git; we do not incrementally maintain the scrape cache.)

### 4. Engine changes (Go, `pkg/gamestate/handler.go` + callers)

- **Delete** `populateStatsTables` and `RefreshWeeklyStats` (no more `ATTACH` / copy).
- `getSaveFileName` → one `data/game_states/{year}/season.db` (drop the `draft`/`season`
  descriptor split). `NewGameStateHandlerForDraft` and `LoadGameStateForWeeklyFantasy` both
  open `season.db`.
- **The engine owns league-state tables.** During draft it `AutoMigrate`s and runs the
  existing `populateDatabase` (bots / `league_settings` / `game_statuses`) **into the
  already-seeded `season.db`**; season mode runs `initSeason` (matchups) on the same file.
  The draft→season file handoff disappears.
- The stats/player tables already exist in `season.db` (from `build-season`), so the engine
  no longer creates or copies them.
- Container mount unchanged in shape: the engine mounts `season.db` (read-only) to the
  in-container `gamestate.db` location, so `DatabaseManager`'s default URL still resolves.

### 5. `blitz_env` bot interface (the contract)

- `DatabaseManager` binds to `season.db` and is the single data handle. It **absorbs** the
  former `StatsDB` / `ProjectionsDB` methods:
  - `get_seasonal_data(player, seasons=None)` — from `season_stats`
  - `get_weekly_data(player, seasons=None)` — from `weekly_stats`
  - `get_preseason_projections(player, season)` — from `preseason_projections`
  - `get_weekly_projections(player, season, week)` — from `weekly_projections`
  - (plus existing `get_all_players`, `get_league_settings`, `get_game_status`, …)
  - All return the single FantasyPros schema keyed by `fantasypros_id` (== `Player.id`).
- **`StatsDB` and `ProjectionsDB` classes are removed**, along with their exports from
  `blitz_env/__init__.py`. The scraper helper functions (`fp_*`, `load_nfl_projections_*`)
  stay for the collectors but are not exported as runtime classes.
- Bots may still drop to raw SQL via the connection for complex joins.
- A **documented schema reference** (tables + columns + meaning) is added as the bot-author
  contract — the deliverable that makes "program against `blitz_env`" concrete.
- `bots/nfl2025/standard-bot.py` is updated to the final table/column names and to use the
  new `DatabaseManager` accessors where natural.

### 6. Data flow (2026, end to end)

```
scrape 2026 ──► data/stats/2026/stats.db (scrape cache)
                     │ build-season
                     ▼
            data/game_states/2026/season.db  (reference + players)
                     │ engine draft: populateDatabase + draft picks
                     │ engine season: initSeason (matchups) + weeks
                     │ update-scores: append weekly_* each week
                     ▼
            bots ── DatabaseManager(season.db) ── SQL over documented tables
```

## Migration & compatibility

- **2025 → archive.** Move `data/stats/2025/` and `data/game_states/2025/` into
  `data/archive/2025/` (or confirm they already live there) and leave them untouched. No
  conversion. New layout begins at 2026.
- **Removed SDK names break some bots.** `bots/nfl2025/ryan_bot.py`,
  `bots/archive/2024/*` (`mitch-bot.py`, `ryan-bot.py`) `import StatsDB` and read
  nfl_data_py-style columns. These will fail to import after removal. Per the guardrail we do
  **not** silently edit user bots; we (a) migrate the shipped `standard-bot.py` template, and
  (b) flag the affected bots and offer migration to the new `DatabaseManager` accessors +
  FantasyPros schema. The owner decides per bot.
- **`nfl_data_py`** is no longer used at runtime; it can be dropped from the runtime wheel
  deps and kept only in the bootstrap/collector environment.
- **CLAUDE.md §9** (the entire data-model section) is rewritten to describe the single
  per-season DB, the scrape cache, and the bootstrap CLI.

## Testing & verification

- **Bootstrap:** `scrape` + `build-season` produce a `season.db` with every documented table
  populated; assert row counts and the `players` pool size.
- **Harness:** mock draft + offline best-possible-score against a scratch copy, network
  hard-blocked (as in the prior change), proving no hidden fetches.
- **`blitz_env` leanness:** `import blitz_env` still pulls no `requests` / `bs4` /
  `nfl_data_py`.
- **Engine:** `go build ./...` and `go test ./pkg/engine`; a `run-draft` against 2026 if a
  Docker daemon is available; container import check
  (`docker run --rm py_grpc_server python3 -c "import blitz_env; from blitz_env.models import
  DatabaseManager"`).
- **Removed-class check:** `import blitz_env` no longer exposes `StatsDB` / `ProjectionsDB`;
  the new `DatabaseManager` accessors return the expected rows for a known player.

## Open questions / risks

- **Mid-season rebuilds** rely on re-scraping + restoring league state from git rather than an
  incrementally-maintained cache. Acceptable given a single league and infrequent rebuilds.
- **Committed `season.db` carries the maintainer's league state.** Players cloning the repo
  get the maintainer's drafted league; the harness's league-state reset on a scratch copy
  makes their mock drafts clean regardless, so this is cosmetic.
