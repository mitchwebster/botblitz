import importlib.util
from blitz_env.models import DatabaseManager


def _load_standard_bot():
    spec = importlib.util.spec_from_file_location(
        "standard_bot", "bots/nfl2025/standard-bot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_standard_bot_draft_player_runs(season_db_2025, tmp_path, monkeypatch):
    scratch = tmp_path / "gamestate.db"
    DatabaseManager.DB_URL = f"sqlite:///{scratch}"
    import harness.simulate_draft as sd
    monkeypatch.setattr(sd, "get_season_db_path", lambda year: season_db_2025)
    sd.init_database(2025)

    bot = _load_standard_bot()
    pid = bot.draft_player()
    assert isinstance(pid, str) and pid != ""
