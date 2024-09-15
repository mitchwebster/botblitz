#!/usr/bin/env python3

import argparse
import sys
import os
from .stats_db import StatsDB
from .agent_pb2 import GameState, DraftStatus
from rich.console import Console
from rich.table import Table
from rich import box
import numpy as np
# import google.protobuf.internal.decoder as decoder

def get_points(stats_db, player, year, week):
    df = stats_db.get_weekly_data(player)
    return df[(df["season"] == year) & (df["week"] == week)]["fantasy_points_ppr"].iloc[0]

def get_season_points(stats_db, player, year):
    df = stats_db.get_seasonal_data(player)
    try:
        return df[df["season"] == year]["fantasy_points_ppr"].iloc[0]
    except:
        return 0


def get_best_possible_score(stats_db, players, player_slots, year, week):
    total_score = 0
    used_player_ids = set()

    # Sort slots by the size of allowed positions (ascending)
    sorted_slots = sorted(player_slots, key=lambda slot: len(slot.allowed_player_positions))

    # Helper function to find the best player for a given slot
    def find_best_player_for_slot(slot):
        best_player = None
        best_points = -float('inf')

        for player in players:
            if player.id not in used_player_ids and any(pos in slot.allowed_player_positions for pos in player.allowed_positions):
                try:
                    points = get_points(stats_db, player, year, week)
                except IndexError:
                    # Skip players who do not have data for the specified week
                    continue

                if points > best_points:
                    best_points = points
                    best_player = player

        return best_player, best_points

    # Assign players to slots
    for slot in sorted_slots:
        best_player, best_points = find_best_player_for_slot(slot)
        if best_player:
            total_score += best_points
            used_player_ids.add(best_player.id)
            slot.assigned_player_id = best_player.id  # Assign the player to the slot

    return total_score

total_weeks = 17
def get_best_possible_score_season(stats_db, players, player_slots, year):
    total_score = 0.0
    for week in range(1, total_weeks + 1):
        total_score += get_best_possible_score(stats_db, players, player_slots, year, week)
    return total_score

def print_top_teams_by_best_possible_score(game_state, stats_db, year):
    team_scores = []
    
    for team in game_state.teams:
        # Get the players drafted by the team
        team_players = [player for player in game_state.players if player.draft_status.team_id_chosen == team.id]
    
        # Compute the team's best possible score over the season
        best_possible_score = get_best_possible_score_season(stats_db, team_players, game_state.league_settings.slots_per_team, year)
    
        # Append to the list
        team_scores.append((team.owner, best_possible_score))
    
    # Sort the teams by best_possible_score in descending order
    team_scores.sort(key=lambda x: x[1], reverse=True)
    
    # Find the maximum score to normalize the bar lengths
    max_score = team_scores[0][1]
    
    # Determine the width needed for the rank numbers
    rank_width = len(str(len(team_scores)))
    
    # Function to color the bar (optional)
    def color_bar(bar, color_code):
        return f"\033[{color_code}m{bar}\033[0m"
    
    # Print out the teams with a simple bar chart
    print("Top teams by best possible score:")
    for rank, (owner, score) in enumerate(team_scores, start=1):
        bar_length = int((score / max_score) * 50)  # Adjust 50 for the maximum bar length
        bar = '█' * bar_length
        percentage = (score / max_score) * 100
    
        # Optionally color the bar (e.g., green for the top team, blue for others)
        if rank == 1:
            bar = color_bar(bar, '92')  # Bright green
        else:
            bar = color_bar(bar, '94')  # Bright blue

        # Adjust the rank formatting
        print(f"{rank:>{rank_width}}. {owner:<15} | {bar} {score:.2f} points")

