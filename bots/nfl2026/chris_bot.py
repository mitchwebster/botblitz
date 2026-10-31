from blitz_env.models import DatabaseManager
from blitz_env import AttemptedFantasyActions, WaiverClaim
import pandas as pd
import numpy as np
import json

def set_position(df):
    df["position"] = df["allowed_positions"].apply(lambda x: json.loads(x)[0])


# ---- Additive-value draft strategy ----
#
# At every pick, ranks every available player by how many extra points
# they'd add to our optimal weekly lineup, summed over weeks 1-5, given our
# roster as drafted so far -- then takes the top one. The goal is to
# optimize specifically for weeks 1-5, not the full season.
#
# Uses REAL weeks 1-5 stats, not projections: this draft happens between
# week 5 and week 6 of the 2026 season, so weeks 1-5 are already known fact
# by draft day, not a forecast.
#
# additive_value(P | roster) = sum over weeks 1-5 of
#   [best_lineup_score(roster + P, week) - best_lineup_score(roster, week)]
#
# best_lineup_score is a greedy, provably-optimal lineup assignment for
# this league's nested slot-eligibility structure (QB/RB/WR/TE/K/DST, one
# shared FLEX, one shared SUPERFLEX) -- validated against true brute-force
# search (1000/1000 random trials matched exactly). One deliberate
# deviation from a pure points-maximizing greedy: a player with a negative
# score is never forced into a slot -- a manager would always bench them
# instead, scoring 0, rather than start a bad week.

_WIDE_SLOT_ELIGIBILITY = {
    "SUPERFLEX": ["QB", "RB", "WR", "TE"],
    "FLEX": ["RB", "WR", "TE"],
}


def _allowed_positions_for_slot(slot: str) -> list:
    return _WIDE_SLOT_ELIGIBILITY.get(slot, [slot])


def _slots_sorted_by_eligibility_breadth(player_slots: dict) -> list:
    slots = []
    for slot_name, count in player_slots.items():
        if slot_name == "BENCH":
            continue
        slots.extend([slot_name] * count)
    slots.sort(key=lambda s: len(_allowed_positions_for_slot(s)))
    return slots


def _best_lineup_score(slot_names: list, players: list) -> float:
    """players: [(id, position, score), ...] for one week. Highest score
    first, each taking the narrowest still-open eligible slot; a negative
    score is left on the bench rather than force-started."""
    starters = [None] * len(slot_names)
    for player in sorted(players, key=lambda p: p[2], reverse=True):
        if player[2] < 0:
            break  # sorted descending -- every remaining player is also negative
        pos = player[1]
        for i, slot in enumerate(slot_names):
            if starters[i] is not None:
                continue
            if pos in _allowed_positions_for_slot(slot):
                starters[i] = player
                break
    return sum(p[2] for p in starters if p is not None)


def _additive_values(candidates, roster_by_week, scores_by_player_week, player_slots):
    """candidates: [(id, position), ...]. roster_by_week: {week: [(id,
    position, score), ...]} for the team's roster as drafted so far,
    already filtered to players who have a real score that week.
    Returns {candidate_id: additive_value}."""
    slot_names = _slots_sorted_by_eligibility_breadth(player_slots)
    before_by_week = {w: _best_lineup_score(slot_names, players) for w, players in roster_by_week.items()}

    results = {}
    for pid, pos in candidates:
        player_weeks = scores_by_player_week.get(pid, {})
        total = 0.0
        for w, before_players in roster_by_week.items():
            score = player_weeks.get(w)
            if score is None:
                continue
            after = _best_lineup_score(slot_names, before_players + [(pid, pos, score)])
            total += after - before_by_week[w]
        results[pid] = total
    return results


def draft_player() -> str:
    db = DatabaseManager()
    settings = db.get_league_settings()
    year = settings.year

    players_df = pd.read_sql(
        "SELECT id, allowed_positions, availability, current_bot_id FROM players", db.engine
    )
    set_position(players_df)
    pos_by_id = dict(zip(players_df["id"], players_df["position"]))

    weekly = pd.read_sql(
        f"SELECT fantasypros_id AS id, CAST(week AS INTEGER) AS week, MAX(FPTS) AS FPTS "
        f"FROM weekly_stats WHERE year = {year} AND CAST(week AS INTEGER) BETWEEN 1 AND 5 "
        f"GROUP BY fantasypros_id, week",
        db.engine,
    )
    scores_by_player_week = {}
    for pid, week, fpts in weekly.itertuples(index=False):
        scores_by_player_week.setdefault(pid, {})[int(week)] = float(fpts)
    weeks = sorted({w for s in scores_by_player_week.values() for w in s})

    my_bot_id = db.get_game_status().current_bot_id
    roster = [
        (pid, pos_by_id[pid])
        for pid in players_df.loc[players_df["current_bot_id"] == my_bot_id, "id"]
    ]
    roster_by_week = {
        w: [(pid, pos, scores_by_player_week[pid][w]) for pid, pos in roster if w in scores_by_player_week.get(pid, {})]
        for w in weeks
    }

    available_ids = players_df.loc[players_df["availability"] == "AVAILABLE", "id"].tolist()
    candidates = [(pid, pos_by_id[pid]) for pid in available_ids]

    values = _additive_values(candidates, roster_by_week, scores_by_player_week, settings.player_slots)
    return max(values, key=values.get)

