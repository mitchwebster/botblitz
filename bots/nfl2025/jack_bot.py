from blitz_env import AttemptedFantasyActions, WaiverClaim
from blitz_env.models import DatabaseManager
import pandas as pd
import json, os

# ---------------------------------------------------------------------------
# VORP draft valuation core (issue #263)
#
# These are PURE functions: they take plain pandas DataFrames / dicts and
# return DataFrames / dicts. They never touch the DB or engine, so the whole
# valuation can be unit-tested offline. The DB-reading wiring that feeds them
# lives in the small helpers below (used by draft_player / #264), never inside
# the scoring math.
# ---------------------------------------------------------------------------

# Fraction of the projection kept when a player has last-season actuals to
# blend in (the rest comes from the historical actual). Projection-only players
# (rookies / no history) keep 100% projection.
RELIABILITY_PROJECTION_WEIGHT = 0.7

# Share of SUPERFLEX slots that get spent on a second QB across the league.
# Discounted below 1.0 because some teams "punt" the second QB and flex an
# RB/WR there instead. This is what makes the SUPERFLEX QB baseline reflect
# ~two QBs started per team.
SUPERFLEX_QB_FRACTION = 0.75

# How FLEX (and the non-QB share of SUPERFLEX) slots are spread over the
# flex-eligible positions when deriving replacement depth.
FLEX_WEIGHTS = {"RB": 0.45, "WR": 0.45, "TE": 0.10}

# Scarcity / tier-cliff bonus. Added as TIER_CLIFF_BONUS / position_tier so the
# top tier gets the full bump and it decays for lower (worse) tiers, rewarding
# players sitting just above a positional talent cliff.
TIER_CLIFF_BONUS = 8.0

# Players with no projection are ranked strictly below every projected player
# and ordered amongst themselves by players.rank (lower rank == better).
NO_PROJECTION_FLOOR = -1.0e6

FLEX_ELIGIBLE = {"RB", "WR", "TE"}


def compute_replacement_baselines(slots, num_teams):
    """Per-position replacement *depth* derived from this league's started slots.

    Returns a dict ``{position: depth}`` where ``depth`` is the number of
    players at that position drafted as startable across the whole league. That
    depth is the index of the "replacement-level" player; ``score_draft_pool``
    turns it into a replacement FPTS by reading the player at that depth out of
    the currently-available pool.

    ``slots`` is the league's slot->count map (e.g. the parsed
    ``league_settings.player_slots``: QB/RB/WR/TE/FLEX/SUPERFLEX/BENCH/K/DST).
    ``num_teams`` is the number of teams in the league.

    SUPERFLEX is mostly spent on a second QB (``SUPERFLEX_QB_FRACTION``), so the
    QB baseline reflects ~two QBs started per team — far deeper than a 1-QB
    league — which is what creates the QB premium in a superflex format.
    """
    def n(pos):
        return float(slots.get(pos, 0) or 0)

    depth = {pos: n(pos) * num_teams for pos in ["QB", "RB", "WR", "TE", "K", "DST"]}

    flex_pool = n("FLEX") * num_teams
    superflex_pool = n("SUPERFLEX") * num_teams

    # Most superflex slots become a second QB; the rest become extra flex depth.
    depth["QB"] += superflex_pool * SUPERFLEX_QB_FRACTION
    flex_pool += superflex_pool * (1.0 - SUPERFLEX_QB_FRACTION)

    for pos, weight in FLEX_WEIGHTS.items():
        depth[pos] += flex_pool * weight

    return {pos: max(1, int(round(d))) for pos, d in depth.items() if d > 0}


