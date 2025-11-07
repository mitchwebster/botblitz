#!/usr/bin/env python3
"""
Grab a week's projection data and add it to the sqlite database.

Usage:
  python collect_weekly_projections.py --db data/stats/2025/stats.db --year 2025 --week 1
"""

import argparse
from pathlib import Path
import pandas as pd
from sqlalchemy import create_engine, inspect, MetaData, Table, text
from sqlalchemy.dialects.sqlite import insert
import os

from blitz_env.projections_db import fp_projections


def get_projections_for_week(year: int, week: int) -> pd.DataFrame:
    """Fetch weekly projections for a specific week"""
    week_str = str(week)

    all_positions = []

    for pos in ("rb", "qb", "wr", "te", "k", "dst"):
        try:
            df = fp_projections(page=pos, sport='nfl', year=year, week=week_str, scoring='PPR')
            if 'year' not in df.columns:
                df['year'] = year
            df['week'] = week
            df['position'] = df['position'].str.upper()
            all_positions.append(df)
        except Exception as e:
            print(f"Warning: Failed to get projections for {pos}: {e}")
            continue

    if not all_positions:
        return pd.DataFrame()

    combined_df = pd.concat(all_positions, ignore_index=True)
    combined_df.sort_values(by="FPTS", ascending=False, inplace=True)

    return combined_df


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Add weekly projection data to stats.db for year and week")
    ap.add_argument("--db", default="stats.db", help="Path to output SQLite DB")
    ap.add_argument("--year", type=int, required=True, help="Year to collect data for")
    ap.add_argument("--week", type=int, required=True, help="Week number to collect data for")
    return ap.parse_args()


def main():
    args = parse_args()
    db_path = Path(args.db).resolve()
    year = args.year
    week = args.week

    print(f"Adding weekly projection data to DB: {db_path}")
    print(f"Year: {year}, Week: {week}")

    df = get_projections_for_week(year=year, week=week)

    if df.empty:
        print("No projection data collected - skipping database update")
        return

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")
    table_name = "weekly_projections"

    # Check if table exists
    insp = inspect(engine)
    if insp.has_table(table_name):
        # Table exists → upsert
        with engine.begin() as conn:
            conn.execute(text(f"""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_{table_name}_unique
                ON {table_name}(year, week, fantasypros_id, position)
            """))

        records = df.to_dict(orient="records")

        metadata = MetaData()
        projections_table = Table(table_name, metadata, autoload_with=engine)
        stmt = insert(projections_table).values(records)

        # Upsert: update if exists, insert if not
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=["year", "week", "fantasypros_id", "position"],
            set_={c.key: c for c in stmt.excluded if c.key not in ["year", "week", "fantasypros_id", "position"]}
        )

        with engine.begin() as conn:
            conn.execute(upsert_stmt)

    else:
        # Table does not exist → create it
        df.to_sql(table_name, con=engine, if_exists="replace", index=False)

    print(f"[weekly_projections] rows={len(df)}")
    print("Done.")


if __name__ == "__main__":
    main()
