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


def collect_weekly_projections(years: List[int], weeks: List[str]) -> pd.DataFrame:
    """
    Build one DataFrame for weekly projections across positions, years, and weeks.
    Uses fp_projections for each position, then aligns schemas across positions.
    Fails silently for individual weeks and returns whatever data was successfully collected.
    """
    from blitz_env.projections_db import fp_projections
    
    all_parts: List[pd.DataFrame] = []
    
    for year in years:
        for week in weeks:
            print(f"Collecting weekly projections for year {year}, week {week}...")
            
            try:
                week_parts: List[pd.DataFrame] = []
                for pos in ("rb", "qb", "wr", "te", "k", "dst"):
                    try:
                        df = fp_projections(page=pos, sport='nfl', year=year, week=week, scoring='PPR')
                        df = ensure_year_column(df, year)
                        df['week'] = week
                        df['position'] = df['position'].str.upper()
                        week_parts.append(df)
                    except Exception as e:
                        print(f"Warning: Failed to get projections for {pos} in year {year}, week {week}: {e}")
                        continue
                
                if week_parts:
                    week_df = union_align(week_parts)
                    week_df.sort_values(by="FPTS", ascending=False, inplace=True)
                    all_parts.append(week_df)
                    print(f"Successfully collected projections for year {year}, week {week}")
                else:
                    print(f"No projections collected for year {year}, week {week}")
                    
            except Exception as e:
                print(f"Warning: Failed to collect projections for year {year}, week {week}: {e}")
                continue
    
    return union_align(all_parts) if all_parts else pd.DataFrame()


def collect_weekly_stats(years: List[int], weeks: List[str]) -> pd.DataFrame:
    """
    Build one DataFrame for weekly stats across positions, years, and weeks.
    Uses fp_weekly_years from stats_db to get weekly player stats.
    Fails silently for individual positions and returns whatever data was successfully collected.
    """
    from blitz_env.stats_db import fp_weekly_years
    
    all_parts: List[pd.DataFrame] = []
    
    # Filter to only numeric weeks
    weekly_nums = [int(w) for w in weeks if w.isdigit()]
    
    for pos in ("rb", "qb", "wr", "te", "dst", "k"):
        print(f"Collecting weekly stats for position {pos}...")
        try:
            # Get weekly stats for this position across all years
            pos_df = fp_weekly_years(pos, years)
            
            # Filter to only the requested weeks
            if weekly_nums:
                pos_df = pos_df[pos_df['week'].isin(weekly_nums)]
            
            if not pos_df.empty:
                all_parts.append(pos_df)
                print(f"Successfully collected weekly stats for position {pos}")
            else:
                print(f"No weekly stats collected for position {pos}")
                
        except Exception as e:
            print(f"Warning: Failed to get weekly stats for position {pos}: {e}")
            continue
    
    return union_align(all_parts) if all_parts else pd.DataFrame()


def collect_weekly_injuries(years: List[int], weeks: List[str]) -> pd.DataFrame:
    """
    Build one DataFrame for weekly injuries across years and weeks.
    Uses NFLInjuryScraper to get injury data from NFL.com.
    Fails silently for individual weeks and returns whatever data was successfully collected.
    """
    from blitz_env.download_injuries import NFLInjuryScraper

    all_parts: List[pd.DataFrame] = []

    for year in years:
        for week_str in weeks:
            # Convert week string to int
            try:
                week = int(week_str)
            except ValueError:
                print(f"Warning: Skipping non-numeric week: {week_str}")
                continue

            print(f"Collecting injury data for year {year}, week {week}...")

            try:
                scraper = NFLInjuryScraper(year=year, week=week)

                # Scrape the data
                injury_data = scraper.scrape()

                # Convert to DataFrame
                df = scraper.to_dataframe(injury_data)

                # Match with player IDs
                df_with_ids = scraper.match_player_ids(df)

                # Clean up week field - convert "Week 6" to just 6
                if 'week' in df_with_ids.columns:
                    df_with_ids['week'] = df_with_ids['week'].str.replace('Week ', '', regex=False).astype(int)

                if not df_with_ids.empty:
                    all_parts.append(df_with_ids)
                    print(f"Successfully collected {len(df_with_ids)} injury records for year {year}, week {week}")
                else:
                    print(f"No injury data collected for year {year}, week {week}")

            except Exception as e:
                print(f"Warning: Failed to collect injuries for year {year}, week {week}: {e}")
                continue

    return pd.concat(all_parts, ignore_index=True) if all_parts else pd.DataFrame()


def parse_week_range(week_str: str) -> List[str]:
    """
    Parse week range string like '1:17' or comma-separated list like '1,2,3'.
    Returns list of week strings.
    """
    if ':' in week_str:
        start_week, end_week = map(int, week_str.split(':'))
        return [str(w) for w in range(start_week, end_week + 1)]
    else:
        return [w.strip() for w in week_str.split(',')]


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Rebuild stats.db from scratch for the last N years.")
    ap.add_argument("--db", default="stats.db", help="Path to output SQLite DB (recreated each run).")
    ap.add_argument("--years", type=int, default=10, help="How many years back including end-year (default: 10).")
    ap.add_argument("--end-year", type=int, required=True, help="Most recent year to include (e.g., 2025).")
    ap.add_argument("--include-weekly", action="store_true", help="Include weekly projections and stats in the database.")
    ap.add_argument("--include-injuries", action="store_true", help="Include weekly injury data in the database.")
    ap.add_argument("--weeks", default="1:17", help="Week range for weekly data (e.g., '1:17' or '1,2,3').")
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
    
    # 4) Collect weekly data if requested
    weekly_df = pd.DataFrame()
    weekly_stats_df = pd.DataFrame()
    weekly_injuries_df = pd.DataFrame()

    if args.include_weekly or args.include_injuries:
        weeks = parse_week_range(args.weeks)
        print(f"Weekly data weeks: {weeks}")

    if args.include_weekly:
        weekly_df = collect_weekly_projections(years, weeks)

    if args.include_weekly:
        weekly_stats_df = collect_weekly_stats(years, weeks)

    if args.include_injuries:
        weekly_injuries_df = collect_weekly_injuries(years, weeks)

    # 5) Write tables (replace ensures schema exactly matches the DataFrames)
    pre_df.to_sql("preseason_projections", con=engine, if_exists="replace", index=False)
    season_df.to_sql("season_stats", con=engine, if_exists="replace", index=False)

    if not weekly_df.empty:
        weekly_df.to_sql("weekly_projections", con=engine, if_exists="replace", index=False)

    if not weekly_stats_df.empty:
        weekly_stats_df.to_sql("weekly_stats", con=engine, if_exists="replace", index=False)

    if not weekly_injuries_df.empty:
        weekly_injuries_df.to_sql("weekly_injuries", con=engine, if_exists="replace", index=False)

    print(f"[preseason_projections] rows={len(pre_df)}")
    print(f"[season_stats] rows={len(season_df)}")
    if not weekly_df.empty:
        print(f"[weekly_projections] rows={len(weekly_df)}")
    if not weekly_stats_df.empty:
        print(f"[weekly_stats] rows={len(weekly_stats_df)}")
    if not weekly_injuries_df.empty:
        print(f"[weekly_injuries] rows={len(weekly_injuries_df)}")
    print("Done.")


if __name__ == "__main__":
    main()
