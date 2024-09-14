from blitz_env import is_drafted, simulate_draft, visualize_draft_board, Player, GameState
from typing import List

remaining_1st_tier_slots = [
    "QB", "RB", "RB", "WR", "WR", "WR", "TE", "DST", 
]
remaining_2nd_tier_slots = [  # bench
    "QB", "RB", "WR", "WR", "RB", "DST",
]
remaining_3rd_tier_slots = [  # take k last
    "K",
]

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
    undrafted_qbs = [player for player in undrafted_players if player.allowed_positions[0] == 'QB']

    # Select the player with the highest rank (lowest rank number)
    if undrafted_players:
        undrafted_players.sort(key=lambda p: p.rank)
        for player in undrafted_players:
            pos = player.allowed_positions[0]
            if pos in remaining_1st_tier_slots:
                remaining_1st_tier_slots.remove(pos)
                print("1", pos, remaining_1st_tier_slots)
                return player.id
        for player in undrafted_players:
            pos = player.allowed_positions[0]
            if pos in remaining_2nd_tier_slots:
                remaining_2nd_tier_slots.remove(pos)
                print("2", pos, remaining_2nd_tier_slots)
                return player.id
        for player in undrafted_players:
            pos = player.allowed_positions[0]
            if pos in remaining_3rd_tier_slots:
                remaining_3rd_tier_slots.remove(pos)
                print("3", pos, remaining_3rd_tier_slots)
                return player.id
        return undrafted_players[0].id  # backup
    else:
        return ""  # Return empty string if no undrafted players are available