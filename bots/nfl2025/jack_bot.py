from blitz_env import AttemptedFantasyActions, WaiverClaim
from blitz_env.models import DatabaseManager
import pandas as pd
import json, os

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

def load_available_players(db):
    df = pd.read_sql("SELECT * FROM players where availability = 'AVAILABLE'", db.engine)
    return df

def draft_player() -> str:
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

        preferred_players = ["Ja'Marr Chase", "Bijan Robinson", "Justin Jefferson", "Saquon Barkley", "Jahmyr Gibbs", "Christian McCaffrey", "CeeDee Lamb", "Malik Nabers", "Puka Nacua", "Ashton Jeanty", "Amon-Ra St. Brown", "De'Von Achane", "Josh Allen", "Lamar Jackson", "Jayden Daniels", "Jalen Hurts", "Joe Burrow", "Garrett Wilson", "Marvin Harrison Jr.", "DK Metcalf", "Xavier Worthy", "Mike Evans", "DJ Moore", "Zay Flowers", "Courtland Sutton", "Calvin Ridley", "DeVonta Smith", "Jaylen Waddle", "Jerry Jeudy", "Jameson Williams", "Rashee Rice", "George Pickens", "Rome Odunze", "Travis Hunter", "Jakobi Meyers", "Matthew Golden", "Emeka Egbuka", "Chris Olave", "Ricky Pearsall", "Michael Pittman Jr.", "Stefon Diggs", "Cooper Kupp", "Jordan Addison", "Jauan Jennings", "Deebo Samuel Sr.", "Khalil Shakir", "Keon Coleman", "Chris Godwin", "Josh Downs", "Brock Bowers"]
        
        # Check if any preferred player is available
        preferred_available = filtered_df_sorted[
            filtered_df_sorted["full_name"].isin(preferred_players)
        ]
        if not preferred_available.empty:
            best_pref = preferred_available.iloc[0]
            print(f"Drafting preferred player: {best_pref['full_name']}")
            return best_pref["id"]
        
        if not filtered_df_sorted.empty:
            best_player = filtered_df_sorted.iloc[0]
            print(best_player["full_name"])
            return best_player["id"]  # No need for conditional on the Series itself
        else:
            return ""  # No eligible player
    finally:
        db.close()

def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    db = DatabaseManager()

    try:
        bot_id = 8
        gs = pd.read_sql("SELECT * FROM game_statuses", db.engine)
        current_week = gs.iloc[0]["current_fantasy_week"]

        # List my players on bye this week
        my_team_df = pd.read_sql(f"SELECT * FROM players WHERE current_bot_id = {bot_id} order by rank", db.engine)
        bye_players_df = my_team_df[my_team_df["player_bye_week"] == current_week]

        # For each player on bye above tier threshold, try to replace with best available player at that position
        claims = []
        bid = 1
        tier_threshold = 3 # Keep this around 8 so that you don't drop good players
        for _, bye_player in bye_players_df.iterrows():
            position = bye_player["allowed_positions"].split(",")[0]
            print(f"Finding replacements for {bye_player['full_name']} {position}")
            replacement = pd.read_sql(f"SELECT * FROM players WHERE availability = 'AVAILABLE' AND allowed_positions = '{position}' AND player_bye_week != {current_week} ORDER BY rank LIMIT 1", db.engine)
            if bye_player["tier"] < tier_threshold:
                print(f"Keep {bye_player['full_name']}")
                # TODO drop worst player with same position
            else:
                # Add WaiverClaim to claims array
                if not replacement.empty:
                    add = replacement.iloc[0]["id"]
                    drop = bye_player["id"]
                    print(f"Claiming {replacement.iloc[0]['full_name']} to replace {bye_player['full_name']}")
                    claims.append(
                        WaiverClaim(
                            player_to_add_id=add,
                            player_to_drop_id=drop,
                            bid_amount=bid
                        )
                    )
                    print(claims)
        actions = AttemptedFantasyActions(
            waiver_claims=claims
        )
        return actions
    
    finally:
        db.close()