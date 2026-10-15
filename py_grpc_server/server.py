from concurrent import futures
import logging
import subprocess, os, json, hmac
from google.protobuf.json_format import ParseDict
from blitz_env import DraftSelection, AttemptedFantasyActions

import grpc
from agent_pb2_grpc import AgentServiceServicer, add_AgentServiceServicer_to_server

# Per-container secret from the engine (BOTBLITZ_RPC_TOKEN, see ContainerHandler.go).
# Every RPC must carry it, so another bot that can reach this port (e.g. via
# host.docker.internal on Docker Desktop) can't call — or flood — this bot. Popped
# before any bot code is imported just to keep it out of the bot's way; a bot can only
# ever learn its own container's token, which grants nothing.
_RPC_TOKEN = os.environ.pop("BOTBLITZ_RPC_TOKEN", "")
_RPC_TOKEN_METADATA_KEY = "x-botblitz-token"

# When BOTBLITZ_EVAL_INLINE=1 is set (evaluation path only), the bot module is imported
# once at server startup so subsequent calls pay no Python cold-start cost. The
# subprocess isolation path (production default) remains unchanged.
_INLINE = os.getenv("BOTBLITZ_EVAL_INLINE") == "1"
if _INLINE:
    import bot as _bot_module
    print("BOTBLITZ_EVAL_INLINE: bot module loaded, using in-process execution")


class _RequireTokenInterceptor(grpc.ServerInterceptor):
    def __init__(self, token):
        self._token = token.encode()

        def deny(request, context):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "missing or invalid rpc token")

        self._deny_handler = grpc.unary_unary_rpc_method_handler(deny)

    def intercept_service(self, continuation, handler_call_details):
        metadata = dict(handler_call_details.invocation_metadata or ())
        supplied = metadata.get(_RPC_TOKEN_METADATA_KEY, "").encode()
        if hmac.compare_digest(supplied, self._token):
            return continuation(handler_call_details)
        return self._deny_handler

class AgentServiceServicer(AgentServiceServicer):

    def __init__(self):
        print("Initialized gRPC server")

    def perform_action_in_isolation(self, action):
        # Create pipe for result communication
        r, w = os.pipe()

        # Start the subprocess
        proc = subprocess.Popen(
            ["python3", "isolate_action.py", str(w), action],
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
        return result_dict

    def DraftPlayer(self, request, context):
        if _INLINE:
            player_id = _bot_module.draft_player()
            return DraftSelection(player_id=player_id)
        result_dict = self.perform_action_in_isolation("draft")
        player_selection = ParseDict(result_dict, DraftSelection())
        return DraftSelection(player_id=player_selection.player_id)

    def PerformWeeklyFantasyActions(self, request, context):
        if _INLINE:
            return _bot_module.perform_weekly_fantasy_actions()
        result_dict = self.perform_action_in_isolation("perform_weekly_fantasy_actions")
        return ParseDict(result_dict, AttemptedFantasyActions())

def serve():
    # Without a token (running server.py by hand) there's nothing to check against;
    # the engine always sets one.
    interceptors = [_RequireTokenInterceptor(_RPC_TOKEN)] if _RPC_TOKEN else []
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10), interceptors=interceptors)
    add_AgentServiceServicer_to_server(
        AgentServiceServicer(), server
    )
    server.add_insecure_port("[::]:8080")
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig()
    serve()