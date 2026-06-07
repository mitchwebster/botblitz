"""Draft pick selection tests for jack_bot (issue #264).

Two layers:
  * PURE unit tests for ``choose_draft_pick`` — synthetic, in-memory scored
    frames exercising each roster-need / guardrail branch (no DB, no engine).
  * An integration test that loads ``jack_bot.py`` via importlib and runs the
    fully-wired ``draft_player()`` against the ``season_db_2025`` fixture,
    mirroring ``tests/test_standard_bot_draft.py``.
"""
import importlib.util

import numpy as np
import pandas as pd

from blitz_env.models import DatabaseManager


def _load_jack_bot():
    spec = importlib.util.spec_from_file_location(
        "jack_bot", "bots/nfl2025/jack_bot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Standard 2025-style SUPERFLEX league slots (13 roster spots).
SUPERFLEX_SLOTS = {
    "QB": 1, "RB": 2, "WR": 2, "TE": 1,
    "FLEX": 1, "SUPERFLEX": 1, "BENCH": 3, "K": 1, "DST": 1,
}


def _scored_rows(rows):
    """Build a value-sorted (best-first) scored frame from simple dicts.

    Each dict needs: id, position, value_score, and optionally projected_fpts /
    rank (defaulted). This mimics the shape ``score_draft_pool`` returns.
    """
    df = pd.DataFrame([
        {
            "id": r["id"],
            "position": r["position"],
            "projected_fpts": r.get("projected_fpts", 100.0),
            "rank": r.get("rank", 1),
            "value_score": r["value_score"],
        }
        for r in rows
    ])
    return df.sort_values("value_score", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Roster-need filter
# --------------------------------------------------------------------------- #
def test_roster_need_filter_excludes_filled_positions():
    bot = _load_jack_bot()
    # No bench, no flex/superflex: a filled WR slot is truly unrosterable.
    slots = {"QB": 1, "RB": 1, "WR": 1, "TE": 1,
             "FLEX": 0, "SUPERFLEX": 0, "BENCH": 0, "K": 1, "DST": 1}
    # WR, QB, TE, K, DST filled; only RB still open.
    my_roster = {"a": "QB", "b": "WR", "c": "TE", "d": "K", "e": "DST"}
    scored = _scored_rows([
        {"id": "wr_top", "position": "WR", "value_score": 100.0},
        {"id": "rb_only", "position": "RB", "value_score": 50.0},
    ])
    # Despite the WR's higher value, only RB is rosterable.
    assert bot.choose_draft_pick(scored, my_roster, round=2, slots=slots) == "rb_only"


# --------------------------------------------------------------------------- #
# QB-deadline guardrail
# --------------------------------------------------------------------------- #
def test_qb_deadline_forces_qb_when_understocked_late():
    bot = _load_jack_bot()
    slots = SUPERFLEX_SLOTS                      # total_rounds 13, deadline 11
    my_roster = {"q1": "QB"}                     # only one QB (need two)
    scored = _scored_rows([
        {"id": "wr_stud", "position": "WR", "value_score": 200.0},
        {"id": "qb2", "position": "QB", "value_score": 10.0},
    ])
    # Past the deadline: the QB is forced even though the WR scores far higher.
    assert bot.choose_draft_pick(scored, my_roster, round=11, slots=slots) == "qb2"


def test_qb_deadline_does_not_fire_early():
    bot = _load_jack_bot()
    slots = SUPERFLEX_SLOTS
    my_roster = {"q1": "QB"}
    scored = _scored_rows([
        {"id": "wr_stud", "position": "WR", "value_score": 200.0},
        {"id": "qb2", "position": "QB", "value_score": 10.0},
    ])
    # Early in the draft the guardrail is dormant: best value wins.
    assert bot.choose_draft_pick(scored, my_roster, round=3, slots=slots) == "wr_stud"


# --------------------------------------------------------------------------- #
# K/DST reserve guardrail
# --------------------------------------------------------------------------- #
def test_kdst_reserve_fills_required_slots_in_final_rounds():
    bot = _load_jack_bot()
    slots = SUPERFLEX_SLOTS                      # total_rounds 13, kdst reserve = last 2
    # Two QBs already (so the QB guardrail stays dormant); no K/DST yet.
    my_roster = {"q1": "QB", "q2": "QB"}
    scored = _scored_rows([
        {"id": "wr_stud", "position": "WR", "value_score": 300.0},
        {"id": "k1", "position": "K", "value_score": 5.0},
        {"id": "dst1", "position": "DST", "value_score": 4.0},
    ])
    # Final-round reserve forces a K/DST over the far more valuable WR.
    pick = bot.choose_draft_pick(scored, my_roster, round=12, slots=slots)
    assert pick in ("k1", "dst1")


# --------------------------------------------------------------------------- #
# Anti-hoard cap
# --------------------------------------------------------------------------- #
def test_anti_hoard_cap_blocks_overstocked_position():
    bot = _load_jack_bot()
    # WR startable = 1; cap = 1 + ANTI_HOARD_BENCH_DEPTH["WR"].
    slots = {"QB": 1, "RB": 1, "WR": 1, "TE": 1,
             "FLEX": 0, "SUPERFLEX": 0, "BENCH": 5, "K": 1, "DST": 1}
    wr_cap = 1 + bot.ANTI_HOARD_BENCH_DEPTH["WR"]
    my_roster = {f"wr{i}": "WR" for i in range(wr_cap)}   # already at the cap
    scored = _scored_rows([
        {"id": "wr_more", "position": "WR", "value_score": 100.0},
        {"id": "rb_next", "position": "RB", "value_score": 50.0},
    ])
    # WR is capped out, so the lower-value RB is taken instead.
    assert bot.choose_draft_pick(scored, my_roster, round=5, slots=slots) == "rb_next"


# --------------------------------------------------------------------------- #
# Rank fallback when projections are absent
# --------------------------------------------------------------------------- #
def test_falls_back_to_rank_when_no_projections():
    bot = _load_jack_bot()
    slots = SUPERFLEX_SLOTS
    floor = bot.NO_PROJECTION_FLOOR
    scored = _scored_rows([
        {"id": "wr_good_rank", "position": "WR",
         "projected_fpts": np.nan, "rank": 40, "value_score": floor - 40},
        {"id": "wr_bad_rank", "position": "WR",
         "projected_fpts": np.nan, "rank": 90, "value_score": floor - 90},
    ])
    # No projected candidate => order by players.rank (lower is better).
    assert bot.choose_draft_pick(scored, {}, round=4, slots=slots) == "wr_good_rank"


# --------------------------------------------------------------------------- #
# Integration: draft_player() end-to-end against the real season DB
# --------------------------------------------------------------------------- #
def test_jack_bot_draft_player_runs(season_db_2025, tmp_path, monkeypatch):
    scratch = tmp_path / "gamestate.db"
    DatabaseManager.DB_URL = f"sqlite:///{scratch}"
    import harness.simulate_draft as sd
    monkeypatch.setattr(sd, "get_season_db_path", lambda year: season_db_2025)
    sd.init_database(2025)

    bot = _load_jack_bot()
    pid = bot.draft_player()
    assert isinstance(pid, str) and pid != ""

    # The returned id must be a genuinely available player.
    db = DatabaseManager()
    try:
        row = pd.read_sql(
            f"SELECT availability FROM players WHERE id = '{pid}'", db.engine)
        assert not row.empty
        assert row.iloc[0]["availability"] == "AVAILABLE"
    finally:
        db.close()
