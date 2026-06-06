# Single Per-Season DB — Plan 3: Migration, Docs & 2025 Ship Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the single-per-season-DB migration: fix `load_players` to support any year with a rank CSV, build & commit a working **2025** `season.db` from the existing scrape cache, archive the obsolete old-layout 2025 game-state files, flag (not edit) the bots that import the removed `StatsDB`/`ProjectionsDB`, document the 2026 bootstrap path, and rewrite CLAUDE.md §9 to describe the consolidated data model.

**Architecture:** Plans 1 & 2 built the Python data layer (`bootstrap_data` CLI, `DatabaseManager` accessors) and rewired the Go engine onto one `data/game_states/{year}/season.db`. This plan produces the actual shipped artifact for the working year (**2025**, because a real 2026 ship is blocked: it's offseason, there is no `player_ranks_2026.csv`, and R/`ffpros` — the only generator — isn't available here). 2025 is fully buildable offline from the tracked `data/stats/2025/stats.db` cache and exercises the entire pipeline. The 2026 ship is reduced to a documented procedure.

**Tech Stack:** Python 3.12 (SQLAlchemy, pandas, sqlite3, pytest), Markdown docs, SQLite. No Go changes. No network.

**Decisions already made (do not revisit):**
- **Ship 2025 now; document 2026.** 2026 cannot be built in this environment.
- **Bots: flag-only.** Per the repo guardrail "Don't edit user bot files," this plan adds a migration doc and edits **no** bot file.
- **Workflow year stays 2026** (set in Plan 2). The live weekly job targets the *next* season; it is schedule-disabled and gated on a future 2026 `season.db` (documented in Task 5). The shipped 2025 `season.db` serves the local/demo path (`make run-draft` defaults to `-year=2025`). These are intentionally different and both internally consistent — **do not "flip" the workflow back to 2025.**

**Out of scope:** Any Go change (Plan 2 is done). Generating `player_ranks_2026.csv` or running any scrape (no R, offseason). Editing bot logic. Untracking the archived `gs-*.db` from git history.

---

## Background the executor needs

- **Repo root:** `/Users/mitch/Documents/botblitz`. All commands run from there unless noted.
- **`load_players`** (`blitz_env/load_players.py`) reads `blitz_env/player_ranks_{year}.csv` (the copy made by `make build-py-module`; the tracked source CSVs live at repo root). It currently hard-rejects any year not in `[2021..2025]`. The CSVs present today are 2021–2025.
- **`build_season(year, stats_path, season_path)`** (`blitz_env/bootstrap_data.py`, from Plan 1) copies the reference tables from a scrape cache and builds the `players` pool via `load_players(year)`. `get_season_db_path(year)` → `data/game_states/{year}/season.db`.
- **Known-good 2025 build** (already verified by building to a temp path): tables `players, season_stats, weekly_stats, weekly_projections, preseason_projections, weekly_injuries`; row counts `players=518, season_stats=8011, weekly_stats=16981, weekly_projections=9976, preseason_projections=3237, weekly_injuries=5784`. Ja'Marr Chase is `fantasypros_id 19788`, rank 1.
- **`season.db` is NOT gitignored** (`.gitignore` only ignores root `/gamestate.db` and `/stats.db`), so it can be `git add`ed normally.
- **Bots importing the *removed* classes (precise list):** `bots/archive/2024/ryan-bot.py`, `bots/archive/2024/mitch-bot.py`, `bots/nfl2025/justin_bot.py`, `bots/nfl2025/ryan_bot.py`. Other `nfl2025` bots use `pd.read_sql(...)` against `DatabaseManager`, which still works — they are NOT broken.
- **Tests run with:** `python3 -m pytest tests -q` (pytest is already a dev dep from Plan 1). The Plan-1 fixture `season_db_2025` in `tests/conftest.py` builds a 2025 `season.db` to a tmp path.

---

## File Structure

**Modify:**
- `blitz_env/load_players.py` — replace the year allowlist with a CSV-existence check + actionable error.
- `CLAUDE.md` — rewrite §9 (the data-model section) for the single per-season DB.

**Create:**
- `tests/test_load_players.py` — covers the new `load_players` gating.
- `data/game_states/2025/season.db` — the committed working DB (a build artifact, committed as data).
- `tests/test_shipped_season_db.py` — guards the shipped 2025 `season.db` shape.
- `docs/bot-migration-2026.md` — the flag-only migration guide for the 4 affected bots.
- `docs/bootstrap-2026.md` — the documented procedure to ship a real 2026 `season.db`.

**Move (archive):**
- `data/game_states/2025/gs-draft.db` → `data/archive/2025/gs-draft.db`
- `data/game_states/2025/gs-season.db` → `data/archive/2025/gs-season.db`

