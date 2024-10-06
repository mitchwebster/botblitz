import argparse
import os
import sys
from blitz_env.stats_db import fp_stats_dynamic  # Adjust the import as necessary
import pandas as pd

def load_and_save_stats(year, weeks, output_folder, aws_profile=None):
    # Check if output folder is an S3 path
    is_s3 = output_folder.startswith('s3://')
    if is_s3:
        try:
            import s3fs
        except ImportError:
            print("s3fs is required to write to S3 locations. Please install it using 'pip install s3fs'")
            sys.exit(1)
        s3_kwargs = {}
        if aws_profile:
            s3_kwargs['profile'] = aws_profile
        fs = s3fs.S3FileSystem(**s3_kwargs)
    else:
        # Local filesystem
        fs = None

    for week in weeks:
        isSingleWeek = week != ''
        range = 'week' if isSingleWeek else 'full'
        # Fetch stats for each position using fp_stats_dynamic
        rb_df = fp_stats_dynamic(page='rb', year=year, range=range, week=week, scoring='PPR')

        qb_df = fp_stats_dynamic(page='qb', year=year, range=range, week=week, scoring='PPR')

        te_df = fp_stats_dynamic(page='te', year=year, range=range, week=week, scoring='PPR')

        wr_df = fp_stats_dynamic(page='wr', year=year, range=range, week=week, scoring='PPR')

        k_df = fp_stats_dynamic(page='k', year=year, range=range, week=week, scoring='PPR')

        dst_df = fp_stats_dynamic(page='dst', year=year, range=range, week=week, scoring='PPR')

        # Concatenate all positions
        week_df = pd.concat([rb_df, qb_df, te_df, wr_df, k_df, dst_df], ignore_index=True)
        if isSingleWeek:
            week_df['week'] = week  # Add week information for clarity
        week_df['year'] = year
        week_df.sort_values(by="FPTS", ascending=False, inplace=True)

        # Construct output file path
        file_prefix = f"week/{week}" if isSingleWeek else 'season'
        if is_s3:
            output_file = f"{output_folder}/{year}/{file_prefix}-stats.csv"
            with fs.open(output_file, 'w') as f:
                week_df.to_csv(f, index=False)
        else:
            # Ensure local output directory exists
            output_file = f"{output_folder}/{year}/{file_prefix}-stats.csv"
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            week_df.to_csv(output_file, index=False)

        print(f"Saved stats for week {week} to {output_file}")

def parse_week_range(week_str):
    # Check if the week is in range format, e.g., '1:17'
    if ':' in week_str:
        start_week, end_week = map(int, week_str.split(':'))
        return range(start_week, end_week + 1)
    else:
        # If it's a single week, just return a list with one element
        return [week_str]

def main():
    parser = argparse.ArgumentParser(description='Download and save post-game stats for specified weeks.')
    parser.add_argument('--year', type=int, required=True, help='Year to load data for')
    parser.add_argument('--week', type=str, default='', help='Week or week range to load data for (e.g., "1:18" or "1"). If not set, full season is loaded.')
    parser.add_argument('--output-folder', '-o', type=str, default='.', help='Output folder to save stats.csv')
    parser.add_argument('--profile', type=str, default=None, help='AWS profile to use for S3 operations')

    args = parser.parse_args()

    # Parse the week argument to support ranges
    weeks = parse_week_range(args.week)

    load_and_save_stats(args.year, weeks, args.output_folder, args.profile)

if __name__ == '__main__':
    main()
