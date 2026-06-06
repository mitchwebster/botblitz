import socket
import shutil
import pandas as pd
from blitz_env.models import DatabaseManager, Player

class _NoNet(socket.socket):
    def connect(self, *a, **k):
        raise RuntimeError("NETWORK BLOCKED")

def test_offline_draft_and_score(season_db_2025, tmp_path, monkeypatch):
    # Point the harness scratch + DatabaseManager at a tmp gamestate.db
    scratch = tmp_path / "gamestate.db"
    DatabaseManager.DB_URL = f"sqlite:///{scratch}"
    monkeypatch.setattr(socket, "socket", _NoNet)

    import harness.simulate_draft as sd
    monkeypatch.setattr(sd, "get_season_db_path", lambda year: season_db_2025)

    def draft_player():
        db = DatabaseManager()
        try:
            p = (db.session.query(Player)
                 .filter(Player.availability == "AVAILABLE")
                 .order_by(Player.rank).first())
            return p.id if p else ""
        finally:
            db.close()

    sd.simulate_draft(draft_player, 2025)

    db = DatabaseManager()
    drafted = pd.read_sql(
        "SELECT COUNT(*) c FROM players WHERE availability='DRAFTED'", db.engine)
    db.close()
    assert int(drafted.iloc[0]["c"]) > 0