---

## Task 1: Fix `load_players` (support any year with a rank CSV)

**Files:**
- Modify: `blitz_env/load_players.py`
- Create: `tests/test_load_players.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_load_players.py`:

```python
import pytest
from blitz_env.load_players import load_players


def test_load_players_2025_works():
    players = load_players(2025)
    assert len(players) > 0
    # Ja'Marr Chase is fantasypros_id 19788, rank 1 in the 2025 ranks.
    chase = next((p for p in players if p.id == "19788"), None)
    assert chase is not None
    assert chase.full_name == "Ja'Marr Chase"


def test_load_players_missing_year_raises_actionable_error():
    # A year with no player_ranks_<year>.csv must raise a clear, actionable error
    # (FileNotFoundError), NOT the old generic "year not supported".
    with pytest.raises(FileNotFoundError) as exc:
        load_players(2099)
    msg = str(exc.value)
    assert "player_ranks_2099.csv" in msg
    assert "fetch_ranks.R" in msg
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_load_players.py -q`
Expected: `test_load_players_missing_year_raises_actionable_error` FAILS — the current code raises `Exception("year not supported")` (not `FileNotFoundError`, and without the CSV path / `fetch_ranks.R` hint). `test_load_players_2025_works` should already pass.

- [ ] **Step 3: Implement the fix**

In `blitz_env/load_players.py`, replace the `load_players` function:

```python
def load_players(year: int):
    if year not in [2021, 2022, 2023, 2024, 2025]:
        raise Exception("year not supported")
    current_dir = os.path.dirname(__file__)
    file_path = os.path.join(current_dir, f'player_ranks_{str(year)}.csv')
    players = load_all_players(file_path)
    return players
```

with:

```python
def load_players(year: int):
    current_dir = os.path.dirname(__file__)
    file_path = os.path.join(current_dir, f'player_ranks_{str(year)}.csv')
    if not os.path.isfile(file_path):
        raise FileNotFoundError(
            f"No player ranks for {year}: expected '{file_path}'. "
            f"Generate it with `Rscript fetch_ranks.R {year}` (see docs/bootstrap-2026.md), "
            f"then `make build-py-module` to copy it into blitz_env/."
        )
    players = load_all_players(file_path)
    return players
```

