import os
import pandas as pd
import numpy as np
import json
from typing import Dict, List, Tuple, Optional
from openai import OpenAI
from blitz_env.models import DatabaseManager
from blitz_env.agent_pb2 import GameState, AddDropSelection

class PhilipFantasyBot:
    """
    Philip's Fantasy Football AI Bot
    
    This bot uses a sophisticated strategy combining:
    - Value-based drafting (VBD)
    - Position scarcity analysis
    - Team needs assessment
    - AI-powered decision making
    - Risk management
    """
    
    def __init__(self):
        self.db = DatabaseManager()
        self.openai_client = None
        self.setup_openai()
        
    def setup_openai(self):
        """Initialize OpenAI client if token is available"""
        token = os.environ.get('PHILIP_TOKEN')
        if token:
            try:
                self.openai_client = OpenAI(api_key=token)
            except Exception as e:
                print(f"Failed to initialize OpenAI client: {e}")
                self.openai_client = None
    
    def get_league_settings(self):
        """Get current league settings"""
        return self.db.get_league_settings()
    
    def get_game_status(self):
        """Get current game status"""
        return self.db.get_game_status()
    
    def get_my_team(self) -> pd.DataFrame:
        """Get current team roster"""
        game_status = self.get_game_status()
        players_df = pd.read_sql("SELECT * FROM players", self.db.engine)
        return players_df[players_df["current_bot_id"] == game_status.current_bot_id]
    
    def get_available_players(self) -> pd.DataFrame:
        """Get all available (undrafted) players"""
        players_df = pd.read_sql("SELECT * FROM players", self.db.engine)
        return players_df[players_df["availability"] == "AVAILABLE"]
    
    def get_projections_data(self) -> pd.DataFrame:
        """Get preseason projections data"""
        year = self.get_league_settings().year
        query = f"SELECT * FROM preseason_projections WHERE year = {year}"
        return pd.read_sql(query, self.db.engine)
    
    def calculate_position_scarcity(self, available_players: pd.DataFrame) -> Dict[str, float]:
        """Calculate position scarcity scores based on available players and expected demand"""
        # Expected draft counts by position (typical fantasy football)
        expected_draft_counts = {
            "RB": 52,  # 2 starters + 4-5 bench per team
            "WR": 60,  # 2-3 starters + 4-5 bench per team  
            "QB": 32,  # 1 starter + 1-2 bench per team
            "TE": 24,  # 1 starter + 1-2 bench per team
            "K": 13,   # 1 per team
            "DST": 13  # 1 per team
        }
        
        scarcity_scores = {}
        for pos in expected_draft_counts:
            pos_players = available_players[available_players["allowed_positions"].apply(
                lambda x: pos in json.loads(x) if x else False
            )]
            
            if len(pos_players) > 0:
                # Calculate scarcity: fewer available players = higher scarcity
                available_count = len(pos_players)
                expected_count = expected_draft_counts[pos]
                scarcity = max(0, (expected_count - available_count) / expected_count)
                scarcity_scores[pos] = scarcity
            else:
                scarcity_scores[pos] = 1.0  # No players available = maximum scarcity
                
        return scarcity_scores
    
    def calculate_player_value(self, player: pd.Series, projections_df: pd.DataFrame, 
                             scarcity_scores: Dict[str, float]) -> float:
        """Calculate comprehensive player value score"""
        try:
            # Get player's primary position
            positions = json.loads(player["allowed_positions"]) if player["allowed_positions"] else []
            if not positions:
                return -1000  # Invalid player
            
            primary_pos = positions[0]
            
            # Base value from projections
            player_projection = projections_df[projections_df["fantasypros_id"] == player["id"]]
            if player_projection.empty:
                return -1000  # No projection data
            
            base_fpts = float(player_projection.iloc[0]["FPTS"])
            
            # Position scarcity multiplier
            scarcity_multiplier = 1 + (scarcity_scores.get(primary_pos, 0) * 0.5)
            
            # Rank-based value (lower rank = higher value)
            rank_value = max(0, 100 - player["rank"])
            
            # Tier-based value
            tier_value = max(0, 10 - player["position_tier"]) * 10
            
            # Bye week consideration (penalty for multiple players on same bye)
            bye_penalty = 0
            my_team = self.get_my_team()
            if not my_team.empty:
                bye_weeks = my_team["player_bye_week"].dropna()
                if player["player_bye_week"] in bye_weeks.values:
                    bye_penalty = 20
            
            # Calculate final value
            value = (base_fpts * scarcity_multiplier) + rank_value + tier_value - bye_penalty
            
            return value
            
        except Exception as e:
            print(f"Error calculating value for player {player.get('full_name', 'Unknown')}: {e}")
            return -1000
    
    def get_team_needs(self) -> Dict[str, int]:
        """Analyze current team needs by position"""
        my_team = self.get_my_team()
        league_settings = self.get_league_settings()
        
        # Parse required roster slots
        required_slots = json.loads(league_settings.player_slots) if league_settings.player_slots else {}
        
        # Count current players by position
        position_counts = {}
        for _, player in my_team.iterrows():
            positions = json.loads(player["allowed_positions"]) if player["allowed_positions"] else []
            for pos in positions:
                position_counts[pos] = position_counts.get(pos, 0) + 1
        
        # Calculate needs
        needs = {}
        for pos, required in required_slots.items():
            current = position_counts.get(pos, 0)
            needs[pos] = max(0, required - current)
        
        return needs
    
    def get_ai_draft_recommendation(self, available_players: pd.DataFrame, 
                                   team_needs: Dict[str, int], 
                                   current_round: int, 
                                   total_rounds: int) -> Optional[str]:
        """Use AI to get draft recommendation if OpenAI is available"""
        if not self.openai_client:
            return None
            
        try:
            # Prepare context for AI
            my_team = self.get_my_team()
            team_summary = []
            
            for _, player in my_team.iterrows():
                positions = json.loads(player["allowed_positions"]) if player["allowed_positions"] else []
                pos_str = ", ".join(positions) if positions else "Unknown"
                team_summary.append(f"{player['full_name']} ({pos_str}) - Rank {player['rank']}")
            
            # Top available players
            top_players = available_players.nlargest(20, "rank")["full_name"].tolist()
            
            prompt = f"""
            You are a fantasy football expert helping with draft strategy for the 2025 NFL season.
            
            Current Situation:
            - Round {current_round} of {total_rounds}
            - Team needs: {json.dumps(team_needs)}
            - Current roster: {', '.join(team_summary) if team_summary else 'Empty team'}
            
            Available players (top 20 by rank): {', '.join(top_players)}
            
            Strategy priorities:
            1. Fill required starting positions first
            2. Consider position scarcity
            3. Build depth at RB/WR
            4. Avoid multiple players on same bye week
            5. Take best available player in later rounds
            
            Return ONLY the player name (exactly as shown) that should be drafted next.
            """
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=50,
                temperature=0.1
            )
            
            recommended_player = response.choices[0].message.content.strip()
            
            # Find the player ID
            player_match = available_players[available_players["full_name"] == recommended_player]
            if not player_match.empty:
                return player_match.iloc[0]["id"]
                
        except Exception as e:
            print(f"AI recommendation failed: {e}")
            
        return None
    
    def draft_player(self) -> str:
        """
        Main draft function - selects the best available player
        
        Returns:
            str: Player ID to draft
        """
        try:
            # Get current game state
            game_status = self.get_game_status()
            league_settings = self.get_league_settings()
            current_round = ((game_status.current_draft_pick - 1) // league_settings.num_teams) + 1
            
            # Get available players and projections
            available_players = self.get_available_players()
            projections_df = self.get_projections_data()
            
            if available_players.empty:
                print("No available players to draft")
                return ""
            
            # Calculate position scarcity
            scarcity_scores = self.calculate_position_scarcity(available_players)
            
            # Get team needs
            team_needs = self.get_team_needs()
            
            # Try AI recommendation first
            ai_recommendation = self.get_ai_draft_recommendation(
                available_players, team_needs, current_round, league_settings.total_rounds
            )
            
            if ai_recommendation:
                print(f"AI recommended player ID: {ai_recommendation}")
                return ai_recommendation
            
            # Fallback to algorithmic approach
            # Calculate value for each available player
            player_values = []
            
            for _, player in available_players.iterrows():
                value = self.calculate_player_value(player, projections_df, scarcity_scores)
                player_values.append({
                    'id': player['id'],
                    'name': player['full_name'],
                    'position': json.loads(player['allowed_positions'])[0] if player['allowed_positions'] else 'Unknown',
                    'rank': player['rank'],
                    'value': value
                })
            
            # Sort by value and select best
            player_values.sort(key=lambda x: x['value'], reverse=True)
            
            if player_values:
                best_player = player_values[0]
                print(f"Drafting {best_player['name']} ({best_player['position']}) with value {best_player['value']:.2f}")
                return best_player['id']
            
            # Ultimate fallback - highest ranked available player
            best_available = available_players.loc[available_players['rank'].idxmin()]
            print(f"Fallback: Drafting highest ranked player {best_available['full_name']}")
            return best_available['id']
            
        except Exception as e:
            print(f"Error in draft_player: {e}")
            # Return empty string to trigger random selection
            return ""
        finally:
            if hasattr(self, 'db') and self.db:
                self.db.close()

def draft_player() -> str:
    """
    Entry point function required by the bot interface
    
    Returns:
        str: Player ID to draft
    """
    bot = PhilipFantasyBot()
    return bot.draft_player()

def propose_add_drop(game_state: GameState) -> AddDropSelection:
    """
    Propose add/drop transactions for weekly roster management
    
    Args:
        game_state: Current game state with player information
        
    Returns:
        AddDropSelection: Add/drop recommendation
    """
    # For now, return empty selection (no add/drop)
    # This can be enhanced later with weekly roster optimization logic
    return AddDropSelection(
        player_to_add_id="",
        player_to_drop_id=""
    )

# Test the bot if run directly
if __name__ == "__main__":
    print("Testing Philip's Fantasy Football Bot...")
    result = draft_player()
    print(f"Draft result: {result}")
