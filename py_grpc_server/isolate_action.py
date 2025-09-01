import os, sys
from blitz_env import DraftSelection
from bot import draft_player
from google.protobuf.json_format import MessageToJson

fd = int(sys.argv[1])

with os.fdopen(fd, "w") as f:
    player_id = draft_player()

    # Create a DraftSelection object with the player ID
    response = DraftSelection(player_id=player_id)

    serialized_response = MessageToJson(response)
    f.write(serialized_response)