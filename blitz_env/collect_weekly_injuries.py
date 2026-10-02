#!/usr/bin/env python3
"""
Grab a week's injury data and add it to the sqlite database.

Usage:
  python collect_weekly_injuries.py --db data/stats/2025/stats.db --year 2025 --week 6
"""

import argparse
from pathlib import Path
import pandas as pd
from sqlalchemy import create_engine, inspect, MetaData, Table, text
from sqlalchemy.dialects.sqlite import insert
import os

from blitz_env.load_injuries_nflverse import fetch_season_injuries


def get_injuries_for_week(year: int, week: int) -> pd.DataFrame:
    """Fetch injury data for a specific week, sourced from nflverse."""
    df = fetch_season_injuries(year)
    if df.empty:
        return df
    return df[df['week'] == week]


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Add weekly injury data to stats.db for year and week")
    ap.add_argument("--db", default="stats.db", help="Path to output SQLite DB")
    ap.add_argument("--year", type=int, required=True, help="Year to collect data for")
    ap.add_argument("--week", type=int, required=True, help="Week number to collect data for")
    return ap.parse_args()


def main():
    args = parse_args()
    db_path = Path(args.db).resolve()
    year = args.year
    week = args.week

    print(f"Adding weekly injury data to DB: {db_path}")
    print(f"Year: {year}, Week: {week}")

    df = get_injuries_for_week(year=year, week=week)

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")
    table_name = "weekly_injuries"

    # Check if table exists
    insp = inspect(engine)
    if insp.has_table(table_name):
        # Table exists → upsert
        # Keyed on team too, not just (year, week, player_name, position):
        # a player traded mid-season can legitimately appear twice in his
        # trade week, once per team (e.g. Christian McCaffrey, 2022 week 7,
        # listed under both CAR and SF).
        with engine.begin() as conn:
            conn.execute(text(f"""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_{table_name}_unique
                ON {table_name}(year, week, team, player_name, position)
            """))

        records = df.to_dict(orient="records")

        metadata = MetaData()
        injuries_table = Table(table_name, metadata, autoload_with=engine)
        stmt = insert(injuries_table).values(records)

        # Upsert: update if exists, insert if not
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=["year", "week", "team", "player_name", "position"],
            set_={c.key: c for c in stmt.excluded if c.key not in ["year", "week", "team", "player_name", "position"]}
        )

        with engine.begin() as conn:
            conn.execute(upsert_stmt)

    else:
        # Table does not exist → create it
        df.to_sql(table_name, con=engine, if_exists="replace", index=False)

    print(f"[weekly_injuries] rows={len(df)}")
    print("Done.")


if __name__ == "__main__":
    main()
