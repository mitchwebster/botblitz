import pandas as pd
from typing import List
from sqlalchemy import create_engine
from blitz_env.agent_pb2 import Player
import re

# NOTE: `requests`/`bs4`/`nfl_data_py` are intentionally NOT imported at module load.
# They are only needed by the FantasyPros scrapers below (used by the offline data
# collectors, not at bot runtime), so they're imported lazily inside fp_stats_dynamic.
# This keeps `import blitz_env` lean inside the container.

def fp_seasonal_years(page, years):
    dfs = []
    for year in years:
        df = fp_stats_dynamic(page, year=year)
        df["season"] = year
        dfs.append(df)
    return pd.concat(dfs).reset_index()

def fp_weekly_years(page, years):
    dfs = []
    for year in years:
        # Note this may fail for historical data
        for week in range(1, 19):
            # try:
                df = fp_stats_dynamic(page, year=year, range="week", week=week)
                df["week"] = week
                df["season"] = year
                dfs.append(df)
            # except:
            #     print(f"Failed to get week {week}")
    return pd.concat(dfs).reset_index()

def fp_stats_dynamic(page, **kwargs):
    import requests
    from bs4 import BeautifulSoup

    url_query = f"https://www.fantasypros.com/nfl/stats/{page}.php"
    params = kwargs
    response = requests.get(url_query, params=params)

    if response.status_code != 200:
        raise Exception(f"Failed to retrieve data: Status code {response.status_code}")

    content = response.content
    soup = BeautifulSoup(content, 'html.parser')

    # Find the table with id 'data'
    table_html = soup.find(id='data')

    # Get the header rows
    header_rows = table_html.find('thead').find_all('tr')
    column_names = []

    # Process all header rows to build the column names
    for row_index, row in enumerate(header_rows):
        cells = row.find_all(['th', 'td'])
        row_headers = []
        for cell in cells:
            # Get text from <small> tag if present
            small_tag = cell.find('small')
            if small_tag:
                header_text = small_tag.get_text(strip=True)
            else:
                header_text = cell.get_text(strip=True)
            colspan = int(cell.get('colspan', 1))
            row_headers.extend([header_text] * colspan)
        # If column_names is empty, initialize it
        if not column_names:
            column_names = row_headers
        else:
            # Combine with previous headers
            new_column_names = []
            for prev, curr in zip(column_names, row_headers):
                if prev and prev != 'MISC':
                    full_name = f"{prev}_{curr}"
                else:
                    full_name = curr
                new_column_names.append(full_name)
            column_names = new_column_names

    # Clean up column names
    column_names = [name.strip().replace(' ', '_') for name in column_names]

    # Get the data rows
    data_rows = table_html.find('tbody').find_all('tr')

    data = []
    for row in data_rows:
        cells = row.find_all('td')
        row_data = []
        # Add 'position' at the beginning
        position = page.upper()  # Convert page to uppercase for consistency
        year = str(params.get('year', ''))
        week = str(params.get('week', ''))
        # Process the first cell (Rank)
        rank_cell = cells[0]
        rank_text = rank_cell.get_text(strip=True)
        rank = rank_text
        # Process the second cell (Player)
        player_cell = cells[1]
        # Extract player info
        fp_player_link = player_cell.find('a', class_='fp-player-link')
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
            # The team info is in the text of the cell
            team_text = player_cell.get_text(strip=True)
            team = team_text.replace(fp_player_link.get_text(strip=True), '').strip()
            # Extract team abbreviation from parentheses
            team = re.sub(r'[\(\)]', '', team)
        else:
            fp_id = None
            player_name = None
            team = None
        # Append player info to row_data
        row_data.extend([year, week, fp_id, player_name, position, team, rank])
        # Process the remaining cells
        for cell in cells[2:]:
            text = cell.get_text(strip=True)
            row_data.append(text)
        data.append(row_data)

    # Remove 'Player' from the column names since we processed it separately
    stats_columns = column_names.copy()
    stats_columns.pop(1)  # Remove 'Player' column

    columns = ['year', 'week', 'fantasypros_id', 'player_name', 'position', 'team', 'pos_rank'] + stats_columns[1:]

    # Create DataFrame
    stats_df = pd.DataFrame(data, columns=columns)

    # Identify numeric columns dynamically
    non_numeric_cols = ['year', 'week', 'fantasypros_id', 'player_name', 'position', 'team', 'pos_rank']
    for col in stats_df.columns:
        if col not in non_numeric_cols:
            # Clean and convert to numeric
            stats_df[col] = stats_df[col].str.replace(',', '').str.rstrip('%')
            stats_df[col] = pd.to_numeric(stats_df[col], errors='coerce')

    # Convert 'ROST' column to float percentage if it exists
    rost_col = [col for col in stats_df.columns if 'ROST' in col.upper()]
    if rost_col:
        col = rost_col[0]
        stats_df[col] = stats_df[col] / 100.0

    # Return stats DataFrame
    return stats_df

class StatsDB:
    """Reads season & weekly fantasy stats from a local SQLite DB — no network calls.

    The data is the FantasyPros-schema stats (``FPTS``, ``RUSHING_YDS``, ``PASSING_TD``,
    ...) keyed by ``fantasypros_id`` (== ``Player.id``). This is the single schema
    produced by ``collect_stats.py`` and copied into every game DB by the engine, so the
    same data is available uniformly for every position (including K/DST) both in the
    local harness and inside the container.

    By default it reads from the bot's own game DB (``DatabaseManager.DB_URL``), which
    already carries copies of ``season_stats`` and ``weekly_stats``. Pass ``db_url`` to
    read from a different SQLite database.
    """

    def __init__(self, years: List[int], include_k_dst: bool = False, db_url: str = None):
        # `include_k_dst` is accepted for backwards compatibility and ignored: K and DST
        # now live in the same tables and schema as every other position.
        self.years = [int(y) for y in years] if years else []
        if db_url is None:
            from blitz_env.models import DatabaseManager
            db_url = DatabaseManager.DB_URL
        engine = create_engine(db_url)
        self.seasonal_df = self._load_table(engine, "season_stats")
        self.weekly_df = self._load_table(engine, "weekly_stats")

    def _load_table(self, engine, table: str) -> pd.DataFrame:
        try:
            df = pd.read_sql(f"SELECT * FROM {table}", engine)
        except Exception:
            # Table may be absent (e.g. a draft-only game DB has no weekly_stats).
            return pd.DataFrame()
        # Normalize keys/years. weekly_stats stores the year as `year`, season_stats as
        # `season`; expose a uniform numeric `season` either way, and a numeric `week`.
        if "season" not in df.columns and "year" in df.columns:
            df["season"] = df["year"]
        for col in ("season", "week"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        if "fantasypros_id" in df.columns:
            df["fantasypros_id"] = df["fantasypros_id"].astype(str)
        if self.years and "season" in df.columns:
            df = df[df["season"].isin(self.years)]
        return df.reset_index(drop=True)

    def get_weekly_data(self, player: Player) -> pd.DataFrame:
        """Weekly stats rows for a player (FantasyPros schema), keyed by Player.id."""
        if self.weekly_df.empty:
            return self.weekly_df
        return self.weekly_df[self.weekly_df["fantasypros_id"] == str(player.id)]

    def get_seasonal_data(self, player: Player) -> pd.DataFrame:
        """Seasonal stats rows for a player (FantasyPros schema), keyed by Player.id."""
        if self.seasonal_df.empty:
            return self.seasonal_df
        return self.seasonal_df[self.seasonal_df["fantasypros_id"] == str(player.id)]
