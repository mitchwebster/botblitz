import json

from blitz_env.agent_pb2 import Player, PlayerStatus


def parse_positions(allowed_positions) -> list:
    """Normalize a player's ``allowed_positions`` to a list of strings.

    ``players.allowed_positions`` is a JSON column. Reading it through the
    SQLAlchemy ORM (``Player.allowed_positions``) yields a Python list, but
    reading it with raw SQL / pandas (``pd.read_sql``) yields the underlying JSON
    *string* (e.g. ``'["WR"]'``) — SQLite has no native list type, so a raw query
    cannot return a list. This accepts either shape (list, JSON string, or None)
    and always returns a list, so bots don't have to care which read path
    produced the value::

        from blitz_env import parse_positions
        parse_positions('["WR"]')   # -> ["WR"]   (raw SQL / pandas)
        parse_positions(["RB"])     # -> ["RB"]   (ORM)
        parse_positions(None)       # -> []
    """
    if allowed_positions is None:
        return []
    if isinstance(allowed_positions, str):
        return json.loads(allowed_positions) if allowed_positions else []
    return list(allowed_positions)


def is_drafted(player: Player) -> bool:
    """True if a (protobuf) player has been drafted or is on hold.

    Operates on agent_pb2.Player (status.availability enum). This is the runtime-SDK
    helper imported by py_grpc_server/bot.py; keep it dependency-light (agent_pb2 only).
    """
    return player.status.availability in (
        PlayerStatus.Availability.DRAFTED,
        PlayerStatus.Availability.ON_HOLD,
    )
