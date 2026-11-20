"""Tyler's 2026 draft bot.

The 2026 league drafts between weeks 5 and 6, then the engine replays the season from
fantasy week 1 and scores the already-played weeks with their real stats. So a player's
value is the points he has already banked plus a projection for the weeks still to come.
"""
import datetime
import json
import os
import re
import threading
import time

import numpy as np
import pandas as pd

from blitz_env import AttemptedFantasyActions, WaiverClaim
from blitz_env.models import DatabaseManager

TAG = "[tyler_bot]"

FINAL_WEEK = 17
REGULAR_SEASON_WEEKS = 14
PLAYOFF_WEEK_WEIGHT = 0.5  # weeks 15-17 only matter if we make the playoffs

# Rest-of-season projection
PROJ_SOURCES = ("FantasySharks", "ESPN", "FFToday")
# Fit by backtesting 2025 (drafting after weeks 4/5/6, predicting the rest of the season).
# Weight on actual PPG vs projection (scaled by share of games played), per position.
ACTUAL_WEIGHT = {"QB": 0.15, "RB": 0.3, "WR": 0.15, "TE": 0.15, "K": 0.3, "DST": 0.0}
# Calibration: projections assume full health, so actual totals ran 78-97% of them.
ROS_SCALE = {"QB": 0.82, "RB": 0.85, "WR": 0.79, "TE": 0.93, "K": 0.9, "DST": 0.97}
UNPROJECTED_DISCOUNT = 0.85
FREE_AGENT_DISCOUNT = 0.3

# Injury report (latest week), relative to a healthy player's chance of playing: multiplier
# on next week. "Out" in the two latest reports (likely IR) uses IR_AVAILABILITY for the
# next weeks and IR_ROS_MULT after that.
INJURY_NEXT_WEEK = {"Out": 0.3, "Doubtful": 0.65, "Questionable": 0.85}
IR_AVAILABILITY = (0.4, 0.5, 0.6, 0.7)
IR_ROS_MULT = 0.9

# Absent players (likely IR / long-term out, often missing from the injury report): no stats
# in their team's last ABSENT_LOOKBACK_GAMES games and no near-term external projection. They get
# ABSENT_AVAILABILITY for the next weeks and ABSENT_ROS_MULT after that. Fit on 2025: they
# played 1%/22%/34%/38% as often as everyone else over the next 4 team games, and scored 63%
# of their projection for the rest of the season.
ABSENT_LOOKBACK_GAMES = 2
ABSENT_AVAILABILITY = (0.01, 0.22, 0.34, 0.38)
ABSENT_ROS_MULT = 0.63

# Schedule strength: points each position scores against a defense vs the league average,
# shrunk toward 1 by games played (SOS_SHRINK_GAMES), scaled by SOS_WEIGHT and clipped.
SOS_WEIGHT = 0.5
SOS_SHRINK_GAMES = 4.0
SOS_CLIP = (0.75, 1.25)
TEAM_ABBR_FIX = {"JAX": "JAC", "LA": "LAR"}  # weekly_stats (nflreadr) -> player pool / schedule

# Weekly noise: 0 with prob ZERO_PROB (injury/dud), else Gamma with the same overall mean
N_SIMS = 400
ZERO_PROB = 0.22
GAMMA_SHAPE = 2.4

FLEX_ELIGIBLE = ("RB", "WR", "TE")
SUPERFLEX_ELIGIBLE = ("QB", "RB", "WR", "TE")
SPECIALISTS = ("DST", "K")

QB_DENIAL_WEIGHT = 0.3
MAX_QBS = 3

N_CANDIDATES = 14
N_CANDIDATES_PER_POS = 3
N_OPTIONS = 45
N_DRAFT_SIMS = 500
DEFAULT_SIGMA = 6.0
DEFAULT_ALPHA = 0.4  # prior share of opponents drafting by hindsight value vs rank

LLM_MARGIN_POINTS = 15.0
LLM_MAX_CHOICES = 4
PICK_BUDGET_S = 52.0  # the engine gives each pick 60s
LLM_MIN_TIME_S = 12.0

# Full-draft rollouts: the top prescreened candidates are compared by simulating the rest
# of the draft and the season with common random numbers.
N_ROLLOUT_CANDIDATES = 8
ROLLOUT_BUDGET_S = 20.0
LLM_RESERVE_S = 25.0  # left for the LLM call when an API key is configured
MIN_ROLLOUTS = 24
MAX_ROLLOUTS = 160
EVAL_SIMS = 6  # noisy seasons per rollout
POLICY_OPTIONS = 12  # options my greedy rollout policy considers per pick
OPP_PRIOR = {"rank": 0.35, "value": 0.3, "need": 0.35}  # opponent styles, before evidence
OPP_PRIOR_STRENGTH = 6.0  # prior weight in picks; keeps the model open to bots changing
MIN_SIGMA = 4.0
NEED_PENALTY_SLOTS = 12.0  # "need" opponents push bench-only positions this many slots down
STARTER_CAPACITY = {"QB": 2, "RB": 3, "WR": 3, "TE": 1, "K": 1, "DST": 1}
OPP_POS_CAP = {"QB": 3, "TE": 2, "K": 1, "DST": 1}
PLAYOFF_TEAMS = 6
POINTS_TIEBREAK = 0.0005  # objective = P(playoffs) + this x expected season points
LLM_MARGIN_OBJECTIVE = 0.01

# Waivers (FAAB, $100 for the season, max 10 claims/week, highest bid wins, ties -> worse team).
WAIVER_CANDIDATES = 40  # free agents considered, by rest-of-season value
WAIVER_CANDIDATES_PER_POS = 3  # plus the best few at every position (bye-week K/DST streams)
WAIVER_SIMS = 200
WAIVER_MIN_GAIN = 3.0  # expected season points a swap must add to be worth a claim
# Bid a share of the REMAINING budget by the target's rank among this week's upgrades (best
# gain first): top target 30%, next two 15%, next five 8%, the rest 4%. Won head-to-head
# 2025 tournaments vs. gain-priced bidding (1x-3x) and other tier strengths.
WAIVER_BID_TIERS = ((1, 0.30), (3, 0.15), (8, 0.08), (None, 0.04))
MAX_CLAIMS = 10
MAX_CLAIMS_PER_ADD = 2  # best drop + one fallback drop per target
DEFAULT_WAIVER_BUDGET = 100


# --------------------------------------------------------------------------- data


def _position(allowed):
    try:
        values = json.loads(allowed) if isinstance(allowed, str) else allowed
        return values[0]
    except Exception:  # noqa: BLE001
        return None


def _sql(db, query: str) -> pd.DataFrame:
    try:
        return pd.read_sql(query, db.engine)
    except Exception as e:  # noqa: BLE001 - optional tables may be missing
        print(f"{TAG} query failed ({e!r}): {query[:80]}")
        return pd.DataFrame()


