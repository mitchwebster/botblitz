from blitz_env import AttemptedFantasyActions, WaiverClaim
from blitz_env.models import DatabaseManager
import pandas as pd
import json, os, math

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

# Anti-hoard: extra *bench* depth tolerated per position on top of that
# position's startable slots before the anti-hoard guardrail stops drafting more
# of it. K/DST get 0 (never draft a second kicker/defense); skill positions get
# a few bench bodies for bye/injury cover, QB one backup.
ANTI_HOARD_BENCH_DEPTH = {"QB": 1, "RB": 3, "WR": 3, "TE": 1, "K": 0, "DST": 0}


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


# ---------------------------------------------------------------------------
# Draft pick selection (issue #264)
#
# choose_draft_pick is PURE: it consumes the scored pool from score_draft_pool,
# my current roster, the draft round and the league slots, and returns the id
# of the player to draft. It layers roster need + guardrails on top of the raw
# VORP value so the engine wiring (draft_player) only has to read the DB and
# call these functions. All round/slot-derived thresholds come from the slots
# map (no hardcoded magic round numbers).
# ---------------------------------------------------------------------------


def _roster_positions(my_roster):
    """Normalize my_roster (dict {name: position} or an iterable of positions)
    into an upper-cased list of positions."""
    if isinstance(my_roster, dict):
        positions = my_roster.values()
    elif my_roster is None:
        positions = []
    else:
        positions = my_roster
    return [str(p).upper() for p in positions if p is not None]


def _roster_position_counts(my_roster):
    """Count how many rostered players I have at each position."""
    counts = {}
    for p in _roster_positions(my_roster):
        counts[p] = counts.get(p, 0) + 1
    return counts


def remaining_roster_needs(my_roster, slots):
    """Set of positions still rosterable given what I've drafted vs the slots.

    Mirrors the engine's slot accounting: each rostered player fills its own
    explicit slot first, then spills into FLEX, then SUPERFLEX, then BENCH. The
    remaining (count >= 1) slots are expanded through ``adjust_available_positions``
    so FLEX/SUPERFLEX/BENCH openings re-admit their eligible positions.
    """
    counts = {k: int(v or 0) for k, v in slots.items()}
    for position in _roster_positions(my_roster):
        if counts.get(position, 0) > 0:
            counts[position] -= 1
        elif position in ("RB", "WR", "TE") and counts.get("FLEX", 0) > 0:
            counts["FLEX"] -= 1
        elif position in ("QB", "RB", "WR", "TE") and counts.get("SUPERFLEX", 0) > 0:
            counts["SUPERFLEX"] -= 1
        elif counts.get("BENCH", 0) > 0:
            counts["BENCH"] -= 1
    remaining = {pos for pos, count in counts.items() if count >= 1}
    return adjust_available_positions(remaining)


def _startable_slots(slots):
    """Startable slot count per position implied by the league slots. FLEX and
    SUPERFLEX count toward every position they can start (an upper bound used
    only to size the anti-hoard cap)."""
    def n(p):
        return int(slots.get(p, 0) or 0)

    flex = n("FLEX") + n("SUPERFLEX")
    return {
        "QB": n("QB") + n("SUPERFLEX"),
        "RB": n("RB") + flex,
        "WR": n("WR") + flex,
        "TE": n("TE") + flex,
        "K": n("K"),
        "DST": n("DST"),
    }


def _anti_hoard_caps(slots):
    """Max number of each position to draft before the anti-hoard guardrail
    blocks more (startable slots + tolerated bench depth)."""
    startable = _startable_slots(slots)
    return {p: startable[p] + ANTI_HOARD_BENCH_DEPTH.get(p, 0) for p in startable}


def _best_id(candidates):
    """Best id from an already value-sorted candidate frame. If NO candidate
    has a projection, fall back to players.rank order (lower rank == better)."""
    if candidates.empty:
        return ""
    proj = pd.to_numeric(candidates.get("projected_fpts"), errors="coerce")
    if proj is not None and proj.notna().any():
        return candidates.iloc[0]["id"]  # already sorted best-first by value_score
    rank = pd.to_numeric(candidates.get("rank"), errors="coerce")
    if rank is None or rank.notna().sum() == 0:
        return candidates.iloc[0]["id"]
    return candidates.iloc[int(rank.fillna(float("inf")).values.argmin())]["id"]


