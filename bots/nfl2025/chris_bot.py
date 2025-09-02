from blitz_env.models import DatabaseManager
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