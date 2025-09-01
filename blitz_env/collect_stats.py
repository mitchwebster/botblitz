#!/usr/bin/env python3
"""
Rebuild stats.db from scratch: preseason_projections and season_stats
for the last N years, replacing the DB file every run.

Requirements (import paths may need tweaking for your project):
- load_nfl_projections_all_positions(year) -> pd.DataFrame
- fp_seasonal_years(position: str, years: list[int]) -> pd.DataFrame

Usage:
  python collect_stats.py --db stats.db --years 10 --end-year 2025
"""

import argparse
from pathlib import Path
from typing import List
import pandas as pd
from sqlalchemy import create_engine

# --- Adjust these imports to your project structure if needed ---
from blitz_env.projections_db import load_nfl_projections_all_positions
from blitz_env.stats_db import fp_seasonal_years


def ensure_year_column(df: pd.DataFrame, year: int) -> pd.DataFrame:
    if "year" not in df.columns:
        df = df.copy()
        df["year"] = year
    return df


def union_align(dfs: List[pd.DataFrame]) -> pd.DataFrame:
    """
    Align a list of DataFrames to the union of their columns.
    Missing columns are filled with None. Returns a single concatenated DF.
    """
    if not dfs:
        return pd.DataFrame()
    # Compute union of columns, preserving a stable order (first df's cols first)
    cols_order = list(dfs[0].columns)
    union_cols = set(cols_order)
    for d in dfs[1:]:
        for c in d.columns:
            if c not in union_cols:
                cols_order.append(c)
                union_cols.add(c)

    fixed = []
    for d in dfs:
        missing = [c for c in cols_order if c not in d.columns]
        if missing:
            d = d.copy()
            for c in missing:
                d[c] = None
        fixed.append(d[cols_order])
    return pd.concat(fixed, ignore_index=True)


def collect_preseason(years: List[int]) -> pd.DataFrame:
    """
    Build one DataFrame for preseason projections across all requested years.
    Ensures a 'year' column and aligns schema across years.
    """
    parts: List[pd.DataFrame] = []
    for y in years:
        df = load_nfl_projections_all_positions(y)
        df = ensure_year_column(df, y)
        parts.append(df)
    return union_align(parts)


def collect_season_stats(years: List[int]) -> pd.DataFrame:
    """
    Build one DataFrame for season stats across positions and requested years.
    Uses fp_seasonal_years for each position, then aligns schemas across positions.
    """
    pos_parts: List[pd.DataFrame] = []
    for pos in ("rb", "qb", "wr", "te", "dst", "k"):
        d = fp_seasonal_years(pos, years)
        if "year" not in d.columns:
            # safety: enforce presence
            raise ValueError(f"'year' column missing from season stats for position '{pos}'")
        pos_parts.append(d)
    return union_align(pos_parts)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Rebuild stats.db from scratch for the last N years.")
    ap.add_argument("--db", default="stats.db", help="Path to output SQLite DB (recreated each run).")
    ap.add_argument("--years", type=int, default=10, help="How many years back including end-year (default: 10).")
    ap.add_argument("--end-year", type=int, required=True, help="Most recent year to include (e.g., 2025).")
    return ap.parse_args()


def main():
    args = parse_args()
    db_path = Path(args.db).resolve()
    end_year = args.end_year
    start_year = end_year - (args.years - 1)
    years = list(range(start_year, end_year + 1))

    print(f"Rebuilding DB: {db_path}")
    print(f"Years: {years}")

    # 1) Remove existing DB file so we *guarantee* a fresh build
    if db_path.exists():
        db_path.unlink()

    # 2) Create engine (fresh file)
    engine = create_engine(f"sqlite:///{db_path}")

    # 3) Collect data
    pre_df = collect_preseason(years)
    season_df = collect_season_stats(years)

    # 4) Write tables (replace ensures schema exactly matches the DataFrames)
    pre_df.to_sql("preseason_projections", con=engine, if_exists="replace", index=False)
    season_df.to_sql("season_stats", con=engine, if_exists="replace", index=False)

    print(f"[preseason_projections] rows={len(pre_df)}")
    print(f"[season_stats] rows={len(season_df)}")
    print("Done.")


if __name__ == "__main__":
    main()
