import requests
from bs4 import BeautifulSoup
import pandas as pd
import re
from datetime import datetime

class NFLInjuryScraper:
    def __init__(self, year=2025, week=6):
        self.year = year
        self.week = week
        self.base_url = f"https://www.nfl.com/injuries/league/{year}/reg{week}"
        self.playerids_url = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        self.player_ids_df = None
    
    def load_player_ids(self):
        """Load player IDs from dynastyprocess"""
        print("Loading player ID mappings...")
        self.player_ids_df = pd.read_csv(self.playerids_url)
        print(f"Loaded {len(self.player_ids_df)} player ID records")
        return self.player_ids_df
    
    def fetch_page(self):
        """Fetch the injury report page"""
        response = requests.get(self.base_url, headers=self.headers)
        response.raise_for_status()
        return response.text
    
    def extract_team_from_url(self, url):
        """Extract team abbreviation from NFL.com team URL"""
        # URLs look like: /teams/philadelphia-eagles/
        if not url:
            return None
        match = re.search(r'/teams/([^/]+)/', url)
        if match:
            team_slug = match.group(1)
            # Convert slug to abbreviation (you may need to expand this mapping)
            team_map = {
                'arizona-cardinals': 'ARI', 'atlanta-falcons': 'ATL',
                'baltimore-ravens': 'BAL', 'buffalo-bills': 'BUF',
                'carolina-panthers': 'CAR', 'chicago-bears': 'CHI',
                'cincinnati-bengals': 'CIN', 'cleveland-browns': 'CLE',
                'dallas-cowboys': 'DAL', 'denver-broncos': 'DEN',
                'detroit-lions': 'DET', 'green-bay-packers': 'GBP',
                'houston-texans': 'HOU', 'indianapolis-colts': 'IND',
                'jacksonville-jaguars': 'JAX', 'kansas-city-chiefs': 'KC',
                'las-vegas-raiders': 'LV', 'los-angeles-chargers': 'LAC',
                'los-angeles-rams': 'LAR', 'miami-dolphins': 'MIA',
                'minnesota-vikings': 'MIN', 'new-england-patriots': 'NE',
                'new-orleans-saints': 'NO', 'new-york-giants': 'NYG',
                'new-york-jets': 'NYJ', 'philadelphia-eagles': 'PHI',
                'pittsburgh-steelers': 'PIT', 'san-francisco-49ers': 'SF',
                'seattle-seahawks': 'SEA', 'tampa-bay-buccaneers': 'TB',
                'tennessee-titans': 'TEN', 'washington-commanders': 'WAS'
            }
            return team_map.get(team_slug, team_slug.upper()[:3])
        return None
    
    def parse_injuries(self, html_content):
        """Parse injury data from HTML with improved team extraction"""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        injury_data = []
        
        # Find all game sections - they're organized by matchup
        # Look for team logo links to identify teams
        team_sections = soup.find_all('a', href=re.compile(r'/teams/'))
        
        # Get all tables
        tables = soup.find_all('table')
        
        # Try to find game date headers
        date_pattern = re.compile(r'(THURSDAY|FRIDAY|SATURDAY|SUNDAY|MONDAY),\s+(\w+)\s+(\d+)')
        date_headers = soup.find_all(string=date_pattern)
        
        current_date = None
        current_teams = []
        team_queue = []
        
        # Build a queue of teams from the page
        for link in team_sections:
            team_abbr = self.extract_team_from_url(link.get('href'))
            if team_abbr and team_abbr not in team_queue:
                team_queue.append(team_abbr)
        
        team_index = 0
        
        for table_idx, table in enumerate(tables):
            rows = table.find_all('tr')
            
            if len(rows) < 2:
                continue
            
            # Try to find the team for this table by looking at nearby elements
            current_team = None
            current_opponent = None
            
            # Look for team context near this table
            parent = table.find_parent()
            nearby_teams = []
            
            if parent:
                nearby_links = parent.find_all('a', href=re.compile(r'/teams/'), limit=2)
                for link in nearby_links:
                    team = self.extract_team_from_url(link.get('href'))
                    if team:
                        nearby_teams.append(team)
            
            # If we found teams nearby, use them
            if len(nearby_teams) >= 2:
                current_team = nearby_teams[0]
                current_opponent = nearby_teams[1]
            elif len(nearby_teams) == 1:
                current_team = nearby_teams[0]
                current_opponent = 'TBD'
            else:
                # Fall back to queue
                if team_index < len(team_queue):
                    current_team = team_queue[team_index]
                    if team_index + 1 < len(team_queue):
                        current_opponent = team_queue[team_index + 1]
                    team_index += 1
            
            # Parse rows
            for row in rows[1:]:
                cols = row.find_all('td')
                if len(cols) < 5:
                    continue

                # Get player info
                player_link = row.find('a', href=re.compile(r'/players/'))
                if not player_link:
                    continue

                player_name = player_link.text.strip()
                if not player_name:
                    continue

                # NFL.com table structure:
                # cols[0] = Player name/position combo (in first cell)
                # cols[1] = Position (separate column)
                # cols[2] = Injury type
                # cols[3] = Practice status
                # cols[4] = Game status

                injury_data.append({
                    'week': f"Week {self.week}",
                    'year': self.year,
                    'team': current_team or 'Unknown',
                    'opponent': current_opponent or 'Unknown',
                    'game_date': current_date or 'Unknown',
                    'player_name': player_name,
                    'position': cols[1].text.strip(),
                    'injury': cols[2].text.strip(),
                    'practice_status': cols[3].text.strip(),
                    'game_status': cols[4].text.strip(),
                    'scraped_at': datetime.now().isoformat()
                })
        
        return injury_data
    
    def match_player_ids(self, injury_df):
        """Match injury data with FantasyPros IDs using name and position only"""
        if self.player_ids_df is None:
            self.load_player_ids()

        from rapidfuzz import process, fuzz

        # Normalize names and positions for matching
        injury_df['name_normalized'] = injury_df['player_name'].str.lower().str.strip()
        injury_df['position_normalized'] = injury_df['position'].str.upper().str.strip()

        # Prepare player IDs dataset - check which columns are available
        available_cols = list(self.player_ids_df.columns)

        # Try common column name variations
        pos_col = None
        for possible_name in ['pos', 'position', 'Position', 'POS']:
            if possible_name in available_cols:
                pos_col = possible_name
                break

        if pos_col is None:
            raise ValueError(f"Could not find position column in player IDs data. Available columns: {available_cols}")

        player_ids_subset = self.player_ids_df[['name', pos_col, 'fantasypros_id', 'gsis_id', 'sleeper_id']].copy()
        player_ids_subset['name_normalized'] = player_ids_subset['name'].str.lower().str.strip()
        player_ids_subset['position_normalized'] = player_ids_subset[pos_col].str.upper().str.strip()
        
        # First pass: exact match on name AND position
        merged = injury_df.merge(
            player_ids_subset[['name_normalized', 'position_normalized', 'fantasypros_id', 'gsis_id', 'sleeper_id']],
            on=['name_normalized', 'position_normalized'],
            how='left'
        )
        
        # Second pass: fuzzy match for unmatched players using name + position
        unmatched_mask = merged['fantasypros_id'].isna()
        unmatched_count = unmatched_mask.sum()
        
        if unmatched_count > 0:
            print(f"\nAttempting fuzzy matching for {unmatched_count} unmatched players...")
            
            # Create combined name+position strings for better matching
            player_ids_subset['name_pos'] = (
                player_ids_subset['name_normalized'] + ' | ' + 
                player_ids_subset['position_normalized']
            )
            all_player_combos = player_ids_subset['name_pos'].tolist()
            
            fuzzy_matched = 0
            for idx in merged[unmatched_mask].index:
                player_name = merged.loc[idx, 'name_normalized']
                player_pos = merged.loc[idx, 'position_normalized']
                search_string = f"{player_name} | {player_pos}"
                
                # Find best match using rapidfuzz
                match_result = process.extractOne(
                    search_string,
                    all_player_combos,
                    scorer=fuzz.ratio,
                    score_cutoff=80  # Only accept matches with 80%+ similarity
                )
                
                if match_result:
                    best_match_combo, score, _ = match_result
                    
                    # Get the player IDs for the matched name+position
                    matched_row = player_ids_subset[
                        player_ids_subset['name_pos'] == best_match_combo
                    ].iloc[0]
                    
                    # Update the merged dataframe
                    merged.loc[idx, 'fantasypros_id'] = matched_row['fantasypros_id']
                    merged.loc[idx, 'gsis_id'] = matched_row['gsis_id']
                    merged.loc[idx, 'sleeper_id'] = matched_row['sleeper_id']
                    
                    fuzzy_matched += 1
                    matched_name = matched_row['name_normalized']
                    matched_pos = matched_row['position_normalized']
                    print(f"  Fuzzy matched: '{player_name} ({player_pos})' -> '{matched_name} ({matched_pos})' (score: {score})")
            
            print(f"Fuzzy matched {fuzzy_matched} additional players")
        
        # Drop the normalized columns
        merged = merged.drop(['name_normalized', 'position_normalized'], axis=1)
        
        # Report matching stats
        matched_count = merged['fantasypros_id'].notna().sum()
        total_count = len(merged)
        print(f"\nFinal Player ID Matching Stats:")
        print(f"  Matched: {matched_count}/{total_count} ({matched_count/total_count*100:.1f}%)")
        print(f"  Unmatched: {total_count - matched_count}")
        
        if total_count - matched_count > 0:
            print("\nUnmatched players:")
            unmatched = merged[merged['fantasypros_id'].isna()][['player_name', 'position', 'team']].drop_duplicates()
            for _, row in unmatched.iterrows():
                print(f"  - {row['player_name']} ({row['position']}, {row['team']})")
        
        return merged
    
    def scrape(self):
        """Main scraping method"""
        print(f"Scraping NFL injury data for {self.year} Week {self.week}...")
        html = self.fetch_page()
        data = self.parse_injuries(html)
        print(f"Scraped {len(data)} player injury records")
        return data
    
    def to_dataframe(self, data):
        """Convert scraped data to pandas DataFrame"""
        return pd.DataFrame(data)
    
    def save_to_csv(self, df, filename=None, include_ids=True):
        """Save data to CSV file"""
        if filename is None:
            filename = f"nfl_injuries_{self.year}_week{self.week}.csv"
        
        if include_ids and not isinstance(df, pd.DataFrame):
            df = self.to_dataframe(df)
            df = self.match_player_ids(df)
        
        df.to_csv(filename, index=False)
        print(f"Saved to {filename}")
        return filename
    
    def save_to_json(self, df, filename=None, include_ids=True):
        """Save data to JSON file"""
        if filename is None:
            filename = f"nfl_injuries_{self.year}_week{self.week}.json"
        
        if include_ids and not isinstance(df, pd.DataFrame):
            df = self.to_dataframe(df)
            df = self.match_player_ids(df)
        
        df.to_json(filename, orient='records', indent=2)
        print(f"Saved to {filename}")
        return filename


# Example usage
if __name__ == "__main__":
    # Create scraper for Week 6 of 2025 season
    scraper = NFLInjuryScraper(year=2025, week=6)
    
    # Scrape the data
    injury_data = scraper.scrape()
    
    # Convert to DataFrame
    df = scraper.to_dataframe(injury_data)
    
    # Match with player IDs
    df_with_ids = scraper.match_player_ids(df)
    
    # Display sample with IDs
    print("\nSample data with FantasyPros IDs:")
    print(df_with_ids[['player_name', 'team', 'position', 'injury', 'fantasypros_id']].head(10))
    
    # Save to CSV with IDs
    scraper.save_to_csv(df_with_ids, include_ids=False)  # Already has IDs
    
    # Optional: Scrape multiple weeks
    # all_weeks = []
    # for week in range(1, 18):
    #     scraper = NFLInjuryScraper(year=2025, week=week)
    #     data = scraper.scrape()
    #     df = scraper.to_dataframe(data)
    #     df = scraper.match_player_ids(df)
    #     all_weeks.append(df)
    # 
    # combined_df = pd.concat(all_weeks, ignore_index=True)
    # combined_df.to_csv('nfl_injuries_2025_all_weeks.csv', index=False)