import os
import sqlite3

import pandas as pd
import pytest

from blitz_env.load_stats_nflreadr import _add_kicking_points


def test_kicking_points_use_distance_tiers_and_pats():
    df = pd.DataFrame({
        "position": ["K"],
        "FPTS": [0.0],
        "fg_made_0_19": [1], "fg_made_20_29": [1], "fg_made_30_39": [1],
        "fg_made_40_49": [1], "fg_made_50_59": [1], "fg_made_60_": [1],
        "fg_missed": [2], "pat_made": [3], "pat_missed": [1],
    })
    # 3*3 (0-39) + 4 (40-49) + 5*2 (50+) + 3 PAT; misses score 0
    assert _add_kicking_points(df)["FPTS"].tolist() == [26.0]


def test_kicking_points_add_on_top_of_existing_fpts():
    # A kicker's trick-play yards already sit in nflverse's fantasy_points_ppr
    df = pd.DataFrame({
        "position": ["K", "WR"],
        "FPTS": [0.4, 12.5],
        "fg_made_30_39": [2, float("nan")],
        "pat_made": [1, float("nan")],
    })
    assert _add_kicking_points(df)["FPTS"].tolist() == [7.4, 12.5]


def test_kicking_points_handle_nan_fpts_and_missing_columns():
    df = pd.DataFrame({"position": ["K"], "FPTS": [float("nan")], "pat_made": [2]})
    assert _add_kicking_points(df)["FPTS"].tolist() == [2.0]


SHIPPED_2026_DB = "data/game_states/2026/season.db"


@pytest.mark.skipif(
    not os.path.isfile(SHIPPED_2026_DB),
    reason="shipped 2026 season.db not built yet",
)
def test_shipped_2026_kickers_score_points():
    conn = sqlite3.connect(SHIPPED_2026_DB)
    try:
        for table in ("weekly_stats", "season_stats"):
            fpts, fg = conn.execute(
                f"SELECT SUM(FPTS), SUM(fg_made) FROM {table} WHERE position = 'K' AND year = 2025"
            ).fetchone()
            assert fg > 0
            assert fpts > 3 * fg, f"{table}: 2025 kickers made {fg} FGs but scored only {fpts}"
    finally:
        conn.close()
