import os, sys, json
from blitz_env import DraftSelection, AddDropSelection, GameState, Bot, Player, PlayerStatus, LeagueSettings, PlayerSlot
from google.protobuf.json_format import MessageToJson, ParseDict
import subprocess

def create_sample_game_state():
    """Create a sample GameState for testing"""
    
    # Create sample players
    players = [
        Player(
            id="1",
            full_name="Christian McCaffrey",
            allowed_positions=["RB"],
            professional_team="SF",
            player_bye_week=9,
            rank=1,
            tier=1,
            position_rank=1,
            position_tier=1,
            gsis_id="00-0039596"
        ),
        Player(
            id="2", 
            full_name="Tyreek Hill",
            allowed_positions=["WR"],
            professional_team="MIA",
            player_bye_week=11,
            rank=2,
            tier=1,
            position_rank=1,
            position_tier=1,
            gsis_id="00-0031234"
        ),
        Player(
            id="3",
            full_name="Josh Allen",
            allowed_positions=["QB"],
            professional_team="BUF",
            player_bye_week=13,
            rank=3,
            tier=1,
            position_rank=1,
            position_tier=1,
            gsis_id="00-0035678"
        ),
        Player(
            id="4",
            full_name="CeeDee Lamb",
            allowed_positions=["WR"],
            professional_team="DAL",
            player_bye_week=7,
            rank=4,
            tier=1,
            position_rank=2,
            position_tier=1,
            gsis_id="00-0039012"
        ),
        Player(
            id="5",
            full_name="Bijan Robinson",
            allowed_positions=["RB"],
            professional_team="ATL",
            player_bye_week=12,
            rank=5,
            tier=2,
            position_rank=2,
            position_tier=2,
            gsis_id="00-0043456"
        ),
        Player(
            id="6",
            full_name="Travis Kelce",
            allowed_positions=["TE"],
            professional_team="KC",
            player_bye_week=10,
            rank=6,
            tier=1,
            position_rank=1,
            position_tier=1,
            gsis_id="00-0027890"
        ),
        Player(
            id="7",
            full_name="Saquon Barkley",
            allowed_positions=["RB"],
            professional_team="PHI",
            player_bye_week=5,
            rank=7,
            tier=2,
            position_rank=3,
            position_tier=2,
            gsis_id="00-0032345"
        ),
        Player(
            id="8",
            full_name="Amon-Ra St. Brown",
            allowed_positions=["WR"],
            professional_team="DET",
            player_bye_week=9,
            rank=8,
            tier=2,
            position_rank=3,
            position_tier=2,
            gsis_id="00-0046789"
        ),
        Player(
            id="9",
            full_name="Jalen Hurts",
            allowed_positions=["QB"],
            professional_team="PHI",
            player_bye_week=5,
            rank=9,
            tier=2,
            position_rank=2,
            position_tier=2,
            gsis_id="00-0040123"
        ),
        Player(
            id="10",
            full_name="Garrett Wilson",
            allowed_positions=["WR"],
            professional_team="NYJ",
            player_bye_week=7,
            rank=10,
            tier=2,
            position_rank=4,
            position_tier=2,
            gsis_id="00-0044567"
        )
    ]
    
    # Create sample bots
    bots = [
        Bot(
            id="bot1",
            fantasy_team_name="Team Alpha",
            owner="Alice",
            source_type=Bot.Source.LOCAL
        ),
        Bot(
            id="bot2", 
            fantasy_team_name="Team Beta",
            owner="Bob",
            source_type=Bot.Source.LOCAL
        ),
        Bot(
            id="bot3",
            fantasy_team_name="Team Gamma", 
            owner="Charlie",
            source_type=Bot.Source.LOCAL
        ),
        Bot(
            id="bot4",
            fantasy_team_name="Team Delta",
            owner="Diana", 
            source_type=Bot.Source.LOCAL
        )
    ]
    
    # Create league settings
    league_settings = LeagueSettings(
        num_teams=4,
        total_rounds=10,
        is_snake_draft=True,
        points_per_reception=1.0,
        year=2024
    )
    
    # Add player slots
    league_settings.slots_per_team.extend([
        PlayerSlot(name="QB", allowed_player_positions=["QB"]),
        PlayerSlot(name="RB1", allowed_player_positions=["RB"]),
        PlayerSlot(name="RB2", allowed_player_positions=["RB"]),
        PlayerSlot(name="WR1", allowed_player_positions=["WR"]),
        PlayerSlot(name="WR2", allowed_player_positions=["WR"]),
        PlayerSlot(name="TE", allowed_player_positions=["TE"]),
        PlayerSlot(name="FLEX", allowed_player_positions=["RB", "WR", "TE"]),
        PlayerSlot(name="K", allowed_player_positions=["K"]),
        PlayerSlot(name="DST", allowed_player_positions=["DST"]),
        PlayerSlot(name="Bench", allows_any_position=True)
    ])
    
    # Create the game state
    game_state = GameState(
        players=players,
        bots=bots,
        league_settings=league_settings,
        current_bot_team_id="bot1",
        current_draft_pick=1,
        current_fantasy_week=1
    )
    
    return game_state

# Create the sample game state
request = create_sample_game_state()
serialized_request = MessageToJson(request)
print(serialized_request)

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
stdout, stderr = proc.communicate(input=serialized_request)

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