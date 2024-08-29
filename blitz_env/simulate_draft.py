from typing import Callable, List, Mapping
from .agent_pb2 import Player, DraftStatus, FantasyTeam, GameState, PlayerSlot
from .blitz_env import load_players
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import textwrap
import random
import copy

def is_drafted(player: Player) -> bool:
    return player.draft_status.availability == DraftStatus.Availability.DRAFTED

def init_team(id: str, name: str, owner: str) -> FantasyTeam:
    team = FantasyTeam()
    team.id = id
    team.name = name
    team.owner = owner
    return team

def init_player_slot(allowed_positions: List[str]) -> List[PlayerSlot]:
    player_slot = PlayerSlot()
    player_slot.allowed_player_positions.extend(allowed_positions)
    return player_slot

def default_draft_strategy(game_state: GameState) -> str:
    """
    Selects a player to draft based on the highest rank.

    Args:
        players (List[Player]): A list of Player objects.

    Returns:
        str: The id of the drafted player.
    """
    # Filter out already drafted players
    undrafted_players = [player for player in game_state.players if not is_drafted(player)]

    # Select the player with the highest rank (lowest rank number)
    if undrafted_players:
        drafted_player = min(undrafted_players, key=lambda p: p.rank)
        return drafted_player.id
    else:
        return ""  # Return empty string if no undrafted players are available

def init_game_state(year) -> GameState:
    players = load_players(year)
    teams = [
        init_team("1", "A", "Alex"),
        init_team("2", "B", "Ben"),
        init_team("3", "C", "Chris"),
        init_team("4", "D", "Drew"),
        init_team("5", "E", "Elizabeth"),
        init_team("6", "F", "Frank"),
        init_team("7", "G", "Gillian"),
        init_team("8", "H", "Harry"),
        init_team("9", "J", "Jon"),
        init_team("10", "K", "Kevin")
    ]
    game_state = GameState()
    game_state.players.extend(players)
    game_state.teams.extend(teams)
    game_state.current_pick = 1
    game_state.drafting_team_id = teams[0].id
    game_state.league_settings.is_snake_draft = True
    game_state.league_settings.num_teams = 10
    game_state.league_settings.total_rounds = 15
    game_state.league_settings.points_per_reception = 1.0
    game_state.league_settings.year = year
    game_state.league_settings.slots_per_team.extend(
        [
            init_player_slot(["QB"]),
            init_player_slot(["RB"]),
            init_player_slot(["RB"]),
            init_player_slot(["WR"]),
            init_player_slot(["WR"]),
            init_player_slot(["TE"]),
            init_player_slot(["RB", "WR", "TE"]),
            init_player_slot(["K"]),
            init_player_slot(["DST"]),
            init_player_slot(["Bench"]),
            init_player_slot(["Bench"]),
            init_player_slot(["Bench"]),
            init_player_slot(["Bench"]),
            init_player_slot(["Bench"]),
            init_player_slot(["Bench"]),
        ]
    )
    return game_state

def get_picking_team_index(game_state: GameState, pick: int) -> int:
    number_of_teams = len(game_state.teams)
    is_snake_draft = game_state.league_settings.is_snake_draft
    # Adjust pick to be zero-based for easier modulo calculations
    pick_adjusted = pick - 1
    
    # Determine the round of the pick
    round_number = pick_adjusted // number_of_teams
    
    # Determine position within the round
    position_in_round = pick_adjusted % number_of_teams
    
    # If the round number is even or it isn't a snake draft, order is straightforward
    # If the round number is odd, order is reversed
    if is_snake_draft and round_number % 2 == 1:
        position_in_round = number_of_teams - 1 - position_in_round
    return position_in_round

def get_picking_team_id(game_state: GameState, pick: int) -> int:
    return game_state.teams[get_picking_team_index(game_state, pick)].id

def run_draft(game_state, draft_strategy_map):
    total_picks = game_state.league_settings.total_rounds * len(game_state.teams)
    while game_state.current_pick <= total_picks:
        draft_strategy = draft_strategy_map[game_state.drafting_team_id]
        
        player_id = draft_strategy(copy.deepcopy(game_state))
        for player in game_state.players:
            if player.id == player_id:
                if is_drafted(player):
                    raise Exception(f"Player id: {player_id} already drafted")
                player.draft_status.availability = DraftStatus.DRAFTED
                player.draft_status.pick_chosen = game_state.current_pick
                player.draft_status.team_id_chosen = game_state.drafting_team_id 
        # update for next pick
        game_state.current_pick += 1
        game_state.drafting_team_id = get_picking_team_id(game_state, game_state.current_pick)

def simulate_draft(draft_player: Callable[[List[Player]], str], year: int):
    game_state = init_game_state(year)
    draft_strategy_map = {}

    for team in game_state.teams:
        draft_strategy_map[team.id] = default_draft_strategy
    # make random team the user's team
    user_team = random.choice(game_state.teams)
    user_team.owner = "User"
    user_team.name = "Your Bot"
    draft_strategy_map[user_team.id] = draft_player

    run_draft(game_state, draft_strategy_map)
    return game_state

def wrap_text(text, width):
    """Wrap text to fit within a given width."""
    return '\n'.join(textwrap.wrap(text, width))

def visualize_draft_board(game_state: GameState):
    # Position-based color configuration (you can customize this)
    position_colors = {
        'QB': 'lightblue',
        'RB': 'lightgreen',
        'WR': 'lightcoral',
        'TE': 'wheat',
        'DEF': 'lavender'
    }

    # Get the number of teams and prepare the board layout
    num_teams = len(game_state.teams)
    num_rounds = game_state.league_settings.total_rounds

    fig, ax = plt.subplots(figsize=(20, num_rounds )) 
    ax.set_xlim(0, num_teams)
    ax.set_ylim(0, num_rounds)
    ax.set_aspect('equal')  # This makes each box have equal width and height

    # Use a smaller font size if needed
    font_size = 7

    # Plotting each player
    for player in game_state.players:
        if player.draft_status.availability != DraftStatus.Availability.DRAFTED:
            continue
        round_number = (player.draft_status.pick_chosen - 1) // num_teams
        team_index = get_picking_team_index(game_state, player.draft_status.pick_chosen)
        # Determine the color based on the first allowed position (assuming the position list is not empty)
        position_color = position_colors.get(player.allowed_positions[0], 'lightgrey')  # Default to lightgrey if no match

        # Create a rectangle for each pick
        rect = patches.Rectangle((team_index, round_number), 1, 1, linewidth=1, edgecolor='gray', facecolor=position_color)
        ax.add_patch(rect)

        # Add player info text over the rectangle
        player_info = f"{player.full_name}\n{player.professional_team}\n{player.allowed_positions[0]}"
        wrapped_text = wrap_text(player_info, 15)
        ax.text(team_index + 0.5, round_number + 0.5, wrapped_text, ha='center', va='center', fontsize=font_size)

    # Set the grid and labels
    ax.set_xticks([i + 0.5 for i in range(num_teams)])
    ax.set_yticks([i + 0.5 for i in range(num_rounds)])
    ax.set_xticklabels([f"{team.name}\n{team.owner}" for team in game_state.teams], rotation=0)
    ax.set_yticklabels([f"Round {i+1}" for i in range(num_rounds)])
    ax.xaxis.set_tick_params(labeltop=True)  # Display team names at the top

    plt.gca().invert_yaxis()  # Ensure that the first round starts at the top
    plt.title('Fantasy Draft Board')
    plt.xlabel('Teams')
    plt.ylabel('Rounds')
    plt.tight_layout()
    plt.show()