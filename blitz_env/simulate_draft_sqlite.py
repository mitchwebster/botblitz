from typing import Callable, List
from blitz_env.models import DatabaseManager, Player, FantasyTeam, LeagueSettings, PlayerSlot, DraftStatus
from blitz_env.load_players import load_players
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import textwrap
import random
import copy

# Database URL constant
DB_URL = "sqlite:///draft.db"

def is_drafted(player: Player) -> bool:
    return player.availability == 'DRAFTED' or player.availability == 'ON_HOLD'

def init_team(id: str, name: str, owner: str) -> FantasyTeam:
    team = FantasyTeam()
    team.id = id
    team.name = name
    team.owner = owner
    return team

def init_player_slot(allowed_positions: List[str], team_id: str) -> PlayerSlot:
    player_slot = PlayerSlot()
    player_slot.team_id = team_id
    # We'll handle allowed positions separately since it's a many-to-many relationship
    return player_slot

def default_draft_strategy() -> str:
    """
    Selects a player to draft based on the highest rank.
    Uses the global DB_URL to access the database.

    Returns:
        str: The id of the drafted player.
    """
    db = DatabaseManager(DB_URL)
    try:
        # Get the best available player (lowest rank number)
        best_player = db.session.query(Player).filter(
            Player.availability == 'AVAILABLE'
        ).order_by(Player.rank).first()
        
        return best_player.id if best_player else ""
    finally:
        db.close()

def init_database(year: int) -> DatabaseManager:
    db = DatabaseManager(DB_URL)
    
    # Clear existing data
    db.session.query(DraftStatus).delete()
    db.session.query(PlayerSlot).delete()
    db.session.query(Player).delete()
    db.session.query(FantasyTeam).delete()
    db.session.query(LeagueSettings).delete()
    db.session.commit()
    
    # Load and add players
    players_data = load_players(year)
    for player_proto in players_data:
        player = Player()
        player.id = player_proto.id
        player.full_name = player_proto.full_name
        player.professional_team = player_proto.professional_team
        player.player_bye_week = player_proto.player_bye_week
        player.rank = player_proto.rank
        player.tier = player_proto.tier
        player.position_rank = player_proto.position_rank
        player.position_tier = player_proto.position_tier
        player.gsis_id = player_proto.gsis_id
        player.position = player_proto.allowed_positions[0] if player_proto.allowed_positions else ""
        player.availability = 'AVAILABLE'
        db.session.add(player)
    
    # Add teams
    teams_data = [
        ("1", "A", "Alex"),
        ("2", "B", "Ben"),
        ("3", "C", "Chris"),
        ("4", "D", "Drew"),
        ("5", "E", "Elizabeth"),
        ("6", "F", "Frank"),
        ("7", "G", "Gillian"),
        ("8", "H", "Harry"),
        ("9", "J", "Jon"),
        ("10", "K", "Kevin")
    ]
    
    for team_id, name, owner in teams_data:
        team = init_team(team_id, name, owner)
        db.session.add(team)
        
        # Add player slots for each team
        slot_configs = [
            (["QB"],),
            (["RB"],),
            (["RB"],),
            (["WR"],),
            (["WR"],),
            (["TE"],),
            (["RB", "WR", "TE"],),
            (["K"],),
            (["DST"],),
            (["Bench"],),
            (["Bench"],),
            (["Bench"],),
            (["Bench"],),
            (["Bench"],),
            (["Bench"],),
        ]
        
        for allowed_positions, in slot_configs:
            slot = init_player_slot(allowed_positions, team_id)
            db.session.add(slot)
    
    # Add league settings
    settings = LeagueSettings()
    settings.num_teams = 10
    settings.is_snake_draft = True
    settings.total_rounds = 15
    settings.points_per_reception = 1.0
    settings.year = year
    db.session.add(settings)
    
    # Initialize draft status
    draft_status = DraftStatus()
    draft_status.current_draft_pick = 1
    draft_status.current_bot_team_id = "1"  # Start with first team
    db.session.add(draft_status)
    
    db.session.commit()
    return db

def get_picking_team_index(pick: int) -> int:
    db = DatabaseManager(DB_URL)
    try:
        settings = db.get_league_settings()
        number_of_teams = settings.num_teams
        is_snake_draft = settings.is_snake_draft
        
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
    finally:
        db.close()

