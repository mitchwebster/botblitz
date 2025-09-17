from blitz_env import AttemptedFantasyActions, WaiverClaim
from blitz_env.models import DatabaseManager
import pandas as pd
import json

def get_current_round(db):
    settings_df = pd.read_sql("SELECT * FROM league_settings", db.engine) # get leagueSettings
    leagueSettings = settings_df.iloc[0]
    
    status_df = pd.read_sql("SELECT * FROM game_statuses", db.engine) # get game status
    gameSettings = status_df.iloc[0]

    zero_based_round = (gameSettings["current_draft_pick"] - 1) // leagueSettings["num_teams"]
    return zero_based_round + 1

def get_pick_details(db):
    settings_df = pd.read_sql("SELECT * FROM league_settings", db.engine) # get leagueSettings
    leagueSettings = settings_df.iloc[0]
    
    status_df = pd.read_sql("SELECT * FROM game_statuses", db.engine) # get game status
    gameSettings = status_df.iloc[0]

    return gameSettings["current_draft_pick"], leagueSettings["num_teams"]

def get_total_rounds(db) -> int:
    settings_df = pd.read_sql("SELECT * FROM league_settings", db.engine) # get game status
    return settings_df.iloc[0]["total_rounds"]

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

def convert_team_to_position_map(curTeam):
    positionCounts = {}
    for playerName, position in curTeam.items():
        if position in positionCounts:
            positionCounts[position] += 1
        else:
            positionCounts[position] = 1
    return positionCounts

def is_player_allowed(row, current_round, total_rounds):
    team_block_map = { 
            "CHI": "",
            "CAR": "",
            "NYJ": "",
            "NO": "",
            "IND": "",
            "CLE": "",
    }

    if current_round == total_rounds - 1:
        return any(row["allowed_positions_set"] & {"DST"}) and row["professional_team"] not in team_block_map

    if current_round == total_rounds:
        return any(row["allowed_positions_set"] & {"K"}) and row["professional_team"] not in team_block_map

    if current_round < 9 and any(row["allowed_positions_set"] & {"TE"}):
        return False
    
    allowlisted_players = {"Alvin Kamara", "Jonathan Taylor", "David Njoku"}
    if row["full_name"] in allowlisted_players:
        return True

    if row["professional_team"] in team_block_map:
        return False

    if row["professional_team"] == "JAC":
        return any(row["allowed_positions_set"] & {"RB"})

    return True

def get_eligible_available_players(db, current_round, total_rounds):
    df = pd.read_sql("SELECT * FROM players where availability = 'AVAILABLE'", db.engine)

    # expand the allowed_position json strings into a set
    df["allowed_positions_set"] = df["allowed_positions"].apply(
        lambda x: set(json.loads(x)) if x else set()
    )

    df = df[df.apply(lambda row: bool(is_player_allowed(row, current_round, total_rounds)), axis=1)]
    df = df.sort_values(by="rank", ascending=True)
    return df

def is_crazy_value(rank, current_pick, picks_per_round):
    return current_pick >= (rank + 2 * picks_per_round)

def adjust_rankings_for_qbs(db, players, current_round, current_team, current_pick, picks_per_round):
    position_counts = convert_team_to_position_map(current_team)
    
    tier1Players = {
        "Josh Allen",
        "Lamar Jackson",
        "Jayden Daniels",
        "Joe Burrow",
        "Jalen Hurts",
        "Patrick Mahomes"
    }

    tier2Players = {
        "Baker Mayfield",
        "Bo Nix",
        "Kyler Murray"
    }

    if current_round <= 2:
        players["rank"] = players.apply(lambda row: row["rank"] - 1000 if row["full_name"] in tier1Players else row["rank"], axis=1)
        players = players.sort_values(by="rank", ascending=True)
        return players
    elif current_round >= 4 and ("QB" in position_counts and position_counts["QB"] < 2) and ("RB" in position_counts and position_counts["RB"] >= 2) and ("WR" in position_counts and position_counts["WR"] >= 2):
        players["rank"] = players.apply(lambda row: row["rank"] - 1000 if (row["full_name"] in tier1Players or row["full_name"] in tier2Players) else row["rank"], axis=1)
        players = players.sort_values(by="rank", ascending=True)
        return players
    elif ("QB" in position_counts and position_counts["QB"] >= 2):
        penalty = 1000 if "RB" not in position_counts or position_counts["RB"] < 3 or "WR" not in position_counts or position_counts["WR"] < 3 else 0

        # TODO: need to add logic for seeing if there are any folks that are especially good relative to ADP
        
        players["rank"] = players.apply(lambda row: row["rank"] + penalty if any(row["allowed_positions_set"] & {"QB"}) and is_crazy_value(row["rank"], current_pick, picks_per_round) else row["rank"], axis=1)
        players = players.sort_values(by="rank", ascending=True)
        return players
    else:
        return players

def adjust_rankings_for_rbs_and_wrs(db, players, current_round, current_team):
    position_counts = convert_team_to_position_map(current_team)

    if current_round <= 7:
        if "WR" in position_counts and position_counts["WR"] >= 3:
            players["rank"] = players.apply(lambda row: row["rank"] + 1000 if any(row["allowed_positions_set"] & {"WR"}) else row["rank"], axis=1)
        elif "RB" in position_counts and position_counts["RB"] >= 3:
            players["rank"] = players.apply(lambda row: row["rank"] + 1000 if any(row["allowed_positions_set"] & {"RB"}) else row["rank"], axis=1)
    
    # TODO: ideally look for RBs with high targets for PPR
    
    return players


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
        current_round = get_current_round(db)
        total_rounds = get_total_rounds(db)
        my_team = get_my_team(db)
        current_pick, picks_per_round = get_pick_details(db)

        players = get_eligible_available_players(db, current_round, total_rounds)
        players = adjust_rankings_for_qbs(db, players, current_round, my_team, current_pick, picks_per_round)
        players = adjust_rankings_for_rbs_and_wrs(db, players, current_round, my_team)

        if not players.empty:
            best_player = players.iloc[0]
            print(best_player["full_name"])
            return best_player["id"]  # No need for conditional on the Series itself
        else:
            return ""  # No eligible player
    finally:
        db.close()

def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    claims = [ 
        WaiverClaim(
            player_to_add_id="18232",
            player_to_drop_id="23107",
            bid_amount=30
        ),
        WaiverClaim(
            player_to_add_id="25391",
            player_to_drop_id="22718",
            bid_amount=15
        ),
        WaiverClaim(
            player_to_add_id="27297",
            player_to_drop_id="27165",
            bid_amount=10
        )
    ]

    actions = AttemptedFantasyActions(
        waiver_claims=claims
    )

    return actions