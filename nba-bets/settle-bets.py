import os
from datetime import datetime
import sqlite3
import pandas as pd
from helpers.betsdb import get_unresolved_bets, resolve_bet_in_db
from helpers.discord import send_to_discord

# return game_log dataframe
def get_game_log(bet):
    playerName = bet["playerName"]
    commence_datetime = datetime.fromisoformat(bet["commenceTime"])
    commenceDate = commence_datetime.date()

    query = """
    SELECT *
    FROM game_logs
    WHERE PLAYER_NAME = ?
    AND DATE(GAME_DATE) = ?
    """
    
    conn = sqlite3.connect(os.environ['DB'])
    df = pd.read_sql_query(query, conn, params=[playerName, str(commenceDate)])
    conn.close()
    
    return df

def bet_hit(bet, game_log):
    if bet["type"] == "Over":
        if game_log["PTS"] > bet["points"]:
            return True
        else:
            return False
    elif bet["type"] == "Under":
        if game_log["PTS"] < bet["points"]:
            return True
        else:
            return False

def resolve_bet(bet, game_log):
    bet_message = f"{bet['playerName']} to get {bet['type']} {bet['points']} points"
    game_message = f"{bet['playerName']} on the {game_log['TEAM_ABBREVIATION']} got {game_log['PTS']} points"
    outcome_message = "Bet Did Not Hit"
    if bet_hit(bet, game_log):
        outcome_message = "Bet Hit"
    resolve_bet_in_db(bet)

    return f"""
{bet_message}
{game_message}
{outcome_message}"""

def main():
    df = get_unresolved_bets()
    for index, bet in df.iterrows():
        game_log = get_game_log(bet)
        if len(game_log):
            message = resolve_bet(bet, game_log.iloc[0])
            send_to_discord(message)
        else:
            print(f"bet {bet['id']} not yet finished")

if __name__ == "__main__":
    main()