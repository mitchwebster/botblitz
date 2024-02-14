import os
import sqlite3
from helpers.discord import send_to_discord
from helpers.odds import get_today_nba_events, get_player_props
from helpers.betsdb import store_bet
import random
from datetime import datetime
import pytz

def get_date_formatted_pt(t):
    # Define the Pacific Time (PT) timezone
    pt_timezone = pytz.timezone('US/Pacific')
    # Convert the datetime object to Pacific Time (PT)
    timestamp_pt = t.astimezone(pt_timezone)
    # Format the timestamp in a human-readable way
    return timestamp_pt.strftime('%Y-%m-%d %I:%M:%S %p %Z')

def format_chosen_bet(chosen_event, chosen_player_prop):
    return f"""
## Today's Bet
### Game
{chosen_event['away_team']}@{chosen_event['home_team']} starting at {get_date_formatted_pt(chosen_event['commence_time'])}.
### Bet
{chosen_player_prop['name']} to get {chosen_player_prop['type']} {chosen_player_prop['point']} points. Price: {chosen_player_prop['price']}
"""

def main():
    events = get_today_nba_events()
    chosen_event = random.choice(events)
    event_player_props = get_player_props(chosen_event["id"])
    chosen_player_prop = random.choice(event_player_props)
    store_bet(chosen_event, chosen_player_prop)
    send_to_discord(format_chosen_bet(chosen_event, chosen_player_prop))

if __name__ == "__main__":
    main()