"""mitch_bot — a value-based bot for the BotBlitz 2026 mid-season superflex draft.

The 2026 league drafts mid-season and scores every week from week 1 (best-ball
lineups on actual points). A player is valued on rest-of-season points plus HALF the
points he has already scored: those weeks count, but only points that would have
started, and other teams chase hot starts too. Against the other 2026 bots (2026 wks
1-5), half weight lifted starter pts/week from 129.6 to 140.6 vs ignoring banked
points; on a 2025 replay (wks 1-14) it scored 153.0 vs 150.8, where full weight
overpaid for hot starts and fell to 141.5.

  * draft_player          — Value-Over-Replacement drafting on season value:
                            0.5 x points already scored + blended points-per-game x
                            remaining games (byes excluded, minus a game for anyone
                            listed Out; a player no source projects this week while
                            his team plays counts as projected 0), plus a bonus for
                            positional outliers and for players with a big drop-off
                            to what's left at my next pick. Diminishing returns past
                            the startable slots keep the roster balanced; K/DST are
                            deferred to the last rounds.
  * perform_weekly_fantasy_actions
                          — FAAB waivers on the same points-per-game estimate: claim
                            undrafted upgrades over my weakest droppable player and
                            bid a slice of the remaining budget scaled to the upgrade.

Everything is wrapped defensively: on any error a decision falls back to a safe
default (best-available by rank for the draft, no-op for waivers) so the bot never
crashes its container.
"""

from blitz_env import WaiverClaim, AttemptedFantasyActions
from blitz_env.models import DatabaseManager
import pandas as pd
import json

# ----------------------------------------------------------------------------
# Tunable parameters
# ----------------------------------------------------------------------------

FINAL_WEEK = 17  # last NFL week that counts toward the fantasy season
# When the season's stats are already complete (an evaluator replay of a past year),
# draft as if at this week so the bot never sees results it couldn't have known.
REPLAY_DRAFT_WEEK = 6

# Points-per-game blend. Fit on 2025 at the week-6 decision point (predicting wk 6-17
# PPG of draft-relevant players): a single shared blend landed within ~0.1-0.3 MAE of
# per-position optima and beat every input alone, so it's kept simple rather than
# tuned per position on one season. Missing inputs are dropped and the rest reweighted.
PPG_WEIGHTS = {"weekly": 0.50, "actual": 0.25, "pre": 0.25}
MIN_GAMES_FOR_ACTUAL = 2

# Share of points already scored (weeks before the draft) added to a player's value.
# Those weeks are scored, but only the points that would have STARTED count, and other
# teams chase hot starts too -- so it's partial, not the full banked total.
BANKED_WEIGHT = 0.5  # fewer games than this and actual PPG is too noisy to use

# Roster construction targets / floors.
MAX_AT_POSITION = {"QB": 3, "RB": 5, "WR": 6, "TE": 2, "K": 1, "DST": 1}
MIN_AT_POSITION = {"QB": 2, "RB": 2, "WR": 2, "K": 1, "DST": 1}  # must be met by draft's end

# Replacement level per position, as startable players PER TEAM (scaled by league
# size): QB+SUPERFLEX => ~2 QB/team; RB/WR get 2 dedicated starters + a share of
# FLEX/SUPERFLEX. VOR = rest-of-season points - points of the player at that rank.
REPLACEMENT_PER_TEAM = {"QB": 2.0, "RB": 2.5, "WR": 2.5, "TE": 0.86, "K": 1.0, "DST": 1.0}

# Diminishing returns past the realistic number of starters at a position: each
# further body is bench depth worth a fraction of its raw VOR. A 3rd QB almost never
# starts (steep); a 4th RB/WR is real bye/injury depth (gentle).
STARTABLE_SLOTS = {"QB": 2, "RB": 3, "WR": 3, "TE": 1, "K": 1, "DST": 1}
DEPTH_DECAY = {"QB": 0.20, "RB": 0.55, "WR": 0.62, "TE": 0.30, "K": 0.10, "DST": 0.10}

# Outliers: in a superflex league a player far above his position's starter pool is a
# weekly edge nobody else can field. Anyone whose PPG sits OUTLIER_Z standard
# deviations above that pool's mean earns OUTLIER_WEIGHT x his rest-of-season points
# above that threshold, on top of VOR.
OUTLIER_POSITIONS = ("QB", "RB", "WR", "TE")
OUTLIER_Z = 1.5
OUTLIER_WEIGHT = 1.0

