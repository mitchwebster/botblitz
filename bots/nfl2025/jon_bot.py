"""Jon's balanced, stateless bot. All inputs come from the engine's season DB.

Draft: projected roster value, positional scarcity, and legal slot coverage.
Waivers: durable upgrades plus immediate bye/injury coverage, with bounded FAAB.
The public functions are deliberately self-contained in this single shipped file.
"""

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from statistics import median

from sqlalchemy import text
from blitz_env import AttemptedFantasyActions, WaiverClaim, parse_positions
from blitz_env.models import DatabaseManager

FINAL_WEEK = 17
BENCH_WEIGHT = 0.20
MIN_UPGRADE = 0.75
MAX_UPGRADE_GROUPS = 2
ALTERNATIVES_PER_DROP = 3
DEFAULT_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "SUPERFLEX": 1,
                 "FLEX": 1, "K": 1, "DST": 1, "BENCH": 3}
POSITIONS = frozenset(("QB", "RB", "WR", "TE", "K", "DST"))


def _number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _pid(value):
    value = str(value) if value is not None else ""
    return value[:-2] if value.endswith(".0") else value


def _points(row):
    for key in ("FPTS", "fantasy_points_ppr", "points"):
        if row.get(key) is not None:
            value = _number(row[key], float("nan"))
            if math.isfinite(value):
                return max(0.0, value)
    return None


def _read(conn, table, year=None, week=None, history=False, completed=False):
    """Missing optional reference tables are normal; required state isn't optional."""
    columns = {r[1] for r in conn.execute(text("PRAGMA table_info(%s)" % table))}
    if not columns:
        return []
    clauses, params = [], {}
    year_column = "year" if "year" in columns else "season"
    if year is not None:
        # Never consume rows of an unknown season or current-season final totals.
        if year_column not in columns:
            return []
        if history:
            clauses.append("CAST(%s AS INTEGER) >= :start AND CAST(%s AS INTEGER) < :year"
                           % (year_column, year_column))
            params["start"] = year - 3
        else:
            clauses.append("CAST(%s AS INTEGER) = :year" % year_column)
        params["year"] = year
    if week is not None:
        if "week" not in columns:
            return []
        clauses.append("CAST(week AS INTEGER) %s :week" % ("<" if completed else "="))
        params["week"] = week
        if completed:
            clauses.append("CAST(week AS INTEGER) >= 1")
    sql = "SELECT * FROM " + table
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    return [dict(r) for r in conn.execute(text(sql), params).mappings()]


@dataclass(frozen=True)
class Candidate:
    id: str
    name: str
    positions: frozenset
    rank: float
    team: str
    bye: int
    owner: str
    available: bool
    value: float = 0.0  # Expected points per healthy week, not season totals.
    upcoming: float = 0.0


