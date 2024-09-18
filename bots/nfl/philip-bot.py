from blitz_env import is_drafted, simulate_draft, visualize_draft_board, Player, GameState
from typing import List
import collections
import copy

QB_POS = "QB"
RB_POS = "RB"
WR_POS = "WR"
TE_POS = "TE"
K_POS = "K"
D_POS = "DST"

def draft_player(game_state: GameState) -> str:
    """
    Selects a player to draft based on the highest rank.

    Args:
        players (List[Player]): A list of Player objects.

    Returns:
        str: The id of the drafted player.
    """

    undrafted_players = [player for player in game_state.players if not is_drafted(player)]

    def get_player_rank_by_id(player_id: str) -> int:
        """
        Retrieves the rank of a player based on their id.

        Args:
            players (List[Player]): A list of Player objects.
            player_id (str): The id of the player.

        Returns:
            int: The rank of the player.
        """
        for player in undrafted_players:
            if player.id == player_id:
                return player.rank
        return 100000

    # Collect drafted players from other bots
    suggested_players = []
    bot_directory = "/Users/philip/Developer/botblitz/bots/nfl/"
    for bot_file in os.listdir(bot_directory):
        if bot_file.endswith(".py") and bot_file != "philip-bot.py":
            bot_path = os.path.join(bot_directory, bot_file)
            bot_module = importlib.import_module(bot_path)

            try:

                suggested_players += bot_module.draft_player(game_state)
                print(f"Suggestion from {bot_file}: {suggested_players[-1]}")
            except Exception as e:
                print(f"Error occurred while calling draft_player in {bot_file}: {e}")


    # Count the number of times each player is suggested
    player_counts = collections.Counter(suggested_players)

    # Find the player(s) suggested the highest number of times
    max_count = max(player_counts.values())
    most_suggested_players = [player for player, count in player_counts.items() if count == max_count]

    # If there is more than one player suggested the highest number of times, select the player with the lowest rank
    if len(most_suggested_players) > 1:
        drafted_player = min(most_suggested_players, key=lambda p: get_player_rank_by_id(p))
    else:
        drafted_player = min(suggested_players, key=lambda p: get_player_rank_by_id(p))

    return drafted_player