#!/usr/bin/env python3

import argparse
import sys
import os
from .stats_db import StatsDB
from .agent_pb2 import GameState
# import google.protobuf.internal.decoder as decoder

def get_points(stats_db, player, year, week):
    df = stats_db.get_weekly_data(player)
    return df[(df["season"] == year) & (df["week"] == week)]["fantasy_points_ppr"].iloc[0]

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

    # print(game_state)

    # Create stats_db instance
    stats_db = StatsDB([game_state.league_settings.year], include_k_dst=True)

    # Compute and print the top teams
    print_top_teams_by_best_possible_score(game_state, stats_db, game_state.league_settings.year)

if __name__ == '__main__':
    main()
