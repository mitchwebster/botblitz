from blitz_env import WaiverClaim, AttemptedFantasyActions
from blitz_env.models import DatabaseManager
import pandas as pd
import json

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

def count_my_qbs(db):
    """Count how many QB-eligible players the current bot already rosters."""
    bot_id = get_current_bot_id(db)
    queryStr = f"SELECT * FROM players where current_bot_id = '{bot_id}'"
    my_team = pd.read_sql(queryStr, db.engine)

    qb_count = 0
    for _, row in my_team.iterrows():
        allowed = set(json.loads(row["allowed_positions"])) if row["allowed_positions"] else set()
        if "QB" in allowed:
            qb_count += 1
    return qb_count

def best_available_qb_id(db):
    """Return the id of the best-ranked AVAILABLE QB-eligible player, or '' if none."""
    df = pd.read_sql("SELECT * FROM players where availability = 'AVAILABLE'", db.engine)
    df["allowed_positions_set"] = df["allowed_positions"].apply(
        lambda x: set(json.loads(x)) if x else set()
    )
    qb_df = df[df["allowed_positions_set"].apply(lambda s: "QB" in s)]
    qb_df_sorted = qb_df.sort_values(by="rank", ascending=True)
    if not qb_df_sorted.empty:
        return qb_df_sorted.iloc[0]["id"]
    return ""

def draft_player() -> str:
    """
    QB-first baseline: forces the first two draftable picks to be the best
    available QBs, then falls through to the standard-bot selection logic.

    Returns:
        str: The id of the drafted player.
    """
    db = DatabaseManager()
    try:
        # Force the first two picks to be the best available QBs.
        if count_my_qbs(db) < 2:
            qb_id = best_available_qb_id(db)
            if qb_id:
                print(f"QB-first: forcing QB pick (qb_id={qb_id})")
                return qb_id
            # If no QB is available, fall through to standard logic below.

        # --- standard-bot selection logic ---
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

def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    db = DatabaseManager()
    try:
        current_fantasy_week = get_current_fantasy_week(db)
        current_bot_id = get_current_bot_id(db)
        current_budget = get_my_remaining_budget(db, current_bot_id)
        current_opponent = get_current_opponent_id(db, current_bot_id, current_fantasy_week)
        data_on_available_players = get_season_stats_for_available_players(db)

        print(f"Current Fantasy Week: {current_fantasy_week}")
        print(f"My Bot Id: {current_bot_id}")
        print(f"My Remaining Budget: {current_budget}")
        print(f"My Opponent Id: {current_opponent}")

        for index, row in data_on_available_players.iterrows():
            print(index, row["week"], row["full_name"], row["allowed_positions"], row["FPTS"])

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
    finally:
        db.close()

if __name__ == "__main__":
    result = perform_weekly_fantasy_actions()
