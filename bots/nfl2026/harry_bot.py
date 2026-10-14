"""
harry_bot — 2026 draft bot.

Overview
--------
Draft (`draft_player`): a rule-based, position-balancing strategy. Every pick
is "best available by `rank`" (lower = better) within whichever position pool
the rules below select. Rules are checked in order; the first one that fires
wins:

  1. Late specialists: take a K in the second-to-last round and a DST in the
     last round (based on league_settings.total_rounds).
  2. Opening picks: the first two picks are the best RB/WR.
  3. QB1 (SUPERFLEX league, so two QBs): once the roster has both an RB and
     a WR — the 3rd pick, or the 4th if the first two were the same position.
  4. QB2: at the 7th pick, or at the latest in the round before the K
     (total_rounds - 3 players rostered), so it always lands before K/DST.
  5. RB/WR balance: if RB and WR counts differ by 2+, take the lagging position.
  6. TE: one TE, in round 8 or later (the league has no TE slot — a TE only
     scores from FLEX/SUPERFLEX, so he's not worth an early pick).
  7. RB depth: once the core is filled (2+ RB, 2+ WR and a QB), keep
     adding RBs until there are 4.
  8. Otherwise: best available RB/WR.
  9. Fallbacks: TE, QB, K, DST, then anyone left.

Pool filters applied to every pick: no Carolina (CAR) players and no Tua
Tagovailoa (carried over from the 2024 bot), plus an injury filter based on
the latest injury report(s) in weekly_injuries:
  - Out in both of the two latest reports (treated as IR, since IR players
    aren't on the injury report): never drafted.
  - Out in the latest report only: drafted only if an RB/WR ranked top 10 at
    his position (position_rank <= 10); everyone else is skipped.
And a "has scored this season" filter: only players with points in
weekly_stats for the league year (kickers: a made FG/PAT, since their FPTS is
always 0 there). Skipped when the league year has no stats yet.

Weekly (`perform_weekly_fantasy_actions`): position-for-position waivers using
only completed games before the fantasy week. Players are ranked within their
position by a blended score: 40% points per game over their last 5 games, 60%
their next-game projection (next week's if on bye, so byes don't sink them);
with only one signal, that one. Week numbers stored as TEXT are normalized.
  1. Injury replacement: replace a player after 3 consecutive team games missed
     since his first unresolved Out report, provided he has not played or received
     a newer healthy report. Targets must be healthy and playing this week.
  2. Bye cover: account for the league's starting slots, including a second QB
     in SUPERFLEX. Keep top QB12/RB24/WR24 players; replace lesser bye players
     only up to the coverage deficit. K/DST are always swapped for their bye.
  3. Upgrade: if a free agent ranks better than our worst player at that
     position and isn't listed Out, drop ours and add him. Our starters (top 2
     QB, RB and WR) are protected from small ranking changes: they're only
     upgraded on by a free agent ranked 8+ spots better.
Adds must belong to an NFL team; CAR and Tua remain excluded. The schedule is
preferred, with preseason player bye weeks as a fallback when it is absent.
FAAB bids follow the position tiers (30%/15%/8%/4% of remaining funds by
position rank; TE x0.75). K/DST bid $1 for bye/injury cover (or $0 once the
budget's gone) and $0 for plain upgrades. Each drop receives up to 3
fallbacks; the engine rejects any claim we can no longer afford after an
earlier win, so simultaneous wins can't overspend.
"""

from blitz_env import GameState, AttemptedFantasyActions, WaiverClaim
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
    # Basic exclusions: no CAR players, no Tua (to mirror your 2024 rule).
    df = df[df["professional_team"] != "CAR"]
    df = df[df["full_name"].str.lower() != "tua tagovailoa"]
    df = _apply_injury_filter(db, df)
    df = _apply_scored_filter(db, df)
    return df

