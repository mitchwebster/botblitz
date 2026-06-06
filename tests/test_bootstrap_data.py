import sqlite3
from blitz_env.bootstrap_data import build_season

STATS_CACHE_2025 = "data/stats/2025/stats.db"

def test_build_season_materializes_reference_and_players(tmp_path):
    season_db = tmp_path / "season.db"
    build_season(2025, stats_path=STATS_CACHE_2025, season_path=str(season_db))

    conn = sqlite3.connect(season_db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

    # reference tables copied from the scrape cache
    for t in ("season_stats", "preseason_projections", "weekly_stats",
              "weekly_projections", "weekly_injuries"):
        assert t in tables, f"missing reference table {t}"

    # players pool created and populated (Ja'Marr Chase, fantasypros_id 19788, rank 1)
    assert "players" in tables
    chase = conn.execute(
        "SELECT full_name, rank, availability FROM players WHERE id = '19788'"
    ).fetchone()
    assert chase == ("Ja'Marr Chase", 1, "AVAILABLE")

    # NO league-state tables yet (engine/harness own those)
    assert "matchups" not in tables
    assert "bots" not in tables
    conn.close()
