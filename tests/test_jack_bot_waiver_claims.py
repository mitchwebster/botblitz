"""Offline unit tests for the waiver claim selector in jack_bot (issue #266).

These exercise ONLY the pure ``select_waiver_claims`` (and the supporting smoke
imports) with synthetic in-memory DataFrames — no DB, no engine, fully offline.
They cover the conservative no-trigger case, each trigger (severe injury, bye
with no cover, clear underperformance), drop=weakest / add=best-at-position,
ordering by upgrade with fallback claims appended, and budget handling.
"""
import importlib.util

import pandas as pd


def _load_jack_bot():
    spec = importlib.util.spec_from_file_location(
        "jack_bot", "bots/nfl2025/jack_bot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _team(rows):
    return pd.DataFrame(
        rows, columns=["id", "position", "forward_value", "game_status", "on_bye"]
    )


def _avail(rows):
    return pd.DataFrame(rows, columns=["id", "position", "forward_value"])


# --------------------------------------------------------------------------- #
# Smoke: module loads and the public surface exists.
# --------------------------------------------------------------------------- #
def test_module_imports_and_exposes_functions():
    bot = _load_jack_bot()
    for name in (
        "select_waiver_claims",
        "perform_weekly_fantasy_actions",
        "load_waiver_valuation_inputs",
        "get_remaining_waiver_budget",
        "get_my_bot_id",
        "player_forward_value",
        "size_bid",
    ):
        assert callable(getattr(bot, name)), name


# --------------------------------------------------------------------------- #
# (a) No trigger -> empty list (conservative, no churn).
# --------------------------------------------------------------------------- #
def test_no_trigger_returns_empty():
    bot = _load_jack_bot()
    # All rostered players healthy, not on bye, well above startable threshold.
    team = _team([
        {"id": "r1", "position": "RB", "forward_value": 18.0, "game_status": None, "on_bye": False},
        {"id": "r2", "position": "WR", "forward_value": 16.0, "game_status": None, "on_bye": False},
        {"id": "r3", "position": "QB", "forward_value": 22.0, "game_status": None, "on_bye": False},
    ])
    # Even a much better free agent does NOT trigger churn without a real trigger.
    avail = _avail([
        {"id": "a1", "position": "RB", "forward_value": 30.0},
        {"id": "a2", "position": "WR", "forward_value": 29.0},
    ])
    assert bot.select_waiver_claims(team, avail, remaining_budget=100, week=5) == []


def test_no_trigger_when_underperformer_has_no_better_available():
    bot = _load_jack_bot()
    team = _team([
        {"id": "r1", "position": "RB", "forward_value": 2.0, "game_status": None, "on_bye": False},
        {"id": "r2", "position": "WR", "forward_value": 15.0, "game_status": None, "on_bye": False},
    ])
    # Best available RB is no better than the weak rostered RB -> not a trigger.
    avail = _avail([{"id": "a1", "position": "RB", "forward_value": 2.5}])
    assert bot.select_waiver_claims(team, avail, remaining_budget=100, week=5) == []


# --------------------------------------------------------------------------- #
# (b) Severe-injury trigger.
# --------------------------------------------------------------------------- #
def test_severe_injury_trigger_fires():
    bot = _load_jack_bot()
    team = _team([
        {"id": "r1", "position": "RB", "forward_value": 0.0, "game_status": "Out", "on_bye": False},
        {"id": "r2", "position": "WR", "forward_value": 16.0, "game_status": None, "on_bye": False},
    ])
    avail = _avail([{"id": "a1", "position": "RB", "forward_value": 12.0}])
    claims = bot.select_waiver_claims(team, avail, remaining_budget=100, week=5)
    assert claims, "OUT player should trigger a claim"
    assert claims[0]["add_id"] == "a1"
    assert claims[0]["drop_id"] == "r1"  # the OUT player is the weakest -> dropped


# --------------------------------------------------------------------------- #
# (c) Bye-with-no-cover trigger.
# --------------------------------------------------------------------------- #
def test_bye_with_no_cover_triggers():
    bot = _load_jack_bot()
    # The only TE is on bye with no startable TE cover -> trigger.
    team = _team([
        {"id": "te1", "position": "TE", "forward_value": 11.0, "game_status": None, "on_bye": True},
        {"id": "wr1", "position": "WR", "forward_value": 14.0, "game_status": None, "on_bye": False},
        {"id": "k1", "position": "K", "forward_value": 3.0, "game_status": None, "on_bye": False},
    ])
    avail = _avail([{"id": "a_te", "position": "TE", "forward_value": 9.0}])
    claims = bot.select_waiver_claims(team, avail, remaining_budget=100, week=7)
    assert claims
    assert claims[0]["add_id"] == "a_te"
    assert claims[0]["drop_id"] == "k1"  # weakest forward value is dropped


def test_bye_with_startable_cover_does_not_trigger():
    bot = _load_jack_bot()
    # WR1 on bye but WR2 is a startable cover -> no trigger from the bye alone.
    team = _team([
        {"id": "wr1", "position": "WR", "forward_value": 15.0, "game_status": None, "on_bye": True},
        {"id": "wr2", "position": "WR", "forward_value": 12.0, "game_status": None, "on_bye": False},
        {"id": "rb1", "position": "RB", "forward_value": 14.0, "game_status": None, "on_bye": False},
    ])
    avail = _avail([{"id": "a_wr", "position": "WR", "forward_value": 13.0}])
    assert bot.select_waiver_claims(team, avail, remaining_budget=100, week=7) == []


# --------------------------------------------------------------------------- #
# (d) Clear-underperformance trigger.
# --------------------------------------------------------------------------- #
def test_underperformance_triggers():
    bot = _load_jack_bot()
    # RB1 weak (below startable) and a clearly better RB is available.
    team = _team([
        {"id": "rb1", "position": "RB", "forward_value": 3.0, "game_status": None, "on_bye": False},
        {"id": "wr1", "position": "WR", "forward_value": 16.0, "game_status": None, "on_bye": False},
    ])
    avail = _avail([{"id": "a_rb", "position": "RB", "forward_value": 14.0}])
    claims = bot.select_waiver_claims(team, avail, remaining_budget=100, week=6)
    assert claims
    assert claims[0]["add_id"] == "a_rb"
    assert claims[0]["drop_id"] == "rb1"


# --------------------------------------------------------------------------- #
# (e) Drop = weakest rosterable; add = best forward value at the needed position.
# --------------------------------------------------------------------------- #
def test_drop_is_weakest_and_add_is_best_at_position():
    bot = _load_jack_bot()
    team = _team([
        {"id": "out_rb", "position": "RB", "forward_value": 0.0, "game_status": "Out", "on_bye": False},
        {"id": "mid_wr", "position": "WR", "forward_value": 10.0, "game_status": None, "on_bye": False},
        {"id": "weak_k", "position": "K", "forward_value": -1.0, "game_status": None, "on_bye": False},
    ])
    avail = _avail([
        {"id": "rb_best", "position": "RB", "forward_value": 18.0},
        {"id": "rb_ok", "position": "RB", "forward_value": 9.0},
        {"id": "wr_x", "position": "WR", "forward_value": 25.0},
    ])
    claims = bot.select_waiver_claims(team, avail, remaining_budget=100, week=8)
    assert claims
    # Needed position is RB (the OUT player); best RB available is added.
    assert claims[0]["add_id"] == "rb_best"
    # Weakest forward value on the roster is the kicker (-1.0), so it is dropped
    # even though it is not the triggered player.
    assert claims[0]["drop_id"] == "weak_k"


# --------------------------------------------------------------------------- #
# (f) Ordering by upgrade + fallback claims (same drop, alternate add) appended.
# --------------------------------------------------------------------------- #
def test_ordering_by_upgrade_and_fallbacks_appended():
    bot = _load_jack_bot()
    # Two triggers: RB OUT and TE OUT. RB upgrade is bigger -> RB claim first.
    team = _team([
        {"id": "rb_out", "position": "RB", "forward_value": 0.0, "game_status": "Out", "on_bye": False},
        {"id": "te_out", "position": "TE", "forward_value": 0.0, "game_status": "Out", "on_bye": False},
        {"id": "weak", "position": "K", "forward_value": -2.0, "game_status": None, "on_bye": False},
    ])
    avail = _avail([
        {"id": "rb_a", "position": "RB", "forward_value": 20.0},
        {"id": "rb_b", "position": "RB", "forward_value": 17.0},
        {"id": "rb_c", "position": "RB", "forward_value": 15.0},
        {"id": "te_a", "position": "TE", "forward_value": 8.0},
    ])
    claims = bot.select_waiver_claims(team, avail, remaining_budget=200, week=14)
    add_ids = [c["add_id"] for c in claims]
    # Primary order by upgrade: RB (20) before TE (8).
    assert add_ids[0] == "rb_a"
    assert "te_a" in add_ids
    rb_idx = add_ids.index("rb_a")
    te_idx = add_ids.index("te_a")
    assert rb_idx < te_idx
    # Fallbacks: same drop, alternate adds at the TOP trigger's position (RB).
    assert "rb_b" in add_ids and "rb_c" in add_ids
    # Every claim drops the same (weakest) player.
    assert {c["drop_id"] for c in claims} == {"weak"}
    # Fallbacks come after all primaries.
    assert add_ids.index("rb_b") > te_idx
    assert add_ids.index("rb_c") > te_idx


# --------------------------------------------------------------------------- #
# (g) Budget respected.
# --------------------------------------------------------------------------- #
def test_zero_budget_returns_empty():
    bot = _load_jack_bot()
    team = _team([
        {"id": "rb1", "position": "RB", "forward_value": 0.0, "game_status": "Out", "on_bye": False},
    ])
    avail = _avail([{"id": "a1", "position": "RB", "forward_value": 12.0}])
    assert bot.select_waiver_claims(team, avail, remaining_budget=0, week=5) == []


def test_bids_never_exceed_budget():
    bot = _load_jack_bot()
    team = _team([
        {"id": "rb1", "position": "RB", "forward_value": 0.0, "game_status": "Out", "on_bye": False},
        {"id": "k1", "position": "K", "forward_value": 1.0, "game_status": None, "on_bye": False},
    ])
    avail = _avail([
        {"id": "a1", "position": "RB", "forward_value": 25.0},
        {"id": "a2", "position": "RB", "forward_value": 20.0},
    ])
    for budget in (1, 5, 30, 100):
        claims = bot.select_waiver_claims(team, avail, remaining_budget=budget, week=10)
        assert claims
        for c in claims:
            assert 1 <= c["bid"] <= budget
