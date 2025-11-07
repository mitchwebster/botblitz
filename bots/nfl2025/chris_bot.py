from blitz_env.models import DatabaseManager
from blitz_env import AttemptedFantasyActions, WaiverClaim
import pandas as pd
import numpy as np
import json

def get_projections_df():
    db = DatabaseManager()
    year = db.get_league_settings().year
    
    expected_draft_count = {
      "RB": 52,
      "WR": 60,
      "QB": 32,
      "TE": 8,
      "K": 13,
      "DST": 13
    }
    projections_df = pd.read_sql(f"SELECT * FROM preseason_projections where year == {year}", db.engine)
    # projections_df['FPTS'] = pd.to_numeric(projections_df['FPTS'], errors='coerce')
    
    def avg_top_by_position(group: pd.DataFrame) -> float:
        n = expected_draft_count.get(group.name.upper(), 0)
        if n <= 0:
            return np.nan
        # nlargest handles the case where the group has < n rows
        return group.nlargest(n, 'FPTS')['FPTS'].mean()
    
    avg_top_fpts = (
        projections_df
        .groupby('position', group_keys=False)
        .apply(avg_top_by_position)
        .dropna()
    )
    
    # Average of expected points for drafted players
    avg_map = avg_top_fpts.to_dict()
    # print(avg_map)
    
    def get_value(row):
        return row["FPTS"] - avg_map[row["position"]]
    
    projections_df["Value"] = projections_df.apply(get_value, axis=1)
    return projections_df

def get_my_team():
    db = DatabaseManager()
    players_df = pd.read_sql("SELECT * FROM players ", db.engine)
    my_bot_id = db.get_game_status().current_bot_id
    return players_df[players_df["current_bot_id"] == my_bot_id]

def get_players_df_with_value():
    db = DatabaseManager()
    players_df = pd.read_sql("SELECT * FROM players", db.engine)
    
    # subtract predicted points from average drafted
    projections_df = get_projections_df()
    kept_columns = ["fantasypros_id", "FPTS", "Value"]
    df = players_df.merge(projections_df[kept_columns], left_on="id", right_on="fantasypros_id")



    position_count_map = get_position_counts_map()
    set_position(df)
    df["RosterValue"] = df.apply(lambda row: value_given_drafted_players(row, position_count_map), axis=1)
    return df

import math

def get_my_team():
    db = DatabaseManager()
    players_df = pd.read_sql("SELECT * FROM players ", db.engine)
    my_bot_id = db.get_game_status().current_bot_id
    return players_df[players_df["current_bot_id"] == my_bot_id]

def set_position(df):
    df["position"] = df["allowed_positions"].apply(lambda x: json.loads(x)[0])
    
def get_position_counts_map():
    my_team = get_my_team()
    set_position(my_team)
    data = my_team.groupby("position")["position"].count().to_dict()
    # print(data)
    return(data)

position_to_count_to_penalty = {
    'RB': {
        1: 10,
        2: 20,
        3: 30,
        4: 50,
        5: 100,
        6: 1000,
        7: 1000,
        8: 1000,
        9: 1000,
    },
    'WR': {
        1: 10,
        2: 20,
        3: 30,
        4: 50,
        5: 100,
        6: 1000,
        7: 1000,
        8: 1000,
        9: 1000,
    },
    'TE': {
        1: 20,
        2: 50,
        3: 1000,
        4: 1000,
        5: 1000,
        6: 1000,
    },
    'QB': {
        1: 10,
        2: 50,
        3: 1000,
        4: 1000,
        5: 1000,
        6: 1000
    },
    'K': {
        0: 50,
        1: 1000,
        2: 1000,
        3: 1000,
        4: 1000,
        5: 1000
    },
    'DST': {
        0: 50,
        1: 1000,
        2: 1000,
        3: 1000,
        4: 1000,
        5: 1000,
    },
}

def value_given_drafted_players(row, count_map):
    drafted_count = count_map.get(row["position"], 0)
    penalty = position_to_count_to_penalty[row["position"]].get(drafted_count, 0)
    return row["Value"] - penalty


def draft_player() -> str:
    df = get_players_df_with_value()
    return df[df["availability"] == "AVAILABLE"].sort_values(by="RosterValue", ascending=False).iloc[0]["id"]

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
    """Get weekly projections for a specific week"""
    db = DatabaseManager()
    year = db.get_league_settings().year

    weekly_proj_df = pd.read_sql(
        f"SELECT fantasypros_id, FPTS as weekly_projected_points FROM weekly_projections WHERE year = {year} AND week = {week}",
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