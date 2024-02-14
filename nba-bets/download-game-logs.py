import os
import sqlite3
from nba_api.stats.endpoints import playergamelogs
import pandas as pd

def main():
    print("Downloading Game Logs")
    game_logs = playergamelogs.PlayerGameLogs(season_nullable="2023-24")
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