(The `import os` at the top of the file already exists; no new imports.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_load_players.py -q`
Expected: `2 passed`.

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `python3 -m pytest tests -q`
Expected: all passed (Plan 1's tests, which call `build_season(2025)` → `load_players(2025)`, still pass).

- [ ] **Step 6: Commit**

```bash
git add blitz_env/load_players.py tests/test_load_players.py
git commit -m "fix: load_players accepts any year with a rank CSV (clear error otherwise)"
```

---

## Task 2: Build & commit the working 2025 `season.db`

**Files:**
- Create: `data/game_states/2025/season.db` (committed artifact)
- Create: `tests/test_shipped_season_db.py`

- [ ] **Step 1: Write the failing test (guards the shipped artifact)**

Create `tests/test_shipped_season_db.py`:

```python
import os
import sqlite3
import pytest

SHIPPED_DB = "data/game_states/2025/season.db"


@pytest.mark.skipif(
    not os.path.isfile(SHIPPED_DB),
    reason="shipped 2025 season.db not built yet",
)
def test_shipped_season_db_has_reference_tables_and_pool():
    conn = sqlite3.connect(SHIPPED_DB)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("players", "season_stats", "weekly_stats",
                  "weekly_projections", "preseason_projections", "weekly_injuries"):
            assert t in tables, f"missing reference table {t}"

        # league-state tables are NOT shipped (engine/harness create them at run time)
        for t in ("bots", "matchups", "league_settings", "game_statuses"):
            assert t not in tables, f"league-state table {t} should not be shipped"

        # players pool is populated and marked draftable
        player_count = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        assert player_count >= 500, f"player pool too small: {player_count}"
        chase = conn.execute(
            "SELECT full_name, rank, availability FROM players WHERE id = '19788'"
        ).fetchone()
        assert chase == ("Ja'Marr Chase", 1, "AVAILABLE")

        # rolling reference tables carry the full 2025 season
        assert conn.execute("SELECT COUNT(*) FROM weekly_stats").fetchone()[0] > 1000
    finally:
        conn.close()
```

- [ ] **Step 2: Run it to verify it is skipped (DB not built yet)**

Run: `python3 -m pytest tests/test_shipped_season_db.py -q`
Expected: `1 skipped` (the file doesn't exist yet).

- [ ] **Step 3: Build the 2025 `season.db` at the real repo path**

Run:
```bash
python3 -m blitz_env.bootstrap_data build-season --year 2025
```
Expected output: `Built season DB -> .../data/game_states/2025/season.db`.

(`build-season` defaults `stats_path` to `data/stats/2025/stats.db` and `season_path` to `data/game_states/2025/season.db`, and overwrites any existing file at that path.)

- [ ] **Step 4: Run the test to verify it now passes**

Run: `python3 -m pytest tests/test_shipped_season_db.py -q`
Expected: `1 passed`.

- [ ] **Step 5: Sanity-check the artifact directly**

Run:
```bash
python3 -c "import sqlite3;c=sqlite3.connect('data/game_states/2025/season.db');print(sorted(r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")));print('players', c.execute('SELECT COUNT(*) FROM players').fetchone()[0])"
```
Expected: the six reference/player tables listed, and `players 518`.

- [ ] **Step 6: Commit the DB and its guard test**

```bash
git add data/game_states/2025/season.db tests/test_shipped_season_db.py
git commit -m "feat: ship prebuilt 2025 season.db (working per-season DB)"
```

---

## Task 3: Archive the obsolete old-layout 2025 game-state files

The old per-phase files `gs-draft.db` / `gs-season.db` are the pre-consolidation layout and are no longer read by the engine (which now uses `season.db`). Move them into the existing archive folder. **Keep** `data/stats/2025/stats.db` in place — it is the scrape cache / build input, not obsolete.

**Files:**
- Move: `data/game_states/2025/gs-draft.db` → `data/archive/2025/gs-draft.db`
- Move: `data/game_states/2025/gs-season.db` → `data/archive/2025/gs-season.db`

- [ ] **Step 1: Move the files with git (preserves history)**

Run:
```bash
git mv data/game_states/2025/gs-draft.db data/archive/2025/gs-draft.db
git mv data/game_states/2025/gs-season.db data/archive/2025/gs-season.db
```

- [ ] **Step 2: Verify the active path now holds only `season.db`**

Run: `ls data/game_states/2025/`
Expected: exactly `season.db` (no `gs-*.db`).

Run: `ls data/archive/2025/`
Expected: `gamestate.db  gs-draft.db  gs-season.db  stats.db`.

- [ ] **Step 3: Confirm the stats cache is untouched**

Run: `ls data/stats/2025/`
Expected: `stats.db` still present.

- [ ] **Step 4: Commit**

```bash
git add -A data/game_states/2025 data/archive/2025
git commit -m "chore: archive obsolete old-layout 2025 gs-{draft,season}.db"
```

---

## Task 4: Bot migration flag doc (no bot edits)

**Files:**
- Create: `docs/bot-migration-2026.md`

- [ ] **Step 1: Re-verify the precise list of affected bots**

Run:
```bash
grep -rln "import StatsDB\|import ProjectionsDB\|StatsDB(\|ProjectionsDB(\|from blitz_env import.*StatsDB\|from blitz_env import.*ProjectionsDB" bots/
```
Expected (exactly these four):
```
bots/archive/2024/ryan-bot.py
bots/archive/2024/mitch-bot.py
bots/nfl2025/justin_bot.py
bots/nfl2025/ryan_bot.py
```
If the list differs, use the actual grep output in the doc below.

- [ ] **Step 2: Write the migration guide**

Create `docs/bot-migration-2026.md`:

```markdown
# Bot migration: off the removed `StatsDB` / `ProjectionsDB`

The 2025→2026 data consolidation **removed the `StatsDB` and `ProjectionsDB`
classes** (their methods were folded into `DatabaseManager`). Bots that imported
those classes will raise `ImportError` at runtime. Per the repo guardrail we do
**not** edit user bot files automatically — this doc flags the affected bots and
shows the port. The owner migrates each bot when ready.

## Affected bots (import the removed classes)

| Bot | Notes |
|-----|-------|
| `bots/nfl2025/ryan_bot.py` | imports `StatsDB` |
| `bots/nfl2025/justin_bot.py` | imports `StatsDB` |
| `bots/archive/2024/ryan-bot.py` | archived; imports `StatsDB` |
| `bots/archive/2024/mitch-bot.py` | archived; imports `StatsDB` |

Bots that use `pd.read_sql(...)` against `DatabaseManager` (most of `bots/nfl2025/`)
are **not** affected — that path is unchanged.

## How to port

Replace direct `StatsDB` / `ProjectionsDB` usage with the `DatabaseManager`
accessors (all return the single FantasyPros schema keyed by `Player.id`):

| Old | New |
|-----|-----|
| `StatsDB(...).get_seasonal_data(player)` | `db.get_seasonal_data(player, seasons=None)` |
| `StatsDB(...).get_weekly_data(player)` | `db.get_weekly_data(player, seasons=None)` |
| `ProjectionsDB(...).get_preseason_projections(player, season)` | `db.get_preseason_projections(player, season)` |
| `ProjectionsDB(...).get_weekly_projections(player, season, week)` | `db.get_weekly_projections(player, season, week)` |

