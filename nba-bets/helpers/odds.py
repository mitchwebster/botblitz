import os
import requests
from datetime import datetime, date

BOOKMAKER_CHOICE = "fanduel"
PLAYER_POINTS = "player_points"
ODDS_FORMAT = "american"
REGION = "us"

def get_today_nba_events():
    events_api_url = "https://api.the-odds-api.com/v4/sports/basketball_nba/events"
    params = {
        "apiKey": os.environ["ODDS_API_KEY"],
    }
    response = requests.get(events_api_url, params=params)
    # Check if the request was successful (status code 200)
    if response.status_code == 200:
        games = response.json()
        
        today_date = date.today()
        # update commence_tim to datetime object for ease of use
        for game in games:
            game['commence_time'] = datetime.fromisoformat(game['commence_time'].replace('Z', '+00:00'))
        
        today_games = [game for game in games if game['commence_time'].date() == today_date]
        if not today_games:
            print("No games found for today.")
        return today_games
    else:
        print(f"Failed to retrieve data. Status code: {response.status_code}")
    return []

def fetch_event_odds(event_id, regions, markets, oddsFormat):
    """Fetch odds data for a specific event from the API."""
    event_odds_api_url = f"https://api.the-odds-api.com/v4/sports/basketball_nba/events/{event_id}/odds"
    params = {
        "apiKey": os.environ["ODDS_API_KEY"],
        "regions": regions,
        "markets": markets,
        "oddsFormat": oddsFormat
    }
    response = requests.get(event_odds_api_url, params=params)
    response.raise_for_status()
    return response.json()

# This has only been tested for player prop bets
def find_bookmaker_bets(data, bookmaker_key=BOOKMAKER_CHOICE):
    bookmakers = data.get("bookmakers", [])
    for bookmaker in bookmakers:
        if bookmaker["key"] == bookmaker_key:
            bets = []
            markets = bookmaker.get("markets", [])
            for market in markets:
                outcomes = market.get("outcomes", [])
                for outcome in outcomes:
                    bets.append({
                        "name": outcome.get("description"),
                        "price": outcome.get("price"),
                        "point": outcome.get("point"),
                        "type": outcome.get("name")
                    })
            return bets
    return None

def get_player_props(event_id):
    try:
        data = fetch_event_odds(event_id, REGION, PLAYER_POINTS, ODDS_FORMAT)
        return find_bookmaker_bets(data)
    except requests.HTTPError as e:
        print(f"Failed to retrieve data. Error: {e}")
        return None
    