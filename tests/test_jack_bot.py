import importlib.util

from blitz_env.models import DatabaseManager, GameStatus


def _load_jack_bot():
    spec = importlib.util.spec_from_file_location(
        "jack_bot", "bots/nfl2025/jack_bot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_jack_bot_imports_cleanly():
    # The bot must import in isolation (as it does inside the container).
    mod = _load_jack_bot()
    assert hasattr(mod, "get_my_bot_id")


def test_get_my_bot_id_returns_current_acting_bot(tmp_path, monkeypatch):
    """get_my_bot_id derives identity from game_statuses.current_bot_id, the
    id the engine sets to whichever team it is currently asking about."""
    scratch = tmp_path / "gamestate.db"
    monkeypatch.setattr(DatabaseManager, "DB_URL", f"sqlite:///{scratch}")

    bot = _load_jack_bot()

    db = DatabaseManager()
    try:
        # Engine writes the acting bot here before invoking the bot each turn.
        acting_bot_id = "bot-42"
        db.session.add(GameStatus(current_bot_id=acting_bot_id,
                                  current_draft_pick=1,
                                  current_fantasy_week=3))
        db.session.commit()

        assert bot.get_my_bot_id(db) == acting_bot_id

        # When the engine reassigns the acting team, the helper follows it —
        # no hardcoded identity.
        status = db.get_game_status()
        status.current_bot_id = "bot-7"
        db.session.commit()

        assert bot.get_my_bot_id(db) == "bot-7"
    finally:
        db.close()
