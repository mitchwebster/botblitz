"""Persists a completed mock draft (see harness.cli_draft) into a scratch
copy of season.db's `mock_draft_picks` table, so ux/ can show a draft board
tab without a second file format and without re-running anything -- same
"the UI already here reads off the sqlite itself" approach as everything
else in ux/.

Writes to a gitignored scratch copy (data/mock_drafts/{year}/season.db), not
the tracked data/game_states/{year}/season.db -- every mock draft run would
otherwise dirty a tracked, committed file for something that's meant to be
disposable and run often, by anyone, without cluttering the repo. The
scratch copy is freshly re-copied from the tracked file on every run, so it
always reflects current real data (stats/projections/schedule) even if the
tracked file has been refreshed since the last mock draft.

One draft at a time: each write replaces the whole table. This is meant for
"here's how the current bot drafts," not a history of past runs.
"""

import json
import os
import shutil

import pandas as pd
from sqlalchemy import create_engine, text

from blitz_env.models import DatabaseManager
from harness.draft_board_html import build_board_data

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TABLE_NAME = "mock_draft_picks"


def scratch_db_path(year: int) -> str:
    return os.path.join(_REPO_ROOT, "data", "mock_drafts", str(year), "season.db")


def write_to_season_db(db: DatabaseManager, user_bot_id: str, year: int, bot_label: str) -> str:
    """Returns the scratch db path written to, or "" if there was no tracked
    season.db for this year to copy from."""
    data = build_board_data(db, user_bot_id)

    rows = []
    for round_idx, row in enumerate(data["grid"]):
        for team_slot, cell in enumerate(row):
            team = data["teams"][team_slot]
            rows.append({
                "year": year,
                "pick": cell["pick"],
                "round": round_idx + 1,
                "team_slot": team_slot,
                "team_name": team["name"],
                "team_owner": team["owner"],
                "is_user": team["is_user"],
                "fantasypros_id": cell.get("player_id"),
                "player_name": cell.get("player"),
                "position": cell.get("position"),
                "nfl_team": cell.get("nfl_team"),
                "bot_label": bot_label,
            })

    tracked_db = os.path.join(_REPO_ROOT, "data", "game_states", str(year), "season.db")
    if not os.path.isfile(tracked_db):
        return ""

    scratch_db = scratch_db_path(year)
    os.makedirs(os.path.dirname(scratch_db), exist_ok=True)
    shutil.copyfile(tracked_db, scratch_db)

    df = pd.DataFrame(rows)
    engine = create_engine(f"sqlite:///{scratch_db}")
    df.to_sql(_TABLE_NAME, con=engine, if_exists="replace", index=False)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{_TABLE_NAME}_pick ON {_TABLE_NAME}(pick)"))

    return scratch_db


def write_league_state(scratch_db: str, db: DatabaseManager, year: int, season_result: dict = None) -> None:
    """Writes the league-state tables ux/ needs for its Matchup Details,
    Current/Last Week, and Leaderboard tabs -- bots, league_settings,
    game_statuses always, plus matchups/weekly_lineups when a
    harness.simulate_season result is given.

    None of these tables exist in the tracked season.db (they're
    created at runtime, see CLAUDE.md's "League state" note), and
    harness.cli_draft's own draft happens in a separate scratch
    gamestate.db -- so without this, those ux tabs hit "no such table"
    against a mock draft's season.db copy. Always written (even with no
    season_result) so the tables exist and those tabs degrade to "no
    data yet" instead of erroring.
    """
    engine = create_engine(f"sqlite:///{scratch_db}")

    bots = db.get_all_bots()
    bots_df = pd.DataFrame([
        {
            "id": b.id,
            "draft_order": b.draft_order,
            "name": b.name,
            "owner": b.owner,
            "current_waiver_priority": b.current_waiver_priority or 0,
            "remaining_waiver_budget": 0,
        }
        for b in bots
    ])
    bots_df.to_sql("bots", con=engine, if_exists="replace", index=False)

    settings = db.get_league_settings()
    settings_df = pd.DataFrame([{
        "id": 1,
        "year": year,
        "player_slots": json.dumps(settings.player_slots),
        "is_snake_draft": bool(settings.is_snake_draft),
        "total_rounds": settings.total_rounds,
        "points_per_reception": settings.points_per_reception,
        "num_teams": settings.num_teams,
    }])
    settings_df.to_sql("league_settings", con=engine, if_exists="replace", index=False)

    current_fantasy_week = max(season_result["weeks"]) if season_result else 1
    status_df = pd.DataFrame([{
        "id": 1,
        "current_bot_id": None,
        "current_draft_pick": settings.total_rounds * len(bots) + 1,
        "current_fantasy_week": current_fantasy_week,
    }])
    status_df.to_sql("game_statuses", con=engine, if_exists="replace", index=False)

    matchup_rows = []
    lineup_rows = []
    if season_result:
        for week_entry in season_result["weekly_results"]:
            week = week_entry["week"]
            for m in week_entry["matchups"]:
                matchup_rows.append({
                    "week": week,
                    "home_bot_id": m["home"],
                    "visitor_bot_id": m["away"],
                    "home_score": m["home_score"],
                    "visitor_score": m["away_score"],
                    "winning_bot_id": m["winner"],
                    "is_playoff_matchup": False,
                    "home_play_in_matchup_id": None,
                    "visitor_play_in_matchup_id": None,
                })
                for bot_id, lineup in (("home", m["home_lineup"]), ("away", m["away_lineup"])):
                    bot_id = m["home"] if bot_id == "home" else m["away"]
                    for starter in lineup["starters"]:
                        if starter["id"] is None:
                            continue
                        lineup_rows.append({
                            "week": week, "bot_id": bot_id, "player_id": starter["id"],
                            "points": starter["FPTS"], "slot": starter["slot"],
                        })
                    for bench_player in lineup["bench"]:
                        lineup_rows.append({
                            "week": week, "bot_id": bot_id, "player_id": bench_player["id"],
                            "points": bench_player["FPTS"], "slot": "BENCH",
                        })

    matchups_df = pd.DataFrame(matchup_rows, columns=[
        "week", "home_bot_id", "visitor_bot_id", "home_score", "visitor_score",
        "winning_bot_id", "is_playoff_matchup", "home_play_in_matchup_id", "visitor_play_in_matchup_id",
    ])
    matchups_df.to_sql("matchups", con=engine, if_exists="replace", index=True, index_label="id")

    lineups_df = pd.DataFrame(lineup_rows, columns=["week", "bot_id", "player_id", "points", "slot"])
    lineups_df.to_sql("weekly_lineups", con=engine, if_exists="replace", index=True, index_label="id")
