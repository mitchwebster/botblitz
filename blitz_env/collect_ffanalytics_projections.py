#!/usr/bin/env python3
"""
Multi-source projections from ffanalytics (github.com/FantasyFootballAnalytics/ffanalytics),
stored separately from the ffpros-sourced `preseason_projections`/`weekly_projections`
tables in season.db.

Why a separate pipeline: FantasyPros' own projections pages (what ffpros scrapes)
cap out at ~10 rows per position regardless of year or week -- confirmed to be a
site-side limitation, not a scraper bug. Of everywhere ffanalytics can pull from,
only three sources were confirmed (by direct HTTP checks, not just the R
wrapper) to serve real season/week-specific data rather than always redirecting
to the live/current page: FFToday, FantasySharks, and ESPN. See fetch_ffanalytics_projections.R
for the scrape + scoring details, including a known ESPN data gap (2023 preseason
projections are mostly NA on ESPN's own end -- not something we can fix locally,
so that year will simply have thinner ESPN coverage than others).

Storage: one sqlite file per year at data/ffanalytics/{year}/projections.db,
deliberately separate from data/stats/{year}/stats.db (the ffpros/nflreadr scrape
cache). Rows are kept per-source (not pre-averaged) so a consensus can be
computed downstream with whatever weighting makes sense later.

A bot only ever opens one database at runtime -- the season.db the engine
bind-mounts into its container -- so it has no way to reach this file directly.
Every fetch also tries to match its rows to season.db's own `players.id` by
normalized (name, position) and land them in a new `external_projections`
table *in that same season.db* (see write_back_to_season_db below). That's
the table a bot should actually read from; skipped for years with no
season.db yet (pure historical backfill that was never played).

Two distinct operations, matching how this data actually gets used:
  - `refresh`: current week + the week after (so waiver/lineup bots always have a
    look-ahead), meant to run on every periodic fetch. Preseason (week 0) is
    fetched too, but only if not already present for that year.
  - `backfill`: a full year (preseason + all regular-season weeks), meant to be run
    occasionally by hand to seed/repair history -- not on a schedule.

Usage:
  python3 -m blitz_env.collect_ffanalytics_projections refresh --year 2026 --week 3
  python3 -m blitz_env.collect_ffanalytics_projections backfill --years 5
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import tempfile

import pandas as pd
from sqlalchemy import create_engine, inspect, MetaData, Table, text
from sqlalchemy.dialects.sqlite import insert

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FETCH_SCRIPT = os.path.join(_REPO_ROOT, "fetch_ffanalytics_projections.R")

_TABLE_NAME = "projections"
_TOTAL_WEEKS = 18
_BOT_TABLE_NAME = "external_projections"
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _normalize_name(name) -> str:
    if not isinstance(name, str):
        return ""
    name = name.lower().strip()
    name = re.sub(r"[.'’]", "", name)
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    tokens = [t for t in name.split() if t not in _SUFFIXES]
    return " ".join(tokens)


def _season_db_path(year: int) -> str:
    return os.path.join(_REPO_ROOT, "data", "game_states", str(year), "season.db")


def get_db_path(year: int) -> str:
    return os.path.join(_REPO_ROOT, "data", "ffanalytics", str(year), "projections.db")


def fetch_projections(year: int, week: int) -> pd.DataFrame:
    """One (year, week) pull across all three sources. week=0 means preseason."""
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        out_path = tmp.name
    try:
        subprocess.run(
            ["Rscript", _FETCH_SCRIPT, str(year), str(week), out_path],
            check=True, timeout=600,
        )
        if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            return pd.DataFrame()
        df = pd.read_csv(out_path)
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)

    if df.empty:
        return df

    df = df.rename(columns={"id": "ffa_id"})
    # A handful of rows come back with no resolvable ffanalytics player id (and
    # no name) -- unusable, and SQL UNIQUE treats NULL != NULL so they'd never
    # dedupe on re-fetch and would just accumulate duplicates forever.
    df = df.dropna(subset=["ffa_id"])
    return df


def upsert_projections(df: pd.DataFrame, year: int) -> None:
    if df.empty:
        return

    db_path = get_db_path(year)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")

    insp = inspect(engine)
    if insp.has_table(_TABLE_NAME):
        # Different sources/positions/weeks expose slightly different raw stat
        # columns (e.g. `opp`, `xp_miss` don't show up on every pull), so the
        # table created from the first fetch won't have every column a later
        # fetch brings. Add whatever's missing before inserting.
        existing_cols = {c["name"] for c in insp.get_columns(_TABLE_NAME)}
        new_cols = [c for c in df.columns if c not in existing_cols]
        if new_cols:
            with engine.begin() as conn:
                for col in new_cols:
                    sqltype = (
                        "INTEGER" if pd.api.types.is_integer_dtype(df[col]) else
                        "REAL" if pd.api.types.is_float_dtype(df[col]) else
                        "TEXT"
                    )
                    conn.execute(text(f'ALTER TABLE {_TABLE_NAME} ADD COLUMN "{col}" {sqltype}'))
            insp = inspect(engine)

        with engine.begin() as conn:
            conn.execute(text(f"""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_{_TABLE_NAME}_unique
                ON {_TABLE_NAME}(year, week, source, ffa_id, pos)
            """))

        # Many stat columns are position-specific (DST has no pass_yds, etc.),
        # so NaN is common here -- store it as SQL NULL, not a literal NaN.
        records = df.where(pd.notnull(df), None).to_dict(orient="records")
        metadata = MetaData()
        table = Table(_TABLE_NAME, metadata, autoload_with=engine)
        stmt = insert(table).values(records)
        key_cols = ["year", "week", "source", "ffa_id", "pos"]
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=key_cols,
            set_={c.key: c for c in stmt.excluded if c.key not in key_cols},
        )
        with engine.begin() as conn:
            conn.execute(upsert_stmt)
    else:
        df.to_sql(_TABLE_NAME, con=engine, if_exists="replace", index=False)
        with engine.begin() as conn:
            conn.execute(text(f"""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_{_TABLE_NAME}_unique
                ON {_TABLE_NAME}(year, week, source, ffa_id, pos)
            """))


def _year_has_week(year: int, week: int) -> bool:
    db_path = get_db_path(year)
    if not os.path.isfile(db_path):
        return False
    engine = create_engine(f"sqlite:///{db_path}")
    if not inspect(engine).has_table(_TABLE_NAME):
        return False
    with engine.connect() as conn:
        result = conn.execute(
            text(f"SELECT 1 FROM {_TABLE_NAME} WHERE year = :year AND week = :week LIMIT 1"),
            {"year": year, "week": week},
        )
        return result.first() is not None


def write_back_to_season_db(df: pd.DataFrame, year: int) -> int:
    """
    Match ffanalytics rows to season.db's own `players.id` (== fantasypros_id) by
    normalized (name, position), and upsert into a new `external_projections`
    table in that same season.db file.

    Why: bots only ever open one database at runtime -- the season.db the engine
    bind-mounts into their container -- they have no access to
    data/ffanalytics/{year}/projections.db (that path doesn't exist in the
    container). Landing a matched copy in season.db is what actually makes this
    data usable by a bot; it doesn't need to look like preseason_projections/
    weekly_projections (no legacy FantasyPros columns here), just be reachable
    through the one DB connection a bot has.

    Skipped entirely for years with no season.db yet (pure historical backfill
    years that were never actually played) -- nothing would ever read it there.
    """
    season_db = _season_db_path(year)
    if df.empty or not os.path.isfile(season_db):
        return 0

    engine = create_engine(f"sqlite:///{season_db}")
    if not inspect(engine).has_table("players"):
        return 0

    players = pd.read_sql("SELECT id, full_name, allowed_positions FROM players", engine)
    players["name_key"] = players["full_name"].map(_normalize_name)
    players["positions"] = players["allowed_positions"].apply(
        lambda x: json.loads(x) if isinstance(x, str) else (x or [])
    )
    players_exploded = players.explode("positions").rename(columns={"positions": "position"})
    players_exploded["position"] = players_exploded["position"].str.upper()

    # Select only what's needed before merging -- ffanalytics' own add_player_info()
    # already put a "position" column on df (distinct from "pos"), which would
    # otherwise collide with players_exploded's "position" and get silently
    # suffixed (position_x/position_y) instead of raising.
    matched = df[["first_name", "last_name", "pos", "source", "points", "year", "week"]].copy()
    matched["player_full"] = (matched["first_name"].fillna("") + " " + matched["last_name"].fillna("")).str.strip()
    matched["name_key"] = matched["player_full"].map(_normalize_name)
    matched["pos"] = matched["pos"].str.upper()

    matched = matched.merge(
        players_exploded[["id", "name_key", "position"]],
        left_on=["name_key", "pos"], right_on=["name_key", "position"], how="inner",
    )
    if matched.empty:
        return 0

    out = matched[["id", "year", "week", "position", "source", "points"]].rename(
        columns={"id": "fantasypros_id"}
    )
    out = out.dropna(subset=["fantasypros_id", "points"]).drop_duplicates(
        subset=["fantasypros_id", "year", "week", "source"]
    )
    if out.empty:
        return 0

    insp = inspect(engine)
    if insp.has_table(_BOT_TABLE_NAME):
        with engine.begin() as conn:
            conn.execute(text(f"""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_{_BOT_TABLE_NAME}_unique
                ON {_BOT_TABLE_NAME}(fantasypros_id, year, week, source)
            """))
        records = out.to_dict(orient="records")
        metadata = MetaData()
        table = Table(_BOT_TABLE_NAME, metadata, autoload_with=engine)
        stmt = insert(table).values(records)
        key_cols = ["fantasypros_id", "year", "week", "source"]
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=key_cols,
            set_={c.key: c for c in stmt.excluded if c.key not in key_cols},
        )
        with engine.begin() as conn:
            conn.execute(upsert_stmt)
    else:
        out.to_sql(_BOT_TABLE_NAME, con=engine, if_exists="replace", index=False)
        with engine.begin() as conn:
            conn.execute(text(f"""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_{_BOT_TABLE_NAME}_unique
                ON {_BOT_TABLE_NAME}(fantasypros_id, year, week, source)
            """))

    return len(out)


def _load_raw_projections(year: int, week: int) -> pd.DataFrame:
    """Reload already-fetched rows for (year, week) straight from the raw
    ffanalytics store, no re-scraping -- used to repair season.db write-back
    for weeks fetched before write_back_to_season_db existed."""
    db_path = get_db_path(year)
    if not os.path.isfile(db_path):
        return pd.DataFrame()
    engine = create_engine(f"sqlite:///{db_path}")
    if not inspect(engine).has_table(_TABLE_NAME):
        return pd.DataFrame()
    return pd.read_sql(
        text(f"SELECT * FROM {_TABLE_NAME} WHERE year = :year AND week = :week"),
        engine, params={"year": year, "week": week},
    )


def repair_write_back(years: int, end_year: int) -> None:
    """Re-run the season.db write-back for every already-fetched (year, week)
    missing it, without re-scraping anything. Needed once because backfill's
    skip_existing only checks the raw ffanalytics cache, not whether
    write-back ever completed -- a week cached before write_back_to_season_db
    existed (or one where a write-back attempt failed) stays silently missing
    from season.db forever otherwise.
    """
    start_year = end_year - years + 1
    for year in range(start_year, end_year + 1):
        season_db = _season_db_path(year)
        raw_db = get_db_path(year)
        if not os.path.isfile(season_db) or not os.path.isfile(raw_db):
            continue

        raw_engine = create_engine(f"sqlite:///{raw_db}")
        if not inspect(raw_engine).has_table(_TABLE_NAME):
            continue
        with raw_engine.connect() as conn:
            raw_weeks = {w for (w,) in conn.execute(text(f"SELECT DISTINCT week FROM {_TABLE_NAME}"))}

        season_engine = create_engine(f"sqlite:///{season_db}")
        written_weeks = set()
        if inspect(season_engine).has_table(_BOT_TABLE_NAME):
            with season_engine.connect() as conn:
                written_weeks = {w for (w,) in conn.execute(text(f"SELECT DISTINCT week FROM {_BOT_TABLE_NAME}"))}

        for week in sorted(raw_weeks - written_weeks):
            df = _load_raw_projections(year, week)
            n = write_back_to_season_db(df, year)
            print(f"Repaired year={year} week={week}: {n} matched into season.db")


def fetch_and_store(year: int, week: int) -> int:
    print(f"Fetching ffanalytics projections: year={year} week={week}")
    df = fetch_projections(year, week)
    upsert_projections(df, year)
    matched_n = write_back_to_season_db(df, year)
    print(f"  -> {len(df)} rows ({df['source'].nunique() if not df.empty else 0} sources), "
          f"{matched_n} matched into season.db")
    return len(df)


def refresh(year: int, week: int) -> None:
    """Current + next week, plus preseason if this year has none yet."""
    if not _year_has_week(year, 0):
        fetch_and_store(year, 0)
    fetch_and_store(year, week)
    if week < _TOTAL_WEEKS:
        fetch_and_store(year, week + 1)


# 2026 week 1 kickoff, per nflreadr::load_schedules(2026) -- same reference date
# used in .github/workflows/update-scores.yml's PAST_DATE calculation.
_2026_WEEK1_KICKOFF = datetime.datetime(2026, 9, 9, 8, 0, 0)


def _last_playable_week(year: int) -> int:
    """For the current calendar year, weeks that haven't happened yet don't
    have real data on any source's site -- FFToday in particular throws a
    dplyr error scraping an empty/future week's page. Past years are fully
    played, so they always get the full 18."""
    today = datetime.datetime.now()
    if year != today.year:
        return _TOTAL_WEEKS
    if year != 2026:
        return _TOTAL_WEEKS  # only 2026's kickoff date is known here
    weeks_elapsed = (today - _2026_WEEK1_KICKOFF).days // 7 + 1
    return max(0, min(_TOTAL_WEEKS, weeks_elapsed))


def backfill(years: int, end_year: int, skip_existing: bool = True) -> None:
    """Preseason + every played regular-season week, for each of the last `years` seasons.

    skip_existing=True (the default) makes this resumable: a (year, week)
    that already has rows is left alone, so a backfill that got interrupted
    partway through just picks up where it stopped instead of redoing
    everything already fetched. Pass False to force a full re-fetch.
    """
    start_year = end_year - years + 1
    for year in range(start_year, end_year + 1):
        if not (skip_existing and _year_has_week(year, 0)):
            fetch_and_store(year, 0)
        for week in range(1, _last_playable_week(year) + 1):
            if skip_existing and _year_has_week(year, week):
                continue
            fetch_and_store(year, week)

    # A week can be "cached" (skip_existing sees it and moves on) without ever
    # having made it into season.db -- e.g. rows fetched before
    # write_back_to_season_db existed. Sweep for that every run so it can't
    # go silently stale again.
    repair_write_back(years, end_year)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    r = sub.add_parser("refresh", help="Fetch current + next week (and preseason if missing) for one year.")
    r.add_argument("--year", type=int, required=True)
    r.add_argument("--week", type=int, required=True, help="Current week (1-18).")

    b = sub.add_parser("backfill", help="Fetch preseason + all weeks for the last N years. Run by hand, not on a schedule.")
    b.add_argument("--years", type=int, default=5)
    b.add_argument("--end-year", type=int, default=None, help="Most recent year to include (default: current calendar year).")
    b.add_argument("--full-refresh", action="store_true", help="Re-fetch every (year, week) even if already present (default: skip what's already there, so an interrupted backfill resumes).")

    rp = sub.add_parser("repair", help="Re-run season.db write-back for already-fetched (year, week) pairs missing it. No re-scraping. Also runs automatically at the end of every backfill.")
    rp.add_argument("--years", type=int, default=5)
    rp.add_argument("--end-year", type=int, default=None)

    return ap.parse_args()


def main():
    args = parse_args()
    if args.command == "refresh":
        refresh(args.year, args.week)
    elif args.command == "backfill":
        end_year = args.end_year
        if end_year is None:
            end_year = datetime.date.today().year
        backfill(args.years, end_year, skip_existing=not args.full_refresh)
    elif args.command == "repair":
        end_year = args.end_year
        if end_year is None:
            end_year = datetime.date.today().year
        repair_write_back(args.years, end_year)


if __name__ == "__main__":
    main()