# Draft-slot urgency (value over next available): how much better a player is than
# the best at his position I can expect at my NEXT pick, assuming the picks in between
# go to the best players by VOR. Large at the snake's turn, small with a quick next pick.
VONA_WEIGHT = 0.5

# Waiver behaviour.
WAIVER_MAX_CLAIMS = 5           # claims submitted per week (engine cap is 10)
WAIVER_MIN_UPGRADE = 2.0        # min improvement (pts/game) to swap
WAIVER_OUT_DISCOUNT = 0.2       # PPG multiplier for players listed Out (drop targets)
WAIVER_BID_PER_POINT = 1.0      # FAAB bid per pt/game of upgrade
WAIVER_WEEKLY_BUDGET_FRAC = 0.30  # max share of current budget to spend in one week
WAIVER_MIN_WEEKLY_CAP = 4       # but always allow at least this much per week

# Per-position floor that must remain on the roster after any drop, so we can
# always field a legal lineup (QB+SUPERFLEX wants 2 QBs but only 1 is mandatory).
DROP_FLOOR = {"QB": 1, "RB": 2, "WR": 2, "TE": 0, "K": 1, "DST": 1}


# ----------------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------------

def _log(msg):
    print(f"[mitch_bot] {msg}")


def _primary_position(allowed_positions_json):
    """First listed allowed position (the player's primary), or None."""
    try:
        positions = json.loads(allowed_positions_json) if allowed_positions_json else []
        return positions[0] if positions else None
    except Exception:
        return None


def _get_settings(db):
    row = pd.read_sql("SELECT * FROM league_settings", db.engine).iloc[0]
    return {
        "year": int(row["year"]),
        "num_teams": int(row["num_teams"]),
        "total_rounds": int(row["total_rounds"]),
    }


def _get_game_status(db):
    row = pd.read_sql("SELECT * FROM game_statuses", db.engine).iloc[0]
    return {
        "current_bot_id": str(row["current_bot_id"]),
        "current_draft_pick": int(row["current_draft_pick"]),
    }


def _decision_week(db, year):
    """The NFL week about to be played; only data from before it may be used.

    Live season: one past the last week with actual stats. Replay of a finished season
    (stats already through FINAL_WEEK): the engine's current fantasy week during the
    season, or REPLAY_DRAFT_WEEK for the draft, so nothing from the future leaks in."""
    df = pd.read_sql(f"SELECT MAX(CAST(week AS INTEGER)) AS w FROM weekly_stats WHERE year = {year}", db.engine)
    last = df.iloc[0]["w"]
    last = 0 if last is None or pd.isna(last) else int(last)
    if last < FINAL_WEEK:
        return last + 1
    gs = pd.read_sql("SELECT current_fantasy_week FROM game_statuses", db.engine)
    fantasy_week = int(gs.iloc[0]["current_fantasy_week"] or 0) if not gs.empty else 0
    return fantasy_week if fantasy_week >= 1 else REPLAY_DRAFT_WEEK


def _players(db, where):
    df = pd.read_sql(
        f"SELECT id, full_name, rank, professional_team, allowed_positions, availability, current_bot_id "
        f"FROM players WHERE {where}",
        db.engine,
    )
    df["pos"] = df["allowed_positions"].apply(_primary_position)
    return df


def _position_counts(roster_df):
    counts = {}
    for pos in roster_df["pos"]:
        if pos:
            counts[pos] = counts.get(pos, 0) + 1
    return counts


