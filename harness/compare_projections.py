#!/usr/bin/env python3
"""
Compare each projection source against actual PPR points.

Ground truth is nflverse's `fantasy_points_ppr` (weekly_stats/season.db) -- the
repo's standard "actual PPR points" column. Predictions come from the three
ffanalytics sources (FFToday, FantasySharks, ESPN), whose `points` column in
data/ffanalytics/{year}/projections.db was already computed under this
league's PPR rules (see fetch_ffanalytics_projections.R).

FantasyPros (ffpros/weekly_projections) is deliberately NOT included: it only
ever has ~10 rows per position (the site-side cap covered elsewhere in this
repo), so any MAE/RMSE computed on it is over a biased sample -- just the
players FantasyPros itself projected best, which are the easiest to predict.
That's an artifact of coverage, not a real quality signal, and isn't
comparable to the other three sources' error over the full, messier player
pool (bench/deep guys included, where misses are naturally larger).

All three are on a PPR basis, so no rescaling is needed -- this is a direct
points-vs-points comparison. Matching projections to actuals is done by
normalized (name, position) since the two pipelines don't share a player id
(ffanalytics has its own internal id; season.db keys on fantasypros_id/gsis_id).

QB/RB/WR/TE only for now. K and DST are excluded: nflreadr's own
fantasy_points_ppr is always 0 for kickers (it's an offense-only formula, not
a bug in our scrape), and weekly_stats has no player name for DST rows (name
matching fails entirely), so neither has usable ground truth here yet without
more work (a custom K scoring formula from raw fg/pat stats, and a
team-abbreviation join for DST).

Usage:
  python3 -m harness.compare_projections --year 2022
  python3 -m harness.compare_projections --year 2022 --week 1
  python3 -m harness.compare_projections            # all years/weeks available in both DBs
"""

import argparse
import os
import re
import sqlite3

import pandas as pd
from rich.console import Console
from rich.table import Table

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    name = name.lower().strip()
    name = re.sub(r"[.'’]", "", name)
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    tokens = [t for t in name.split() if t not in _SUFFIXES]
    return " ".join(tokens)


def ffanalytics_db_path(year: int) -> str:
    return os.path.join(_REPO_ROOT, "data", "ffanalytics", str(year), "projections.db")


def season_db_path() -> str:
    # Only the most recently rebuilt season.db carries the full historical
    # weekly_stats/weekly_projections backfill (season_stats/weekly_stats span
    # all years regardless of which per-year folder they live in) -- older
    # per-year season.db files (e.g. data/game_states/2025/season.db) predate
    # this session's nflreadr/ffpros migration and have the old scraper schema.
    return os.path.join(_REPO_ROOT, "data", "game_states", "2026", "season.db")


def load_actuals(year: int, week: int) -> pd.DataFrame:
    db_path = season_db_path()
    if not os.path.isfile(db_path):
        return pd.DataFrame()
    con = sqlite3.connect(db_path)
    try:
        df = pd.read_sql(
            "SELECT player_display_name AS player, position, fantasy_points_ppr AS actual "
            "FROM weekly_stats WHERE year = ? AND week = ?",
            con, params=(year, week),
        )
    finally:
        con.close()
    if df.empty:
        return df
    df["name_key"] = df["player"].map(normalize_name)
    df["position"] = df["position"].str.upper()
    return df.dropna(subset=["actual"])


def load_ffanalytics_predictions(year: int, week: int) -> pd.DataFrame:
    db_path = ffanalytics_db_path(year)
    if not os.path.isfile(db_path):
        return pd.DataFrame()
    con = sqlite3.connect(db_path)
    try:
        df = pd.read_sql(
            "SELECT source, first_name, last_name, pos AS position, points AS predicted "
            "FROM projections WHERE year = ? AND week = ?",
            con, params=(year, week),
        )
    finally:
        con.close()
    if df.empty:
        return df
    df["player"] = (df["first_name"].fillna("") + " " + df["last_name"].fillna("")).str.strip()
    df["name_key"] = df["player"].map(normalize_name)
    df["position"] = df["position"].str.upper()
    return df.dropna(subset=["predicted"])


