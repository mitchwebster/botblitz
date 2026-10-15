# from blitz_env.simulate_draft_sqlite import simulate_draft, visualize_draft_board
from blitz_env.models import DatabaseManager, Player, Bot, GameStatus
from blitz_env import AttemptedFantasyActions, WaiverClaim
import pandas as pd
import json

QB_ROUNDS = [5, 8, 10, 20]

REAL_POSITIONS = ["QB", "WR", "RB", "TE", "K", "DST"]

# Don't worry about it
# (FantasyPros 2026 superflex half-PPR draft tiers, last updated 9/10/2026, + top DSTs.
#  Only used as a fallback when there's no in-season data -- see build_in_season_tiers.)
RANDOM_NUMBERS = {
    1: ["17298","17233","23046","19196","19275","22968","22902",
    ],
    2: ["19788","23084","23133","18635","23180","19780","23070","19799","15600","16393","19217",
    ],
    3: ["22958","19202","19797","19236","22910","23160","18218","25324","17240","23163","23136","16413","20130","23021","15501","15514","25395","9451",
    ],
    4: ["20111","22936","22963","25989","18600","25409","19222","19246","22916","23113","22739","17237","23059","26006","19211","22982","22955","19790","23096","23072","26122","25403","23499","25417",
    ],
    5: ["23062","19231","19210","27016","12123","26434","18466","23677","25981","17265","23071","27166","18239","23886","25391","18232","27329","23106","25411","17236","27297","12119","22726",
    ],
    6: ["24755","24209","25298","23064","26009","23065","19647","18219","18705","22978","23000","16406","20164","27050","26148","17253","23123","19245","23013","25333","19278","16499","19792","23791","23118","13981","22900","22969","24706","27142","23020","27316","11594","23310","22985","23107",
    ],
    7: ["25247","15642","27131","23891","28138","17270","27331","25420","9001","23781","18598","16673","26024","22986","23794","19562","23019","27520","22718","24172","26403","17269","23748","18244","11687","25323","27320","28108","24687","22908","18269","25360","25337","27339","15561","23982","27102","25345","17349","26035",
    ],
    8: ["24357","23152","22989","28107","24346","20094","16421","23179","24354","24177","23091","18290","26191","26215","26335","28013","22980","19590","26398","27211","19201","11616","26055","26314","27337","28127","28401","24205","17258","27109","20163","26355","25997","22973","23679","26038","19794","24343","23181","27122","24362","19198","27994","19302","16433","26034","18256","25419","25987","19810","27446","22915",
    ],
    9: ["16411","23153","11177","26409","26039","16423","16398","27165","24333","28130","28135","25823","16447","28084","25885","16399","26019","25984","23092","25968","27224","25418","18406","27310","22913","19372","25977","24360","27285","26022","23079","26136","27059","28424","27259","26005","27097","27315","25361","23080","18280","27487","24367","27463","25654","23104","18226","16489","18706","26212","25335","17268","28126","25393","17272",
    ],
    10: ["23144","25304","24371","14104","24344","23056","23054","22930","23101","22756","25876","26392","23682","25693","24778","25388","22967","16425","24335","23004","25332","28129","23770","22921","25430","18631","17066","19229","28154","22845","23837","25322","27147","28132","18864","19252","24901","26012","15623","15802","22905","28133","19539","26328","28896",
    ],
    11: ["18520","26309","16081","24334","27076","27993","27449","24351","17527","28297","12127","25983","15665","22953","25287","16420","28067","26037","20126","26310","22795","24238","27144","16460","11798","26194","28125","25416","23135","23119","27454","15581","27138","22728","27288","22895","26011","27810","28168","26329","23812","18656","18487","28079","20113","23986","27093","19423","26379","25941","27182","16445","28157","25282","28403","28109","28427","27469","28047","27294","25267","23249","19747",
    ],
    12: ["22833","25269","22928","19844","17826","25627","16556","16483","28434","28141","23018","28137","17508","27499","20156","22976","18571","17145","11689","26748","24372","22947","19781","25368","27326","17598","10977","18561","28161","24347","23742","20114","24370","25289","27486","13971","18590","22763","18585","11606","24361","25748","27444","13890","13891","9300","27066","8120","8280","8090","8260","8230","8240",
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

# Tier sizes copied from the FantasyPros list above, so in-season tiers keep the same shape
IN_SEASON_TIER_SIZES = [7, 11, 18, 24, 23, 36, 40, 52, 55, 45, 63, 47]
MAX_GAMES_MISSED = 2  # miss 3+ of your team's games and you're everyone else's problem
SECRET_WEAPON = "8000"
MAX_GAMES_MISSED_LAST_YEAR = 4  # injury history: missing this many last season costs a tier

# season.db is missing 2026 week 1 stats for 12 teams; these are those games' rows from
# nflverse (fantasypros_id, position, team, FPTS), merged in wherever the DB lacks them.
WEEK_1_2026_BACKFILL = [
    # ARI
    ("22936", "TE", "ARI", 24.5),
    ("15642", "QB", "ARI", 16.48),
    ("17066", "WR", "ARI", 15.5),
    ("25403", "RB", "ARI", 13.0),
    ("25333", "WR", "ARI", 10.6),
    ("24172", "RB", "ARI", 9.0),
    ("8000", "DST", "ARI", 6.0),
    ("23064", "WR", "ARI", 4.3),
    ("25354", "K", "ARI", 0.0),
    # DAL
    ("22739", "RB", "DAL", 24.2),
    ("19202", "WR", "DAL", 15.4),
    ("15600", "QB", "DAL", 14.4),
    ("22963", "WR", "DAL", 5.8),
    ("26398", "WR", "DAL", 4.2),
    ("22718", "TE", "DAL", 2.6),
    ("25267", "TE", "DAL", 2.2),
    ("18520", "WR", "DAL", 1.8),
    ("8080", "DST", "DAL", 1.0),
    ("25654", "RB", "DAL", 0.7),
    ("25941", "RB", "DAL", 0.7),
    ("26068", "K", "DAL", 0.0),
    # DEN
    ("16411", "TE", "DEN", 14.3),
    ("27211", "WR", "DEN", 8.2),
    ("27142", "RB", "DEN", 8.1),
    ("22910", "QB", "DEN", 5.44),
    ("19245", "RB", "DEN", 3.6),
    ("17253", "WR", "DEN", 3.1),
    ("8090", "DST", "DEN", 3.0),
    ("19423", "TE", "DEN", 3.0),
    ("19790", "WR", "DEN", 1.2),
    ("23092", "WR", "DEN", 0.5),
    ("27320", "RB", "DEN", 0.0),
    ("16026", "K", "DEN", 0.0),
    ("23080", "WR", "DEN", 0.0),
    # GB
    ("23886", "WR", "GB", 32.7),
    ("19246", "QB", "GB", 19.48),
    ("26024", "WR", "GB", 15.5),
    ("25298", "TE", "GB", 9.5),
    ("16460", "TE", "GB", 7.0),
    ("23020", "WR", "GB", 5.0),
    ("23118", "RB", "GB", 3.7),
    ("25823", "RB", "GB", 2.9),
    ("8110", "DST", "GB", 1.0),
    ("27165", "RB", "GB", 0.0),
    # KC
    ("23021", "RB", "KC", 34.1),
    ("16413", "QB", "KC", 21.66),
    ("11594", "TE", "KC", 10.1),
    ("23113", "WR", "KC", 9.9),
    ("8150", "DST", "KC", 9.0),
    ("28107", "RB", "KC", 8.8),
    ("23019", "WR", "KC", 4.8),
    ("19539", "TE", "KC", 1.5),
    ("16712", "K", "KC", 0.0),
    ("27076", "WR", "KC", 0.0),
    ("28424", "WR", "KC", 0.0),
    ("22913", "WR", "KC", 0.0),
    # LAC
    ("26122", "WR", "LAC", 19.2),
    ("18635", "QB", "LAC", 13.26),
    ("16399", "TE", "LAC", 10.8),
    ("25395", "RB", "LAC", 8.3),
    ("25997", "TE", "LAC", 5.2),
    ("26215", "WR", "LAC", 5.0),
    ("23123", "WR", "LAC", 3.7),
    ("8250", "DST", "LAC", 1.0),
    ("25360", "RB", "LAC", 0.9),
    ("23901", "K", "LAC", 0.0),
    ("22756", "TE", "LAC", 0.0),
    # LV
    ("25989", "RB", "LV", 32.7),
    ("11177", "QB", "LV", 13.8),
    ("27224", "WR", "LV", 11.3),
    ("8220", "DST", "LV", 11.0),
    ("23056", "TE", "LV", 9.2),
    ("24177", "WR", "LV", 5.7),
    ("25337", "WR", "LV", 4.7),
    ("28108", "RB", "LV", 4.1),
    ("26012", "WR", "LV", 0.8),
    ("26194", "RB", "LV", 0.0),
    # MIA
    ("23499", "QB", "MIA", 16.7),
    ("28135", "WR", "MIA", 14.4),
    ("23136", "RB", "MIA", 10.6),
    ("26314", "WR", "MIA", 6.3),
    ("8160", "DST", "MIA", 4.0),
    ("23153", "TE", "MIA", 3.8),
    ("26038", "WR", "MIA", 3.5),
    ("28067", "WR", "MIA", 0.0),
    ("28403", "TE", "MIA", 0.0),
    ("25987", "RB", "MIA", -0.1),
    # MIN
    ("19236", "WR", "MIN", 31.2),
    ("18290", "TE", "MIN", 13.6),
    ("22969", "RB", "MIN", 11.9),
    ("16673", "RB", "MIN", 10.0),
    ("8170", "DST", "MIN", 6.0),
    ("18487", "TE", "MIN", 4.2),
    ("23107", "WR", "MIN", 0.0),
    ("19590", "WR", "MIN", 0.0),
    ("26341", "K", "MIN", 0.0),
    ("18600", "QB", "MIN", -0.38),
    # NYG
    ("23781", "TE", "NYG", 27.8),
    ("23160", "QB", "NYG", 26.6),
    ("27166", "RB", "NYG", 14.1),
    ("18280", "RB", "NYG", 13.8),
    ("25409", "WR", "NYG", 12.9),
    ("19810", "WR", "NYG", 4.7),
    ("27994", "WR", "NYG", 4.5),
    ("8200", "DST", "NYG", 3.0),
    ("23104", "TE", "NYG", 1.9),
    ("17145", "RB", "NYG", 0.0),
    ("22980", "RB", "NYG", -0.6),
    # PHI
    ("19275", "QB", "PHI", 24.72),
    ("17270", "TE", "PHI", 23.7),
    ("24354", "WR", "PHI", 15.3),
    ("17240", "RB", "PHI", 9.0),
    ("19222", "WR", "PHI", 8.3),
    ("25420", "WR", "PHI", 2.5),
    ("22930", "RB", "PHI", 2.2),
    ("8230", "DST", "PHI", 1.0),
    ("22908", "RB", "PHI", 0.3),
    ("16540", "K", "PHI", 0.0),
    # WAS
    ("22902", "QB", "WAS", 17.66),
    ("25418", "WR", "WAS", 16.4),
    ("13981", "WR", "WAS", 15.5),
    ("27316", "RB", "WAS", 12.6),
    ("23891", "RB", "WAS", 4.7),
    ("23982", "TE", "WAS", 3.6),
    ("18466", "WR", "WAS", 3.4),
    ("22833", "TE", "WAS", 3.3),
    ("8310", "DST", "WAS", 3.0),
    ("27454", "WR", "WAS", 1.4),
    ("20113", "WR", "WAS", 0.0),
]

def load_weekly_stats(db, year):
    """This season's weekly_stats (id, position, team, week, FPTS), plus WEEK_1_2026_BACKFILL."""
    stats = pd.read_sql(
        "SELECT fantasypros_id AS id, position, team, week, FPTS FROM weekly_stats "
        "WHERE year = ? AND fantasypros_id IS NOT NULL",
        db.engine, params=(year,),
    )
    if year == 2026:
        backfill = pd.DataFrame(WEEK_1_2026_BACKFILL, columns=["id", "position", "team", "FPTS"]).assign(week=1)
        stats = pd.concat([stats, backfill], ignore_index=True).drop_duplicates(["id", "week"], keep="first")
    return stats

def get_too_many_missed_games(db):
    """Ids of players who have missed more than MAX_GAMES_MISSED games so far this season.

    A game only counts as missed when the player's team has stats that week, so byes
    (and any week the scrape is missing a team) don't count against anyone.
    """
    year = db.get_league_settings().year
    stats = load_weekly_stats(db, year)
    games = pd.read_sql("SELECT id, professional_team FROM players", db.engine)
    games["team_games"] = games["professional_team"].map(stats.groupby("team")["week"].nunique()).fillna(0)
    games["games"] = games["id"].map(stats.groupby("id")["week"].nunique()).fillna(0)
    missed = games["team_games"] - games["games"]
    return set(games[missed > MAX_GAMES_MISSED]["id"])

def get_fragility(db, year):
    """Tiers to drop each player for injury red flags (one tier per flag):
    ruled Out at any point this season, and missed MAX_GAMES_MISSED_LAST_YEAR+ of their team's games last season.
    """
    flags = {}
    out = pd.read_sql(
        "SELECT DISTINCT fantasypros_id AS id FROM weekly_injuries "
        "WHERE year = ? AND game_status = 'Out' AND fantasypros_id IS NOT NULL",
        db.engine, params=(year,),
    )
    for player_id in out["id"]:
        flags[player_id] = flags.get(player_id, 0) + 1

    # Last season: games played vs. games their (most frequent) team played; rookies have no rows
    last_year = pd.read_sql(
        "SELECT fantasypros_id AS id, team, week FROM weekly_stats "
        "WHERE year = ? AND week <= 18 AND fantasypros_id IS NOT NULL",
        db.engine, params=(year - 1,),
    )
    if not last_year.empty:
        team_games = last_year.groupby("team")["week"].nunique()
        per_player = last_year.groupby("id").agg(
            team=("team", lambda t: t.mode().iloc[0]), games=("week", "nunique")
        )
        missed = per_player["team"].map(team_games) - per_player["games"]
        for player_id in missed[missed >= MAX_GAMES_MISSED_LAST_YEAR].index:
            flags[player_id] = flags.get(player_id, 0) + 1
    return flags

def build_in_season_tiers(db):
    """Tier players by fantasy points per game in the league year's regular season so far.

    Falls back to the preseason FantasyPros tiers when there's no in-season data yet.
    """
    year = db.get_league_settings().year
    stats = load_weekly_stats(db, year)
    if stats.empty:
        return RANDOM_NUMBERS

    ppg = (
        stats.groupby(["id", "position"])
        .agg(ppg=("FPTS", "mean"), games=("week", "nunique"))
        .reset_index()
    )
    ppg = ppg[~ppg["id"].isin(get_too_many_missed_games(db))].sort_values("ppg", ascending=False)

    # Out in the last two injury reports -> not tiered at all
    injuries = pd.read_sql(
        "SELECT fantasypros_id AS id, week, game_status FROM weekly_injuries "
        "WHERE year = ? AND fantasypros_id IS NOT NULL",
        db.engine, params=(year,),
    )
    out_last_two = set()
    if not injuries.empty:
        latest = injuries["week"].max()
        out = injuries[injuries["game_status"] == "Out"]
        out_last_two = set(out[out["week"] == latest]["id"]) & set(out[out["week"] == latest - 1]["id"])
    fragility = get_fragility(db, year)

    tiers = {}
    skill = ppg[~ppg["position"].isin(["K", "DST"]) & ~ppg["id"].isin(out_last_two)]
    ids = list(skill["id"])
    start = 0
    for tier, size in enumerate(IN_SEASON_TIER_SIZES, start=1):
        for player_id in ids[start:start + size]:
            tiers[player_id] = min(tier + fragility.get(player_id, 0), len(IN_SEASON_TIER_SIZES))
        start += size

    for player_id in list(ppg[ppg["position"] == "DST"]["id"])[:6]:
        tiers[player_id] = len(IN_SEASON_TIER_SIZES)
    tiers[SECRET_WEAPON] = len(IN_SEASON_TIER_SIZES) + 1

    numbers = {}
    for player_id, tier in tiers.items():
        numbers.setdefault(tier, []).append(player_id)
    return numbers

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
    df = df[~df["id"].isin(get_too_many_missed_games(db))]
    my_team_df = get_my_team_as_df(db)

    # expand the allowed_position json strings into a set
    df["allowed_positions_set"] = df["allowed_positions"].apply(
        lambda x: set(json.loads(x)) if x else set()
    )

    # apply the filtered posiions
    filtered_df = df[df["allowed_positions_set"].apply(lambda s: bool(s & position_filter))]
    filtered_df = apply_team_limits(filtered_df, my_team_df, current_round, 12)
    tier_list = build_in_season_tiers(db)
    df_with_tiers = add_an_extra_column_to_dataframe(filtered_df, 'id', tier_list)
    
    top_tier = df_with_tiers["extra_column"].min()
    top_tier_count = 0
    while (top_tier_count <= 3 and top_tier <= len(tier_list.keys()) + 1):
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

def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    return AttemptedFantasyActions(waiver_claims=[])
