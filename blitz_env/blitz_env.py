import os
from .loadPlayers import load_all_players

def load_players(year: int):
    if year not in [2023, 2024]:
        raise Exception("only 2023, and 2024 supported")
    current_dir = os.path.dirname(__file__)
    file_path = os.path.join(current_dir, f'player_ranks_{str(year)}.csv')
    players = load_all_players(file_path)
    return players