import requests
from bs4 import BeautifulSoup
import pandas as pd
import re

def fp_projections(page, sport=None, include_metadata=False, **kwargs):
    if sport is None:
        sport = 'nfl'  # Default sport

    if sport not in ['nfl', 'mlb', 'nba', 'nhl']:
        raise ValueError("Sport must be one of 'nfl', 'mlb', 'nba', 'nhl'")

    url_query = f"https://www.fantasypros.com/{sport}/projections/{page}.php"

    params = kwargs
    response = requests.get(url_query, params=params)

    response_obj = {
        'content': response.content,
        'query': response.url,
        'sport': sport,
        'response': response
    }

    parsed_projections = fp_projections_parse(response_obj)

    if not include_metadata:
        return parsed_projections['projections']

    return parsed_projections

def fp_projections_parse(response_obj):
    sport = response_obj['sport']
    if sport == 'nfl':
        return fp_projections_parse_nfl(response_obj)
    elif sport == 'mlb':
        return fp_projections_parse_mlb(response_obj)
    elif sport == 'nba':
        return fp_projections_parse_nba(response_obj)
    elif sport == 'nhl':
        return fp_projections_parse_nhl(response_obj)
    else:
        raise ValueError("Invalid sport")

def fp_projections_parse_nfl(response):
    content = response['content']
    soup = BeautifulSoup(content, 'html.parser')

    # Find the table with id 'data'
    table_html = soup.find(id='data')

    # Get the header rows
    header_rows = table_html.select('thead > tr')

    # Process the first header row (grouping labels)
    first_header_cells = header_rows[0].find_all(['th', 'td'])
    grouping_labels = []
    for cell in first_header_cells:
        colspan = int(cell.get('colspan', 1))
        label = cell.get_text(strip=True)
        if not label:
            labels = [''] * colspan
        else:
            labels = [label] * colspan
        grouping_labels.extend(labels)

    # Process the second header row (column names)
    second_header_cells = header_rows[1].find_all(['th', 'td'])
    column_labels = [cell.get_text(strip=True) for cell in second_header_cells]

    # Combine grouping labels and column labels
    full_column_names = []
    for group, col in zip(grouping_labels, column_labels):
        if group:
            full_name = f"{group}_{col}"
        else:
            full_name = col
        full_column_names.append(full_name)

    # Get the data rows
    data_rows = table_html.select('tbody > tr')

    data = []
    for row in data_rows:
        cells = row.find_all(['th', 'td'])
        row_data = []
        # Process the first cell separately
        first_cell = cells[0]
        # Extract player_name and team
        player_label = first_cell
        fp_player_link = player_label.select_one('.fp-player-link')
        if fp_player_link:
            # Get fantasypros_id from class attribute
            fp_player_class = fp_player_link.get('class', [])
            fp_id = None
            for cls in fp_player_class:
                m = re.search(r'fp-id-(\d+)', cls)
                if m:
                    fp_id = m.group(1)
                    break
            if fp_id is None:
                fp_id = fp_player_link.get('fp-player-id', None)
            player_name = fp_player_link.get('fp-player-name', None)
            # The team info is in the text of first_cell
            team_text = first_cell.get_text(strip=True)
            team = team_text.replace(player_name, '').strip()
        else:
            fp_id = None
            player_name = None
            team = None
        row_data.extend([fp_id, player_name, team])

        # Get the rest of the cells
        for cell in cells[1:]:
            text = cell.get_text(strip=True)
            row_data.append(text)
        data.append(row_data)

    # Create column names including player info
    columns = ['fantasypros_id', 'player_name', 'team'] + full_column_names[1:]

    projections_df = pd.DataFrame(data, columns=columns)

    # Convert numeric columns
    numeric_cols = projections_df.columns.drop(['fantasypros_id', 'player_name', 'team'])
    for col in numeric_cols:
        projections_df[col] = projections_df[col].replace(',', '', regex=True)
        projections_df[col] = pd.to_numeric(projections_df[col], errors='coerce')

    return {'projections': projections_df, 'response': response['response']}

def fp_projections_parse_mlb(response):
    # Similar logic for MLB projections parsing
    pass  # Placeholder for MLB parsing implementation

def fp_projections_parse_nba(response):
    # Similar logic for NBA projections parsing
    pass  # Placeholder for NBA parsing implementation

def fp_projections_parse_nhl(response):
    print("No projections for NHL yet")
    return {'projections': None, 'response': response['response']}


from .agent_pb2 import Player  

class ProjectionsDB:
    def __init__(self):
        self._cache = {}
    
    def get_preseason_projections(self, player: Player, season: int) -> pd.DataFrame:
        page = player.allowed_positions[0].lower()
        sport = "nfl"
        year = str(season)
        week = "draft"
        key = (page, sport, year, week)
        
        if key in self._cache:
            df = self._cache[key]
        else:
            df = fp_projections(page=page, sport=sport, year=year, week=week)
            self._cache[key] = df
            
        return df[df['id'] == player.id]
    
    def get_weekly_projections(self, player: Player, season: int, week: int) -> pd.DataFrame:
        page = player.allowed_positions[0].lower()
        sport = "nfl"
        year = str(season)
        week_str = str(week)
        key = (page, sport, year, week_str)
        
        if key in self._cache:
            df = self._cache[key]
        else:
            df = fp_projections(page=page, sport=sport, year=year, week=week_str)
            self._cache[key] = df
            
        return df[df['id'] == player.id]
