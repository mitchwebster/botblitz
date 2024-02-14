import os
import sqlite3
from nba_api.stats.endpoints import playergamelogs
import pandas as pd

def main():
    print("Downloading Game Logs")
    custom_headers = {
        'Accept': '*/*',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive',
        'Host': 'stats.nba.com',
        'Origin': 'https://www.nba.com',
        'Referer': 'https://www.nba.com/',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-site',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'sec-ch-ua': '"Not A Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"'
    }
    game_logs = playergamelogs.PlayerGameLogs(season_nullable="2023-24", headers=custom_headers, timeout=60)
    df = game_logs.get_data_frames()[0]
    print(f"Found {len(df)} rows")
    # Connect to SQLite database
    conn = sqlite3.connect(os.environ['DB'])
    # Write the DataFrame to a table in the database
    df.to_sql('game_logs', conn, if_exists='replace', index=False)
    # Close the database connection
    conn.close()

if __name__ == "__main__":
    main()