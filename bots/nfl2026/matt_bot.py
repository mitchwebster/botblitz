"""
matt_bot 2026 -- best-ball marginal-value drafter with one-pick lookahead.

The draft happens after week 5 and is scored as best-ball: every week 1-17 the
engine builds our optimal lineup from the whole roster (bench included). So a
player's worth is how much they raise that season total given who we already have.

Player value, per week:
  - weeks already played: actual half-PPR points from weekly_stats
  - future weeks: Sleeper (Rotowire) + ESPN weekly projections embedded at the
    bottom of this file (real matchups, byes = 0), nudged toward the player's
    trimmed early-season points per game by W_EARLY
  - DO_NOT_DRAFT (out for season) are never picked

Pick decision:
  gain(c) = season_total(roster + c) - season_total(roster), lineups built the same
  way the engine does (players by points desc, each into the narrowest open slot).
  With LOOKAHEAD, score(c) = gain(c) + best gain at our next pick, where the pool at
  our next pick is the current pool minus the players we expect opponents to take
  in between. That's VONA: it reaches for a position before a cliff.
"""
import json
import time

import pandas as pd
from blitz_env import AttemptedFantasyActions
from blitz_env.models import DatabaseManager

# ---- feature flags / tuning ----
DEBUG = False          # print reasoning for each pick
LOOKAHEAD = True       # False = plain greedy on marginal season gain
PPR = 0.5              # points per reception
W_EARLY = 0.25         # weight on our trimmed early-season PPG vs expert projections
W_PRESEASON = 0.0      # weight on preseason PPG (external_projections week 0)
W_MARKET_OURS = 0.5    # opponent model: blend of our value rank vs players.rank (ADP)
VALUE_KICKERS = True   # weekly_stats K FPTS is 0; False = treat K as worthless (still fills the slot)
SEASON_WEEKS = 17
CANDIDATES_PER_POS = 10
NEXT_POOL_PER_POS = 8
TIME_BUDGET_S = 60

EXTERNAL_PROJ_FIRST_WEEK = 6
DEFERRABLE = ("K", "DST")  # one-slot, flat positions we fill as late as is safe
FLEX_ELIGIBILITY = {
    "FLEX": ("RB", "WR", "TE"),
    "SUPERFLEX": ("QB", "RB", "WR", "TE"),
}

# Out for season as of week 5 (fantasy-relevant ones that are in the draft pool).
DO_NOT_DRAFT = {
    "23136": "De'Von Achane",
    "23160": "Jaxson Dart",
    "23020": "Jayden Reed",
    "16399": "David Njoku",
    "22913": "Tyquan Thornton",
    "24205": "Malik Davis",
    "25388": "Trey Benson",
    "24778": "Jake Tonges",
    "27454": "Jaylin Lane",
    "17508": "Durham Smythe",
    "17826": "Tanner Hudson",
}


def log(*args):
    if DEBUG:
        print("[matt_bot]", *args, flush=True)


def _clean_id(x) -> str:
    s = str(x)
    return s[:-2] if s.endswith(".0") else s


# ---------------------------------------------------------------------------
# Lineup scoring (mirrors pkg/engine/EndOfWeekHandler.go scoreTeam)
# ---------------------------------------------------------------------------

def build_slots(player_slots: dict) -> list:
    """Starting slots as tuples of eligible positions, narrowest first."""
    slots = []
    for name, count in player_slots.items():
        if name == "BENCH":
            continue
        slots.extend([FLEX_ELIGIBILITY.get(name, (name,))] * int(count))
    slots.sort(key=len)
    return slots


def week_score(slots: list, entries: list) -> float:
    """entries: [(points, pos)]. Highest points first, each into the first open
    eligible slot."""
    open_slots = list(slots)
    total = 0.0
    for pts, pos in sorted(entries, key=lambda e: e[0], reverse=True):
        for i, elig in enumerate(open_slots):
            if pos in elig:
                total += pts
                del open_slots[i]
                break
        if not open_slots:
            break
    return total


def season_score(slots: list, roster: list) -> float:
    """roster: [(pos, weekly_points_list)]"""
    total = 0.0
    for w in range(SEASON_WEEKS):
        total += week_score(slots, [(vals[w], pos) for pos, vals in roster])
    return total


# ---------------------------------------------------------------------------
# Player values
# ---------------------------------------------------------------------------

def load_actuals(db, year: int):
    """{pid: {week: half_ppr_points}} for played weeks, and the last played week."""
    df = pd.read_sql(
        "SELECT fantasypros_id, CAST(week AS INTEGER) AS week, position, "
        "fantasy_points, receptions, FPTS, FG, XPT "
        f"FROM weekly_stats WHERE year = {int(year)} AND fantasypros_id IS NOT NULL",
        db.engine,
    )
    actuals = {}
    last_week = 0
    for r in df.itertuples(index=False):
        week = int(r.week)
        if week < 1 or week > SEASON_WEEKS:
            continue
        last_week = max(last_week, week)
        if r.position == "K":
            # Kicker FPTS is 0 in the data; compute a simple 3/FG + 1/XP so K values
            # line up with the K projections.
            pts = r.FPTS if r.FPTS else 3.0 * (r.FG or 0) + (r.XPT or 0)
        elif pd.notna(r.fantasy_points):
            pts = r.fantasy_points + PPR * (r.receptions or 0)
        else:
            pts = r.FPTS or 0.0
        pid = _clean_id(r.fantasypros_id)
        prev = actuals.setdefault(pid, {}).get(week)
        actuals[pid][week] = pts if prev is None else max(prev, pts)
    return actuals, last_week


def load_db_projections(db, year: int, last_week: int):
    """Fallback rates from external_projections: per-game rate from the most recent
    weekly projections, and preseason PPG from week 0."""
    try:
        df = pd.read_sql(
            "SELECT fantasypros_id, week, source, points FROM external_projections "
            f"WHERE year = {int(year)}",
            db.engine,
        )
    except Exception:
        return {}, {}
    df["fantasypros_id"] = df["fantasypros_id"].map(_clean_id)
    pre = df[df["week"] == 0].groupby("fantasypros_id")["points"].mean() / SEASON_WEEKS
    recent = df[(df["week"] >= max(1, last_week - 1)) & (df["points"] > 0)]
    rate = recent.groupby("fantasypros_id")["points"].mean()
    return rate.to_dict(), pre.to_dict()


def trimmed_ppg(weeks: dict):
    """PPG over games played with the best game dropped; None if < 3 games."""
    pts = sorted(weeks.values())
    if len(pts) < 3:
        return None
    pts = pts[:-1]
    return sum(pts) / len(pts)


def build_values(players: pd.DataFrame, actuals: dict, last_week: int,
                 db_rate: dict, db_pre: dict) -> dict:
    """{pid: [points week1..week17]}"""
    values = {}
    for p in players.itertuples(index=False):
        pid = p.id
        played = actuals.get(pid, {})
        early = trimmed_ppg(played)
        pre = db_pre.get(pid)
        ext = EXTERNAL_WEEKLY_PROJ.get(pid)

        # expert weekly projections for future weeks
        future = {}
        for w in range(last_week + 1, SEASON_WEEKS + 1):
            v = None
            if ext is not None and w >= EXTERNAL_PROJ_FIRST_WEEK:
                idx = w - EXTERNAL_PROJ_FIRST_WEEK
                if idx < len(ext):
                    v = ext[idx]
            if v is None:
                if w == p.bye:
                    v = 0.0
                elif pid in db_rate:
                    v = db_rate[pid]
                elif ext is None and early is not None:
                    v = early
                else:
                    v = 0.0
            future[w] = v

        playing = [v for v in future.values() if v > 0]
        if playing:
            mean = sum(playing) / len(playing)
            shift = 0.0
            if early is not None:
                shift += W_EARLY * (early - mean)
            if pre is not None and W_PRESEASON:
                shift += W_PRESEASON * (pre - mean)
            for w, v in future.items():
                if v > 0:
                    future[w] = max(0.0, v + shift)

        if p.pos == "K" and not VALUE_KICKERS:
            values[pid] = [0.0] * SEASON_WEEKS
            continue

        weekly = []
        for w in range(1, SEASON_WEEKS + 1):
            weekly.append(played.get(w, 0.0) if w <= last_week else future[w])
        values[pid] = weekly
    return values


# ---------------------------------------------------------------------------
# Draft state
# ---------------------------------------------------------------------------

def my_pick_numbers(num_teams: int, my_index: int, total_rounds: int, snake: bool) -> list:
    picks = []
    for r in range(total_rounds):
        pos = num_teams - 1 - my_index if (snake and r % 2 == 1) else my_index
        picks.append(r * num_teams + pos + 1)
    return picks


def unfilled_required(slots_cfg: dict, roster_pos: list) -> dict:
    """Single-position starting slots we can't fill yet, e.g. {'K': 1, 'RB': 1}."""
    need = {}
    for name, count in slots_cfg.items():
        if name == "BENCH" or name in FLEX_ELIGIBILITY:
            continue
        short = int(count) - roster_pos.count(name)
        if short > 0:
            need[name] = short
    return need


def market_order(pool: pd.DataFrame) -> list:
    """Opponent model: available ids in the order we expect them to go. Blend of our
    value order and the site's rank (ADP). pool must be sorted by value desc."""
    mkt = pool[["id", "rank"]].copy()
    mkt["r_ours"] = range(len(mkt))
    mkt["r_adp"] = mkt["rank"].rank(method="first", na_option="bottom")
    mkt["m"] = W_MARKET_OURS * mkt["r_ours"] + (1 - W_MARKET_OURS) * mkt["r_adp"]
    return list(mkt.sort_values("m")["id"])


def positions_to_defer(need, remaining_picks, current_pick, pool, order, gain_of,
                       pos_of, other_counts, slots_cfg) -> set:
    """For K/DST we still need: compare taking one now and a skill player at our
    deadline pick vs the reverse. The deadline is the latest pick we can fill the
    remaining K/DST slots with. Expected K/DST taken by others before then comes from
    the draft so far: their share of picks at that position (so a run raises it),
    capped by how many teams still need one; best ones go first. The rest of their
    picks before our deadline are skill players in market order.

    Take K now iff  K_now - K_deadline  >  S_now - S_deadline."""
    defer_need = {p: n for p, n in need.items() if p in DEFERRABLE}
    n_defer = sum(defer_need.values())
    if not defer_need or len(remaining_picks) <= n_defer:
        return set()
    deadline = remaining_picks[-n_defer]
    ours_between = sum(1 for p in remaining_picks if current_pick < p < deadline)
    opp_picks = deadline - current_pick - 1 - ours_between

    n_other_picks = sum(sum(c.values()) for c in other_counts)
    taken = {}
    for pos in defer_need:
        demand = sum(1 for c in other_counts if c.get(pos, 0) < int(slots_cfg.get(pos, 1)))
        rate = sum(c.get(pos, 0) for c in other_counts) / n_other_picks if n_other_picks else 0.0
        taken[pos] = min(demand, opp_picks, round(opp_picks * rate))

    skill = pool[~pool["pos"].isin(DEFERRABLE)]
    top_skill = list(skill.groupby("pos", sort=False).head(NEXT_POOL_PER_POS)["id"])
    s_now = max((gain_of(pid) for pid in top_skill), default=0.0)
    n_skill_gone = max(0, opp_picks - sum(taken.values()))
    gone = []
    for pid in order:
        if len(gone) >= n_skill_gone:
            break
        if pos_of[pid] not in DEFERRABLE:
            gone.append(pid)
    gone = set(gone)
    left = skill[~skill["id"].isin(gone)]
    s_later = max((gain_of(pid) for pid in left.groupby("pos", sort=False)
                   .head(NEXT_POOL_PER_POS)["id"]), default=0.0)
    skill_wait = s_now - s_later

    defer = set()
    for pos in defer_need:
        ids = list(pool.loc[pool["pos"] == pos, "id"])
        now = max((gain_of(pid) for pid in ids[:3]), default=0.0)
        later = max((gain_of(pid) for pid in ids[taken[pos]:taken[pos] + 3]), default=0.0)
        if now - later <= skill_wait:
            defer.add(pos)
        log(f"  defer check {pos}: wait cost {now - later:.1f} (now {now:.1f}, "
            f"deadline pick {deadline} {later:.1f}, {taken[pos]} taken) vs skill "
            f"{skill_wait:.1f} -> {'defer' if pos in defer else 'consider now'}")
    return defer