def _ppg_estimates(db, year, week):
    """id -> blended points-per-game going forward, from next week's projection, actual
    PPG so far (weeks < `week`, games played only), and preseason projection / 17."""
    ext = pd.read_sql(
        f"SELECT fantasypros_id AS id, CAST(week AS INTEGER) AS week, position, points "
        f"FROM external_projections WHERE year = {year} AND CAST(week AS INTEGER) IN (0, {week})",
        db.engine,
    )
    ext["id"] = ext["id"].astype(str)
    this_week = ext[ext["week"] == week]
    weekly = this_week.groupby("id")["points"].mean()
    pre_rows = ext[ext["week"] == 0]
    pre = pre_rows.groupby("id")["points"].mean() / 17.0

    # No projection this week from any source means "not expected to play" (injured,
    # IR, benched) -- count it as 0 rather than falling back to the preseason number.
    # Exceptions: his team is on bye (no one on it is projected this week), or no source
    # covers his position this week; then it's just missing and the blend reweights.
    teams = pd.read_sql("SELECT id, professional_team FROM players", db.engine)
    team_of = dict(zip(teams["id"].astype(str), teams["professional_team"]))
    playing_teams = {team_of.get(pid) for pid in this_week["id"]}
    covered_positions = set(this_week["position"])
    for pid, pos in pre_rows.groupby("id")["position"].first().items():
        if (pid not in weekly.index and pos in covered_positions
                and team_of.get(pid) in playing_teams):
            weekly.loc[pid] = 0.0

    stats = pd.read_sql(
        f"SELECT fantasypros_id AS id, FPTS FROM weekly_stats "
        f"WHERE year = {year} AND CAST(week AS INTEGER) < {week} AND fantasypros_id IS NOT NULL",
        db.engine,
    )
    stats["id"] = stats["id"].astype(str)
    stats["FPTS"] = pd.to_numeric(stats["FPTS"], errors="coerce").fillna(0.0)
    agg = stats.groupby("id")["FPTS"].agg(["mean", "count"])
    actual = agg.loc[agg["count"] >= MIN_GAMES_FOR_ACTUAL, "mean"]

    parts = {"weekly": weekly, "actual": actual, "pre": pre}
    df = pd.DataFrame(parts)
    num = sum(df[k].fillna(0.0) * w for k, w in PPG_WEIGHTS.items())
    den = sum(df[k].notna() * w for k, w in PPG_WEIGHTS.items())
    return (num / den).dropna().to_dict()


def _banked_points(db, year, week):
    """id -> points already scored this season (weeks before `week`)."""
    df = pd.read_sql(
        f"SELECT fantasypros_id AS id, FPTS FROM weekly_stats "
        f"WHERE year = {year} AND CAST(week AS INTEGER) < {week} AND fantasypros_id IS NOT NULL",
        db.engine,
    )
    df["id"] = df["id"].astype(str)
    df["FPTS"] = pd.to_numeric(df["FPTS"], errors="coerce").fillna(0.0)
    return df.groupby("id")["FPTS"].sum().to_dict()


def _out_ids(db, year, week):
    """Players listed Out on the latest injury report published by `week`."""
    df = pd.read_sql(
        f"SELECT fantasypros_id AS id FROM weekly_injuries WHERE year = {year} "
        f"AND game_status = 'Out' AND CAST(week AS INTEGER) = (SELECT MAX(CAST(week AS INTEGER)) "
        f"FROM weekly_injuries WHERE year = {year} AND CAST(week AS INTEGER) <= {week})",
        db.engine,
    )
    return set(df["id"].dropna().astype(str))


def _games_remaining(db, year, week):
    """team -> games scheduled from `week` through FINAL_WEEK (byes excluded). Empty if
    this season.db has no schedule table; callers then assume a flat game count."""
    try:
        df = pd.read_sql(
            f"SELECT team, COUNT(*) AS n FROM schedule WHERE year = {year} "
            f"AND week BETWEEN {week} AND {FINAL_WEEK} GROUP BY team",
            db.engine,
        )
    except Exception:
        return {}
    return dict(zip(df["team"], df["n"]))


# ----------------------------------------------------------------------------
# DRAFT
# ----------------------------------------------------------------------------

def _replacement_values(avail, num_teams):
    """For each position, the rest-of-season points of the replacement-level player."""
    replacement = {}
    for pos, per_team in REPLACEMENT_PER_TEAM.items():
        pts = avail.loc[avail["pos"] == pos, "ros"].dropna().sort_values(ascending=False)
        if pts.empty:
            replacement[pos] = 0.0
        else:
            rank = max(1, round(per_team * num_teams))
            replacement[pos] = float(pts.iloc[min(rank, len(pts)) - 1])
    return replacement


def _outlier_bonus(everyone, num_teams):
    """id -> bonus for players far above their position's starter pool. The pool is
    measured over ALL players (drafted or not), so it stays fixed during the draft
    instead of promoting the next-best once the real outliers are gone."""
    bonus = {}
    for pos in OUTLIER_POSITIONS:
        pool_size = max(2, round(REPLACEMENT_PER_TEAM[pos] * num_teams))
        pool = everyone[everyone["pos"] == pos].dropna(subset=["ppg"])
        pool = pool.sort_values("ppg", ascending=False).head(pool_size)
        if len(pool) < 2:
            continue
        threshold = pool["ppg"].mean() + OUTLIER_Z * pool["ppg"].std()
        for _, r in pool[pool["ppg"] > threshold].iterrows():
            bonus[str(r["id"])] = OUTLIER_WEIGHT * (r["ppg"] - threshold) * r["games"]
    return bonus


