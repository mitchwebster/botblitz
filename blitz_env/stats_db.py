import nfl_data_py as nfl
import pandas as pd
import numpy as np
from typing import List
import requests
from .agent_pb2 import Player
from bs4 import BeautifulSoup
import re

def fp_seasonal_years(page, years):
    dfs= []
    for year in years:
        dfs.append(fp_stats_dynamic(page, year=year))
    return pd.concat(dfs).reset_index()

def fp_weekly_years(page, years):
    dfs = []
    for year in years:
        # Note this may fail for historical data
        for week in range(1, 19):
            try:
                df = fp_stats_dynamic(page, year=year, range="week", week=week)
                df["week"] = week
                dfs.append(df)
            except:
                print(f"Failed to get week {week}")
    return pd.concat(dfs).reset_index()

def fp_stats_dynamic(page, **kwargs):
    url_query = f"https://www.fantasypros.com/nfl/stats/{page}.php"
    params = kwargs
    response = requests.get(url_query, params=params)

    if response.status_code != 200:
        raise Exception(f"Failed to retrieve data: Status code {response.status_code}")

    content = response.content
    soup = BeautifulSoup(content, 'html.parser')

    # Find the table with id 'data'
    table_html = soup.find(id='data')

    # Get the header rows
    header_rows = table_html.find('thead').find_all('tr')
    column_names = []

    # Process all header rows to build the column names
    for row in header_rows:
        cells = row.find_all(['th', 'td'])
        row_headers = []
        for cell in cells:
            # Get text from <small> tag if present
            small_tag = cell.find('small')
            if small_tag:
                header_text = small_tag.get_text(strip=True)
            else:
                header_text = cell.get_text(strip=True)
            colspan = int(cell.get('colspan', 1))
            row_headers.extend([header_text] * colspan)
        # If column_names is empty, initialize it
        if not column_names:
            column_names = row_headers
        else:
            # Combine with previous headers
            column_names = [f"{prev}_{curr}" if prev else curr for prev, curr in zip(column_names, row_headers)]

    # Clean up column names
    column_names = [name.strip().replace(' ', '_') for name in column_names]

    # Get the data rows
    data_rows = table_html.find('tbody').find_all('tr')

    data = []
    for row in data_rows:
        cells = row.find_all('td')
        row_data = []
        # Process the first cell (Rank)
        rank_cell = cells[0]
        rank_text = rank_cell.get_text(strip=True)
        row_data.append(rank_text)
        # Process the second cell (Player)
        player_cell = cells[1]
        # Extract player info
        fp_player_link = player_cell.find('a', class_='fp-player-link')
        if fp_player_link:
            # Get fantasypros_id from class attribute
            fp_player_class = fp_player_link.get('class', [])
            fp_id = None
            for cls in fp_player_class:
                m = re.search(r'fp-id-(\d+)', cls)
                if m:
                    fp_id = m.group(1)
                    break
            if fp_id is None:
                fp_id = fp_player_link.get('fp-player-id', None)
            player_name = fp_player_link.get('fp-player-name', None)
            # The team info is in the text of the cell
            team_text = player_cell.get_text(strip=True)
            team = team_text.replace(fp_player_link.get_text(strip=True), '').strip()
            # Extract team abbreviation from parentheses
            team = re.sub(r'[\(\)]', '', team)
        else:
            fp_id = None
            player_name = None
            team = None
        # Append player info to row_data
        row_data.extend([fp_id, player_name, team])
        # Process the remaining cells
        for cell in cells[2:]:
            text = cell.get_text(strip=True)
            row_data.append(text)
        data.append(row_data)

    # Create column names including player info
    # Remove 'Player' column from headers since we processed it separately
    stats_columns = column_names.copy()
    stats_columns.pop(1)  # Remove 'Player' column

    # Our final columns are:
    # ['Rank', 'fantasypros_id', 'player_name', 'team'] + stats_columns[1:]
    columns = ['Rank', 'fantasypros_id', 'player_name', 'team'] + stats_columns[1:]

    # Create DataFrame
    stats_df = pd.DataFrame(data, columns=columns)

    # Identify numeric columns dynamically
    non_numeric_cols = ['fantasypros_id', 'player_name', 'team']
    for col in stats_df.columns:
        if col not in non_numeric_cols:
            # Clean and convert to numeric
            stats_df[col] = stats_df[col].str.replace(',', '').str.rstrip('%')
            stats_df[col] = pd.to_numeric(stats_df[col], errors='coerce')

    # Convert 'ROST' column to float percentage if it exists
    rost_col = [col for col in stats_df.columns if 'ROST' in col.upper()]
    if rost_col:
        col = rost_col[0]
        stats_df[col] = stats_df[col] / 100.0

    # Reorder columns if needed
    # stats_df = stats_df[['fantasypros_id', 'player_name', 'team'] + [col for col in projections_df.columns if col not in ['fantasypros_id', 'player_name', 'team']]]

    # Return stats DataFrame
    stats_df['fantasy_points_ppr'] = stats_df['FPTS']
    return stats_df

