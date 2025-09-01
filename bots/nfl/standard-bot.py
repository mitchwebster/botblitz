from blitz_env import Player, GameState, AddDropSelection
from blitz_env.models import DatabaseManager, Player
import pandas as pd
import json

def get_positions_to_fill(db):
    df = pd.read_sql("SELECT * FROM league_settings", db.engine)
    return json.loads(df.iloc[0]["player_slots"])

def get_my_team(db):
    df = pd.read_sql("SELECT * FROM game_statuses", db.engine) # get game status
    draft_pick = df.iloc[0]["current_draft_pick"]
    print(f"Current pick is {draft_pick}")
    
    bot_id = df.iloc[0]["current_bot_id"]
    queryStr = f"SELECT * FROM players where current_bot_id = '{bot_id}'"
    my_team = pd.read_sql(queryStr, db.engine) 

    player_positions_map = {
        row["full_name"]: (json.loads(row["allowed_positions"])[0] if row["allowed_positions"] else None)
        for _, row in my_team.iterrows()
    }

    return player_positions_map

def adjust_available_positions(remaining_positions_to_fill):
    if "FLEX" in remaining_positions_to_fill:
        remaining_positions_to_fill |= {"RB", "WR", "TE"}

    if "SUPERFLEX" in remaining_positions_to_fill:
        remaining_positions_to_fill |= {"QB", "RB", "WR", "TE"}

    if "BENCH" in remaining_positions_to_fill:
        remaining_positions_to_fill |= {"QB", "RB", "WR", "TE", "K", "DST"}
    
    special_positions = {"FLEX", "SUPERFLEX", "BENCH"}
    remaining_positions_to_fill -= special_positions
    return remaining_positions_to_fill

def draft_player(game_state: GameState) -> str:
    """
    Selects a player to draft based on the highest rank.

    Args:
        players (List[Player]): A list of Player objects.

    Returns:
        str: The id of the drafted player.
    """
    db = DatabaseManager()
    try:
        positions_to_fill = get_positions_to_fill(db)
        my_team = get_my_team(db)

        for player_name, position in my_team.items():
            if position in positions_to_fill and positions_to_fill[position] > 0:
                # fill explicit positions first
                positions_to_fill[position] = positions_to_fill[position] - 1
            else:
                # if the position is not explicitly in the map, then begin decrementing the FLEX, SUPERFLEX, and Bench slots
                # start with most specific
                if position in ["RB", "WR", "TE"] and positions_to_fill["FLEX"] > 0:
                    positions_to_fill["FLEX"] = positions_to_fill["FLEX"] - 1
                elif position in ["QB", "RB", "WR", "TE"] and positions_to_fill["SUPERFLEX"] > 0:
                    positions_to_fill["SUPERFLEX"] = positions_to_fill["SUPERFLEX"] - 1
                else:
                    positions_to_fill["BENCH"] = positions_to_fill["BENCH"] - 1

        remaining_positions_to_fill = {pos for pos, count in positions_to_fill.items() if count >= 1}
        position_filter = adjust_available_positions(remaining_positions_to_fill)

        # load all of the available players into a pandas dataframe
        df = pd.read_sql("SELECT * FROM players where availability = 'AVAILABLE'", db.engine)

        # expand the allowed_position json strings into a set
        df["allowed_positions_set"] = df["allowed_positions"].apply(
            lambda x: set(json.loads(x)) if x else set()
        )

        # apply the filtered posiions
        filtered_df = df[df["allowed_positions_set"].apply(lambda s: bool(s & position_filter))]
        filtered_df_sorted = filtered_df.sort_values(by="rank", ascending=True)

        if not filtered_df_sorted.empty:
            best_player = filtered_df_sorted.iloc[0]
            print(best_player["full_name"])
            return best_player["id"]  # No need for conditional on the Series itself
        else:
            return ""  # No eligible player
    finally:
        db.close()

def propose_add_drop(game_state: GameState) -> AddDropSelection:
    """
    Selects a player to draft based on the highest rank.

    Args:
        players (List[Player]): A list of Player objects.

    Returns:
        str: The id of the drafted player.
    """
    return AddDropSelection(
        player_to_add_id="",
        player_to_drop_id=""
    )