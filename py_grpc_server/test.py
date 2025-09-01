import os, sys, json
from blitz_env import DraftSelection
from google.protobuf.json_format import ParseDict
import subprocess

# Create pipe for result communication
r, w = os.pipe()

# Start the subprocess
proc = subprocess.Popen(
    ["python3", "isolate_action.py", str(w)],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    pass_fds=(w,),
)

# Close the write end in the parent process
os.close(w)

# Send input and wait for completion in one call
stdout, stderr = proc.communicate()

# Now read the result from the pipe
with os.fdopen(r) as fr:
    result = fr.read()

print("Debug stdout:", stdout[:2000])
print("Debug stderr:", stderr[:2000])
print("Result from pipe:", result[:200])

# Parse the result
if result.strip():
    result_dict = json.loads(result)
    player_selection = ParseDict(result_dict, DraftSelection())
    print(player_selection.player_id)
else:
    print("No result received from pipe")