#!/usr/bin/env python3
"""
Build/refresh stats.db: preseason_projections, season_stats, and (optionally)
weekly_projections/weekly_stats/weekly_injuries.

Incremental by default: years/weeks already present in an existing DB are not
re-fetched (no network call at all for them) -- only missing years/weeks are
pulled and merged in. The current/target year (--end-year) is always
re-fetched at the year level (preseason_projections, season_stats), since an
in-progress season's totals change week to week; weekly tables only ever fetch
(year, week) pairs that aren't already cached, since a played week's box score
is final. Pass --full-refresh to wipe the DB and rebuild everything from
scratch instead.

All network sourcing goes through R (ffpros for projections, nflreadr for
actual stats and injuries) via blitz_env/load_projections_ffpros.py and
blitz_env/load_stats_nflreadr.py -- see those modules for the legacy-column
alias policy.

Usage:
  python collect_stats.py --db stats.db --years 10 --end-year 2025
  python collect_stats.py --db stats.db --years 10 --end-year 2025 --full-refresh
"""

import argparse
from pathlib import Path
from typing import Dict, List, Set, Tuple
import pandas as pd
from sqlalchemy import create_engine, inspect

from blitz_env.load_projections_ffpros import fetch_projections
from blitz_env.load_stats_nflreadr import fetch_season_stats


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


def read_existing_table(engine, table_name: str) -> pd.DataFrame:
    """Read a table if it exists, else an empty DataFrame."""
    if not inspect(engine).has_table(table_name):
        return pd.DataFrame()
    return pd.read_sql(f"SELECT * FROM {table_name}", engine)


def existing_years(df: pd.DataFrame) -> Set[int]:
    if df.empty or "year" not in df.columns:
        return set()
    return set(pd.to_numeric(df["year"], errors="coerce").dropna().astype(int).unique())


def existing_year_weeks(df: pd.DataFrame) -> Set[Tuple[int, int]]:
    if df.empty or "year" not in df.columns or "week" not in df.columns:
        return set()
    sub = df[["year", "week"]].copy()
    sub["year"] = pd.to_numeric(sub["year"], errors="coerce")
    sub["week"] = pd.to_numeric(sub["week"], errors="coerce")
    sub = sub.dropna()
    return set(zip(sub["year"].astype(int), sub["week"].astype(int)))


def collect_preseason(years: List[int]) -> pd.DataFrame:
    """
    Build one DataFrame for preseason projections across the given years
    (already filtered down to only what needs fetching by the caller).
    Sourced from ffpros (R) via fetch_projections; ensures a 'year' column.
    """
    parts: List[pd.DataFrame] = []
    for y in years:
        print(f"Collecting preseason projections for year {y}...")
        try:
            df = fetch_projections(y, ["draft"])
        except Exception as e:
            print(f"Warning: Failed to collect preseason projections for year {y}: {e}")
            continue
        if df.empty:
            print(f"No preseason projections available for year {y}")
            continue
        df = ensure_year_column(df, y)
        parts.append(df)
    return union_align(parts) if parts else pd.DataFrame()


def collect_season_stats(years: List[int]) -> pd.DataFrame:
    """
    Build one DataFrame for season stats (offense + DST) across the given
    years (already filtered down to only what needs fetching by the caller).
    Sourced from nflreadr (R) via fetch_season_stats(summary_level="reg").
    """
    parts: List[pd.DataFrame] = []
    for y in years:
        print(f"Collecting season stats for year {y}...")
        try:
            offense, dst = fetch_season_stats(y, summary_level="reg")
        except Exception as e:
            print(f"Warning: Failed to collect season stats for year {y}: {e}")
            continue
        for df in (offense, dst):
            if df is not None and not df.empty:
                parts.append(ensure_year_column(df, y))
        if offense.empty and dst.empty:
            print(f"No season stats available for year {y}")
    return union_align(parts) if parts else pd.DataFrame()