def _weekly_stats(db, year: int) -> pd.DataFrame:
    """Actual weekly points. Older season.db files lack opponent_team (only used for schedule
    strength), so fall back to the core columns rather than losing every stat."""
    base = (f"FROM weekly_stats WHERE CAST(year AS INTEGER) = {year} AND fantasypros_id IS NOT NULL "
            f"AND CAST(week AS INTEGER) <= {FINAL_WEEK} GROUP BY 1, 2")
    core = "SELECT fantasypros_id AS id, CAST(week AS INTEGER) AS week, MAX(FPTS) AS pts, MAX(position) AS stat_position"
    try:
        return pd.read_sql(f"{core}, MAX(opponent_team) AS opp {base}", db.engine)
    except Exception:  # noqa: BLE001
        return _sql(db, f"{core} {base}")


def _load(db):
    settings = pd.read_sql("SELECT * FROM league_settings", db.engine).iloc[0]
    status = pd.read_sql("SELECT * FROM game_statuses", db.engine).iloc[0]
    year = int(settings["year"])
    players = pd.read_sql(
        "SELECT id, full_name, professional_team, player_bye_week, rank, allowed_positions, "
        "availability, current_bot_id, pick_chosen FROM players",
        db.engine,
    )
    players["id"] = players["id"].astype(str)
    players["position"] = players["allowed_positions"].apply(_position)
    players["current_bot_id"] = players["current_bot_id"].where(players["current_bot_id"].notna(), None)
    players.loc[players["current_bot_id"].notna(), "current_bot_id"] = \
        players.loc[players["current_bot_id"].notna(), "current_bot_id"].astype(str)
    bot_ids = [str(b) for b in pd.read_sql("SELECT id FROM bots", db.engine)["id"]]
    data = {
        "weekly": _weekly_stats(db, year),
        "ext": _sql(db,
            f"SELECT fantasypros_id AS id, CAST(week AS INTEGER) AS week, source, points "
            f"FROM external_projections WHERE CAST(year AS INTEGER) = {year} "
            f"AND NOT (position = 'K' AND source = 'ESPN')"),  # ESPN's K projections run ~half the others
        "fp": _sql(db,
            f"SELECT fantasypros_id AS id, CAST(week AS INTEGER) AS week, CAST(FPTS AS REAL) AS points "
            f"FROM weekly_projections WHERE CAST(year AS INTEGER) = {year}"),
        "schedule": _sql(db,
            f"SELECT team, CAST(week AS INTEGER) AS week, opponent FROM schedule "
            f"WHERE CAST(year AS INTEGER) = {year}"),
        "injuries": _sql(db,
            f"SELECT fantasypros_id AS id, CAST(week AS INTEGER) AS week, game_status "
            f"FROM weekly_injuries WHERE CAST(year AS INTEGER) = {year} AND fantasypros_id IS NOT NULL"),
    }
    for df in data.values():
        if "id" in df.columns:
            df["id"] = df["id"].astype(str)
    return settings, status, players, data, bot_ids


# --------------------------------------------------------------------------- value model


def _matchup_factors(played: pd.DataFrame) -> dict:
    """(defense, position) -> multiplier: points that position scores per game against
    that defense relative to the league average, shrunk toward 1 by games played."""
    if played.empty or "opp" not in played.columns or SOS_WEIGHT == 0:
        return {}
    g = played.dropna(subset=["opp", "stat_position"])
    g = g.assign(opp=g["opp"].replace(TEAM_ABBR_FIX))
    per_game = g.groupby(["opp", "week", "stat_position"])["pts"].sum().reset_index()
    per_team = per_game.groupby(["opp", "stat_position"])["pts"].agg(["mean", "count"])
    league = per_game.groupby("stat_position")["pts"].mean()
    avg = per_team.index.get_level_values("stat_position").map(league).to_numpy()
    raw = np.divide(per_team["mean"].to_numpy(), avg, out=np.ones(len(avg)), where=avg > 0)
    shrink = per_team["count"].to_numpy() / (per_team["count"].to_numpy() + SOS_SHRINK_GAMES)
    factor = np.clip(1 + SOS_WEIGHT * (raw - 1) * shrink, *SOS_CLIP)
    return dict(zip(per_team.index, factor))