def _apply_scored_filter(db: DatabaseManager, df: pd.DataFrame) -> pd.DataFrame:
    """Keep only players who have scored this season (league year): FPTS > 0 in
    any week, or — for kickers, whose FPTS is always 0 in weekly_stats — a made
    FG or PAT. Skipped if the league year has no stats yet (e.g. a preseason
    draft), since it would otherwise empty the pool."""
    year = int(db.get_league_settings().year)
    try:
        scored = pd.read_sql(
            "SELECT DISTINCT fantasypros_id FROM weekly_stats "
            f"WHERE year = {year} AND fantasypros_id IS NOT NULL "
            "AND (FPTS > 0 OR fg_made > 0 OR pat_made > 0)",
            db.engine,
        )
    except Exception:
        return df  # no stats table — don't filter anyone
    if scored.empty:
        return df
    return df[df["id"].astype(str).isin(set(scored["fantasypros_id"]))]

def _load_out_ids(db: DatabaseManager):
    """Return (out_latest, out_multi): player ids listed 'Out' in the latest
    injury report, and those listed 'Out' in both of the two latest reports.

    "Latest" = the most recent (year, week) in weekly_injuries up to the league
    year — at a preseason draft that's last season's final week."""
    year = db.get_league_settings().year
    try:
        inj = pd.read_sql(
            "SELECT year, week, fantasypros_id, game_status FROM weekly_injuries "
            f"WHERE year <= {int(year)} AND fantasypros_id IS NOT NULL",
            db.engine,
        )
    except Exception:
        return set(), set()  # no injury table — don't filter anyone
    if inj.empty:
        return set(), set()

    reports = (inj[["year", "week"]].drop_duplicates()
               .sort_values(["year", "week"], ascending=False)
               .head(2).itertuples(index=False))
    out_by_report = [
        set(inj[(inj["year"] == y) & (inj["week"] == w) & (inj["game_status"] == "Out")]["fantasypros_id"])
        for y, w in reports
    ]
    out_latest = out_by_report[0]
    out_multi = out_latest & out_by_report[1] if len(out_by_report) > 1 else set()
    return out_latest, out_multi