def draft_player() -> str:
    db = DatabaseManager()
    try:
        return _draft(db)
    except Exception as e:
        log("draft failed, falling back:", repr(e))
        try:
            return _fallback_pick(db)
        except Exception as e2:
            log("fallback failed:", repr(e2))
            return ""
    finally:
        db.close()


def _fallback_pick(db) -> str:
    df = pd.read_sql(
        "SELECT id FROM players WHERE availability = 'AVAILABLE' ORDER BY rank", db.engine
    )
    for pid in df["id"].map(_clean_id):
        if pid not in DO_NOT_DRAFT:
            return pid
    return df["id"].iloc[0] if not df.empty else ""


def _draft(db) -> str:
    t0 = time.time()
    settings = db.get_league_settings()
    status = db.get_game_status()
    year = settings.year
    slots_cfg = settings.player_slots
    if isinstance(slots_cfg, str):
        slots_cfg = json.loads(slots_cfg)
    slots = build_slots(slots_cfg)

    bots = sorted(db.get_all_bots(), key=lambda b: b.draft_order)
    num_teams = len(bots) or settings.num_teams
    my_id = str(status.current_bot_id)
    my_index = next(i for i, b in enumerate(bots) if str(b.id) == my_id)
    current_pick = int(status.current_draft_pick)
    total_rounds = int(settings.total_rounds)
    snake = settings.is_snake_draft if settings.is_snake_draft is not None else True
    my_picks = my_pick_numbers(num_teams, my_index, total_rounds, snake)
    later = [p for p in my_picks if p > current_pick]
    next_pick = later[0] if later else None
    picks_left = len(later) + 1

    players = pd.read_sql(
        "SELECT id, full_name, allowed_positions, player_bye_week AS bye, rank, "
        "availability, current_bot_id FROM players",
        db.engine,
    )
    players["id"] = players["id"].map(_clean_id)
    players["pos"] = players["allowed_positions"].map(
        lambda x: (json.loads(x) if isinstance(x, str) else list(x))[0]
    )

    actuals, last_week = load_actuals(db, year)
    db_rate, db_pre = load_db_projections(db, year, last_week)
    values = build_values(players, actuals, last_week, db_rate, db_pre)
    players["value"] = players["id"].map(lambda pid: sum(values[pid]))

    mine = players[players["current_bot_id"].astype(str) == my_id]
    roster = [(r.pos, values[r.id]) for r in mine.itertuples(index=False)]
    base = season_score(slots, roster)

    pool = players[(players["availability"] == "AVAILABLE") & (~players["id"].isin(DO_NOT_DRAFT))]
    pool = pool.sort_values("value", ascending=False)
    names = dict(zip(players["id"], players["full_name"]))
    pos_of = dict(zip(players["id"], players["pos"]))

    # Must-fill guard: if remaining picks only just cover empty single-position
    # slots, only consider those positions.
    def gain_of(pid):
        return season_score(slots, roster + [(pos_of[pid], values[pid])]) - base

    order = market_order(pool)
    need = unfilled_required(slots_cfg, list(mine["pos"]))
    cand_pool = pool
    deferred = set()
    if need and picks_left <= sum(need.values()):
        cand_pool = pool[pool["pos"].isin(need)]
        log(f"must fill {need} with {picks_left} picks left")
    else:
        drafted = players[players["current_bot_id"].notna()]
        other_counts = [
            drafted.loc[drafted["current_bot_id"].astype(str) == str(b.id), "pos"]
            .value_counts().to_dict()
            for b in bots if str(b.id) != my_id
        ]
        deferred = positions_to_defer(
            need, [current_pick] + later, current_pick, pool, order, gain_of,
            pos_of, other_counts, slots_cfg,
        )
        cand_pool = pool[~pool["pos"].isin(deferred)]

    candidates = list(cand_pool.groupby("pos", sort=False).head(CANDIDATES_PER_POS)["id"])
    gains = {c: gain_of(c) for c in candidates}
    scores = dict(gains)

    if LOOKAHEAD and next_pick is not None:
        k = next_pick - current_pick - 1
        market = order
        by_pos = {pos: list(g["id"]) for pos, g in pool.groupby("pos", sort=False)
                  if pos not in deferred}

        # Evaluate in greedy-gain order; if the time budget runs out, only compare
        # the candidates that got a lookahead score.
        evaluated = {}
        for c in sorted(candidates, key=lambda x: gains[x], reverse=True):
            if evaluated and time.time() - t0 > TIME_BUDGET_S:
                log("time budget hit; skipping remaining candidates")
                break
            gone = set()
            for pid in market:
                if len(gone) >= k:
                    break
                if pid != c:
                    gone.add(pid)
            roster_c = roster + [(pos_of[c], values[c])]
            base_c = base + gains[c]
            best_next, best_next_id = 0.0, None
            for pos, ids in by_pos.items():
                n = 0
                for pid in ids:
                    if pid == c or pid in gone:
                        continue
                    g = season_score(slots, roster_c + [(pos, values[pid])]) - base_c
                    if g > best_next:
                        best_next, best_next_id = g, pid
                    n += 1
                    if n >= NEXT_POOL_PER_POS:
                        break
            evaluated[c] = gains[c] + best_next
            log(f"  {names[c]:<25} {pos_of[c]:<3} gain={gains[c]:6.1f} "
                f"next={best_next:6.1f} ({names.get(best_next_id, '-')})")
        scores = evaluated

    pick = max(scores, key=scores.get)
    log(f"pick {current_pick} (next {next_pick}, {picks_left} left): {names[pick]} "
        f"{pos_of[pick]} score={scores[pick]:.1f} gain={gains[pick]:.1f} "
        f"value={sum(values[pick]):.1f} [{time.time() - t0:.1f}s]")
    return pick


def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    return AttemptedFantasyActions(waiver_claims=[])


