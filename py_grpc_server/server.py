from concurrent import futures
import logging
import subprocess, os, json
from google.protobuf.json_format import ParseDict
from blitz_env import DraftSelection

import grpc
from agent_pb2_grpc import AgentServiceServicer, add_AgentServiceServicer_to_server
from bot import propose_add_drop

class AgentServiceServicer(AgentServiceServicer):

    def __init__(self):
        print("Initialized gRPC server")

    def DraftPlayer(self, request, context):
        print("Received Request: ")

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
        if not result.strip():
            raise Exception("No response from bot: " + stderr)

        result_dict = json.loads(result)
        player_selection = ParseDict(result_dict, DraftSelection())

        return DraftSelection(
            player_id=player_selection.player_id
        )
    
    def ProposeAddDrop(self, request, context):
        print("Received Request: ")
        # print(request)

        return propose_add_drop(request)

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    add_AgentServiceServicer_to_server(
        AgentServiceServicer(), server
    )
    server.add_insecure_port("[::]:8080")
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig()
    serve()