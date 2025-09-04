import json
import os

import pandas as pd

try:
    import openai
except ImportError:
    openai = None

from blitz_env import AddDropSelection, GameState
from blitz_env.models import DatabaseManager


def get_projections_df(db: DatabaseManager) -> pd.DataFrame:
    """Get 2025 projections with value-over-replacement calculations."""
    year = db.get_league_settings().year

    expected_draft_count = {"RB": 52, "WR": 60, "QB": 32, "TE": 8, "K": 13, "DST": 13}

    projections_df = pd.read_sql(
        f"SELECT * FROM preseason_projections WHERE year = {year}", db.engine
    )

    if projections_df.empty:
        return projections_df

    projections_df["FPTS"] = pd.to_numeric(projections_df["FPTS"], errors="coerce")

    def avg_top_by_position(group: pd.DataFrame) -> float:
        n = expected_draft_count.get(group.name.upper(), 0)
        if n <= 0:
            return np.nan
        return group.nlargest(n, "FPTS")["FPTS"].mean()

    avg_top_fpts = (
        projections_df.groupby("position", group_keys=False)
        .apply(avg_top_by_position)
        .dropna()
    )

    avg_map = avg_top_fpts.to_dict()
    projections_df["Value"] = projections_df.apply(
        lambda row: row["FPTS"] - avg_map.get(row["position"], 0), axis=1
    )

    return projections_df


def get_current_round(db):
    settings_df = pd.read_sql(
        "SELECT * FROM league_settings", db.engine
    )  # get leagueSettings
    leagueSettings = settings_df.iloc[0]

    status_df = pd.read_sql("SELECT * FROM game_statuses", db.engine)  # get game status
    gameSettings = status_df.iloc[0]

    zero_based_round = (gameSettings["current_draft_pick"] - 1) // leagueSettings[
        "num_teams"
    ]
    return zero_based_round + 1


def get_total_rounds(db) -> int:
    settings_df = pd.read_sql(
        "SELECT * FROM league_settings", db.engine
    )  # get game status
    return settings_df.iloc[0]["total_rounds"]


def get_my_team(db: DatabaseManager) -> pd.DataFrame:
    """Get my current team roster."""
    players_df = pd.read_sql("SELECT * FROM players", db.engine)
    my_bot_id = db.get_game_status().current_bot_id
    my_team = players_df[players_df["current_bot_id"] == my_bot_id]

    my_team["position"] = my_team["allowed_positions"].apply(
        lambda x: json.loads(x)[0] if x else None
    )

    return my_team


def get_position_counts(my_team: pd.DataFrame) -> Dict[str, int]:
    """Get count of players by position."""
    return my_team.groupby("position")["position"].count().to_dict()


POSITION_PENALTIES = {
    "RB": {0: 0, 1: 10, 2: 20, 3: 30, 4: 50, 5: 100, 6: 200},
    "WR": {0: 0, 1: 10, 2: 20, 3: 30, 4: 50, 5: 100, 6: 200},
    "TE": {0: 0, 1: 20, 2: 100, 3: 500},
    "QB": {0: 0, 1: 10, 2: 100, 3: 500},
    "K": {0: 50, 1: 500},
    "DST": {0: 50, 1: 500},
}


def calculate_roster_value_penalty(position: str, drafted_count: int) -> float:
    """Calculate penalty for having too many players at a position."""
    penalties = POSITION_PENALTIES.get(position, {})
    return penalties.get(drafted_count, 1000)


def get_allowed_positions_to_draft(
    current_round: int, total_rounds: int, position_counts: Dict[str, int]
) -> Set[str]:
    """Determine which positions are allowed to draft based on round and roster."""
    if current_round == total_rounds:
        return {"K"}
    elif current_round + 1 == total_rounds:
        return {"DST"}
    elif current_round < 9:
        positions = {"QB", "WR", "RB"}
    else:
        positions = {"QB", "WR", "RB", "TE"}

    if position_counts.get("QB", 0) >= 2:
        positions.discard("QB")
    if position_counts.get("TE", 0) >= 1:
        positions.discard("TE")

    return positions


def get_players_with_value(db: DatabaseManager) -> pd.DataFrame:
    """Get all players with value calculations based on projections."""
    players_df = pd.read_sql("SELECT * FROM players", db.engine)
    projections_df = get_projections_df(db)

    if projections_df.empty:
        players_df["Value"] = 0
        players_df["FPTS"] = 0
        return players_df

    kept_columns = ["fantasypros_id", "FPTS", "Value"]
    df = players_df.merge(
        projections_df[kept_columns],
        left_on="id",
        right_on="fantasypros_id",
        how="left",
    )

    df["position"] = df["allowed_positions"].apply(
        lambda x: json.loads(x)[0] if x else None
    )

    df["Value"] = df["Value"].fillna(0)
    df["FPTS"] = df["FPTS"].fillna(0)

    return df


def calculate_roster_value(
    player_row: pd.Series, position_counts: Dict[str, int], current_round: int
) -> float:
    """Calculate the roster-adjusted value of a player."""
    base_value = player_row.get("Value", 0)
    position = player_row.get("position")

    if not position:
        return base_value

    drafted_count = position_counts.get(position, 0)
    penalty = calculate_roster_value_penalty(position, drafted_count)

    # Positional bonuses/penalties based on draft strategy
    if position == "QB" and current_round <= 6:
        base_value += 5  # Prioritize QB early in superflex
    elif position == "TE" and player_row.get("position_tier", 99) > 2:
        penalty += 20  # Avoid late-tier TEs
    elif position in ["RB", "WR"] and current_round >= 12:
        base_value += 3  # Value depth at skill positions late

    return base_value - penalty


