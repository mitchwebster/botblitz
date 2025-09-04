from blitz_env import GameState, AddDropSelection
from blitz_env.models import DatabaseManager
import pandas as pd
import json

# --- helpers ---------------------------------------------------------------

def _load_my_team_df(db: DatabaseManager) -> pd.DataFrame:
    """Return rows for players on the current bot's team."""
    gs = pd.read_sql("SELECT * FROM game_statuses", db.engine)
    bot_id = gs.iloc[0]["current_bot_id"]
    q = f"SELECT * FROM players WHERE current_bot_id = '{bot_id}'"
    df = pd.read_sql(q, db.engine)
    # normalize positions into sets for easy membership tests
    df["allowed_positions_set"] = df["allowed_positions"].apply(
        lambda x: set(json.loads(x)) if x else set()
    )
    return df

def _load_undrafted_df(db: DatabaseManager) -> pd.DataFrame:
    """Return rows for available players, with normalized fields and filters applied."""
    df = pd.read_sql("SELECT * FROM players WHERE availability = 'AVAILABLE'", db.engine)
    if df.empty:
        return df
    # Normalize
    df["allowed_positions_set"] = df["allowed_positions"].apply(
        lambda x: set(json.loads(x)) if x else set()
    )
    # Basic exclusions: no CAR players, no Tua (to mirror your 2024 rule), and (optionally) no DEF early.
    df = df[df["professional_team"] != "CAR"]
    df = df[df["full_name"].str.lower() != "tua tagovailoa"]
    return df

def _count_by_position(team_df: pd.DataFrame):
    """Return counts for QB/RB/WR/TE/K and total drafted."""
    pos_counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0, "K": 0, "DST": 0}
    for s in team_df.get("allowed_positions_set", []):
        for p in pos_counts:
            if p in s:
                pos_counts[p] += 1
    total = len(team_df.index)
    return pos_counts, total

def _choose_min_by_rank(df: pd.DataFrame) -> str:
    """Pick best available by rank; return id or empty string."""
    if df.empty:
        return ""
    best = df.sort_values(by="rank", ascending=True).iloc[0]
    return best["id"]

# --- main strategy ---------------------------------------------------------

def draft_player() -> str:

    db = DatabaseManager()
    try:
        my_team = _load_my_team_df(db)
        undrafted = _load_undrafted_df(db)
        
        # Quick exits
        if undrafted.empty:
            return ""

        # Split by position
        def has(pos): return undrafted["allowed_positions_set"].apply(lambda s: pos in s)

        undrafted_qb  = undrafted[has("QB")]
        undrafted_rb  = undrafted[has("RB")]
        undrafted_wr  = undrafted[has("WR")]
        undrafted_te  = undrafted[has("TE")]
        undrafted_k   = undrafted[has("K")]
        undrafted_dst = undrafted[has("DST")]
        undrafted_rbwr = undrafted[undrafted["allowed_positions_set"].apply(lambda s: bool(s & {"RB","WR"}) )]

        # My current counts
        counts, drafted_count = _count_by_position(my_team)
        rb_count = counts["RB"]; wr_count = counts["WR"]; te_count = counts["TE"]
        qb_count = counts["QB"]; k_count = counts["K"];  dst_count = counts["DST"]

        rbwr_diff = abs(rb_count - wr_count)

        # Mirror your "round" heuristics using drafted_count (players already rostered):
        # K at 13, DST at 14 (same as your 2024 code)
        if drafted_count == 13 and not undrafted_k.empty:
            return _choose_min_by_rank(undrafted_k)
        if drafted_count == 14 and not undrafted_dst.empty:
            return _choose_min_by_rank(undrafted_dst)

        # If RB/WR unbalanced, pick the lagging one
        if rbwr_diff >= 2:
            if rb_count > wr_count and not undrafted_wr.empty:
                return _choose_min_by_rank(undrafted_wr)
            if wr_count > rb_count and not undrafted_rb.empty:
                return _choose_min_by_rank(undrafted_rb)
            # fallback if one is empty
            if not undrafted_rbwr.empty:
                return _choose_min_by_rank(undrafted_rbwr)

        # Balanced path (rbwr_diff < 2) — mirror your 2024 branching
        # First two picks: best RB/WR by rank
        if drafted_count < 2 and not undrafted_rbwr.empty:
            return _choose_min_by_rank(undrafted_rbwr)

        # Draft TE once we’ve started on RB/WR (roughly rounds 3-4)
        if te_count == 0 and not undrafted_te.empty:
            return _choose_min_by_rank(undrafted_te)

        # Draft a QB after TE—only one QB total.
        # You had: qb when drafted_count == 6 or 12 (after TE). Keep those gates but also allow if late and we still have none.
        if qb_count == 0:
            if drafted_count in (6, 12) and not undrafted_qb.empty:
                return _choose_min_by_rank(undrafted_qb)
            # late safety: if bench filling and still no QB, allow picking one
            if drafted_count >= 10 and not undrafted_qb.empty:
                return _choose_min_by_rank(undrafted_qb)

        # Prioritize RBs once all core positions touched (your 2024 rule: rb_count>1,<4 etc.)
        if (rb_count > 1 and rb_count < 4 and wr_count > 1 and te_count > 0 and qb_count > 0
            and drafted_count not in (12, 13, 14) and not undrafted_rb.empty):
            return _choose_min_by_rank(undrafted_rb)

        # Otherwise best available RB/WR
        if not undrafted_rbwr.empty:
            return _choose_min_by_rank(undrafted_rbwr)

        # Last fallbacks
        for pool in (undrafted_te, undrafted_qb, undrafted_k, undrafted_dst, undrafted):
            pick = _choose_min_by_rank(pool)
            if pick:
                return pick

        return ""
    finally:

        db.close()

def propose_add_drop(game_state: GameState) -> AddDropSelection:
    """
    Selects a player to draft based on the highest rank.

    Args:
        players (List[Player]): A list of Player objects.

    Returns:
        str: The id of the drafted player.
    """

    return AddDropSelection(
        player_to_add_id="", # do not add
        player_to_drop_id="" # do not drop
    )