def collect_weekly_projections(year_weeks: Dict[int, List[int]]) -> pd.DataFrame:
    """
    Build one DataFrame for weekly projections across the given {year: [weeks]}
    map (already filtered down to only missing (year, week) pairs by the caller).
    Sourced from ffpros (R) via fetch_projections. One R subprocess call per
    (year, week) -- not per year -- so a slow/failed week (ffpros has no bulk
    historical endpoint; a full year is ~100+ sequential FantasyPros requests
    and can exceed any reasonable subprocess timeout) only drops that week,
    not the whole year.
    """
    all_parts: List[pd.DataFrame] = []
    for year, weeks in year_weeks.items():
        if not weeks:
            continue
        print(f"Collecting weekly projections for year {year}, weeks {weeks}...")
        year_parts: List[pd.DataFrame] = []
        for week in weeks:
            try:
                df = fetch_projections(year, [week])
            except Exception as e:
                print(f"Warning: Failed to collect weekly projections for year {year}, week {week}: {e}")
                continue
            if not df.empty:
                year_parts.append(df)

        if not year_parts:
            print(f"No weekly projections available for year {year}")
            continue
        year_df = union_align(year_parts)
        year_df = ensure_year_column(year_df, year)
        all_parts.append(year_df)
        print(f"Successfully collected {len(year_df)} weekly projection rows for year {year}")

    return union_align(all_parts) if all_parts else pd.DataFrame()


def collect_weekly_stats(year_weeks: Dict[int, List[int]]) -> pd.DataFrame:
    """
    Build one DataFrame for weekly stats (offense + DST) across the given
    {year: [weeks]} map (already filtered down to only missing (year, week)
    pairs by the caller). Sourced from nflreadr (R) via
    fetch_season_stats(summary_level="week").
    """
    all_parts: List[pd.DataFrame] = []
    for year, weeks in year_weeks.items():
        if not weeks:
            continue
        print(f"Collecting weekly stats for year {year}, weeks {weeks}...")
        try:
            offense, dst = fetch_season_stats(year, summary_level="week")
        except Exception as e:
            print(f"Warning: Failed to collect weekly stats for year {year}: {e}")
            continue

        for df in (offense, dst):
            if df is None or df.empty:
                continue
            df = df[df["week"].isin(weeks)]
            if not df.empty:
                all_parts.append(ensure_year_column(df, year))

        if offense.empty and dst.empty:
            print(f"No weekly stats available for year {year}")
        else:
            print(f"Successfully collected weekly stats for year {year}")

    return union_align(all_parts) if all_parts else pd.DataFrame()


def collect_weekly_injuries(year_weeks: Dict[int, List[int]]) -> pd.DataFrame:
    """
    Build one DataFrame for weekly injuries across the given {year: [weeks]}
    map (already filtered down to only missing (year, week) pairs by the
    caller). Sources from nflverse (one request per season, via
    `fetch_injuries.R`) and joins to FantasyPros IDs on `gsis_id`.
    """
    from blitz_env.load_injuries_nflverse import fetch_season_injuries

    all_parts: List[pd.DataFrame] = []

    for year, weeks in year_weeks.items():
        if not weeks:
            continue
        print(f"Collecting injury data for year {year}...")

        try:
            df = fetch_season_injuries(year)
        except Exception as e:
            print(f"Warning: Failed to collect injuries for year {year}: {e}")
            continue

        if df.empty:
            print(f"No injury data available for year {year}")
            continue

        df = df[df['week'].isin(weeks)]
        if not df.empty:
            all_parts.append(df)
            print(f"Successfully collected {len(df)} injury records for year {year}")
        else:
            print(f"No injury data in requested weeks for year {year}")

    return pd.concat(all_parts, ignore_index=True) if all_parts else pd.DataFrame()