def _picks_before_my_next(pick, num_teams, total_rounds):
    """Picks other teams make between this pick (1-indexed) and my next one in a
    snake draft; None if this is my last pick."""
    rnd, idx = divmod(pick - 1, num_teams)
    seat = idx if rnd % 2 == 0 else num_teams - 1 - idx
    if rnd + 1 >= total_rounds:
        return None
    nxt = (rnd + 1) * num_teams + (seat if (rnd + 1) % 2 == 0 else num_teams - 1 - seat) + 1
    return nxt - pick - 1


def _vona(avail, replacement, picks_between):
    """id -> value over next available: this player's rest-of-season points minus the
    best at his position expected to survive until my next pick, assuming the picks in
    between go to the top of the board by raw VOR."""
    if not picks_between:
        return {}
    board = avail.dropna(subset=["ros", "pos"]).copy()
    board["vor"] = board["ros"] - board["pos"].map(replacement).fillna(0.0)
    taken = board.sort_values("vor", ascending=False).head(picks_between)["pos"].value_counts()
    out = {}
    for pos, grp in board.groupby("pos"):
        pts = grp.sort_values("ros", ascending=False)
        k = int(taken.get(pos, 0))
        for i, (pid, r) in enumerate(zip(pts["id"], pts["ros"])):
            rest = pts["ros"].iloc[[j for j in range(len(pts)) if j != i]]
            survivor = rest.iloc[k] if k < len(rest) else replacement.get(pos, 0.0)
            out[str(pid)] = max(0.0, r - survivor)
    return out


def draft_player() -> str:
    db = DatabaseManager()
    try:
        settings = _get_settings(db)
        status = _get_game_status(db)
        year = settings["year"]
        week = _decision_week(db, year)

        roster = _players(db, f"current_bot_id = '{status['current_bot_id']}'")
        counts = _position_counts(roster)
        rounds_left = settings["total_rounds"] - len(roster)  # including this pick

        everyone = _players(db, "1 = 1")
        ppg = _ppg_estimates(db, year, week)
        out = _out_ids(db, year, week)
        games = _games_remaining(db, year, week)
        default_games = max(0, FINAL_WEEK - week)  # 1 bye already behind most teams

        everyone["ppg"] = everyone["id"].astype(str).map(ppg)
        everyone["games"] = [
            max(0, games.get(team, default_games) - (1 if str(pid) in out else 0))
            for pid, team in zip(everyone["id"], everyone["professional_team"])
        ]
        forward = everyone["ppg"] * everyone["games"]
        past = everyone["id"].astype(str).map(_banked_points(db, year, week))
        everyone["ros"] = forward.fillna(0.0) + BANKED_WEIGHT * past.fillna(0.0)
        everyone.loc[forward.isna() & past.isna(), "ros"] = float("nan")  # nothing known
        outlier = _outlier_bonus(everyone, settings["num_teams"])

        avail = everyone[everyone["availability"] == "AVAILABLE"].copy()
        if avail.empty:
            return ""
        replacement = _replacement_values(avail, settings["num_teams"])
        picks_between = _picks_before_my_next(
            status["current_draft_pick"], settings["num_teams"], settings["total_rounds"])
        vona = _vona(avail, replacement, picks_between)

        # Forced onto mandatory needs once the remaining rounds only just cover them.
        unmet = {p: max(0, MIN_AT_POSITION[p] - counts.get(p, 0)) for p in MIN_AT_POSITION}
        forced = rounds_left <= sum(unmet.values())
        # K and DST are streamy: only consider them in the last two rounds (or if forced).
        allow_kdst = rounds_left <= 2 or forced

        def value(row):
            pos = row["pos"]
            if pos is None:
                return -1e6
            if counts.get(pos, 0) >= MAX_AT_POSITION.get(pos, 99):
                return -1e6
            if pos in ("K", "DST") and not allow_kdst:
                return -1e6
            if forced and unmet.get(pos, 0) == 0:
                return -1e6
            if pd.isna(row["ros"]):
                # No projection or stats: deep player, below any real VOR but still
                # ordered by rank so we can fill out a roster if needed.
                return -1e5 - float(row["rank"] if pd.notna(row["rank"]) else 9999)
            pid = str(row["id"])
            vor = (row["ros"] - replacement.get(pos, 0.0)
                   + outlier.get(pid, 0.0) + VONA_WEIGHT * vona.get(pid, 0.0))
            have = counts.get(pos, 0)
            startable = STARTABLE_SLOTS.get(pos, 1)
            if have >= startable:
                vor *= DEPTH_DECAY.get(pos, 0.5) ** (have - startable + 1)
            return vor

        avail["score"] = avail.apply(value, axis=1)
        avail = avail.sort_values(by=["score", "rank"], ascending=[False, True])

        best = avail.iloc[0]
        if best["score"] <= -1e6:
            best = avail.sort_values(by="rank").iloc[0]
        bid = str(best["id"])
        _log(f"pick {status['current_draft_pick']} (wk{week}): {best['full_name']} "
             f"({best['pos']}, ros={best['ros']:.1f}, outlier={outlier.get(bid, 0.0):.1f}, "
             f"vona={vona.get(bid, 0.0):.1f}, score={best['score']:.1f})")
        return str(best["id"])
    except Exception as e:
        _log(f"draft fell back to best-available: {e}")
        try:
            df = pd.read_sql(
                "SELECT id FROM players WHERE availability = 'AVAILABLE' ORDER BY rank ASC LIMIT 1",
                db.engine,
            )
            return str(df.iloc[0]["id"]) if not df.empty else ""
        except Exception:
            return ""
    finally:
        db.close()


