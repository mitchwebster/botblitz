"""Offline unit tests for the VORP draft-valuation core in jack_bot (issue #263).

These exercise ONLY the pure functions (compute_replacement_baselines,
score_draft_pool) with synthetic in-memory DataFrames — no DB, no engine.
"""
import importlib.util

import numpy as np
import pandas as pd


def _load_jack_bot():
    spec = importlib.util.spec_from_file_location(
        "jack_bot", "bots/nfl2025/jack_bot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Standard 2025-style SUPERFLEX league slots.
SUPERFLEX_SLOTS = {
    "QB": 1, "RB": 2, "WR": 2, "TE": 1,
    "FLEX": 1, "SUPERFLEX": 1, "BENCH": 6, "K": 1, "DST": 1,
}
ONE_QB_SLOTS = {
    "QB": 1, "RB": 2, "WR": 2, "TE": 1,
    "FLEX": 1, "BENCH": 6, "K": 1, "DST": 1,
}


def _make_pool():
    """Synthetic available pool: deep QB/RB/WR/TE ladders with linear FPTS."""
    rows = []
    pid = 0

    def ladder(position, top, step, count):
        nonlocal pid
        for i in range(count):
            pid += 1
            rows.append({
                "id": f"p{pid}",
                "position": position,
                "projected_fpts": top - i * step,
                "last_season_fpts": np.nan,        # projection-only (rookies)
                "position_tier": 1 + i // 6,        # 6 players per tier
                "rank": pid,
            })

    ladder("QB", 380.0, 6.0, 30)
    ladder("RB", 310.0, 3.0, 60)
    ladder("WR", 300.0, 3.0, 60)
    ladder("TE", 200.0, 4.0, 30)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# compute_replacement_baselines
# --------------------------------------------------------------------------- #
def test_baselines_are_league_correct_per_position():
    bot = _load_jack_bot()
    b = bot.compute_replacement_baselines(SUPERFLEX_SLOTS, num_teams=10)

    # Hard-slot positions: count * num_teams (K/DST have no flex contribution).
    assert b["K"] == 10
    assert b["DST"] == 10
    # RB/WR get base 2*10 plus a share of FLEX (+non-QB SUPERFLEX) => deeper.
    assert b["RB"] > 20
    assert b["WR"] > 20
    # TE base 1*10 plus a small flex share.
    assert b["TE"] >= 10


def test_superflex_qb_baseline_reflects_two_qbs_per_team():
    bot = _load_jack_bot()
    sf = bot.compute_replacement_baselines(SUPERFLEX_SLOTS, num_teams=10)
    one = bot.compute_replacement_baselines(ONE_QB_SLOTS, num_teams=10)

    # 1-QB league: replacement QB is roughly the last starter (~1 per team).
    assert one["QB"] == 10
    # SUPERFLEX: ~two QBs started per team, discounted for punts => ~1.75/team.
    assert 15 <= sf["QB"] <= 20
    assert sf["QB"] > one["QB"]


# --------------------------------------------------------------------------- #
# score_draft_pool — VORP, blend, tiers, fallback, recompute
# --------------------------------------------------------------------------- #
def test_vorp_is_blended_minus_supplied_baseline():
    bot = _load_jack_bot()
    df = pd.DataFrame([
        {"id": "a", "position": "RB", "projected_fpts": 300.0,
         "last_season_fpts": np.nan, "position_tier": np.nan, "rank": 1},
    ])
    # depth 1 => replacement is the only/best RB itself => VORP 0.
    scored = bot.score_draft_pool(df, {"RB": 1})
    row = scored.iloc[0]
    assert row["baseline_fpts"] == 300.0
    assert row["vorp"] == 0.0


def test_reliability_blend_uses_history_when_present():
    bot = _load_jack_bot()
    df = pd.DataFrame([
        # with history: blended = 0.7*proj + 0.3*last
        {"id": "hist", "position": "RB", "projected_fpts": 300.0,
         "last_season_fpts": 200.0, "position_tier": np.nan, "rank": 1},
        # rookie / no history: blended = proj
        {"id": "rook", "position": "RB", "projected_fpts": 300.0,
         "last_season_fpts": np.nan, "position_tier": np.nan, "rank": 2},
    ])
    scored = bot.score_draft_pool(df, {"RB": 99}).set_index("id")
    expected = 0.7 * 300.0 + 0.3 * 200.0
    assert scored.loc["hist", "blended_fpts"] == expected
    assert scored.loc["rook", "blended_fpts"] == 300.0
    # The historical player regressed down, so the rookie projection wins.
    assert scored.loc["rook", "value_score"] > scored.loc["hist", "value_score"]


def test_tier_cliff_bump_applied_from_position_tier():
    bot = _load_jack_bot()
    df = pd.DataFrame([
        {"id": "t1", "position": "WR", "projected_fpts": 250.0,
         "last_season_fpts": np.nan, "position_tier": 1, "rank": 1},
        {"id": "t3", "position": "WR", "projected_fpts": 250.0,
         "last_season_fpts": np.nan, "position_tier": 3, "rank": 2},
    ])
    scored = bot.score_draft_pool(df, {"WR": 99}).set_index("id")
    # Same projection/VORP, but the top-tier player gets the bigger bump.
    assert scored.loc["t1", "tier_bump"] > scored.loc["t3", "tier_bump"]
    assert scored.loc["t1", "value_score"] > scored.loc["t3", "value_score"]


def test_no_projection_falls_back_to_rank_order_below_projected():
    bot = _load_jack_bot()
    df = pd.DataFrame([
        {"id": "proj", "position": "WR", "projected_fpts": 100.0,
         "last_season_fpts": np.nan, "position_tier": np.nan, "rank": 500},
        {"id": "noproj_good", "position": "WR", "projected_fpts": np.nan,
         "last_season_fpts": np.nan, "position_tier": np.nan, "rank": 50},
        {"id": "noproj_bad", "position": "WR", "projected_fpts": np.nan,
         "last_season_fpts": np.nan, "position_tier": np.nan, "rank": 80},
    ])
    scored = bot.score_draft_pool(df, {"WR": 1})
    order = list(scored["id"])
    # Projected player always above any no-projection player...
    assert order[0] == "proj"
    # ...and amongst no-projection players, better (lower) rank ranks higher.
    assert order.index("noproj_good") < order.index("noproj_bad")


def test_baseline_recomputes_against_currently_available_pool():
    bot = _load_jack_bot()
    full = _make_pool()
    baselines = bot.compute_replacement_baselines(SUPERFLEX_SLOTS, num_teams=10)

    scored_full = bot.score_draft_pool(full, baselines)
    base_full = scored_full[scored_full["position"] == "QB"]["baseline_fpts"].iloc[0]

    # Remove the top 15 QBs from the pool: the replacement QB is now a weaker
    # player, so the recomputed baseline must drop.
    qb_ids = [f"p{i}" for i in range(1, 16)]
    depleted = full[~full["id"].isin(qb_ids)]
    scored_dep = bot.score_draft_pool(depleted, baselines)
    base_dep = scored_dep[scored_dep["position"] == "QB"]["baseline_fpts"].iloc[0]

    assert base_dep < base_full


# --------------------------------------------------------------------------- #
# The headline acceptance case: SUPERFLEX QB premium
# --------------------------------------------------------------------------- #
def test_top_qb_outranks_comparable_wr_rb_under_superflex():
    bot = _load_jack_bot()
    pool = _make_pool()

    sf_baselines = bot.compute_replacement_baselines(SUPERFLEX_SLOTS, num_teams=10)
    scored = bot.score_draft_pool(pool, sf_baselines).set_index("id")

    top_qb = scored.loc["p1", "value_score"]          # best QB
    top_rb = scored.loc["p31", "value_score"]         # best RB
    top_wr = scored.loc["p91", "value_score"]         # best WR

    # Once the deep SUPERFLEX QB baseline is applied, the elite QB is the most
    # valuable pick despite comparable raw projections at RB/WR.
    assert top_qb > top_wr
    assert top_qb > top_rb

    # Sanity: under a 1-QB baseline the QB premium collapses and a skill player
    # is at least as valuable, confirming the premium comes from SUPERFLEX.
    one_qb_baselines = bot.compute_replacement_baselines(ONE_QB_SLOTS, num_teams=10)
    scored_1qb = bot.score_draft_pool(pool, one_qb_baselines).set_index("id")
    assert scored_1qb.loc["p1", "value_score"] < scored.loc["p1", "value_score"]
    assert scored_1qb.loc["p91", "value_score"] > scored_1qb.loc["p1", "value_score"]
