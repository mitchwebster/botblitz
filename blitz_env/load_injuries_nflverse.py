"""Weekly injury reports sourced from nflverse (via R's nflreadr).

Replaces the retired NFL.com HTML scraper: one network request per season
(instead of one per team/week) via `fetch_injuries.R`, and an exact join on
`gsis_id` against the dynastyprocess ID crosswalk (instead of fuzzy name
matching) to recover `fantasypros_id`.
"""

import os
import subprocess
import tempfile

import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FETCH_SCRIPT = os.path.join(_REPO_ROOT, "fetch_injuries.R")
_PLAYER_IDS_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"

_player_ids_df = None


def _load_player_ids() -> pd.DataFrame:
    global _player_ids_df
    if _player_ids_df is None:
        ids = pd.read_csv(_PLAYER_IDS_URL)
        _player_ids_df = ids[["gsis_id", "fantasypros_id", "sleeper_id"]].dropna(subset=["gsis_id"])
    return _player_ids_df


def fetch_season_injuries(year: int) -> pd.DataFrame:
    """Pull one season of nflverse injury reports, joined to FantasyPros IDs."""
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        out_path = tmp.name

    try:
        subprocess.run(
            ["Rscript", _FETCH_SCRIPT, str(year), out_path],
            check=True, capture_output=True, text=True, timeout=60,
        )
        if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            return pd.DataFrame()
        raw = pd.read_csv(out_path)
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)

    if raw.empty:
        return pd.DataFrame()

    merged = raw.merge(_load_player_ids(), on="gsis_id", how="left")

    return pd.DataFrame({
        "year": merged["season"],
        "week": merged["week"],
        "team": merged["team"],
        "position": merged["position"],
        "player_name": merged["full_name"],
        "injury": merged["report_primary_injury"],
        "practice_status": merged["practice_status"],
        "game_status": merged["report_status"],
        "fantasypros_id": merged["fantasypros_id"],
        "gsis_id": merged["gsis_id"],
        "sleeper_id": merged["sleeper_id"],
    })
