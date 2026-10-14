"""haiku_bot: 2026 draft and weekly bot.

Draft: each pick takes the player who adds the most best-lineup points over
weeks 1-17. Weeks 1-5 are real scores, known on draft day. Weeks 6-17 use a
per-game rate: the preseason projection blended with this season's games.
Byes come from the schedule and injuries from the latest report. Open starting
slots are filled at replacement level, so a K or DST is not taken early just
because a slot is empty. Playoff weeks (15-17) count extra. At most 2 QB, 1 K
and 1 DST are drafted, and at least 1 K and 1 DST by the end. Missing K or DST
are forced in the last turns (MAX_DRAFTED, MIN_DRAFTED).

Weekly: claims use only weeks before the engine's current fantasy week, so
nothing from later weeks is used. Up to 10 one-for-one swaps, bid in proportion
to the gain, with about a third of the remaining FAAB held back until week 14.

Uses only blitz_env and the pandas, numpy and SQLAlchemy it depends on, all of
which the container ships with.
"""

import numpy as np
import pandas as pd
from sqlalchemy import text

from blitz_env import AttemptedFantasyActions, WaiverClaim, parse_positions
from blitz_env.models import DatabaseManager

SEASON_WEEKS = 17
PLAYOFF_WEEKS = (15, 16, 17)
PLAYOFF_WEIGHT = 1.5
REFERENCE_TEAMS = 14  # the replacement indexes below are tuned for this many teams
SCORING_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")
# Most players the bot drafts at each position. The value model alone can exceed these,
# since a third QB or a second K/DST still adds points when it beats the one already held.
MAX_DRAFTED = {"QB": 2, "K": 1, "DST": 1}
# Fewest players the bot drafts at each position. Enforced late: once the missing positions
# take every turn left, the bot has to fill them (see _must_draft).
MIN_DRAFTED = {"K": 1, "DST": 1}

# Weight on this season's per-game average vs. the preseason prior, by position.
# Copied from ben_bot.py, which fit them on 2025.
ACTUAL_WEIGHT = {"QB": 0.7, "RB": 0.6, "WR": 0.4, "TE": 0.4, "K": 0.3, "DST": 0.3}
# Per-game points for a player with no projection or history at all.
DEFAULT_RATE = {"QB": 12.0, "RB": 7.0, "WR": 7.0, "TE": 5.0, "K": 5.0, "DST": 4.0}
# Replacement level: the undrafted player at this index (by season average) at each
# position, for 14 teams. Flex and superflex slots are split across positions.
REPLACEMENT_INDEX = {"QB": 21, "RB": 35, "WR": 35, "TE": 10, "K": 14, "DST": 14}
# Starting slots that accept more than one position. Other slots take their own name.
MULTI_SLOTS = {"FLEX": ("RB", "WR", "TE"), "SUPERFLEX": ("QB", "RB", "WR", "TE")}

MAX_CLAIMS = 10
MIN_GAIN_PER_WEEK = 0.5  # points per remaining week a claim has to add
BID_PER_POINT = 4  # FAAB per point of weekly gain, before the caps
RESERVE_SHARE = 1 / 3  # FAAB held back for the late season
RESERVE_UNTIL_WEEK = 14
SURVIVAL_SPREAD = 4.0  # pick-number spread of the rank-to-availability curve


# --- ids and league shape ------------------------------------------------------

def _norm_id(value):
    """Player ids are text in `players` but can read back as int or float elsewhere."""
    if value is None or pd.isna(value):
        return None
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(value)


def _starting_slots(player_slots):
    """One eligibility tuple per starting slot, narrowest first. BENCH never scores."""
    slots = []
    for name, count in player_slots.items():
        if name != "BENCH":
            slots += [MULTI_SLOTS.get(name, (name,))] * int(count)
    return sorted(slots, key=len)


def _team_index(pick, num_teams):
    """0-based seat that owns an overall pick in a snake draft."""
    rnd, k = divmod(pick - 1, num_teams)
    return k if rnd % 2 == 0 else num_teams - 1 - k


def _next_pick_for(pick, num_teams, total_picks):
    """Overall pick number of our next turn after `pick`, or None if none is left."""
    mine = _team_index(pick, num_teams)
    for later in range(pick + 1, total_picks + 1):
        if _team_index(later, num_teams) == mine:
            return later
    return None


# --- lineup math ---------------------------------------------------------------

