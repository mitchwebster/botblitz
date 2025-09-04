# from blitz_env.simulate_draft_sqlite import simulate_draft, visualize_draft_board
from blitz_env.models import DatabaseManager, Player
from blitz_env import GameState, AddDropSelection
import pandas as pd

pd.set_option('mode.chained_assignment',None)

def get_roster(db):
    roster = {
            'QB': [],
            'RB': [],
            'WR': [],
            'TE': [],
            'DST': [],
            'K': []
        }

    df = pd.read_sql("SELECT * FROM game_statuses", db.engine) # get game status
    draft_pick = df.iloc[0]["current_draft_pick"]
    print(f"Current pick is {draft_pick}")
    
    bot_id = df.iloc[0]["current_bot_id"]
    queryStr = f"SELECT * FROM players where current_bot_id = '{bot_id}'"
    my_team = pd.read_sql(queryStr, db.engine) 

    # queryStr = f"SELECT * FROM players where current_bot_id = '{draft_position}'"
    # my_team = pd.read_sql(queryStr, db.engine) 

    my_team.loc[:, "allowed_positions"] = my_team['allowed_positions'].str.strip('[""]')

    # cur_players = db.session.query(Player.id, Player.full_name, Player.rank, Player.allowed_positions, Player.current_bot_id ).filter(
    #         Player.current_bot_id == draft_position)

    for p in my_team.to_dict(orient='records'):
        roster[p['allowed_positions']].append(p['full_name'])
    
    return roster

def draftable_positions(roster_dic):
    
    pos_max_dict = {
            'QB': 3,
            'RB': 4,
            'WR': 4,
            'TE': 1,
            'DST': 1,
            'K': 1
        }

    roster_pos = list(roster_dic.keys())

    draftable_pos_list = [pos for pos in roster_pos if len(roster_dic[pos]) < pos_max_dict[pos]]

    return draftable_pos_list