def _apply_injury_filter(db: DatabaseManager, df: pd.DataFrame) -> pd.DataFrame:
    """Drop injured players from the draft pool:
      - Out in both of the last two reports (our stand-in for IR, which the
        injury report doesn't list): never drafted.
      - Out in the latest report only: drafted only if an RB/WR with a
        position rank of 10 or better."""
    out_latest, out_multi = _load_out_ids(db)
    if not out_latest:
        return df
    is_star_rbwr = (
        df["allowed_positions_set"].apply(lambda s: bool(s & {"RB", "WR"}))
        & (df["position_rank"] <= 10)
    )
    ids = df["id"].astype(str)
    drop = ids.isin(out_multi) | (ids.isin(out_latest) & ~is_star_rbwr)
    return df[~drop]

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
    """Return the id of the player to draft (see the module docstring for the rule order)."""
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

        # K in the second-to-last round, DST in the last round. drafted_count is
        # players already rostered, so the final round is drafted_count == total_rounds - 1.
        total_rounds = db.get_league_settings().total_rounds
        if drafted_count == total_rounds - 2 and k_count == 0 and not undrafted_k.empty:
            return _choose_min_by_rank(undrafted_k)
        if drafted_count == total_rounds - 1 and dst_count == 0 and not undrafted_dst.empty:
            return _choose_min_by_rank(undrafted_dst)

        # First two picks: best RB/WR by rank
        if drafted_count < 2 and not undrafted_rbwr.empty:
            return _choose_min_by_rank(undrafted_rbwr)

        # SUPERFLEX league, so two QBs. QB1 once we have both an RB and a WR
        # (the 3rd pick, or the 4th if the first two were the same position —
        # the balance rule below then takes the missing one first).
        if qb_count == 0 and rb_count >= 1 and wr_count >= 1 and not undrafted_qb.empty:
            return _choose_min_by_rank(undrafted_qb)

        # QB2 at the 7th pick (where the 2024 bot took its QB), or at the latest
        # in the round before the K, so it always lands before K/DST.
        if (qb_count == 1 and drafted_count in (6, total_rounds - 3)
                and not undrafted_qb.empty):
            return _choose_min_by_rank(undrafted_qb)

        # If RB/WR unbalanced, pick the lagging one
        if rbwr_diff >= 2:
            if rb_count > wr_count and not undrafted_wr.empty:
                return _choose_min_by_rank(undrafted_wr)
            if wr_count > rb_count and not undrafted_rb.empty:
                return _choose_min_by_rank(undrafted_rb)
            # fallback if one is empty
            if not undrafted_rbwr.empty:
                return _choose_min_by_rank(undrafted_rbwr)

        # TE in round 8 or later. There's no TE slot — a TE only scores from
        # FLEX/SUPERFLEX, competing with RB/WR — so he's not worth an early pick.
        if te_count == 0 and drafted_count >= 7 and not undrafted_te.empty:
            return _choose_min_by_rank(undrafted_te)

        # Prioritize RBs once all core positions touched (your 2024 rule: rb_count>1,<4 etc.)
        if (rb_count > 1 and rb_count < 4 and wr_count > 1 and qb_count > 0
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

# --- weekly add/drop ---------------------------------------------------------

MAX_CLAIMS = 10           # the engine only considers a bot's first 10 claims
OPTIONS_PER_DROP = 3      # fallback targets per drop, in case we're outbid
INJURY_TAGS = {"Out", "Doubtful", "Questionable"}
RECENT_GAMES = 5          # games averaged for recent form (more = steadier ranks)
RECENT_WEIGHT = 0.4       # score = 40% recent form + 60% next-game projection

# Upgrades never churn our starters over small ranking changes: our top
# QB x2 (QB + SUPERFLEX), RB x2 and WR x2 can only be upgraded on by a free
# agent ranked at least PROTECT_MIN_RANK_GAIN spots better.
PROTECTED_STARTERS = {"QB": 2, "RB": 2, "WR": 2}
PROTECT_MIN_RANK_GAIN = 8
LONG_TERM_OUT_GAMES = 3   # consecutive team games missed since an unresolved Out report

# Bid = % of remaining budget by the add's position rank (better rank = bigger
# bid), scaled down for TE, which has no slot of its own. K/DST bid a flat
# FLAT_BID for bye/injury cover (or $0 once the budget's gone), and $0 for
# plain upgrades.
BID_TIERS = [(3, 0.30), (8, 0.15), (16, 0.08), (24, 0.04)]  # (max position_rank, pct)
BID_POSITION_WEIGHT = {"QB": 1.0, "RB": 1.0, "WR": 1.0, "TE": 0.75}
FLAT_BID_POSITIONS = {"K", "DST"}
FLAT_BID = 1

# Never roster more than one of these: a claim may only add one if it drops
# one. (Every rule already swaps like for like; this guard keeps it that way.)
ONE_PER_ROSTER = {"K", "DST"}

# Byes: starters at these positions are needed in the lineup every week
# (QB x2 including SUPERFLEX, RB x2, WR x2, K, DST — TE has no slot of its own). A player on bye is
# only swapped out if the roster can't cover his slot that week AND he's
# ranked worse than this; anyone ranked this well or better is kept and we
# take the zero for that week. K and DST are 0: always swapped for their bye.
STARTERS_NEEDED = {"QB": 2, "RB": 2, "WR": 2, "K": 1, "DST": 1}
BYE_KEEP_RANK = {"QB": 12, "RB": 24, "WR": 24, "TE": 12, "K": 0, "DST": 0}

# Standard kicker scoring, since kicker FPTS is 0 in weekly_stats (nflreadr
# doesn't score kicking). Only used when FPTS is lower.
_KICKER_POINTS = {
    "fg_made_0_19": 3, "fg_made_20_29": 3, "fg_made_30_39": 3,
    "fg_made_40_49": 4, "fg_made_50_59": 5, "fg_made_60_": 5,
    "pat_made": 1, "fg_missed": -1, "pat_missed": -1,
}

# nflreadr abbreviations -> the pool's (same mapping as load_stats_nflreadr).
_TEAM_ABBR_TO_POOL = {"JAX": "JAC", "LA": "LAR"}


def _bid_for(position: str, position_rank: int, budget: int, reason: str = "upgrade") -> int:
    if position in FLAT_BID_POSITIONS:
        return 0 if reason == "upgrade" else min(FLAT_BID, budget)
    pct = next((p for max_rank, p in BID_TIERS if position_rank <= max_rank), 0.0)
    return min(budget, int(round(budget * pct * BID_POSITION_WEIGHT.get(position, 1.0))))


def _weekly_stats(db: DatabaseManager, year: int, week: int) -> pd.DataFrame:
    """One row per (player, week) this season before the current week, with
    `pts` = FPTS (or the kicker formula, if higher)."""
    ws = pd.read_sql(f"SELECT * FROM weekly_stats WHERE year = {year} AND CAST(week AS INTEGER) < {week} "
                     "AND fantasypros_id IS NOT NULL", db.engine)
    if ws.empty:
        return pd.DataFrame(columns=["fantasypros_id", "week", "team", "pts"])
    ws["week"] = pd.to_numeric(ws["week"], errors="coerce")
    kick = sum(pd.to_numeric(ws[c], errors="coerce").fillna(0) * v
               for c, v in _KICKER_POINTS.items() if c in ws.columns)
    ws["pts"] = pd.concat([pd.to_numeric(ws["FPTS"], errors="coerce").fillna(0), pd.Series(kick, index=ws.index)], axis=1).max(axis=1)
    return (ws.sort_values("pts").drop_duplicates(["fantasypros_id", "week"], keep="last")
              [["fantasypros_id", "week", "team", "pts"]])


def _injury_reports(db: DatabaseManager, year: int, week: int) -> pd.DataFrame:
    """Injury report rows this season before the current fantasy week."""
    inj = pd.read_sql(
        "SELECT week, fantasypros_id, team, game_status FROM weekly_injuries "
        f"WHERE year = {year} AND CAST(week AS INTEGER) < {week} AND fantasypros_id IS NOT NULL",
        db.engine,
    )
    inj["week"] = pd.to_numeric(inj["week"], errors="coerce")
    return inj


def _team_game_weeks(db: DatabaseManager, year: int) -> dict:
    """Read schedule when present; otherwise use the pool's preseason bye weeks.

    The fallback deliberately avoids future actual stats: a historical replay
    must not infer a player's availability from games he has not played yet.
    """
    from sqlalchemy import inspect
    if inspect(db.engine).has_table("schedule"):
        sched = pd.read_sql(f"SELECT team, week FROM schedule WHERE year = {year}", db.engine)
        if not sched.empty:
            sched["week"] = pd.to_numeric(sched["week"], errors="coerce")
            sched["team"] = sched["team"].replace(_TEAM_ABBR_TO_POOL)
            return sched.dropna(subset=["week"]).groupby("team")["week"].apply(set).to_dict()
    pool = pd.read_sql("SELECT professional_team, player_bye_week FROM players", db.engine)
    pool["professional_team"] = pool["professional_team"].replace(_TEAM_ABBR_TO_POOL)
    teams = set(pool["professional_team"].dropna()) - {"", "FA"}
    byes = pd.to_numeric(pool["player_bye_week"], errors="coerce")
    result = {}
    for team in teams:
        bye = byes[pool["professional_team"] == team].dropna()
        # Several teammates establish the shared bye; ignore invalid markers.
        bye = bye[bye.between(1, 18)]
        result[team] = set(range(1, 19)) - ({int(bye.mode().iloc[0])} if not bye.empty else set())
    return result


def _player_scores(db: DatabaseManager, players: pd.DataFrame, stats: pd.DataFrame,
                   year: int, week: int, plays_this_week: pd.Series) -> pd.Series:
    """In-season value per player: a weighted blend of (a) points per game
    over his last RECENT_GAMES games (RECENT_WEIGHT) and (b) his projection
    for his next game (the rest) — this week, or next week if he's on bye, so
    a bye doesn't sink him. With only one signal, that signal alone."""
    recent = (stats.sort_values("week").groupby("fantasypros_id").tail(RECENT_GAMES)
                   .groupby("fantasypros_id")["pts"].mean())
    proj = pd.read_sql(
        "SELECT fantasypros_id, week, AVG(points) AS pts FROM external_projections "
        f"WHERE year = {year} AND CAST(week AS INTEGER) IN ({week}, {week + 1}) GROUP BY 1, 2", db.engine)
    proj["week"] = pd.to_numeric(proj["week"], errors="coerce")
    proj_now = proj[proj["week"] == week].set_index("fantasypros_id")["pts"]
    proj_next = proj[proj["week"] == week + 1].set_index("fantasypros_id")["pts"]
    next_game = players["id"].map(proj_now).where(plays_this_week, players["id"].map(proj_next))
    recent_pts = players["id"].map(recent)
    blended = RECENT_WEIGHT * recent_pts + (1 - RECENT_WEIGHT) * next_game
    # NaN only if we have neither signal
    return blended.fillna(recent_pts).fillna(next_game)


def _long_term_out(stats: pd.DataFrame, injuries: pd.DataFrame, team_now: dict, game_weeks: dict, week: int) -> set:
    """Count team games since an unresolved Out report, excluding bye weeks.

    Missing reports may mean IR; a newer non-Out report or actual game resets
    the injury window. Games missed before the first Out tag do not count.
    """
    last_game = stats.groupby("fantasypros_id")["week"].max()
    result = set()
    for pid, reports in injuries.sort_values("week").groupby("fantasypros_id"):
        # An old Out tag must not survive a later healthy report or game.
        if reports.iloc[-1]["game_status"] != "Out":
            continue
        played = last_game.get(pid, 0)
        unresolved = reports[reports["week"] > played]
        recovered = unresolved[unresolved["game_status"] != "Out"]["week"]
        if not recovered.empty:
            unresolved = unresolved[unresolved["week"] > recovered.max()]
        out = unresolved[unresolved["game_status"] == "Out"]
        if out.empty:
            continue
        first_out = out["week"].min()
        missed = sum(1 for w in game_weeks.get(team_now.get(pid), set())
                     if first_out <= w < week)
        if missed >= LONG_TERM_OUT_GAMES:
            result.add(pid)
    return result


def _plan_claims(roster: pd.DataFrame, free_agents: pd.DataFrame, latest: dict,
                 long_out: set, starters_needed: dict = None) -> list:
    """Prioritize injury and bye coverage, then upgrades: any free agent with
    a better position rank than our worst at that position (and not listed
    Out) replaces him.

    Fallbacks share a drop. A later drop may still target a shared fallback,
    because the engine cancels claims after that add or drop has already won.
    """
    healthy = free_agents[
        ~free_agents["id"].map(latest).isin(INJURY_TAGS)
        & free_agents["plays"] & free_agents["score"].notna()
    ]
    plan, used_drops = [], set()

    def add_options(drop, pool, reason):
        options = pool[pool["pos"] == drop["pos"]].sort_values("pos_rank").head(OPTIONS_PER_DROP)
        if options.empty:
            return False
        plan.extend((drop, add, reason) for _, add in options.iterrows())
        used_drops.add(drop["id"])
        return True

    for _, drop in roster[roster["id"].isin(long_out)].iterrows():
        add_options(drop, healthy, "injury")

    available = roster[~roster["id"].isin(long_out)]
    for pos, needed in (starters_needed or STARTERS_NEEDED).items():
        at_pos = available[available["pos"] == pos]
        active = at_pos["plays"] & ~at_pos["id"].map(latest).isin({"Out", "Doubtful"})
        deficit = max(0, needed - int(active.sum()))
        # Count planned injury replacements toward coverage; don't over-swap byes.
        injury_drops = set(drop["id"] for drop, _, _ in plan if drop["pos"] == pos)
        deficit = max(0, deficit - len(injury_drops))
        for _, drop in at_pos[~at_pos["plays"]].sort_values("pos_rank", ascending=False).iterrows():
            if deficit == 0:
                break
            if drop["pos_rank"] > BYE_KEEP_RANK.get(pos, 0) and add_options(drop, healthy, "bye"):
                deficit -= 1

    not_out = free_agents[free_agents["id"].map(latest) != "Out"]
    for pos, mine in roster[~roster["id"].isin(used_drops | long_out)].groupby("pos"):
        candidates = not_out[not_out["pos"] == pos].sort_values("pos_rank")
        starters = set(roster[roster["pos"] == pos].sort_values("pos_rank")
                       .head(PROTECTED_STARTERS.get(pos, 0))["id"])
        taken = 0
        # Worst-ranked first; each later drop competes for the next-best targets.
        for _, drop in mine.sort_values("pos_rank", ascending=False).iterrows():
            margin = PROTECT_MIN_RANK_GAIN if drop["id"] in starters else 1
            better = candidates[candidates["pos_rank"] <= drop["pos_rank"] - margin].iloc[taken:]
            if better.empty:
                if drop["id"] in starters:
                    break  # starters are the best-ranked; the rest are protected too
                continue
            plan.extend((drop, add, "upgrade") for _, add in better.head(OPTIONS_PER_DROP).iterrows())
            taken += 1
    return plan


def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    """Waiver claims (see _plan_claims for the rules). Every add must be on an
    NFL team, and bids scale with the add's in-season position rank (_bid_for)."""
    db = DatabaseManager()
    try:
        gs = pd.read_sql("SELECT current_bot_id, current_fantasy_week FROM game_statuses", db.engine).iloc[0]
        bot_id, week = gs["current_bot_id"], int(gs["current_fantasy_week"])
        year = int(db.get_league_settings().year)
        me = pd.read_sql(f"SELECT * FROM bots WHERE id = '{bot_id}'", db.engine).iloc[0]
        # The engine's bots table has the budget; the Python harness's doesn't
        # (default to the engine's starting $100 there).
        budget = int(me.get("remaining_waiver_budget", 100))

        players = pd.read_sql("SELECT id, full_name, professional_team, allowed_positions, "
                              "current_bot_id FROM players", db.engine)
        players["pos"] = players["allowed_positions"].apply(lambda x: json.loads(x)[0] if x else "")

        stats = _weekly_stats(db, year, week)
        injuries = _injury_reports(db, year, week)
        game_weeks = _team_game_weeks(db, year)

        # Current team = latest seen this season (players.professional_team is
        # frozen at preseason); must be one of the 32 teams on the schedule.
        seen = pd.concat([stats[["fantasypros_id", "week", "team"]],
                          injuries[["fantasypros_id", "week", "team"]]])
        seen = seen.sort_values("week").drop_duplicates("fantasypros_id", keep="last")
        latest_team = dict(zip(seen["fantasypros_id"], seen["team"].replace(_TEAM_ABBR_TO_POOL)))
        team_now = players["id"].map(latest_team).fillna(players["professional_team"])
        players["plays"] = team_now.map(lambda t: week in game_weeks.get(t, set()))

        players["score"] = _player_scores(db, players, stats, year, week, players["plays"])
        players["pos_rank"] = (players.groupby("pos")["score"]
                               .rank(ascending=False, method="first", na_option="bottom").astype(int))

        latest_week = injuries["week"].max() if not injuries.empty else None
        latest = dict(injuries[injuries["week"] == latest_week][["fantasypros_id", "game_status"]]
                      .fillna("").values) if latest_week is not None else {}
        long_out = _long_term_out(stats, injuries,
                                  dict(zip(players["id"], team_now)), game_weeks, week)

        roster = players[players["current_bot_id"] == bot_id]
        free_agents = players[
            players["current_bot_id"].isna()
            & team_now.isin(set(game_weeks))
            # same exclusions as the draft
            & (team_now != "CAR")
            & (players["full_name"].str.lower() != "tua tagovailoa")
        ]

        slots = db.get_league_settings().player_slots
        starters = {pos: int(slots.get(pos, 0)) for pos in STARTERS_NEEDED}
        starters["QB"] += int(slots.get("SUPERFLEX", 0))
        plan = [(drop, add, reason) for drop, add, reason in _plan_claims(
                    roster, free_agents, latest, long_out, starters)
                if add["pos"] not in ONE_PER_ROSTER or drop["pos"] == add["pos"]]
        claims = []
        for drop, add, reason in plan[:MAX_CLAIMS]:
            bid = _bid_for(add["pos"], int(add["pos_rank"]), budget, reason)
            print(f"Claim: add {add['full_name']} ({add['pos']}{add['pos_rank']}) "
                  f"for {drop['full_name']} ({drop['pos']}{drop['pos_rank']}), {reason}, bid ${bid}")
            claims.append(WaiverClaim(player_to_add_id=str(add["id"]),
                                      player_to_drop_id=str(drop["id"]),
                                      bid_amount=bid))
        return AttemptedFantasyActions(waiver_claims=claims)
    except Exception as e:
        # Never crash the container over waivers — just make no moves.
        print(f"perform_weekly_fantasy_actions failed, no claims: {e}")
        return AttemptedFantasyActions(waiver_claims=[])
    finally:
        db.close()