class Snapshot:
    def __init__(self, db, weekly=False):
        with db.engine.connect() as conn:
            settings = _read(conn, "league_settings")
            status = _read(conn, "game_statuses")
            if not settings or not status:
                raise ValueError("Bot requires initialized league settings and game status")
            settings, status = settings[0], status[0]
            self.year = int(settings["year"])
            self.week = int(status.get("current_fantasy_week") or 1)
            self.bot_id = _pid(status["current_bot_id"])
            if not self.bot_id:
                raise ValueError("Bot requires current_bot_id to identify its roster")
            slots = settings.get("player_slots")
            self.slots = json.loads(slots) if isinstance(slots, str) else slots
            self.slots = self.slots or DEFAULT_SLOTS
            self.teams = max(1, int(settings.get("num_teams") or 1))
            bots = _read(conn, "bots")
            bot = next((b for b in bots if _pid(b["id"]) == self.bot_id), {})
            self.budget = max(0, int(bot.get("remaining_waiver_budget") or 0))
            raw_players = _read(conn, "players")
            if not raw_players:
                raise ValueError("Bot requires a populated player pool")
            self.players = []
            for row in raw_players:
                positions = frozenset("DST" if p.upper() in ("DEF", "D/ST") else p.upper()
                                      for p in parse_positions(row.get("allowed_positions")))
                owner = _pid(row.get("current_bot_id"))
                self.players.append(Candidate(
                    _pid(row["id"]), row.get("full_name") or str(row["id"]),
                    positions, _number(row.get("rank"), 9999),
                    row.get("professional_team") or "", int(row.get("player_bye_week") or 0),
                    owner, row.get("availability") == "AVAILABLE" and not owner))
            self._set_values(conn, raw_players, weekly)
        self.roster = [p for p in self.players if p.owner == self.bot_id]
        self.available = [p for p in self.players if p.available and p.positions & POSITIONS]

    def _set_values(self, conn, raw_players, weekly):
        projections = defaultdict(dict)
        for row in _read(conn, "external_projections", self.year, 0):
            points = _points(row)
            if points is not None:
                projections[_pid(row.get("fantasypros_id"))][row.get("source", "external")] = points / 17
        preseason = _read(conn, "preseason_projections", self.year)
        # Some refreshed preseason tables contain weekly-sized numbers. Don't mix
        # those with annual totals; use the supplied external consensus/rank instead.
        maxima = defaultdict(float)
        for row in preseason:
            maxima[str(row.get("position", "")).upper()] = max(
                maxima[str(row.get("position", "")).upper()], _points(row) or 0)
        for row in preseason:
            points = _points(row)
            if points is not None and maxima[str(row.get("position", "")).upper()] > 50:
                projections[_pid(row.get("fantasypros_id"))]["FantasyPros"] = points / 17
        historical = defaultdict(list)
        for row in _read(conn, "season_stats", self.year, history=True):
            points = _points(row)
            games = _number(row.get("G", row.get("games")))
            if points is not None and games >= 4:
                historical[_pid(row.get("fantasypros_id"))].append(
                    (int(_number(row.get("year", row.get("season")))), points / games))
        recent, upcoming, injuries = defaultdict(dict), defaultdict(dict), {}
        scheduled_teams = set()
        if weekly:
            for row in _read(conn, "weekly_stats", self.year, self.week, completed=True):
                points = _points(row)
                if points is not None:
                    recent[_pid(row.get("fantasypros_id"))][int(_number(row["week"]))] = points
            for table, source in (("external_projections", "external"),
                                  ("weekly_projections", "FantasyPros")):
                for row in _read(conn, table, self.year, self.week):
                    points = _points(row)
                    if points is not None:
                        upcoming[_pid(row.get("fantasypros_id"))][row.get("source", source)] = points
            for row in _read(conn, "weekly_injuries", self.year, self.week):
                injuries[_pid(row.get("fantasypros_id"))] = str(row.get("game_status") or "").lower()
            scheduled_teams = {r["team"] for r in _read(conn, "schedule", self.year, self.week)}
        position_ranks = {_pid(r["id"]): _number(r.get("position_rank"), 0) for r in raw_players}
        updated = []
        for p in self.players:
            pos = next((x for x in ("QB", "RB", "WR", "TE", "K", "DST") if x in p.positions), "WR")
            ceiling, decay = {"QB": (24, .035), "RB": (22, .065), "WR": (22, .055),
                              "TE": (14, .08), "K": (9, .02), "DST": (9, .035)}[pos]
            rank = position_ranks[p.id] or (1 + sum(q.rank < p.rank and pos in q.positions for q in self.players))
            fallback = ceiling / (1 + decay * max(0, rank - 1))
            if historical[p.id]:
                history = sorted(historical[p.id], reverse=True)
                weights = [0.5 ** (self.year - y - 1) for y, _ in history]
                estimate = sum(v * w for (_, v), w in zip(history, weights)) / sum(weights)
                fallback = .65 * fallback + .35 * estimate
            value = median(projections[p.id].values()) if projections[p.id] else fallback
            observations = sorted(recent[p.id].items(), reverse=True)[:4]
            if observations:
                weights = [.75 ** (self.week - w - 1) for w, _ in observations]
                form = sum(v * wt for (_, v), wt in zip(observations, weights)) / sum(weights)
                influence = min(.60, .12 * len(recent[p.id]))
                value = (1 - influence) * value + influence * form
            near_term = .75 * median(upcoming[p.id].values()) + .25 * value if upcoming[p.id] else value
            injury = injuries.get(p.id, "")
            if injury in ("out", "ir", "injured reserve", "pup", "suspended"):
                near_term = 0.0
                if injury in ("ir", "injured reserve", "pup"):
                    value *= .65
            elif injury == "doubtful":
                near_term *= .15
            elif injury == "questionable":
                near_term *= .80
            bye = p.team not in scheduled_teams if scheduled_teams and p.team else p.bye == self.week
            if weekly and bye:
                near_term = 0.0
            updated.append(Candidate(p.id, p.name, p.positions, p.rank, p.team, p.bye,
                                     p.owner, p.available, value, near_term))
        self.players = updated