# ---------------------------------------------------------------------------
# Embedded data
# ---------------------------------------------------------------------------
# fantasypros_id -> [wk6..wk17] half-PPR, avg of Sleeper(Rotowire) + ESPN, pulled 2026-10-14
EXTERNAL_WEEKLY_PROJ = {
    '17298': [25.0, 0.0, 23.7, 20.6, 23.6, 26.4, 22.6, 22.9, 24.2, 24.6, 22.3, 26.0],  # Josh Allen
    '22968': [0.0, 23.9, 20.9, 24.7, 22.8, 22.4, 24.4, 22.1, 24.0, 20.4, 24.8, 23.9],  # Jahmyr Gibbs
    '23021': [20.6, 17.3, 19.1, 20.1, 18.9, 21.7, 20.0, 18.6, 20.7, 19.7, 18.4, 19.6],  # Kenneth Walker III
    '17233': [20.1, 22.1, 22.6, 20.0, 21.7, 21.6, 20.9, 0.0, 21.3, 21.3, 21.2, 21.8],  # Lamar Jackson
    '19797': [21.5, 20.4, 0.0, 23.0, 23.8, 18.8, 19.8, 21.7, 20.4, 21.2, 19.9, 20.8],  # Brock Purdy
    '23133': [22.8, 19.3, 19.4, 22.0, 19.4, 0.0, 18.0, 22.9, 20.4, 21.1, 19.9, 22.3],  # Bijan Robinson
    '16413': [19.0, 16.9, 17.5, 19.2, 17.9, 21.9, 20.0, 17.5, 18.8, 18.4, 17.9, 18.7],  # Patrick Mahomes II
    '23046': [20.5, 20.3, 22.4, 21.0, 22.9, 0.0, 19.9, 21.8, 17.5, 18.4, 19.9, 19.1],  # Drake Maye
    '19780': [17.8, 0.0, 20.8, 19.2, 20.0, 19.9, 20.4, 19.9, 19.9, 19.0, 22.1, 21.9],  # Trevor Lawrence
    '22900': [17.5, 18.7, 19.2, 17.7, 19.4, 18.8, 18.3, 15.7, 19.9, 19.1, 18.6, 17.1],  # Bryce Young
    '19196': [0.0, 18.7, 20.0, 18.3, 19.4, 21.1, 20.2, 18.3, 18.0, 18.8, 19.9, 19.2],  # Joe Burrow
    '22902': [18.3, 0.0, 19.0, 18.7, 20.2, 20.3, 22.4, 19.9, 19.3, 19.1, 16.4, 17.2],  # Jayden Daniels
    '16393': [19.3, 18.9, 0.0, 20.6, 20.8, 17.2, 17.4, 20.1, 18.6, 19.0, 17.7, 18.6],  # Christian McCaffrey
    '15501': [0.0, 19.5, 16.4, 20.9, 18.1, 18.8, 19.3, 17.7, 19.4, 16.1, 19.2, 19.0],  # Jared Goff
    '19246': [21.0, 20.2, 17.8, 16.7, 14.9, 0.0, 15.9, 18.3, 19.3, 20.5, 17.7, 17.4],  # Jordan Love
    '23084': [17.1, 17.2, 15.7, 17.6, 0.0, 18.5, 20.0, 16.2, 19.8, 18.7, 18.4, 20.4],  # Caleb Williams
    '15514': [17.8, 19.6, 18.7, 16.2, 18.7, 19.2, 16.8, 0.0, 17.5, 17.2, 18.8, 18.9],  # Derrick Henry
    '23070': [17.2, 17.4, 18.5, 19.4, 18.4, 0.0, 17.3, 19.7, 18.2, 17.4, 17.4, 17.7],  # Jaxon Smith-Njigba
    '15600': [17.9, 17.1, 21.0, 18.6, 17.1, 18.7, 17.3, 16.2, 0.0, 16.7, 16.7, 18.5],  # Dak Prescott
    '19275': [16.6, 20.0, 18.7, 17.9, 0.0, 17.2, 19.5, 19.7, 18.3, 15.2, 16.7, 15.4],  # Jalen Hurts
    '19217': [19.4, 15.0, 15.0, 19.7, 19.4, 16.2, 19.3, 0.0, 16.4, 17.6, 19.1, 17.3],  # Jonathan Taylor
    '23180': [19.1, 17.7, 17.1, 18.3, 18.5, 0.0, 17.6, 16.6, 16.4, 19.0, 16.0, 17.1],  # Puka Nacua
    '9451': [20.7, 17.8, 16.8, 18.4, 19.4, 0.0, 17.3, 15.6, 15.3, 19.3, 14.7, 16.3],  # Matthew Stafford
    '23059': [19.4, 18.0, 17.3, 17.3, 18.1, 0.0, 17.7, 16.1, 15.5, 19.0, 14.9, 16.0],  # Kyren Williams
    '23096': [16.9, 17.0, 0.0, 16.6, 17.0, 17.2, 16.8, 17.8, 16.6, 16.6, 20.2, 15.8],  # Tyler Shough
    '18600': [0.0, 17.4, 18.7, 17.7, 16.5, 14.8, 15.6, 16.5, 15.3, 19.1, 18.3, 16.0],  # Kyler Murray
    '22958': [17.3, 0.0, 16.1, 14.6, 16.4, 18.2, 15.5, 16.1, 16.7, 17.6, 16.2, 17.7],  # James Cook III
    '19788': [0.0, 16.2, 16.9, 16.0, 16.6, 17.3, 16.8, 15.7, 15.8, 16.0, 16.7, 16.4],  # Ja'Marr Chase
    '22910': [13.7, 18.7, 14.7, 15.5, 0.0, 17.6, 15.6, 18.7, 15.7, 17.2, 17.4, 14.9],  # Bo Nix
    '22739': [15.6, 15.6, 18.0, 17.5, 15.4, 17.2, 16.1, 14.4, 0.0, 15.6, 14.9, 18.1],  # Javonte Williams
    '15642': [14.9, 15.1, 18.8, 13.9, 15.1, 14.5, 18.7, 15.7, 0.0, 16.6, 16.9, 18.1],  # Jacoby Brissett
    '19799': [0.0, 16.5, 14.7, 17.4, 15.8, 16.2, 16.7, 15.7, 16.6, 14.6, 16.3, 16.6],  # Amon-Ra St. Brown
    '23071': [14.5, 16.4, 0.0, 15.7, 15.0, 16.8, 15.8, 15.7, 17.6, 14.4, 14.9, 16.2],  # C.J. Stroud
    '9001': [15.4, 16.9, 15.5, 0.0, 15.5, 15.0, 14.9, 15.7, 14.0, 16.0, 16.0, 16.3],  # Aaron Rodgers
    '19202': [15.7, 15.0, 16.8, 15.9, 15.1, 16.0, 15.1, 14.7, 0.0, 14.9, 15.3, 15.7],  # CeeDee Lamb
    '17236': [13.9, 14.2, 15.8, 18.2, 16.4, 0.0, 13.9, 17.9, 15.8, 14.3, 14.2, 14.9],  # Sam Darnold
    '16398': [15.0, 15.5, 14.7, 16.0, 14.8, 0.0, 10.4, 16.1, 16.5, 16.2, 15.3, 18.8],  # Deshaun Watson
    '19792': [14.2, 14.0, 14.7, 14.3, 15.1, 13.8, 13.3, 12.4, 15.5, 15.6, 13.6, 12.7],  # Chuba Hubbard
    '22916': [14.1, 15.6, 15.6, 15.0, 15.2, 15.0, 15.2, 0.0, 15.3, 15.5, 14.8, 15.5],  # Zay Flowers
    '23499': [0.0, 14.4, 14.1, 16.9, 15.1, 15.3, 14.7, 13.3, 15.1, 14.8, 14.7, 15.7],  # Malik Willis
    '11687': [13.4, 16.7, 15.8, 12.8, 15.6, 14.1, 16.3, 0.0, 13.2, 16.8, 13.8, 11.9],  # Geno Smith
    '25989': [14.6, 14.5, 14.8, 13.6, 13.3, 14.1, 14.5, 0.0, 15.0, 14.5, 15.4, 15.8],  # Ashton Jeanty
    '19236': [0.0, 14.7, 15.3, 14.6, 14.4, 13.6, 13.9, 14.0, 13.8, 15.5, 15.3, 14.0],  # Justin Jefferson
    '12119': [14.2, 14.5, 0.0, 14.9, 15.8, 13.3, 13.7, 14.6, 14.0, 14.4, 14.0, 14.1],  # Mike Evans
    '22973': [15.6, 13.4, 13.9, 14.2, 13.3, 0.0, 11.9, 16.5, 13.5, 15.7, 14.2, 14.8],  # Michael Penix Jr.
    '20130': [13.9, 14.3, 0.0, 14.0, 13.6, 14.6, 14.2, 14.3, 15.1, 13.9, 13.6, 14.4],  # Nico Collins
    '24755': [14.1, 13.1, 13.5, 0.0, 12.2, 15.8, 11.8, 15.7, 15.8, 14.4, 14.7, 13.8],  # Cam Ward
    '20111': [14.1, 14.5, 0.0, 13.5, 13.7, 14.3, 14.1, 14.2, 13.6, 14.0, 15.1, 13.5],  # Chris Olave
    '25417': [12.2, 13.1, 13.2, 12.2, 13.3, 13.0, 13.0, 11.6, 13.4, 13.3, 13.1, 12.1],  # Tetairoa McMillan
    '18635': [12.3, 0.0, 12.8, 14.0, 13.8, 14.5, 13.8, 13.7, 15.5, 13.2, 16.4, 13.1],  # Justin Herbert
    '25981': [13.7, 14.4, 13.3, 14.2, 0.0, 14.9, 14.9, 13.9, 12.7, 14.9, 12.9, 13.1],  # Bucky Irving
    '22955': [14.9, 13.7, 14.2, 13.6, 13.4, 13.0, 13.4, 0.0, 14.0, 13.1, 14.5, 15.1],  # Brock Bowers
    '24209': [13.8, 14.6, 14.0, 0.0, 14.6, 12.9, 13.6, 13.3, 12.1, 13.3, 14.8, 14.0],  # Jaylen Warren
    '25324': [0.0, 12.9, 14.2, 13.1, 13.5, 13.9, 14.6, 13.5, 12.7, 14.3, 14.4, 13.2],  # Chase Brown
    '23106': [12.6, 0.0, 13.8, 13.3, 13.7, 13.4, 13.8, 13.7, 13.6, 13.3, 14.6, 14.3],  # Parker Washington
    '23163': [14.5, 13.1, 13.6, 13.8, 13.1, 0.0, 12.3, 14.7, 13.0, 14.5, 13.7, 13.8],  # Drake London
    '12123': [14.3, 13.7, 13.3, 14.3, 14.4, 0.0, 13.6, 12.9, 12.8, 14.7, 12.5, 13.4],  # Davante Adams
    '22936': [13.1, 12.8, 15.2, 12.6, 13.0, 12.9, 14.9, 13.3, 0.0, 13.7, 14.0, 14.3],  # Trey McBride
    '23886': [15.0, 14.3, 13.2, 13.0, 12.1, 0.0, 12.7, 13.6, 13.7, 14.5, 13.6, 13.3],  # Christian Watson
    '19210': [13.2, 13.4, 11.7, 12.9, 0.0, 14.4, 14.4, 12.1, 14.3, 13.6, 13.9, 14.8],  # D'Andre Swift
    '23113': [11.9, 11.4, 11.4, 12.2, 11.9, 13.2, 12.4, 11.6, 12.4, 12.0, 11.8, 12.0],  # Rashee Rice
    '17237': [0.0, 14.0, 13.7, 14.6, 0.0, 16.6, 14.3, 14.5, 13.9, 15.1, 13.3, 13.3],  # Baker Mayfield
    '18232': [14.1, 10.2, 11.1, 15.3, 15.2, 12.3, 13.4, 0.0, 11.9, 13.2, 13.1, 12.1],  # Daniel Jones
    '27297': [11.6, 0.0, 13.9, 12.0, 12.9, 13.7, 13.2, 13.2, 12.5, 11.8, 13.8, 13.2],  # Bhayshul Tuten
    '25984': [0.0, 12.0, 12.3, 13.7, 13.1, 12.5, 12.4, 11.9, 13.1, 12.4, 12.5, 12.9],  # Ollie Gordon II
    '26403': [11.0, 11.7, 11.8, 11.0, 11.9, 11.7, 11.6, 10.5, 12.0, 11.9, 11.7, 11.0],  # Jalen Coker
    '23072': [12.1, 13.7, 12.9, 11.8, 12.8, 12.3, 13.6, 0.0, 11.6, 13.5, 12.2, 11.2],  # Garrett Wilson
    '19211': [0.0, 12.3, 12.8, 12.1, 12.6, 13.2, 12.7, 11.9, 11.9, 12.1, 12.7, 12.4],  # Tee Higgins
    '17240': [11.5, 13.8, 12.7, 13.6, 0.0, 11.9, 13.3, 13.0, 13.4, 10.7, 11.7, 10.8],  # Saquon Barkley
    '25409': [12.9, 12.0, 0.0, 11.5, 13.4, 11.7, 12.6, 11.5, 10.9, 11.7, 13.4, 13.7],  # Malik Nabers
    '16499': [12.3, 12.3, 0.0, 12.6, 13.3, 11.3, 11.7, 12.3, 11.9, 12.2, 11.9, 12.0],  # George Kittle
    '25333': [11.5, 11.3, 13.6, 11.0, 11.5, 11.4, 13.3, 11.8, 0.0, 12.2, 12.4, 12.7],  # Michael Wilson
    '18218': [5.0, 13.3, 13.7, 12.9, 13.7, 0.0, 12.5, 13.1, 11.5, 12.1, 12.5, 11.9],  # A.J. Brown
    '11177': [18.0, 15.4, 16.3, 14.9, 14.8, 7.8, 8.3, 0.0, 8.7, 7.8, 9.0, 10.3],  # Kirk Cousins
    '22982': [0.0, 14.2, 14.1, 11.5, 13.5, 12.7, 13.8, 0.0, 12.7, 13.7, 12.8, 11.3],  # Breece Hall
    '26024': [13.2, 12.6, 11.4, 11.2, 10.3, 0.0, 10.8, 11.8, 11.9, 12.9, 11.9, 11.6],  # Matthew Golden
    '22726': [15.4, 13.2, 11.9, 11.5, 12.1, 0.0, 11.0, 11.5, 10.0, 10.2, 10.9, 11.0],  # Rhamondre Stevenson
    '17265': [11.9, 0.0, 11.6, 10.5, 11.6, 12.6, 11.2, 11.3, 11.8, 12.0, 10.9, 12.5],  # DJ Moore
    '19222': [10.5, 12.4, 11.9, 11.2, 0.0, 11.3, 12.3, 12.1, 11.5, 10.3, 11.1, 10.5],  # DeVonta Smith
    '25391': [10.0, 11.2, 10.4, 11.9, 10.5, 0.0, 12.4, 12.3, 10.8, 12.0, 10.3, 12.4],  # Quinshon Judkins
    '18705': [11.5, 11.1, 11.4, 0.0, 9.2, 11.8, 8.9, 11.3, 11.7, 11.9, 11.6, 10.5],  # Tony Pollard
    '22963': [11.1, 10.5, 11.8, 11.2, 10.5, 11.3, 10.6, 10.2, 0.0, 10.4, 10.7, 11.0],  # George Pickens
    '27166': [12.2, 10.0, 0.0, 10.0, 11.3, 9.4, 11.5, 9.8, 9.0, 11.0, 11.8, 11.9],  # Cam Skattebo
    '23781': [11.3, 10.3, 0.0, 10.0, 11.3, 10.1, 10.7, 9.9, 9.5, 10.0, 11.3, 11.6],  # Isaiah Likely
    '26009': [10.4, 9.8, 10.5, 0.0, 10.0, 11.4, 9.9, 11.2, 11.1, 10.5, 10.5, 10.5],  # Carnell Tate
    '11594': [9.5, 9.1, 9.1, 9.8, 9.5, 10.5, 9.9, 9.3, 9.9, 9.6, 9.4, 9.7],  # Travis Kelce
    '25403': [9.7, 10.6, 11.6, 8.9, 10.2, 9.4, 11.0, 10.2, 0.0, 10.7, 11.2, 11.7],  # Jeremiyah Love
    '26341': [0.0, 10.4, 10.2, 10.4, 10.0, 9.9, 10.2, 10.3, 9.9, 10.5, 10.3, 9.9],  # Will Reichard
    '26763': [10.7, 9.7, 9.8, 10.5, 10.5, 9.9, 10.3, 0.0, 10.0, 10.1, 10.2, 10.0],  # Spencer Shrader
    '22978': [0.0, 10.4, 9.3, 11.0, 9.9, 10.3, 10.5, 9.9, 10.5, 9.2, 10.2, 10.5],  # Sam LaPorta
    '27016': [9.8, 9.7, 9.2, 10.0, 0.0, 10.2, 10.8, 9.7, 10.8, 10.1, 10.2, 10.8],  # Luther Burden III
    '18239': [0.0, 12.1, 0.0, 10.7, 10.7, 12.0, 10.5, 10.2, 11.0, 9.5, 10.1, 11.0],  # David Montgomery
    '15756': [9.5, 10.0, 0.0, 9.7, 9.7, 10.2, 9.9, 9.6, 9.9, 9.7, 9.7, 9.8],  # Ka'imi Fairbairn
    '13891': [15.6, 12.8, 0.0, 12.6, 15.8, 12.1, 6.3, 5.7, 5.5, 5.9, 7.6, 7.4],  # Jameis Winston
    '18466': [9.5, 0.0, 9.5, 9.4, 9.9, 10.1, 10.7, 10.2, 9.9, 9.6, 8.8, 9.5],  # Terry McLaurin
    '23152': [0.0, 8.2, 11.1, 11.8, 11.2, 0.0, 9.4, 11.9, 11.5, 10.0, 10.2, 10.9],  # Zach Charbonnet
    '19790': [8.5, 10.4, 9.0, 9.2, 0.0, 9.8, 9.6, 10.6, 9.4, 9.7, 9.7, 9.1],  # Jaylen Waddle
    '26068': [9.5, 9.4, 9.9, 9.6, 9.5, 9.7, 9.6, 9.2, 0.0, 9.4, 9.4, 9.7],  # Brandon Aubrey
    '27339': [9.3, 9.7, 9.4, 9.7, 9.4, 0.0, 9.9, 9.6, 9.3, 9.4, 9.3, 9.9],  # Denzel Boston
    '18219': [9.9, 10.5, 9.2, 0.0, 9.6, 9.1, 8.9, 9.6, 9.1, 9.6, 9.4, 9.9],  # DK Metcalf
    '16712': [8.7, 8.3, 8.4, 8.7, 8.5, 9.0, 8.8, 8.4, 8.4, 8.7, 8.5, 8.5],  # Harrison Butker
    '26122': [7.8, 0.0, 9.0, 9.5, 9.4, 9.5, 9.3, 9.5, 9.8, 9.1, 10.4, 9.1],  # Ladd McConkey
    '28013': [0.0, 0.0, 0.0, 0.0, 0.0, 15.4, 15.9, 0.0, 17.2, 16.4, 17.8, 19.3],  # Fernando Mendoza
    '26434': [9.9, 8.1, 8.8, 10.2, 10.1, 9.1, 9.2, 0.0, 8.7, 9.4, 9.4, 8.7],  # Tyler Warren
    '25395': [7.8, 0.0, 9.0, 9.1, 8.9, 9.8, 9.6, 8.7, 10.4, 8.8, 10.4, 8.9],  # Omarion Hampton
    '23677': [0.0, 9.4, 8.4, 10.0, 9.0, 9.3, 9.6, 9.0, 9.5, 8.3, 9.3, 9.5],  # Jameson Williams
    '23062': [8.8, 8.9, 9.0, 9.5, 0.0, 10.0, 9.0, 9.1, 9.1, 9.5, 8.9, 8.7],  # Emeka Egbuka
    '27142': [8.1, 9.3, 8.6, 9.3, 0.0, 9.6, 8.9, 9.9, 8.9, 9.5, 9.4, 8.9],  # RJ Harvey
    '19245': [8.2, 10.2, 8.7, 9.3, 0.0, 9.9, 8.2, 9.9, 8.6, 9.5, 9.3, 8.4],  # J.K. Dobbins
    '16673': [0.0, 9.7, 9.6, 9.2, 8.9, 8.0, 8.6, 9.5, 8.6, 9.9, 9.3, 8.7],  # Aaron Jones Sr.
    '25411': [8.7, 8.6, 8.3, 9.0, 0.0, 9.1, 9.6, 8.7, 9.7, 9.0, 9.1, 9.7],  # Rome Odunze
    '26475': [8.9, 0.0, 9.3, 8.9, 9.0, 9.0, 9.2, 8.9, 9.0, 8.8, 9.1, 9.3],  # Cam Little
    '25533': [9.0, 9.1, 9.3, 9.1, 9.4, 0.0, 9.4, 9.0, 8.4, 9.8, 8.3, 8.5],  # Blake Grupe
    '28176': [9.2, 8.8, 0.0, 8.9, 9.3, 8.9, 9.1, 8.9, 8.7, 9.1, 9.2, 9.1],  # Dominic Zvada
    '17270': [8.5, 9.7, 9.3, 8.9, 0.0, 8.9, 9.6, 9.5, 9.0, 8.3, 8.8, 8.4],  # Dallas Goedert
    '23019': [8.2, 7.7, 7.7, 8.4, 8.1, 9.2, 8.5, 7.9, 8.6, 8.2, 8.0, 8.3],  # Xavier Worthy
    '27534': [8.8, 9.0, 9.2, 8.9, 9.1, 9.0, 8.8, 0.0, 9.0, 8.8, 9.1, 8.8],  # Tyler Loop
    '27131': [6.8, 9.0, 7.8, 8.7, 0.0, 9.8, 9.7, 8.0, 9.7, 9.1, 9.4, 10.0],  # Kyle Monangai
    '25298': [9.9, 9.5, 8.6, 8.5, 7.8, 0.0, 8.2, 8.9, 9.0, 9.7, 9.0, 8.7],  # Tucker Kraft
    '19231': [0.0, 0.0, 0.0, 10.6, 11.3, 10.9, 11.2, 10.8, 11.0, 9.7, 11.4, 9.9],  # Travis Etienne Jr.
    '14003': [8.2, 8.7, 8.8, 9.2, 8.7, 0.0, 8.3, 9.1, 8.9, 8.6, 8.7, 8.7],  # Jason Myers
    '27050': [8.5, 8.9, 8.7, 8.8, 8.6, 0.0, 9.0, 8.8, 8.4, 8.6, 8.5, 9.0],  # Harold Fannin Jr.
    '27261': [7.8, 8.0, 8.0, 8.0, 8.1, 8.1, 7.8, 7.6, 8.4, 8.0, 7.8, 7.9],  # Ryan Fitzgerald
    '25345': [8.0, 0.0, 8.7, 8.4, 8.7, 8.5, 8.8, 8.7, 8.6, 8.4, 9.3, 9.1],  # Brenton Strange
    '22969': [0.0, 9.3, 9.0, 8.8, 8.4, 7.5, 8.3, 9.2, 8.0, 9.4, 8.8, 8.2],  # Jordan Mason
    '9491': [9.1, 8.5, 8.3, 8.5, 8.6, 0.0, 8.1, 9.0, 8.4, 8.6, 8.6, 9.0],  # Nick Folk
    '13029': [8.3, 8.9, 8.7, 0.0, 8.4, 8.5, 8.7, 8.6, 8.3, 8.7, 8.8, 8.6],  # Chris Boswell
    '26075': [0.0, 8.7, 8.4, 8.7, 8.5, 8.5, 8.6, 8.5, 8.7, 8.1, 8.7, 8.4],  # Jake Bates
    '26314': [0.0, 8.4, 8.2, 9.2, 8.7, 8.5, 8.5, 7.8, 8.8, 8.6, 8.4, 8.6],  # Malik Washington
    '13981': [8.4, 0.0, 8.3, 8.2, 8.7, 8.8, 9.4, 8.9, 8.6, 8.4, 7.6, 8.3],  # Stefon Diggs
    '19562': [8.6, 8.8, 0.0, 8.1, 8.2, 8.7, 8.5, 8.6, 8.2, 8.4, 9.1, 8.1],  # Juwan Johnson
    '19760': [8.6, 0.0, 8.5, 8.0, 8.3, 8.8, 8.4, 8.3, 8.4, 8.5, 8.3, 8.6],  # Tyler Bass
    '17269': [7.8, 8.7, 8.6, 8.3, 8.4, 8.3, 8.5, 0.0, 8.5, 8.6, 8.2, 8.7],  # Mark Andrews
    '23018': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 16.5, 13.9, 12.1, 15.1, 17.3, 17.5],  # J.J. McCarthy
    '28175': [7.8, 0.0, 8.5, 8.4, 8.4, 8.4, 8.6, 8.4, 8.4, 8.5, 7.9, 8.1],  # Drew Stevens
    '23901': [7.4, 0.0, 8.2, 8.4, 8.3, 8.5, 8.5, 8.2, 8.5, 8.4, 8.6, 8.4],  # Cameron Dicker
    '13731': [8.3, 8.2, 7.8, 8.1, 0.0, 8.5, 8.3, 8.0, 8.3, 8.3, 8.3, 8.5],  # Cairo Santos
    '23107': [0.0, 8.4, 8.8, 8.4, 8.2, 7.6, 7.9, 8.0, 7.8, 8.9, 8.7, 7.9],  # Jordan Addison
    '17575': [8.7, 8.1, 0.0, 8.5, 8.3, 7.9, 8.1, 8.1, 8.2, 8.1, 7.9, 8.3],  # Eddy Pineiro
    '28507': [8.8, 8.2, 8.3, 7.9, 7.8, 0.0, 7.9, 8.2, 8.4, 8.4, 7.9, 8.0],  # Trey Smack
    '18290': [0.0, 8.3, 8.7, 8.2, 8.1, 7.6, 7.8, 7.8, 7.7, 8.8, 8.7, 7.9],  # T.J. Hockenson
    '19058': [7.8, 8.1, 8.2, 7.9, 0.0, 8.3, 8.3, 8.2, 7.9, 8.5, 8.0, 8.1],  # Chase McLaughlin
    '22985': [8.0, 7.5, 8.0, 0.0, 7.6, 8.8, 7.6, 8.5, 8.5, 8.1, 8.0, 8.0],  # Wan'Dale Robinson
    '16026': [7.6, 8.2, 8.0, 8.0, 0.0, 8.2, 7.8, 8.3, 7.9, 8.0, 8.3, 7.9],  # Wil Lutz
    '18598': [7.4, 0.0, 8.1, 7.7, 8.0, 7.8, 8.1, 8.0, 7.9, 7.7, 8.6, 8.4],  # Jakobi Meyers
    '27520': [11.2, 8.5, 0.0, 7.6, 7.6, 8.3, 7.4, 7.3, 7.8, 6.8, 7.1, 7.8],  # Woody Marks
    '24706': [8.8, 7.1, 7.5, 8.7, 8.6, 7.8, 7.8, 0.0, 7.4, 8.0, 8.0, 7.4],  # Josh Downs
    '20163': [7.7, 8.5, 7.7, 0.0, 8.0, 7.6, 7.5, 7.9, 7.6, 8.0, 7.9, 8.2],  # Pat Freiermuth
    '23297': [0.0, 7.6, 7.9, 7.8, 7.8, 7.8, 8.1, 7.6, 7.8, 7.8, 7.9, 7.9],  # Evan McPherson
    '26006': [7.5, 7.3, 7.0, 7.7, 0.0, 7.7, 8.2, 7.4, 8.3, 7.6, 7.7, 8.2],  # Colston Loveland
    '27331': [7.0, 7.8, 7.5, 7.8, 7.5, 0.0, 8.0, 7.8, 7.3, 7.6, 7.4, 8.0],  # KC Concepcion
    '23794': [8.7, 8.9, 8.1, 7.4, 7.9, 0.0, 7.1, 7.5, 6.5, 6.9, 7.2, 6.8],  # Romeo Doubs
    '23065': [0.0, 4.8, 9.3, 9.0, 9.4, 0.0, 8.6, 9.0, 7.8, 7.8, 8.6, 8.7],  # TreVeyon Henderson
    '25337': [8.1, 7.4, 7.7, 7.3, 7.2, 6.9, 7.2, 0.0, 7.5, 7.1, 7.9, 8.3],  # Tre Tucker
    '25247': [7.7, 0.0, 7.5, 6.8, 7.5, 8.1, 7.2, 7.3, 7.6, 7.7, 7.0, 8.1],  # Dalton Kincaid
    '17253': [6.7, 8.1, 7.0, 7.1, 0.0, 7.7, 7.5, 8.3, 7.3, 7.6, 7.6, 7.1],  # Courtland Sutton
    '27316': [7.1, 0.0, 7.4, 7.4, 8.3, 8.4, 8.2, 7.7, 7.2, 7.5, 6.4, 6.2],  # Jacory Croskey-Merritt
    '18244': [0.0, 0.0, 0.0, 9.6, 10.1, 8.3, 8.6, 9.3, 8.9, 9.1, 8.7, 9.0],  # Deebo Samuel Sr.
    '27291': [7.4, 7.2, 7.5, 7.5, 7.6, 0.0, 7.2, 7.7, 7.1, 7.1, 7.2, 7.4],  # Andy Borregales
    '18269': [0.0, 1.4, 5.4, 4.8, 4.2, 0.0, 9.7, 11.0, 10.8, 11.5, 10.8, 9.9],  # Josh Jacobs
    '14104': [6.2, 6.7, 6.7, 6.2, 6.7, 6.6, 6.7, 6.0, 6.8, 6.8, 6.7, 6.2],  # Darren Waller
    '17420': [6.9, 7.0, 0.0, 7.2, 7.3, 7.0, 6.9, 7.3, 7.1, 6.9, 7.5, 7.0],  # Daniel Carlson
    '8260': [7.2, 5.7, 6.5, 8.0, 7.1, 0.0, 6.5, 7.1, 8.8, 7.7, 6.6, 6.9],  # Seattle Seahawks
    '23013': [7.9, 7.5, 7.2, 7.1, 7.5, 0.0, 7.4, 6.6, 6.3, 7.9, 6.0, 6.5],  # Blake Corum
    '25354': [6.7, 7.1, 7.2, 6.7, 7.1, 6.8, 7.4, 7.2, 0.0, 7.1, 7.2, 7.4],  # Chad Ryland
    '8120': [5.8, 7.7, 0.0, 7.7, 7.9, 7.4, 6.0, 7.3, 6.5, 7.0, 7.6, 6.7],  # Houston Texans
    '17533': [6.7, 7.3, 7.2, 6.7, 7.3, 6.8, 7.1, 0.0, 7.0, 7.1, 7.0, 6.7],  # Jason Sanders
    '23791': [0.0, 0.0, 7.3, 9.4, 9.2, 8.5, 8.6, 0.0, 8.2, 8.7, 8.7, 8.2],  # Alec Pierce
    '26035': [0.0, 0.0, 0.0, 7.9, 8.3, 8.7, 8.6, 8.7, 8.2, 8.5, 9.3, 8.2],  # Jordyn Tyson
    '16421': [8.7, 8.5, 0.0, 6.5, 6.8, 6.7, 6.8, 6.6, 6.6, 6.1, 7.0, 6.1],  # Alvin Kamara
    '15561': [7.1, 7.0, 7.5, 7.0, 7.5, 0.0, 6.8, 7.1, 6.3, 6.6, 6.9, 6.5],  # Hunter Henry
    '16540': [6.7, 7.2, 6.9, 7.0, 0.0, 6.9, 7.0, 7.1, 7.2, 6.7, 6.8, 6.6],  # Jake Elliott
    '26335': [6.4, 6.6, 7.2, 7.6, 7.1, 0.0, 6.5, 7.7, 7.0, 6.6, 6.6, 6.8],  # AJ Barner
    '23748': [7.1, 0.0, 6.9, 6.2, 6.8, 7.5, 6.6, 6.7, 7.0, 7.1, 6.4, 7.5],  # Khalil Shakir
    '8280': [7.7, 6.2, 7.5, 6.1, 7.6, 0.0, 7.7, 6.6, 6.1, 6.3, 6.4, 7.3],  # Los Angeles Rams
    '16406': [6.5, 6.6, 6.7, 7.1, 0.0, 7.4, 6.7, 6.8, 6.8, 7.0, 6.6, 6.5],  # Chris Godwin Jr.
    '8170': [0.0, 7.7, 5.8, 5.8, 7.1, 5.9, 6.4, 7.1, 7.1, 6.4, 7.3, 8.0],  # Minnesota Vikings
    '8150': [8.1, 6.0, 5.9, 7.3, 5.1, 6.9, 5.2, 5.5, 6.0, 6.5, 5.1, 7.0],  # Kansas City Chiefs
    '25323': [6.9, 6.6, 6.9, 0.0, 6.0, 7.3, 5.9, 7.0, 7.2, 7.1, 7.0, 6.6],  # Tyjae Spears
    '23310': [6.7, 6.9, 6.5, 7.1, 0.0, 7.3, 7.0, 6.7, 6.5, 7.0, 6.4, 6.4],  # Kenny Gainwell
    '8140': [7.1, 0.0, 7.2, 5.2, 7.3, 7.1, 7.3, 6.2, 7.2, 6.8, 5.9, 6.6],  # Jacksonville Jaguars
    '24346': [12.0, 6.7, 6.7, 5.4, 6.4, 6.0, 6.6, 0.0, 6.0, 6.5, 6.0, 5.3],  # Braelon Allen
    '27329': [0.0, 0.0, 10.5, 8.5, 8.2, 0.0, 6.7, 8.6, 8.4, 7.2, 7.4, 8.0],  # Jadarian Price
    '8240': [7.6, 6.7, 8.0, 0.0, 5.6, 6.5, 6.7, 6.2, 5.7, 5.1, 6.5, 7.6],  # Pittsburgh Steelers
    '8020': [8.3, 5.6, 4.4, 5.9, 7.3, 6.1, 6.5, 0.0, 7.2, 6.7, 8.4, 5.7],  # Baltimore Ravens
    '24687': [5.8, 6.2, 6.8, 7.2, 6.7, 0.0, 6.1, 7.3, 6.6, 6.2, 6.2, 6.4],  # Rashid Shaheed
    '26039': [8.0, 8.2, 6.0, 0.0, 6.2, 5.9, 5.8, 6.1, 5.9, 6.2, 6.2, 6.4],  # Germie Bernard
    '25360': [6.7, 0.0, 6.1, 6.2, 6.2, 6.5, 6.5, 6.1, 6.9, 6.0, 6.9, 6.1],  # Keaton Mitchell
    '26398': [6.5, 6.1, 7.0, 6.6, 6.2, 6.7, 6.2, 5.9, 0.0, 6.1, 6.3, 6.5],  # Ryan Flournoy
    '8090': [6.0, 6.7, 5.1, 6.2, 0.0, 6.9, 6.9, 7.3, 7.2, 7.1, 4.8, 5.9],  # Denver Broncos
    '11616': [7.1, 5.8, 6.0, 7.0, 6.9, 6.2, 6.2, 0.0, 5.9, 6.4, 6.3, 5.9],  # Keenan Allen
    '19028': [6.3, 6.4, 6.1, 0.0, 6.2, 6.4, 6.0, 6.5, 6.5, 6.6, 6.3, 6.3],  # Joey Slye
    '8180': [7.8, 5.0, 7.3, 6.4, 4.9, 0.0, 7.3, 5.3, 6.6, 4.9, 7.4, 6.5],  # New England Patriots
    '17349': [6.1, 6.4, 0.0, 6.2, 6.0, 6.5, 6.3, 6.4, 6.8, 6.2, 6.0, 6.4],  # Dalton Schultz
    '20164': [6.8, 6.0, 6.3, 6.4, 6.0, 0.0, 5.6, 6.8, 6.0, 6.7, 6.3, 6.4],  # Kyle Pitts Sr.
    '24354': [6.0, 6.8, 6.5, 6.2, 0.0, 6.2, 6.7, 6.6, 6.3, 5.7, 6.1, 5.8],  # Dontayvion Wicks
    '23118': [10.6, 9.6, 7.7, 6.8, 6.3, 0.0, 4.1, 4.6, 4.6, 4.9, 4.7, 4.3],  # MarShawn Lloyd
    '8230': [6.1, 5.2, 5.2, 7.1, 0.0, 6.7, 6.0, 7.0, 7.3, 6.5, 6.6, 4.3],  # Philadelphia Eagles
    '22718': [6.2, 6.0, 6.8, 6.4, 6.0, 6.5, 6.0, 5.8, 0.0, 5.9, 6.1, 6.3],  # Jake Ferguson
    '23181': [6.0, 6.0, 6.0, 6.5, 0.0, 6.8, 6.0, 6.2, 6.2, 6.4, 6.0, 5.9],  # Cade Otton
    '19647': [6.1, 6.6, 6.3, 0.0, 6.7, 5.8, 6.1, 5.9, 5.3, 6.0, 6.8, 6.3],  # Rico Dowdle
    '28135': [0.0, 6.1, 6.0, 6.6, 6.3, 6.2, 6.1, 5.7, 6.3, 6.2, 6.1, 6.2],  # Caleb Douglas
    '24357': [0.0, 7.4, 7.0, 6.3, 6.9, 6.6, 7.4, 0.0, 6.2, 7.3, 6.5, 5.9],  # Adonai Mitchell
    '8270': [6.1, 5.2, 0.0, 6.7, 5.2, 5.9, 6.7, 6.9, 5.6, 6.8, 5.0, 7.1],  # San Francisco 49ers
    '8040': [6.5, 6.1, 5.1, 5.6, 5.6, 4.4, 5.7, 5.2, 5.6, 5.1, 5.7, 5.6],  # Carolina Panthers
    '8100': [0.0, 5.6, 6.1, 7.1, 5.8, 6.4, 5.6, 4.9, 6.9, 5.7, 6.9, 5.1],  # Detroit Lions
    '23891': [6.1, 0.0, 5.9, 5.9, 6.4, 6.5, 6.5, 6.2, 5.9, 6.0, 5.3, 5.4],  # Rachaad White
    '23000': [5.5, 0.0, 6.0, 5.8, 6.0, 5.8, 6.1, 6.0, 6.0, 5.8, 6.5, 6.3],  # Brian Thomas Jr.
    '24172': [5.6, 5.9, 6.7, 5.2, 5.8, 5.4, 6.4, 5.8, 0.0, 6.1, 6.3, 6.6],  # Tyler Allgeier
    '23123': [5.8, 0.0, 5.6, 6.0, 5.9, 6.0, 5.8, 6.0, 6.2, 5.7, 6.7, 5.7],  # Quentin Johnston
    '17066': [5.7, 5.5, 6.7, 5.4, 5.7, 5.6, 6.6, 5.8, 0.0, 6.0, 6.1, 6.2],  # Kendrick Bourne
    '26019': [6.1, 0.0, 5.9, 5.4, 5.9, 6.4, 5.7, 5.8, 6.0, 6.1, 5.5, 6.4],  # Keon Coleman
    '26055': [4.7, 6.6, 6.2, 5.7, 6.1, 5.9, 6.6, 0.0, 5.6, 6.5, 5.8, 5.4],  # Kenyon Sadiq
    '19794': [5.5, 6.1, 6.1, 5.8, 5.9, 5.8, 5.9, 0.0, 6.0, 6.0, 5.7, 6.1],  # Rashod Bateman
    '17272': [0.0, 5.8, 6.1, 5.7, 6.0, 6.3, 6.0, 5.6, 5.6, 5.7, 6.0, 5.9],  # Mike Gesicki
    '8070': [4.9, 7.1, 6.1, 6.2, 5.9, 0.0, 6.4, 5.5, 4.7, 6.9, 4.2, 6.8],  # Cleveland Browns
    '20094': [6.4, 5.4, 5.4, 6.3, 5.4, 0.0, 5.0, 6.5, 5.8, 5.9, 5.6, 6.5],  # Brian Robinson Jr.
    '26215': [0.0, 0.0, 6.0, 6.5, 6.4, 6.5, 6.2, 6.5, 6.6, 6.1, 7.2, 6.1],  # Tre' Harris
    '16081': [5.6, 5.5, 5.3, 5.7, 0.0, 5.8, 6.1, 5.6, 6.2, 5.8, 5.8, 6.2],  # Kalif Raymond
    '28896': [0.0, 0.0, 6.9, 0.0, 7.2, 6.9, 6.7, 7.2, 6.8, 7.2, 7.1, 7.4],  # Roman Wilson
    '8010': [5.4, 4.3, 6.3, 5.7, 4.9, 0.0, 5.9, 4.9, 7.0, 5.3, 7.1, 6.6],  # Atlanta Falcons
    '26409': [8.7, 8.8, 0.0, 4.8, 4.9, 5.2, 5.0, 5.1, 4.9, 5.0, 5.5, 4.8],  # Devaughn Vele
    '8210': [5.1, 7.2, 6.3, 4.3, 4.2, 5.6, 6.1, 0.0, 5.3, 6.4, 5.3, 5.8],  # New York Jets
    '23056': [6.0, 5.5, 5.7, 5.5, 5.4, 5.2, 5.4, 0.0, 5.6, 5.2, 5.9, 6.2],  # Michael Mayer
    '23982': [5.5, 0.0, 5.4, 5.3, 5.7, 5.8, 6.2, 5.9, 5.7, 5.5, 5.0, 5.5],  # Chig Okonkwo
    '8130': [7.1, 5.0, 4.3, 4.5, 6.4, 4.8, 6.0, 0.0, 5.6, 6.5, 4.5, 6.8],  # Indianapolis Colts
    '8030': [5.5, 0.0, 4.5, 5.7, 6.4, 6.3, 4.8, 5.2, 5.2, 4.9, 5.7, 7.1],  # Buffalo Bills
    '8110': [5.0, 4.1, 5.8, 5.2, 6.1, 0.0, 5.3, 6.1, 5.0, 7.5, 4.9, 6.2],  # Green Bay Packers
    '8200': [6.0, 5.2, 0.0, 6.3, 5.3, 5.3, 6.2, 4.7, 5.6, 7.7, 4.0, 4.5],  # New York Giants
    '8050': [4.6, 5.8, 5.6, 6.6, 0.0, 5.9, 4.6, 5.4, 6.5, 4.4, 6.0, 4.9],  # Chicago Bears
    '8060': [0.0, 4.3, 6.7, 4.6, 6.1, 4.7, 5.9, 6.6, 4.7, 5.4, 6.5, 4.6],  # Cincinnati Bengals
    '8310': [3.6, 0.0, 6.5, 4.8, 6.2, 5.2, 6.2, 6.1, 5.5, 4.9, 5.9, 4.8],  # Washington Commanders
    '8290': [6.1, 5.6, 5.0, 4.9, 0.0, 4.6, 5.9, 6.5, 4.5, 6.6, 4.6, 4.8],  # Tampa Bay Buccaneers
    '16423': [0.0, 5.1, 5.6, 5.2, 5.3, 5.5, 5.7, 5.3, 5.0, 5.5, 5.6, 5.2],  # Samaje Perine
    '8220': [3.7, 4.3, 6.2, 3.7, 5.1, 4.7, 6.0, 0.0, 5.7, 5.2, 6.7, 5.8],  # Las Vegas Raiders
    '8250': [3.8, 0.0, 4.2, 5.6, 4.1, 6.2, 5.5, 5.8, 5.7, 4.4, 6.9, 4.5],  # Los Angeles Chargers
    '25420': [4.8, 5.7, 5.4, 5.1, 0.0, 5.1, 5.6, 5.5, 5.2, 4.6, 5.0, 4.7],  # Makai Lemon
    '8080': [4.4, 5.6, 5.9, 5.4, 3.9, 5.8, 5.9, 5.0, 0.0, 4.2, 4.4, 6.2],  # Dallas Cowboys
    '22967': [5.0, 5.4, 5.0, 0.0, 5.3, 5.0, 4.9, 5.2, 5.0, 5.2, 5.1, 5.4],  # Darnell Washington
    '26038': [0.0, 5.1, 4.9, 5.6, 5.2, 5.1, 5.1, 4.6, 5.3, 5.2, 5.1, 5.2],  # Chris Bell
    '23153': [0.0, 5.1, 4.9, 5.5, 5.2, 5.1, 5.1, 4.7, 5.3, 5.2, 5.1, 5.1],  # Greg Dulcich
    '18256': [4.9, 5.4, 5.2, 4.8, 5.1, 5.2, 5.0, 0.0, 5.0, 5.1, 5.1, 5.4],  # Justice Hill
    '16433': [4.8, 4.8, 5.2, 5.5, 5.1, 0.0, 4.7, 5.6, 5.1, 4.8, 4.8, 4.9],  # Cooper Kupp
    '18406': [5.1, 0.0, 4.9, 4.5, 4.9, 5.3, 4.8, 4.9, 5.0, 5.3, 4.8, 5.3],  # Ty Johnson
    '27122': [4.7, 5.0, 0.0, 4.9, 4.8, 5.1, 4.9, 5.0, 5.2, 4.7, 4.7, 5.0],  # Jaylin Noel
    '27446': [0.0, 5.0, 4.5, 5.3, 4.8, 4.9, 5.1, 4.8, 5.1, 4.4, 5.0, 5.1],  # Isaac TeSlaa
    '22986': [4.4, 0.0, 5.3, 4.5, 4.9, 5.3, 5.0, 5.0, 4.8, 4.5, 5.2, 5.0],  # Chris Rodriguez Jr.
    '8300': [5.2, 7.2, 4.2, 0.0, 4.4, 4.0, 4.3, 4.3, 3.5, 5.7, 5.4, 5.4],  # Tennessee Titans
    '8190': [5.5, 4.9, 0.0, 6.6, 4.7, 4.1, 4.2, 4.4, 4.2, 4.9, 5.8, 4.0],  # New Orleans Saints
    '25987': [0.0, 4.6, 4.6, 5.2, 5.0, 4.7, 4.7, 4.5, 5.0, 4.7, 4.7, 4.8],  # Jaylen Wright
    '27994': [5.0, 4.6, 0.0, 4.5, 5.1, 4.5, 4.8, 4.4, 4.2, 4.5, 5.2, 5.3],  # Malachi Fields
    '28107': [4.4, 3.7, 4.1, 4.3, 4.1, 4.7, 4.3, 4.0, 4.5, 4.2, 4.0, 4.2],  # Emmett Johnson
    '19810': [4.8, 4.4, 0.0, 4.3, 4.9, 4.3, 4.7, 4.3, 4.0, 4.3, 5.0, 5.1],  # Darnell Mooney
    '28108': [4.3, 4.5, 4.6, 4.2, 4.2, 4.5, 4.6, 0.0, 4.7, 4.6, 4.8, 4.9],  # Mike Washington Jr.
    '19302': [6.0, 4.4, 0.0, 4.1, 4.6, 3.8, 4.8, 4.0, 3.7, 4.6, 4.8, 4.9],  # Najee Harris
    '27059': [4.5, 4.2, 4.5, 0.0, 4.3, 4.9, 4.2, 4.8, 4.7, 4.5, 4.5, 4.5],  # Elic Ayomanor
    '16411': [4.0, 4.9, 4.2, 4.3, 0.0, 4.6, 4.5, 4.9, 4.4, 4.6, 4.6, 4.3],  # Evan Engram
    '28401': [5.3, 4.5, 0.0, 4.9, 4.8, 3.9, 3.8, 4.8, 4.3, 4.4, 3.9, 4.2],  # Kaelon Black
    '24177': [4.8, 4.3, 4.5, 4.3, 4.2, 4.1, 4.2, 0.0, 4.4, 4.1, 4.6, 4.8],  # Jalen Nailor
    '25418': [4.3, 0.0, 4.3, 4.2, 4.4, 4.5, 4.8, 4.6, 4.4, 4.3, 4.0, 4.3],  # Antonio Williams
    '8000': [3.0, 4.5, 3.7, 4.2, 3.7, 3.6, 3.8, 4.9, 0.0, 6.1, 4.7, 5.3],  # Arizona Cardinals
    '8160': [0.0, 5.8, 4.2, 3.5, 4.7, 3.4, 5.3, 4.0, 3.8, 3.9, 5.3, 3.5],  # Miami Dolphins
    '23101': [4.6, 4.1, 4.2, 4.3, 4.1, 0.0, 3.8, 4.7, 4.1, 4.5, 4.3, 4.4],  # Jahan Dotson
    '22989': [4.0, 3.3, 0.0, 4.3, 4.2, 4.5, 4.4, 4.4, 4.7, 4.3, 4.2, 4.5],  # Kayshon Boutte
    '25304': [6.1, 5.4, 4.4, 3.9, 4.2, 0.0, 3.8, 4.0, 3.4, 3.6, 3.8, 3.6],  # DeMario Douglas
    '26355': [5.6, 0.0, 3.9, 3.9, 3.9, 4.1, 4.1, 3.8, 4.4, 3.8, 4.4, 3.8],  # Kimani Vidal
    '19372': [4.5, 4.1, 4.0, 4.3, 4.3, 0.0, 4.1, 3.8, 3.8, 4.4, 3.7, 4.0],  # Colby Parkinson
    '28130': [3.9, 4.0, 4.0, 4.2, 0.0, 4.5, 4.0, 4.1, 4.0, 4.2, 3.9, 3.9],  # Ted Hurst III
    '28137': [3.7, 0.0, 4.1, 3.9, 4.0, 3.9, 4.0, 4.0, 4.0, 3.8, 4.3, 4.2],  # Josh Cameron
    '27109': [3.9, 3.7, 3.9, 0.0, 3.7, 4.3, 3.7, 4.2, 4.1, 3.9, 3.9, 3.9],  # Gunnar Helm
    '25332': [3.6, 4.0, 0.0, 3.8, 3.7, 4.1, 3.9, 3.9, 4.1, 3.8, 3.8, 3.9],  # Xavier Hutchinson
    '25997': [3.2, 0.0, 3.7, 4.0, 3.9, 4.0, 3.8, 4.0, 4.0, 3.7, 4.4, 3.7],  # Oronde Gadsden II
    '26638': [0.0, 4.2, 4.2, 4.2, 4.2, 0.0, 4.2, 4.2, 4.2, 4.2, 4.2, 4.2],  # Harrison Mevis
    '26309': [0.0, 3.9, 3.4, 4.1, 3.7, 3.7, 4.0, 3.6, 3.9, 3.3, 4.1, 3.9],  # Sione Vaki
    '17527': [3.7, 3.8, 0.0, 3.6, 3.6, 3.8, 3.8, 3.8, 3.6, 3.8, 4.0, 3.6],  # Noah Fant
    '19201': [3.8, 3.7, 3.6, 3.8, 3.6, 0.0, 3.8, 3.7, 3.6, 3.6, 3.5, 3.9],  # Jerry Jeudy
    '28154': [4.0, 3.6, 3.9, 3.6, 3.9, 0.0, 3.5, 3.7, 3.2, 3.4, 3.5, 3.4],  # Eli Raridon
    '15623': [4.7, 3.6, 3.4, 3.6, 3.7, 0.0, 3.5, 3.4, 3.3, 3.8, 3.2, 3.3],  # Tyler Higbee
    '16483': [3.4, 0.0, 3.5, 3.5, 3.9, 4.0, 3.9, 3.7, 3.5, 3.6, 3.2, 3.2],  # Austin Ekeler
    '23092': [3.8, 4.8, 3.2, 3.2, 0.0, 3.4, 3.3, 3.6, 3.2, 3.4, 3.5, 3.2],  # Troy Franklin
    '19539': [3.2, 3.0, 3.0, 3.2, 3.1, 3.6, 3.3, 3.1, 3.3, 3.2, 3.1, 3.2],  # Noah Gray
    '25968': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 6.9, 6.6, 5.6, 6.5, 6.1, 6.4],  # Shedeur Sanders
    '27076': [3.1, 3.0, 3.0, 3.2, 3.1, 3.5, 3.3, 3.0, 3.2, 3.2, 3.1, 3.2],  # Jalen Royals
    '22976': [3.4, 3.3, 3.8, 3.7, 3.2, 3.6, 3.4, 3.0, 0.0, 3.3, 3.1, 3.8],  # Tyler Goodson
    '23080': [3.1, 3.7, 3.2, 3.3, 0.0, 3.5, 3.4, 3.7, 3.4, 3.5, 3.4, 3.2],  # Marvin Mims Jr.
    '22756': [3.2, 0.0, 3.2, 3.4, 3.4, 3.4, 3.4, 3.4, 3.6, 3.3, 3.8, 3.3],  # Charlie Kolar
    '22795': [3.0, 3.2, 3.2, 3.0, 3.3, 3.2, 3.1, 2.8, 3.3, 3.2, 3.2, 2.9],  # Tommy Tremble
    '25876': [4.5, 3.2, 3.0, 3.4, 3.0, 0.0, 3.4, 3.3, 3.1, 3.3, 3.0, 3.4],  # Jaleel McLaughlin
    '19590': [0.0, 3.3, 3.5, 3.2, 3.3, 3.1, 3.1, 3.1, 3.1, 3.5, 3.6, 3.3],  # Jauan Jennings
    '26011': [2.9, 3.4, 3.4, 3.3, 3.3, 3.3, 3.3, 0.0, 3.3, 3.3, 3.2, 3.4],  # Devontez Walker
    '28067': [0.0, 3.1, 3.1, 3.5, 3.2, 3.2, 3.1, 2.9, 3.3, 3.2, 3.1, 3.3],  # Kevin Coleman Jr.
    '26005': [3.6, 3.5, 3.2, 2.9, 3.2, 3.1, 3.4, 0.0, 2.9, 3.4, 3.0, 2.7],  # Mason Taylor
    '22915': [6.0, 3.9, 3.1, 1.1, 2.9, 0.0, 2.5, 3.1, 3.0, 2.4, 2.5, 3.0],  # George Holani
    '25419': [3.3, 2.9, 3.0, 3.1, 2.9, 0.0, 2.7, 3.3, 2.9, 3.2, 3.0, 3.1],  # Zachariah Branch
    '24360': [8.5, 6.8, 0.0, 1.9, 2.1, 2.0, 2.1, 2.0, 2.0, 1.8, 2.0, 1.8],  # Kendre Miller
    '18631': [3.0, 0.0, 3.0, 2.7, 2.9, 3.2, 2.9, 2.9, 3.0, 3.0, 2.8, 3.2],  # Dawson Knox
    '27147': [2.8, 2.9, 2.9, 3.0, 0.0, 3.2, 2.9, 2.9, 2.9, 3.0, 2.8, 2.8],  # Tez Johnson
    '23079': [3.3, 2.4, 2.4, 3.3, 3.2, 2.6, 3.2, 0.0, 2.7, 2.9, 3.2, 2.8],  # Seth McGowan
    '17258': [2.8, 2.7, 2.8, 0.0, 2.7, 3.0, 2.7, 3.0, 3.0, 2.9, 2.8, 2.8],  # Calvin Ridley
    '27144': [4.8, 2.8, 2.6, 2.5, 2.3, 0.0, 2.4, 2.7, 2.8, 2.8, 2.6, 2.5],  # Savion Williams
    '22930': [0.0, 1.2, 3.2, 3.4, 0.0, 3.1, 3.4, 3.4, 3.4, 2.8, 3.0, 2.8],  # Will Shipley
    '19229': [2.6, 2.5, 2.5, 2.7, 0.0, 2.7, 2.9, 2.6, 2.9, 2.6, 2.7, 2.9],  # Cole Kmet
    '27211': [0.0, 0.0, 0.0, 3.4, 0.0, 3.6, 3.8, 4.1, 3.7, 3.6, 3.5, 3.5],  # Pat Bryant
    '23812': [2.3, 2.8, 2.4, 2.5, 0.0, 2.7, 2.6, 2.9, 2.6, 2.7, 2.6, 2.5],  # Tyler Badie
    '23091': [0.0, 0.0, 3.1, 3.4, 3.4, 0.0, 3.2, 2.8, 2.9, 3.4, 2.8, 3.2],  # Terrance Ferguson
    '28168': [2.4, 2.6, 2.6, 2.5, 2.5, 2.5, 2.6, 0.0, 2.6, 2.6, 2.5, 2.6],  # Matt Hibner
    '25885': [13.1, 6.6, 0.9, 1.6, 0.8, 0.0, 0.7, 0.9, 0.9, 0.7, 0.8, 0.9],  # Emanuel Wilson
    '23119': [2.4, 2.4, 2.8, 2.3, 2.4, 2.4, 2.7, 2.5, 0.0, 2.5, 2.6, 2.6],  # Elijah Higgins
    '22845': [2.6, 0.0, 2.5, 2.2, 2.5, 2.7, 2.4, 2.4, 2.5, 2.6, 2.3, 2.7],  # Joshua Palmer
    '23104': [2.6, 2.4, 0.0, 2.3, 2.6, 2.3, 2.5, 2.3, 2.2, 2.3, 2.6, 2.7],  # Theo Johnson
    '15665': [0.0, 0.0, 0.0, 3.0, 3.2, 2.7, 2.7, 3.0, 2.8, 3.0, 2.8, 2.8],  # Demarcus Robinson
    '23837': [0.0, 2.3, 2.4, 2.3, 2.4, 2.4, 2.5, 2.2, 2.3, 2.3, 2.4, 2.4],  # Erick All Jr.
    '27487': [4.2, 3.0, 1.9, 2.0, 2.2, 0.0, 2.0, 2.1, 1.8, 1.9, 1.9, 1.9],  # Kyle Williams
    '15581': [2.5, 2.1, 2.3, 2.3, 2.1, 0.0, 2.0, 2.4, 2.1, 2.4, 2.3, 2.3],  # Austin Hooper
    '24351': [0.0, 2.5, 2.4, 2.4, 2.4, 0.0, 2.5, 2.7, 2.3, 2.6, 2.3, 2.5],  # Raheim Sanders
    '28127': [2.3, 2.5, 2.2, 2.1, 2.1, 2.2, 2.5, 0.0, 2.0, 2.4, 2.1, 2.0],  # Omar Cooper Jr.
    '22905': [2.1, 0.0, 2.2, 2.2, 2.3, 2.3, 2.4, 2.3, 2.2, 2.2, 2.0, 2.1],  # Treylon Burks
    '23064': [2.0, 3.2, 0.0, 2.0, 2.0, 2.2, 2.1, 2.1, 2.2, 2.0, 2.0, 2.1],  # Marvin Harrison Jr.
    '26022': [2.0, 2.1, 2.2, 2.4, 2.2, 0.0, 2.1, 2.4, 2.2, 2.1, 2.1, 2.1],  # Tory Horton
    '16556': [4.1, 2.1, 2.1, 1.9, 2.1, 1.9, 2.1, 0.0, 1.8, 2.1, 1.9, 1.7],  # Tim Patrick
    '16460': [2.4, 2.3, 2.0, 2.0, 1.9, 0.0, 2.0, 2.1, 2.1, 2.3, 2.2, 2.1],  # Jonnu Smith
    '19278': [0.0, 0.0, 0.0, 0.0, 3.1, 2.7, 2.6, 3.0, 2.9, 2.9, 2.8, 3.2],  # Michael Pittman Jr.
    '26148': [0.0, 0.0, 0.0, 2.5, 2.6, 2.6, 2.6, 2.3, 2.7, 3.0, 2.6, 2.3],  # Jonathon Brooks
    '27315': [2.1, 2.0, 2.1, 0.0, 2.0, 2.2, 2.0, 2.2, 2.2, 2.1, 2.1, 2.1],  # Chimere Dike
    '18520': [2.1, 2.0, 2.3, 2.1, 2.0, 2.1, 2.0, 1.9, 0.0, 2.0, 2.0, 2.1],  # KaVontae Turpin
    '18864': [2.2, 1.9, 2.0, 2.0, 1.9, 0.0, 1.7, 2.2, 1.9, 2.1, 2.0, 2.0],  # Olamide Zaccheaus
    '18561': [0.0, 1.9, 2.0, 1.9, 2.0, 2.1, 2.0, 1.8, 1.8, 1.9, 2.0, 1.9],  # Drew Sample
    '24238': [1.9, 1.8, 1.9, 0.0, 1.9, 2.1, 1.9, 2.0, 2.0, 1.9, 1.9, 1.9],  # Daniel Bellinger
    '23679': [1.8, 2.0, 1.9, 1.8, 0.0, 2.1, 2.1, 2.0, 1.7, 2.2, 1.8, 1.8],  # Sean Tucker
    '25289': [5.1, 4.5, 0.0, 1.3, 1.4, 1.1, 1.2, 1.3, 1.2, 1.2, 1.2, 1.3],  # Jacob Cowing
    '27224': [0.0, 0.0, 2.4, 2.2, 2.2, 2.1, 2.3, 0.0, 2.3, 2.1, 2.5, 2.6],  # Jack Bech
    '27337': [1.5, 2.0, 1.8, 1.9, 1.9, 1.8, 2.0, 0.0, 2.0, 2.0, 1.8, 2.0],  # Ja'Kobi Lane
    '11798': [0.0, 0.0, 0.0, 2.4, 2.5, 2.1, 2.2, 2.3, 2.2, 2.2, 2.3, 2.3],  # Kyle Juszczyk
    '28427': [1.6, 0.0, 1.8, 1.7, 1.8, 1.7, 1.8, 1.8, 1.7, 1.7, 1.9, 1.8],  # Nate Boerkircher
    '27138': [1.5, 1.6, 1.6, 1.5, 1.7, 1.6, 1.6, 1.4, 1.7, 1.7, 1.6, 1.5],  # Mitchell Evans
    '24371': [1.8, 1.7, 1.7, 1.7, 1.6, 0.0, 1.7, 1.7, 1.6, 1.7, 1.6, 1.7],  # Isaiah Bond
    '22980': [1.9, 1.6, 0.0, 1.6, 1.7, 1.5, 1.7, 1.5, 1.5, 1.7, 1.8, 1.8],  # Tyrone Tracy Jr.
    '18590': [1.5, 1.8, 1.6, 1.6, 0.0, 1.7, 1.6, 1.7, 1.5, 1.7, 1.8, 1.6],  # Lil'Jordan Humphrey
    '26379': [3.9, 1.6, 1.4, 1.3, 1.3, 1.4, 1.6, 0.0, 1.2, 1.5, 1.3, 1.2],  # Isaiah Williams
    '27320': [0.0, 0.9, 1.7, 1.9, 0.0, 1.8, 1.7, 1.9, 1.8, 1.8, 1.8, 1.8],  # Jonah Coleman
    '28161': [1.5, 1.4, 1.4, 1.5, 0.0, 1.5, 1.6, 1.5, 1.7, 1.5, 1.5, 1.6],  # Sam Roush
    '22895': [1.4, 1.7, 1.4, 1.3, 1.5, 1.4, 1.3, 1.2, 1.5, 1.4, 1.3, 1.3],  # John Metchie III
    '25335': [0.0, 0.0, 0.0, 1.8, 1.9, 2.1, 1.8, 1.8, 1.7, 1.7, 1.9, 1.8],  # Andrei Iosivas
    '22947': [5.9, 3.6, 0.7, 0.8, 0.0, 0.7, 0.8, 0.8, 0.8, 0.7, 0.7, 0.7],  # Dameon Pierce
    '16489': [0.0, 0.0, 0.0, 2.1, 2.2, 0.0, 2.1, 2.0, 1.9, 1.9, 2.1, 1.9],  # Mack Hollins
    '20113': [1.4, 0.0, 1.4, 1.4, 1.5, 1.5, 1.6, 1.5, 1.4, 1.4, 1.3, 1.4],  # Dyami Brown
    '23742': [1.4, 1.6, 1.5, 1.3, 1.5, 1.4, 1.5, 0.0, 1.3, 1.5, 1.4, 1.3],  # Jeremy Ruckert
    '28424': [1.3, 1.2, 1.2, 1.3, 1.3, 1.4, 1.3, 1.2, 1.3, 1.3, 1.2, 1.3],  # Cyrus Allen
    '19423': [1.2, 1.5, 1.3, 1.3, 0.0, 1.4, 1.4, 1.5, 1.3, 1.4, 1.4, 1.3],  # Adam Trautman
    '28403': [0.0, 1.3, 1.3, 1.5, 1.4, 1.4, 1.3, 1.2, 1.4, 1.3, 1.3, 1.4],  # Will Kacmarek
    '26136': [0.0, 0.0, 1.7, 1.4, 0.0, 1.6, 1.7, 1.4, 1.6, 1.7, 1.7, 1.5],  # Xavier Legette
    '27285': [1.2, 0.0, 1.3, 1.2, 1.3, 1.3, 1.3, 1.3, 1.3, 1.2, 1.4, 1.3],  # LeQuint Allen Jr.
    '28129': [3.4, 3.7, 0.0, 0.7, 0.7, 0.8, 0.8, 0.8, 0.7, 0.8, 0.9, 0.7],  # Bryce Lance
    '16399': [0.0, 0.0, 0.4, 0.4, 1.6, 1.7, 1.6, 1.7, 1.7, 1.5, 1.9, 1.5],  # David Njoku
    '25393': [0.0, 1.2, 1.3, 1.2, 1.2, 1.2, 1.2, 1.1, 1.2, 1.4, 1.2, 1.0],  # Trevor Etienne
    '26191': [1.2, 0.0, 1.2, 1.0, 1.2, 1.3, 1.1, 1.2, 1.2, 1.3, 1.2, 1.2],  # Ray Davis
    '28133': [1.4, 0.0, 1.1, 1.2, 1.2, 1.2, 1.1, 1.2, 1.2, 1.1, 1.3, 1.1],  # Brenen Thompson
    '27449': [1.3, 1.1, 1.1, 1.3, 1.3, 0.0, 1.2, 1.0, 1.1, 1.3, 1.0, 1.2],  # Konata Mumpfield
    '26194': [2.1, 1.1, 1.1, 1.0, 1.0, 1.0, 1.0, 0.0, 1.1, 1.1, 1.1, 1.2],  # Dylan Laube
    '22833': [1.1, 0.0, 1.1, 1.1, 1.1, 1.2, 1.3, 1.2, 1.2, 1.1, 1.0, 1.1],  # John Bates
    '22908': [0.0, 0.0, 0.0, 1.7, 0.0, 1.4, 1.6, 1.5, 1.6, 1.3, 1.5, 1.4],  # Tank Bigsby
    '23179': [0.0, 0.0, 0.0, 0.0, 0.0, 1.9, 1.6, 1.7, 1.7, 1.7, 1.7, 1.6],  # Jalen McMillan
    '24370': [0.0, 0.8, 1.1, 1.0, 1.1, 1.2, 1.0, 1.0, 1.0, 1.0, 1.1, 1.1],  # Mitch Tinsley
    '27182': [0.0, 1.0, 1.1, 1.0, 1.0, 0.9, 1.0, 1.0, 1.0, 1.1, 1.1, 1.0],  # Tai Felton
    '23135': [1.1, 1.0, 0.9, 1.0, 1.0, 0.0, 1.0, 0.9, 0.9, 1.1, 0.9, 1.0],  # Jordan Whittington
    '20126': [1.0, 1.0, 0.9, 1.0, 1.0, 0.0, 0.9, 0.9, 0.9, 1.1, 0.9, 0.9],  # Tutu Atwell
    '26034': [0.9, 0.0, 1.0, 0.9, 0.9, 1.0, 1.0, 1.0, 0.9, 0.9, 1.0, 1.0],  # Travis Hunter
    '11689': [0.3, 1.1, 1.1, 1.0, 0.0, 1.0, 1.1, 1.1, 1.0, 0.9, 1.0, 0.9],  # Zach Ertz
    '25267': [1.0, 0.9, 1.0, 1.0, 0.9, 1.0, 0.9, 0.9, 0.0, 0.9, 0.9, 0.9],  # Luke Schoonmaker
    '27288': [1.0, 0.9, 0.9, 0.9, 0.9, 0.8, 0.8, 0.0, 0.9, 0.9, 0.9, 1.0],  # Dont'e Thornton Jr.
    '25941': [0.8, 0.8, 1.0, 0.9, 0.8, 0.9, 0.9, 0.8, 0.0, 0.8, 0.8, 0.9],  # Hunter Luepke
    '27486': [2.5, 1.5, 0.0, 0.6, 0.6, 0.5, 0.5, 0.6, 0.6, 0.6, 0.6, 0.6],  # Jordan Watkins
    '28132': [0.8, 0.8, 0.7, 0.8, 0.0, 0.8, 0.9, 0.8, 0.8, 0.8, 0.8, 0.9],  # Zavion Thomas
    '25823': [0.0, 0.4, 0.0, 1.0, 0.9, 0.0, 0.9, 1.0, 1.0, 1.1, 1.1, 1.0],  # Chris Brooks
    '18656': [1.6, 1.2, 0.6, 0.7, 0.7, 0.6, 0.6, 0.0, 0.5, 0.6, 0.6, 0.6],  # Ashton Dulin
    '25977': [5.2, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.0, 0.3, 0.3, 0.3, 0.3],  # Isaiah Davis
    '24343': [0.8, 0.7, 0.8, 0.0, 0.6, 0.8, 0.6, 0.7, 0.8, 0.8, 0.8, 0.7],  # Nicholas Singleton
    '17528': [2.5, 0.0, 0.5, 0.6, 0.5, 0.6, 0.5, 0.6, 0.6, 0.5, 0.7, 0.5],  # Marquez Valdes-Scantling
    '27810': [0.7, 0.7, 0.7, 0.7, 0.0, 0.7, 0.8, 0.7, 0.8, 0.7, 0.7, 0.8],  # Jahdae Walker
    '28157': [0.7, 0.7, 0.0, 0.7, 0.7, 0.7, 0.7, 0.8, 0.8, 0.7, 0.7, 0.8],  # Marlin Klein
    '23770': [0.0, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.6, 0.7, 0.7, 0.7, 0.7],  # Jalen Tolbert
    '17145': [0.7, 0.7, 0.0, 0.7, 0.7, 0.7, 0.7, 0.6, 0.6, 0.6, 0.7, 0.7],  # Patrick Ricard
    '16447': [0.0, 0.7, 0.8, 0.6, 0.7, 0.7, 0.8, 0.7, 0.0, 0.7, 0.7, 0.8],  # James Conner
    '17826': [0.0, 0.6, 0.7, 0.6, 0.7, 0.7, 0.6, 0.6, 0.6, 0.6, 0.7, 0.6],  # Tanner Hudson
    '28141': [0.6, 0.6, 0.7, 0.6, 0.6, 0.6, 0.7, 0.6, 0.0, 0.6, 0.6, 0.7],  # Reggie Virgil
    '27463': [0.4, 0.7, 0.6, 0.6, 0.6, 0.6, 0.7, 0.0, 0.7, 0.7, 0.6, 0.7],  # Elijah Sarratt
    '23004': [0.6, 0.6, 0.7, 0.7, 0.6, 0.0, 0.6, 0.7, 0.6, 0.6, 0.6, 0.6],  # Elijah Arroyo
    '26012': [0.6, 0.6, 0.7, 0.6, 0.6, 0.6, 0.6, 0.0, 0.6, 0.6, 0.7, 0.7],  # Malik Benson
    '27165': [2.3, 1.7, 0.5, 0.4, 0.4, 0.0, 0.2, 0.2, 0.2, 0.3, 0.3, 0.2],  # Kaleb Johnson
    '25430': [0.6, 0.6, 0.0, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.7, 0.6],  # Oscar Delp
    '26310': [0.6, 0.0, 0.6, 0.6, 0.6, 0.6, 0.7, 0.6, 0.6, 0.6, 0.5, 0.6],  # Ben Sinnott
    '27499': [0.5, 0.0, 0.5, 0.5, 0.6, 0.5, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6],  # Tanner Koziol
    '27469': [0.6, 0.0, 0.5, 0.5, 0.5, 0.6, 0.5, 0.5, 0.5, 0.6, 0.5, 0.6],  # Jackson Hawes
    '25269': [0.5, 0.5, 0.0, 0.5, 0.5, 0.6, 0.5, 0.5, 0.6, 0.5, 0.5, 0.6],  # Cade Stover
    '25361': [0.9, 0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.4, 0.5],  # Tank Dell
    '18706': [1.6, 0.4, 0.3, 0.4, 0.4, 0.3, 0.3, 0.0, 0.3, 0.4, 0.3, 0.3],  # Darius Slayton
    '17268': [0.0, 0.0, 0.0, 0.5, 0.6, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],  # Christian Kirk
    '18280': [0.0, 0.4, 0.0, 0.4, 0.5, 0.4, 0.5, 0.4, 0.4, 0.4, 0.5, 0.5],  # Devin Singletary
    '25416': [0.4, 0.4, 0.0, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4],  # Barion Brown
    '23144': [0.0, 0.4, 0.4, 0.4, 0.0, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4],  # Eli Stowers
    '26037': [0.0, 0.7, 0.4, 0.3, 0.4, 0.4, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],  # Colbie Young
    '18585': [0.3, 0.0, 0.3, 0.3, 0.4, 0.4, 0.3, 0.3, 0.4, 0.4, 0.3, 0.4],  # Greg Dortch
    '27259': [0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],  # Brashard Smith
    '27310': [0.3, 0.3, 0.0, 0.3, 0.3, 0.3, 0.3, 0.4, 0.3, 0.3, 0.3, 0.3],  # Jordan James
    '18571': [0.3, 0.0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],  # Alec Ingold
    '26212': [0.0, 0.3, 0.3, 0.2, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],  # Tahj Brooks
    '23986': [0.0, 0.3, 0.3, 0.3, 0.3, 0.0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],  # Ronnie Rivers
    '25322': [1.9, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Roschon Johnson
    '28084': [0.0, 0.3, 0.3, 0.2, 0.2, 0.2, 0.2, 0.3, 0.2, 0.3, 0.3, 0.2],  # Demond Claiborne
    '24372': [0.0, 0.0, 0.0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],  # KeAndre Lambert-Smith
    '23054': [2.3, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Audric Estime
    '28079': [0.0, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],  # Justin Joly
    '25693': [0.0, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],  # Jacob Saylors
    '26328': [0.2, 0.0, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],  # Luke McCaffrey
    '27102': [1.7, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Dylan Sampson
    '24334': [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Ja'Tavion Sanders
    '17598': [0.0, 0.1, 0.1, 0.2, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Tyler Conklin
    '27097': [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1],  # DJ Giddens
    '22728': [0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Michael Carter
    '25983': [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1],  # Rasheen Ali
    '23249': [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1],  # Kene Nwangwu
    '28126': [0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Skyler Bell
    '20114': [0.1, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Elijah Moore
    '27093': [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1],  # Deion Burks
    '24367': [0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1],  # Adam Randall
    '28138': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5, 0.5],  # De'Zhaun Stribling
    '28297': [0.0, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Eli Heidenreich
    '27993': [0.1, 0.0, 0.0, 0.1, 0.1, 0.0, 0.0, 0.0, 0.0, 0.1, 0.0, 0.1],  # Max Klare
    '28434': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Max Bredeson
    '23020': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jayden Reed
    '10007': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Tyrod Taylor
    '28125': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # CJ Daniels
    '19198': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Tua Tagovailoa
    '23893': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Michael Woods II
    '26444': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Charlie Smyth
    '19624': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Antonio Gibson
    '25387': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Roman Hemby
    '22928': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Phil Mafah
    '26329': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jawhar Jordan
    '25282': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Luke Musgrave
    '26964': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Terrell Jennings
    '16420': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Joe Mixon
    '19483': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Van Jefferson
    '27444': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Josh Williams
    '13274': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Brandon McManus
    '19844': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Reggie Gilliam
    '24205': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Malik Davis
    '15802': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Tyreek Hill
    '24333': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Isiah Pacheco
    '25627': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Xavier Smith
    '27798': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Ben Sauls
    '26748': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # British Brooks
    '18487': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Josh Oliver
    '25654': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Emari Demercado
    '18557': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jake Browning
    '22913': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Tyquan Thornton
    '26392': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Isaac Guerendo
    '23680': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Quinn Ewers
    '28124': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Cash Jones
    '16548': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Zane Gonzalez
    '23093': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Moliki Matavao
    '24361': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Cade Klubnik
    '23682': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Devin Neal
    '16425': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Kareem Hunt
    '25748': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Zavier Scott
    '24901': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Bam Knight
    '25980': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Frank Gore Jr.
    '25388': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Trey Benson
    '20155': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jaret Patterson
    '25976': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Damien Martinez
    '12127': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Odell Beckham Jr.
    '19747': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Nick Westbrook-Ikhine
    '24347': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Anthony Richardson Sr.
    '18226': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Hollywood Brown
    '16445': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jeremy McNichols
    '25368': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Ty Simpson
    '23160': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jaxson Dart
    '27326': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jam Miller
    '20082': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Trey Lance
    '24345': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Drew Allar
    '24344': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jaydon Blue
    '28404': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Robert Henry Jr.
    '19781': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Justin Fields
    '14191': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Michael Burton
    '27066': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Xavier Restrepo
    '20156': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Mac Jones
    '23451': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Adam Prentice
    '25967': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Joe Milton III
    '25321': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Tyson Bagent
    '28109': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # J'Mari Taylor
    '24778': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jake Tonges
    '18562': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Gardner Minshew II
    '19252': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Brandon Aiyuk
    '22953': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Carson Beck
    '17508': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Durham Smythe
    '24335': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jarquez Hunter
    '25394': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jaydn Ott
    '27294': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jimmy Horn Jr.
    '24214': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Velus Jones Jr.
    '23136': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # De'Von Achane
    '24362': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Kaytron Allen
    '28115': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Noah Whittington
    '9300': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Joe Flacco
    '13971': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Tyler Lockett
    '22921': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jerome Ford
    '11606': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # DeAndre Hopkins
    '25443': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Lew Nichols III
    '28047': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Chris Brazzell II
    '19219': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jalen Reagor
    '22684': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Chris Blair
    '22763': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Khalil Herbert
    '25373': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Riley Leonard
    '24009': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jake Moody
    '27454': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jaylin Lane
    '13890': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Marcus Mariota
    '25287': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Cedric Tillman
}
