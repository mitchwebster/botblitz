from blitz_env import is_drafted, GameState, AddDropSelection
from typing import List
from collections import Counter
import requests
import copy
import importlib
import sys

QB_POS = "QB"
RB_POS = "RB"
WR_POS = "WR"
TE_POS = "TE"
K_POS = "K"
D_POS = "DST"

BAD_TEAMS_2024 = set([
    "CLE",
    "NYG",
    "CAR",
    "DEN",
    "TEN",
    "WAS"
])

INJURED_LIST_2024 = set([
    "Christian McCaffrey",
    "Tua Tagovailoa",
    "Cooper Kupp",
    "A.J. Brown",
    "AJ Brown",
    "Deebo Samuel",
    "Puka Nacua",
    "Marquise Brown",
    "J.J. McCarthy",
    "Odell Beckham Jr.",
    "Odell Beckham",
    "T.J. Hockenson",
    "Tyler Higbee",
    "Kendrick Bourne",
    "Keaton Mitchell",
    "Nick Chubb",
])

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
        "QB", "RB", "RB", "WR", "WR", "WR", "TE",
    ]
    remaining_2nd_tier_slots = [  # bench
        "QB", "RB", "WR", "WR", "RB", "DST",
    ]
    remaining_3rd_tier_slots = [  # take k last
        "K", "DST"
    ]
    for pos in occupied_slots:
        if pos in remaining_1st_tier_slots:
            remaining_1st_tier_slots.remove(pos)
        elif pos in remaining_2nd_tier_slots:
            remaining_2nd_tier_slots.remove(pos)
        elif pos in remaining_3rd_tier_slots:
            remaining_3rd_tier_slots.remove(pos)
        else:
            print("somewhere we took an unwanted position")

    if len(remaining_1st_tier_slots) > 0:
        return remaining_1st_tier_slots
    elif len(remaining_2nd_tier_slots) > 0:
        return remaining_2nd_tier_slots
    elif len(remaining_3rd_tier_slots) > 0:
        return remaining_3rd_tier_slots
    else: # all slots are full, whatever, focus on valuable positions
        return ["QB", "RB", "WR"]


def draft_player(game_state: GameState) -> str:
    """
    Selects a player to draft based on the highest rank.
    Args:
        players (List[Player]): A list of Player objects.
    Returns:
        str: The id of the drafted player.
    """
    try:
        pick = ensemble_all_the_bad_advice(game_state)
        if pick is not None and pick != "":
            print("Using ensemble pick")
            return pick
    except Exception as e:
        print(f"Error getting ensemble pick: {e}")

    print("falling back to original game plan")
    _game_state = copy.deepcopy(game_state)
    return draft_player_tyler_backup(_game_state)


def draft_player_tyler_backup(game_state: GameState) -> str:
    """
    Selects a player to draft based on the highest rank.
    Args:
        players (List[Player]): A list of Player objects.
    Returns:
        str: The id of the drafted player.
    """
    try:
        year = int(game_state.league_settings.year)
    except:
        year = 2024

    # Filter out already drafted players
    candidate_players = [player for player in game_state.players if not is_drafted(player)]

    if year == 2024:
        # Filter out players from the teams we don't want to draft from
        candidate_players = [player for player in candidate_players if player.professional_team not in BAD_TEAMS_2024]

        # Filter out injured players
        candidate_players = [player for player in candidate_players if player.full_name not in INJURED_LIST_2024]

    roster = get_drafted_team(game_state.players, game_state.current_bot_team_id)
    pickable_slots = set(roster_to_pickable_slots(roster))

    # Select the player with the highest rank (lowest rank number)
    if candidate_players:
        candidate_players.sort(key=lambda p: p.rank)
        for player in candidate_players:
            pos = player.allowed_positions[0]
            if pos in pickable_slots:
                return player.id
        return candidate_players[0].id  # backup
    else:
        return ""  # Return empty string if no undrafted players are available


def solicit_bad_advice(_from: str):
    try:
        url = f"https://raw.githubusercontent.com/mitchwebster/botblitz/ab6f2ce2baa771ba9301a5485846d047bf69ba54/bots/nfl/{_from}-bot.py"
        response = requests.get(url)
        code = response.text

        filename = f"{_from}_bot.py"
        with open(filename, "w") as file:
            file.write(code)
    except Exception as e:
        print(f"Error fetching bot code for {_from}: {e}")
        return None

    try:
        # Dynamically import the module
        spec = importlib.util.spec_from_file_location(f"{_from}_bot", filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{_from}_bot"] = module
        spec.loader.exec_module(module)

        # Now you can use the draft_player function from the dynamically imported module
        draft_player = module.draft_player
        return draft_player
    except Exception as e:
        print(f"Error importing bot code for {_from}: {e}")
        return None

def get_bad_advice(advice_fns: dict, game_state: GameState) -> list[str]:
    import os
    picks = []
    for who, f in advice_fns.items():
        _game_state = copy.deepcopy(game_state)
        try:
            print(f"Running {who}'s bot")
            pick = f(_game_state)
            print(f"{who} picked {pick}")
        except Exception as e:
            print(f"Error getting pick from {who}'s bot: {e}")
            continue
        picks.append(pick)
    return picks

def ensemble_all_the_bad_advice(game_state: GameState):
    who = ["chris", "parker", "harry", "jon", "justin", "matt", "mitch"]
    advice_fns = {_from: solicit_bad_advice(_from) for _from in who}
    advice_fns = {k: v for k, v in advice_fns.items() if v is not None}
    picks = get_bad_advice(advice_fns, game_state)

    # Count the occurrences of each pick, return None if no consensus
    pick_counts = Counter(picks)
    most_common_pick, count = pick_counts.most_common(1)[0] if pick_counts else (None, 0)
    output = most_common_pick if count > 1 else None
    print(f"The group picked {output}, with {count} votes")
    return most_common_pick if count > 1 else None

def propose_add_drop(game_state: GameState) -> AddDropSelection:
    """
    Selects a player to draft based on the highest rank.

    Args:
        players (List[Player]): A list of Player objects.

    Returns:
        str: The id of the drafted player.
    """
    return AddDropSelection(
        player_to_add_id="",
        player_to_drop_id=""
    )