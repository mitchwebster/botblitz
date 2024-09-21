#!/usr/bin/env python3

import argparse
import sys
import os
from blitz_env.stats_db import StatsDB
from blitz_env.agent_pb2 import GameState, PlayerStatus
from blitz_env import load_players

# This file is used for updating a previous game state to include player_ids which were not available pre-season
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

    players = load_players(game_state.league_settings.year)
    new_player_map = {player.id: player for player in players}
    for player in game_state.players:
        if player.id in new_player_map:
            new_player = new_player_map[player.id]
            if player.gsis_id != new_player.gsis_id:
                print(f"Updating ID for {player.full_name}")
                player.gsis_id = new_player.gsis_id

    # Serialize the updated GameState back to the same file
    output_file = game_state_file
    try:
        with open(output_file, 'wb') as f:
            f.write(game_state.SerializeToString())
        print(f"Updated GameState saved to '{output_file}'.")
    except Exception as e:
        print(f"Error writing to '{output_file}': {e}")
        sys.exit(1)

    

if __name__ == '__main__':
    main()
