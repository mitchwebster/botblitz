# blitz_env is the public package that bots import at runtime (shipped as the
# blitz_env-0.1.0 wheel baked into the py_grpc_server Docker image). Two draft/scoring
# backends coexist on purpose and must both remain importable:
#   - CSV / in-memory:  load_players, simulate_draft, score_game  (init_game_state(year))
#   - sqlite-backed:    *_sqlite modules + models.DatabaseManager  (init_database(year))
# The 2025 engine uses the sqlite/DatabaseManager path; the CSV path is kept for
# backward compatibility with existing and historically-fetched bots. Do not remove
# either backend without an intentional, versioned API deprecation.
from .load_players import load_players
from .stats_db import StatsDB
from .simulate_draft import is_drafted, simulate_draft, visualize_draft_board
from .agent_pb2 import (
    Player, 
    GameState, 
    DraftSelection, 
    WaiverClaim,
    AttemptedFantasyActions,
    Bot,
    PlayerStatus,
    LeagueSettings,
    PlayerSlot,
)
from.models import (
    Player,
    Bot,
    LeagueSettings,
    GameStatus,
    DatabaseManager
)
from .projections_db import ProjectionsDB