from blitz_env import is_drafted, simulate_draft, visualize_draft_board, Player, GameState
from typing import List

def draft_player(game_state: GameState) -> str:
    """
    Selects a player to draft based on the highest rank.

    Args:
        players (List[Player]): A list of Player objects.

    Returns:
        str: The id of the drafted player.
    """
    # Filter out already drafted players
    undrafted_players = [player for player in game_state.players if not is_drafted(player)]

    # Select the player with the highest rank (lowest rank number)
    if undrafted_players:
        drafted_player = min(undrafted_players, key=lambda p: p.rank)
        return drafted_player.id
    else:
        return "None"  # Return empty string if no undrafted players are available