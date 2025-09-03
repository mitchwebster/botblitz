from blitz_env.simulate_draft_sqlite import simulate_draft, visualize_draft_board
from blitz_env.models import DatabaseManager, Player, Bot, GameStatus
from blitz_env import GameState, AddDropSelection
import pandas as pd
import json

QB_ROUNDS = [5, 8, 10, 20]

REAL_POSITIONS = ["QB", "WR", "RB", "TE", "K", "DST"]

# Don't worry about it
RANDOM_NUMBERS = {
    1: ["17298","17233","22902","19275","19196",],
    2: ["19788","16413","19202","19236","17237","22910","23133",],
    3: ["22968","18600","25409","15600","19799","17240","16393","25989",
    ],
    4: [ "20130", "23180", "19797", "23000", "19781", "23163", "23046", "18635", "23084",
    ],
    5: ["18218","15501","26122","22955","23136","23070","19780","19246","22936","15514","19211","25324","23072","23071","25981",
    ],
    6: ["23018","18269","15802","16499","19217","12119","12123","23064","18466","23059","17265","16421",
    ],
    7: ["19198","17253","22900","22958","25417","18219","25395","23021","19222","11687","22982","22973","23019","19790","19792","9451","23065","17258","22963","16447","22916","24755","23677",
    ],
    8: ["18705","17236","20111","19210","18290","27142","22978","26034","24333","18239","16673","19201","25411","23062","13981","23113","11594","26214","9001","22980","18598","24209","16399","18232","16411",
    ],
    9: ["17269","19231","27165","23748","18244","23107","24706","23152","19278","19590","26024","26434","22969","22739","25298","27316","16406","16433","19245","23020","22718","16483","11180","19810","22908","22726","26019","25247",
    ],
    10: ["27166","26006","17268","17270","24687","24346","23891","24347","19252","19302","23080","25388","27016","25287","19794","20094","17246","15561","23081","26191","16420","22921","25391","27297","24344","22985","11616","27077","9300","25323","24172","27102","18226","11689","23794","25304","16460","13429","23096",
    ],
    11: [ "19647", "22845", "26136", "27487", "26215", "25345", "23982", "26403", "23123", "25984", "11606", "23013", "25322", "27520", "23181", "17272", "20163", "23160", "23791", "23781", "26392", "25333", "26005", "18706", "22930", "25987", "24357",
    ],
    12: ["26068","13029","26475","26075","23901","8090","8230","8240","8020","8170","8030"
    ],
    13: [
        "8000", # Our secret weapon
    ],
}

# just having fun
STOPS = {
    1: [1,1, 1, 0, 0,0],
    2: [1,1, 1, 0, 0,0],
    3: [2,1, 1, 0, 0,0],
    4: [2,1, 1, 0, 0,0],
    5: [2,3, 3, 1, 0,0],
    6: [2,3, 3, 1, 0,0],
    7: [2,4, 4, 1, 0,0],
    8: [3,4, 4, 1, 0,0],
    9: [3,4, 4, 1, 0,0],
    10:[3,4, 4, 1, 1,0],
    11:[3,4, 4, 1, 1,0],
    12:[3,4, 4, 1, 1,1],
}


POSITION_MINIMUMS = {
    "QB": 3,
    "WR": 3,
    "RB": 3
}

def add_an_extra_column_to_dataframe(df, id_column, numbers):
    player_to_data = {}
    for data, players in numbers.items():
        for player in players:
            player_to_data[player] = data

    max_number = len(numbers.keys())
    bottom_number = max_number + 1
    
    df['extra_column'] = df[id_column].map(player_to_data).fillna(bottom_number)
    
    return df

def get_positions_to_fill(db):
    df = pd.read_sql("SELECT * FROM league_settings", db.engine)
    return json.loads(df.iloc[0]["player_slots"])

def get_my_team(db):
    my_team = get_my_team_as_df(db)
    player_positions_map = {
        row["full_name"]: (json.loads(row["allowed_positions"])[0] if row["allowed_positions"] else None)
        for _, row in my_team.iterrows()
    }

    return player_positions_map

def get_my_team_as_df(db):
    df = pd.read_sql("SELECT * FROM game_statuses", db.engine)
    bot_id = df.iloc[0]["current_bot_id"]
    queryStr = f"SELECT * FROM players where current_bot_id = '{bot_id}'"
    return pd.read_sql(queryStr, db.engine) 

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

def apply_team_limits(filtered_df, my_team_df, current_round, total_rounds):
    team_counts = my_team_df['professional_team'].value_counts().to_dict()
    bye_counts = my_team_df['player_bye_week'].value_counts().to_dict()
    
    receiver_teams = set()
    for _, row in my_team_df.iterrows():
        positions = json.loads(row['allowed_positions']) if row['allowed_positions'] else []
        if any(pos in ['WR', 'TE'] for pos in positions):
            receiver_teams.add(row['professional_team'])
    
    team_limit = 2 if current_round <= total_rounds // 2 else 3
    bye_limit = 3 if current_round <= 8 else 4
    
    def is_valid(row):
        team = row['professional_team']
        player_bye_week = row['player_bye_week']
        positions = json.loads(row['allowed_positions']) if row['allowed_positions'] else []
        
        if team_counts.get(team, 0) >= team_limit:
            return False
        
        if bye_counts.get(player_bye_week, 0) >= bye_limit:
            return False
        
        if any(pos in ['WR', 'TE'] for pos in positions) and team in receiver_teams:
            return False
            
        return True
    
    return filtered_df[filtered_df.apply(is_valid, axis=1)]

def get_draft_round(db):
    status = db.session.query(GameStatus).first()
    bots = db.session.query(Bot).all()
    current_round = (status.current_draft_pick - 1) // len(bots) + 1
    return current_round