def ai_draft_veto(
    player_name: str, position: str, current_round: int, my_team_summary: str
) -> bool:
    """Use OpenAI to potentially veto a draft pick based on fantasy football strategy."""
    if not openai:
        return False  # No veto if OpenAI unavailable

    try:
        # Set up OpenAI client
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return False

        client = openai.OpenAI(api_key=api_key)

        prompt = f"""You are a fantasy football expert analyzing a 2025 draft pick.

Player being considered: {player_name} ({position})
Current round: {current_round}
My current roster: {my_team_summary}

Should I VETO this pick? Consider:
- Injury risk for 2025
- Age/decline concerns
- Better alternatives likely available
- Positional value
- Draft strategy

Respond with only "VETO" or "APPROVE" and a brief reason (max 30 words)."""

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50,
            temperature=0.3,
        )

        ai_response = response.choices[0].message.content.strip()
        print(f"🤖 AI Analysis: {ai_response}")

        return ai_response.upper().startswith("VETO")

    except Exception as e:
        print(f"AI veto error: {e}")
        return False  # Default to no veto on error


def draft_player() -> str:
    """Select the best available player based on value and roster construction."""

    db = DatabaseManager()
    try:
        current_round = get_current_round(db)
        total_rounds = get_total_rounds(db)
        my_team = get_my_team(db)
        position_counts = get_position_counts(my_team)
        positions_to_draft = get_allowed_positions_to_draft(
            current_round, total_rounds, position_counts
        )

        # Get all available players with value calculations
        df = get_players_with_value(db)
        available_df = df[df["availability"] == "AVAILABLE"].copy()

        if available_df.empty:
            return ""

        # Filter by allowed positions
        available_df = available_df[available_df["position"].isin(positions_to_draft)]

        # Forbid Christian McCaffrey
        available_df = available_df[
            ~available_df["full_name"].str.contains(
                "Christian McCaffrey", case=False, na=False
            )
        ]

        if available_df.empty:
            return ""

        # Calculate roster-adjusted value for each player
        available_df["RosterValue"] = available_df.apply(
            lambda row: calculate_roster_value(row, position_counts, current_round),
            axis=1,
        )

        # Get top candidates and check for AI veto
        top_candidates = available_df.nlargest(3, "RosterValue")

        # Create team summary for AI
        team_positions = [f"{pos}: {count}" for pos, count in position_counts.items()]
        team_summary = f"Round {current_round}, Roster: {', '.join(team_positions)}"

        for _, candidate in top_candidates.iterrows():
            player_name = candidate.get("full_name", "Unknown")
            position = candidate.get("position", "Unknown")

            # Check if AI wants to veto this pick
            if ai_draft_veto(player_name, position, current_round, team_summary):
                print(f"🚫 AI vetoed {player_name}, trying next best option...")
                continue
            else:
                print(f"✅ AI approved: {player_name}")
                return candidate["id"]

        # If all top 3 were vetoed, just pick the best remaining
        best_player = available_df.nlargest(1, "RosterValue").iloc[0]
        print(
            f"⚠️ AI vetoed top picks, selecting: {best_player.get('full_name', 'Unknown')}"
        )
        return best_player["id"]

    except Exception as e:
        print(f"Error in draft_player: {e}")
        return ""
    finally:
        db.close()


def find_best_add_drop(db: DatabaseManager) -> Tuple[Optional[str], Optional[str]]:
    """Find the best waiver wire add and corresponding drop."""
    my_team = get_my_team(db)
    position_counts = get_position_counts(my_team)

    # Get available players on waivers
    df = get_players_with_value(db)
    available_df = df[df["availability"] == "AVAILABLE"].copy()

    if available_df.empty:
        return None, None

    # Calculate value for available players
    available_df["RosterValue"] = available_df.apply(
        lambda row: calculate_roster_value(row, position_counts, 16), axis=1
    )

    # Find best available player
    best_available = available_df.nlargest(1, "RosterValue")
    if best_available.empty:
        return None, None

    best_add = best_available.iloc[0]

    # Find worst player on my team at same position (or overall worst)
    same_position_team = my_team[my_team["position"] == best_add["position"]]
    if not same_position_team.empty:
        worst_same_pos = same_position_team.nsmallest(1, "FPTS")
        if not worst_same_pos.empty:
            worst_player = worst_same_pos.iloc[0]
            # Only drop if the add is significantly better
            if best_add["RosterValue"] > worst_player.get("Value", 0) + 5:
                return best_add["id"], worst_player["id"]

    return None, None


def propose_add_drop(game_state: GameState) -> AddDropSelection:
    """Smart waiver wire management based on value analysis."""
    db = DatabaseManager()
    try:
        add_id, drop_id = find_best_add_drop(db)
        return AddDropSelection(
            player_to_add_id=add_id or "", player_to_drop_id=drop_id or ""
        )
    except Exception as e:
        print(f"Error in propose_add_drop: {e}")
        return AddDropSelection(player_to_add_id="", player_to_drop_id="")
    finally:
        db.close()
