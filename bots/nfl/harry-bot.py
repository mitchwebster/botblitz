from blitz_env import is_drafted, simulate_draft, visualize_draft_board, Player, GameState
from typing import List

def draft_player(game_state: GameState) -> str:
    """
    Selects a player to draft based on the highest rank, ensuring only one QB is drafted.

    Args:
        game_state (GameState): The current state of the game containing all players.

    Returns:
        str: The id of the drafted player.
    """

    # my team
    draft_id = game_state.current_bot_team_id
    my_team = [player for player in game_state.players if player.draft_status.team_id_chosen == draft_id]

    # declare variables
    drafted_qb_count = 0
    drafted_rb_count = 0
    drafted_wr_count = 0
    drafted_k_count = 0
    drafted_te_count = 0
    drafted_player_count = 0
    rbwr_diff = 0


    # check team for specific positions and create a count
    for player in my_team:
      drafted_rb_count += 1 if "RB" in player.allowed_positions else 0
      drafted_wr_count += 1 if "WR" in player.allowed_positions else 0
      drafted_te_count += 1 if "TE" in player.allowed_positions else 0
      drafted_qb_count += 1 if "QB" in player.allowed_positions else 0
      drafted_k_count += 1 if "K" in player.allowed_positions else 0
      drafted_player_count += 1

    # all players except for panthers and tua and defense
    undrafted_players = [player for player in game_state.players if not is_drafted(player) and player.professional_team != 'CAR']
    # rb and wr list
    undrafted_rbswrs = [player for player in undrafted_players if  player.allowed_positions[0] == 'RB' or player.allowed_positions[0] == 'WR']
    # qb list
    undrafted_qbs = [player for player in undrafted_players if player.allowed_positions[0] == 'QB']
    # rb list
    undrafted_rbs = [player for player in undrafted_players if player.allowed_positions[0] == 'RB']
    # wr list
    undrafted_wrs = [player for player in undrafted_players if player.allowed_positions[0] == 'WR']
    # te list
    undrafted_tes = [player for player in undrafted_players if player.allowed_positions[0] == 'TE']
    # k list
    undrafted_ks = [player for player in undrafted_players if player.allowed_positions[0] == 'K']
    # dst list
    undrafted_dsts = [player for player in undrafted_players if player.allowed_positions[0] == 'DST']

    # variable rbwr_diff calculating the absolute value diff between rb and wr
    rbwr_diff = abs(drafted_rb_count - drafted_wr_count)
  
    if rbwr_diff < 2:
      # prioritizes rbs once all positions have been drafted, and eliminates the picks from round 13-15
      if drafted_rb_count > 1 and drafted_rb_count < 4 and drafted_wr_count > 1 and drafted_te_count > 0 and drafted_qb_count > 0 and drafted_player_count != 13 and drafted_player_count != 14 and drafted_player_count != 12:
          drafted_player = min(undrafted_rbs, key=lambda p: p.rank)
      # selects 1st and 2nd round pick from high adp rank
      elif drafted_player_count < 2:
          drafted_player = min(undrafted_rbswrs, key=lambda p: p.rank)
      # drafts a tight end on either round 3 if rb and wr get picked 1 and 2, or round 4 if 1 and 2 are the same
      elif drafted_te_count == 0:
          drafted_player = min(undrafted_tes, key=lambda p: p.rank)
      # drafts a qb round 4 or 5 after a tight end
      elif drafted_qb_count == 0 and drafted_player_count == 6 or drafted_player_count == 12:
          drafted_player = min(undrafted_qbs, key=lambda p: p.rank)
      # drafts a kicker round 14
      elif drafted_player_count == 13:
          drafted_player = min(undrafted_ks, key=lambda p: p.rank)
      # drafts a DST round 15
      elif drafted_player_count == 14:
          drafted_player = min(undrafted_dsts, key=lambda p: p.rank)
      # if all else fails, draft an rb or wr
      else:
          drafted_player = min(undrafted_rbswrs, key=lambda p: p.rank)
    # if rb and wr are unbalanced in favor of rbs, draft a wr
    elif drafted_rb_count > drafted_wr_count:
          drafted_player = min(undrafted_wrs, key=lambda p: p.rank)
    # if rb and wr are unbalanced in favor of wrs, drafft a rb
    elif drafted_wr_count > drafted_rb_count:
          drafted_player = min(undrafted_rbs, key=lambda p: p.rank)

    # adjust position counts
    # drafted_rb_count += 1 if "RB" in drafted_player.allowed_positions else 0
    # drafted_wr_count += 1 if "WR" in drafted_player.allowed_positions else 0
    # drafted_te_count += 1 if "TE" in drafted_player.allowed_positions else 0
    # drafted_qb_count += 1 if "QB" in drafted_player.allowed_positions else 0
    # drafted_k_count += 1 if "K" in drafted_player.allowed_positions else 0
    # drafted_player_count += 1    
    # print("drafted rb count", drafted_rb_count)
    # print("drafted wr count", drafted_wr_count)
    # print("drafted te count", drafted_te_count)
    # print("drafted qb count", drafted_qb_count)
    # print("drafted k count", drafted_k_count)
    # print("drafted player count", drafted_player_count)
    # print(rbwr_diff)
    # print(game_state.current_bot_team_id)
    # print(drafted_player)
    
    # for player in my_team:
    #   print(player)
    # print(undrafted_players)



    return drafted_player.id



game_state = simulate_draft(draft_player, 2024)


visualize_draft_board(game_state)
