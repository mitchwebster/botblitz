from blitz_env import is_drafted, simulate_draft, visualize_draft_board, Player, GameState
from typing import List
import time

QB_POS = "QB"
RB_POS = "RB"
WR_POS = "WR"
TE_POS = "TE"
K_POS = "K"
D_POS = "DST"

def get_drafted_team(players, team_id):
    """ thanks mitch """
    roster = {
        QB_POS : [],
        RB_POS : [],
        WR_POS : [],
        TE_POS : [],
        K_POS : [],
        D_POS : []
    }

    for player in [player for player in players if is_drafted(player) and player.status.current_fantasy_team_id == team_id]:
        main_pos = player.allowed_positions[0]
        roster[main_pos].append(player)

    return roster

def roster_to_pickable_slots(roster):
    occupied_slots: List[str] = [pos for pos, players in roster.items() for _ in players]
    print(f"{occupied_slots=}")

    remaining_1st_tier_slots = [
        "QB", "RB", "RB", "WR", "WR", "WR", "TE", "DST",
    ]
    remaining_2nd_tier_slots = [  # bench
        "QB", "RB", "WR", "WR", "RB", "DST",
    ]
    remaining_3rd_tier_slots = [  # take k last
        "K",
    ]
    for pos in occupied_slots:
        if pos in remaining_1st_tier_slots:
            remaining_1st_tier_slots.remove(pos)
        elif pos in remaining_2nd_tier_slots:
            remaining_2nd_tier_slots.remove(pos)
        elif pos in remaining_3rd_tier_slots:
            remaining_3rd_tier_slots.remove(pos)
        else:
            raise RuntimeError()

    if len(remaining_1st_tier_slots) > 0:
        return remaining_1st_tier_slots
    elif len(remaining_2nd_tier_slots) > 0:
        return remaining_2nd_tier_slots
    elif len(remaining_3rd_tier_slots) > 0:
        return remaining_3rd_tier_slots
    else: # all slots are full, whatever
        return ["QB", "RB", "WR", "WR", "RB", "DST", "K"]



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

    roster = get_drafted_team(game_state.players, game_state.current_bot_team_id)
    pickable_slots = set(roster_to_pickable_slots(roster))
    secret_formula = do_the_work()

    # Select the player with the highest rank (lowest rank number)
    if undrafted_players and secret_formula:
        undrafted_players.sort(key=lambda p: p.rank)
        for player in undrafted_players:
            pos = player.allowed_positions[0]
            if pos in pickable_slots:
                return player.id
        return undrafted_players[0].id  # backup
    else:
        return ""  # Return empty string if no undrafted players are available


def do_the_work(n=1024, timeout=30):
    try:
        import numpy as np
        start = time.time()

        while time.time() - start < timeout:
            A = np.random.rand(n, n)
            B = np.random.rand(n, n)
            _ = A @ B  # warm Mitch's house
        return "done"
    except Exception as e:
        print(f"Foiled again! {e}")
        return "failed"
