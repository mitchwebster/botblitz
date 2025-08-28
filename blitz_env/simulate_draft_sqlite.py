from typing import Callable, List, Dict
from blitz_env.models import DatabaseManager, Player, Bot, LeagueSettings, GameStatus
from blitz_env.load_players import load_players  # used by init_database only
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import textwrap
import os
import random

def is_drafted(player: Player) -> bool:
    return player.availability in ('DRAFTED', 'ON_HOLD')

# -----------------------------
# (UNCHANGED) Your init_database
# -----------------------------
def init_database(year: int) -> DatabaseManager:
    db = DatabaseManager()
    # Clear existing data
    db.session.query(Player).delete()
    db.session.query(Bot).delete()
    db.session.query(LeagueSettings).delete()
    db.session.query(GameStatus).delete()
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
        player.allowed_positions = list(player_proto.allowed_positions)
        player.availability = 'AVAILABLE'
        db.session.add(player)

    db.session.add(Bot(id="1", draft_order=0, name="Ryan", owner="Ryan", current_waiver_priority=0))
    db.session.add(Bot(id="2", draft_order=1, name="Harry", owner="Harry", current_waiver_priority=1))
    db.session.add(Bot(id="3", draft_order=2, name="Jon", owner="Jon", current_waiver_priority=2))
    db.session.add(Bot(id="4", draft_order=3, name="Chris", owner="Chris", current_waiver_priority=3))
    db.session.add(Bot(id="5", draft_order=4, name="Tyler", owner="Tyler", current_waiver_priority=4))
    db.session.add(Bot(id="6", draft_order=5, name="Mitch", owner="Mitch", current_waiver_priority=5))
    db.session.add(Bot(id="7", draft_order=6, name="Justin", owner="Justin", current_waiver_priority=6))
    db.session.add(Bot(id="8", draft_order=7, name="Matt", owner="Matt", current_waiver_priority=7))
    db.session.add(Bot(id="9", draft_order=8, name="Parker", owner="Parker", current_waiver_priority=8))
    db.session.add(Bot(id="10", draft_order=9, name="Philip", owner="Philp", current_waiver_priority=9))
    
    player_slots = {"QB": 2, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1, "Bench": 3}
    
    # Add league settings
    settings = LeagueSettings()
    settings.num_bots = 10
    settings.is_snake_draft = True
    settings.total_rounds = 15
    settings.points_per_reception = 1.0
    settings.year = year
    settings.player_slots = player_slots
    db.session.add(settings)
    
    # Initialize draft status
    game_status = GameStatus()
    game_status.current_draft_pick = 1
    game_status.current_bot_team_id = "1"  # Start with first team
    db.session.add(game_status)
    
    db.session.commit()
    return db

def default_draft_strategy() -> str:
    """
    Selects a player to draft based on the highest rank.
    Uses the global DB_URL to access the database.

    Returns:
        str: The id of the drafted player.
    """
    db = DatabaseManager()
    try:
        best_player = (
            db.session.query(Player)
            .filter(Player.availability == 'AVAILABLE')
            .order_by(Player.rank)
            .first()
        )
        return best_player.id if best_player else ""
    finally:
        db.close()


def get_picking_team_index(pick: int) -> int:
    """
    Given an absolute pick number (1-based), return the index (0-based) of the bot that should pick.
    Uses LeagueSettings.is_snake_draft and the current number of bots in the DB.
    """
    db = DatabaseManager()
    try:
        settings: LeagueSettings = db.get_league_settings()
        num_bots = len(db.get_all_bots())
        is_snake = settings.is_snake_draft if settings else True

        pick_adj = pick - 1
        round_number = pick_adj // num_bots
        pos_in_round = pick_adj % num_bots

        if is_snake and (round_number % 2 == 1):
            pos_in_round = num_bots - 1 - pos_in_round
        return pos_in_round
    finally:
        db.close()


def get_picking_team_id(pick: int) -> str:
    """
    Map the current pick number to the Bot.id that should pick.
    """
    db = DatabaseManager()
    try:
        bot_index = get_picking_team_index(pick)
        bot = db.get_bot_by_index(bot_index)
        return bot.id if bot else "1"
    finally:
        db.close()