Before:

    from blitz_env import StatsDB
    stats = StatsDB(year=2025)
    df = stats.get_weekly_data(player)

After:

    from blitz_env.models import DatabaseManager
    db = DatabaseManager()
    df = db.get_weekly_data(player)

The returned columns use the FantasyPros schema (`FPTS`, `RUSHING_YDS`, …) with
`season`/`week` normalized to numeric. See `docs/bot-data-schema.md` for the full
table/column contract. Bots may still drop to raw SQL via `db.engine` for complex
joins.

## Note on column names

The removed classes historically returned some `nfl_data_py`-style columns (e.g.
`fantasy_points_ppr`). The `DatabaseManager` accessors return the FantasyPros
schema instead. A ported bot that read `fantasy_points_ppr` should read `FPTS`.
```

- [ ] **Step 3: Verify no bot files were touched**

Run: `git status --porcelain bots/`
Expected: empty (no changes under `bots/`).

- [ ] **Step 4: Commit**

```bash
git add docs/bot-migration-2026.md
git commit -m "docs: flag bots importing the removed StatsDB/ProjectionsDB"
```

---

## Task 5: Document the 2026 bootstrap procedure

**Files:**
- Create: `docs/bootstrap-2026.md`

- [ ] **Step 1: Write the procedure**

Create `docs/bootstrap-2026.md`:

```markdown
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
```

- [ ] **Step 2: Verify the Makefile targets referenced exist**

Run: `grep -n "bootstrap-data-scrape:\|bootstrap-data-build-season:\|build-py-module:" Makefile`
Expected: all three targets present (added in Plan 1 / pre-existing). If `build-py-module` is named differently, correct the doc to match the actual target name.

- [ ] **Step 3: Commit**

```bash
git add docs/bootstrap-2026.md
git commit -m "docs: 2026 season bootstrap procedure"
```

---

## Task 6: Rewrite CLAUDE.md §9 (data model)

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Replace the entire §9 section**

In `CLAUDE.md`, replace everything from the line `## 9. Data & source of truth` up to (but **not** including) the line `## 10. CI / GitHub Actions` with:

```markdown
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
Scraped from NFL.com; fuzzy-matched (rapidfuzz) on `(year, week, player_name, position)`
to FantasyPros IDs. Fields: `player_name`, `team`, `position`, `injury`,
`practice_status`, `game_status`, `fantasypros_id`, `gsis_id`, `sleeper_id`.

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
```

- [ ] **Step 2: Verify no stale references remain in §9**

Run:
```bash
grep -n "getStatsDatabaseFilePath\|populateStatsTables\|RefreshWeeklyStats\|gs-{draft|season}\|statsFolderRelativePath\|statsDatabaseFileName" CLAUDE.md
```
Expected: NO matches in §9. (If §11 "Caveats" or other sections mention them, leave those — only §9 is in scope for this task. Re-run scoped to confirm the data-model section is clean.)

- [ ] **Step 3: Verify the section boundaries are intact**

Run: `grep -n "^## 9\|^## 10" CLAUDE.md`
Expected: `## 9. Data & source of truth` and `## 10. CI / GitHub Actions` both present, in order, with the new content between them.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: rewrite CLAUDE.md §9 for the single per-season DB"
```

---

## Plan 3 self-review checklist (run before handoff)

- [ ] `python3 -m pytest tests -q` — all green (includes new `test_load_players.py` + `test_shipped_season_db.py`).
- [ ] `python3 -c "from blitz_env.load_players import load_players; load_players(2099)"` → raises `FileNotFoundError` naming `player_ranks_2099.csv` and `fetch_ranks.R`.
- [ ] `ls data/game_states/2025/` → only `season.db`. `ls data/archive/2025/` → includes `gs-draft.db`, `gs-season.db`. `ls data/stats/2025/` → `stats.db` still present.
- [ ] `git status --porcelain bots/` → empty (no bot files edited).
- [ ] `grep -rn "getStatsDatabaseFilePath\|populateStatsTables\|RefreshWeeklyStats" CLAUDE.md` → only in §11 Caveats if at all, not in §9.
- [ ] `git status` clean; no stray root `gamestate.db`/`stats.db`/temp DBs committed.

## Done = the consolidation is complete

After Plan 3, the repo ships a working per-season DB, the engine/harness/bots all read one file, the old stats-duplication and the `gs-draft → gs-season` handoff are gone, the affected bots are flagged, and the path to ship 2026 is documented.
```
