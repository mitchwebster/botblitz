import os
import requests
import json


def send_to_discord(message):
    # Create a dictionary with the message content
    payload = {
        "content": message
    }
    
    payload_json = json.dumps(payload)

    requests.post(os.environ["DISCORD_WEBHOOK"], data=payload_json, headers={'Content-Type': 'application/json'})