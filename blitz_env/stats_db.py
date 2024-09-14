import nfl_data_py as nfl
import pandas as pd
import numpy as np
from typing import List
import requests
from blitz_env.agent_pb2 import Player
from blitz_env.projections_db import fp_projections_parse


def import_def_seasonal_data(
        kwargs
    ):
    url_query = f"https://www.fantasypros.com/nfl/stats/dst.php"
    params = kwargs
    response = requests.get(url_query, params=params)

    response_obj = {
        'content': response.content,
        'query': response.url,
        'sport': 'nfl',
        'response': response
    }

    parsed_projections = fp_projections_parse(response_obj)
    
    return parsed_projections['projections']


def import_weekly_data(
        years, 
        columns=None, 
        downcast=True,
        player_type="offense"
    ):
    """Imports weekly player data
    
    Args:
        years (List[int]): years to get weekly data for
        columns (List[str]): only return these columns
        downcast (bool): convert float64 to float32, default True
        player_type (str): one of "offense" (default), "kicking", "def" 
    Returns:
        DataFrame
    """
    
    # check variable types
    if not isinstance(years, (list, range)):
        raise ValueError('Input must be list or range.')
        
    if min(years) < 1999:
        raise ValueError('Data not available before 1999.')
    
    if not columns:
        columns = []

    player_type_prefix = ""
    if player_type == "def":
        player_type_prefix = "_def"
    elif player_type == "kicking":
        player_type_prefix = "_kicking"
    url = r'https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats{0}_{1}.parquet'

    data = pd.concat([pd.read_parquet(url.format(player_type_prefix, x), engine='auto') for x in years])        

    if columns:
        data = data[columns]

    # converts float64 to float32, saves ~30% memory
    if downcast:
        print('Downcasting floats.')
        cols = data.select_dtypes(include=[np.float64]).columns
        data[cols] = data[cols].astype(np.float32)

    return data


def import_seasonal_data(
        years,
        s_type='REG',
        player_type='offense'):
    """Imports seasonal player data
    
    Args:
        years (List[int]): years to get seasonal data for
        s_type (str): season type to include in average ('ALL','REG','POST')
        player_type (str): one of "offense" (default), "kicking", "def" 
    Returns:
        DataFrame
    """
    
    # check variable types
    if not isinstance(years, (list, range)):
        raise ValueError('years input must be list or range.')
        
    if min(years) < 1999:
        raise ValueError('Data not available before 1999.')
        
    if s_type not in ('REG','ALL','POST'):
        raise ValueError('Only REG, ALL, POST allowed for s_type.')
    

    player_type_prefix = ""
    if player_type == "def":
        player_type_prefix = "_def"
    elif player_type == "kicking":
        player_type_prefix = "_kicking"
    url = r'https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats{0}_season_{1}.parquet'

    data = pd.concat([pd.read_parquet(url.format(player_type_prefix, x), engine='auto') for x in years])

    if s_type == 'REG':
        data = data[data['season_type'] == 'REG']
    if s_type == 'POST':
        data = data[data['season_type'] == 'POST']
    
    return data

def import_ids(columns=None, ids=None):
    """Import mapping table of ids for most major data providers
    
    Args:
        columns (List[str]): list of columns to return
        ids (List[str]): list of specific ids to return
        
    Returns:
        DataFrame
    """
    
    # create list of id options
    avail_ids = ['mfl_id', 'sportradar_id', 'fantasypros_id', 'gsis_id', 'pff_id',
       'sleeper_id', 'nfl_id', 'espn_id', 'yahoo_id', 'fleaflicker_id',
       'cbs_id', 'rotowire_id', 'rotoworld_id', 'ktc_id', 'pfr_id',
       'cfbref_id', 'stats_id', 'stats_global_id', 'fantasy_data_id']
    avail_sites = [x[:-3] for x in avail_ids]
    
    # check variable types
    if columns is None:
        columns = []
    
    if ids is None:
        ids = []

    if not isinstance(columns, list):
        raise ValueError('columns variable must be list.')
        
    if not isinstance(ids, list):
        raise ValueError('ids variable must be list.')
        
    # confirm id is in table
    if False in [x in avail_sites for x in ids]:
        raise ValueError('ids variable can only contain ' + ', '.join(avail_sites))
        
    # import data
    df = pd.read_csv(r'https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv')
    
    rem_cols = [x for x in df.columns if x not in avail_ids]
    tgt_ids = [x + '_id' for x in ids]
        
    # filter df to just specified columns
    if len(columns) > 0 and len(ids) > 0:
        df = df[set(tgt_ids + columns)]
    elif len(columns) > 0 and len(ids) == 0:
        df = df[set(avail_ids + columns)]
    elif len(columns) == 0 and len(ids) > 0:
        df = df[set(tgt_ids + rem_cols)]
    
    return df

class StatsDB:
    def __init__(self, years: List[int]):
        """
        Initialize the StatsDB with a list of years and loads NFL data into memory.

        The data includes weekly, play-by-play (pbp), seasonal data, and player IDs, which are all
        fetched from the nfl_data_py library.

        Args:
        years (List[int]): A list of integers representing years for which data is to be loaded.
        """
        self.years = years
        self.weekly_df = import_weekly_data(years)
        # self.pbp_df = nfl.import_pbp_data(years)
        self.seasonal_df = import_seasonal_data(years)
        self.ids_df = import_ids()

    def get_weekly_data(self, player: Player) -> pd.DataFrame:
        """
        Retrieves weekly data for a specified player.

        Args:
        player (Player): A player protobuf object containing the player's ID.

        Returns:
        pd.DataFrame: A DataFrame containing the weekly data for the specified player.
        """
        return self.weekly_df[self.weekly_df.player_id == player.gsis_id]

    def get_seasonal_data(self, player: Player) -> pd.DataFrame:
        """
        Retrieves seasonal data for a specified player.

        Args:
        player (Player): A player protobuf object containing the player's ID.

        Returns:
        pd.DataFrame: A DataFrame containing the seasonal data for the specified player.
        """
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