def get_picking_team_id(pick: int) -> str:
    db = DatabaseManager(DB_URL)
    try:
        team_index = get_picking_team_index(pick)
        team = db.get_team_by_index(team_index)
        return team.id if team else "1"
    finally:
        db.close()

def run_draft(draft_strategy_map):
    db = DatabaseManager(DB_URL)
    try:
        while not db.is_draft_complete():
            status = db.get_draft_status()
            current_pick = status.current_draft_pick
            current_team_id = status.current_bot_team_id
            
            draft_strategy = draft_strategy_map[current_team_id]
            player_id = draft_strategy()
            
            # Check if player is already drafted
            player = db.get_player_by_id(player_id)
            if player and is_drafted(player):
                raise Exception(f"Player id: {player_id} already drafted")
            
            # Draft the player
            if player_id:
                db.draft_player(player_id, current_team_id, current_pick)
            
            # Update for next pick
            next_pick = current_pick + 1
            next_team_id = get_picking_team_id(next_pick)
            db.update_draft_pick(next_pick, next_team_id)
    finally:
        db.close()

def simulate_draft(draft_player: Callable[[], str], year: int):
    # Initialize the database with game state
    db = init_database(year)
    
    try:
        draft_strategy_map = {}
        teams = db.get_all_teams()
        
        # Set all teams to use default strategy initially
        for team in teams:
            draft_strategy_map[team.id] = default_draft_strategy
        
        # Make random team the user's team
        user_team = random.choice(teams)
        user_team.owner = "User"
        user_team.name = "Your Bot"
        draft_strategy_map[user_team.id] = draft_player
        db.session.commit()
        
        # Run the draft
        run_draft(draft_strategy_map)
        
        # No return needed - data is persisted in database
    finally:
        db.close()

def wrap_text(text, width):
    """Wrap text to fit within a given width."""
    return '\n'.join(textwrap.wrap(text, width))

def visualize_draft_board():
    """Visualize the draft board using data directly from the database"""
    db = DatabaseManager(DB_URL)
    try:
        # Position-based color configuration
        position_colors = {
            'QB': 'lightblue',
            'RB': 'lightgreen',
            'WR': 'lightcoral',
            'TE': 'wheat',
            'DEF': 'lavender',
            'DST': 'lavender',
            'K': 'lightyellow'
        }

        # Get data from database
        teams = db.get_all_teams()
        settings = db.get_league_settings()
        players = db.get_all_players()
        
        num_teams = len(teams)
        num_rounds = settings.total_rounds

        fig, ax = plt.subplots(figsize=(30, num_rounds * 1.5)) 
        ax.set_xlim(0, num_teams)
        ax.set_ylim(0, num_rounds)
        ax.set_aspect('equal')

        font_size = 12

        # Plot each drafted player
        for player in players:
            if player.availability != 'DRAFTED':
                continue
            
            round_number = (player.pick_chosen - 1) // num_teams
            team_index = get_picking_team_index(player.pick_chosen)
            
            # Get position color
            position_color = position_colors.get(player.position, 'lightgrey')

            # Create rectangle for each pick
            rect = patches.Rectangle((team_index, round_number), 1, 1, 
                                   linewidth=1, edgecolor='gray', facecolor=position_color)
            ax.add_patch(rect)

            # Add player info text
            player_info = f"{player.full_name}\n{player.professional_team}\n{player.position}"
            wrapped_text = wrap_text(player_info, 15)
            ax.text(team_index + 0.5, round_number + 0.5, wrapped_text, 
                   ha='center', va='center', fontsize=font_size)

        # Set grid and labels
        ax.set_xticks([i + 0.5 for i in range(num_teams)])
        ax.set_yticks([i + 0.5 for i in range(num_rounds)])
        ax.set_xticklabels([f"{team.name}\n{team.owner}" for team in teams], rotation=0)
        ax.set_yticklabels([f"Round {i+1}" for i in range(num_rounds)])
        ax.xaxis.set_tick_params(labeltop=True)

        plt.gca().invert_yaxis()
        plt.title('Fantasy Draft Board')
        plt.xlabel('Teams')
        plt.ylabel('Rounds')
        plt.tight_layout()
        plt.show()
    finally:
        db.close()