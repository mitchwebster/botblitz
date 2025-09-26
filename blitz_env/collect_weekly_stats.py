#!/usr/bin/env python3
"""
Grab a weeks data and add it to the sqlite database.

Requirements (import paths may need tweaking for your project):
- load_nfl_projections_all_positions(year) -> pd.DataFrame
- fp_seasonal_years(position: str, years: list[int]) -> pd.DataFrame

Usage:
  python collect_weekly_stats.py --db stats.db --year 2025 --week 1
"""

import argparse
from pathlib import Path
from typing import List
import pandas as pd
from sqlalchemy import create_engine, inspect, MetaData, Table, text
from sqlalchemy.dialects.sqlite import insert
import os

# --- Adjust these imports to your project structure if needed ---
from blitz_env.download_stats import get_stats_for_week

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Add weekly data to stats.db for year and week")
    ap.add_argument("--db", default="stats.db", help="Path to output SQLite DB (recreated each run).")
    ap.add_argument("--year", type=int, required=True, help="Year to collect data for (default: 2025).")
    ap.add_argument("--week", type=int, required=True, help="Week number to collect data for (default: 1).")
    return ap.parse_args()

def main():
    args = parse_args()
    db_path = Path(args.db).resolve()
    year = args.year
    week = args.week

    print(f"Adding weekly data to DB: {db_path}")
    print(f"Year: {year}, Week: {week}")

    df = get_stats_for_week(year=year, week=week)

    os.makedirs(os.path.dirname(db_path), exist_ok=True)  # create directory if missing
    engine = create_engine(f"sqlite:///{db_path}")
    table_name = "weekly_stats"

    # Check if table exists
    insp = inspect(engine)
    if insp.has_table(table_name):
        # Table exists → append
        # df.to_sql(table_name, con=engine, if_exists="append", index=False)

        with engine.begin() as conn:
            conn.execute(text(f"""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_{table_name}_unique
                ON {table_name}(year, week, fantasypros_id, position)
            """))

        records = df.to_dict(orient="records")

        metadata = MetaData()
        weekly_table = Table(table_name, metadata, autoload_with=engine)
        stmt = insert(weekly_table).values(records)

        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=["year", "week", "fantasypros_id", "position"],
            set_={c.key: c for c in stmt.excluded if c.key not in ["year", "week", "fantasypros_id", "position"]}
        )

        with engine.begin() as conn:
            conn.execute(upsert_stmt)

    else:
        # Table does not exist → create it
        df.to_sql(table_name, con=engine, if_exists="replace", index=False)

    print(f"[weekly_stats] rows={len(df)}")
    print("Done.")


if __name__ == "__main__":
    main()
