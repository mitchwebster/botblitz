import requests
from bs4 import BeautifulSoup
import pandas as pd
import re
from typing import Union

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
        'response': response,
        'params': params
    }

    parsed_projections = fp_projections_parse(response_obj, page)

    if not include_metadata:
        return parsed_projections['projections']

    return parsed_projections

def fp_projections_parse(response_obj, page):
    sport = response_obj['sport']
    if sport == 'nfl':
        return fp_projections_parse_nfl(response_obj, page)
    elif sport == 'mlb':
        return fp_projections_parse_mlb(response_obj)
    elif sport == 'nba':
        return fp_projections_parse_nba(response_obj)
    elif sport == 'nhl':
        return fp_projections_parse_nhl(response_obj)
    else:
        raise ValueError("Invalid sport")

def fp_projections_parse_nfl(response, page):
    content = response['content']
    soup = BeautifulSoup(content, 'html.parser')
    params = response['params']

    # Find the table with id 'data'
    table_html = soup.find(id='data')

    # Get the header rows
    header_rows = table_html.select('thead > tr')

    if len(header_rows) == 2:
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
            if group and group != 'MISC':
                full_name = f"{group}_{col}"
            else:
                full_name = col
            full_column_names.append(full_name)
    elif len(header_rows) == 1:
        # Only one header row
        header_cells = header_rows[0].find_all(['th', 'td'])
        full_column_names = [cell.get_text(strip=True) for cell in header_cells]
    else:
        raise ValueError("No header rows found in the table.")

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
            # The team info is in the text of first_cell after the player name
            team_text = first_cell.get_text(strip=True)
            team = team_text.replace(player_name, '').strip()
        else:
            fp_id = None
            player_name = None
            team = None
        row_data.extend([str(params.get('year', '')), str(params.get('week', '')), fp_id, player_name, page, team])

        # Get the rest of the cells
        for cell in cells[1:]:
            text = cell.get_text(strip=True)
            row_data.append(text)
        data.append(row_data)

    # Create column names including player info
    columns = ['year', 'week', 'fantasypros_id', 'player_name', 'position', 'team'] + full_column_names[1:]

    projections_df = pd.DataFrame(data, columns=columns)

    # Convert numeric columns
    numeric_cols = projections_df.columns.drop(['year', 'week', 'fantasypros_id', 'player_name', 'position', 'team'])
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


# Default set of FantasyPros NFL position pages
DEFAULT_NFL_PAGES = ("qb", "rb", "wr", "te", "k", "dst")

