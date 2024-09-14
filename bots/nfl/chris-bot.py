from blitz_env import is_drafted, simulate_draft, visualize_draft_board, Player, GameState, StatsDB, ProjectionsDB
from typing import List
import math

def get_points_week_1(stats_db, player, year):
    weekly_df = stats_db.get_weekly_data(player)
    try:
      return weekly_df[(weekly_df["week"] == 1) & (weekly_df["season"] == year)]["fantasy_points_ppr"].iloc[0]
    except:
      return 0

def get_preseason_projections(projections_db, player, year):
    try:
        return projections_db.get_preseason_projections(player, season=year)["MISC_FPTS"].iloc[0]
    except:
        return 0

def get_week_2_projections(projections_db, player, year):
    try:
        return projections_db.get_weekly_projections(player, season=year, week=2)["MISC_FPTS"].iloc[0]
    except:
        return 0

player_expected_points = None

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

    # Find players currently on team
    team_players = [player for player in game_state.players if player.draft_status.team_id_chosen == game_state.drafting_team_id]

    def get_expected_points(stats_db, projections_db, player):
        preseason_projections = get_preseason_projections(projections_db, player, game_state.league_settings.year)
        week_2_projections = get_week_2_projections(projections_db, player, game_state.league_settings.year)
        week_1_points = get_points_week_1(stats_db, player, game_state.league_settings.year)
        # take the average of preseason_projections, week_2_projections*17, and week_1_points*17 as the projected points
        expected_points = (preseason_projections + week_2_projections*17 + week_1_points*17) / 3.0
        return expected_points

    # This is the expensive API calls.  It needs to be done at least once, but in case this is part of a simulation, cache the values for re-use
    global player_expected_points
    if player_expected_points == None:
        stats_db = StatsDB([game_state.league_settings.year])
        projections_db = ProjectionsDB()
        player_expected_points = {player.id: get_expected_points(stats_db, projections_db, player) for player in game_state.players}


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

    current_round = (game_state.current_pick - 1) // len(game_state.teams)

    # print(current_round)
    # index = 0
    # for player in undrafted_players[0:10]:
    #     print(index, player.full_name, player.allowed_positions[0], score_player(player))
    #     index += 1
    # print()
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