def score_draft_pool(available_df, baselines, my_roster=None):
    """Annotate the available player pool with a VORP-based value score.

    Expected ``available_df`` columns (built by the DB helpers / caller):
      - ``position``        position string (any case)
      - ``projected_fpts``  preseason projected FPTS (NaN if no projection)
      - ``last_season_fpts``last season's actual FPTS (NaN for rookies/no history)
      - ``position_tier``   the player's tier within its position
      - ``rank``            players.rank, used only as no-projection fallback

    Returns a copy sorted best-first with added columns ``blended_fpts``,
    ``baseline_fpts``, ``vorp``, ``tier_bump`` and ``value_score``.

    ``baselines`` is the depth dict from ``compute_replacement_baselines``. The
    baseline FPTS for each position is recomputed here from the *currently
    available* pool (the projection at the replacement depth), so the same
    baselines re-price the field as players come off the board.

    ``my_roster`` is accepted for API stability; positional-need weighting on
    top of the raw valuation is layered in by the draft-pick logic (#264).
    """
    df = available_df.copy().reset_index(drop=True)
    if df.empty:
        for col in ["blended_fpts", "baseline_fpts", "vorp", "tier_bump", "value_score"]:
            df[col] = pd.Series(dtype="float64")
        return df

    pos = df["position"].astype(str).str.upper()
    proj = pd.to_numeric(df.get("projected_fpts"), errors="coerce")
    last = pd.to_numeric(df.get("last_season_fpts"), errors="coerce")

    # Reliability blend: mix projection with last-season actual for players that
    # have history; projection-only otherwise.
    has_hist = last.notna()
    blended = proj.copy()
    blended[has_hist] = (
        RELIABILITY_PROJECTION_WEIGHT * proj[has_hist]
        + (1.0 - RELIABILITY_PROJECTION_WEIGHT) * last[has_hist]
    )
    df["blended_fpts"] = blended

    # Replacement FPTS per position, read from the current available pool at the
    # supplied replacement depth (recomputes as the pool shrinks).
    baseline_fpts = {}
    for position, depth in baselines.items():
        pool = proj[pos == position].dropna().sort_values(ascending=False).reset_index(drop=True)
        if len(pool) == 0:
            baseline_fpts[position] = 0.0
        else:
            idx = min(int(depth) - 1, len(pool) - 1)
            baseline_fpts[position] = float(pool.iloc[idx])
    df["baseline_fpts"] = pos.map(baseline_fpts).fillna(0.0)

    df["vorp"] = df["blended_fpts"] - df["baseline_fpts"]

    # Tier-cliff bump from position_tier (top tier gets the full bonus).
    ptier = pd.to_numeric(df.get("position_tier"), errors="coerce")
    df["tier_bump"] = (TIER_CLIFF_BONUS / ptier).where(ptier.notna() & (ptier > 0), 0.0)

    df["value_score"] = df["vorp"] + df["tier_bump"]

    # No-projection fallback: rank strictly below every projected player and
    # order amongst themselves by players.rank (lower rank == higher value).
    no_proj = proj.isna()
    rank = pd.to_numeric(df.get("rank"), errors="coerce").fillna(1.0e6)
    df.loc[no_proj, "vorp"] = float("nan")
    df.loc[no_proj, "tier_bump"] = float("nan")
    df.loc[no_proj, "value_score"] = NO_PROJECTION_FLOOR - rank[no_proj]

    return df.sort_values("value_score", ascending=False).reset_index(drop=True)


def get_num_teams(db):
    """Number of teams in the league (one row per bot)."""
    return int(pd.read_sql("SELECT COUNT(*) AS n FROM bots", db.engine).iloc[0]["n"])


def load_draft_valuation_inputs(db):
    """Build the available-player DataFrame the pure scorer expects from the DB.

    Joins the available ``players`` pool to this year's ``preseason_projections``
    (projected FPTS) and last year's ``season_stats`` (historical actuals). This
    is the DB seam for the pure functions; the valuation math stays in
    ``score_draft_pool``.
    """
    year = int(db.get_league_settings().year)
    players = pd.read_sql(
        "SELECT * FROM players WHERE availability = 'AVAILABLE'", db.engine
    )
    proj = pd.read_sql(
        "SELECT fantasypros_id, position, FPTS AS projected_fpts "
        f"FROM preseason_projections WHERE year = '{year}'",
        db.engine,
    )
    hist = pd.read_sql(
        "SELECT fantasypros_id, FPTS AS last_season_fpts "
        f"FROM season_stats WHERE year = '{year - 1}'",
        db.engine,
    )
    df = players.merge(proj, left_on="id", right_on="fantasypros_id", how="left")
    df = df.merge(hist, left_on="id", right_on="fantasypros_id", how="left",
                  suffixes=("", "_hist"))
    # Prefer the projection's position; fall back to the first allowed position.
    df["position"] = df["position"].fillna(
        df["allowed_positions"].apply(
            lambda x: json.loads(x)[0] if x else None
        )
    )
    return df

def get_positions_to_fill(db):
    df = pd.read_sql("SELECT * FROM league_settings", db.engine)
    return json.loads(df.iloc[0]["player_slots"])

def get_my_bot_id(db):
    """Return the id of the bot the engine is currently asking to act.

    The engine writes the acting bot into game_statuses.current_bot_id before
    invoking us (each draft pick and each weekly turn), so this is the single
    source of truth for "which team am I?" — never hardcode an id.
    """
    df = pd.read_sql("SELECT current_bot_id FROM game_statuses", db.engine)
    return df.iloc[0]["current_bot_id"]

