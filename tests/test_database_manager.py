from blitz_env.models import DatabaseManager, Player

def _db(path):
    DatabaseManager.DB_URL = f"sqlite:///{path}"
    return DatabaseManager()

def test_weekly_and_seasonal_accessors(season_db_2025):
    db = _db(season_db_2025)
    try:
        chase = db.get_player_by_id("19788")
        assert chase.full_name == "Ja'Marr Chase"

        weekly = db.get_weekly_data(chase)
        assert not weekly.empty
        assert "FPTS" in weekly.columns
        # normalized numeric helpers exist
        assert str(weekly["season"].dtype) == "Int64"
        assert str(weekly["week"].dtype) == "Int64"

        seasonal = db.get_seasonal_data(chase)
        assert "FPTS" in seasonal.columns
    finally:
        db.close()

def test_seasons_filter(season_db_2025):
    db = _db(season_db_2025)
    try:
        chase = db.get_player_by_id("19788")
        # weekly_stats in the 2025 cache is year 2025 only
        assert not db.get_weekly_data(chase, seasons=[2025]).empty
        assert db.get_weekly_data(chase, seasons=[1999]).empty
    finally:
        db.close()
