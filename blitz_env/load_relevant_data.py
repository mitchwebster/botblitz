import argparse
import os
import sys
from blitz_env.projections_db import fp_projections
from blitz_env.agent_pb2 import GameState
import pandas as pd
import boto3
import io

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

# Create 4 CSVs, season_projections, weekly_projections, season_stats, weekly_stats
# only consider current_year, current_year-1, current_year-2, and for current_year, only consider weeks 1:current_week
def load_and_save_data(year, week, output_folder):
    s3 = boto3.client('s3')
    bucket_name = 'botblitz'
    years = [year, year - 1, year - 2]

    season_projections_dfs = []
    season_stats_dfs = []
    weekly_projections_dfs = []
    weekly_stats_dfs = []

    for y in years:
        load_season_data(s3, bucket_name, y, season_projections_dfs, season_stats_dfs)
        weeks = get_weeks_to_process(s3, bucket_name, y, year, week)
        for w in weeks:
            load_weekly_data(
                s3, bucket_name, y, w,
                weekly_projections_dfs, weekly_stats_dfs
            )

    save_dataframe(season_projections_dfs, output_folder, 'season_projections.csv')
    save_dataframe(weekly_projections_dfs, output_folder, 'weekly_projections.csv')
    save_dataframe(season_stats_dfs, output_folder, 'season_stats.csv')
    save_dataframe(weekly_stats_dfs, output_folder, 'weekly_stats.csv')

def load_season_data(s3, bucket_name, year, projections_list, stats_list):
    proj_key = f'projections/{year}/draft-projections.csv'
    df_proj = load_csv_from_s3(s3, bucket_name, proj_key)
    if df_proj is not None:
        projections_list.append(df_proj)

    stats_key = f'stats/{year}/season-stats.csv'
    df_stats = load_csv_from_s3(s3, bucket_name, stats_key)
    if df_stats is not None:
        stats_list.append(df_stats)

def get_weeks_to_process(s3, bucket_name, y, current_year, current_week):
    if y == current_year:
        return range(1, current_week + 1)
    else:
        weeks_proj = list_available_weeks(s3, bucket_name, f'projections/{y}/week/', '-projections.csv')
        weeks_stats = list_available_weeks(s3, bucket_name, f'stats/{y}/week/', '-stats.csv')
        weeks = sorted(set(weeks_proj).union(weeks_stats))
        if not weeks:
            print(f"No weekly data found for year {y}.")
        return weeks

def load_weekly_data(s3, bucket_name, y, w, projections_list, stats_list):
    proj_key = f'projections/{y}/week/{w}-projections.csv'
    df_proj = load_csv_from_s3(s3, bucket_name, proj_key)
    if df_proj is not None:
        projections_list.append(df_proj)

    stats_key = f'stats/{y}/week/{w}-stats.csv'
    df_stats = load_csv_from_s3(s3, bucket_name, stats_key)
    if df_stats is not None:
        stats_list.append(df_stats)

def list_available_weeks(s3, bucket_name, prefix, file_suffix):
    keys = list_s3_objects(s3, bucket_name, prefix)
    weeks = []
    for obj in keys:
        key = obj['Key']
        if key.endswith(file_suffix):
            filename = os.path.basename(key)
            week_num = filename.split('-')[0]
            try:
                weeks.append(int(week_num))
            except ValueError:
                continue
    return weeks

def list_s3_objects(s3, bucket_name, prefix):
    continuation_token = None
    keys = []
    while True:
        params = {'Bucket': bucket_name, 'Prefix': prefix}
        if continuation_token:
            params['ContinuationToken'] = continuation_token
        response = s3.list_objects_v2(**params)
        keys.extend(response.get('Contents', []))
        if not response.get('IsTruncated'):
            break
        continuation_token = response.get('NextContinuationToken')
    return keys

def load_csv_from_s3(s3, bucket_name, key):
    try:
        obj = s3.get_object(Bucket=bucket_name, Key=key)
        df = pd.read_csv(io.BytesIO(obj['Body'].read()))
        return df
    except s3.exceptions.NoSuchKey:
        return None
    except Exception as e:
        print(f"Error reading {key}: {e}")
        return None

def save_dataframe(df_list, output_folder, filename):
    if df_list:
        df = pd.concat(df_list, ignore_index=True)
        df.to_csv(os.path.join(output_folder, filename), index=False)
    else:
        print(f"No data loaded for {filename}.")


def main():
    parser = argparse.ArgumentParser(description='Compute and display top teams by best possible score from a GameState protobuf.')
    parser.add_argument('game_state_file', type=str, help='Path to the GameState .bin file')
    parser.add_argument('--output-folder', '-o', type=str, default='.', help='Optional output folder to save projections.csv')

    args = parser.parse_args()

    game_state = load_game_state(game_state_file=args.game_state_file)
    year = game_state.league_settings.year
    # week = game_state.current_fantasy_week
    week = 5
    output_folder = args.output_folder
    print(f"loading year {year} up to week {week}")
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    load_and_save_data(year, week, output_folder)

if __name__ == '__main__':
    main()
