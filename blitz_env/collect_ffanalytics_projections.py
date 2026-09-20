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
cache) and data/game_states/{year}/season.db (what bots actually read). Rows are
kept per-source (not pre-averaged) so a consensus can be computed downstream with
whatever weighting makes sense later.

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
import os
import subprocess
import tempfile

import pandas as pd
from sqlalchemy import create_engine, inspect, MetaData, Table, text
from sqlalchemy.dialects.sqlite import insert

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FETCH_SCRIPT = os.path.join(_REPO_ROOT, "fetch_ffanalytics_projections.R")

_TABLE_NAME = "projections"
_TOTAL_WEEKS = 18


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


def fetch_and_store(year: int, week: int) -> int:
    print(f"Fetching ffanalytics projections: year={year} week={week}")
    df = fetch_projections(year, week)
    upsert_projections(df, year)
    print(f"  -> {len(df)} rows ({df['source'].nunique() if not df.empty else 0} sources)")
    return len(df)


def refresh(year: int, week: int) -> None:
    """Current + next week, plus preseason if this year has none yet."""
    if not _year_has_week(year, 0):
        fetch_and_store(year, 0)
    fetch_and_store(year, week)
    if week < _TOTAL_WEEKS:
        fetch_and_store(year, week + 1)


def backfill(years: int, end_year: int) -> None:
    """Preseason + every regular-season week, for each of the last `years` seasons."""
    start_year = end_year - years + 1
    for year in range(start_year, end_year + 1):
        fetch_and_store(year, 0)
        for week in range(1, _TOTAL_WEEKS + 1):
            fetch_and_store(year, week)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    r = sub.add_parser("refresh", help="Fetch current + next week (and preseason if missing) for one year.")
    r.add_argument("--year", type=int, required=True)
    r.add_argument("--week", type=int, required=True, help="Current week (1-18).")

    b = sub.add_parser("backfill", help="Fetch preseason + all weeks for the last N years. Run by hand, not on a schedule.")
    b.add_argument("--years", type=int, default=5)
    b.add_argument("--end-year", type=int, default=None, help="Most recent year to include (default: current calendar year).")

    return ap.parse_args()


def main():
    args = parse_args()
    if args.command == "refresh":
        refresh(args.year, args.week)
    elif args.command == "backfill":
        end_year = args.end_year
        if end_year is None:
            import datetime
            end_year = datetime.date.today().year
        backfill(args.years, end_year)


if __name__ == "__main__":
    main()
