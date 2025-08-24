import os
import pandas as pd
from blitz_env.agent_pb2 import Player

def load_players(year: int):
    if year not in [2021, 2022, 2023, 2024, 2025]:
        raise Exception("year not supported")
    current_dir = os.path.dirname(__file__)
    file_path = os.path.join(current_dir, f'player_ranks_{str(year)}.csv')
    players = load_all_players(file_path)
    return players

def load_all_players(csv_path):
    # Load the CSV file
    df = pd.read_csv(csv_path)

    # Initialize an empty list to store Player objects
    players = []

    # Iterate through each row in the DataFrame
    for index, row in df.iterrows():
        # Create a new Player object and assign data from the row
        player = Player(
            id=str(row['fantasypros_id']),
            gsis_id=str(row['gsis_id']),
            full_name=row['player_name'],
            allowed_positions=[row['pos']],  # assuming 'pos' is a string; adjust if it's actually a list
            professional_team=row['team'],
            player_bye_week=int(row['position_rank']) if pd.notna(row['position_rank']) else 0,
            rank=int(row['rank']),
            tier=int(row['tier']),
            position_rank=int(row['position_rank']) if pd.notna(row['position_rank']) else 0,
            position_tier=int(row['position_tier']) if pd.notna(row['position_tier']) else 0
        )
        
        # Add the Player object to the list
        players.append(player)
    return players