# --- Example function to apply adjustments ---
def apply_adjustments(df, round_num, roster):

    """
    df: DataFrame of players
    round_num: current draft round (1-14)
    roster: dict with counts of positions already drafted
            e.g. {"QB":1, "RB":1, "WR":0, "TE":0}
    """
    # --- Helper: Define QB tiers ---
    tier1_qbs = ["Josh Allen", "Jalen Hurts", "Lamar Jackson"]
    tier2_qbs = ["Patrick Mahomes", "Joe Burrow", "C.J. Stroud", "Justin Herbert", "Anthony Richardson"]
    tier3_qbs = ["Dak Prescott", "Tua Tagovailoa", "Trevor Lawrence", "Jared Goff", "Jordan Love"]


    # Create adjustments
    df["Adjustment"] = 0
    
    # ------------------------------
    # 1. QB scarcity rules
    # ------------------------------
    if roster.get("QB", 0) == 0 and round_num <= 2:
        df.loc[df["full_name"].isin(tier1_qbs), "Adjustment"] += 10
        df.loc[df["full_name"].isin(tier2_qbs), "Adjustment"] += 6
    if roster.get("QB", 0) == 1 and round_num <= 4:
        df.loc[df["full_name"].isin(tier2_qbs), "Adjustment"] += 5
        df.loc[df["full_name"].isin(tier3_qbs), "Adjustment"] += 3
    if roster.get("QB", 0) >= 2:
        # no QB bumps unless you want QB3 insurance
        if round_num >= 7:
            df.loc[df["allowed_positions"] == "QB", "Adjustment"] += 2
        else:
            df.loc[df["allowed_positions"] == "QB", "Adjustment"] -= 3

    # ------------------------------
    # 2. Positional needs
    # ------------------------------
    if roster.get("RB", 0) < 2 and round_num <= 5:
        df.loc[df["allowed_positions"] == "RB", "Adjustment"] += 5
    if roster.get("WR", 0) < 2 and round_num <= 5:
        df.loc[df["allowed_positions"] == "WR", "Adjustment"] += 5

    # Flex slot pressure
    if roster.get("RB", 0) + roster.get("WR", 0) < 3:
        df.loc[df["allowed_positions"].isin(["RB", "WR"]), "Adjustment"] += 2

    # ------------------------------
    # 3. Round-based rules
    # ------------------------------
    if round_num in [1, 2]:
        df.loc[df["allowed_positions"] == "QB", "Adjustment"] += 6
        df.loc[df["allowed_positions"] == "WR", "Adjustment"] += 3
    if round_num in [3, 4, 5]:
        df.loc[df["allowed_positions"] == "RB", "Adjustment"] += 4
    if 6 <= round_num <= 10:
        df.loc[df["allowed_positions"] == "WR", "Adjustment"] += 3
        df.loc[df["allowed_positions"] == "QB", "Adjustment"] += 2
        df.loc[df["allowed_positions"].isin(["K", "DST"]), "Adjustment"] += 10
    if round_num >= 11:
        df.loc[df["allowed_positions"] == "RB", "Adjustment"] += 5  # upside handcuffs
        df.loc[df["allowed_positions"] == "WR", "Adjustment"] += 3
        df.loc[df["allowed_positions"].isin(["K", "DST"]), "Adjustment"] += 10

    # ------------------------------
    # 3. Round-based rules
    # ------------------------------
    if round_num in [1, 2]:
        df.loc[df["allowed_positions"] == "QB", "Adjustment"] += 6
        df.loc[df["allowed_positions"] == "WR", "Adjustment"] += 3
    if round_num in [3, 4, 5]:
        df.loc[df["allowed_positions"] == "RB", "Adjustment"] += 4
    if 6 <= round_num <= 10:
        df.loc[df["allowed_positions"] == "WR", "Adjustment"] += 3
        df.loc[df["allowed_positions"] == "QB", "Adjustment"] += 2
    if round_num >= 11:
        df.loc[df["allowed_positions"] == "RB", "Adjustment"] += 5  # upside handcuffs
        df.loc[df["allowed_positions"] == "WR", "Adjustment"] += 3

    # ------------------------------
    # 4. Force K/DST late (12-round draft)
    # ------------------------------
    if round_num >= 11:
        # Last two rounds → prioritize K and DST
        df.loc[df["allowed_positions"].isin(["K", "DST"]), "Adjustment"] += 1000  
    else:
        # Before Round 11 → make them undraftable
        df.loc[df["allowed_positions"].isin(["K", "DST"]), "Adjustment"] -= 1000  

    # ------------------------------
    # 5. Player-specific tweaks
    # ------------------------------
    df.loc[df["allowed_positions"] == "Patrick Mahomes", "Adjustment"] -= 4
    df.loc[df["allowed_positions"].isin(["Austin Ekeler", "Rachaad White", "Alvin Kamara"]), "Adjustment"] += 3
    df.loc[df["allowed_positions"].isin(["Derrick Henry", "Nick Chubb"]), "Adjustment"] -= 3

    # ------------------------------
    # Final adjusted rank
    # ------------------------------
    df.loc[:,"AdjustedRank"] = df["rank"] - df["Adjustment"]

    # Sort so lowest AdjustedRank = best pick
    return df.sort_values("AdjustedRank").reset_index(drop=True).head(1)

def draft_player() -> str:
    db = DatabaseManager()

    # get game state 
    # draft_position = db.get_game_status().current_bot_id
    game_statuses_df = pd.read_sql("SELECT * FROM game_statuses", db.engine) # get game status
    draft_pick = game_statuses_df.iloc[0]["current_draft_pick"]
    
    # draft_position = df.iloc[0]["current_bot_id"]
    cur_draft_pick = db.get_game_status().current_draft_pick 
    bot_count = len(db.get_all_bots())
    cur_round = -(-cur_draft_pick//bot_count) # ceiling of cur_draft_pick divided by num of bots

    # get players
    all_players_df = pd.read_sql("SELECT * FROM players", db.engine)
    available_players_df = all_players_df[all_players_df.availability == 'AVAILABLE']
    available_players_df.loc[:, "allowed_positions"] = available_players_df['allowed_positions'].str.strip('[""]')

    roster = get_roster(db) # get my team's roster
    roster_count ={k:len(v) for k, v in roster.items()}
    
    
    draftable_positions_list = draftable_positions(roster)
    print('round:',cur_round)
    print(roster_count)
    print('my roster:', roster)
    print(draftable_positions_list)
    draftable_available_players_df = available_players_df[available_players_df.allowed_positions.isin(draftable_positions_list)]
    
    adj_rank_top_pick = apply_adjustments(draftable_available_players_df, cur_round, roster_count) 


    try:     
        # display(adj_rank_top_pick)       
        return adj_rank_top_pick['id'].values[0]

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
