import nfl_data_py as nfl
import pandas as pd
from typing import List
from .agent_pb2 import Player  # Ensure this import path is correct based on your project structure

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
        self.weekly_df = nfl.import_weekly_data(years)
        # self.pbp_df = nfl.import_pbp_data(years)
        self.seasonal_df = nfl.import_seasonal_data(years)
        self.ids_df = nfl.import_ids()

    def get_weekly_data(self, player: Player) -> pd.DataFrame:
        """
        Retrieves weekly data for a specified player.

        Args:
        player (Player): A player protobuf object containing the player's ID.

        Returns:
        pd.DataFrame: A DataFrame containing the weekly data for the specified player.
        """
        return self.weekly_df[self.weekly_df.player_id == player.id]

    def get_seasonal_data(self, player: Player) -> pd.DataFrame:
        """
        Retrieves seasonal data for a specified player.

        Args:
        player (Player): A player protobuf object containing the player's ID.

        Returns:
        pd.DataFrame: A DataFrame containing the seasonal data for the specified player.
        """
        return self.seasonal_df[self.seasonal_df.player_id == player.id]

    def get_ids(self, player: Player) -> pd.DataFrame:
        """
        Retrieves ID data for a specified player.

        Args:
        player (Player): A player protobuf object containing the player's ID.

        Returns:
        pd.DataFrame: A DataFrame containing the ID mappings for the specified player.
        """
        return self.ids_df[self.ids_df.gsis_id == player.id]
