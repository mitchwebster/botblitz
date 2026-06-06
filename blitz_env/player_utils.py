from blitz_env.agent_pb2 import Player, PlayerStatus


def is_drafted(player: Player) -> bool:
    """True if a (protobuf) player has been drafted or is on hold.

    Operates on agent_pb2.Player (status.availability enum). This is the runtime-SDK
    helper imported by py_grpc_server/bot.py; keep it dependency-light (agent_pb2 only).
    """
    return player.status.availability in (
        PlayerStatus.Availability.DRAFTED,
        PlayerStatus.Availability.ON_HOLD,
    )