def parse_week_range(week_str: str) -> List[int]:
    """
    Parse week range string like '1:17' or comma-separated list like '1,2,3'.
    Returns a list of ints.
    """
    if ':' in week_str:
        start_week, end_week = map(int, week_str.split(':'))
        return list(range(start_week, end_week + 1))
    else:
        return [int(w.strip()) for w in week_str.split(',')]


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Build/refresh stats.db for the last N years.")
    ap.add_argument("--db", default="stats.db", help="Path to output SQLite DB.")
    ap.add_argument("--years", type=int, default=10, help="How many years back including end-year (default: 10).")
    ap.add_argument("--end-year", type=int, required=True, help="Most recent year to include (e.g., 2025).")
    ap.add_argument("--include-weekly", action="store_true", help="Include weekly projections and stats in the database.")
    ap.add_argument("--include-injuries", action="store_true", help="Include weekly injury data in the database.")
    ap.add_argument("--weeks", default="1:17", help="Week range for weekly data (e.g., '1:17' or '1,2,3').")
    ap.add_argument("--full-refresh", action="store_true",
                     help="Wipe the DB and re-fetch everything, ignoring any existing cached data.")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    db_path = Path(args.db).resolve()
    end_year = args.end_year
    start_year = end_year - (args.years - 1)
    years = list(range(start_year, end_year + 1))
    weeks = parse_week_range(args.weeks) if (args.include_weekly or args.include_injuries) else []

    print(f"Target DB: {db_path}")
    print(f"Years: {years}")
    print(f"Full refresh: {args.full_refresh}")

    if args.full_refresh and db_path.exists():
        db_path.unlink()

    engine = create_engine(f"sqlite:///{db_path}")

    # --- Year-level tables: fetch only missing years, always refresh end_year ---
    existing_pre = pd.DataFrame() if args.full_refresh else read_existing_table(engine, "preseason_projections")
    existing_season = pd.DataFrame() if args.full_refresh else read_existing_table(engine, "season_stats")

    pre_have = existing_years(existing_pre)
    season_have = existing_years(existing_season)

    pre_years_to_fetch = [y for y in years if y == end_year or y not in pre_have]
    season_years_to_fetch = [y for y in years if y == end_year or y not in season_have]

    print(f"Preseason projections: fetching {pre_years_to_fetch}, keeping cached {sorted(pre_have - set(pre_years_to_fetch))}")
    print(f"Season stats: fetching {season_years_to_fetch}, keeping cached {sorted(season_have - set(season_years_to_fetch))}")

    new_pre = collect_preseason(pre_years_to_fetch)
    new_season = collect_season_stats(season_years_to_fetch)

    pre_df = union_align([
        existing_pre[~existing_pre.get("year", pd.Series(dtype=int)).isin(pre_years_to_fetch)] if not existing_pre.empty else existing_pre,
        new_pre,
    ])
    season_df = union_align([
        existing_season[~existing_season.get("year", pd.Series(dtype=int)).isin(season_years_to_fetch)] if not existing_season.empty else existing_season,
        new_season,
    ])

    # --- Weekly tables: fetch only missing (year, week) pairs ---
    weekly_df = pd.DataFrame()
    weekly_stats_df = pd.DataFrame()
    weekly_injuries_df = pd.DataFrame()

    if args.include_weekly:
        existing_weekly_proj = pd.DataFrame() if args.full_refresh else read_existing_table(engine, "weekly_projections")
        existing_weekly_stats = pd.DataFrame() if args.full_refresh else read_existing_table(engine, "weekly_stats")

        proj_have = existing_year_weeks(existing_weekly_proj)
        stats_have = existing_year_weeks(existing_weekly_stats)

        proj_missing = {y: [w for w in weeks if (y, w) not in proj_have] for y in years}
        stats_missing = {y: [w for w in weeks if (y, w) not in stats_have] for y in years}

        new_weekly_proj = collect_weekly_projections(proj_missing)
        new_weekly_stats = collect_weekly_stats(stats_missing)

        weekly_df = union_align([existing_weekly_proj, new_weekly_proj]) if not new_weekly_proj.empty else existing_weekly_proj
        weekly_stats_df = union_align([existing_weekly_stats, new_weekly_stats]) if not new_weekly_stats.empty else existing_weekly_stats

    if args.include_injuries:
        existing_weekly_inj = pd.DataFrame() if args.full_refresh else read_existing_table(engine, "weekly_injuries")
        inj_have = existing_year_weeks(existing_weekly_inj)
        inj_missing = {y: [w for w in weeks if (y, w) not in inj_have] for y in years}

        new_weekly_inj = collect_weekly_injuries(inj_missing)
        weekly_injuries_df = pd.concat([existing_weekly_inj, new_weekly_inj], ignore_index=True) if not new_weekly_inj.empty else existing_weekly_inj

    # --- Write tables (replace: we've already merged old + new above) ---
    if not pre_df.empty:
        pre_df.to_sql("preseason_projections", con=engine, if_exists="replace", index=False)
    if not season_df.empty:
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
