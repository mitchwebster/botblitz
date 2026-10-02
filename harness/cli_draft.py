#!/usr/bin/env python3
"""CLI mock draft runner — the terminal equivalent of the old SimulateDraft.ipynb.

Loads a bot's `draft_player` from bots/nfl2025/<file>.py, drafts it against a
field of default-strategy opponents on a given year's season.db, and prints
the resulting draft board + the bot's own roster.

Usage:
    python3 -m harness.cli_draft --bot bots/nfl2025/chris_bot.py --year 2026
"""

import argparse
import importlib.util
import os
import sys

from rich.box import SQUARE
from rich.console import Console
from rich.table import Table

from blitz_env.models import Bot, DatabaseManager
from harness.draft_board_html import write_html
from harness.mock_draft_store import write_to_season_db
from harness.simulate_draft import (
    default_draft_strategy,
    get_picking_team_index,
    init_database,
    run_draft,
)


def load_bot_module(bot_path: str):
    bot_path = os.path.abspath(bot_path)
    spec = importlib.util.spec_from_file_location("user_bot", bot_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def print_draft_board(db: DatabaseManager, user_bot_id: str) -> None:
    """Round-by-round listing (rather than a wide grid) so it stays readable
    regardless of terminal width."""
    console = Console(force_terminal=True)
    bots = db.get_all_bots()
    settings = db.get_league_settings()
    players = db.get_all_players()
    bots_by_draft_order = {b.draft_order: b for b in bots}

    num_teams = len(bots)
    num_rounds = settings.total_rounds

    picks_by_number = {}
    for player in players:
        if player.availability != "DRAFTED" or not player.pick_chosen:
            continue
        picks_by_number[player.pick_chosen] = player

    console.rule(f"[bold]Mock Draft Board - {settings.year}[/bold]")
    for round_num in range(num_rounds):
        table = Table(title=f"Round {round_num + 1}", box=SQUARE, show_lines=False)
        table.add_column("Pick", justify="right")
        table.add_column("Team")
        table.add_column("Player")
        table.add_column("Pos")
        table.add_column("NFL Team")

        for pos_in_round in range(num_teams):
            pick = round_num * num_teams + pos_in_round + 1
            team_index = get_picking_team_index(pick)
            bot = bots_by_draft_order.get(team_index + 1)
            player = picks_by_number.get(pick)
            row_style = "bold green" if bot and bot.id == user_bot_id else None
            if player:
                position = player.allowed_positions[0] if player.allowed_positions else "N/A"
                table.add_row(
                    f"#{pick}", bot.name if bot else "?", player.full_name, position,
                    player.professional_team or "", style=row_style,
                )
            else:
                table.add_row(f"#{pick}", bot.name if bot else "?", "-", "-", "-", style=row_style)

        console.print(table)


def print_roster(db: DatabaseManager, bot: Bot) -> None:
    console = Console(force_terminal=True)
    players = [p for p in db.get_all_players() if p.current_bot_id == bot.id]
    players.sort(key=lambda p: p.pick_chosen or 0)

    table = Table(title=f"{bot.name}'s Roster ({bot.owner})", box=SQUARE)
    table.add_column("Pick", justify="right")
    table.add_column("Player")
    table.add_column("Team")
    table.add_column("Position")
    table.add_column("Overall Rank", justify="right")

    for p in players:
        position = p.allowed_positions[0] if p.allowed_positions else "N/A"
        table.add_row(
            str(p.pick_chosen),
            p.full_name,
            p.professional_team or "",
            position,
            str(p.rank) if p.rank is not None else "N/A",
        )

    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="Run a mock draft with a bot against default-strategy opponents.")
    parser.add_argument("--bot", required=True, help="Path to the bot .py file (must define draft_player()).")
    parser.add_argument("--year", type=int, default=2026, help="Season year to draft against (default 2026).")
    parser.add_argument("--bot-name", default=None, help="Bot name in the league to assign as the user's bot (default: derived from filename, e.g. chris_bot.py -> Chris).")
    parser.add_argument("--html", default=None, help="Also write a grid draft board (teams x rounds) to this HTML file.")
    parser.add_argument("--no-ui", action="store_true", help="Skip writing results into season.db's mock_draft_picks table (written by default -- that's what ux/'s Draft Board tab reads).")
    args = parser.parse_args()

    if not os.path.isfile(args.bot):
        print(f"Error: bot file not found: {args.bot}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading bot from {args.bot}...")
    bot_module = load_bot_module(args.bot)
    if not hasattr(bot_module, "draft_player"):
        print(f"Error: {args.bot} has no draft_player() function.", file=sys.stderr)
        sys.exit(1)

    print(f"Initializing {args.year} season database for a mock draft...")
    init_database(args.year)

    db = DatabaseManager()
    try:
        bots = db.get_all_bots()

        default_name = os.path.basename(args.bot).replace("_bot.py", "").replace(".py", "").capitalize()
        target_name = args.bot_name or default_name
        user_bot = next((b for b in bots if b.name.lower() == target_name.lower()), None)
        if user_bot is None:
            print(f"Warning: no league bot named '{target_name}' found; assigning your bot to the first draft slot instead.")
            user_bot = sorted(bots, key=lambda b: b.draft_order)[0]

        user_bot.owner = "You"
        user_bot.name = f"{default_name} (your bot)"
        db.session.commit()

        strategy_map = {b.id: default_draft_strategy for b in bots}
        strategy_map[user_bot.id] = bot_module.draft_player

        print(f"Running draft: {user_bot.name} vs. {len(bots) - 1} default-strategy opponents...\n")
        run_draft(strategy_map)
        db.session.commit()

        if not args.no_ui:
            n = write_to_season_db(db, user_bot.id, args.year, bot_label=user_bot.name)
            print(f"Wrote {n} picks to season.db's mock_draft_picks table -- see the Draft Board tab in ux/.")

        if args.html:
            out_path = write_html(db, user_bot.id, args.html)
            print(f"Wrote draft board to {out_path}")
            picks = [p for p in db.get_all_players() if p.current_bot_id == user_bot.id]
            picks.sort(key=lambda p: p.pick_chosen or 0)
            summary = ", ".join(f"{p.full_name} ({p.allowed_positions[0] if p.allowed_positions else '?'})" for p in picks)
            print(f"{user_bot.name}'s roster: {summary}")
        else:
            print_draft_board(db, user_bot.id)
            print()
            print_roster(db, user_bot)
    finally:
        db.close()


if __name__ == "__main__":
    main()