class Model:
    """Per-player weekly expectations: known points for played weeks, projections after."""

    def __init__(self, players: pd.DataFrame, data: dict, cutoff=None):
        weekly = data["weekly"]
        if cutoff is None:
            rows = weekly.groupby("week").size() if not weekly.empty else pd.Series(dtype=int)
            # A week counts as played once it has at least half the rows of a full week.
            full = rows[rows >= 0.5 * rows.max()].index if not rows.empty else []
            cutoff = int(max(full)) if len(full) else 0
        self.cutoff = int(cutoff)
        played = weekly[weekly["week"] <= self.cutoff] if not weekly.empty else weekly

        self.players = players.set_index("id")
        ids = self.players.index
        pos = self.players["position"]

        # Positions the engine scores as 0 (e.g. kickers' FPTS) are worthless to draft.
        self.dead_positions = set()
        if self.cutoff > 0:
            totals = played.assign(position=played["id"].map(pos)).groupby("position")["pts"].apply(
                lambda s: s.abs().sum())
            self.dead_positions = {p for p, t in totals.items() if t == 0}

        known = played.pivot_table(index="id", columns="week", values="pts", aggfunc="max")
        known = known.reindex(index=ids, columns=range(1, self.cutoff + 1)).fillna(0.0)
        self.known = known.to_numpy(dtype=float)
        games = played.groupby("id")["pts"].count().reindex(ids).fillna(0)
        act_ppg = played.groupby("id")["pts"].mean().reindex(ids)

        ext, fp = data["ext"], data["fp"]
        near_parts = []
        if not ext.empty:
            ext = ext[ext["source"].isin(PROJ_SOURCES)]
            near_parts.append(ext[(ext["week"] > self.cutoff) & (ext["week"] <= self.cutoff + 3)][["id", "points"]])
            pre_ppg = ext[ext["week"] == 0].groupby("id")["points"].mean() / FINAL_WEEK
        else:
            pre_ppg = pd.Series(dtype=float)
        if not fp.empty:
            near_parts.append(fp[(fp["week"] > self.cutoff) & (fp["week"] <= self.cutoff + 3)][["id", "points"]])
        near = pd.concat(near_parts).groupby("id")["points"].mean() if near_parts else pd.Series(dtype=float)
        # FantasyPros' weekly_projections lists future weeks even for sidelined players, so only
        # the external sources count as evidence that an absent player is back.
        ext_near = set(near_parts[0]["id"]) if not ext.empty else set()
        proj_ppg = pd.concat([near.reindex(ids), pre_ppg.reindex(ids)], axis=1).mean(axis=1)

        w = pos.map(ACTUAL_WEIGHT).fillna(0.45) * (games / max(self.cutoff, 1)).clip(upper=1)
        ros = np.where(
            proj_ppg.isna(),
            act_ppg.fillna(0) * UNPROJECTED_DISCOUNT,
            w * act_ppg.fillna(proj_ppg) + (1 - w) * proj_ppg,
        )
        ros = pd.Series(np.clip(ros, 0, None), index=ids) * pos.map(ROS_SCALE).fillna(1.0)
        team = self.players["professional_team"].fillna("FA")
        ros[team.isin(["FA", ""])] *= FREE_AGENT_DISCOUNT
        ros[pos.isin(self.dead_positions)] = 0.0

        # Future weeks: byes and opponents from the NFL schedule (fallback: player_bye_week).
        self.future_weeks = np.arange(self.cutoff + 1, FINAL_WEEK + 1)
        wf = len(self.future_weeks)
        playing = np.ones((len(ids), wf))
        sched = data["schedule"]
        if not sched.empty:
            factors = _matchup_factors(played)
            opponent = {(t, wk): o for t, wk, o in sched[["team", "week", "opponent"]].itertuples(index=False)}
            scheduled_teams = set(sched["team"])
            for i, (t, p) in enumerate(zip(team, pos)):
                if t not in scheduled_teams:
                    continue
                playing[i] = [factors.get((opponent[(t, wk)], p), 1.0) if (t, wk) in opponent else 0.0
                              for wk in self.future_weeks]
        else:
            bye = pd.to_numeric(self.players["player_bye_week"], errors="coerce").to_numpy()
            playing = (self.future_weeks[None, :] != bye[:, None]).astype(float)

        avail = np.ones((len(ids), wf))
        self.absent = pd.Series(False, index=ids)
        if not sched.empty and wf > 0:
            past = sched[sched["week"] <= self.cutoff].sort_values("week")
            last_games = past.groupby("team")["week"].apply(lambda s: set(s.iloc[-ABSENT_LOOKBACK_GAMES:]))
            played_weeks = played.groupby("id")["week"].apply(set)
            for i, (pid, t) in enumerate(zip(ids, team)):
                games = last_games.get(t)
                if not games or len(games) < ABSENT_LOOKBACK_GAMES or pid in ext_near:
                    continue
                if not games & played_weeks.get(pid, set()):
                    self.absent.iloc[i] = True
                    ros.iloc[i] *= ABSENT_ROS_MULT
                    k = min(len(ABSENT_AVAILABILITY), wf)
                    avail[i, :k] = np.array(ABSENT_AVAILABILITY[:k]) / max(ABSENT_ROS_MULT, 1e-9)

        self.injury = pd.Series("", index=ids)
        inj = data["injuries"]
        if not inj.empty and wf > 0:
            inj = inj[inj["week"] <= max(self.cutoff, 1)]
            latest_week = inj["week"].max()
            latest = inj[inj["week"] == latest_week].set_index("id")["game_status"].fillna("")
            latest = latest[~latest.index.duplicated()]
            prev_out = set(inj[(inj["week"] == latest_week - 1) & (inj["game_status"] == "Out")]["id"])
            self.injury = latest.reindex(ids).fillna("")
            for i, (pid, st) in enumerate(self.injury.items()):
                if self.absent.iloc[i]:
                    continue  # absent-player treatment already applied
                if st == "Out" and pid in prev_out:  # out two straight reports: likely IR
                    ros.iloc[i] *= IR_ROS_MULT
                    k = min(len(IR_AVAILABILITY), wf)
                    avail[i, :k] = np.array(IR_AVAILABILITY[:k]) / IR_ROS_MULT
                elif st in INJURY_NEXT_WEEK:
                    avail[i, 0] = INJURY_NEXT_WEEK[st]

        all_weeks = np.arange(1, FINAL_WEEK + 1)
        week_weight = np.where(all_weeks <= REGULAR_SEASON_WEEKS, 1.0, PLAYOFF_WEEK_WEIGHT)
        self.known_weight = week_weight[: self.cutoff]
        self.future_weight = week_weight[self.cutoff:]
        self.ros = ros
        self.expected = ros.to_numpy()[:, None] * playing * avail  # [n, future weeks]
        self.row = {pid: i for i, pid in enumerate(ids)}

        known_total = (self.known * self.known_weight).sum(axis=1)
        future_total = (self.expected * self.future_weight).sum(axis=1)
        self.players["known"] = known_total
        self.players["ros_ppg"] = ros
        self.players["value"] = known_total + future_total
        self.players["future_value"] = future_total

    def simulate(self, ids, seed=7):
        """Points matrix [len(ids), cutoff + N_SIMS * future weeks] (week weights applied)
        plus per-column weights that turn a column sum into an expected season total."""
        rows = [self.row[i] for i in ids]
        rng = np.random.default_rng(seed)
        n, wf = len(rows), len(self.future_weeks)
        noise = rng.gamma(GAMMA_SHAPE, 1.0 / (GAMMA_SHAPE * (1 - ZERO_PROB)), (n, N_SIMS, wf))
        noise[rng.random((n, N_SIMS, wf)) < ZERO_PROB] = 0.0
        future = (self.expected[rows] * self.future_weight)[:, None, :] * noise
        known = self.known[rows] * self.known_weight
        points = np.concatenate([known, future.reshape(n, N_SIMS * wf)], axis=1)
        col_weight = np.concatenate([np.ones(self.cutoff), np.full(N_SIMS * wf, 1.0 / N_SIMS)])
        return points, col_weight


def _sorted_desc(a: np.ndarray, k: int) -> np.ndarray:
    if a.shape[0] == 0 or k <= 0:
        return a[:0]
    return -np.sort(-a, axis=0)[:k]


def lineup_columns(points, positions, slots) -> np.ndarray:
    """Best-lineup score for every column (week x sim). Dedicated slots first, then FLEX,
    then SUPERFLEX -- optimal because FLEX eligibility is a subset of SUPERFLEX's. Players
    with negative scores sit on the bench."""
    pts = np.clip(points, 0, None)
    n_flex, n_sf = slots.get("FLEX", 0), slots.get("SUPERFLEX", 0)
    total = np.zeros(pts.shape[1])
    flex_pool, sf_only_pool = [], []
    for pos in ("QB", "RB", "WR", "TE", "K", "DST"):
        need = slots.get(pos, 0)
        extra = n_flex + n_sf if pos in FLEX_ELIGIBLE else (n_sf if pos in SUPERFLEX_ELIGIBLE else 0)
        top = _sorted_desc(pts[positions == pos], need + extra)
        total += top[:need].sum(axis=0)
        left = top[need:]
        if pos in FLEX_ELIGIBLE:
            flex_pool.append(left)
        elif pos in SUPERFLEX_ELIGIBLE:
            sf_only_pool.append(left)
    flex_all = np.concatenate(flex_pool) if flex_pool else pts[:0]
    flex_top = _sorted_desc(flex_all, n_flex + n_sf)
    total += flex_top[:n_flex].sum(axis=0)
    sf_all = np.concatenate([flex_top[n_flex:]] + sf_only_pool)
    total += _sorted_desc(sf_all, n_sf).sum(axis=0)
    return total


def lineup_total(points, positions, slots, col_weight) -> float:
    """Expected season total of the best weekly lineup."""
    return float((lineup_columns(points, positions, slots) * col_weight).sum())


# --------------------------------------------------------------------------- draft helpers