def get_stops(round_num):
    return dict(zip(REAL_POSITIONS, STOPS[round_num]))

def apply_position_limits(positions_to_fill, my_team, current_round):
    # Count current players by position
    current_counts = {}
    position_limits = get_stops(current_round)
    limited_positions = set([position for position, limit in position_limits.items() if limit == 0])
    for player_name, position in my_team.items():
        if position in position_limits:
            current_counts[position] = current_counts.get(position, 0) + 1
            if current_counts[position] >= position_limits[position]:
                limited_positions.add(position)
    return positions_to_fill - limited_positions

def detect_inverse_square_opportunity(my_team_df, available_players_df):
    my_qbs = my_team_df[my_team_df["allowed_positions"].apply(lambda x: "QB" in json.loads(x) if x else False)]
    inverse_squares = []
    for qb in my_qbs.itertuples():
        potential_targets = available_players_df[
            available_players_df["professional_team"] == qb.professional_team
        ]
        potential_targets = potential_targets[
            potential_targets["allowed_positions"].apply(lambda x: bool(set(json.loads(x)) & {"WR", "TE"}) if x else False)
        ]
        if not potential_targets.empty:
            top_target = potential_targets.sort_values(by="rank").iloc[0]
            inverse_squares.append((top_target))
    return inverse_squares

def detect_qb_inverse_square_opportunity(my_team_df, available_players_df):
    my_wrs_tes = my_team_df[my_team_df["allowed_positions"].apply(lambda x: bool(set(json.loads(x)) & {"WR", "TE"}) if x else False)]
    inverse_squares = []
    for wr_te in my_wrs_tes.itertuples():        
        potential_qbs = available_players_df[
            available_players_df["professional_team"] == wr_te.professional_team
        ]
        potential_qbs = potential_qbs[
            potential_qbs["allowed_positions"].apply(lambda x: "QB" in json.loads(x) if x else False)
        ]
        if not potential_qbs.empty:
            top_qb = potential_qbs.sort_values(by="rank").iloc[0]
            inverse_squares.append((top_qb))
    return inverse_squares
    

def detect_partying_opportunity(my_team_df, available_players_df):
    my_rbs = my_team_df[my_team_df["allowed_positions"].apply(lambda x: "RB" in json.loads(x) if x else False)]
    partys = []
    for rb in my_rbs.itertuples():
        rb_id = rb.id
        potential_partys = available_players_df[
            available_players_df["professional_team"] == rb.professional_team
        ]
        potential_partys = potential_partys[
            potential_partys["allowed_positions"].apply(lambda x: "RB" in json.loads(x) if x else False)
        ]
        potential_partys = potential_partys[potential_partys["id"] != rb_id] if not potential_partys.empty else potential_partys
        if not potential_partys.empty:
            top_party = potential_partys.sort_values(by="rank").iloc[0]
            partys.append((top_party))
    return partys

def trust_the_process(db, current_round):
    positions_to_fill = get_positions_to_fill(db)
    my_team = get_my_team(db)

    for _, position in my_team.items():
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
    position_filter = apply_position_limits(adjust_available_positions(remaining_positions_to_fill), my_team, current_round)

    if (current_round >= 8):
        unmet_minumums = set()
        # If we have an unfilled minimum at this point, filter to fill those positions
        for position, minimum in POSITION_MINIMUMS.items():
            current_count = sum(1 for pos in my_team.values() if pos == position)
            if current_count < minimum:
                unmet_minumums.add(position)
        if unmet_minumums:
            position_filter = unmet_minumums

    # load all of the available players into a pandas dataframe
    df = pd.read_sql("SELECT * FROM players where availability = 'AVAILABLE'", db.engine)
    my_team_df = get_my_team_as_df(db)

    # expand the allowed_position json strings into a set
    df["allowed_positions_set"] = df["allowed_positions"].apply(
        lambda x: set(json.loads(x)) if x else set()
    )

    # apply the filtered posiions
    filtered_df = df[df["allowed_positions_set"].apply(lambda s: bool(s & position_filter))]
    filtered_df = apply_team_limits(filtered_df, my_team_df, current_round, 12)
    df_with_tiers = add_an_extra_column_to_dataframe(filtered_df, 'id', RANDOM_NUMBERS)
    
    top_tier = df_with_tiers["extra_column"].min()
    top_tier_count = 0
    while (top_tier_count <= 3 and top_tier <= len(RANDOM_NUMBERS.keys()) + 1):
        only_top_tier_df = df_with_tiers[df_with_tiers["extra_column"] <= top_tier]
        top_tier_count = only_top_tier_df.shape[0]
        top_tier += 1
    filtered_df_sorted = only_top_tier_df.sort_values(by=["extra_column", "rank"], ascending=True)

    # Obfuscated strategic magic
    opportunities = []
    inverse_square_opportunity = detect_inverse_square_opportunity(my_team_df, filtered_df_sorted)
    opportunities += inverse_square_opportunity

    qb_inverse_square_opportunity = detect_qb_inverse_square_opportunity(my_team_df, filtered_df_sorted)
    opportunities += qb_inverse_square_opportunity

    party_opportunity = detect_partying_opportunity(my_team_df, filtered_df_sorted)
    opportunities += party_opportunity

    if opportunities:
        best_opportunity = sorted(opportunities, key=lambda x: (x['extra_column'], x['rank']))[0]
        return best_opportunity['id']

    if not filtered_df_sorted.empty:
        best_player = filtered_df_sorted.iloc[0]
        print(best_player["full_name"])
        return best_player["id"]
    else:
        return ""  # No eligible player

def draft_player() -> str:
    db = DatabaseManager()
    try:
        current_round = get_draft_round(db)
        return trust_the_process(db, current_round)
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
