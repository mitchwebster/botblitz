import pytest
from blitz_env.load_players import load_players


def test_load_players_2025_works():
    players = load_players(2025)
    assert len(players) > 0
    # Ja'Marr Chase is fantasypros_id 19788, rank 1 in the 2025 ranks.
    chase = next((p for p in players if p.id == "19788"), None)
    assert chase is not None
    assert chase.full_name == "Ja'Marr Chase"


def test_load_players_missing_year_raises_actionable_error():
    # A year with no player_ranks_<year>.csv must raise a clear, actionable error
    # (FileNotFoundError), NOT the old generic "year not supported".
    with pytest.raises(FileNotFoundError) as exc:
        load_players(2099)
    msg = str(exc.value)
    assert "player_ranks_2099.csv" in msg
    assert "fetch_ranks.R" in msg