def get_my_team(db):
    df = pd.read_sql("SELECT * FROM game_statuses", db.engine) # get game status
    draft_pick = df.iloc[0]["current_draft_pick"]
    print(f"Current pick is {draft_pick}")

    bot_id = get_my_bot_id(db)
    queryStr = f"SELECT * FROM players where current_bot_id = '{bot_id}'"
    my_team = pd.read_sql(queryStr, db.engine) 

    player_positions_map = {
        row["full_name"]: (json.loads(row["allowed_positions"])[0] if row["allowed_positions"] else None)
        for _, row in my_team.iterrows()
    }

    return player_positions_map

def adjust_available_positions(remaining_positions_to_fill):
    if "FLEX" in remaining_positions_to_fill:
        remaining_positions_to_fill |= {"RB", "WR", "TE"}

    if "SUPERFLEX" in remaining_positions_to_fill:
        remaining_positions_to_fill |= {"QB", "RB", "WR", "TE"}

    if "BENCH" in remaining_positions_to_fill:
        remaining_positions_to_fill |= {"QB", "RB", "WR", "TE", "K", "DST"}
    
    special_positions = {"FLEX", "SUPERFLEX", "BENCH"}
    remaining_positions_to_fill -= special_positions
    return remaining_positions_to_fill

def load_available_players(db):
    df = pd.read_sql("SELECT * FROM players where availability = 'AVAILABLE'", db.engine)
    return df

def draft_player() -> str:
    """
    Selects a player to draft based on the highest rank.

    Args:
        players (List[Player]): A list of Player objects.

    Returns:
        str: The id of the drafted player.
    """
    db = DatabaseManager()
    try:
        positions_to_fill = get_positions_to_fill(db)
        my_team = get_my_team(db)

        for player_name, position in my_team.items():
            if position in positions_to_fill and positions_to_fill[position] > 0:
                # fill explicit positions first
                positions_to_fill[position] = positions_to_fill[position] - 1
            else:
                # if the position is not explicitly in the map, then begin decrementing the FLEX, SUPERFLEX, and Bench slots
                # start with most specific
                if position in ["RB", "WR", "TE"] and positions_to_fill["FLEX"] > 0:
                    positions_to_fill["FLEX"] = positions_to_fill["FLEX"] - 1
                elif position in ["QB", "RB", "WR", "TE"] and positions_to_fill["SUPERFLEX"] > 0:
                    positions_to_fill["SUPERFLEX"] = positions_to_fill["SUPERFLEX"] - 1
                else:
                    positions_to_fill["BENCH"] = positions_to_fill["BENCH"] - 1

        remaining_positions_to_fill = {pos for pos, count in positions_to_fill.items() if count >= 1}
        position_filter = adjust_available_positions(remaining_positions_to_fill)

        # load all of the available players into a pandas dataframe
        df = pd.read_sql("SELECT * FROM players where availability = 'AVAILABLE'", db.engine)

        # expand the allowed_position json strings into a set
        df["allowed_positions_set"] = df["allowed_positions"].apply(
            lambda x: set(json.loads(x)) if x else set()
        )

        # apply the filtered posiions
        filtered_df = df[df["allowed_positions_set"].apply(lambda s: bool(s & position_filter))]
        filtered_df_sorted = filtered_df.sort_values(by="rank", ascending=True)

        preferred_players = ["Ja'Marr Chase", "Bijan Robinson", "Justin Jefferson", "Saquon Barkley", "Jahmyr Gibbs", "Christian McCaffrey", "CeeDee Lamb", "Malik Nabers", "Puka Nacua", "Ashton Jeanty", "Amon-Ra St. Brown", "De'Von Achane", "Josh Allen", "Lamar Jackson", "Jayden Daniels", "Jalen Hurts", "Joe Burrow", "Garrett Wilson", "Marvin Harrison Jr.", "DK Metcalf", "Xavier Worthy", "Mike Evans", "DJ Moore", "Zay Flowers", "Courtland Sutton", "Calvin Ridley", "DeVonta Smith", "Jaylen Waddle", "Jerry Jeudy", "Jameson Williams", "Rashee Rice", "George Pickens", "Rome Odunze", "Travis Hunter", "Jakobi Meyers", "Matthew Golden", "Emeka Egbuka", "Chris Olave", "Ricky Pearsall", "Michael Pittman Jr.", "Stefon Diggs", "Cooper Kupp", "Jordan Addison", "Jauan Jennings", "Deebo Samuel Sr.", "Khalil Shakir", "Keon Coleman", "Chris Godwin", "Josh Downs", "Brock Bowers"]
        
        # Check if any preferred player is available
        preferred_available = filtered_df_sorted[
            filtered_df_sorted["full_name"].isin(preferred_players)
        ]
        if not preferred_available.empty:
            best_pref = preferred_available.iloc[0]
            print(f"Drafting preferred player: {best_pref['full_name']}")
            return best_pref["id"]
        
        if not filtered_df_sorted.empty:
            best_player = filtered_df_sorted.iloc[0]
            print(best_player["full_name"])
            return best_player["id"]  # No need for conditional on the Series itself
        else:
            return ""  # No eligible player
    finally:
        db.close()