def choose_draft_pick(scored_df, my_roster, round, slots):
    """Pick a player id from the scored pool, applying (in priority order):

      1. Roster-need filter — only positions still rosterable given my roster.
      2. QB-deadline guardrail — secure two startable QBs (QB + SUPERFLEX) by a
         deadline round derived from the slots; force a QB when under-stocked.
      3. K/DST reserve — in the final rounds reserve picks for any unfilled
         required K/DST slot.
      4. Anti-hoard cap — drop positions I've already stocked to capacity.

    Then take the highest ``value_score`` candidate, falling back to
    ``players.rank`` order when no remaining candidate has a projection.

    PURE: ``scored_df`` is the (already best-first) output of
    ``score_draft_pool``; ``my_roster`` is the {name: position} map (or position
    iterable); ``round`` is the current draft round; ``slots`` is the league
    player_slots map. No DB / engine access.
    """
    if scored_df is None or len(scored_df) == 0:
        return ""

    df = scored_df.copy().reset_index(drop=True)
    pos = df["position"].astype(str).str.upper()

    # 1. Roster-need filter.
    needs = remaining_roster_needs(my_roster, slots)
    candidates = df[pos.isin(needs)] if needs else df
    if candidates.empty:
        candidates = df  # never return empty-handed while the pool has players

    roster_counts = _roster_position_counts(my_roster)
    total_rounds = sum(int(v or 0) for v in slots.values())
    kdst_slots = int(slots.get("K", 0) or 0) + int(slots.get("DST", 0) or 0)
    rounds_remaining = max(1, total_rounds - int(round) + 1)

    def cand_pos(frame):
        return frame["position"].astype(str).str.upper()

    # 2. QB-deadline guardrail: secure two startable QBs (QB + SUPERFLEX) before
    # the K/DST reserve rounds; if still short by then, force the best QB.
    qb_target = int(slots.get("QB", 0) or 0) + int(slots.get("SUPERFLEX", 0) or 0)
    qb_deadline_round = total_rounds - kdst_slots
    if roster_counts.get("QB", 0) < qb_target and round >= qb_deadline_round:
        qb_cand = candidates[cand_pos(candidates) == "QB"]
        if not qb_cand.empty:
            return _best_id(qb_cand)

    # 3. K/DST reserve: in the final rounds, force any unfilled required K/DST.
    needed_kdst = [
        p for p in ("K", "DST")
        if roster_counts.get(p, 0) < int(slots.get(p, 0) or 0)
    ]
    if needed_kdst and rounds_remaining <= len(needed_kdst):
        kdst_cand = candidates[cand_pos(candidates).isin(needed_kdst)]
        if not kdst_cand.empty:
            return _best_id(kdst_cand)

    # 4. Anti-hoard cap: drop positions already stocked to capacity.
    caps = _anti_hoard_caps(slots)
    over = cand_pos(candidates).map(
        lambda p: roster_counts.get(p, 0) >= caps.get(p, 10 ** 9)
    )
    capped = candidates[~over.values]
    if not capped.empty:
        candidates = capped

    return _best_id(candidates)


def draft_player() -> str:
    """Draft the best available player for my team.

    Orchestrates the full draft path: read league slots / team count / my roster
    / the available pool (with projections + last-season actuals) from the DB,
    compute VORP replacement baselines, score the pool, then apply roster-need
    and guardrails via choose_draft_pick. Returns the chosen available player id.
    """
    db = DatabaseManager()
    try:
        slots = get_positions_to_fill(db)
        num_teams = get_num_teams(db)
        my_roster = get_my_team(db)

        available_df = load_draft_valuation_inputs(db)
        baselines = compute_replacement_baselines(slots, num_teams)
        scored = score_draft_pool(available_df, baselines, my_roster)

        # Current round from the engine: ceil(current_draft_pick / num_teams).
        status = pd.read_sql("SELECT current_draft_pick FROM game_statuses", db.engine)
        current_pick = int(status.iloc[0]["current_draft_pick"])
        round_num = math.ceil(current_pick / num_teams) if num_teams else 1

        chosen = choose_draft_pick(scored, my_roster, round_num, slots)
        if chosen:
            print(f"Drafting player id {chosen}")
            return str(chosen)

        # Last-ditch fallback: best available by rank (should rarely trigger).
        fallback = load_available_players(db).sort_values(by="rank", ascending=True)
        return str(fallback.iloc[0]["id"]) if not fallback.empty else ""
    finally:
        db.close()

# ---------------------------------------------------------------------------
# Weekly waiver valuation + FAAB bid sizing (issue #265)
#
# These are PURE functions: player_forward_value takes already-extracted
# sequences (recent weekly actuals + upcoming weekly projections) plus an
# injury status string; size_bid takes plain numbers. Neither reads the DB or
# engine, so both are unit-tested fully offline. The weekly DB wiring that
# feeds them (pulling trailing_actuals / projections / game_status out of
# weekly_stats / weekly_projections / weekly_injuries) lands in #266.
# ---------------------------------------------------------------------------

# Injury game-status gates, keyed off weekly_injuries.game_status (NFL.com values
# like 'Out', 'Doubtful', 'Questionable'). The multiplier scales a player's
# forward value DOWN toward zero when they're unlikely to play: OUT collapses it
# to 0 (won't play), DOUBTFUL / QUESTIONABLE get partial discounts, everything
# else (ACTIVE / PROBABLE / None / unknown) keeps full value. Matched
# case-insensitively so 'OUT' and 'Out' gate identically.
INJURY_PLAY_MULTIPLIER = {
    "OUT": 0.0,
    "DOUBTFUL": 0.25,
    "QUESTIONABLE": 0.75,
    "PROBABLE": 1.0,
    "ACTIVE": 1.0,
}

# Blend weight on trailing actuals vs. upcoming projections in forward value.
# 0.5 = equal trust in recent production and what the projections expect next.
FORWARD_TRAILING_WEIGHT = 0.5