# ----------------------------------------------------------------------------
# WAIVERS
# ----------------------------------------------------------------------------

def _get_budget(db, bot_id):
    try:
        df = pd.read_sql(f"SELECT remaining_waiver_budget FROM bots WHERE id = '{bot_id}'", db.engine)
        return int(df.iloc[0]["remaining_waiver_budget"])
    except Exception:
        return 0


def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    db = DatabaseManager()
    try:
        settings = _get_settings(db)
        status = _get_game_status(db)
        year = settings["year"]
        bot_id = status["current_bot_id"]
        week = _decision_week(db, year)
        budget = _get_budget(db, bot_id)

        ppg = _ppg_estimates(db, year, week)
        out = _out_ids(db, year, week)

        def val(pid):
            pid = str(pid)
            v = ppg.get(pid, 0.0)
            return v * WAIVER_OUT_DISCOUNT if pid in out else v

        # My roster, weakest first (Out players sink to the top of the drop list).
        roster = _players(db, f"current_bot_id = '{bot_id}'")
        roster["val"] = roster["id"].apply(val)
        roster = roster.sort_values("val", ascending=True)
        live_counts = _position_counts(roster)

        # Available players with an estimate, strongest first.
        avail = _players(db, "current_bot_id IS NULL")
        avail = avail[avail["id"].astype(str).isin(ppg.keys())]
        avail["val"] = avail["id"].apply(val)
        avail = avail.sort_values("val", ascending=False)

        claims = []
        used_drops = set()
        remaining = budget
        # Spend at most a fraction of the current budget in any single week so FAAB
        # lasts the whole season (it tapers as budget shrinks, never hitting zero).
        weekly_cap = max(WAIVER_MIN_WEEKLY_CAP, int(WAIVER_WEEKLY_BUDGET_FRAC * budget))
        spent = 0

        for _, add in avail.iterrows():
            if len(claims) >= WAIVER_MAX_CLAIMS or remaining <= 0 or spent >= weekly_cap:
                break
            if add["pos"] is None:
                continue
            for _, drop in roster.iterrows():
                drop_id = str(drop["id"])
                drop_pos = drop["pos"]
                if drop_id in used_drops or drop_pos is None:
                    continue
                if live_counts.get(drop_pos, 0) <= DROP_FLOOR.get(drop_pos, 0):
                    continue  # would break a positional floor
                upgrade = add["val"] - drop["val"]
                if upgrade < WAIVER_MIN_UPGRADE:
                    continue
                bid = max(1, int(round(upgrade * WAIVER_BID_PER_POINT)))
                bid = min(bid, remaining, weekly_cap - spent)
                if bid < 1:
                    break  # weekly budget slice exhausted
                claims.append(WaiverClaim(
                    player_to_add_id=str(add["id"]),
                    player_to_drop_id=drop_id,
                    bid_amount=bid,
                ))
                used_drops.add(drop_id)
                live_counts[drop_pos] = live_counts.get(drop_pos, 0) - 1
                live_counts[add["pos"]] = live_counts.get(add["pos"], 0) + 1
                remaining -= bid
                spent += bid
                _log(f"wk{week} claim: +{add['full_name']} ({add['val']:.1f}) / "
                     f"-{drop['full_name']} ({drop['val']:.1f}) bid {bid}")
                break

        return AttemptedFantasyActions(waiver_claims=claims)
    except Exception as e:
        _log(f"waiver no-op due to error: {e}")
        return AttemptedFantasyActions(waiver_claims=[])
    finally:
        db.close()
