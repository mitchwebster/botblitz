#!/usr/bin/env python3
"""
NFL schedule (who plays whom, every week of the season, via nflreadr) --
lands in season.db's `schedule` table so the UI can show a real opponent for
future weeks too, not just weeks that already have actuals in weekly_stats.

Usage:
  python3 -m blitz_env.collect_schedule --year 2026
"""

import argparse
import os
import subprocess
import tempfile

import pandas as pd
from sqlalchemy import create_engine, inspect, MetaData, Table, text
from sqlalchemy.dialects.sqlite import insert

from blitz_env.load_stats_nflreadr import _TEAM_ABBR_TO_POOL

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FETCH_SCRIPT = os.path.join(_REPO_ROOT, "fetch_schedule.R")
_TABLE_NAME = "schedule"


def fetch_schedule(year: int) -> pd.DataFrame:
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        out_path = tmp.name
    try:
        subprocess.run(
            ["Rscript", _FETCH_SCRIPT, str(year), out_path],
            check=True, timeout=60,
        )
        if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            return pd.DataFrame()
        df = pd.read_csv(out_path)
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)

    if df.empty:
        return df

    # Same team-abbreviation normalization used for DST stats, so `team` /
    # `opponent` line up with players.professional_team (e.g. nflreadr's "LA"
    # -> our pool's "LAR").
    df["team"] = df["team"].replace(_TEAM_ABBR_TO_POOL)
    df["opponent"] = df["opponent"].replace(_TEAM_ABBR_TO_POOL)
    return df


def write_to_season_db(df: pd.DataFrame, year: int) -> int:
    season_db = os.path.join(_REPO_ROOT, "data", "game_states", str(year), "season.db")
    if df.empty or not os.path.isfile(season_db):
        return 0

    engine = create_engine(f"sqlite:///{season_db}")
    insp = inspect(engine)
    if insp.has_table(_TABLE_NAME):
        with engine.begin() as conn:
            conn.execute(text(f"""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_{_TABLE_NAME}_unique
                ON {_TABLE_NAME}(year, week, team)
            """))
        records = df.to_dict(orient="records")
        metadata = MetaData()
        table = Table(_TABLE_NAME, metadata, autoload_with=engine)
        stmt = insert(table).values(records)
        key_cols = ["year", "week", "team"]
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
                ON {_TABLE_NAME}(year, week, team)
            """))

    return len(df)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", type=int, required=True)
    args = ap.parse_args()

    df = fetch_schedule(args.year)
    n = write_to_season_db(df, args.year)
    print(f"Fetched {len(df)} team-week rows for {args.year}, wrote {n} to season.db")


if __name__ == "__main__":
    main()
