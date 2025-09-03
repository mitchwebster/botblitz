
from typing import List
from blitz_env import projections_db, GameState, AddDropSelection
from blitz_env.models import DatabaseManager, Player



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
        # Construct my current team
        current_team_id = db.get_game_status().current_bot_id
        team_players = db.session.query(Player).filter(
            Player.availability == 'DRAFTED',
            Player.current_bot_id == current_team_id
        ).order_by(Player.rank).all()
        team_state = TeamState(team_players)

        # Draft player
        available_players = db.session.query(Player).filter(
            Player.availability == 'AVAILABLE',
            Player.allowed_positions.contains("WR")
        ).order_by(Player.rank).all()

        
        return available_players[0].id if available_players and available_players[0] else ""
    
    finally:
        db.close()
    
    

# def propose_add_drop(game_state: GameState) -> AddDropSelection:
#     """
#     Do not add or drop players yet
#     """
#     return AddDropSelection(
#         player_to_add_id="",
#         player_to_drop_id=""
#     )




    ### Algorithm
    # Get players 
