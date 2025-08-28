import os, sys, json
from blitz_env import DraftSelection, AddDropSelection, GameState
from bot import draft_player
from google.protobuf.json_format import MessageToJson, ParseDict

fd = int(sys.argv[1])

payload = sys.stdin.read()
request = json.loads(payload)

with os.fdopen(fd, "w") as f:
    env = ParseDict(request, GameState())
    player_id = draft_player(env)

    # Create a DraftSelection object with the player ID
    response = DraftSelection(player_id=player_id)

    serialized_response = MessageToJson(response)
    f.write(serialized_response)