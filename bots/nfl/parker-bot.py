from blitz_env import is_drafted, simulate_draft, visualize_draft_board, Player, GameState
from typing import List

def get_current_round(game_state: GameState) -> int:
    zero_based_round = (game_state.current_draft_pick - 1) // len(game_state.teams)
    return zero_based_round + 1

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

    cur_round = get_current_round(game_state)

    # - do not draft from any of these teams: Cleveland, New York Giants, Carolina, Las Vegas
    team_block_map = { 
        "MIN": "",
        "GB": "",
        "CHI": "",
        "SF": "",
        "CLE": "",
        "NYG": "",
        "CAR": "",
        "LV": ""
    }
    undrafted_players = [player for player in undrafted_players if player.professional_team not in team_block_map]

    # don’t pick a QB until after round 7 
    if cur_round <= 7:
        undrafted_players = [player for player in undrafted_players if player.allowed_positions[0] != 'QB']

    if cur_round <= 10:
        undrafted_players = [player for player in undrafted_players if player.allowed_positions[0] != 'TE']

    remaining_players = sorted(undrafted_players, key=lambda p: p.rank)

    # - if a Detroit Lion is projected in the next 20 picks, pick him.
    for i in range(20):
        if remaining_players[i].professional_team == "DET":
            return remaining_players[i].id
    
    # Otherwise pick the next highest player
    return remaining_players[0].id