def _week_points(slots, entries):
    """Best starting lineup for one week. entries: (position, points) pairs. The highest
    scorer takes the narrowest open slot that fits it. The slot sets nest, so this is
    optimal. Players with no points add nothing, so they are benched."""
    open_slots = list(slots)
    total = 0.0
    for pos, pts in sorted(entries, key=lambda e: -e[1]):
        if pts <= 0:
            break
        for i, allowed in enumerate(open_slots):
            if pos in allowed:
                del open_slots[i]
                total += pts
                break
    return total


def _season_points(slots, rows, weeks, weights):
    """Weighted best-lineup points over `weeks` (0-based). rows: (position, per-week array)."""
    total = 0.0
    for w in weeks:
        total += weights[w] * _week_points(slots, [(pos, vals[w]) for pos, vals in rows])
    return total


def _week_weights():
    weights = np.ones(SEASON_WEEKS)
    for week in PLAYOFF_WEEKS:
        weights[week - 1] = PLAYOFF_WEIGHT
    return weights


def _ranked_by_position(pool, expected):
    """Player ids per position, best season average first."""
    season_mean = {pid: expected[pid].mean() for pid in pool["pid"]}
    ranked = {}
    for pos, group in pool.groupby("pos"):
        ranked[pos] = sorted(group["pid"], key=lambda pid: -season_mean[pid])
    return ranked


def _replacement_rows(slots, ranked, expected, num_teams):
    """Stand-ins for empty starting slots: one copy per slot the position can fill,
    valued at what the best undrafted player at that position would score (averaged
    over three neighbours to smooth byes and noise)."""
    rows = []
    for pos in SCORING_POSITIONS:
        copies = sum(pos in allowed for allowed in slots)
        if not copies:
            continue
        start = round(REPLACEMENT_INDEX[pos] * num_teams / REFERENCE_TEAMS)
        group = ranked.get(pos, [])[start:start + 3]
        value = np.mean([expected[pid] for pid in group], axis=0) if group else np.zeros(SEASON_WEEKS)
        rows += [(pos, value)] * copies
    return rows


# --- projections ---------------------------------------------------------------

def _load(db):
    """Everything the bot reads, pulled once per call."""
    settings = db.get_league_settings()
    status = db.get_game_status()
    year = int(settings.year)

    players = pd.read_sql(
        "SELECT id, professional_team, player_bye_week, rank, allowed_positions, "
        "availability, current_bot_id FROM players",
        db.engine,
    )
    players["pid"] = players["id"].map(_norm_id)
    players["pos"] = players["allowed_positions"].map(lambda v: (parse_positions(v) or [None])[0])
    players = players[players["pos"].isin(SCORING_POSITIONS) & players["pid"].notna()].copy()
    players = players.rename(columns={"availability": "status", "current_bot_id": "bot"})

    stats = pd.read_sql(
        f"SELECT fantasypros_id, year, week, team, FPTS FROM weekly_stats WHERE year IN ({year - 1}, {year})",
        db.engine,
    )
    stats["pid"] = stats["fantasypros_id"].map(_norm_id)
    stats["week"] = pd.to_numeric(stats["week"], errors="coerce")
    stats = stats[stats["week"].between(1, SEASON_WEEKS)].copy()
    stats["week"] = stats["week"].astype(int)
    stats["year"] = stats["year"].astype(int)
    this_year = stats[stats["year"] == year]
    last_year = stats[stats["year"] == year - 1]

    # Max per player-week, as the engine scores it (weekly_stats can repeat a row).
    grouped = this_year.groupby(["pid", "week"])["FPTS"].max()
    actual = {(pid, int(week)): float(pts) for (pid, week), pts in grouped.items() if pd.notna(pts)}
    team_weeks = set(zip(this_year["team"], this_year["week"]))
    last_played = int(this_year["week"].max()) if not this_year.empty else 0

    prev = last_year.groupby("pid")["FPTS"].agg(["mean", "count"])
    prev_pg = prev[prev["count"] >= 3]["mean"].to_dict()

    sched = pd.read_sql(f"SELECT team, week FROM schedule WHERE year = {year}", db.engine)
    team_games = {}
    for team, week in zip(sched["team"], sched["week"]):
        if team and pd.notna(week):
            team_games.setdefault(team, set()).add(int(week))

    ext = pd.read_sql(
        f"SELECT fantasypros_id, source, week, position, points FROM external_projections WHERE year = {year}",
        db.engine,
    )
    ext["pid"] = ext["fantasypros_id"].map(_norm_id)
    ext["week"] = pd.to_numeric(ext["week"], errors="coerce")
    # Week 0 is the preseason season total. FFToday's kicker totals leave out field goals.
    pre = ext[(ext["week"] == 0) & ~((ext["position"] == "K") & (ext["source"] == "FFToday"))]
    pre_pg = (pre.groupby("pid")["points"].mean() / SEASON_WEEKS).to_dict()

    inj = pd.read_sql(f"SELECT fantasypros_id, week, game_status FROM weekly_injuries WHERE year = {year}", db.engine)
    injuries = {}
    for pid, week, game_status in zip(inj["fantasypros_id"].map(_norm_id), inj["week"], inj["game_status"]):
        if pid and pd.notna(week):
            injuries.setdefault(pid, {})[int(week)] = game_status if isinstance(game_status, str) else "Healthy"

    return {
        "year": year,
        "num_teams": int(settings.num_teams or REFERENCE_TEAMS),
        "total_rounds": int(settings.total_rounds),
        "slots": dict(settings.player_slots),
        "pick": int(status.current_draft_pick or 1),
        "current_week": status.current_fantasy_week,
        "bot_id": status.current_bot_id,
        "last_played": last_played,
        "players": players,
        "actual": actual,
        "team_weeks": team_weeks,
        "team_games": team_games,
        "prev_pg": prev_pg,
        "pre_pg": pre_pg,
        "injuries": injuries,
    }


