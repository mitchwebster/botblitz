import pytest
from blitz_env.bootstrap_data import build_season

STATS_CACHE_2025 = "data/stats/2025/stats.db"

@pytest.fixture
def season_db_2025(tmp_path):
    """A freshly built 2025 season.db (offline, from the tracked scrape cache)."""
    path = tmp_path / "season.db"
    build_season(2025, stats_path=STATS_CACHE_2025, season_path=str(path))
    return str(path)