def _slots(settings):
    result = []
    for name, count in settings.items():
        name = name.upper()
        eligible = {"FLEX": {"RB", "WR", "TE"}, "SUPERFLEX": {"QB", "RB", "WR", "TE"},
                    "BENCH": POSITIONS, "DST": {"DST"}, "DEF": {"DST"}}.get(name, {name})
        result.extend((name, eligible) for _ in range(int(count)))
    return result


def _roster_value(players, settings, upcoming=False, draft=False):
    """Exact maximum-weight slot assignment (rectangular Hungarian algorithm).

    Each player occupies one eligible slot. Bench depth receives partial credit;
    impossible rosters return -inf. Flexible players are not assigned greedily.
    """
    slots = _slots(settings)
    n, m = len(players), len(slots)
    if n > m:
        return -math.inf
    if not n:
        return 0.0
    costs = []
    for player in players:
        points = player.upcoming if upcoming else player.value
        weights = []
        for name, eligible in slots:
            depth = .025 if player.positions <= {"K", "DST"} else BENCH_WEIGHT
            value = points * depth if name == "BENCH" else points + (4 if draft else 0)
            weights.append(-value if player.positions & eligible else 1e6)
        costs.append(weights)
    u, v, occupied, previous = [0.] * (n + 1), [0.] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        occupied[0], j0 = i, 0
        best, used = [math.inf] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = occupied[j0], math.inf, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cost = costs[i0 - 1][j - 1] - u[i0] - v[j]
                    if cost < best[j]:
                        best[j], previous[j] = cost, j0
                    if best[j] < delta:
                        delta, j1 = best[j], j
            for j in range(m + 1):
                if used[j]:
                    u[occupied[j]] += delta
                    v[j] -= delta
                else:
                    best[j] -= delta
            j0 = j1
            if not occupied[j0]:
                break
        while j0:
            occupied[j0] = occupied[previous[j0]]
            j0 = previous[j0]
    assigned = [costs[occupied[j] - 1][j - 1] for j in range(1, m + 1) if occupied[j]]
    return -sum(assigned) if all(c < 1e5 for c in assigned) else -math.inf


def _choose_draft(state):
    baseline = _roster_value(state.roster, state.slots, draft=True)
    # Replacement is the estimated last starter at each position across the league.
    demand = defaultdict(float)
    for name, eligible in _slots(state.slots):
        if name == "BENCH":
            continue
        shares = {"QB": .85, "RB": .075, "WR": .075} if name == "SUPERFLEX" else (
            {"RB": .45, "WR": .45, "TE": .10} if name == "FLEX" else {next(iter(eligible)): 1})
        for pos, share in shares.items():
            demand[pos] += share * state.teams
    replacement = {}
    for pos in POSITIONS:
        pool = sorted((p.value for p in state.available if pos in p.positions), reverse=True)
        drafted = sum(not p.available and bool(p.owner) and pos in p.positions for p in state.players)
        index = max(0, int(math.ceil(demand[pos])) - drafted - 1)
        replacement[pos] = pool[min(index, len(pool) - 1)] if pool else 0
    scored = []
    for player in state.available:
        after = _roster_value(state.roster + [player], state.slots, draft=True)
        if not math.isfinite(after) or not math.isfinite(baseline):
            continue
        gain = after - baseline
        scarcity = max((player.value - replacement[p] for p in player.positions if p in replacement), default=0)
        contribution = min(1.0, max(0.0, (gain - 4) / max(1, player.value)))
        score = gain + .40 * max(0, scarcity) * contribution
        # Avoid sharing a QB bye with the only other QB, without overriding value.
        if "QB" in player.positions and player.bye and any(
                "QB" in p.positions and p.bye == player.bye for p in state.roster):
            score -= .6
        scored.append((score, -player.rank, player.id, player))
    if scored:
        return max(scored, key=lambda x: x[:3])[3]
    # An already-invalid roster must still produce a legal available pick.
    return min(state.available, key=lambda p: (p.rank, p.id)) if state.available else None


