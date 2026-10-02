import sys

def test_classes_removed():
    import blitz_env
    assert not hasattr(blitz_env, "StatsDB")
    assert not hasattr(blitz_env, "ProjectionsDB")

def test_scraper_helpers_still_available():
    # collectors still need these -- projections/stats/injuries are all sourced
    # via R (ffpros/nflreadr) as of the 2026 season-prep bootstrap simplification.
    from blitz_env.load_projections_ffpros import fetch_projections
    from blitz_env.load_stats_nflreadr import fetch_season_stats
    from blitz_env.load_injuries_nflverse import fetch_season_injuries

def test_import_stays_lean():
    for m in ("nfl_data_py", "requests", "bs4"):
        sys.modules.pop(m, None)
    import importlib, blitz_env
    importlib.reload(blitz_env)
    assert "nfl_data_py" not in sys.modules
    assert "requests" not in sys.modules
    assert "bs4" not in sys.modules