def _picks_until_next(pick: int, num_teams: int, rounds: int):
    """Picks other teams make between my current pick (1-based overall, snake) and my
    next one; None when this is my last pick."""
    rnd, k = divmod(pick - 1, num_teams)
    if rnd + 1 >= rounds:
        return None
    seat = k if rnd % 2 == 0 else num_teams - 1 - k
    nxt = (rnd + 1) * num_teams + (num_teams - 1 - seat if rnd % 2 == 0 else seat) + 1
    return nxt - pick - 1


def _replacement(df: pd.DataFrame, num_teams: int, slots: dict, dead: set) -> dict:
    """Best player per position left after every team fills its starting lineup."""
    pool = df[~df["position"].isin(dead)].sort_values("value", ascending=False)
    taken = set()
    order = [(p, (p,)) for p in ("QB", "RB", "WR", "TE", "K", "DST")]
    order += [("FLEX", FLEX_ELIGIBLE), ("SUPERFLEX", SUPERFLEX_ELIGIBLE)]
    for slot, eligible in order:
        need = slots.get(slot, 0) * num_teams
        picks = pool[pool["position"].isin(eligible) & ~pool.index.isin(taken)].head(need)
        taken.update(picks.index)
    left = pool[~pool.index.isin(taken)]
    return left.groupby("position")["value"].idxmax().to_dict()


def _ghosts(mine_pos: list, slots: dict, repl: dict, dead: set) -> list:
    """Replacement-level stand-ins for the starting spots my future picks will fill."""
    ghosts = []
    for pos in ("QB", "RB", "WR", "DST", "TE", "K"):
        if pos in dead or pos not in repl:
            continue
        ghosts += [repl[pos]] * max(0, slots.get(pos, 0) - mine_pos.count(pos))
    flex = [repl[p] for p in SUPERFLEX_ELIGIBLE if p in repl and p not in dead]
    if flex:
        ghosts += [flex[0]] * slots.get("SUPERFLEX", 0)  # QB repl, the best SF filler
    flex = [repl[p] for p in FLEX_ELIGIBLE if p in repl and p not in dead]
    if flex:
        ghosts += [flex[0]] * slots.get("FLEX", 0)
    return ghosts


def _eligible(df: pd.DataFrame, mine_pos: list, rounds_left: int, slots: dict, dead: set) -> pd.DataFrame:
    """Draftable positions for a pick with `rounds_left` picks (including it) to go."""
    df = df[~df["position"].isin(dead)]
    needed = [p for p in SPECIALISTS if p not in dead and mine_pos.count(p) < slots.get(p, 0)]
    if rounds_left <= len(needed):
        return df[df["position"].isin(needed)]
    df = df[~df["position"].isin(SPECIALISTS)]
    if mine_pos.count("QB") >= MAX_QBS:
        df = df[df["position"] != "QB"]
    return df


def _calibrate(players: pd.DataFrame, me: str):
    """Noise (sigma, in draft slots) and share of opponents drafting by hindsight value
    rather than consensus rank, fit on the picks already made this draft."""
    drafted = players[players["pick_chosen"].notna() & (players["current_bot_id"] != me)]
    if len(drafted) < 6:
        return DEFAULT_SIGMA, DEFAULT_ALPHA
    rank = pd.to_numeric(players["rank"], errors="coerce").where(lambda r: r > 0)
    rank_pos = rank.rank(method="first", na_option="bottom")
    value_pos = (-players["value"]).rank(method="first")
    pick = drafted["pick_chosen"].astype(float)
    err_rank = (pick - rank_pos[drafted.index]).abs()
    err_value = (pick - value_pos[drafted.index]).abs()
    alpha = ((err_value < err_rank).sum() + 5 * DEFAULT_ALPHA) / (len(drafted) + 5)  # 5 prior picks
    sigma = float(np.clip(np.median(np.minimum(err_rank, err_value)) * 1.5, 3, 25))
    return sigma, float(alpha)


def _survival(available: pd.DataFrame, picks_between: int, sigma: float, alpha: float,
              allow_specialists: bool, rng) -> np.ndarray:
    """[N_DRAFT_SIMS, len(available)] mask of players still there at my next pick."""
    n = len(available)
    eligible = np.ones(n, bool) if allow_specialists else ~available["position"].isin(SPECIALISTS).to_numpy()
    rank = pd.to_numeric(available["rank"], errors="coerce").where(lambda r: r > 0)
    rank_pos = rank.rank(method="first", na_option="bottom").to_numpy()
    value_pos = (-available["value"]).rank(method="first").to_numpy()
    by_value = rng.random(N_DRAFT_SIMS) < alpha
    keys = np.where(by_value[:, None], value_pos[None, :], rank_pos[None, :])
    keys = keys + rng.normal(0, sigma, (N_DRAFT_SIMS, n))
    keys[:, ~eligible] = np.inf
    k = int(min(picks_between, eligible.sum()))
    taken = np.zeros((N_DRAFT_SIMS, n), bool)
    if k > 0:
        idx = np.argpartition(keys, k - 1, axis=1)[:, :k]
        np.put_along_axis(taken, idx, True, axis=1)
    return ~taken


# --------------------------------------------------------------------------- full-draft rollouts


def _seat_order(df: pd.DataFrame, bot_ids: list, me: str, pick_no: int, num_teams: int) -> list:
    """Bot id per draft seat. The engine drafts in a random order (not bots.draft_order), so
    seats come from round-1 picks and my own pick; unknown seats are filled arbitrarily."""
    seats = {}
    first = df[df["pick_chosen"].notna() & (df["pick_chosen"] <= num_teams) & df["current_bot_id"].notna()]
    for b, pk in zip(first["current_bot_id"], first["pick_chosen"]):
        seats[str(b)] = int(pk) - 1
    rnd, k = divmod(pick_no - 1, num_teams)
    seats[me] = k if rnd % 2 == 0 else num_teams - 1 - k
    free = [x for x in range(num_teams) if x not in seats.values()]
    for b in bot_ids:
        if b not in seats and free:
            seats[b] = free.pop(0)
    order = [None] * num_teams
    for b, x in seats.items():
        if 0 <= x < num_teams:
            order[x] = b
    return order


def _opponent_mix(df: pd.DataFrame, bot_ids: list, me: str) -> dict:
    """Per-bot odds of drafting by consensus rank, hindsight value, or value adjusted for
    roster needs: a broad prior, updated with each bot's picks so far tonight."""
    rank = pd.to_numeric(df["rank"], errors="coerce").where(lambda r: r > 0)
    rank_pos = rank.rank(method="first", na_option="bottom")
    value_pos = (-df["value"]).rank(method="first")
    drafted = df[df["pick_chosen"].notna()]
    mixes = {}
    for b in bot_ids:
        if b == me:
            continue
        picks = drafted[drafted["current_bot_id"] == b]
        pk = picks["pick_chosen"].astype(float)
        by_rank = int(((pk - rank_pos[picks.index]).abs() < (pk - value_pos[picks.index]).abs()).sum())
        by_value = len(picks) - by_rank
        counts = {"rank": by_rank, "value": by_value / 2, "need": by_value / 2}
        total = OPP_PRIOR_STRENGTH + len(picks)
        mixes[b] = {t: (OPP_PRIOR_STRENGTH * OPP_PRIOR[t] + counts[t]) / total for t in OPP_PRIOR}
    return mixes


