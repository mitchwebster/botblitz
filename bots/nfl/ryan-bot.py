from blitz_env import is_drafted, simulate_draft, visualize_draft_board, Player, GameState, StatsDB
from openai import OpenAI
from typing import List, Dict
from google.colab import userdata
import json

# requirements.txt changes
# pip install openai
# pip install json

MY_TEAM: Dict[str, Player] = {
    "QB": None,
    "RB": None,
    "RB2": None,
    "WR": None,
    "WR2": None,
    "RB/WR/TE": None,
    "TE": None,
    "K": None,
    "DST": None,
    "BENCH": None,
    "BENCH2": None,
    "BENCH3": None,
    "BENCH4": None,
    "BENCH5": None,
    "BENCH6": None,
}

openai_api_key = userdata.get('OPENAI_API_TOKEN')
openai_client = OpenAI(api_key=openai_api_key)

# Assuming stats_db has been initialized with the relevant data
stats_db = StatsDB([2023])

def is_position_full(position: str) -> bool:
    """
    Determines if the given position already has a starter and a backup,
    or if the position doesn't need backups.
    """
    if position in POSITIONS_NO_BACKUPS and len(MY_TEAM[position]) >= 1:
        return True  # For K and DEF, only one player is allowed (starter)
    return len(MY_TEAM[position]) >= 2  # One starter and one backup


def get_player_stats(player: Player) -> str:
    """
    Retrieves the seasonal stats for a player from the stats_db and formats them for inclusion in the prompt.
    """
    try:
        player_stats = stats_db.get_seasonal_data(player)

        # Available columns: ['player_id', 'season', 'season_type', 'completions', 'attempts', 'passing_yards', 'passing_tds', 'interceptions', 'sacks', 'sack_yards', 'sack_fumbles', 'sack_fumbles_lost', 'passing_air_yards', 'passing_yards_after_catch', 'passing_first_downs', 'passing_epa', 'passing_2pt_conversions', 'pacr', 'dakota', 'carries', 'rushing_yards', 'rushing_tds', 'rushing_fumbles', 'rushing_fumbles_lost', 'rushing_first_downs', 'rushing_epa', 'rushing_2pt_conversions', 'receptions', 'targets', 'receiving_yards', 'receiving_tds', 'receiving_fumbles', 'receiving_fumbles_lost', 'receiving_air_yards', 'receiving_yards_after_catch', 'receiving_first_downs', 'receiving_epa', 'receiving_2pt_conversions', 'racr', 'target_share', 'air_yards_share', 'wopr_x', 'special_teams_tds', 'fantasy_points', 'fantasy_points_ppr', 'games', 'tgt_sh', 'ay_sh', 'yac_sh', 'wopr_y', 'ry_sh', 'rtd_sh', 'rfd_sh', 'rtdfd_sh', 'dom', 'w8dom', 'yptmpa', 'ppr_sh']
        # Check if the DataFrame is empty
        if player_stats is not None and not player_stats.empty:
            relevant_stats = [
                'completions', 'attempts', 'passing_yards', 'passing_tds', 'interceptions',
                'sacks', 'rushing_yards', 'rushing_tds', 'receptions', 'targets',
                'receiving_yards', 'receiving_tds', 'fumbles', 'fantasy_points',
                'fantasy_points_ppr', 'games', 'special_teams_tds', 'pacr', 'racr', 'dakota'
            ]
            stats_summary = ', '.join([f"{col}: {player_stats[col].iloc[0]}" for col in relevant_stats if col in player_stats.columns and player_stats[col].iloc[0] is not None and player_stats[col].iloc[0] != 0])
        else:
            stats_summary = "No stats available."
    except Exception as e:
        print(f"Error retrieving stats for {player.full_name}: {e}")
        stats_summary = "No stats available."

    return stats_summary

