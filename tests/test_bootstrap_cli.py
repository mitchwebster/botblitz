import sqlite3
from blitz_env.bootstrap_data import main

def test_cli_build_season(tmp_path):
    season_db = tmp_path / "season.db"
    rc = main([
        "build-season", "--year", "2025",
        "--stats-path", "data/stats/2025/stats.db",
        "--season-path", str(season_db),
    ])
    assert rc == 0
    conn = sqlite3.connect(season_db)
    assert conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] > 0
    conn.close()