class Rollouts:
    """Simulates the rest of the draft (opponents by sampled style, me by greedy expected
    lineup gain), then the season, and scores my playoff odds."""

    def __init__(self, model: Model, df: pd.DataFrame, slots: dict, num_teams: int, rounds: int,
                 pick_no: int, me: str, order: list, repl: dict, dead: set, sigma: float, mixes: dict):
        self.model, self.slots, self.dead = model, slots, dead
        self.num_teams, self.rounds, self.pick_no, self.me = num_teams, rounds, pick_no, me
        self.order, self.sigma, self.mixes = order, max(sigma, MIN_SIGMA), mixes
        self.ids = list(df.index)
        self.j = {pid: i for i, pid in enumerate(self.ids)}
        rows = [model.row[pid] for pid in self.ids]
        self.pos = df["position"].to_numpy()
        self.known_w = model.known[rows] * model.known_weight
        self.exp_w = model.expected[rows] * model.future_weight
        self.det = np.concatenate([self.known_w, self.exp_w], axis=1)
        self.ones = np.ones(self.det.shape[1])
        rank = pd.to_numeric(df["rank"], errors="coerce").where(lambda r: r > 0)
        self.rank_pos = rank.rank(method="first", na_option="bottom").to_numpy()
        self.value_pos = (-df["value"]).rank(method="first").to_numpy()
        self.value_order = np.argsort(-df["value"].to_numpy(), kind="stable")
        self.avail0 = (df["availability"] == "AVAILABLE").to_numpy()
        owner = df["current_bot_id"].to_numpy()
        self.rosters0 = {b: [i for i in range(len(self.ids)) if owner[i] == b] for b in order}
        self.repl = repl

    def _owner(self, pick: int) -> str:
        rnd, k = divmod(pick - 1, self.num_teams)
        return self.order[k if rnd % 2 == 0 else self.num_teams - 1 - k]

    def _opp_pick(self, kind, roster, avail, orders, noisy_value_pos, ptr):
        counts = {}
        for jj in roster:
            counts[self.pos[jj]] = counts.get(self.pos[jj], 0) + 1
        need_spec = [p for p in SPECIALISTS if counts.get(p, 0) < 1]
        must_spec = self.rounds - len(roster) <= len(need_spec)
        key = "rank" if kind == "rank" else "value"
        seq = orders[key]
        i = ptr[key]
        while i < len(seq) and not avail[seq[i]]:
            i += 1
        ptr[key] = i
        best, best_score = None, np.inf
        for jj in seq[i:]:
            if not avail[jj]:
                continue
            p = self.pos[jj]
            if must_spec:
                if p not in need_spec:
                    continue
            elif p in SPECIALISTS or counts.get(p, 0) >= OPP_POS_CAP.get(p, 99):
                continue
            if kind != "need":
                return jj
            if noisy_value_pos[jj] >= best_score:
                break  # sorted by noisy value: nothing later can beat the best so far
            score = noisy_value_pos[jj] + (NEED_PENALTY_SLOTS if counts.get(p, 0) >= STARTER_CAPACITY.get(p, 1) else 0)
            if score < best_score:
                best, best_score = jj, score
        return best

    def _my_pick(self, roster, avail):
        my_pos = list(self.pos[roster])
        left = self.rounds - len(roster)
        needed = [p for p in SPECIALISTS if p not in self.dead and my_pos.count(p) < self.slots.get(p, 0)]
        must_spec = left <= len(needed)
        opts, per_pos = [], {}
        for jj in self.value_order[:200]:
            if not avail[jj]:
                continue
            p = self.pos[jj]
            if p in self.dead:
                continue
            if must_spec:
                if p not in needed:
                    continue
            elif p in SPECIALISTS or (p == "QB" and my_pos.count("QB") >= MAX_QBS):
                continue
            if len(opts) < POLICY_OPTIONS or per_pos.get(p, 0) < 2:
                opts.append(jj)
                per_pos[p] = per_pos.get(p, 0) + 1
        if not opts:
            return None
        ghosts = [self.j[g] for g in _ghosts(my_pos, self.slots, self.repl, self.dead)[: max(left - 1, 0)]]
        base = list(roster) + ghosts
        best, best_val = None, -np.inf
        for jj in opts:
            rows = base + [jj]
            val = lineup_total(self.det[rows], self.pos[rows], self.slots, self.ones)
            if val > best_val:
                best, best_val = jj, val
        return best

    def _score(self, rosters, rng):
        """(objective, P(playoffs), my expected weighted season points)."""
        cutoff, wf = self.model.cutoff, self.exp_w.shape[1]
        teams = [b for b in self.order if b is not None]
        drafted = sorted({jj for b in teams for jj in rosters[b]})
        loc = {jj: k for k, jj in enumerate(drafted)}
        noise = rng.gamma(GAMMA_SHAPE, 1.0 / (GAMMA_SHAPE * (1 - ZERO_PROB)), (len(drafted), EVAL_SIMS, wf))
        noise[rng.random(noise.shape) < ZERO_PROB] = 0.0
        future = (self.exp_w[drafted][:, None, :] * noise).reshape(len(drafted), EVAL_SIMS * wf)
        pts = np.concatenate([self.known_w[drafted], future], axis=1)
        weekly = np.zeros((len(teams), EVAL_SIMS, cutoff + wf))
        for t, b in enumerate(teams):
            rows = [loc[jj] for jj in rosters[b]]
            cols = lineup_columns(pts[rows], self.pos[rosters[b]], self.slots)
            weekly[t, :, :cutoff] = cols[:cutoff]
            weekly[t, :, cutoff:] = cols[cutoff:].reshape(EVAL_SIMS, wf)
        reg = weekly[:, :, :REGULAR_SEASON_WEEKS]  # [teams, sims, weeks]
        n = len(teams)
        sims = np.arange(EVAL_SIMS)[:, None]
        wins = np.zeros((EVAL_SIMS, n))
        for w in range(reg.shape[2]):
            perm = rng.permuted(np.tile(np.arange(n), (EVAL_SIMS, 1)), axis=1)
            a, b = perm[:, 0:2 * (n // 2):2], perm[:, 1:2 * (n // 2):2]
            score = reg[:, :, w].T  # [sims, teams]
            sa, sb = np.take_along_axis(score, a, 1), np.take_along_axis(score, b, 1)
            wins[sims, a] += sa > sb
            wins[sims, b] += sb > sa
        key = wins * 1e6 + reg.sum(axis=2).T
        me = teams.index(self.me)
        rank = (key > key[:, [me]]).sum(axis=1)
        p_playoffs = float((rank < min(PLAYOFF_TEAMS, n // 2)).mean())
        my_points = float(weekly[me].sum(axis=1).mean())
        return p_playoffs + POINTS_TIEBREAK * my_points, p_playoffs, my_points

    def rollout(self, cand: str, seed: int):
        rng = np.random.default_rng(10_000 + seed)  # same seed for every candidate
        n = len(self.ids)
        kinds = list(OPP_PRIOR)
        styles = {b: kinds[rng.choice(len(kinds), p=[mix[k] for k in kinds])] for b, mix in self.mixes.items()}
        noisy_value_pos = self.value_pos + rng.normal(0, self.sigma, n)
        orders = {"rank": np.argsort(self.rank_pos + rng.normal(0, self.sigma, n)),
                  "value": np.argsort(noisy_value_pos)}
        ptr = {"rank": 0, "value": 0}
        avail = self.avail0.copy()
        rosters = {b: list(r) for b, r in self.rosters0.items()}
        c = self.j[cand]
        avail[c] = False
        rosters[self.me].append(c)
        for pick in range(self.pick_no + 1, self.num_teams * self.rounds + 1):
            b = self._owner(pick)
            if b is None or len(rosters[b]) >= self.rounds:
                continue
            if b == self.me:
                jj = self._my_pick(rosters[b], avail)
            else:
                jj = self._opp_pick(styles.get(b, "value"), rosters[b], avail, orders, noisy_value_pos, ptr)
            if jj is not None:
                avail[jj] = False
                rosters[b].append(jj)
        return self._score(rosters, rng)

    def run(self, cands: list, deadline: float):
        results = {c: [] for c in cands}
        r = 0
        while r < MAX_ROLLOUTS:
            for c in cands:
                results[c].append(self.rollout(c, r))
            r += 1
            if time.time() > deadline and r >= 4:
                break
            if r >= MIN_ROLLOUTS and r % 4 == 0 and len(cands) > 1:
                obj = np.array([[x[0] for x in results[c]] for c in cands])
                best, second = np.argsort(-obj.mean(axis=1))[:2]
                diff = obj[best] - obj[second]
                if diff.mean() > 3 * diff.std(ddof=1) / np.sqrt(r):
                    break
        return results, r


# --------------------------------------------------------------------------- LLM


def _llm_pick(shortlist: pd.DataFrame, model: Model, mine: pd.DataFrame, round_no: int, num_teams: int,
              time_left: float):
    key = os.getenv("OPENAI_API_KEY")
    if not key or os.getenv("TYLER_BOT_DISABLE_LLM") or time_left < LLM_MIN_TIME_S:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None

    def describe(pid, r):
        weeks = model.known[model.row[pid]]
        return (f'- id="{pid}" {r["full_name"]} ({r["position"]}, {r["professional_team"]}): '
                f"weeks 1-{model.cutoff} pts {[round(x, 1) for x in weeks]}, "
                f'projected {r["ros_ppg"]:.1f} pts/game rest of season, injury report "{r["injury"] or "none"}", '
                f'chance still available at my next pick {r["survive"]:.0%}'
                + (f', my playoff odds if I take him {r["p_playoffs"]:.0%}' if pd.notna(r["p_playoffs"]) else ""))

    roster = ", ".join(f'{r["full_name"]} ({r["position"]})' for _, r in mine.iterrows()) or "empty"
    prompt = (
        f"Today is {datetime.date.today():%B %d, %Y}. I'm drafting in a {num_teams}-team bot-vs-bot PPR "
        f"fantasy football league that drafts mid-season; weeks 1-{model.cutoff} are already played and "
        f"score retroactively with real stats. Lineup: QB, 2 RB, 2 WR, FLEX (RB/WR/TE), SUPERFLEX "
        f"(QB/RB/WR/TE), K, DST, 3 bench; no TE slot. Round {round_no}. My roster: {roster}.\n"
        f"My model rates these candidates as nearly equal:\n"
        + "\n".join(describe(pid, r) for pid, r in shortlist.iterrows())
        + "\n\nSearch the web for the latest news on each (injuries, IR, suspensions, depth chart or role "
        "changes, trades) and pick the one who will score the most fantasy points for the rest of "
        'the season. Reply with only JSON: {"id": "<id>", "reason": "<15 words max>"}'
    )
    deadline_s = max(5.0, time_left - 4.0)

    def call():
        client = OpenAI(api_key=key, timeout=deadline_s, max_retries=0)
        resp = client.responses.create(
            model=os.getenv("TYLER_BOT_LLM_MODEL", "gpt-6.1-sol"),
            input=prompt,
            tools=[{"type": "web_search"}],
            reasoning={"effort": os.getenv("TYLER_BOT_LLM_REASONING", "high")},
        )
        return resp.output_text or ""

    t0 = time.time()
    try:
        text = _call_with_deadline(call, deadline_s)
    except Exception as e:  # noqa: BLE001 - bad key, network, API or timeout: keep the model's pick
        print(f"{TAG} LLM call failed after {time.time() - t0:.1f}s: {e!r}")
        return None
    elapsed = time.time() - t0
    try:
        match = re.search(r"\{.*\}", text, re.S)
        answer = json.loads(match.group(0)) if match else {}
        pid = str(answer.get("id", "")) if isinstance(answer, dict) else ""
    except Exception:  # noqa: BLE001
        answer, pid = {}, ""
    if pid in shortlist.index:
        print(f"{TAG} LLM picked {shortlist.at[pid, 'full_name']} in {elapsed:.1f}s: {answer.get('reason', '')}")
        return pid
    print(f"{TAG} LLM answer unusable after {elapsed:.1f}s: {str(text)[:200]!r}")
    return None


def _call_with_deadline(fn, timeout_s: float):
    """Run fn() with a hard wall-clock limit. The worker is a daemon thread, so a hung
    request can't keep the per-pick process alive past the engine's deadline."""
    result = {}

    def run():
        try:
            result["value"] = fn()
        except BaseException as e:  # noqa: BLE001
            result["error"] = e

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise TimeoutError(f"no response within {timeout_s:.0f}s")
    if "error" in result:
        raise result["error"]
    return result["value"]


# --------------------------------------------------------------------------- draft


def _pick(db, started: float) -> str:
    settings, status, players, data, bot_ids = _load(db)
    slots = settings["player_slots"]
    slots = json.loads(slots) if isinstance(slots, str) else dict(slots)
    num_teams, rounds = int(settings["num_teams"]), int(settings["total_rounds"])
    pick_no = int(status["current_draft_pick"])
    me = str(status["current_bot_id"])

    model = Model(players, data)
    df = model.players
    df["injury"] = model.injury
    dead = model.dead_positions
    mine = df[df["current_bot_id"] == me]
    mine_pos = list(mine["position"])
    available = df[df["availability"] == "AVAILABLE"]
    rounds_left = rounds - len(mine)  # including this pick
    repl = _replacement(df, num_teams, slots, dead)

    pool = _eligible(available, mine_pos, rounds_left, slots, dead)
    if pool.empty:
        pool = available
    by_value = pool.sort_values("value", ascending=False)
    cand_ids = list(dict.fromkeys(
        list(by_value.head(N_CANDIDATES).index)
        + list(by_value.groupby("position").head(N_CANDIDATES_PER_POS).index)))
    candidates = pool.loc[cand_ids].copy()

    # Options for my next pick (from players not yet drafted).
    between = _picks_until_next(pick_no, num_teams, rounds)
    ghosts_now = _ghosts(mine_pos, slots, repl, dead)[: max(rounds_left - 1, 0)]
    if between is not None and rounds_left > 1:
        opt_pool = available.sort_values("value", ascending=False)
        option_ids = list(dict.fromkeys(
            list(opt_pool[~opt_pool["position"].isin(SPECIALISTS)].head(N_OPTIONS).index)
            + list(opt_pool[opt_pool["position"].isin(SPECIALISTS)].groupby("position").head(3).index)))
    else:
        option_ids = []

    sim_ids = list(dict.fromkeys(list(mine.index) + cand_ids + option_ids + list(repl.values())))
    points, col_weight = model.simulate(sim_ids)
    sim_row = {pid: i for i, pid in enumerate(sim_ids)}
    sim_pos = np.array([df.at[pid, "position"] for pid in sim_ids])
    mine_rows = [sim_row[p] for p in mine.index]

    def total(extra_ids, ghosts):
        rows = mine_rows + [sim_row[p] for p in extra_ids] + [sim_row[g] for g in ghosts]
        return lineup_total(points[rows], sim_pos[rows], slots, col_weight)

    base = total([], ghosts_now)
    candidates["now"] = [total([c], ghosts_now) for c in cand_ids]
    candidates["gain"] = candidates["now"] - base

    # Opponent-modelled lookahead: expected best lineup after my next pick too.
    candidates["survive"] = 1.0
    candidates["score"] = candidates["now"]
    if option_ids:
        sigma, alpha = _calibrate(df, me)
        rng = np.random.default_rng(11)
        allow_spec = (pick_no - 1) // num_teams >= rounds - 2
        survive = _survival(available, between, sigma, alpha, allow_spec, rng)
        surv_df = pd.DataFrame(survive, columns=available.index)
        candidates["survive"] = surv_df[cand_ids].mean().to_numpy()
        opt_surv = surv_df[option_ids].to_numpy()  # [sims, options]
        for c in cand_ids:
            c_pos = mine_pos + [df.at[c, "position"]]
            nxt_pool = _eligible(df.loc[option_ids], c_pos, rounds_left - 1, slots, dead)
            ghosts_next = _ghosts(c_pos, slots, repl, dead)[: max(rounds_left - 2, 0)]
            floor = total([c], _ghosts(c_pos, slots, repl, dead)[: max(rounds_left - 1, 0)])
            vals = np.full(len(option_ids), -np.inf)
            for j, o in enumerate(option_ids):
                if o != c and o in nxt_pool.index:
                    vals[j] = total([c, o], ghosts_next)
            best = np.where(opt_surv, vals[None, :], -np.inf).max(axis=1)
            candidates.at[c, "score"] = float(np.maximum(best, floor).mean())
        print(f"{TAG} {between} picks until my next; opponents sigma={sigma:.1f} value-share={alpha:.2f}")

    # Superflex QB denial bonus, scaled by the chance an opponent takes him before my next
    # pick (if he'll still be there, I can deny him later).
    qb_repl = df.at[repl["QB"], "value"] if "QB" in repl else 0.0
    is_qb = candidates["position"] == "QB"
    candidates["denial"] = np.where(
        is_qb & (mine_pos.count("QB") < MAX_QBS),
        QB_DENIAL_WEIGHT * (candidates["value"] - qb_repl).clip(lower=0) * (1 - candidates["survive"]), 0.0)
    candidates["score"] += candidates["denial"]

    candidates = candidates.sort_values("score", ascending=False)
    print(f"{TAG} pick {pick_no} (round {(pick_no - 1) // num_teams + 1}), weeks<= {model.cutoff} known, "
          f"dead positions {sorted(dead) or 'none'}")

    # Full-draft rollouts on the top prescreened candidates: P(playoffs) + points tiebreak.
    # Taking a player away from rivals shows up in their simulated rosters, so QB denial is
    # valued here directly.
    candidates["p_playoffs"] = np.nan
    candidates["objective"] = np.nan
    rollout_ids = list(candidates.index[:N_ROLLOUT_CANDIDATES])
    used_rollouts = False
    if len(rollout_ids) > 1 and len(bot_ids) == num_teams:
        try:
            order = _seat_order(df, bot_ids, me, pick_no, num_teams)
            sigma_r, _ = _calibrate(df, me)
            sim = Rollouts(model, df, slots, num_teams, rounds, pick_no, me, order, repl, dead, sigma_r,
                           _opponent_mix(df, bot_ids, me))
            reserve = LLM_RESERVE_S if os.getenv("OPENAI_API_KEY") else 5.0
            deadline = min(time.time() + ROLLOUT_BUDGET_S, started + PICK_BUDGET_S - reserve)
            results, n_rollouts = sim.run(rollout_ids, deadline)
            for c, res in results.items():
                arr = np.array(res)
                candidates.at[c, "objective"] = arr[:, 0].mean()
                candidates.at[c, "p_playoffs"] = arr[:, 1].mean()
            candidates = candidates.loc[rollout_ids].sort_values("objective", ascending=False)
            used_rollouts = True
            print(f"{TAG} {n_rollouts} rollouts x {len(rollout_ids)} candidates")
        except Exception as e:  # noqa: BLE001 - fall back to the one-step lookahead ranking
            print(f"{TAG} rollouts failed ({e!r}); using one-step lookahead")

    for pid, r in candidates.head(6).iterrows():
        print(f"{TAG}   {r['full_name']:<24} {r['position']:<3} value={r['value']:6.1f} gain={r['gain']:6.1f} "
              f"survive={r['survive']:.2f} 1-step={r['score'] - candidates['score'].max():+6.1f} "
              f"P(playoffs)={r['p_playoffs']:.2f} objective={r['objective']:.3f}")

    choice = candidates.index[0]
    if used_rollouts:
        close = candidates["objective"] >= candidates["objective"].iloc[0] - LLM_MARGIN_OBJECTIVE
    else:
        close = candidates["score"] >= candidates["score"].iloc[0] - LLM_MARGIN_POINTS
    shortlist = candidates[close].head(LLM_MAX_CHOICES)
    if len(shortlist) > 1:
        try:
            llm = _llm_pick(shortlist, model, mine, (pick_no - 1) // num_teams + 1, num_teams,
                            PICK_BUDGET_S - (time.time() - started))
        except Exception as e:  # noqa: BLE001 - never let the LLM path cost us the model's pick
            print(f"{TAG} LLM tiebreak failed ({e!r}); keeping the model's pick")
            llm = None
        if llm:
            choice = llm
    print(f"{TAG} drafting {df.at[choice, 'full_name']} ({df.at[choice, 'position']}) "
          f"in {time.time() - started:.1f}s")
    return str(choice)


def _fallback_pick(db) -> str:
    """Best-ranked available player, honoring the K/DST timing. The engine drafts a
    *random* player if we fail, so any sane pick beats raising."""
    players = pd.read_sql(
        "SELECT id, allowed_positions, availability, current_bot_id, rank FROM players", db.engine)
    players["position"] = players["allowed_positions"].apply(_position)
    rank = pd.to_numeric(players["rank"], errors="coerce")
    players["rank"] = rank.where(rank > 0)
    available = players[players["availability"] == "AVAILABLE"].sort_values("rank", na_position="last")
    try:
        me = str(pd.read_sql("SELECT current_bot_id FROM game_statuses", db.engine)["current_bot_id"].iloc[0])
        rounds = int(pd.read_sql("SELECT total_rounds FROM league_settings", db.engine)["total_rounds"].iloc[0])
        mine = players[players["current_bot_id"].astype(str) == me]
        missing = [p for p in SPECIALISTS if not (mine["position"] == p).any()]
        late = rounds - len(mine) <= len(missing)
        pool = available[available["position"].isin(missing)] if late else \
            available[~available["position"].isin(SPECIALISTS)]
        if not pool.empty:
            available = pool
    except Exception:  # noqa: BLE001
        pass
    return str(available["id"].iloc[0])


def draft_player() -> str:
    started = time.time()
    db = DatabaseManager()
    try:
        try:
            return _pick(db, started)
        except Exception as e:  # noqa: BLE001 - never hand the pick to the engine's random auto-draft
            print(f"{TAG} model failed ({e!r}); using rank fallback")
            return _fallback_pick(db)
    finally:
        db.close()


# --------------------------------------------------------------------------- waivers


def _remaining_budget(db, me: str) -> int:
    try:
        df = pd.read_sql(f"SELECT remaining_waiver_budget FROM bots WHERE id = '{me}'", db.engine)
        return int(df["remaining_waiver_budget"].iloc[0])
    except Exception:  # noqa: BLE001 - older/local schemas lack the column
        return DEFAULT_WAIVER_BUDGET


def _still_alive(db, me: str, week: int) -> bool:
    """False once the regular season is over and I have no matchup this week."""
    if week <= REGULAR_SEASON_WEEKS:
        return True
    try:
        df = pd.read_sql(
            f"SELECT COUNT(*) AS n FROM matchups WHERE week = {week} "
            f"AND (home_bot_id = '{me}' OR visitor_bot_id = '{me}')", db.engine)
        return int(df["n"].iloc[0]) > 0
    except Exception:  # noqa: BLE001
        return True


def _waiver_claims(db) -> list:
    started = time.time()
    settings, status, players, data, _ = _load(db)
    slots = settings["player_slots"]
    slots = json.loads(slots) if isinstance(slots, str) else dict(slots)
    me = str(status["current_bot_id"])
    week = int(status["current_fantasy_week"] or 0)
    if week < 1 or week > FINAL_WEEK or not _still_alive(db, me, week):
        print(f"{TAG} week {week}: no waiver claims (season over or eliminated)")
        return []

    # Only use what was known before this fantasy week, so replayed weeks stay honest.
    data = dict(data)
    for k in ("weekly", "injuries"):
        if not data[k].empty:
            data[k] = data[k][data[k]["week"] < week]
    model = Model(players, data, cutoff=week - 1)
    df = model.players
    dead = model.dead_positions
    mine = list(df.index[df["current_bot_id"] == me])
    if not mine:
        return []
    budget = _remaining_budget(db, me)

    free = df[(df["availability"] == "AVAILABLE") & df["current_bot_id"].isna() & ~df["position"].isin(dead)]
    free = free.assign(future=free["value"] - free["known"]).sort_values("future", ascending=False)
    cand = list(dict.fromkeys(list(free.head(WAIVER_CANDIDATES).index)
                              + list(free.groupby("position").head(WAIVER_CANDIDATES_PER_POS).index)))
    if not cand:
        return []

    # Remaining-season lineup value, simulated with common random numbers. In the playoffs
    # every remaining week counts fully.
    weeks = model.future_weeks
    week_w = np.where((weeks <= REGULAR_SEASON_WEEKS) | (week > REGULAR_SEASON_WEEKS), 1.0, PLAYOFF_WEEK_WEIGHT)
    ids = mine + cand
    rows = [model.row[i] for i in ids]
    rng = np.random.default_rng(week)
    wf = len(weeks)
    noise = rng.gamma(GAMMA_SHAPE, 1.0 / (GAMMA_SHAPE * (1 - ZERO_PROB)), (len(ids), WAIVER_SIMS, wf))
    noise[rng.random(noise.shape) < ZERO_PROB] = 0.0
    pts = (model.expected[rows] * week_w)[:, None, :] * noise
    pts = pts.reshape(len(ids), WAIVER_SIMS * wf)
    pos = df.loc[ids, "position"].to_numpy()
    col_w = np.full(WAIVER_SIMS * wf, 1.0 / WAIVER_SIMS)
    at = {pid: i for i, pid in enumerate(ids)}

    def value(roster):
        r = [at[p] for p in roster]
        return lineup_total(pts[r], pos[r], slots, col_w)

    base = value(mine)
    pairs = []
    for a in cand:
        for d in mine:
            gain = value([p for p in mine if p != d] + [a]) - base
            if gain >= WAIVER_MIN_GAIN:
                pairs.append((gain, a, d))
    if not pairs:
        print(f"{TAG} week {week}: no upgrades worth a claim (budget ${budget})")
        return []
    pairs.sort(reverse=True)

    targets = list(dict.fromkeys(a for _, a, _ in pairs))  # by best gain

    def bid_for(a):
        rank = targets.index(a) + 1
        frac = next(f for cap, f in WAIVER_BID_TIERS if cap is None or rank <= cap)
        return int(min(budget, max(1, round(budget * frac))))

    # Best pairs first; later pairs reusing a won add or a used drop become the engine-side
    # fallbacks (it voids claims whose add was won or whose drop already left).
    claims, per_add = [], {}
    for gain, a, d in pairs:
        if per_add.get(a, 0) >= MAX_CLAIMS_PER_ADD:
            continue
        per_add[a] = per_add.get(a, 0) + 1
        claims.append(WaiverClaim(player_to_add_id=str(a), player_to_drop_id=str(d), bid_amount=bid_for(a)))
        print(f"{TAG}   claim {df.at[a, 'full_name']} ({df.at[a, 'position']}) for "
              f"{df.at[d, 'full_name']} ({df.at[d, 'position']}): +{gain:.1f} pts, bid ${bid_for(a)}")
        if len(claims) >= MAX_CLAIMS:
            break
    print(f"{TAG} week {week}: {len(claims)} claims, budget ${budget}, {time.time() - started:.1f}s")
    return claims


def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    db = DatabaseManager()
    try:
        return AttemptedFantasyActions(waiver_claims=_waiver_claims(db))
    except Exception as e:  # noqa: BLE001 - a bad week should cost us claims, not crash the bot
        print(f"{TAG} waiver logic failed ({e!r}); submitting no claims")
        return AttemptedFantasyActions(waiver_claims=[])
    finally:
        db.close()