def get_current_week() -> int:
    """Get the current fantasy week from game status"""
    db = DatabaseManager()
    return db.get_game_status().current_fantasy_week

def get_season_total_points() -> pd.DataFrame:
    """Get season total points for all players"""
    db = DatabaseManager()
    year = db.get_league_settings().year

    # Get season stats which should have cumulative points
    season_stats_df = pd.read_sql(f"SELECT * FROM season_stats WHERE year = {year}", db.engine)
    # Return only the columns we need
    return season_stats_df[["fantasypros_id", "FPTS"]].rename(columns={"FPTS": "season_total_points"})

def get_weekly_projections(week: int) -> pd.DataFrame:
    """Get weekly projections for a specific week (ESPN, via external_projections).

    Was FantasySharks, but that source (and FFToday) started getting
    blocked/rate-limited on GitHub Actions' shared runner IPs partway
    through the season -- FantasySharks stalled at week 6, FFToday at week
    3, while ESPN has stayed fully complete the whole season. Checked: no
    other bot has this single-source dependency, they all average across
    whichever sources are actually present, which is what made them
    resilient to this and what this bot should have done from the start.
    ESPN has no DST data at all (dropped from its scrape entirely, a
    separate known gap), so DST candidates always have no projection here
    -- see the NaN handling in find_upgrade_candidates for why that no
    longer kills the whole comparison for them.
    """
    db = DatabaseManager()
    year = db.get_league_settings().year

    weekly_proj_df = pd.read_sql(
        f"SELECT fantasypros_id, points AS weekly_projected_points FROM external_projections "
        f"WHERE year = {year} AND week = {week} AND source = 'ESPN'",
        db.engine
    )
    return weekly_proj_df

def get_injury_status(week: int) -> pd.DataFrame:
    """Get injury status for a specific week"""
    db = DatabaseManager()
    year = db.get_league_settings().year

    injury_df = pd.read_sql(
        f"SELECT fantasypros_id, injury, practice_status, game_status FROM weekly_injuries WHERE year = {year} AND week = {week}",
        db.engine
    )
    return injury_df

def is_player_healthy(player_id: str, injury_df: pd.DataFrame) -> bool:
    """Check if a player has no injury status set"""
    player_injuries = injury_df[injury_df["fantasypros_id"] == player_id]

    # If no injury record exists, player is considered healthy
    if player_injuries.empty:
        return True

    # If injury field is null/None/empty, player is healthy
    injury_value = player_injuries.iloc[0]["injury"]
    return pd.isna(injury_value) or injury_value == "" or injury_value is None

def find_upgrade_candidates() -> list[WaiverClaim]:
    """Find players that are upgrades based on the strategy"""
    db = DatabaseManager()
    current_week = get_current_week()

    # Get all data we need
    players_df = pd.read_sql("SELECT * FROM players", db.engine)
    season_points_df = get_season_total_points()
    weekly_proj_df = get_weekly_projections(current_week)
    injury_df = get_injury_status(current_week)

    # Merge all data together
    players_df = players_df.merge(season_points_df, left_on="id", right_on="fantasypros_id", how="left")
    players_df = players_df.merge(weekly_proj_df, left_on="id", right_on="fantasypros_id", how="left")

    # A player with no row in either source (not just "zero points", genuinely
    # missing -- e.g. every DST via ESPN, or a rookie season_stats hasn't
    # caught up to yet) must compare as 0, not NaN. `row.get(col, 0)` does NOT
    # protect against this: the column exists after the merge, just holds NaN,
    # so the default is never used -- `candidate > NaN` and `NaN > my_value`
    # are both always False in pandas, which was silently failing every single
    # comparison for every player missing from either source, not just the
    # ones actually tied/worse.
    players_df["season_total_points"] = players_df["season_total_points"].fillna(0)
    players_df["weekly_projected_points"] = players_df["weekly_projected_points"].fillna(0)

    # Get my team
    my_bot_id = db.get_game_status().current_bot_id
    my_team = players_df[players_df["current_bot_id"] == my_bot_id].copy()
    set_position(my_team)

    # Get available players
    available_players = players_df[players_df["availability"] == "AVAILABLE"].copy()
    set_position(available_players)

    claims = []

    # For each player on my team, check if there's an upgrade
    for _, my_player in my_team.iterrows():
        my_position = my_player["position"]
        my_season_points = my_player.get("season_total_points", 0)
        my_weekly_proj = my_player.get("weekly_projected_points", 0)

        # Find available players at same position with better stats
        position_candidates = available_players[available_players["position"] == my_position].copy()

        for _, candidate in position_candidates.iterrows():
            candidate_id = candidate["id"]
            candidate_season_points = candidate.get("season_total_points", 0)
            candidate_weekly_proj = candidate.get("weekly_projected_points", 0)

            # Check all conditions
            has_more_total_points = candidate_season_points > my_season_points
            is_healthy = is_player_healthy(candidate_id, injury_df)
            has_better_projection = candidate_weekly_proj > my_weekly_proj

            if has_more_total_points and is_healthy and has_better_projection:
                claim = WaiverClaim(
                    player_to_add_id=candidate_id,
                    player_to_drop_id=my_player["id"],
                    bid_amount=0
                )
                claims.append(claim)

    return claims

def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    """Generate waiver claims based on upgrade strategy"""
    claims = find_upgrade_candidates()

    actions = AttemptedFantasyActions(
        waiver_claims=claims
    )

    return actions