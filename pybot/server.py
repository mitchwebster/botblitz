from concurrent import futures
import logging
import math
import time

import grpc
import agent_pb2
import agent_pb2_grpc

class AgentServiceServicer(agent_pb2_grpc.AgentServiceServicer):

    def __init__(self):
        print("Initialized gRPC server")

    def PerformFantasyActions(self, request, context):
        print("Performing bot actions")
        print(request)

        return agent_pb2.FantasySelections(
            make_bet=True
            # slots = [
            #     agent_pb2.PlayerSlot(name="QB", assigned_player_id="007")
            # ]
        )

        # context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        # context.set_details('Method not implemented!')
        # raise NotImplementedError('Method not implemented!')

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