import os
from .loadPlayers import load_all_players

def load_players():
    current_dir = os.path.dirname(__file__)
    file_path = os.path.join(current_dir, 'player_ranks_2024.csv')
    players = load_all_players(file_path)
    return players