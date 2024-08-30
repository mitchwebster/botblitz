import os
from .loadPlayers import load_all_players
from .agent_pb2 import GameState

def load_players(year: int):
    if year not in [2023, 2024]:
        raise Exception("only 2023, and 2024 supported")
    current_dir = os.path.dirname(__file__)
    file_path = os.path.join(current_dir, f'player_ranks_{str(year)}.csv')
    players = load_all_players(file_path)
    return players

def get_current_round(game_state: GameState) -> int:
    zero_based_round = (game_state.current_pick - 1) // len(game_state.teams)
    return zero_based_round + 1