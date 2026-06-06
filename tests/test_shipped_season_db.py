import os
import sqlite3
import pytest

SHIPPED_DB = "data/game_states/2025/season.db"


@pytest.mark.skipif(
    not os.path.isfile(SHIPPED_DB),
    reason="shipped 2025 season.db not built yet",
)
def test_shipped_season_db_has_reference_tables_and_pool():
    conn = sqlite3.connect(SHIPPED_DB)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("players", "season_stats", "weekly_stats",
                  "weekly_projections", "preseason_projections", "weekly_injuries"):
            assert t in tables, f"missing reference table {t}"

        # league-state tables are NOT shipped (engine/harness create them at run time)
        for t in ("bots", "matchups", "league_settings", "game_statuses"):
            assert t not in tables, f"league-state table {t} should not be shipped"

        # players pool is populated and marked draftable
        player_count = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        assert player_count >= 500, f"player pool too small: {player_count}"
        chase = conn.execute(
            "SELECT full_name, rank, availability FROM players WHERE id = '19788'"
        ).fetchone()
        assert chase == ("Ja'Marr Chase", 1, "AVAILABLE")

        # rolling reference tables carry the full 2025 season
        assert conn.execute("SELECT COUNT(*) FROM weekly_stats").fetchone()[0] > 1000
    finally:
        conn.close()
