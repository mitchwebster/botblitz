# Single Per-Season DB — Plan 1: Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the Python data foundation for the single-per-season DB: a `bootstrap_data` CLI that materializes `data/game_states/{year}/season.db`, a `DatabaseManager` that absorbs the `StatsDB`/`ProjectionsDB` interfaces, removal of those two classes, and a harness that mock-drafts/scores against `season.db` — all offline.

**Architecture:** `bootstrap_data.scrape` writes the scrape cache `data/stats/{year}/stats.db` (existing `collect_stats` behavior); `bootstrap_data.build_season` materializes a fresh `season.db` (reference tables copied from the cache + a `players` pool from the rank CSV, no league-state tables). Bots/harness read `season.db` through one `DatabaseManager` via SQL and typed accessors. The harness copies the prebuilt `season.db` to a gitignored scratch and re-copies to reset between mock drafts.

**Tech Stack:** Python 3.12, SQLAlchemy, pandas, sqlite3, pytest. (Scrapers `requests`/`bs4` are lazy-imported and unchanged.)

**Out of scope (later plans):** Plan 2 = Go engine rewire onto `season.db` (delete `populateStatsTables`/`RefreshWeeklyStats`, one file for draft→season, engine populates league state). Plan 3 = archive 2025, rewrite CLAUDE.md §9, flag/migrate `StatsDB`-using bots, ship prebuilt 2026.

---

## File Structure

**Create:**
- `blitz_env/bootstrap_data.py` — path helpers (`get_stats_cache_path`, `get_season_db_path`), `build_season(year, ...)`, `scrape(year, ...)`, and an argparse CLI with `scrape` / `build-season` subcommands.
- `tests/conftest.py` — pytest fixture that builds a 2025 `season.db` into a tmp path.
- `tests/test_bootstrap_data.py` — `build_season` materialization tests.
- `tests/test_database_manager.py` — `DatabaseManager` accessor tests.
- `tests/test_blitz_env_surface.py` — public-surface tests (lean import; `StatsDB`/`ProjectionsDB` gone).
- `tests/test_harness_offline.py` — offline mock-draft + score test.
- `docs/bot-data-schema.md` — the documented-tables contract for bot authors.

**Modify:**
- `blitz_env/models.py` — add `get_seasonal_data`, `get_weekly_data`, `get_preseason_projections`, `get_weekly_projections` to `DatabaseManager`.
- `blitz_env/stats_db.py` — delete the `StatsDB` class (keep `fp_*` helpers).
- `blitz_env/projections_db.py` — delete the `ProjectionsDB` class (keep `fp_projections`, `load_nfl_projections_all_positions`).
- `blitz_env/__init__.py` — drop `StatsDB` and `ProjectionsDB` imports/exports.
- `blitz_env/update_players.py` — remove the dead `from blitz_env.stats_db import StatsDB` import.
- `harness/simulate_draft.py` — replace `init_preseason_stats` with a copy-prebuilt-`season.db`-to-scratch + populate-league-state flow.
- `harness/score_game.py` — read stats via `DatabaseManager` accessors instead of `StatsDB`.
- `requirements.txt` — add `pytest`.
- `Makefile` — add `bootstrap-data-scrape`, `bootstrap-data-build-season`, `test-py` targets.
- `.gitignore` — already ignores `/gamestate.db` (the harness scratch); no change needed but verify.

---

## Task 0: Test tooling

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py` (empty), `tests/test_smoke.py`
- Modify: `Makefile`

- [ ] **Step 1: Install pytest and pin it**

Run: `python3 -m pip install pytest`
Then add a line to `requirements.txt`:

```
pytest
```

- [ ] **Step 2: Create an empty tests package marker**

Create `tests/__init__.py` with no content (empty file).

- [ ] **Step 3: Write a smoke test**

Create `tests/test_smoke.py`:

```python
def test_blitz_env_imports_clean():
    import blitz_env  # must not raise
    assert hasattr(blitz_env, "load_players")
```

- [ ] **Step 4: Add a Makefile test target**

Add to `Makefile`:

```make
test-py:
	python3 -m pytest tests -q