def run_draft(draft_strategy_map: Dict[str, Callable[[], str]]):
    """
    Execute the draft until completion using the provided per-bot strategies.
    """
    db = DatabaseManager()
    try:
        while not db.is_draft_complete():
            status: GameStatus = db.get_game_status()
            current_pick = status.current_draft_pick
            current_bot_id = status.current_bot_id

            draft_strategy = draft_strategy_map.get(current_bot_id, default_draft_strategy)
            player_id = draft_strategy()

            # Validate selection
            player = db.get_player_by_id(player_id) if player_id else None
            if player and is_drafted(player):
                raise Exception(f"Player id: {player_id} already drafted")

            # Apply pick
            if player_id:
                db.draft_player(player_id, current_bot_id, current_pick)

            # Advance to next pick
            next_pick = current_pick + 1
            next_bot_id = get_picking_team_id(next_pick)
            db.update_draft_pick(next_pick, next_bot_id)
    finally:
        db.close()


def simulate_draft(draft_player: Callable[[], str], year: int):
    """
    Initialize the DB (via your already-correct init_database), attach strategies,
    mark one random bot as the user's bot, and run the draft.
    """
    db = init_database(year)
    try:
        draft_strategy_map: Dict[str, Callable[[], str]] = {}
        bots: List[Bot] = db.get_all_bots()

        # Default everyone to the generic strategy
        for bot in bots:
            draft_strategy_map[bot.id] = default_draft_strategy

        # Make a random bot the "User"—use the provided draft callback for that bot
        user_bot = random.choice(bots)
        user_bot.owner = "User"
        user_bot.name = "Your Bot"
        draft_strategy_map[user_bot.id] = draft_player
        db.session.commit()

        run_draft(draft_strategy_map)
    finally:
        db.close()


def wrap_text(text: str, width: int) -> str:
    return '\n'.join(textwrap.wrap(text, width))


def visualize_draft_board():
    """
    Visualize the draft board using DB data.
    Assumes Player.allowed_positions is a JSON array and LeagueSettings.player_slots is a JSON dict.
    """
    db = DatabaseManager()
    try:
        position_colors = {
            'QB': 'lightblue',
            'RB': 'lightgreen',
            'WR': 'lightcoral',
            'TE': 'wheat',
            'DEF': 'lavender',
            'DST': 'lavender',
            'K': 'lightyellow',
            'FLEX': 'lightgrey',
            'BENCH': 'gainsboro',
        }

        bots: List[Bot] = db.get_all_bots()
        settings: LeagueSettings = db.get_league_settings()
        players: List[Player] = db.get_all_players()

        num_bots = len(bots)
        num_rounds = settings.total_rounds if settings else 0

        fig, ax = plt.subplots(figsize=(30, max(1, num_rounds) * 1.5))
        ax.set_xlim(0, max(1, num_bots))
        ax.set_ylim(0, max(1, num_rounds))
        ax.set_aspect('equal')

        font_size = 12

        for player in players:
            if player.availability != 'DRAFTED' or not player.pick_chosen:
                continue

            round_number = (player.pick_chosen - 1) // max(1, num_bots)
            team_index = get_picking_team_index(player.pick_chosen)

            # Choose a display/primary position from allowed_positions
            primary_pos = None
            try:
                if player.allowed_positions and isinstance(player.allowed_positions, list):
                    primary_pos = (player.allowed_positions[0] or "").upper()
            except Exception:
                primary_pos = None

            color = position_colors.get(primary_pos or 'FLEX', 'lightgrey')

            rect = patches.Rectangle(
                (team_index, round_number), 1, 1,
                linewidth=1, edgecolor='gray', facecolor=color
            )
            ax.add_patch(rect)

            # Text inside each pick cell
            # Show all allowed positions if you like: "/".join(player.allowed_positions or [])
            pos_text = primary_pos or 'FLEX'
            player_info = f"{player.full_name}\n{player.professional_team}\n{pos_text}"
            wrapped_text = wrap_text(player_info, 15)
            ax.text(team_index + 0.5, round_number + 0.5, wrapped_text,
                    ha='center', va='center', fontsize=font_size)

        # Labels: bots along x, rounds along y
        ax.set_xticks([i + 0.5 for i in range(num_bots)])
        ax.set_yticks([i + 0.5 for i in range(num_rounds)])
        ax.set_xticklabels([f"{b.name}\n{b.owner}" for b in bots], rotation=0)
        ax.set_yticklabels([f"Round {i+1}" for i in range(num_rounds)])
        ax.xaxis.set_tick_params(labeltop=True)

        plt.gca().invert_yaxis()
        plt.title('Fantasy Draft Board')
        plt.xlabel('Bots')
        plt.ylabel('Rounds')
        plt.tight_layout()
        plt.show()
    finally:
        db.close()