class StatsDB:
    def __init__(self, years: List[int], include_k_dst = False):
        """
        Initialize the StatsDB with a list of years and loads NFL data into memory.

        The data includes weekly, play-by-play (pbp), seasonal data, and player IDs, which are all
        fetched from the nfl_data_py library.

        Args:
        years (List[int]): A list of integers representing years for which data is to be loaded.
        """
        self.years = years
        self.weekly_df = nfl.import_weekly_data(years)
        # self.pbp_df = nfl.import_pbp_data(years)
        self.seasonal_df = nfl.import_seasonal_data(years)
        self.dst_seasonal_df = None
        self.dst_weekly_df = None
        self.k_seasonal_df = None
        self.k_weekly_df = None
        if include_k_dst:
            self.dst_seasonal_df = fp_seasonal_years("dst", years)
            self.dst_weekly_df = fp_weekly_years("dst", years)
            self.k_seasonal_df = fp_seasonal_years("k", years)
            self.k_weekly_df = fp_weekly_years("k", years)

        self.ids_df = nfl.import_ids()

    def get_weekly_data(self, player: Player) -> pd.DataFrame:
        """
        Retrieves weekly data for a specified player.

        Args:
        player (Player): A player protobuf object containing the player's ID.

        Returns:
        pd.DataFrame: A DataFrame containing the weekly data for the specified player.
        """
        if player.allowed_positions[0] == 'K' and self.k_weekly_df is not None:
            return self.k_weekly_df[self.k_weekly_df['fantasypros_id'] == player.id]
        if player.allowed_positions[0] == 'DST' and self.dst_weekly_df is not None:
            return self.dst_weekly_df[self.dst_weekly_df['fantasypros_id'] == player.id]

        return self.weekly_df[self.weekly_df.player_id == player.gsis_id]

    def get_seasonal_data(self, player: Player) -> pd.DataFrame:
        """
        Retrieves seasonal data for a specified player.

        Args:
        player (Player): A player protobuf object containing the player's ID.

        Returns:
        pd.DataFrame: A DataFrame containing the seasonal data for the specified player.
        """
        if player.allowed_positions[0] == 'K' and self.k_seasonal_df is not None:
            return self.k_seasonal_df[self.k_seasonal_df['fantasypros_id'] == player.id]
        if player.allowed_positions[0] == 'DST' and self.dst_seasonal_df is not None:
            return self.dst_seasonal_df[self.dst_seasonal_df['fantasypros_id'] == player.id]
        
        return self.seasonal_df[self.seasonal_df.player_id == player.gsis_id]

    def get_ids(self, player: Player) -> pd.DataFrame:
        """
        Retrieves ID data for a specified player.

        Args:
        player (Player): A player protobuf object containing the player's ID.

        Returns:
        pd.DataFrame: A DataFrame containing the ID mappings for the specified player.
        """
        return self.ids_df[self.ids_df.gsis_id == player.gsis_id]
