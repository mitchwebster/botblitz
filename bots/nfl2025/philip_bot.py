import os
import pandas as pd
import numpy as np
import json
import ast
from typing import Dict, List, Tuple, Optional
from openai import OpenAI
from blitz_env.models import DatabaseManager
from blitz_env import AttemptedFantasyActions, WaiverClaim

def safe_positions(x):
    """Safely parse allowed_positions field, handling various formats"""
    if not x:
        return []
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        try:
            return json.loads(x)
        except json.JSONDecodeError:
            try:
                return ast.literal_eval(x)
            except Exception:
                return [p.strip() for p in x.split(",") if p.strip()]
    return []

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
                print("OpenAI client initialized")
            except Exception as e:
                print(f"Failed to initialize OpenAI client: {e}")
                self.openai_client = None
        else:
            print("No OpenAI token found")
    
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
        players_df["rank"] = pd.to_numeric(players_df["rank"], errors="coerce")
        return players_df[players_df["current_bot_id"] == game_status.current_bot_id]
    
    def get_available_players(self) -> pd.DataFrame:
        """Get all available (undrafted) players with reasonable filtering"""
        players_df = pd.read_sql("SELECT * FROM players", self.db.engine)
        players_df["rank"] = pd.to_numeric(players_df["rank"], errors="coerce")
        available_players = players_df[players_df["availability"] == "AVAILABLE"]
        
        print(f"Total available players before filtering: {len(available_players)}")
        
        # Filter out players with very high ranks (likely not fantasy relevant)
        # This is a heuristic - players with ranks > 300 are likely injured/out/not relevant
        filtered_players = available_players[available_players["rank"] <= 350]
        
        print(f"Available players after rank filter: {len(filtered_players)}")
        
        return filtered_players
    
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
            def position_filter(x):
                if not x:
                    return False
                try:
                    if isinstance(x, str):
                        positions = json.loads(x)
                    else:
                        positions = x
                    return pos in positions
                except (json.JSONDecodeError, TypeError):
                    return False
            
            pos_players = available_players[available_players["allowed_positions"].apply(position_filter)]
            
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
            positions = safe_positions(player["allowed_positions"])
            
            if not positions:
                return -1000  # Invalid player
            
            primary_pos = positions[0]
            
            # Base value from projections
            player_projection = projections_df[
                projections_df["fantasypros_id"] == player.get("fantasypros_id")
            ]
            if player_projection.empty:
                return -1000  # No projection data
            
            base_fpts = float(player_projection.iloc[0]["FPTS"])
            
            # Position scarcity multiplier
            scarcity_multiplier = 1 + (scarcity_scores.get(primary_pos, 0) * 0.5)
            
            # Rank-based value (lower rank = higher value)
            # Since rank 1 is best, we need to invert: better ranks get higher values
            rank_value = max(0, 500 - player["rank"])
            
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
        
        # Get required roster slots - handle both string and dict formats
        required_slots = {}
        if league_settings.player_slots:
            if isinstance(league_settings.player_slots, str):
                try:
                    required_slots = json.loads(league_settings.player_slots)
                except json.JSONDecodeError:
                    required_slots = {}
            else:
                required_slots = league_settings.player_slots
        
        # Count current players by position
        position_counts = {}
        for _, player in my_team.iterrows():
            positions = safe_positions(player["allowed_positions"])
            
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
                positions = safe_positions(player["allowed_positions"])
                pos_str = ", ".join(positions) if positions else "Unknown"
                team_summary.append(f"{player['full_name']} ({pos_str}) - Rank {player['rank']}")
            
            # Top available players (lowest rank numbers are best)
            top_players = available_players.nsmallest(200, "rank")["full_name"].tolist()
            
            prompt = f"""
            You are a fantasy football expert helping with draft strategy for the current NFL season.
            
            Current Situation:
            - Round {current_round} of {total_rounds}
            - Team needs: {json.dumps(team_needs)}
            - Current roster: {', '.join(team_summary) if team_summary else 'Empty team'}
            
            Available players (top 200 by rank): {', '.join(top_players)}
            
            Strategy priorities:
            1. Fill required starting positions first
            2. Consider position scarcity
            3. Build depth at RB/WR
            4. Avoid multiple players on same bye week
            5. Take best available player in later rounds
            
            Return ONLY the player name (exactly as shown) that should be drafted next.
            """
            
            print(f"AI prompt sent with {len(top_players)} available players")
            
            response = self.openai_client.chat.completions.create(
                model="gpt-5",
                messages=[{"role": "user", "content": prompt}],
            )

            print(f"AI response: {response}")
            
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
            current_round = ((game_status.current_draft_pick - 1) // league_settings.num_teams) + 1 if game_status.current_draft_pick else 1
            
            print(f"=== Draft Round {current_round} of {league_settings.total_rounds} ===")
            print(f"Current draft pick: {game_status.current_draft_pick}")
            
            # Get available players and projections
            available_players = self.get_available_players()
            projections_df = self.get_projections_data()
            
            print(f"Available players: {len(available_players)}")
            print(f"Projections data loaded: {len(projections_df)} records")
            
            if available_players.empty:
                print("No available players to draft")
                return ""
            
            # Calculate position scarcity
            scarcity_scores = self.calculate_position_scarcity(available_players)
            print(f"Position scarcity scores: {scarcity_scores}")
            
            # Get team needs
            team_needs = self.get_team_needs()
            print(f"Current team needs: {team_needs}")
            
            # Show current roster
            my_team = self.get_my_team()
            if not my_team.empty:
                print("Current roster:")
                for _, player in my_team.iterrows():
                    positions = safe_positions(player["allowed_positions"])
                    pos_str = ", ".join(positions) if positions else "Unknown"
                    print(f"  {player['full_name']} ({pos_str}) - Rank {player['rank']}")
            else:
                print("Current roster: Empty")
            
            # Try AI recommendation first
            ai_recommendation = self.get_ai_draft_recommendation(
                available_players, team_needs, current_round, league_settings.total_rounds
            )
            
            if ai_recommendation:
                # Get player name for logging
                player_name = available_players[available_players["id"] == ai_recommendation]["full_name"].iloc[0] if not available_players[available_players["id"] == ai_recommendation].empty else "Unknown"
                print(f"AI recommended player: {player_name} (ID: {ai_recommendation})")
                return ai_recommendation
            
            # Fallback to algorithmic approach
            # Calculate value for each available player
            player_values = []
            
            print(f"Calculating values for {len(available_players)} available players...")
            
            for _, player in available_players.iterrows():
                value = self.calculate_player_value(player, projections_df, scarcity_scores)
                # Get player position safely
                positions = safe_positions(player['allowed_positions'])
                position = positions[0] if positions else 'Unknown'
                
                player_values.append({
                    'id': player['id'],
                    'name': player['full_name'],
                    'position': position,
                    'rank': player['rank'],
                    'value': value
                })
            
            # Sort by value and select best
            player_values.sort(key=lambda x: x['value'], reverse=True)
            
            # Log top 5 players by value for debugging
            print("Top 5 players by calculated value:")
            for i, player in enumerate(player_values[:5]):
                print(f"  {i+1}. {player['name']} ({player['position']}) - Value: {player['value']:.2f}, Rank: {player['rank']}")
            
            if player_values:
                best_player = player_values[0]
                print(f"Drafting {best_player['name']} ({best_player['position']}) with value {best_player['value']:.2f} (ID: {best_player['id']})")
                return best_player['id']
            
            # Ultimate fallback - highest ranked available player
            best_available = available_players.loc[available_players['rank'].idxmin()]
            print(f"Fallback: Drafting highest ranked player {best_available['full_name']} (ID: {best_available['id']})")
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

def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    claims = [
        WaiverClaim(
            player_to_add_id="17236",  # Sam Darnold
            player_to_drop_id="23018", # J.J. McCarthy
            bid_amount=0
        ),
        WaiverClaim(
            player_to_add_id="11687",  # Geno Smith
            player_to_drop_id="23018", # fallback if Darnold taken
            bid_amount=0
        ),
        WaiverClaim(
            player_to_add_id="22973",  # Michael Penix Jr.
            player_to_drop_id="23018", # tertiary QB fallback
            bid_amount=0
        ),
        # --- Tight End Reinforcements ---
        WaiverClaim(
            player_to_add_id="25247",  # Dalton Kincaid
            player_to_drop_id="19562", # replace Juwan Johnson
            bid_amount=0
        ),
        WaiverClaim(
            player_to_add_id="17270",  # Dallas Goedert
            player_to_drop_id="19562", # fallback if Kincaid taken
            bid_amount=0
        ),
        # --- RB / Flex Depth ---
        WaiverClaim(
            player_to_add_id="19647",  # Rico Dowdle
            player_to_drop_id="18256", # swap Justice Hill
            bid_amount=0
        ),
        WaiverClaim(
            player_to_add_id="23891",  # Rachaad White
            player_to_drop_id="18256", # fallback if Dowdle taken
            bid_amount=0
        ),
        # --- Kicker / DST Upgrades ---
        WaiverClaim(
            player_to_add_id="15756",  # Ka'imi Fairbairn
            player_to_drop_id="16910", # Younghoe Koo
            bid_amount=0
        ),
        WaiverClaim(
            player_to_add_id="9443",   # Matt Prater
            player_to_drop_id="16910", # backup kicker pickup
            bid_amount=0
        ),
        WaiverClaim(
            player_to_add_id="26475",  # Cam Little
            player_to_drop_id="16910", # tertiary kicker backup
            bid_amount=0
        ),
        WaiverClaim(
            player_to_add_id="8180",   # New England Patriots DST
            player_to_drop_id="8140",  # rotate vs Jacksonville bye/bad matchup
            bid_amount=0
        )
    ]


    actions = AttemptedFantasyActions(
        waiver_claims=claims
    )

    return actions

# Test the bot if run directly
if __name__ == "__main__":
    print("Testing Philip's Fantasy Football Bot...")
    result = draft_player()
    print(f"Draft result: {result}")
