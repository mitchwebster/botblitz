from concurrent import futures
import logging
import math
import time

import grpc
from blitz_env import DraftSelection, AddDropSelection
from agent_pb2_grpc import AgentServiceServicer, add_AgentServiceServicer_to_server
from bot import draft_player, propose_add_drop

class AgentServiceServicer(AgentServiceServicer):

    def __init__(self):
        print("Initialized gRPC server")

    def DraftPlayer(self, request, context):
        print("Received Request: ")
        # print(request)

        player_selection_id = draft_player(request)
        print("player selected: ")
        print(player_selection_id)
        
        return DraftSelection(
            player_id=player_selection_id
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