```

- [ ] **Step 5: Run it**

Run: `python3 -m pytest tests/test_smoke.py -q`
Expected: `1 passed`

- [ ] **Step 6: Commit**

```bash
git add requirements.txt tests/__init__.py tests/test_smoke.py Makefile
git commit -m "test: add pytest tooling and smoke test"
```

---

## Task 1: `build_season` materialization

**Files:**
- Create: `blitz_env/bootstrap_data.py`
- Create: `tests/conftest.py`
- Create: `tests/test_bootstrap_data.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bootstrap_data.py`:

```python
import sqlite3
from blitz_env.bootstrap_data import build_season

STATS_CACHE_2025 = "data/stats/2025/stats.db"

def test_build_season_materializes_reference_and_players(tmp_path):
    season_db = tmp_path / "season.db"
    build_season(2025, stats_path=STATS_CACHE_2025, season_path=str(season_db))

    conn = sqlite3.connect(season_db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

    # reference tables copied from the scrape cache
    for t in ("season_stats", "preseason_projections", "weekly_stats",
              "weekly_projections", "weekly_injuries"):
        assert t in tables, f"missing reference table {t}"

    # players pool created and populated (Ja'Marr Chase, fantasypros_id 19788, rank 1)
    assert "players" in tables
    chase = conn.execute(
        "SELECT full_name, rank, availability FROM players WHERE id = '19788'"
    ).fetchone()
    assert chase == ("Ja'Marr Chase", 1, "AVAILABLE")

    # NO league-state tables yet (engine/harness own those)
    assert "matchups" not in tables
    assert "bots" not in tables
    conn.close()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_bootstrap_data.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'blitz_env.bootstrap_data'`

- [ ] **Step 3: Implement `bootstrap_data.py` (path helpers + `build_season`)**

Create `blitz_env/bootstrap_data.py`:

```python
#!/usr/bin/env python3
"""Bootstrap a season's data into a single per-season SQLite DB.

Two phases:
  scrape       -> data/stats/{year}/stats.db        (network; the scrape cache)
  build-season -> data/game_states/{year}/season.db (offline; the live per-season DB)

`build_season` copies the reference tables (stats/projections/injuries) from the
scrape cache and materializes the draftable `players` pool. It does NOT create
league-state tables (bots/matchups/...); the engine and harness own those.
"""

import argparse
import os
import sqlite3

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from blitz_env.models import Base, Player
from blitz_env.load_players import load_players

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Reference tables copied from the scrape cache into season.db (copied only if present).
_REFERENCE_TABLES = (
    "season_stats",
    "preseason_projections",
    "weekly_stats",
    "weekly_projections",
    "weekly_injuries",
)


def get_stats_cache_path(year: int) -> str:
    """The scraped reference DB (build input; bots never read this)."""
    return os.path.join(_REPO_ROOT, "data", "stats", str(year), "stats.db")


def get_season_db_path(year: int) -> str:
    """The single per-season DB that the engine, harness, and bots use."""
    return os.path.join(_REPO_ROOT, "data", "game_states", str(year), "season.db")


def build_season(year: int, stats_path: str = None, season_path: str = None) -> str:
    """Create a fresh season.db: players pool + reference tables from the scrape cache."""
    stats_path = stats_path or get_stats_cache_path(year)
    season_path = season_path or get_season_db_path(year)

    if not os.path.isfile(stats_path):
        raise FileNotFoundError(
            f"Scrape cache not found at '{stats_path}'. Run "
            f"`python3 -m blitz_env.bootstrap_data scrape --year {year}` first."
        )

    os.makedirs(os.path.dirname(season_path), exist_ok=True)
    if os.path.exists(season_path):
        os.remove(season_path)

    # 1) players pool via the ORM schema, populated from the rank CSV
    engine = create_engine(f"sqlite:///{season_path}")
    Player.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    try:
        for p in load_players(year):
            session.add(Player(
                id=p.id,
                full_name=p.full_name,
                professional_team=p.professional_team,
                player_bye_week=p.player_bye_week,
                rank=p.rank,
                tier=p.tier,
                position_rank=p.position_rank,
                position_tier=p.position_tier,
                gsis_id=p.gsis_id,
                allowed_positions=list(p.allowed_positions),
                availability="AVAILABLE",
            ))
        session.commit()
    finally:
        session.close()
        engine.dispose()

    # 2) reference tables copied from the scrape cache (raw sqlite3 for clean ATTACH)
    conn = sqlite3.connect(season_path)
    try:
        conn.execute("ATTACH DATABASE ? AS cache", (stats_path,))
        for table in _REFERENCE_TABLES:
            present = conn.execute(
                "SELECT 1 FROM cache.sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if present:
                conn.execute(f"CREATE TABLE {table} AS SELECT * FROM cache.{table}")
        conn.commit()
        conn.execute("DETACH DATABASE cache")
    finally:
        conn.close()

    return season_path
```

- [ ] **Step 4: Add a shared fixture**

Create `tests/conftest.py`:

```python
import pytest
from blitz_env.bootstrap_data import build_season

STATS_CACHE_2025 = "data/stats/2025/stats.db"

@pytest.fixture
def season_db_2025(tmp_path):
    """A freshly built 2025 season.db (offline, from the tracked scrape cache)."""
    path = tmp_path / "season.db"
    build_season(2025, stats_path=STATS_CACHE_2025, season_path=str(path))
    return str(path)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_bootstrap_data.py -q`
Expected: `1 passed`

- [ ] **Step 6: Commit**

```bash
git add blitz_env/bootstrap_data.py tests/conftest.py tests/test_bootstrap_data.py
git commit -m "feat: build_season materializes a single per-season DB"
```

---

## Task 2: `scrape` + CLI wiring

**Files:**
- Modify: `blitz_env/bootstrap_data.py`
- Modify: `Makefile`
- Create: `tests/test_bootstrap_cli.py`

- [ ] **Step 1: Write the failing test (CLI dispatches build-season offline)**

Create `tests/test_bootstrap_cli.py`:

```python
import sqlite3
from blitz_env.bootstrap_data import main

def test_cli_build_season(tmp_path):
    season_db = tmp_path / "season.db"
    rc = main([
        "build-season", "--year", "2025",
        "--stats-path", "data/stats/2025/stats.db",
        "--season-path", str(season_db),
    ])
    assert rc == 0
    conn = sqlite3.connect(season_db)
    assert conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] > 0
    conn.close()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_bootstrap_cli.py -q`
Expected: FAIL with `ImportError: cannot import name 'main'`

- [ ] **Step 3: Add `scrape` and the CLI to `bootstrap_data.py`**

Append to `blitz_env/bootstrap_data.py`:

```python
def scrape(year: int, years_back: int = 10, weeks: str = "1:18") -> str:
    """Full network pull into the scrape cache. Delegates to collect_stats."""
    from blitz_env import collect_stats

    out = get_stats_cache_path(year)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    collect_stats.main([
        "--db", out,
        "--end-year", str(year),
        "--years", str(years_back),
        "--include-weekly",
        "--include-injuries",
        "--weeks", weeks,
    ])
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bootstrap_data")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scrape", help="Network pull into data/stats/{year}/stats.db")
    s.add_argument("--year", type=int, required=True)
    s.add_argument("--years", type=int, default=10)
    s.add_argument("--weeks", default="1:18")

    b = sub.add_parser("build-season",
                       help="Materialize data/game_states/{year}/season.db")
    b.add_argument("--year", type=int, required=True)
    b.add_argument("--stats-path", default=None)
    b.add_argument("--season-path", default=None)

    args = parser.parse_args(argv)
    if args.command == "scrape":
        path = scrape(args.year, years_back=args.years, weeks=args.weeks)
        print(f"Scraped -> {path}")
    elif args.command == "build-season":
        path = build_season(args.year, stats_path=args.stats_path,
                            season_path=args.season_path)
        print(f"Built season DB -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Note: `collect_stats.main` currently parses `sys.argv` via `parse_args()`. Update its signature in this step to accept an optional argv: change `def main():` to `def main(argv=None):` and `args = parse_args()` to `args = parse_args(argv)`, and change `def parse_args()` to `def parse_args(argv=None)` with `return ap.parse_args(argv)`. (File: `blitz_env/collect_stats.py`.)

- [ ] **Step 4: Add Makefile targets**

Add to `Makefile`:

```make
bootstrap-data-scrape:
	python3 -m blitz_env.bootstrap_data scrape --year $(YEAR)

bootstrap-data-build-season:
	python3 -m blitz_env.bootstrap_data build-season --year $(YEAR)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_bootstrap_cli.py -q`
Expected: `1 passed`

- [ ] **Step 6: Commit**

```bash
git add blitz_env/bootstrap_data.py blitz_env/collect_stats.py Makefile tests/test_bootstrap_cli.py
git commit -m "feat: bootstrap_data CLI (scrape + build-season)"
```

---

## Task 3: `DatabaseManager` data accessors

**Files:**
- Modify: `blitz_env/models.py`
- Create: `tests/test_database_manager.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_database_manager.py`:

```python
from blitz_env.models import DatabaseManager, Player

def _db(path):
    DatabaseManager.DB_URL = f"sqlite:///{path}"
    return DatabaseManager()

def test_weekly_and_seasonal_accessors(season_db_2025):
    db = _db(season_db_2025)
    try:
        chase = db.get_player_by_id("19788")
        assert chase.full_name == "Ja'Marr Chase"

        weekly = db.get_weekly_data(chase)
        assert not weekly.empty
        assert "FPTS" in weekly.columns
        # normalized numeric helpers exist
        assert str(weekly["season"].dtype) == "Int64"
        assert str(weekly["week"].dtype) == "Int64"

        seasonal = db.get_seasonal_data(chase)
        assert "FPTS" in seasonal.columns
    finally:
        db.close()

def test_seasons_filter(season_db_2025):
    db = _db(season_db_2025)
    try:
        chase = db.get_player_by_id("19788")
        # weekly_stats in the 2025 cache is year 2025 only
        assert not db.get_weekly_data(chase, seasons=[2025]).empty
        assert db.get_weekly_data(chase, seasons=[1999]).empty
    finally:
        db.close()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_database_manager.py -q`
Expected: FAIL with `AttributeError: 'DatabaseManager' object has no attribute 'get_weekly_data'`

- [ ] **Step 3: Add accessors to `DatabaseManager`**

Add these imports at the top of `blitz_env/models.py` (after the existing imports):

```python
import pandas as pd
from sqlalchemy import text
```

Add these methods inside the `DatabaseManager` class (after `is_draft_complete`):

```python
    # --- Stats/projections accessors (absorbed from the former StatsDB/ProjectionsDB) ---
    # All return the single FantasyPros schema (FPTS, RUSHING_YDS, ...) keyed by
    # fantasypros_id (== Player.id), normalized so `season`/`week` are numeric (Int64).

    def _read_for_player(self, table: str, player) -> "pd.DataFrame":
        try:
            df = pd.read_sql(
                text(f"SELECT * FROM {table} WHERE fantasypros_id = :pid"),
                self.engine,
                params={"pid": str(player.id)},
            )
        except Exception:
            return pd.DataFrame()
        if "season" not in df.columns and "year" in df.columns:
            df["season"] = df["year"]
        for col in ("season", "week"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        return df.reset_index(drop=True)

    def get_seasonal_data(self, player, seasons=None) -> "pd.DataFrame":
        df = self._read_for_player("season_stats", player)
        if seasons is not None and "season" in df.columns:
            df = df[df["season"].isin([int(s) for s in seasons])]
        return df.reset_index(drop=True)

    def get_weekly_data(self, player, seasons=None) -> "pd.DataFrame":
        df = self._read_for_player("weekly_stats", player)
        if seasons is not None and "season" in df.columns:
            df = df[df["season"].isin([int(s) for s in seasons])]
        return df.reset_index(drop=True)

    def get_preseason_projections(self, player, season) -> "pd.DataFrame":
        df = self._read_for_player("preseason_projections", player)
        if season is not None and "season" in df.columns:
            df = df[df["season"] == int(season)]
        return df.reset_index(drop=True)

    def get_weekly_projections(self, player, season, week) -> "pd.DataFrame":
        df = self._read_for_player("weekly_projections", player)
        if season is not None and "season" in df.columns:
            df = df[df["season"] == int(season)]
        if week is not None and "week" in df.columns:
            df = df[df["week"] == int(week)]
        return df.reset_index(drop=True)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_database_manager.py -q`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add blitz_env/models.py tests/test_database_manager.py
git commit -m "feat: DatabaseManager absorbs StatsDB/ProjectionsDB accessors"
```

---

## Task 4: Remove `StatsDB` and `ProjectionsDB`

**Files:**
- Modify: `blitz_env/stats_db.py`, `blitz_env/projections_db.py`, `blitz_env/__init__.py`, `blitz_env/update_players.py`
- Create: `tests/test_blitz_env_surface.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_blitz_env_surface.py`:

```python
import sys

def test_classes_removed():
    import blitz_env
    assert not hasattr(blitz_env, "StatsDB")
    assert not hasattr(blitz_env, "ProjectionsDB")

def test_scraper_helpers_still_available():
    # collectors still need these
    from blitz_env.stats_db import fp_seasonal_years, fp_weekly_years, fp_stats_dynamic
    from blitz_env.projections_db import fp_projections, load_nfl_projections_all_positions

def test_import_stays_lean():
    for m in ("nfl_data_py", "requests", "bs4"):
        sys.modules.pop(m, None)
    import importlib, blitz_env
    importlib.reload(blitz_env)
    assert "nfl_data_py" not in sys.modules
    assert "requests" not in sys.modules
    assert "bs4" not in sys.modules
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_blitz_env_surface.py -q`
Expected: FAIL on `test_classes_removed` (`StatsDB` still present)

- [ ] **Step 3: Delete the `StatsDB` class**

In `blitz_env/stats_db.py`, remove the entire `class StatsDB:` block (from `class StatsDB:` through its last method `get_seasonal_data`). Keep the module docstring, the lazy-import note, and the `fp_seasonal_years` / `fp_weekly_years` / `fp_stats_dynamic` functions. Also remove the now-unused top imports `from typing import List`, `from sqlalchemy import create_engine`, and `from blitz_env.agent_pb2 import Player` if nothing else in the file references them (verify with `grep -n "List\|create_engine\|Player" blitz_env/stats_db.py` after the deletion; remove only imports with zero remaining references).

- [ ] **Step 4: Delete the `ProjectionsDB` class**

In `blitz_env/projections_db.py`, remove the `class ProjectionsDB:` block and the `from .agent_pb2 import Player` line that precedes it (verify `Player` is unused elsewhere in the file first). Keep `fp_projections`, `fp_projections_parse*`, and `load_nfl_projections_all_positions`.

- [ ] **Step 5: Drop the exports from `__init__.py`**

In `blitz_env/__init__.py`, delete these two lines:

```python
from .stats_db import StatsDB
```
```python
from .projections_db import ProjectionsDB
```

- [ ] **Step 6: Remove the dead import in `update_players.py`**

In `blitz_env/update_players.py`, delete the line:

```python
from blitz_env.stats_db import StatsDB
```

- [ ] **Step 7: Run the surface test**

Run: `python3 -m pytest tests/test_blitz_env_surface.py -q`
Expected: `3 passed`

- [ ] **Step 8: Run the whole suite (no regressions)**

Run: `python3 -m pytest tests -q`
Expected: all passed

- [ ] **Step 9: Commit**

```bash
git add blitz_env/stats_db.py blitz_env/projections_db.py blitz_env/__init__.py blitz_env/update_players.py tests/test_blitz_env_surface.py
git commit -m "refactor: remove StatsDB/ProjectionsDB classes (folded into DatabaseManager)"
```

---

## Task 5: Harness reads `season.db`

**Files:**
- Modify: `harness/simulate_draft.py`, `harness/score_game.py`
- Create: `tests/test_harness_offline.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_harness_offline.py`:

```python
import socket
import shutil
import pandas as pd
from blitz_env.models import DatabaseManager, Player

class _NoNet(socket.socket):
    def connect(self, *a, **k):
        raise RuntimeError("NETWORK BLOCKED")

def test_offline_draft_and_score(season_db_2025, tmp_path, monkeypatch):
    # Point the harness scratch + DatabaseManager at a tmp gamestate.db
    scratch = tmp_path / "gamestate.db"
    DatabaseManager.DB_URL = f"sqlite:///{scratch}"
    monkeypatch.setattr(socket, "socket", _NoNet)

    import harness.simulate_draft as sd
    monkeypatch.setattr(sd, "get_season_db_path", lambda year: season_db_2025)

    def draft_player():
        db = DatabaseManager()
        try:
            p = (db.session.query(Player)
                 .filter(Player.availability == "AVAILABLE")
                 .order_by(Player.rank).first())
            return p.id if p else ""
        finally:
            db.close()

    sd.simulate_draft(draft_player, 2025)

    db = DatabaseManager()
    drafted = pd.read_sql(
        "SELECT COUNT(*) c FROM players WHERE availability='DRAFTED'", db.engine)
    db.close()
    assert int(drafted.iloc[0]["c"]) > 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_harness_offline.py -q`
Expected: FAIL (current `init_database` calls `init_preseason_stats`, which no longer matches the new flow / references removed pieces)

- [ ] **Step 3: Replace the stats-copy flow with copy-prebuilt-to-scratch + populate league state**

In `harness/simulate_draft.py`:

(a) Add the import near the top (after the existing `from blitz_env.load_players import load_players` line):

```python
import shutil
from blitz_env.bootstrap_data import get_season_db_path
```

(b) Replace the body of `init_database(year)` (the whole function) with:

```python
def init_database(year: int):
    """Reset the harness scratch DB from the prebuilt season.db and seed league state.

    Copies the tracked, read-only season.db to the scratch DB that DatabaseManager
    points at, then (re)creates and populates the league-state tables (bots, league
    settings, game status). Stats/projections come from the copied season.db — no
    network, no per-run stats copying.
    """
    season_db = get_season_db_path(year)
    if not os.path.isfile(season_db):
        raise FileNotFoundError(
            f"season.db not found at '{season_db}'. Run "
            f"`make bootstrap-data-build-season YEAR={year}` first."
        )

    # DatabaseManager.DB_URL looks like 'sqlite:///<path>'; copy the prebuilt DB there.
    scratch_path = DatabaseManager.DB_URL.replace("sqlite:///", "", 1)
    if os.path.dirname(scratch_path):
        os.makedirs(os.path.dirname(scratch_path), exist_ok=True)
    shutil.copyfile(season_db, scratch_path)

    db = DatabaseManager()  # create_all() adds the empty league-state tables
    try:
        # fresh league state
        db.session.query(Bot).delete()
        db.session.query(LeagueSettings).delete()
        db.session.query(GameStatus).delete()
        db.session.commit()

        db.session.add(Bot(id="0", draft_order=1, name="Ryan", owner="Ryan", current_waiver_priority=0))
        db.session.add(Bot(id="1", draft_order=2, name="Harry", owner="Harry", current_waiver_priority=0))
        db.session.add(Bot(id="2", draft_order=3, name="Jon", owner="Jon", current_waiver_priority=0))
        db.session.add(Bot(id="3", draft_order=4, name="Chris", owner="Chris", current_waiver_priority=0))
        db.session.add(Bot(id="4", draft_order=5, name="Tyler", owner="Tyler", current_waiver_priority=0))
        db.session.add(Bot(id="5", draft_order=6, name="Mitch", owner="Mitch", current_waiver_priority=0))
        db.session.add(Bot(id="6", draft_order=7, name="Justin", owner="Justin", current_waiver_priority=0))
        db.session.add(Bot(id="7", draft_order=8, name="Matt", owner="Matt", current_waiver_priority=0))
        db.session.add(Bot(id="8", draft_order=9, name="Parker", owner="Parker", current_waiver_priority=0))
        db.session.add(Bot(id="9", draft_order=10, name="Philip", owner="Philp", current_waiver_priority=0))
        db.session.add(Bot(id="10", draft_order=11, name="Ben", owner="Ben", current_waiver_priority=0))
        db.session.add(Bot(id="11", draft_order=12, name="Chris H", owner="Chris H", current_waiver_priority=0))
        db.session.add(Bot(id="12", draft_order=13, name="Jack", owner="Jack", current_waiver_priority=0))

        player_slots = {"QB": 1, "RB": 2, "WR": 2, "SUPERFLEX": 1, "FLEX": 1, "K": 1, "DST": 1, "BENCH": 3}
        settings = LeagueSettings()
        settings.is_snake_draft = True
        settings.total_rounds = sum(player_slots.values())
        settings.points_per_reception = 1.0
        settings.year = year
        settings.player_slots = player_slots
        settings.num_teams = 13
        db.session.add(settings)

        game_status = GameStatus()
        game_status.current_draft_pick = 1
        game_status.current_bot_id = "0"
        game_status.current_fantasy_week = 1
        db.session.add(game_status)

        # players already come from season.db as AVAILABLE; ensure a clean draft state
        db.session.query(Player).update(
            {Player.availability: "AVAILABLE", Player.current_bot_id: None, Player.pick_chosen: None}
        )
        db.session.commit()
    finally:
        db.close()
```

(c) Delete the now-unused `init_preseason_stats` function, the `get_stats_db_path` helper, the `_STATS_CACHE` global, and the unused `text`/`nfl`/`fp_seasonal_years`/`load_nfl_projections_all_positions` imports it relied on (verify each is unreferenced elsewhere in the file via `grep` before removing; `fp_seasonal_years`/`nfl` only appeared in commented-out code).

- [ ] **Step 4: Point `score_game` at the `DatabaseManager` accessors**

In `harness/score_game.py`:

(a) Delete `from blitz_env.stats_db import StatsDB` (line ~6).

(b) Change `get_points` to take the `DatabaseManager` directly:

```python
def get_points(db, player, year, week):
    df = db.get_weekly_data(player)
    try:
        data_row = df[(df["season"] == year) & (df["week"] == week)]
        if "fantasy_points_ppr" in data_row.columns:
            return data_row["fantasy_points_ppr"].iloc[0]
        else:
            return data_row["FPTS"].iloc[0]
    except IndexError:
        return 0
```

(c) Everywhere `get_best_possible_score`, `get_best_possible_score_season`, `get_weekly_rankings` accept and pass `stats_db`, rename that parameter to `db` and pass the `DatabaseManager` through (the call already has a `db` in `main`/`score_draft_for_visualization`). Concretely: in `score_draft_for_visualization` and `main`, delete the `stats_db = StatsDB(...)` line and pass the existing `db` object wherever `stats_db` was passed. Update the three function signatures from `(stats_db, ...)` to `(db, ...)` and their internal `get_points(stats_db, ...)` calls to `get_points(db, ...)`.

- [ ] **Step 5: Run the harness test**

Run: `python3 -m pytest tests/test_harness_offline.py -q`
Expected: `1 passed`

- [ ] **Step 6: Run the whole suite**

Run: `python3 -m pytest tests -q`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add harness/simulate_draft.py harness/score_game.py tests/test_harness_offline.py
git commit -m "refactor: harness reads season.db; scoring via DatabaseManager accessors"
```

---

## Task 6: Schema doc + standard-bot regression

**Files:**
- Create: `docs/bot-data-schema.md`
- Modify: `bots/nfl2025/standard-bot.py` (only if its draft path references a removed name)
- Create: `tests/test_standard_bot_draft.py`

- [ ] **Step 1: Write the regression test for the template's draft path**

Create `tests/test_standard_bot_draft.py`:

```python
import importlib.util
from blitz_env.models import DatabaseManager

def _load_standard_bot():
    spec = importlib.util.spec_from_file_location(
        "standard_bot", "bots/nfl2025/standard-bot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_standard_bot_draft_player_runs(season_db_2025, tmp_path, monkeypatch):
    scratch = tmp_path / "gamestate.db"
    DatabaseManager.DB_URL = f"sqlite:///{scratch}"
    import harness.simulate_draft as sd
    monkeypatch.setattr(sd, "get_season_db_path", lambda year: season_db_2025)
    sd.init_database(2025)

    bot = _load_standard_bot()
    pid = bot.draft_player()
    assert isinstance(pid, str) and pid != ""
```

- [ ] **Step 2: Run it to verify it fails or passes**

Run: `python3 -m pytest tests/test_standard_bot_draft.py -q`
Expected: PASS if the template's `draft_player` only touches surviving tables (`players`, `league_settings`, `game_statuses`); if it FAILS due to a removed name, fix `bots/nfl2025/standard-bot.py` minimally so `draft_player` uses the `players`/`league_settings`/`game_statuses` tables present in `season.db`, then re-run. Do NOT alter its `perform_weekly_fantasy_actions` (that path depends on engine-owned league-state tables delivered in Plan 2).

- [ ] **Step 3: Write the bot-author schema contract**

Create `docs/bot-data-schema.md`:

```markdown
# Bot data schema

Your bot programs against **`blitz_env` only**. You get one `DatabaseManager`
bound to the season's DB and read these tables with SQL (or the typed accessors).
You never open files or know how many DBs exist.

## Getting a handle

    from blitz_env.models import DatabaseManager
    db = DatabaseManager()
    import pandas as pd
    pd.read_sql("SELECT * FROM players WHERE availability = 'AVAILABLE'", db.engine)

## Typed accessors (FantasyPros schema, keyed by Player.id)

- `db.get_seasonal_data(player, seasons=None)`  -> rows from `season_stats`
- `db.get_weekly_data(player, seasons=None)`    -> rows from `weekly_stats`
- `db.get_preseason_projections(player, season)`-> rows from `preseason_projections`
- `db.get_weekly_projections(player, season, week)` -> rows from `weekly_projections`

## Tables

| Table | Meaning |
|-------|---------|
| `players` | Draftable pool + draft status (`availability`, `current_bot_id`, `pick_chosen`, `rank`, `allowed_positions`). |
| `season_stats` | Per-season actuals, all historical years. `FPTS`, `RUSHING_YDS`, ... keyed by `fantasypros_id`. |
| `weekly_stats` | Per-week actuals for the current season (rolls in week over week). |
| `preseason_projections` | Preseason projections. |
| `weekly_projections` | Per-week projections. |
| `weekly_injuries` | Per-week injury report. |
| `bots`, `league_settings`, `game_statuses` | League state (created by the engine during the draft). |
| `matchups`, `transactions`, `weekly_lineups` | Season league state (created by the engine during the season). |

Note: league-state tables exist once a draft/season has been run by the engine
or harness; the bootstrapped `season.db` ships with only the reference tables and
`players`.
```

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest tests -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add docs/bot-data-schema.md tests/test_standard_bot_draft.py bots/nfl2025/standard-bot.py
git commit -m "docs: bot data schema contract; verify standard-bot draft path"
```

---

## Plan 1 self-review checklist (run before handoff)

- [ ] `python3 -m pytest tests -q` — all green.
- [ ] `python3 -c "import blitz_env; assert not hasattr(blitz_env,'StatsDB') and not hasattr(blitz_env,'ProjectionsDB')"`.
- [ ] `python3 -c "import sys, blitz_env; assert not ({'nfl_data_py','requests','bs4'} & set(sys.modules))"` — lean import preserved.
- [ ] No stray root `gamestate.db`/`stats.db` committed (`git status` clean).

## Follow-on plans (not in this plan)

- **Plan 2 — Engine:** in `pkg/gamestate/handler.go`, delete `populateStatsTables`/`RefreshWeeklyStats`, make `getSaveFileName` resolve `data/game_states/{year}/season.db`, open the same file for draft + season, `AutoMigrate` league-state tables into the bootstrapped DB, and update the container mount/source. Repoint `update-scores` to append weekly rows into `season.db`.
- **Plan 3 — Migration & docs:** move `data/stats/2025` + `data/game_states/2025` to `data/archive/2025`; run `scrape 2026` + `build-season 2026` and commit the prebuilt `season.db`; rewrite CLAUDE.md §9; flag/migrate `ryan_bot.py` and the archive bots off the removed `StatsDB`.
```