def _per_game_rate(pid, pos, played, data):
    """Per-game points: the preseason projection (or last season's average) blended
    with this season's games so far."""
    prior = data["pre_pg"].get(pid, data["prev_pg"].get(pid, DEFAULT_RATE[pos]))
    games = played.get(pid, [])
    if not games:
        return prior
    n = len(games)
    weight = ACTUAL_WEIGHT[pos] * n / (n + 1)
    return weight * (sum(games) / n) + (1 - weight) * prior


def _availability(reports, known_upto):
    """Share of future games a player is expected to play, from the latest reports."""
    now = reports.get(known_upto)
    before = reports.get(known_upto - 1)
    if now == "Out" and before == "Out":
        return 0.25
    if now == "Out":
        return 0.6
    if now is None and before == "Out":  # off the report after being out: likely IR
        return 0.4
    if now == "Doubtful":
        return 0.6
    if now == "Questionable":
        return 0.85
    return 1.0


def _is_bye(team, bye_week, week, team_games):
    if team in team_games:
        return week not in team_games[team]
    return bye_week is not None and pd.notna(bye_week) and int(bye_week) == week


def _known_points(pid, team, week, actual, team_weeks, team_games, rate):
    if (pid, week) in actual:
        return actual[(pid, week)]
    if (team, week) in team_weeks:  # the team reported that week, so this player sat out
        return 0.0
    if week in team_games.get(team, ()):  # a game on the schedule with no stats: data gap
        return rate
    return 0.0  # bye


def _on_nfl_team(team, team_games):
    """Unsigned players are listed as 'FA' and will not play, whatever their history says."""
    if team_games:
        return team in team_games
    return isinstance(team, str) and team not in ("", "FA")


def _projected_points(team, bye_week, week, data, rate, avail):
    # The blended rate, not FantasyPros' weekly projections: in a 2026 backtest those
    # ran about 2 points a week high on the players they cover, and were less accurate.
    if not _on_nfl_team(team, data["team_games"]) or _is_bye(team, bye_week, week, data["team_games"]):
        return 0.0
    return rate * avail


def _expected(data, known_upto):
    """Expected points per week (index 0 = week 1) for every scoring player. Weeks up to
    known_upto use real scores; later weeks are projections."""
    played = {}
    for (pid, week), pts in data["actual"].items():
        if week <= known_upto:
            played.setdefault(pid, []).append(pts)

    out = {}
    players = data["players"]
    for pid, team, bye_week, pos in zip(players["pid"], players["professional_team"],
                                        players["player_bye_week"], players["pos"]):
        rate = _per_game_rate(pid, pos, played, data)
        avail = _availability(data["injuries"].get(pid, {}), known_upto)
        vals = np.zeros(SEASON_WEEKS)
        for week in range(1, SEASON_WEEKS + 1):
            if week <= known_upto:
                vals[week - 1] = _known_points(pid, team, week, data["actual"], data["team_weeks"],
                                               data["team_games"], rate)
            else:
                vals[week - 1] = _projected_points(team, bye_week, week, data, rate, avail)
        out[pid] = vals
    return out


