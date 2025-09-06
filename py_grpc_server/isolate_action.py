import os, sys
from blitz_env import DraftSelection
from bot import draft_player, perform_weekly_fantasy_actions
from google.protobuf.json_format import MessageToJson

fd = int(sys.argv[1])
action = str(sys.argv[2])

with os.fdopen(fd, "w") as f:
    serialized_response = ""
    if action == "draft":
        player_id = draft_player()
        response = DraftSelection(player_id=player_id)
        serialized_response = MessageToJson(response)
    elif action == "perform_weekly_fantasy_actions":
        response = perform_weekly_fantasy_actions()
        serialized_response = MessageToJson(response)

    f.write(serialized_response)