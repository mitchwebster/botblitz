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

from blitz_env.download_injuries import NFLInjuryScraper


def get_injuries_for_week(year: int, week: int) -> pd.DataFrame:
    """Fetch injury data for a specific week and match with player IDs"""
    scraper = NFLInjuryScraper(year=year, week=week)

    # Scrape the data
    injury_data = scraper.scrape()

    # Convert to DataFrame
    df = scraper.to_dataframe(injury_data)

    # Match with player IDs
    df_with_ids = scraper.match_player_ids(df)

    return df_with_ids


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

    # Clean up week field - convert "Week 6" to just "6"
    if 'week' in df.columns:
        df['week'] = df['week'].str.replace('Week ', '', regex=False).astype(int)

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")
    table_name = "weekly_injuries"

    # Check if table exists
    insp = inspect(engine)
    if insp.has_table(table_name):
        # Table exists → upsert
        with engine.begin() as conn:
            conn.execute(text(f"""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_{table_name}_unique
                ON {table_name}(year, week, player_name, position)
            """))

        records = df.to_dict(orient="records")

        metadata = MetaData()
        injuries_table = Table(table_name, metadata, autoload_with=engine)
        stmt = insert(injuries_table).values(records)

        # Upsert: update if exists, insert if not
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=["year", "week", "player_name", "position"],
            set_={c.key: c for c in stmt.excluded if c.key not in ["year", "week", "player_name", "position"]}
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