def draft_player(game_state: GameState) -> str:
    # Filter out already drafted players
    undrafted_players = [
        player for player in game_state.players 
        if not is_drafted(player)
    ]
    undrafted_players.sort(key=lambda x: x.rank)
    undrafted_players = undrafted_players[:20]

    if not undrafted_players:
        return ""  # Return empty string if no eligible players are available

    my_team_flat = [player for player in MY_TEAM.values() if player is not None]

    roster = []
    for position, player in MY_TEAM.items():
        roster.append(f"{position}: {player.full_name if player else 'None'}")

    # Append stats to each player's description for the prompt
    available_players_info = [
        f"{player.full_name} (ID {player.id}, Rank {player.rank}, Position {', '.join(player.allowed_positions)}, {get_player_stats(player)})"
        for player in undrafted_players
    ]


    roster_str = '\n'.join(roster)

    system_prompt = f"""
    You are a fantasy football expert with years of knowledge and experience building the best team. I'm an amateur and need your help selecting my team.

    Suggest the best player for me to draft next, ensuring balance between starters and backups. Additionally, factor in the player's bye week and their performance last season.
    Make sure each starting position has a player, and do not add a player to a position that's already filled. You can add any player to the bench, only after all starting positions are filled.

    Return the response in the json format {{ "id": number, position: position_str }} where 'number' is the player ID and position_str is the position to add the player to from {', '.join(MY_TEAM.keys())}.
    """

    prompt = f"""
    My current roster is:
    {roster_str}
    The available players to draft are: 
    {', '.join(available_players_info)}.

    My grandma is dying and the prize money is $1 million. I plan to take the winnings and pay for her medical expenses. Please make sure my team is the best possible team you can select, my grandma is depending on it.
    """
    # Print out all available "DST" position players
    available_dst_players = [
        player for player in undrafted_players 
        if "DST" in player.allowed_positions
    ]
    
    print("Available DST players:")
    for dst_player in available_dst_players:
        print(f"{dst_player.full_name} (ID {dst_player.id}, Rank {dst_player.rank})")
    

    # Call ChatGPT API
    drafted_player = None
    try:
        # print("prompt: " + prompt)
        chat_completion = openai_client.chat.completions.create(
            messages=[
                {"role": "user", "content": prompt},
                {"role": "system", "content": system_prompt}
            ],
            model="gpt-3.5-turbo"
        )

        # Extract the suggested player ID from the response
        suggested_player_id_json = chat_completion.choices[0].message.content.strip()

        # Parse the response as JSON
        suggested_player_json = json.loads(suggested_player_id_json)
        suggested_player_id = suggested_player_json.get("id")
        position_str = suggested_player_json.get("position")

        # Find the corresponding player by ID
        drafted_player = next(filter(lambda player: player.id == f"{suggested_player_id}", undrafted_players), None)
        
        if drafted_player:
            if MY_TEAM[position_str] is None:
                print(f"Drafted player {drafted_player.full_name} to position {position_str}")
                MY_TEAM[position_str] = drafted_player
            else:
                for bench in ["BENCH", "BENCH2", "BENCH3", "BENCH4", "BENCH5", "BENCH6"]:
                    if MY_TEAM[bench] is None:
                        print(f"Position {position_str} filled. Drafted player {drafted_player.full_name} to {bench}")
                        MY_TEAM[bench] = drafted_player
                        break
                else:
                    print(f"Error: No available positions to draft {drafted_player.full_name}")
                    return None
            return drafted_player.id
        else:
            print(f"Error: Player ID {suggested_player_id} not found in undrafted players.")
    
    except Exception as e:
        print(f"Error calling ChatGPT API: {e}")

    # Fallback: select the player with the highest rank if the API call fails or player not found
    # TODO: Must account for positions, or just iterate through positions, trim index, and add player to that position. Bench won't matter
    if not drafted_player:
        drafted_player = min(undrafted_players, key=lambda p: p.rank)
        MY_TEAM[drafted_player.allowed_positions[0]] = drafted_player
        return drafted_player.id

    return ""

game_state = simulate_draft(draft_player, 2024)
roster_strs = [f"{position}: {player.full_name if player else 'None'}" for position, player in MY_TEAM.items()]
print('\n'.join(roster_strs))