from concurrent import futures
import logging
import math
import time

import grpc
import agent_pb2
import agent_pb2_grpc
from bot import PerformFantasyActions

class AgentServiceServicer(agent_pb2_grpc.AgentServiceServicer):

    def __init__(self):
        print("Initialized gRPC server")

    def PerformFantasyActions(self, request, context):
        print("Input from GRPC: ")
        print(request)

        return PerformFantasyActions(request)

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    agent_pb2_grpc.add_AgentServiceServicer_to_server(
        AgentServiceServicer(), server
    )
    server.add_insecure_port("[::]:8080")
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig()
    serve()