def draft_player() -> str:
    db = DatabaseManager()
    try:
        state = Snapshot(db)
        player = _choose_draft(state)
        if player:
            print("Draft %s: projected %.1f points/week" % (player.name, player.value))
        return player.id if player else ""
    finally:
        db.close()


def _upgrade_gain(roster, add, drop_id, settings):
    if not any(p.id == drop_id for p in roster):
        return None
    after = [p for p in roster if p.id != drop_id] + [add]
    future = _roster_value(after, settings) - _roster_value(roster, settings)
    immediate = (_roster_value(after, settings, upcoming=True)
                 - _roster_value(roster, settings, upcoming=True))
    # Don't sacrifice a strong healthy long-term player merely for a bye-week fill-in.
    max_sacrifice = 2 if add.positions <= {"K", "DST"} else 1
    gain = .65 * future + .35 * immediate
    if not math.isfinite(gain) or future < -max_sacrifice or gain < MIN_UPGRADE:
        return None
    return gain


def _waiver_claims(state):
    if not state.roster or not 1 <= state.week <= FINAL_WEEK:
        return []
    # Shortlist both durable prospects and this week's best options by position.
    shortlist = {}
    for pos in POSITIONS:
        pool = [p for p in state.available if pos in p.positions]
        for metric in ("value", "upcoming"):
            for p in sorted(pool, key=lambda p: (-getattr(p, metric), p.rank, p.id))[:8]:
                shortlist[p.id] = p
    candidates = sorted(shortlist.values(), key=lambda p: (p.rank, p.id))
    # Conserve early; release reserves as opportunities to spend disappear.
    fraction = .35 if state.week < 12 else (.60 if state.week < 15 else 1.0)
    spending_limit = min(state.budget, max(1, int(state.budget * fraction))) if state.budget else 0
    claims, used_adds, used_drops = [], set(), set()
    possible_rosters = [state.roster]
    for _ in range(MAX_UPGRADE_GROUPS):
        swaps = []
        for add in candidates:
            if add.id in used_adds:
                continue
            for drop in state.roster:
                if drop.id in used_drops:
                    continue
                gains = [_upgrade_gain(roster, add, drop.id, state.slots) for roster in possible_rosters]
                if all(g is not None for g in gains):
                    swaps.append((min(gains), add, drop))
        if not swaps:
            break
        swaps.sort(key=lambda s: (-s[0], s[1].rank, s[1].id, s[2].id))
        drop = swaps[0][2]
        alternatives = [s for s in swaps if s[2].id == drop.id][:ALTERNATIVES_PER_DROP]
        # Bid on roster improvement, not name recognition; one claim can win per drop.
        group_max_bid = 0
        branches = list(possible_rosters)  # include losing every claim in this group
        for gain, add, _ in alternatives:
            desired = round(state.budget * min(.30, .025 + .04 * gain))
            bid = min(spending_limit, max(1, desired)) if spending_limit else 0
            claims.append(WaiverClaim(player_to_add_id=add.id, player_to_drop_id=drop.id, bid_amount=bid))
            print("Waiver add %s, drop %s, bid %d, value gain %.2f" % (add.name, drop.name, bid, gain))
            group_max_bid = max(group_max_bid, bid)
            used_adds.add(add.id)
            branches.extend([p for p in roster if p.id != drop.id] + [add] for roster in possible_rosters)
        spending_limit -= group_max_bid
        used_drops.add(drop.id)
        possible_rosters = branches
    return claims


def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    db = DatabaseManager()
    try:
        return AttemptedFantasyActions(waiver_claims=_waiver_claims(Snapshot(db, weekly=True)))
    finally:
        db.close()
