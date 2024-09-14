from blitz_env import is_drafted, simulate_draft, visualize_draft_board, Player, GameState
from typing import List
import time

def draft_player(game_state: GameState) -> str:
    """
    Selects a player to draft based on the highest rank.

    Args:
        players (List[Player]): A list of Player objects.

    Returns:
        str: The id of the drafted player.
    """
    time.sleep(10)

    return "None"  # Return empty string if no undrafted players are available