def available_year_weeks() -> list[tuple[int, int]]:
    """(year, week) pairs present in BOTH an ffanalytics projections db and season.db actuals."""
    base = os.path.join(_REPO_ROOT, "data", "ffanalytics")
    pairs = []
    if not os.path.isdir(base):
        return pairs
    for year_dir in sorted(os.listdir(base)):
        try:
            year = int(year_dir)
        except ValueError:
            continue
        db_path = os.path.join(base, year_dir, "projections.db")
        if not os.path.isfile(db_path) or not os.path.isfile(season_db_path()):
            continue
        con = sqlite3.connect(db_path)
        try:
            weeks = [w for (w,) in con.execute("SELECT DISTINCT week FROM projections WHERE week > 0")]
        finally:
            con.close()
        for week in sorted(weeks):
            pairs.append((year, week))
    return pairs


_SKILL_POSITIONS = {"QB", "RB", "WR", "TE"}


def compare_one(year: int, week: int) -> pd.DataFrame:
    actuals = load_actuals(year, week)
    if actuals.empty:
        return pd.DataFrame()
    actuals = actuals[actuals["position"].isin(_SKILL_POSITIONS)]

    predictions = load_ffanalytics_predictions(year, week)
    if predictions.empty:
        return pd.DataFrame()
    predictions = predictions[predictions["position"].isin(_SKILL_POSITIONS)]

    merged = predictions.merge(actuals[["name_key", "position", "actual"]], on=["name_key", "position"], how="inner")
    if merged.empty:
        return merged
    merged["year"] = year
    merged["week"] = week
    merged["error"] = merged["predicted"] - merged["actual"]
    return merged[["source", "position", "year", "week", "player", "predicted", "actual", "error"]]


def summarize(all_rows: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    g = all_rows.groupby(group_cols)
    summary = g["error"].agg(
        n="count",
        mae=lambda s: s.abs().mean(),
        rmse=lambda s: (s ** 2).mean() ** 0.5,
        bias="mean",
    ).reset_index()
    corr = g.apply(lambda d: d["predicted"].corr(d["actual"]) if len(d) > 2 else float("nan"))
    summary = summary.merge(corr.rename("corr").reset_index(), on=group_cols)
    return summary.sort_values("mae")


def print_summary_table(title: str, df: pd.DataFrame, group_cols: list[str]) -> None:
    console = Console(force_terminal=True)
    table = Table(title=title)
    for col in group_cols:
        table.add_column(col.title())
    table.add_column("N", justify="right")
    table.add_column("MAE", justify="right")
    table.add_column("RMSE", justify="right")
    table.add_column("Bias", justify="right")
    table.add_column("Corr", justify="right")
    for _, row in df.iterrows():
        table.add_row(
            *[str(row[c]) for c in group_cols],
            str(int(row["n"])),
            f"{row['mae']:.2f}",
            f"{row['rmse']:.2f}",
            f"{row['bias']:+.2f}",
            f"{row['corr']:.3f}" if pd.notna(row["corr"]) else "n/a",
        )
    console.print(table)


def main():
    ap = argparse.ArgumentParser(description="Compare ffanalytics projection sources against actual PPR points.")
    ap.add_argument("--year", type=int, default=None, help="Limit to one year (default: all years available).")
    ap.add_argument("--week", type=int, default=None, help="Limit to one week (default: all weeks available for the year(s)).")
    args = ap.parse_args()

    if args.year is not None and args.week is not None:
        pairs = [(args.year, args.week)]
    elif args.year is not None:
        pairs = [(y, w) for (y, w) in available_year_weeks() if y == args.year]
    else:
        pairs = available_year_weeks()

    if not pairs:
        print("No (year, week) pairs with both ffanalytics projections and season.db actuals found.")
        return

    print(f"Comparing {len(pairs)} (year, week) pair(s): {pairs}")
    all_rows = pd.concat([compare_one(y, w) for y, w in pairs], ignore_index=True, sort=False)
    all_rows = all_rows[all_rows["source"].notna()] if not all_rows.empty else all_rows

    if all_rows.empty:
        print("No overlapping players found between projections and actuals for the selected range.")
        return

    print_summary_table("Overall accuracy by source (PPR points)", summarize(all_rows, ["source"]), ["source"])
    print()
    print_summary_table("Accuracy by source + position", summarize(all_rows, ["source", "position"]), ["source", "position"])


if __name__ == "__main__":
    main()
