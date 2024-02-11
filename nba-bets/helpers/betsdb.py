import os
import pandas as pd
import sqlite3

def store_bet(chosen_event, event_player_prop):
    # Connect to SQLite database
    conn = sqlite3.connect(os.environ['DB'])

    # Define the data to be inserted as a dictionary
    new_row_data = {
        "homeTeam": chosen_event['home_team'],
        "awayTeam": chosen_event['away_team'],
        "playerName": event_player_prop['name'],
        "type": event_player_prop['type'],
        "points": event_player_prop['point'],
        "price": event_player_prop['price'],
        "commenceTime": chosen_event['commence_time'],
        "resolved": False
    }

    # Convert the dictionary to a DataFrame
    new_row_df = pd.DataFrame([new_row_data])
    
    # Write the new row to the database
    new_row_df.to_sql('bets', conn, if_exists='append', index=False) 

    # Close the database connection
    conn.close()

def get_unresolved_bets():
    # Connect to SQLite database
    conn = sqlite3.connect(os.environ['DB'])

    sql_query = "SELECT * FROM bets WHERE NOT resolved"

    # Read data into a pandas DataFrame
    df = pd.read_sql_query(sql_query, conn)    
    
    # Close the database connection
    conn.close()
    return df

def resolve_bet_in_db(bet):
    # Connect to SQLite database
    conn = sqlite3.connect(os.environ['DB'])
    cursor = conn.cursor()

    sql_query = f"""
        UPDATE bets
        SET resolved = 0
        WHERE id = {bet['id']}
    """
    
    # Execute the query
    cursor.execute(sql_query)
    
    # Commit the changes
    conn.commit()
    
    # Close the connection
    conn.close()