def _survival(rank, next_pick):
    """Rough chance a player is still on the board at our next pick, from his consensus rank."""
    r = 999.0 if rank is None or pd.isna(rank) else float(rank)
    return 1.0 / (1.0 + np.exp((next_pick - r) / SURVIVAL_SPREAD))


# --- draft ---------------------------------------------------------------------

def _at_draft_limit(drafted):
    """Positions the bot has drafted up to its MAX_DRAFTED limit."""
    taken = list(drafted)
    return {pos for pos, limit in MAX_DRAFTED.items() if taken.count(pos) >= limit}


def _must_draft(drafted, turns_left):
    """Positions still below MIN_DRAFTED that take every turn left. None means the turns are
    unknown, so nothing is forced."""
    if turns_left is None:
        return set()
    taken = list(drafted)
    short = {pos for pos, minimum in MIN_DRAFTED.items() if taken.count(pos) < minimum}
    return short if len(short) >= turns_left else set()


def _turns_remaining(pick, num_teams, total_rounds):
    """Our turns from `pick` to the end of the draft, counting the pick on the clock."""
    seat = _team_index(pick, num_teams)
    later = range(pick, num_teams * total_rounds + 1)
    return sum(1 for p in later if _team_index(p, num_teams) == seat)


def _draft_candidates(available, drafted, turns_left):
    """Who the bot may take now: the short positions once they take every turn left (if any are
    still available), otherwise anyone below MAX_DRAFTED."""
    must = _must_draft(drafted, turns_left)
    if must:
        forced = available[available["pos"].isin(must)]
        if not forced.empty:
            return forced
    return available[~available["pos"].isin(_at_draft_limit(drafted))]


def _draft_choice(db):
    data = _load(db)
    num_teams = data["num_teams"]
    slots = _starting_slots(data["slots"])
    expected = _expected(data, data["last_played"])
    players = data["players"]
    weights = _week_weights()
    every_week = list(range(SEASON_WEEKS))

    available = players[players["status"] == "AVAILABLE"]
    mine = players[(players["bot"] == data["bot_id"]) & (players["status"] == "DRAFTED")]
    roster = [(pos, expected[pid]) for pid, pos in zip(mine["pid"], mine["pos"])]
    fill = _replacement_rows(slots, _ranked_by_position(available, expected), expected, num_teams)
    base = _season_points(slots, roster + fill, every_week, weights)

    # Capped positions still count in `roster` and `fill`; only the candidates are cut.
    turns_left = _turns_remaining(data["pick"], num_teams, data["total_rounds"])
    candidates = _draft_candidates(available, mine["pos"], turns_left)
    gains = {}
    for pid, pos in zip(candidates["pid"], candidates["pos"]):
        gains[pid] = _season_points(slots, roster + fill + [(pos, expected[pid])], every_week, weights) - base

    next_pick = _next_pick_for(data["pick"], num_teams, num_teams * data["total_rounds"])
    if next_pick is None:
        return max(gains, key=gains.get)

    best = max(gains.values())
    band = [pid for pid, gain in gains.items() if gain >= best - max(0.5, 0.03 * abs(best))]
    ranks = dict(zip(available["pid"], available["rank"]))
    # Close calls go to the player least likely to last until our next turn.
    return min(band, key=lambda pid: (_survival(ranks.get(pid), next_pick), -gains[pid]))


def _fallback_pick(db):
    """Best available by consensus rank that the bot may take. K and DST go last unless forced."""
    players = pd.read_sql(
        "SELECT id, rank, allowed_positions, availability, current_bot_id FROM players",
        db.engine,
    )
    players["pos"] = players["allowed_positions"].map(lambda v: (parse_positions(v) or [None])[0])
    status = db.get_game_status()
    bot_id = status.current_bot_id
    mine = players[(players["current_bot_id"] == bot_id) & (players["availability"] == "DRAFTED")]
    try:
        settings = db.get_league_settings()
        num_teams = int(settings.num_teams or REFERENCE_TEAMS)
        pick = int(status.current_draft_pick or 1)
        turns_left = _turns_remaining(pick, num_teams, int(settings.total_rounds))
    except Exception:
        turns_left = None  # no league settings: skip the late rule rather than fail the pick
    free = players[players["availability"] == "AVAILABLE"]
    free = _draft_candidates(free, mine["pos"], turns_left)
    early = free[~free["pos"].isin(["K", "DST"])]
    pool = early if not early.empty else free
    if pool.empty:
        return ""
    return _norm_id(pool.sort_values("rank", na_position="last").iloc[0]["id"]) or ""


