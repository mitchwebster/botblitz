from typing import Callable, List, Dict, Tuple
from blitz_env.models import DatabaseManager, Player, Bot, LeagueSettings, GameStatus
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import textwrap
import os
import random
import shutil
from blitz_env.bootstrap_data import get_season_db_path
import pandas as pd


def is_drafted(player: Player) -> bool:
    return player.availability in ('DRAFTED', 'ON_HOLD')


def init_database(year: int):
    """Reset the harness scratch DB from the prebuilt season.db and seed league state.

    Copies the tracked, read-only season.db to the scratch DB that DatabaseManager
    points at, then (re)creates and populates the league-state tables (bots, league
    settings, game status). Stats/projections come from the copied season.db — no
    network, no per-run stats copying.
    """
    season_db = get_season_db_path(year)
    if not os.path.isfile(season_db):
        raise FileNotFoundError(
            f"season.db not found at '{season_db}'. Run "
            f"`make bootstrap-data-build-season YEAR={year}` first."
        )

    # DatabaseManager.DB_URL looks like 'sqlite:///<path>'; copy the prebuilt DB there.
    scratch_path = DatabaseManager.DB_URL.replace("sqlite:///", "", 1)
    if os.path.dirname(scratch_path):
        os.makedirs(os.path.dirname(scratch_path), exist_ok=True)
    shutil.copyfile(season_db, scratch_path)

    db = DatabaseManager()  # create_all() adds the empty league-state tables
    try:
        # fresh league state
        db.session.query(Bot).delete()
        db.session.query(LeagueSettings).delete()
        db.session.query(GameStatus).delete()
        db.session.commit()

        db.session.add(Bot(id="0", draft_order=1, name="Ryan", owner="Ryan", current_waiver_priority=0))
        db.session.add(Bot(id="1", draft_order=2, name="Harry", owner="Harry", current_waiver_priority=0))
        db.session.add(Bot(id="2", draft_order=3, name="Jon", owner="Jon", current_waiver_priority=0))
        db.session.add(Bot(id="3", draft_order=4, name="Chris", owner="Chris", current_waiver_priority=0))
        db.session.add(Bot(id="4", draft_order=5, name="Tyler", owner="Tyler", current_waiver_priority=0))
        db.session.add(Bot(id="5", draft_order=6, name="Mitch", owner="Mitch", current_waiver_priority=0))
        db.session.add(Bot(id="6", draft_order=7, name="Justin", owner="Justin", current_waiver_priority=0))
        db.session.add(Bot(id="7", draft_order=8, name="Matt", owner="Matt", current_waiver_priority=0))
        db.session.add(Bot(id="8", draft_order=9, name="Parker", owner="Parker", current_waiver_priority=0))
        db.session.add(Bot(id="9", draft_order=10, name="Philip", owner="Philp", current_waiver_priority=0))
        db.session.add(Bot(id="10", draft_order=11, name="Ben", owner="Ben", current_waiver_priority=0))
        db.session.add(Bot(id="11", draft_order=12, name="Chris H", owner="Chris H", current_waiver_priority=0))
        db.session.add(Bot(id="12", draft_order=13, name="Jack", owner="Jack", current_waiver_priority=0))

        player_slots = {"QB": 1, "RB": 2, "WR": 2, "SUPERFLEX": 1, "FLEX": 1, "K": 1, "DST": 1, "BENCH": 3}
        settings = LeagueSettings()
        settings.is_snake_draft = True
        settings.total_rounds = sum(player_slots.values())
        settings.points_per_reception = 1.0
        settings.year = year
        settings.player_slots = player_slots
        settings.num_teams = 13
        db.session.add(settings)

        game_status = GameStatus()
        game_status.current_draft_pick = 1
        game_status.current_bot_id = "0"
        game_status.current_fantasy_week = 1
        db.session.add(game_status)

        # players already come from season.db as AVAILABLE; ensure a clean draft state
        db.session.query(Player).update(
            {Player.availability: "AVAILABLE", Player.current_bot_id: None, Player.pick_chosen: None}
        )
        db.session.commit()
    finally:
        db.close()


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
    db = DatabaseManager()
    try:
        settings: LeagueSettings = db.get_league_settings()
        num_bots = len(db.get_all_bots())
        is_snake = settings.is_snake_draft if settings else True

        i = pick - 1  # zero-based index
        round_number = i // num_bots
        pos_in_round = i % num_bots

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
        return bot.id if bot else "0"
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
    init_database(year)
    db = DatabaseManager()
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

        # All other bots use the built-in default_draft_strategy (set above).

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

        fig, ax = plt.subplots(figsize=(30, max(1, num_rounds) * 1.2))
        ax.set_xlim(0, max(1, num_bots))
        ax.set_ylim(0, max(1, num_rounds))
        ax.set_aspect('equal')

        font_size = 10

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
