from blitz_env import is_drafted, simulate_draft, visualize_draft_board, Player, GameState
from typing import List

# Drafted players for my team
drafted_wrs = []
drafted_rbs = []
drafted_tes = []
drafted_qbs = []
drafted_ks = []
drafted_dsts = []

# Max number of positions to draft
NUM_MAX_WRS = 5
NUM_MAX_RBS = 5
NUM_MAX_TES = 1 # TODO probably drop this to 1?
NUM_MAX_QBS = 2
NUM_MAX_KS = 1
NUM_MAX_DSTS = 1

# Max number of positions based on CSV for 2024 projected players
MAX_POS_RANK_WR = 200
MAX_POS_RANK_RB = 156
MAX_POS_RANK_TE = 97
MAX_POS_RANK_QB = 67
MAX_POS_RANK_K = 34
MAX_POS_RANK_DST = 32
MAX_RANK_ALL_PLAYERS = 586

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
    drafted_players = [player for player in game_state.players if is_drafted(player)]

    # Select the player with the highest rank (lowest rank number)
    if undrafted_players:
        # Default boilerplate
        # drafted_player = min(undrafted_players, key=lambda p: p.rank)
        # drafted_player = undrafted_players.sorted(undrafted_players, key=lambda player:player.)

        print("\n\nHERE IS A NEW ROUND - PICK " + str(game_state.current_pick) + " \n\n")

        drafted_player = experiment(undrafted_players, drafted_players)

        print("Current team: " + str(list(map(lambda p: p.full_name, get_all_drafted_players()))))

        return drafted_player.id
    else:
        return ""  # Return empty string if no undrafted players are available

def get_player_score(player: Player):
  return player.position_rank

def experiment(undrafted_players: List[Player], drafted_players: List[Player]):
  # Filter out players that are un-draftable based on position limits
  undrafted_players = [p for p in undrafted_players if can_draft_position(p)]

  # Sort players according to scoring function
  players_scored = []
  for player in undrafted_players:
    player_score = score_player(player, undrafted_players, drafted_players)
    scored_player = ScoredPlayer(player, player_score)
    players_scored.append(scored_player)

  # Sort players according to scoring function
  # players_ranked = sorted(undrafted_players, key=lambda p: score_player(p))
  players_ranked = sorted(players_scored, key=lambda s_p: s_p.score, reverse=True)

  print(list(map(lambda p: str(p.player.full_name) + " | score: " + str(p.score) + " | need: " + str(get_position_need(p.player)) + " | demand: " + str(get_position_demand(p.player, drafted_players)), players_ranked)))

  # Get best Player object
  best_player = players_ranked[0].player

  # Store the player
  store_player(best_player)

  return best_player

def score_player(player: Player, undrafted_players: List[Player], drafted_players: List[Player]):
  # Default boilerplate
  # return player.rank
  # return player.rank * player.position_rank

  # This worked okay
  # position_multiplier = 1 if (is_wr(player) or is_rb(player)) and player.position_tier == 1 else 2
  # score = (player.rank - player.position_rank) * position_multiplier
  # score = player.rank * 0.3 + player.position_rank * 0.4 + position_need * 0.3

  # This worked well
  # score = get_rank_percentile(player) * 0.3 + get_position_rank_percentile(player) * 0.4 + get_position_need(player) * 0.3

  # Weighted average for player score
  score = get_rank_percentile(player) * 0.35 + get_position_rank_percentile(player) * 0.35 + get_position_need(player) * 0.285 + get_position_demand(player, drafted_players) * 0.015

  return score

def get_rank_percentile(player: Player):
  return (MAX_RANK_ALL_PLAYERS - player.rank + 1) / MAX_RANK_ALL_PLAYERS