def draft_player() -> str:
    """Return the id of the player to draft. Falls back to best available by rank on any error."""
    db = DatabaseManager()
    try:
        return _draft_choice(db)
    except Exception:
        try:
            return _fallback_pick(db)
        except Exception:
            return ""
    finally:
        db.close()


# --- weekly --------------------------------------------------------------------

def _remaining_budget(db, bot_id):
    try:
        value = db.session.execute(
            text("SELECT remaining_waiver_budget FROM bots WHERE id = :id"), {"id": bot_id}
        ).scalar()
        return int(value or 0)
    except Exception:
        db.session.rollback()
        return 0


def _weekly_claims(db):
    data = _load(db)
    current_week = data["current_week"]
    bot_id = data["bot_id"]
    if not bot_id or not current_week or int(current_week) > SEASON_WEEKS:
        return []
    current_week = int(current_week)

    # No hindsight: only completed weeks before the current one are known.
    known_upto = min(current_week - 1, data["last_played"])
    expected = _expected(data, known_upto)
    slots = _starting_slots(data["slots"])
    weights = _week_weights()
    future = list(range(current_week - 1, SEASON_WEEKS))  # 0-based indexes of weeks cur..17

    players = data["players"]
    free = players[players["status"] == "AVAILABLE"]
    fill = _replacement_rows(slots, _ranked_by_position(free, expected), expected, data["num_teams"])
    roster = players[(players["bot"] == bot_id) & (players["status"] == "DRAFTED")]
    current = {pid: (pos, expected[pid]) for pid, pos in zip(roster["pid"], roster["pos"])}

    shortlist = []
    for pos in SCORING_POSITIONS:
        group = free[free["pos"] == pos]["pid"]
        ranked = sorted(group, key=lambda pid: -expected[pid][future].mean())
        shortlist += [(pid, pos) for pid in ranked[:20]]

    # Weeks already in the database are retro weeks: the bot can only see preseason-level
    # information there, so it bids 0 and keeps its FAAB for the live season.
    retro = current_week <= data["last_played"]
    budget = _remaining_budget(db, bot_id)
    reserve = int(budget * RESERVE_SHARE) if current_week < RESERVE_UNTIL_WEEK else 0
    spendable = 0 if retro else max(0, budget - reserve)
    spent = 0
    added = set()
    claims = []

    while len(claims) < MAX_CLAIMS and current:
        base = _season_points(slots, list(current.values()) + fill, future, weights)
        # Players added earlier in this run can't be dropped: the engine checks drops at submission.
        losses = {}
        for pid in current:
            if pid in added:
                continue
            rest = [val for key, val in current.items() if key != pid]
            losses[pid] = base - _season_points(slots, rest + fill, future, weights)
        cheapest_drops = sorted(losses, key=losses.get)[:3]

        best = None
        for fa, fa_pos in shortlist:
            if fa in current or fa in added:
                continue
            for drop in cheapest_drops:
                rest = [val for key, val in current.items() if key != drop]
                gain = _season_points(slots, rest + [(fa_pos, expected[fa])] + fill, future, weights) - base
                if best is None or gain > best[0]:
                    best = (gain, fa, fa_pos, drop)
        if best is None or best[0] < MIN_GAIN_PER_WEEK * len(future):
            break

        gain, fa, fa_pos, drop = best
        per_week = gain / len(future)
        bid = min(int(round(BID_PER_POINT * per_week)), int(spendable * 0.3), spendable - spent)
        bid = max(0, bid)
        spent += bid
        claims.append(WaiverClaim(player_to_add_id=fa, player_to_drop_id=drop, bid_amount=bid))

        del current[drop]
        current[fa] = (fa_pos, expected[fa])
        added.add(fa)

    return claims


def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    """Return this week's waiver claims. Returns no claims on any error."""
    db = DatabaseManager()
    try:
        claims = _weekly_claims(db)
    except Exception:
        claims = []
    finally:
        db.close()
    return AttemptedFantasyActions(waiver_claims=claims)