# Default FAAB aggressiveness when the BID_AMOUNT env var is unset. 1.0 is
# neutral; >1 bids harder (spends a larger share of budget per upgrade), <1 is
# more conservative. The engine injects BID_AMOUNT per-bot from bots/nfl/envs.
DEFAULT_BID_AMOUNT = 1.0

# Regular-season FAAB weeks, used to derive how much of the budget is unlocked
# each week (the late-season reserve ramp).
FAAB_SEASON_WEEKS = 14

# Floor on the per-week spendable fraction so even very early / low-budget weeks
# can still place a small bid instead of being capped to 1.
MIN_SPEND_FRACTION = 0.10

# Tie-break margin. League ties break toward the worse-ranked team, so to win a
# claim as the better-ranked team we must strictly OUTBID an equal-value
# opponent — round up and add this margin (still subject to the budget clamp).
TIE_BREAK_MARGIN = 1


def injury_play_multiplier(injury_status):
    """Map an injury / game-status string to a [0, 1] play-likelihood multiplier.

    Case-insensitive; None / unknown statuses keep full value (1.0). 'OUT'
    collapses to 0.0 (player won't play)."""
    if injury_status is None:
        return 1.0
    return INJURY_PLAY_MULTIPLIER.get(str(injury_status).strip().upper(), 1.0)


def player_forward_value(trailing_actuals, projections, injury_status=None):
    """Forward-looking weekly value: a blend of average trailing-N-week actual
    FPTS and average next-N-week projected FPTS, gated DOWN by injury status.

    PURE: ``trailing_actuals`` is an iterable of recent weekly actual FPTS,
    ``projections`` an iterable of upcoming weekly projected FPTS (None / NaN
    entries are ignored, empty -> 0.0), ``injury_status`` a game-status string
    (e.g. 'OUT'/'Questionable') or None.

    Monotonic in both inputs: raising any trailing actual or any projection
    weakly increases the value (positive blend weights). Injury gating only
    ever scales the value down (OUT -> 0)."""
    def _avg(seq):
        vals = [
            float(x) for x in (seq or [])
            if x is not None and not (isinstance(x, float) and math.isnan(x))
        ]
        return sum(vals) / len(vals) if vals else 0.0

    trailing = _avg(trailing_actuals)
    upcoming = _avg(projections)
    base = (
        FORWARD_TRAILING_WEIGHT * trailing
        + (1.0 - FORWARD_TRAILING_WEIGHT) * upcoming
    )
    return base * injury_play_multiplier(injury_status)


def _bid_aggressiveness():
    """Read the BID_AMOUNT env var as a non-negative aggressiveness scalar,
    falling back to DEFAULT_BID_AMOUNT when unset / unparseable."""
    try:
        return max(0.0, float(os.environ.get("BID_AMOUNT", DEFAULT_BID_AMOUNT)))
    except (TypeError, ValueError):
        return DEFAULT_BID_AMOUNT


def size_bid(upgrade, upgrade_max, remaining_budget, week):
    """Size a FAAB bid (int) for a waiver upgrade.

    The bid is proportional to this upgrade's share of the best upgrade
    available (``upgrade / upgrade_max``), scaled by the remaining budget and
    the BID_AMOUNT aggressiveness scalar, then clamped:

      * never below 1 (when there's any positive upgrade and budget to spend),
      * never above ``remaining_budget``,
      * never above a budget-reserve cap that SHRINKS when the budget is low
        (the cap is a fraction OF the budget) or the season is early (the
        fraction ramps from MIN_SPEND_FRACTION up to 1.0 by FAAB_SEASON_WEEKS),
        preserving a late-season reserve.

    A strictly-positive upgrade rounds UP and adds TIE_BREAK_MARGIN so we
    clearly outbid an equal-value opponent (ties break toward the worse-ranked
    team); the budget / reserve clamps always win over that margin.

    PURE: plain numbers in, int out. No DB / engine access."""
    budget = int(remaining_budget or 0)
    if budget <= 0:
        return 0

    up = max(0.0, float(upgrade or 0.0))
    up_max = float(upgrade_max or 0.0)
    if up <= 0.0:
        return 1  # minimal speculative bid; never drop below 1 with budget left

    share = up / up_max if up_max > 0 else 1.0
    share = min(1.0, max(0.0, share))

    # Late-season reserve ramp: unlock more of the budget as the season goes on.
    spend_fraction = max(
        MIN_SPEND_FRACTION,
        min(1.0, float(week or 0) / FAAB_SEASON_WEEKS),
    )
    # Reserve cap shrinks with low budget (fraction OF budget) and early weeks
    # (smaller fraction). Bounded by the remaining budget.
    cap = min(budget, max(1, int(math.ceil(budget * spend_fraction))))

    raw = share * budget * _bid_aggressiveness()
    # Round up + margin so a better-ranked team clearly outbids on a tie.
    bid = int(math.ceil(raw)) + TIE_BREAK_MARGIN
    # Clamp: at least 1, never over the reserve cap or the remaining budget.
    return max(1, min(bid, cap, budget))


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