def load_nfl_projections_all_positions(
    year: Union[int, str],
    week: Union[int, str] = "draft",
    scoring: str = "PPR",
    *,
    include_errors: bool = False,
    verbose: bool = False,
    **extra_params,
) -> pd.DataFrame:
    """
    Load FantasyPros NFL projections for a given year/week across multiple positions
    and return a single concatenated DataFrame.

    Parameters
    ----------
    year : int | str
        Season year (e.g., 2024).
    week : int | str, default "draft"
        "draft" for preseason projections, or a specific week number (e.g., 1..18).
    scoring : str, default "PPR"
        Scoring format accepted by FantasyPros (e.g., "PPR", "HALF", "STD").
    pages : Iterable[str], optional
        Iterable of position pages to fetch. Defaults to QB/RB/WR/TE/K/DST.
        (FantasyPros page names are lowercase like "qb", "rb", "wr", "te", "k", "dst".)
    include_errors : bool, default False
        If True, rows from failed pages will be skipped silently.
        If False, the first error encountered will be raised.
    verbose : bool, default False
        If True, prints progress messages for each page.
    **extra_params
        Any additional query params to pass through to `fp_projections`, such as:
        - "experts": "consensus" or an expert id filter
        - "filters": site-specific filters, if supported

    Returns
    -------
    pd.DataFrame
        One DataFrame containing all rows across the requested position pages
        with unified columns (non-shared columns will be present with NaNs as needed).
    """
    pages = DEFAULT_NFL_PAGES

    all_frames = []
    year_str = str(year)
    week_str = str(week)

    for page in pages:
        if verbose:
            print(f"Fetching {page.upper()} projections for year={year_str}, week={week_str}, scoring={scoring}...")
        try:
            df = fp_projections(
                page=page,
                sport="nfl",
                year=year_str,
                week=week_str,
                scoring=scoring,
                **extra_params
            )
            # Ensure required identifier columns exist (parser already sets these)
            # but we guard just in case.
            required_cols = ['year', 'week', 'fantasypros_id', 'player_name', 'position', 'team']
            for col in required_cols:
                if col not in df.columns:
                    df[col] = pd.NA
            all_frames.append(df)
        except Exception as e:
            if include_errors:
                if verbose:
                    print(f"  Skipping {page} due to error: {e}")
                continue
            raise

    if not all_frames:
        # If nothing fetched (e.g., all failed and include_errors=True), return empty with a sane schema
        return pd.DataFrame(columns=['year', 'week', 'fantasypros_id', 'player_name', 'position', 'team'])

    # Concatenate and reset index; union of columns is preserved automatically.
    merged = pd.concat(all_frames, ignore_index=True, sort=False)

    # Normalize dtypes for key identifiers
    for c in ('year', 'week', 'fantasypros_id', 'position', 'team', 'player_name'):
        if c in merged.columns:
            merged[c] = merged[c].astype('string')

    return merged

from .agent_pb2 import Player  

class ProjectionsDB:
    """
    A class to manage NFL player projections with caching to avoid redundant data loading.

    The projections are loaded from FantasyPros (ffpros), which maintains historical pre-game and pre-season projections.
    """

    def __init__(self):
        """
        Initializes the ProjectionsDB instance with an empty cache.
        """
        self._cache = {}

    def get_preseason_projections(self, player: Player, season: int) -> pd.DataFrame:
        """
        Retrieves preseason projections for a specific player and season.

        This method uses caching to avoid reloading data if it has been loaded before.
        The projections are sourced from FantasyPros (ffpros), which provides historical pre-season projections.

        Parameters:
        - player (Player): The player object for whom to retrieve projections.
        - season (int): The season year.

        Returns:
        - pd.DataFrame: A DataFrame containing the preseason projections for the specified player.
        """
        page = player.allowed_positions[0].lower()
        sport = "nfl"
        year = str(season)
        week = "draft"
        scoring = "PPR"
        key = (page, sport, year, week, scoring)

        if key in self._cache:
            df = self._cache[key]
        else:
            # Load projections from FantasyPros and cache the result
            df = fp_projections(page=page, sport=sport, year=year, week=week, scoring=scoring)
            self._cache[key] = df

        # Filter the DataFrame to return projections only for the specified player
        return df[df['fantasypros_id'] == player.id]

    def get_weekly_projections(self, player: Player, season: int, week: int) -> pd.DataFrame:
        """
        Retrieves weekly projections for a specific player, season, and week.

        This method uses caching to avoid reloading data if it has been loaded before.
        The projections are sourced from FantasyPros (ffpros), which provides historical pre-game projections.

        Parameters:
        - player (Player): The player object for whom to retrieve projections.
        - season (int): The season year.
        - week (int): The week number within the season.

        Returns:
        - pd.DataFrame: A DataFrame containing the weekly projections for the specified player and week.
        """
        page = player.allowed_positions[0].lower()
        sport = "nfl"
        year = str(season)
        week_str = str(week)
        scoring = "PPR"
        key = (page, sport, year, week_str, scoring)

        if key in self._cache:
            df = self._cache[key]
        else:
            # Load projections from FantasyPros and cache the result
            df = fp_projections(page=page, sport=sport, year=year, week=week_str, scoring=scoring)
            self._cache[key] = df

        # Filter the DataFrame to return projections only for the specified player
        return df[df['fantasypros_id'] == player.id]
