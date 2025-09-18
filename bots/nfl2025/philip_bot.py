import os
import pandas as pd
import numpy as np
import json
from typing import Dict, List, Tuple, Optional
from openai import OpenAI
from blitz_env.models import DatabaseManager
from blitz_env import AttemptedFantasyActions, WaiverClaim

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
            positions = []
            if player["allowed_positions"]:
                if isinstance(player["allowed_positions"], str):
                    try:
                        positions = json.loads(player["allowed_positions"])
                    except json.JSONDecodeError:
                        positions = []
                else:
                    positions = player["allowed_positions"]
            
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
            positions = []
            if player["allowed_positions"]:
                if isinstance(player["allowed_positions"], str):
                    try:
                        positions = json.loads(player["allowed_positions"])
                    except json.JSONDecodeError:
                        positions = []
                else:
                    positions = player["allowed_positions"]
            
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
                positions = []
                if player["allowed_positions"]:
                    if isinstance(player["allowed_positions"], str):
                        try:
                            positions = json.loads(player["allowed_positions"])
                        except json.JSONDecodeError:
                            positions = []
                    else:
                        positions = player["allowed_positions"]
                
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
                    positions = []
                    if player["allowed_positions"]:
                        if isinstance(player["allowed_positions"], str):
                            try:
                                positions = json.loads(player["allowed_positions"])
                            except json.JSONDecodeError:
                                positions = []
                        else:
                            positions = player["allowed_positions"]
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
                positions = []
                if player['allowed_positions']:
                    if isinstance(player['allowed_positions'], str):
                        try:
                            positions = json.loads(player['allowed_positions'])
                        except json.JSONDecodeError:
                            positions = []
                    else:
                        positions = player['allowed_positions']
                
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

    def get_ai_waiver_recommendations(self, my_team: pd.DataFrame, 
                                     available_players: pd.DataFrame) -> List[Dict]:
        """Use AI to get waiver wire recommendations if OpenAI is available"""
        if not self.openai_client:
            return []
            
        try:
            # Prepare context for AI
            team_summary = []
            for _, player in my_team.iterrows():
                positions = []
                if player["allowed_positions"]:
                    if isinstance(player["allowed_positions"], str):
                        try:
                            positions = json.loads(player["allowed_positions"])
                        except json.JSONDecodeError:
                            positions = []
                    else:
                        positions = player["allowed_positions"]
                
                pos_str = ", ".join(positions) if positions else "Unknown"
                team_summary.append(f"{player['full_name']} ({pos_str}) - Rank {player['rank']}")
            
            # Top available players by position
            available_by_pos = {}
            for _, player in available_players.iterrows():
                positions = []
                if player["allowed_positions"]:
                    if isinstance(player["allowed_positions"], str):
                        try:
                            positions = json.loads(player["allowed_positions"])
                        except json.JSONDecodeError:
                            positions = []
                    else:
                        positions = player["allowed_positions"]
                
                if positions:
                    pos = positions[0]
                    if pos not in available_by_pos:
                        available_by_pos[pos] = []
                    available_by_pos[pos].append(f"{player['full_name']} (Rank {player['rank']})")
            
            # Limit to top 10 per position for prompt
            for pos in available_by_pos:
                available_by_pos[pos] = available_by_pos[pos][:10]
            
            prompt = f"""
            You are a fantasy football expert analyzing waiver wire moves for the current NFL season.
            
            SITUATION: Team is in LAST PLACE and needs aggressive moves to catch up.
            
            Current Roster: {', '.join(team_summary)}
            
            Available Players by Position:
            {json.dumps(available_by_pos, indent=2)}
            
            CRITICAL: We only have $20 FAAB left for the ENTIRE REST OF THE SEASON! Be EXTREMELY selective.
            
            EMERGENCY STRATEGY:
            1. ONLY bid on strong pickups
            2. ONLY drop players who are worthless (injured for season, cut, etc.)
            3. NO D/ST or Kicker moves unless $0-1 bid
            
            BIDDING GUIDELINES (VERY CONSERVATIVE):
            - League-winning pickup (new starter due to injury): 3
            - Good breakout candidate: 2
            - Streaming/depth: 1
            - Defense/Kicker: 1 (shouldn't do this unless you're desperate)
            
            Return AT MOST 5 moves in this JSON format:
            [
                {{
                    "add_player": "Exact Player Name",
                    "drop_player": "Exact Player Name", 
                    "bid_amount": 1,
                    "reasoning": "This is a must-have pickup worth spending precious FAAB"
                }}
            ]
            
            Only suggest moves where:
            1. The pickup could genuinely save our season
            2. We're confident this is worth our limited budget
            
            Remember: Most weeks should return an empty array [] to preserve budget!
            """
            
            print(f"AI waiver prompt sent with {len(my_team)} roster players and {len(available_players)} available")
            
            response = self.openai_client.chat.completions.create(
                model="gpt-5",
                messages=[{"role": "user", "content": prompt}],
            )
            
            ai_response = response.choices[0].message.content.strip()
            print(f"AI waiver response: {ai_response}")
            
            # Parse JSON response
            import re
            json_match = re.search(r'\[(.*?)\]', ai_response, re.DOTALL)
            if json_match:
                json_str = '[' + json_match.group(1) + ']'
                recommendations = json.loads(json_str)
                return recommendations
                
        except Exception as e:
            print(f"AI waiver recommendation failed: {e}")
            
        return []

