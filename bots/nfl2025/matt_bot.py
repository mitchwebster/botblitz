from typing import List
from blitz_env import AttemptedFantasyActions, WaiverClaim
from blitz_env.models import DatabaseManager, Player
from sqlalchemy import or_, desc
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

        if team_state.num_drafted_qbs() == 2:
            available_players = db.session.query(Player).filter(
            Player.availability == 'AVAILABLE',
                or_(
                    Player.allowed_positions.contains("WR"),
                    Player.allowed_positions.contains("RB")
                )
            ).order_by(Player.rank).all()

        if remaining_rounds < 5 and team_state.num_drafted_rbs() < 3:
            available_players = db.session.query(Player).filter(
            Player.availability == 'AVAILABLE',
                or_(
                    Player.allowed_positions.contains("RB")
                )
            ).order_by(Player.rank).all()

        if remaining_rounds < 5 and team_state.num_drafted_wrs() < 3:
            available_players = db.session.query(Player).filter(
            Player.availability == 'AVAILABLE',
                or_(
                    Player.allowed_positions.contains("WR")
                )
            ).order_by(Player.rank).all()

        available_player_ids = [player.id for player in available_players]

        # Get preseason projections and filter for available players
        preseason_projections = pd.read_sql(f"""
            SELECT * FROM preseason_projections 
            WHERE year = 2025 
            AND position IN ('wr', 'qb', 'rb')
            ORDER BY FPTS DESC
        """, db.engine)


        # # Debug: Check if the DataFrame has any data
        # print(f"Shape of preseason_projections: {preseason_projections.shape}")
        # print(f"Is DataFrame empty: {preseason_projections.empty}")

        # # Check the raw SQL query first
        # print("Testing raw SQL query...")
        # test_df = pd.read_sql(f"SELECT COUNT(*) as count FROM preseason_projections WHERE year = 2025", db.engine)
        # print(f"Total records for 2025: {test_df['count'].iloc[0]}")

        # # Check what years are actually in the table
        # years_df = pd.read_sql("SELECT DISTINCT year FROM preseason_projections ORDER BY year", db.engine)
        # print(f"Available years: {years_df['year'].tolist()}")

        # # Check what positions are in the table
        # positions_df = pd.read_sql("SELECT DISTINCT position FROM preseason_projections ORDER BY position", db.engine)
        # print(f"Available positions: {positions_df['position'].tolist()}")

        # # Try without filters first
        # all_projections = pd.read_sql("SELECT * FROM preseason_projections LIMIT 5", db.engine)
        # print(f"Sample data:")
        # print(all_projections.head())


        # Iterate through projections and manually check if player is available
        for index, player in preseason_projections.iterrows():
            fantasypros_id = player['fantasypros_id']
            
            # Check if this player is in available_players list
            available_player = next((ap for ap in available_players if ap.id == fantasypros_id), None)
            
            if available_player:
                print(f"Available: {player['player_name']} ({player['position']}) - {player['FPTS']} points")
                # Do whatever you need with this available player
                return available_player.id
        
        return ""
    finally:
        db.close()
    

def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    claims = [ 
        WaiverClaim(
            player_to_add_id="",
            player_to_drop_id="",
            bid_amount=0
        )
    ]

    actions = AttemptedFantasyActions(
        waiver_claims=claims
    )

    return actions




    ### Algorithm
    # Get players 