def get_position_rank_percentile(player: Player):
  match player.allowed_positions[0]:
    case "WR":
      return (MAX_POS_RANK_WR - player.position_rank + 1) / MAX_POS_RANK_WR
    case "RB":
      return (MAX_POS_RANK_RB - player.position_rank + 1) / MAX_POS_RANK_RB
    case "TE":
      return (MAX_POS_RANK_TE - player.position_rank + 1) / MAX_POS_RANK_TE
    case "QB":
      return (MAX_POS_RANK_QB - player.position_rank + 1) / MAX_POS_RANK_QB
    case "K":
      return (MAX_POS_RANK_K - player.position_rank + 1) / MAX_POS_RANK_K
    case "DST":
      return (MAX_POS_RANK_DST - player.position_rank + 1) / MAX_POS_RANK_DST

def get_position_need(player: Player):
  total_drafted = 0
  max_draftable = 0

  match player.allowed_positions[0]:
    case "WR":
      total_drafted = len(drafted_wrs)
      max_draftable = NUM_MAX_WRS
    case "RB":
      total_drafted = len(drafted_rbs)
      max_draftable = NUM_MAX_RBS
    case "TE":
      total_drafted = len(drafted_tes)
      max_draftable = NUM_MAX_TES
    case "QB":
      total_drafted = len(drafted_qbs)
      max_draftable = NUM_MAX_QBS
    case "K":
      total_drafted = len(drafted_ks)
      max_draftable = NUM_MAX_KS
    case "DST":
      total_drafted = len(drafted_dsts)
      max_draftable = NUM_MAX_DSTS

  percent_position_drafted = total_drafted / max_draftable
  remaining_required_for_position = max_draftable - total_drafted

  need = (1 - percent_position_drafted) * remaining_required_for_position

  return need

def get_position_demand(player: Player, drafted_players: List[Player]):
  total_drafted = 0
  max_draftable = 0

  total_position_rank = 0
  undrafted_players_in_position = [p for p in drafted_players if p.allowed_positions[0] == player.allowed_positions[0]]
  for undrafted_player in undrafted_players_in_position:
    total_position_rank = total_position_rank + undrafted_player.position_rank

  average_position_rank_drafted = total_position_rank / get_max_players_for_position(player)

  return average_position_rank_drafted

def can_draft_position(player: Player):
  match player.allowed_positions[0]:
    case "WR":
      return NUM_MAX_WRS - len(drafted_wrs) > 0
    case "RB":
      return NUM_MAX_RBS - len(drafted_rbs) > 0
    case "TE":
      return NUM_MAX_TES - len(drafted_tes) > 0
    case "QB":
      return NUM_MAX_QBS - len(drafted_qbs) > 0
    case "K":
      return NUM_MAX_KS - len(drafted_ks) > 0
    case "DST":
      return NUM_MAX_DSTS - len(drafted_dsts) > 0

def get_max_players_for_position(player: Player):
  match player.allowed_positions[0]:
    case "WR":
      return NUM_MAX_WRS
    case "RB":
      return NUM_MAX_RBS
    case "TE":
      return NUM_MAX_TES
    case "QB":
      return NUM_MAX_QBS
    case "K":
      return NUM_MAX_KS
    case "DST":
      return NUM_MAX_DSTS

def is_wr(player: Player):
  return player.allowed_positions[0] == "WR"

def is_rb(player: Player):
  return player.allowed_positions[0] == "RB"

def get_all_drafted_players():
  return drafted_wrs + drafted_rbs + drafted_tes + drafted_qbs + drafted_ks + drafted_dsts

def store_player(player: Player):
  match player.allowed_positions[0]:
    case "WR":
      drafted_wrs.append(player)
      return
    case "RB":
      drafted_rbs.append(player)
      return
    case "TE":
      drafted_tes.append(player)
      return
    case "QB":
      drafted_qbs.append(player)
      return
    case "K":
      drafted_ks.append(player)
      return
    case "DST":
      drafted_dsts.append(player)
      return

# Class for containing the Player with their corresponding score
class ScoredPlayer:
  def __init__(self, player: Player, score: float):
      self.player = player
      self.score = score