def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    db = DatabaseManager()

    def find_replacements(position, current_week: int, league_year: int, exclude_players: set = None) -> pd.DataFrame:
        """
        Find available replacement players for a given position that are not on bye during the current week.
        Uses season_stats table for better ranking based on current season performance.
        
        Args:
            position: The position to find replacements for
            current_week: The current fantasy week
            league_year: The current league year
            exclude_players: Set of fantasypros_ids to exclude from the search
        
        Returns:
            A DataFrame containing the best available player at that position
        """

        exclude_clause = ""
        if exclude_players:
            excluded_ids = ", ".join(f"'{pid}'" for pid in exclude_players)
            exclude_clause = f"AND ss.fantasypros_id NOT IN ({excluded_ids})"
        
        replacement = pd.read_sql(
            f"""
            SELECT
            ss.fantasypros_id, ss.player_name, ss.position, ss.FPTS, p.availability, p.player_bye_week, ws.FPTS AS 'pFTPS'
            FROM
            season_stats ss
            JOIN players p ON ss.fantasypros_id = p.id
            JOIN weekly_stats ws ON ss.fantasypros_id = ws.fantasypros_id AND ws.week = {current_week - 1}
            WHERE
            p.availability = 'AVAILABLE' AND
            ss.position = '{position}'
            AND ss.year = {league_year}
            AND p.player_bye_week != {current_week}
            AND ws.FPTS > 0
            {exclude_clause}
            ORDER BY
            ss.FPTS desc
            LIMIT
            1
            """,
            db.engine
        )
        return replacement

    try:
        bot_id = get_my_bot_id(db)
        league_year = db.get_league_settings().year
        gs = pd.read_sql("SELECT * FROM game_statuses", db.engine)
        current_week = gs.iloc[0]["current_fantasy_week"]

        # Get my team data and last week's points
        my_team_df = pd.read_sql(
            f"""
            SELECT p.*, ws.FPTS
            FROM players p
            JOIN weekly_stats ws ON p.id = ws.fantasypros_id AND ws.week = {current_week - 1}
            WHERE p.current_bot_id = '{bot_id}'
            """,
            db.engine
        )

        # Identify players on bye
        bye_players_df = my_team_df[my_team_df["player_bye_week"] == current_week]
        if bye_players_df.empty:
            print("No players on bye this week.")
        else:
            print("Players on bye:")
            print(bye_players_df[["full_name", "player_bye_week", "tier"]])

        # Identify players who scored 0 points last week and are not on bye
        zero_score_df = my_team_df[
            (my_team_df["player_bye_week"] != current_week - 1) & 
            (my_team_df["FPTS"] == 0)
        ]
        if zero_score_df.empty:
            print("No players scored 0 points this week (excluding bye).")
        else:
            print("\nPlayers who scored 0 points (not on bye):")
            print(zero_score_df[["full_name", "player_bye_week", "tier", "FPTS"]])

        # Combine into trading_block (distinct players)
        trading_block = pd.concat([bye_players_df, zero_score_df]).drop_duplicates(subset=["id"])
        print(f"\nTrading block ({len(trading_block)} players):")
        print(trading_block[["full_name", "player_bye_week", "tier", "FPTS"]])

        # For each player in the trading block, try to replace with best available player at that position
        claims = []
        claimed_players = set()
        bid = 1
        tier_threshold = 3 # Keep this around 8 so that you don't drop good players
        print("\nTier threshold for replacement:", tier_threshold)
        for _, player in trading_block.iterrows():
            # Get primary position
            position = json.loads(player["allowed_positions"])[0]
            print(f"Finding replacements for {player['full_name']} - {position}")
            
            # Find replacement excluding already claimed players
            replacement = find_replacements(position, current_week, league_year, exclude_players=claimed_players)
            if player["tier"] < tier_threshold and player["FPTS"] > 0:
                print(f"Keep {player['full_name']}")
            else:
                # Add WaiverClaim to claims array, tracking claimed players
                if not replacement.empty:
                    add = replacement.iloc[0]["fantasypros_id"]
                    drop = player["id"]
                    print(f"Claiming {replacement.iloc[0]['player_name']} to replace {player['full_name']}")
                    claims.append(
                        WaiverClaim(
                            player_to_add_id=add,
                            player_to_drop_id=drop,
                            bid_amount=bid
                        )
                    )
                    claimed_players.add(add)
        print(claims)
        actions = AttemptedFantasyActions(
            waiver_claims=claims
        )
        return actions
    
    finally:
        db.close()