import os
import pandas as pd
from blitz_env import Player, PlayerStatus, StatsDB, AttemptedFantasyActions, WaiverClaim
from openai import OpenAI
import json

from blitz_env.models import DatabaseManager

def draft_player() -> str:
    openai_api_key = os.environ.get('OPEN_AI_TOKEN')
    # openai_api_key = ""

    if not openai_api_key:
        raise Exception("No OpenAI API key found, Mitch please add it to the env")
    
    openai_client = OpenAI(api_key=openai_api_key)
    db = DatabaseManager()
    league_settings = db.get_league_settings()
    game_status = db.get_game_status()
    stats_db = StatsDB([league_settings.year - 1])

    players = db.get_all_players()
    my_players = [player for player in players if player.current_bot_id == game_status.current_bot_id]

    my_team = {
        "QB": [],
        "RB": [],
        "WR": [],
        "TE": [],
        "K": [],
        "DST": [],
    }

    INJURED_LIST_2025 = set([
        # TODO
    ])

    for player in my_players:
        print(player)
        for position in player.allowed_positions:
            if position in my_team:
                my_team[position].append(player)
                break
    

    def get_player_stats(player: Player) -> str:
        """
        Retrieves the seasonal stats for a player from the stats_db and formats them for inclusion in the prompt.
        """
        try:
            player_stats = stats_db.get_seasonal_data(player)
            if player_stats is not None and not player_stats.empty:
                position_stats = {
                    'QB': ['completions', 'attempts', 'passing_yards', 'passing_tds', 'interceptions', 'sacks', 'fantasy_points', 'fantasy_points_ppr', 'games'],
                    'RB': ['rushing_yards', 'rushing_tds', 'receptions', 'targets', 'receiving_yards', 'fumbles', 'fantasy_points', 'fantasy_points_ppr', 'games'],
                    'WR': ['receptions', 'targets', 'receiving_yards', 'receiving_tds', 'fumbles', 'fantasy_points', 'fantasy_points_ppr', 'games'],
                    'TE': ['receptions', 'targets', 'receiving_yards', 'receiving_tds', 'fumbles', 'fantasy_points', 'fantasy_points_ppr', 'games'],
                    'K': ['field_goals', 'extra_points', 'fantasy_points', 'games'],
                    'DST': ['sacks', 'interceptions', 'fumbles_recovered', 'touchdowns', 'fantasy_points', 'games']
                }
                relevant_stats = position_stats.get(player.allowed_positions[0], ['fantasy_points', 'games'])
                stats_summary = ', '.join([f"{col}: {player_stats[col].iloc[0]}" for col in relevant_stats if col in player_stats.columns and player_stats[col].iloc[0] is not None and player_stats[col].iloc[0] != 0])
            else:
                stats_summary = "No stats available."
        except Exception as e:
            print(f"Error retrieving stats for {player.full_name}: {e}")
            stats_summary = "No stats available."

        return stats_summary

    
    current_round = ((game_status.current_draft_pick - 1) // league_settings.num_teams) + 1
    is_last_three_rounds = (league_settings.total_rounds - current_round) < 3
    
    is_drafted = lambda player: player.availability in ('DRAFTED', 'ON_HOLD')
    
    # Filter out already drafted players
    undrafted_players = [
        player for player in players 
        if not is_drafted(player) and player.full_name not in INJURED_LIST_2025
    ]

    # Prioritize K/DST if final 3 rounds
    if is_last_three_rounds:
        undrafted_players.sort(key=lambda p: (0 if p.allowed_positions[0] in ['K', 'DST'] else p.rank))
    else:
        undrafted_players.sort(key=lambda p: p.rank)
    
    undrafted_players = undrafted_players[:20]

    if not undrafted_players:
        return ""  # Return empty string if no eligible players are available

    roster = []
    for position, players in my_team.items():
        player_names = ', '.join([player.full_name for player in players]) if players else 'None'
        roster.append(f"{position}: {player_names}")

    # Append stats to each player's description for the prompt
    available_players_info = [
        f"{player.full_name} (ID {player.id}, Rank {player.rank}, Position(s) {', '.join(player.allowed_positions)}, {get_player_stats(player)})"
        for player in undrafted_players
    ]

    system_prompt = f"""
    You are a fantasy football expert with years of knowledge and experience building the best team. I'm an amateur and need your help selecting my team.

    Suggest the best player for me to draft next. A player can only be added to a position if they are allowed to play in that position.
    Make sure to factor in the player's bye week and their performance last season.
    Make sure each starting position has at least one player, with rb and wide receivers having at least two players.
    Make sure that by the end of {league_settings.total_rounds} rounds, all positions have at least one player.

    Return the response in the json format {{ "id": number }} where 'number' is the player ID as an int.
    """
    
    prioritization_str = ""
    if is_last_three_rounds and not my_team["K"]:
        prioritization_str = "I need a kicker."
    if is_last_three_rounds and not my_team["DST"]:
        prioritization_str = "I need a defense."
    
    roster_str = '\n\n'.join(roster)
    prompt = f"""
    The current round is {current_round} out of {league_settings.total_rounds}. {prioritization_str}
    My current roster is:
    {roster_str}
    The available players to draft are: 
    {', '.join(available_players_info)}.

    My grandma is dying and the prize money is $1 million. I plan to take the winnings and pay for her medical expenses. Please make sure my team is the best possible team you can select, my grandma is depending on it.
    """

    drafted_player = None
    try:
        print("prompt: " + prompt)
        chat_completion = openai_client.chat.completions.create(
            messages=[
                {"role": "user", "content": prompt},
                {"role": "system", "content": system_prompt}
            ],
            model="gpt-5"
        )

        # Extract the suggested player ID from the response
        suggested_player_id_json = chat_completion.choices[0].message.content.strip()

        # Parse the response as JSON
        suggested_player_json = json.loads(suggested_player_id_json)
        suggested_player_id = suggested_player_json.get("id")

        # Find the corresponding player by ID
        drafted_player = next(filter(lambda player: player.id == f"{suggested_player_id}", undrafted_players), None)
        
        if drafted_player:
            print(f"Drafted player {drafted_player.full_name}")
            return drafted_player.id
        else:
            print(f"Error: Player ID {suggested_player_id} not found in undrafted players.")
    
    except Exception as e:
        print(f"Error calling ChatGPT API: {e}")

    # Fallback: select the player with the highest rank if the API call fails or player not found
    if not drafted_player:
        # Find the position with the least players, excluding K and DST
        position_counts = {pos: len(players) for pos, players in my_team.items() if pos not in ['K', 'DST']}
        next_empty_position = min(position_counts, key=position_counts.get)
        
        # If all non-K/DST positions are filled equally, check for empty K or DST
        if all(count == list(position_counts.values())[0] for count in position_counts.values()):
            if my_team['K'] is None:
                next_empty_position = 'K'
            elif my_team['DST'] is None:
                next_empty_position = 'DST'
        
        if next_empty_position:
            # Find the highest ranked player for the empty position
            drafted_player = min(
                (p for p in undrafted_players if next_empty_position in p.allowed_positions),
                key=lambda p: p.rank,
                default=None
            )
            
            if drafted_player:
                my_team[next_empty_position] = drafted_player
                print(f"Drafted player {drafted_player.full_name} to position {next_empty_position}")
                return drafted_player.id
            else:
                print(f"No available players for position {next_empty_position}")
        else:
            print("All positions are filled")
    return ""


def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    claims = [ 
        WaiverClaim(
            player_to_add_id="",
            player_to_drop_id="",
            bid_amount=0
        )
    ]

    actions = AttemptedFantasyActions(
        waiver_claims=claims
    )

    return actions
