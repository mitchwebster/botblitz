# from blitz_env.simulate_draft_sqlite import simulate_draft, visualize_draft_board
from blitz_env import WaiverClaim, AttemptedFantasyActions
from blitz_env.models import DatabaseManager
import pandas as pd
import json

def get_player_history(db, player_id):
    history_df = pd.read_sql(f"SELECT * FROM season_stats where fantasypros_id = '{player_id}'", db.engine)
    print(len(history_df))

def get_current_round(db):
    settings_df = pd.read_sql("SELECT * FROM league_settings", db.engine) # get leagueSettings
    leagueSettings = settings_df.iloc[0]
    
    status_df = pd.read_sql("SELECT * FROM game_statuses", db.engine) # get game status
    gameSettings = status_df.iloc[0]

    zero_based_round = (gameSettings["current_draft_pick"] - 1) // leagueSettings["num_teams"]
    return zero_based_round + 1

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

def get_allowed_positions_to_draft(curRound, totalRounds, curTeam):
    position_set = set()

    if curRound == totalRounds:
        position_set = set(["K"])
    elif curRound + 1 == totalRounds:
        position_set = set(["DST"])
    elif curRound < 9:
        position_set = set(["QB", "WR", "RB"])
    else:
        position_set = set(["QB", "WR", "RB", "TE"])

    positionCounts = convert_team_to_position_map(curTeam)

    # only allow 2 QB
    if "QB" in positionCounts and positionCounts["QB"] >= 2:
        position_set -= {"QB"}
    
    # only allow 1 TE
    if "TE" in positionCounts and positionCounts["TE"] >= 1:
        position_set -= {"TE"}

    return position_set 

def get_top_50_ranked_available_players(db, positions_to_draft):
    df = pd.read_sql("SELECT * FROM players where availability = 'AVAILABLE'", db.engine)

    # expand the allowed_position json strings into a set
    df["allowed_positions_set"] = df["allowed_positions"].apply(
        lambda x: set(json.loads(x)) if x else set()
    )

    filtered_df = df[df["allowed_positions_set"].apply(lambda s: bool(s & positions_to_draft))]
    df = filtered_df.sort_values(by="rank", ascending=True)
    return df.head(50)

def score_potential_player(player_row, db, current_round, my_team):
    current_rank = player_row["rank"]

    # TODO: need to consider position in the snake draft in early rounds
    
    # Draft QBs earlier given superflex
    if any(player_row["allowed_positions_set"] & {"QB"}):
        positionCounts = convert_team_to_position_map(my_team)

        # currentQBCount = positionCounts["QB"] if "QB" in positionCounts else 0

        rank_reduction = max(30 - 5*player_row["position_tier"], 0)
        current_rank -= rank_reduction

    if any(player_row["allowed_positions_set"] & {"RB"}):
        positionCounts = convert_team_to_position_map(my_team)
        rankIncrease = 10 if "RB" in positionCounts and positionCounts["RB"] >= 2 else 0
        current_rank += rankIncrease

    if any(player_row["allowed_positions_set"] & {"WR"}):
        positionCounts = convert_team_to_position_map(my_team)
        rankIncrease = 10 if "WR" in positionCounts and positionCounts["WR"] >= 2 else 0
        current_rank += rankIncrease

    if any(player_row["allowed_positions_set"] & {"TE"}):
        rankIncrease = (player_row["position_tier"] - 1) * 5 # TE picks need to be excellent 
        current_rank += rankIncrease

    return current_rank
    
def draft_player() -> str:
    db = DatabaseManager()
    try:
        current_round = get_current_round(db)
        total_rounds = get_total_rounds(db)
        my_team = get_my_team(db)
        positions_to_draft = get_allowed_positions_to_draft(current_round, total_rounds, my_team)
        df = get_top_50_ranked_available_players(db, positions_to_draft)
        df["score"] = df.apply(lambda row: score_potential_player(row, db, current_round, my_team), axis=1)
        df = df.sort_values(by="score", ascending=True)

        if not df.empty:
            best_player = df.iloc[0]
            print(best_player["full_name"])
            return best_player["id"]  # No need for conditional on the Series itself
        else:
            return ""  # No eligible player
    finally:
        db.close()

def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    claims = [ 
        WaiverClaim(
            player_to_add_id="",
            player_to_drop_id="",
            bid_amount=0
        )
    ]

    actions = AttemptedFantasyActions(
        waiver_claims=claims
    )

    return actions