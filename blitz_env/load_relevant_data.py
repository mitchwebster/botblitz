import argparse
import os
import sys
from blitz_env.projections_db import fp_projections
from blitz_env.agent_pb2 import GameState
import pandas as pd

def load_game_state(game_state_file: str) -> GameState:
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
    return game_state

def main():
    parser = argparse.ArgumentParser(description='Compute and display top teams by best possible score from a GameState protobuf.')
    parser.add_argument('game_state_file', type=str, help='Path to the GameState .bin file')
    parser.add_argument('--output-folder', '-o', type=str, default='.', help='Optional output folder to save projections.csv')

    args = parser.parse_args()

    game_state = load_game_state(game_state_file=args.game_state_file)

    year = game_state.league_settings.year
    rb_df = fp_projections(page='rb', sport='nfl', year=str(year), week='draft', scoring='PPR')
    qb_df = fp_projections(page='qb', sport='nfl', year=str(year), week='draft', scoring='PPR')
    te_df = fp_projections(page='te', sport='nfl', year=str(year), week='draft', scoring='PPR')
    wr_df = fp_projections(page='wr', sport='nfl', year=str(year), week='draft', scoring='PPR')
    k_df = fp_projections(page='k', sport='nfl', year=str(year), week='draft', scoring='PPR')
    dst_df = fp_projections(page='dst', sport='nfl', year=str(year), week='draft', scoring='PPR')
    season_projections_df = pd.concat([rb_df, qb_df, te_df, wr_df, k_df, dst_df], ignore_index=True)
    season_projections_df['position'] = season_projections_df['position'].str.upper()
    season_projections_df.sort_values(by="FPTS", ascending=False, inplace=True)

    # Ensure output folder exists
    output_folder = args.output_folder
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    
    # Save the projections.csv in the output folder
    output_file = os.path.join(output_folder, "season-projections.csv")
    season_projections_df.to_csv(output_file, index=False)

if __name__ == '__main__':
    main()
