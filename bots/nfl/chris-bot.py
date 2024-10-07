from blitz_env import is_drafted, simulate_draft, visualize_draft_board, Player, GameState, AddDropSelection
from typing import List
import math
import pandas as pd

# Load the CSV files
weekly_projections = pd.read_csv('./data/weekly_projections.csv')
season_projections = pd.read_csv('./data/season_projections.csv')
weekly_stats = pd.read_csv('./data/weekly_stats.csv')

def get_points_week_1(player, year):
    try:
        return weekly_stats[(weekly_stats['year'] == year) & 
                            (weekly_stats['week'] == 1) & 
                            (weekly_stats['fantasypros_id'] == player.fantasypros_id)]['FPTS'].iloc[0]
    except:
        return 0

def get_preseason_projections(player, year):
    try:
        return season_projections[(season_projections['year'] == year) & 
                                  (season_projections['fantasypros_id'] == player.fantasypros_id)]['FPTS'].iloc[0]
    except:
        return 0

def get_week_2_projections(player, year):
    try:
        return weekly_projections[(weekly_projections['year'] == year) & 
                                  (weekly_projections['week'] == 2) & 
                                  (weekly_projections['fantasypros_id'] == player.fantasypros_id)]['FPTS'].iloc[0]
    except:
        return 0

def draft_player(game_state: GameState) -> str:
    """
    Selects a player to draft based on the highest rank.

    Args:
        game_state (GameState): The current game state.

    Returns:
        str: The id of the drafted player.
    """
    # Filter out already drafted players
    undrafted_players = [player for player in game_state.players if not is_drafted(player)]

    # Find players currently on team
    team_players = [player for player in game_state.players if player.status.current_fantasy_team_id == game_state.current_bot_team_id]

    def get_expected_points(player):
        preseason_projections = get_preseason_projections(player, game_state.league_settings.year)
        week_2_projections = get_week_2_projections(player, game_state.league_settings.year)
        week_1_points = get_points_week_1(player, game_state.league_settings.year)
        # take the average of preseason_projections, week_2_projections*17, and week_1_points*17 as the projected points
        expected_points = (preseason_projections + week_2_projections*17 + week_1_points*17) / 3.0
        return expected_points
    player_expected_points = {player.id: get_expected_points(player) for player in game_state.players}

    def score_player(player):
        expected_points = player_expected_points[player.id]
        # Use a multiplier based on count of positions the player can play - the players at this position drafted * 0.5
        player_pos = player.allowed_positions[0]
        position_players_drafted = len([p for p in team_players if player_pos in p.allowed_positions])
        position_player_slots = len([p for p in game_state.league_settings.slots_per_team if player_pos in p.allowed_player_positions])

        score = expected_points * math.exp(position_player_slots - position_players_drafted)
        rank = -1 * player.rank
        return (score, rank)

    # Sort based on precomputed scores
    undrafted_players.sort(key=lambda player: score_player(player), reverse=True)

    current_round = (game_state.current_draft_pick - 1) // len(game_state.teams)

    # if it's the second to last round, pick the top available kicker
    rounds_remaining = game_state.league_settings.total_rounds - current_round
    if rounds_remaining == 1:
        for player in undrafted_players:
            if player.allowed_positions[0] == 'K':
                return player.id
    if rounds_remaining == 2:
        for player in undrafted_players:
            if player.allowed_positions[0] == 'DST':
                return player.id

    # Select the player with the highest rank (lowest rank number)
    if undrafted_players:
        return undrafted_players[0].id
    else:
        return ""  # Return empty string if no undrafted players are available

def propose_add_drop(game_state: GameState) -> AddDropSelection:
    """
    Selects a player to add and drop based on the highest rank.

    Args:
        game_state (GameState): The current game state.

    Returns:
        AddDropSelection: The add/drop selection.
    """
    return AddDropSelection(
        player_to_add_id="",
        player_to_drop_id=""
    )