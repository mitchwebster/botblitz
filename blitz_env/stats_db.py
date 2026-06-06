import pandas as pd
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

