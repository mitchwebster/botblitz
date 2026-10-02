"""Shared FantasyPros/gsis/sleeper ID crosswalk, sourced from nflverse.

Used to join nflreadr's gsis_id-keyed data (stats, injuries) back to
FantasyPros' own fantasypros_id, which is this repo's primary player key
(blitz_env.models.Player.id).
"""

import os
import subprocess
import tempfile

import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FETCH_SCRIPT = os.path.join(_REPO_ROOT, "fetch_playerids.R")

_crosswalk_df = None


def load_player_id_crosswalk() -> pd.DataFrame:
    """gsis_id -> fantasypros_id, sleeper_id, via nflreadr::load_ff_playerids()."""
    global _crosswalk_df
    if _crosswalk_df is not None:
        return _crosswalk_df

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        out_path = tmp.name

    try:
        subprocess.run(
            ["Rscript", _FETCH_SCRIPT, out_path],
            check=True, capture_output=True, text=True, timeout=60,
        )
        ids = pd.read_csv(out_path)
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)

    ids = ids.dropna(subset=["gsis_id"])
    # A small number of gsis_ids are genuinely ambiguous in the source data
    # (two different real players sharing one ID -- an nflverse data-quality
    # quirk affecting a handful of obscure historical players). Deduping
    # arbitrarily picks one; that's an accepted, rare inaccuracy rather than
    # a merge fanning out every row for common cases.
    ids = ids.drop_duplicates(subset=["gsis_id"])

    # fantasypros_id/sleeper_id are NA for some rows (not every gsis_id maps
    # to every platform), which forces pandas to read them as float64 (can't
    # hold NaN as int) -- every id would otherwise silently become "X.0" once
    # merged downstream and written out as text, breaking every join against
    # players.id (a clean string like "11687"). Format as clean nullable
    # integer strings instead.
    for col in ("fantasypros_id", "sleeper_id"):
        ids[col] = ids[col].apply(lambda v: str(int(v)) if pd.notna(v) else None)

    _crosswalk_df = ids
    return _crosswalk_df
