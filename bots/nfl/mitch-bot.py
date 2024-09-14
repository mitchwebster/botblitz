from blitz_env import load_players, StatsDB, is_drafted, simulate_draft, visualize_draft_board, Player, GameState
from typing import List
import math
import sys

QB_POS = "QB"
RB_POS = "RB"
WR_POS = "WR"
TE_POS = "TE"
K_POS = "K"
D_POS = "DST"

def get_drafted_team(players, team_id):
    roster = {
        QB_POS : [],
        RB_POS : [],
        WR_POS : [],
        TE_POS : [],
        K_POS : [],
        D_POS : []
    }

    for player in [player for player in players if is_drafted(player) and player.draft_status.team_id_chosen == team_id]:
        main_pos = player.allowed_positions[0]
        roster[main_pos].append(player)

    return roster

def get_target_positions(drafted_team, cur_round, total_rounds):
    target_positions = []

    if total_rounds - cur_round < 2:
        # Last 2 rounds we go for kickers and defense
        if len(drafted_team[K_POS]) < 1:
            target_positions.append(K_POS)

        if len(drafted_team[D_POS]) < 1:
            target_positions.append(D_POS)
    else:
        rb_count = len(drafted_team[RB_POS])
        wr_count = len(drafted_team[WR_POS])

        if rb_count < 5 and (rb_count + wr_count) < 10:
            target_positions.append(RB_POS)

        if wr_count < 5 and (rb_count + wr_count) < 10:
            target_positions.append(WR_POS)

        allowed_qbs = 1 if cur_round > 2 else 2 if cur_round > 5 else 0
        if cur_round > 2 and len(drafted_team[QB_POS]) < allowed_qbs:
            target_positions.append(QB_POS)

        if cur_round > 3 and len(drafted_team[TE_POS]) < 1:
            target_positions.append(TE_POS)
    
    return target_positions

def get_current_round(game_state: GameState) -> int:
    zero_based_round = (game_state.current_pick - 1) // len(game_state.teams)
    return zero_based_round + 1

def list_to_map(list):
    map = {}
    for item in list: 
        map[item] = ""
    
    return map

def player_need(player, existing_players_in_position):
    other_players_penalty = 0 if existing_players_in_position < 2 else existing_players_in_position
    position_rank_penalty = 2 * (player.position_rank // 5)
    return player.rank + other_players_penalty + position_rank_penalty

def draft_player(game_state: GameState) -> str:
    """
    Selects a player to draft based on the highest rank.

    Args:
        players (List[Player]): A list of Player objects.

    Returns:
        str: The id of the drafted player.
    """

    # relevant current game state
    my_team_id = game_state.drafting_team_id
    drafted_team = get_drafted_team(game_state.players, my_team_id)
    cur_round = get_current_round(game_state)

    # positions we are looking for
    target_positions = get_target_positions(drafted_team, cur_round, game_state.league_settings.total_rounds)
    position_map = list_to_map(target_positions)

    # players we are looking for
    undrafted_players = [player for player in game_state.players if not is_drafted(player) and player.allowed_positions[0] in position_map]



    # print(position_map)

    # for k,v in drafted_team.items():
    #     print(k + ": " + str(len(v)))

    # if cur_round == game_state.league_settings.total_rounds:
    #     print(drafted_team)

    # Select the player with the highest rank (lowest rank number)
    selected_player = ""
    cur_min = sys.maxsize
    for player in undrafted_players:
        score = player_need(player, len(drafted_team[player.allowed_positions[0]]))
        if score < cur_min:
            cur_min = score
            selected_player = player.id

    print("Selecting: " + selected_player)
    print("Score: " + str(cur_min))

    if cur_round == game_state.league_settings.total_rounds:
        print(drafted_team)
    
    return selected_player  # Return empty string if no undrafted players are available