def print_draft_board(game_state, stats_db, year):
    console = Console()
    
    # Get the number of teams and prepare the board layout
    num_teams = len(game_state.teams)
    num_rounds = game_state.league_settings.total_rounds

    # Build a mapping of team IDs to team names and owners
    teams = game_state.teams
    team_id_to_info = {team.id: {'name': team.name, 'owner': team.owner} for team in teams}

    # Collect season points for all drafted players
    player_points = {}
    for player in game_state.players:
        if player.draft_status.availability == DraftStatus.Availability.DRAFTED:
            points = get_season_points(stats_db, player, year)
            player_points[player.id] = points

    # Get min and max points
    points_values = list(player_points.values())
    min_points = min(points_values)
    max_points = max(points_values)

    # Function to format player's name (e.g., "C. McCaffrey")
    def format_player_name(full_name):
        names = full_name.split()
        if len(names) >= 2:
            first_initial = names[0][0]
            last_name = ' '.join(names[1:])
            formatted_name = f"{first_initial}. {last_name}"
        else:
            formatted_name = full_name  # If only one name, keep it as is
        return formatted_name

    # Function to get color based on points using a gradient from red to green
    def get_color_for_points(points, min_points, max_points):
        # Normalize the score between 0 and 1
        normalized = (points - min_points) / (max_points - min_points) if max_points > min_points else 0.5
        # Interpolate between red and green
        # Start color (red): (255, 0, 0)
        # End color (green): (0, 255, 0)
        red = int((1 - normalized) * 255)
        green = int(normalized * 255)
        blue = 0
        # Convert RGB to hex string
        color_hex = f"#{red:02x}{green:02x}{blue:02x}"
        return color_hex

    # Create a table with team headers
    table = Table(title="Fantasy Draft Board", box=box.SQUARE)

    # Add columns for each team
    for team in teams:
        team_header = f"{team.name}\n({team.owner})"
        table.add_column(team_header, style="bold", justify="center")

    # Initialize the draft board as a list of lists
    draft_board = [['' for _ in range(num_teams)] for _ in range(num_rounds)]

    # Populate the draft board with picks
    for player in game_state.players:
        if player.draft_status.availability != DraftStatus.Availability.DRAFTED:
            continue
        pick_number = player.draft_status.pick_chosen
        round_number = (pick_number - 1) // num_teams
        pick_in_round = (pick_number - 1) % num_teams

        # Determine the team picking in this slot (handle snake order)
        if round_number % 2 == 0:
            team_index = pick_in_round
        else:
            team_index = num_teams - 1 - pick_in_round

        # Get player's total season points
        points = player_points.get(player.id, 0)

        # Get color based on points
        color = get_color_for_points(points, min_points, max_points)

        # Build the player info string with name, position, and points
        formatted_name = format_player_name(player.full_name)
        position = player.allowed_positions[0] if player.allowed_positions else 'N/A'
        player_info = f"{formatted_name}\n{position}\n{points:.1f} pts"

        # Apply color
        colored_player_info = f"[{color}]{player_info}[/{color}]"

        draft_board[round_number][team_index] = colored_player_info

    # Add rows to the table for each round
    for round_num in range(num_rounds):
        row_picks = draft_board[round_num]
        table.add_row(*row_picks)

    # Print the table
    console.print(table)

def main():
    parser = argparse.ArgumentParser(description='Compute and display top teams by best possible score from a GameState protobuf.')
    parser.add_argument('game_state_file', type=str, help='Path to the GameState .bin file')

    args = parser.parse_args()

    game_state_file = args.game_state_file

    # Check if the file exists
    if not os.path.isfile(game_state_file):
        print(f"Error: File '{game_state_file}' does not exist.")
        sys.exit(1)

    # Read and deserialize the GameState protobuf
    game_state = GameState()

    try:
        with open(game_state_file, 'rb') as f:
            game_state.ParseFromString(f.read())
    except Exception as e:
        print(f"Error reading or parsing '{game_state_file}': {e}")
        sys.exit(1)

    # Create stats_db instance
    stats_db = StatsDB([game_state.league_settings.year], include_k_dst=True)

    # Print the draft board
    print_draft_board(game_state, stats_db=stats_db, year=game_state.league_settings.year)
    
    # Compute and print the top teams
    print_top_teams_by_best_possible_score(game_state, stats_db, game_state.league_settings.year)

if __name__ == '__main__':
    main()
