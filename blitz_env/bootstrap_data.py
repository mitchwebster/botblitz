#!/usr/bin/env python3
"""Bootstrap a season's data into a single per-season SQLite DB.

Two phases:
  scrape       -> data/stats/{year}/stats.db        (network; the scrape cache)
  build-season -> data/game_states/{year}/season.db (offline; the live per-season DB)

`build_season` copies the reference tables (stats/projections/injuries) from the
scrape cache and materializes the draftable `players` pool. It does NOT create
league-state tables (bots/matchups/...); the engine and harness own those.
"""

import argparse
import os
import sqlite3

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from blitz_env.models import Player
from blitz_env.load_players import load_players

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Reference tables copied from the scrape cache into season.db (copied only if present).
_REFERENCE_TABLES = (
    "season_stats",
    "preseason_projections",
    "weekly_stats",
    "weekly_projections",
    "weekly_injuries",
)


def get_stats_cache_path(year: int) -> str:
    """The scraped reference DB (build input; bots never read this)."""
    return os.path.join(_REPO_ROOT, "data", "stats", str(year), "stats.db")


def get_season_db_path(year: int) -> str:
    """The single per-season DB that the engine, harness, and bots use."""
    return os.path.join(_REPO_ROOT, "data", "game_states", str(year), "season.db")


def build_season(year: int, stats_path: str = None, season_path: str = None) -> str:
    """Create a fresh season.db: players pool + reference tables from the scrape cache."""
    stats_path = stats_path or get_stats_cache_path(year)
    season_path = season_path or get_season_db_path(year)

    if not os.path.isfile(stats_path):
        raise FileNotFoundError(
            f"Scrape cache not found at '{stats_path}'. Run "
            f"`python3 -m blitz_env.bootstrap_data scrape --year {year}` first."
        )

    os.makedirs(os.path.dirname(season_path), exist_ok=True)
    if os.path.exists(season_path):
        os.remove(season_path)

    # 1) players pool via the ORM schema, populated from the rank CSV
    engine = create_engine(f"sqlite:///{season_path}")
    session = None
    try:
        Player.__table__.create(engine)
        session = sessionmaker(bind=engine)()
        for p in load_players(year):
            session.add(Player(
                id=p.id,
                full_name=p.full_name,
                professional_team=p.professional_team,
                player_bye_week=p.player_bye_week,
                rank=p.rank,
                tier=p.tier,
                position_rank=p.position_rank,
                position_tier=p.position_tier,
                gsis_id=p.gsis_id,
                allowed_positions=list(p.allowed_positions),
                availability="AVAILABLE",
            ))
        session.commit()
    finally:
        if session is not None:
            session.close()
        engine.dispose()

    # 2) reference tables copied from the scrape cache (raw sqlite3 for clean ATTACH)
    conn = sqlite3.connect(season_path)
    try:
        conn.execute("ATTACH DATABASE ? AS cache", (stats_path,))
        for table in _REFERENCE_TABLES:
            present = conn.execute(
                "SELECT 1 FROM cache.sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if present:
                conn.execute(f"CREATE TABLE {table} AS SELECT * FROM cache.{table}")
        conn.commit()
    finally:
        try:
            conn.execute("DETACH DATABASE cache")
        except sqlite3.Error:
            pass
        conn.close()

    return season_path


def scrape(year: int, years_back: int = 10, weeks: str = "1:18") -> str:
    """Full network pull into the scrape cache. Delegates to collect_stats."""
    from blitz_env import collect_stats

    out = get_stats_cache_path(year)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    collect_stats.main([
        "--db", out,
        "--end-year", str(year),
        "--years", str(years_back),
        "--include-weekly",
        "--include-injuries",
        "--weeks", weeks,
    ])
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bootstrap_data")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scrape", help="Network pull into data/stats/{year}/stats.db")
    s.add_argument("--year", type=int, required=True)
    s.add_argument("--years", type=int, default=10)
    s.add_argument("--weeks", default="1:18")

    b = sub.add_parser("build-season",
                       help="Materialize data/game_states/{year}/season.db")
    b.add_argument("--year", type=int, required=True)
    b.add_argument("--stats-path", default=None)
    b.add_argument("--season-path", default=None)

    args = parser.parse_args(argv)
    if args.command == "scrape":
        path = scrape(args.year, years_back=args.years, weeks=args.weeks)
        print(f"Scraped -> {path}")
    elif args.command == "build-season":
        path = build_season(args.year, stats_path=args.stats_path,
                            season_path=args.season_path)
        print(f"Built season DB -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
