import sys

def test_classes_removed():
    import blitz_env
    assert not hasattr(blitz_env, "StatsDB")
    assert not hasattr(blitz_env, "ProjectionsDB")

def test_scraper_helpers_still_available():
    # collectors still need these
    from blitz_env.stats_db import fp_seasonal_years, fp_weekly_years, fp_stats_dynamic
    from blitz_env.projections_db import fp_projections, load_nfl_projections_all_positions

def test_import_stays_lean():
    for m in ("nfl_data_py", "requests", "bs4"):
        sys.modules.pop(m, None)
    import importlib, blitz_env
    importlib.reload(blitz_env)
    assert "nfl_data_py" not in sys.modules
    assert "requests" not in sys.modules
    assert "bs4" not in sys.modules
