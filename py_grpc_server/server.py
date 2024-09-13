from concurrent import futures
import logging
import math
import time

import grpc
from blitz_env import DraftSelection
from agent_pb2_grpc import AgentServiceServicer, add_AgentServiceServicer_to_server
from bot import draft_player

class AgentServiceServicer(AgentServiceServicer):

    def __init__(self):
        print("Initialized gRPC server")

    def PerformFantasyActions(self, request, context):
        print("Input from GRPC: ")
        print(request)

        player_selection_id = draft_player(request)
        print("player selected: ")
        print(player_selection_id)
        
        return DraftSelection(
            player_id=player_selection_id
        )

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