def draft_player() -> str:
    """
    Entry point function required by the bot interface
    
    Returns:
        str: Player ID to draft
    """
    bot = PhilipFantasyBot()
    return bot.draft_player()

def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    """
    Perform weekly add/drop actions using AI-powered waiver recommendations
    
    Strategy:
    1. Use AI to analyze roster and available players
    2. Get specific add/drop recommendations with reasoning
    3. Convert to waiver claims with appropriate bid amounts
    """
    try:
        bot = PhilipFantasyBot()
        
        # Get current team and available players
        my_team = bot.get_my_team()
        available_players = bot.get_available_players()
        
        print(f"=== AI-Powered Weekly Fantasy Actions ===")
        print(f"Current roster size: {len(my_team)}")
        print(f"Available players: {len(available_players)}")
        
        if my_team.empty or available_players.empty:
            print("No team or no available players - no actions needed")
            return AttemptedFantasyActions(waiver_claims=[])
        
        # Try AI recommendations first
        ai_recommendations = bot.get_ai_waiver_recommendations(my_team, available_players)
        
        claims = []
        
        if ai_recommendations:
            print(f"AI provided {len(ai_recommendations)} waiver recommendations")
            
            for rec in ai_recommendations:
                try:
                    # Find player IDs
                    add_player = available_players[available_players["full_name"] == rec["add_player"]]
                    drop_player = my_team[my_team["full_name"] == rec["drop_player"]]
                    
                    if not add_player.empty and not drop_player.empty:
                        # Override AI bid with our own logic based on ranks and position
                        add_rank = add_player.iloc[0]["rank"]
                        drop_rank = drop_player.iloc[0]["rank"]
                        
                        # Only make move if adding player is significantly better ranked
                        if add_rank >= drop_rank - 10:  # Not a clear upgrade
                            print(f"Skipping AI recommendation - not a clear upgrade: Add rank {add_rank} vs Drop rank {drop_rank}")
                            continue
                            
                        # Calculate conservative bid based on rank difference
                        rank_diff = drop_rank - add_rank
                        if rank_diff > 50:
                            bid_amount = 3  # Big upgrade
                        elif rank_diff > 25:
                            bid_amount = 2  # Good upgrade  
                        else:
                            bid_amount = 1  # Small upgrade
                            
                        # Special cases - avoid expensive D/ST and K moves
                        add_positions = []
                        if add_player.iloc[0]["allowed_positions"]:
                            if isinstance(add_player.iloc[0]["allowed_positions"], str):
                                try:
                                    add_positions = json.loads(add_player.iloc[0]["allowed_positions"])
                                except json.JSONDecodeError:
                                    add_positions = []
                            else:
                                add_positions = add_player.iloc[0]["allowed_positions"]
                        
                        if add_positions and add_positions[0] in ['DST', 'K']:
                            bid_amount = 1  # Never bid more than $1 on D/ST or K
                        
                        print(f"AI Waiver claim: Add {rec['add_player']} - Drop {rec['drop_player']}")
                        print(f"                 Bid: ${bid_amount}")
                        print(f"                 Reasoning: {rec.get('reasoning', 'AI recommendation')}")
                        
                        claims.append(WaiverClaim(
                            player_to_add_id=add_player.iloc[0]["id"],
                            player_to_drop_id=drop_player.iloc[0]["id"],
                            bid_amount=bid_amount
                        ))
                    else:
                        print(f"Could not find players for AI recommendation: {rec['add_player']} / {rec['drop_player']}")
                        
                except Exception as e:
                    print(f"Error processing AI recommendation: {e}")
        
        # Fallback to previous sophisticated algorithmic approach if AI failed
        if not claims:
            print("No AI recommendations available, using sophisticated fallback logic")
            
            # Get projections and calculate position scarcity
            projections_df = bot.get_projections_data()
            scarcity_scores = bot.calculate_position_scarcity(available_players)
            
            # Evaluate all available players with full value calculation
            available_player_values = []
            for _, player in available_players.iterrows():
                value = bot.calculate_player_value(player, projections_df, scarcity_scores)
                
                # Get player position safely
                positions = []
                if player['allowed_positions']:
                    if isinstance(player['allowed_positions'], str):
                        try:
                            positions = json.loads(player['allowed_positions'])
                        except json.JSONDecodeError:
                            positions = []
                    else:
                        positions = player['allowed_positions']
                
                if positions and value > -1000:  # Only consider valid players
                    available_player_values.append({
                        'id': player['id'],
                        'name': player['full_name'],
                        'position': positions[0],
                        'rank': player['rank'],
                        'value': value
                    })
            
            # Sort available players by value
            available_player_values.sort(key=lambda x: x['value'], reverse=True)
            
            # Evaluate current roster players
            roster_player_values = []
            for _, player in my_team.iterrows():
                value = bot.calculate_player_value(player, projections_df, scarcity_scores)
                
                positions = []
                if player['allowed_positions']:
                    if isinstance(player['allowed_positions'], str):
                        try:
                            positions = json.loads(player['allowed_positions'])
                        except json.JSONDecodeError:
                            positions = []
                    else:
                        positions = player['allowed_positions']
                
                if positions:
                    roster_player_values.append({
                        'id': player['id'],
                        'name': player['full_name'],
                        'position': positions[0],
                        'rank': player['rank'],
                        'value': value
                    })
            
            # Sort roster players by value (lowest first - these are drop candidates)
            roster_player_values.sort(key=lambda x: x['value'])
            
            # Look for upgrade opportunities - be more selective to preserve FAAB
            for roster_player in roster_player_values[:5]:  # Check bottom 5 roster players (reduced from 8)
                position = roster_player['position']
                
                # Find best available player at same position
                best_available = None
                for avail_player in available_player_values:
                    if avail_player['position'] == position:
                        # VERY high threshold since we only have $20 left (was 75, now 150)
                        if avail_player['value'] - roster_player['value'] > 150:
                            best_available = avail_player
                            break
                
                if best_available:
                    # Calculate bid amount based on value difference and position scarcity
                    value_diff = best_available['value'] - roster_player['value']
                    scarcity_factor = scarcity_scores.get(position, 0)
                    
                    # ULTRA conservative bidding - only $20 left for entire season!
                    # Base bid: 1-6 depending on value difference (emergency mode)
                    if value_diff > 300:
                        base_bid = 3   # Must-have pickup
                    elif value_diff > 250:
                        base_bid = 2   # Excellent upgrade
                    elif value_diff > 200:
                        base_bid = 1   # Very good upgrade
                    else:
                        base_bid = 1   # Good upgrade
                    
                    # Minimal bonuses to preserve precious budget
                    value_bonus = min(1, int(value_diff / 100))  # Up to 2 extra for exceptional value
                    scarcity_bonus = int(scarcity_factor * 1)    # Up to 1 extra for scarcity
                    
                    bid_amount = base_bid + value_bonus + scarcity_bonus
                    bid_amount = min(3, bid_amount) # Cap max spend
                    
                    print(f"Fallback claim: Add {best_available['name']} (Rank {best_available['rank']}, Value {best_available['value']:.1f})")
                    print(f"                Drop {roster_player['name']} (Rank {roster_player['rank']}, Value {roster_player['value']:.1f})")
                    print(f"                Bid: ${bid_amount} (Value diff: {value_diff:.1f})")
                    
                    claims.append(WaiverClaim(
                        player_to_add_id=best_available['id'],
                        player_to_drop_id=roster_player['id'],
                        bid_amount=bid_amount
                    ))
                    
                    # Remove claimed player from available list to avoid double-claiming
                    available_player_values = [p for p in available_player_values if p['id'] != best_available['id']]
                    
                    # Extremely limited claims due to low budget (max 1-2)
                    if len(claims) >= 2:
                        break
        
        if not claims:
            print("No beneficial waiver claims identified")
            return AttemptedFantasyActions(waiver_claims=[])
        
        print(f"Submitting {len(claims)} waiver claims")
        return AttemptedFantasyActions(waiver_claims=claims)
        
    except Exception as e:
        print(f"Error in perform_weekly_fantasy_actions: {e}")
        return AttemptedFantasyActions(waiver_claims=[])

# Test the bot if run directly
if __name__ == "__main__":
    print("Testing Philip's Fantasy Football Bot...")
    result = draft_player()
    print(f"Draft result: {result}")
