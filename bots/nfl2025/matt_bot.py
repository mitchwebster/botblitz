
from typing import List
from blitz_env import projections_db, GameState, AddDropSelection
from blitz_env.models import DatabaseManager, Player
from sqlalchemy import or_
import pandas as pd



class TeamState:
    def __init__(self, team_players: List[Player]):
        self.drafted_wrs = [player for player in team_players if player.allowed_positions[0] == "WR"]
        self.drafted_rbs = [player for player in team_players if player.allowed_positions[0] == "RB"]
        self.drafted_qbs = [player for player in team_players if player.allowed_positions[0] == "QB"]
        self.drafted_tes = [player for player in team_players if player.allowed_positions[0] == "TE"]
        self.drafted_k = [player for player in team_players if player.allowed_positions[0] == "K"]
        self.drafted_dst = [player for player in team_players if player.allowed_positions[0] == "DST"]   

    def num_drafted_wrs(self) -> int:
        return len(self.drafted_wrs)

    def num_drafted_rbs(self) -> int:
        return len(self.drafted_rbs)
    
    def num_drafted_qbs(self) -> int:
        return len(self.drafted_qbs)
    
    def num_drafted_tes(self) -> int:
        return len(self.drafted_tes)
    
    def num_drafted_k(self) -> int:
        return len(self.drafted_k)
    
    def num_drafted_dst(self) -> int:
        return len(self.drafted_dst)


def draft_player() -> str:
    db = DatabaseManager()

    try:
        league_settings = db.get_league_settings()
        game_status = db.get_game_status()

        # Draft K and DST in last two rounds
        current_round = ((game_status.current_draft_pick - 1) // league_settings.num_teams) + 1
        remaining_rounds = league_settings.total_rounds - current_round
        if remaining_rounds == 0:
            # Draft DST in last round
            best_dst = db.session.query(Player).filter(
                Player.availability == 'AVAILABLE',
                Player.allowed_positions.contains("DST")
            ).order_by(Player.rank).first()
            return best_dst.id


        if remaining_rounds == 1:
            # Draft K in second to last round
            best_k = db.session.query(Player).filter(
                Player.availability == 'AVAILABLE',
                Player.allowed_positions.contains("K")
            ).order_by(Player.rank).first()
            return best_k.id


        # Construct my current team
        current_team_id = db.get_game_status().current_bot_id
        team_players = db.session.query(Player).filter(
            Player.availability == 'DRAFTED',
            Player.current_bot_id == current_team_id
        ).order_by(Player.rank).all()
        team_state = TeamState(team_players)

        # Draft best player available
        available_players = db.session.query(Player).filter(
            Player.availability == 'AVAILABLE',
            or_(
                Player.allowed_positions.contains("WR"),
                Player.allowed_positions.contains("QB"),
                Player.allowed_positions.contains("RB")
            )
        ).order_by(Player.rank).all()        
        
        return available_players[0].id if available_players and available_players[0] else ""
    
    finally:
        db.close()
    

def propose_add_drop(game_state: GameState) -> AddDropSelection:
    """
    Selects a player to draft based on the highest rank.

    Args:
        players (List[Player]): A list of Player objects.

    Returns:
        str: The id of the drafted player.
    """
    return AddDropSelection(
        player_to_add_id="",
        player_to_drop_id=""
    )




    ### Algorithm
    # Get players 
