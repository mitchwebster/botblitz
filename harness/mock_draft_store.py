"""Persists a completed mock draft (see harness.cli_draft) into season.db's
own `mock_draft_picks` table, so ux/ can show a draft board tab without a
second file and without re-running anything -- same "the UI already here
reads off the sqlite itself" approach as everything else in ux/.

One draft at a time: each write replaces the whole table. This is meant for
"here's how the current bot drafts," not a history of past runs.
"""

import os

import pandas as pd
from sqlalchemy import create_engine, text

from blitz_env.models import DatabaseManager
from harness.draft_board_html import build_board_data

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TABLE_NAME = "mock_draft_picks"


def write_to_season_db(db: DatabaseManager, user_bot_id: str, year: int, bot_label: str) -> int:
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

    df = pd.DataFrame(rows)
    season_db = os.path.join(_REPO_ROOT, "data", "game_states", str(year), "season.db")
    if not os.path.isfile(season_db):
        return 0

    engine = create_engine(f"sqlite:///{season_db}")
    df.to_sql(_TABLE_NAME, con=engine, if_exists="replace", index=False)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{_TABLE_NAME}_pick ON {_TABLE_NAME}(pick)"))

    return len(df)
