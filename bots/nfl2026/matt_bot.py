"""
matt_bot 2026 -- best-ball marginal-value drafter with one-pick lookahead.

The draft happens after week 5 and is scored as best-ball: every week 1-17 the
engine builds our optimal lineup from the whole roster (bench included). So a
player's worth is how much they raise that season total given who we already have.

Player value, per week:
  - weeks already played: actual PPR points from weekly_stats
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
PPR = 1.0              # points per reception (embedded projections are std + rec, so this applies to them too)
W_EARLY = 0.25         # weight on our trimmed early-season PPG vs expert projections
W_PRESEASON = 0.0      # weight on preseason PPG (external_projections week 0)
W_MARKET_OURS = 0.5    # opponent model: blend of our value rank vs players.rank (ADP)
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
    """{pid: {week: points}} for played weeks, and the last played week."""
    df = pd.read_sql(
        "SELECT fantasypros_id, CAST(week AS INTEGER) AS week, position, "
        "fantasy_points, receptions, FPTS "
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
        if r.position in ("K", "DST"):
            pts = r.FPTS or 0.0
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
        ext = EXTERNAL_WEEKLY_STD.get(pid)
        ext_rec = EXTERNAL_WEEKLY_REC.get(pid)

        # expert weekly projections for future weeks
        future = {}
        for w in range(last_week + 1, SEASON_WEEKS + 1):
            v = None
            if ext is not None and w >= EXTERNAL_PROJ_FIRST_WEEK:
                idx = w - EXTERNAL_PROJ_FIRST_WEEK
                if idx < len(ext):
                    v = ext[idx] + (PPR * ext_rec[idx] if ext_rec else 0.0)
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
    # Never roster a backup K/DST: in best ball a bench RB/WR's weekly upside is
    # worth more than a second DST's bye-week cover.
    capped = {p for p in DEFERRABLE
              if list(mine["pos"]).count(p) >= int(slots_cfg.get(p, 0))}
    pool = pool[~pool["pos"].isin(capped)]
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
# fantasypros_id -> [wk6..wk17], avg of Sleeper(Rotowire) + ESPN, pulled 2026-10-14.
# Projected points = standard points + PPR * receptions.
EXTERNAL_WEEKLY_STD = {
    '22968': [0.0, 21.4, 18.6, 22.1, 20.2, 19.9, 21.9, 19.6, 21.4, 18.0, 22.3, 21.3],  # Jahmyr Gibbs
    '17298': [25.0, 0.0, 23.7, 20.6, 23.6, 26.4, 22.6, 22.9, 24.2, 24.6, 22.3, 26.0],  # Josh Allen
    '23021': [19.0, 15.6, 17.4, 18.4, 17.2, 20.0, 18.3, 17.0, 18.9, 18.0, 16.7, 17.9],  # Kenneth Walker III
    '23133': [21.0, 17.4, 17.5, 20.2, 17.6, 0.0, 16.3, 21.0, 18.6, 19.2, 18.1, 20.4],  # Bijan Robinson
    '23070': [13.9, 14.0, 15.1, 16.0, 15.0, 0.0, 13.7, 16.1, 14.7, 14.0, 14.1, 14.3],  # Jaxon Smith-Njigba
    '17233': [20.1, 22.1, 22.6, 20.0, 21.7, 21.6, 20.9, 0.0, 21.3, 21.3, 21.2, 21.8],  # Lamar Jackson
    '23180': [15.7, 14.1, 13.7, 14.7, 15.0, 0.0, 14.0, 13.1, 12.8, 15.2, 12.5, 13.5],  # Puka Nacua
    '16393': [17.3, 16.6, 0.0, 18.4, 18.4, 15.1, 15.2, 17.8, 16.4, 16.8, 15.4, 16.4],  # Christian McCaffrey
    '19797': [21.5, 20.4, 0.0, 23.0, 23.8, 18.8, 19.8, 21.7, 20.4, 21.2, 19.9, 20.8],  # Brock Purdy
    '16413': [19.0, 16.9, 17.5, 19.2, 17.9, 21.9, 20.0, 17.5, 18.8, 18.4, 17.9, 18.7],  # Patrick Mahomes II
    '23046': [20.5, 20.3, 22.4, 21.0, 22.9, 0.0, 19.9, 21.8, 17.5, 18.4, 19.9, 19.1],  # Drake Maye
    '19780': [17.8, 0.0, 20.8, 19.2, 20.0, 19.9, 20.4, 19.9, 19.9, 19.0, 22.1, 21.9],  # Trevor Lawrence
    '22900': [17.5, 18.7, 19.2, 17.7, 19.4, 18.8, 18.3, 15.7, 19.9, 19.1, 18.6, 17.1],  # Bryce Young
    '19788': [0.0, 12.6, 13.3, 12.5, 13.1, 13.8, 13.3, 12.3, 12.3, 12.6, 13.2, 12.8],  # Ja'Marr Chase
    '19799': [0.0, 13.0, 11.5, 13.8, 12.2, 12.8, 13.3, 12.3, 13.1, 11.3, 12.8, 13.1],  # Amon-Ra St. Brown
    '19196': [0.0, 18.7, 20.0, 18.3, 19.4, 21.1, 20.2, 18.3, 18.0, 18.8, 19.9, 19.2],  # Joe Burrow
    '22902': [18.3, 0.0, 19.0, 18.7, 20.2, 20.3, 22.4, 19.9, 19.3, 19.1, 16.4, 17.2],  # Jayden Daniels
    '19217': [18.1, 13.7, 13.6, 18.3, 18.0, 14.8, 17.9, 0.0, 15.1, 16.3, 17.7, 16.0],  # Jonathan Taylor
    '19202': [12.2, 11.6, 13.4, 12.5, 11.6, 12.5, 11.7, 11.2, 0.0, 11.6, 11.8, 12.2],  # CeeDee Lamb
    '23059': [17.7, 16.2, 15.7, 15.6, 16.4, 0.0, 16.0, 14.3, 13.7, 17.1, 13.2, 14.2],  # Kyren Williams
    '15514': [17.2, 18.9, 17.9, 15.5, 18.0, 18.5, 16.1, 0.0, 16.8, 16.5, 18.1, 18.2],  # Derrick Henry
    '15501': [0.0, 19.5, 16.4, 20.9, 18.1, 18.8, 19.3, 17.7, 19.4, 16.1, 19.2, 19.0],  # Jared Goff
    '19246': [21.0, 20.2, 17.8, 16.7, 14.9, 0.0, 15.9, 18.3, 19.3, 20.5, 17.7, 17.4],  # Jordan Love
    '23084': [17.1, 17.2, 15.7, 17.6, 0.0, 18.5, 20.0, 16.2, 19.8, 18.7, 18.4, 20.4],  # Caleb Williams
    '22916': [11.3, 12.6, 12.6, 12.0, 12.3, 12.1, 12.2, 0.0, 12.4, 12.5, 12.0, 12.5],  # Zay Flowers
    '15600': [17.9, 17.1, 21.0, 18.6, 17.1, 18.7, 17.3, 16.2, 0.0, 16.7, 16.7, 18.5],  # Dak Prescott
    '19275': [16.6, 20.0, 18.7, 17.9, 0.0, 17.2, 19.5, 19.7, 18.3, 15.2, 16.7, 15.4],  # Jalen Hurts
    '19236': [0.0, 11.6, 12.0, 11.5, 11.1, 10.3, 10.8, 10.9, 10.5, 12.2, 12.1, 10.9],  # Justin Jefferson
    '22739': [14.2, 14.3, 16.7, 16.2, 14.1, 15.9, 14.8, 13.0, 0.0, 14.3, 13.5, 16.8],  # Javonte Williams
    '9451': [20.7, 17.8, 16.8, 18.4, 19.4, 0.0, 17.3, 15.6, 15.3, 19.3, 14.7, 16.3],  # Matthew Stafford
    '22936': [9.5, 9.2, 11.2, 8.8, 9.4, 9.1, 11.1, 9.6, 0.0, 10.0, 10.1, 10.5],  # Trey McBride
    '22958': [16.6, 0.0, 15.3, 13.9, 15.6, 17.4, 14.8, 15.3, 15.9, 16.8, 15.5, 16.9],  # James Cook III
    '20111': [10.9, 11.3, 0.0, 10.5, 10.6, 11.1, 11.0, 11.1, 10.5, 10.8, 12.1, 10.4],  # Chris Olave
    '23096': [16.9, 17.0, 0.0, 16.6, 17.0, 17.2, 16.8, 17.8, 16.6, 16.6, 20.2, 15.8],  # Tyler Shough
    '22955': [11.6, 10.6, 10.9, 10.2, 10.1, 10.0, 10.3, 0.0, 10.9, 10.1, 11.2, 12.0],  # Brock Bowers
    '20130': [11.0, 11.4, 0.0, 11.2, 10.8, 11.7, 11.2, 11.4, 12.1, 10.9, 10.8, 11.4],  # Nico Collins
    '18600': [0.0, 17.4, 18.7, 17.7, 16.5, 14.8, 15.6, 16.5, 15.3, 19.1, 18.3, 16.0],  # Kyler Murray
    '12119': [11.7, 11.8, 0.0, 12.4, 13.1, 10.9, 11.2, 11.9, 11.6, 11.9, 11.4, 11.7],  # Mike Evans
    '25417': [9.7, 10.5, 10.6, 9.7, 10.6, 10.4, 10.3, 9.2, 10.7, 10.7, 10.5, 9.6],  # Tetairoa McMillan
    '19792': [12.9, 12.7, 13.4, 13.1, 13.8, 12.5, 12.1, 11.2, 14.3, 14.3, 12.3, 11.5],  # Chuba Hubbard
    '25989': [12.7, 12.5, 12.7, 11.4, 11.2, 12.2, 12.6, 0.0, 13.0, 12.5, 13.3, 13.8],  # Ashton Jeanty
    '23163': [11.6, 10.2, 10.7, 11.0, 10.2, 0.0, 9.6, 11.7, 10.3, 11.6, 10.8, 10.9],  # Drake London
    '22910': [13.7, 18.7, 14.7, 15.5, 0.0, 17.6, 15.6, 18.7, 15.7, 17.2, 17.4, 14.9],  # Bo Nix
    '15642': [14.9, 15.1, 18.8, 13.9, 15.1, 14.5, 18.7, 15.7, 0.0, 16.6, 16.9, 18.1],  # Jacoby Brissett
    '23106': [10.3, 0.0, 11.5, 10.9, 11.2, 11.0, 11.4, 11.3, 11.3, 10.9, 12.0, 12.0],  # Parker Washington
    '12123': [12.1, 11.3, 11.0, 11.9, 12.1, 0.0, 11.3, 10.5, 10.4, 12.2, 10.1, 11.0],  # Davante Adams
    '23113': [9.4, 8.7, 8.8, 9.6, 9.2, 10.6, 9.8, 9.1, 9.7, 9.3, 9.1, 9.4],  # Rashee Rice
    '23886': [12.5, 11.9, 11.0, 10.6, 10.0, 0.0, 10.5, 11.2, 11.4, 12.1, 11.2, 11.0],  # Christian Watson
    '23071': [14.5, 16.4, 0.0, 15.7, 15.0, 16.8, 15.8, 15.7, 17.6, 14.4, 14.9, 16.2],  # C.J. Stroud
    '25324': [0.0, 10.8, 12.1, 11.1, 11.4, 11.9, 12.5, 11.6, 10.7, 12.3, 12.4, 11.2],  # Chase Brown
    '9001': [15.4, 16.9, 15.5, 0.0, 15.5, 15.0, 14.9, 15.7, 14.0, 16.0, 16.0, 16.3],  # Aaron Rodgers
    '23072': [9.1, 10.6, 9.9, 8.8, 9.8, 9.3, 10.5, 0.0, 8.8, 10.6, 9.1, 8.4],  # Garrett Wilson
    '24209': [12.2, 12.9, 12.3, 0.0, 12.9, 11.3, 12.0, 11.6, 10.3, 11.6, 13.1, 12.2],  # Jaylen Warren
    '17236': [13.9, 14.2, 15.8, 18.2, 16.4, 0.0, 13.9, 17.9, 15.8, 14.3, 14.2, 14.9],  # Sam Darnold
    '16398': [15.0, 15.5, 14.7, 16.0, 14.8, 0.0, 10.4, 16.1, 16.5, 16.2, 15.3, 18.8],  # Deshaun Watson
    '25981': [12.3, 13.0, 11.9, 12.7, 0.0, 13.3, 13.4, 12.5, 11.2, 13.4, 11.4, 11.7],  # Bucky Irving
    '26403': [8.6, 9.2, 9.2, 8.6, 9.3, 9.1, 9.0, 8.1, 9.4, 9.3, 9.2, 8.5],  # Jalen Coker
    '25409': [10.2, 9.2, 0.0, 8.9, 10.6, 8.9, 9.9, 8.8, 8.3, 9.1, 10.6, 10.7],  # Malik Nabers
    '23499': [0.0, 14.4, 14.1, 16.9, 15.1, 15.3, 14.7, 13.3, 15.1, 14.8, 14.7, 15.7],  # Malik Willis
    '19211': [0.0, 9.7, 10.3, 9.7, 10.1, 10.7, 10.2, 9.5, 9.4, 9.7, 10.2, 9.9],  # Tee Higgins
    '19210': [12.0, 12.2, 10.5, 11.7, 0.0, 13.2, 13.1, 10.8, 13.1, 12.4, 12.7, 13.6],  # D'Andre Swift
    '16499': [9.8, 9.8, 0.0, 10.2, 10.7, 9.0, 9.3, 9.8, 9.5, 9.8, 9.4, 9.6],  # George Kittle
    '25333': [9.0, 8.9, 10.7, 8.5, 9.1, 8.8, 10.7, 9.3, 0.0, 9.7, 9.8, 10.1],  # Michael Wilson
    '11687': [13.4, 16.7, 15.8, 12.8, 15.6, 14.1, 16.3, 0.0, 13.2, 16.8, 13.8, 11.9],  # Geno Smith
    '18218': [4.0, 10.6, 11.0, 10.4, 11.1, 0.0, 10.0, 10.5, 9.1, 9.5, 10.0, 9.5],  # A.J. Brown
    '22973': [15.6, 13.4, 13.9, 14.2, 13.3, 0.0, 11.9, 16.5, 13.5, 15.7, 14.2, 14.8],  # Michael Penix Jr.
    '24755': [14.1, 13.1, 13.5, 0.0, 12.2, 15.8, 11.8, 15.7, 15.8, 14.4, 14.7, 13.8],  # Cam Ward
    '19222': [8.0, 9.6, 9.2, 8.6, 0.0, 8.7, 9.4, 9.5, 8.9, 7.8, 8.4, 7.9],  # DeVonta Smith
    '26024': [10.7, 10.3, 9.3, 8.9, 8.3, 0.0, 8.8, 9.5, 9.7, 10.5, 9.6, 9.3],  # Matthew Golden
    '18635': [12.3, 0.0, 12.8, 14.0, 13.8, 14.5, 13.8, 13.7, 15.5, 13.2, 16.4, 13.1],  # Justin Herbert
    '27297': [10.7, 0.0, 13.0, 11.0, 11.9, 12.8, 12.3, 12.3, 11.6, 10.9, 12.8, 12.3],  # Bhayshul Tuten
    '17265': [9.8, 0.0, 9.4, 8.6, 9.5, 10.4, 9.1, 9.1, 9.6, 9.9, 8.8, 10.3],  # DJ Moore
    '25984': [0.0, 11.1, 11.4, 12.8, 12.2, 11.6, 11.6, 11.0, 12.2, 11.5, 11.7, 12.0],  # Ollie Gordon II
    '17240': [10.7, 12.7, 11.6, 12.6, 0.0, 10.9, 12.3, 12.0, 12.5, 9.7, 10.7, 9.8],  # Saquon Barkley
    '22963': [8.6, 8.2, 9.5, 8.8, 8.1, 8.8, 8.3, 7.9, 0.0, 8.1, 8.3, 8.6],  # George Pickens
    '22726': [13.9, 11.7, 10.4, 10.1, 10.6, 0.0, 9.6, 10.1, 8.6, 8.7, 9.5, 9.6],  # Rhamondre Stevenson
    '23781': [8.7, 7.6, 0.0, 7.4, 8.7, 7.5, 8.1, 7.3, 6.9, 7.5, 8.6, 8.8],  # Isaiah Likely
    '22982': [0.0, 12.8, 12.7, 10.1, 12.1, 11.3, 12.4, 0.0, 11.4, 12.4, 11.4, 10.0],  # Breece Hall
    '17237': [0.0, 14.0, 13.7, 14.6, 0.0, 16.6, 14.3, 14.5, 13.9, 15.1, 13.3, 13.3],  # Baker Mayfield
    '26009': [8.0, 7.5, 8.0, 0.0, 7.5, 8.7, 7.4, 8.7, 8.6, 8.1, 8.0, 8.1],  # Carnell Tate
    '18232': [14.1, 10.2, 11.1, 15.3, 15.2, 12.3, 13.4, 0.0, 11.9, 13.2, 13.1, 12.1],  # Daniel Jones
    '11594': [7.4, 6.9, 7.0, 7.6, 7.3, 8.4, 7.7, 7.2, 7.7, 7.3, 7.2, 7.5],  # Travis Kelce
    '25391': [8.8, 9.9, 9.1, 10.6, 9.2, 0.0, 11.2, 11.0, 9.6, 10.7, 9.0, 11.1],  # Quinshon Judkins
    '22978': [0.0, 7.9, 7.0, 8.5, 7.4, 7.8, 8.1, 7.5, 8.0, 6.9, 7.8, 8.0],  # Sam LaPorta
    '27016': [7.6, 7.5, 7.1, 7.8, 0.0, 8.0, 8.5, 7.5, 8.5, 7.9, 8.0, 8.6],  # Luther Burden III
    '26434': [7.1, 5.6, 6.0, 7.4, 7.3, 6.3, 6.5, 0.0, 6.1, 6.6, 6.7, 6.1],  # Tyler Warren
    '11177': [18.0, 15.4, 16.3, 14.9, 14.8, 7.8, 8.3, 0.0, 8.7, 7.8, 9.0, 10.3],  # Kirk Cousins
    '18705': [10.6, 10.2, 10.5, 0.0, 8.3, 10.9, 8.0, 10.4, 10.8, 11.1, 10.7, 9.6],  # Tony Pollard
    '19790': [6.4, 8.2, 6.8, 7.0, 0.0, 7.6, 7.4, 8.3, 7.2, 7.5, 7.5, 6.8],  # Jaylen Waddle
    '18466': [7.4, 0.0, 7.6, 7.5, 7.8, 8.1, 8.7, 8.0, 7.8, 7.7, 6.9, 7.4],  # Terry McLaurin
    '27166': [11.3, 9.0, 0.0, 9.1, 10.4, 8.4, 10.6, 8.8, 8.1, 10.1, 10.8, 10.9],  # Cam Skattebo
    '25403': [8.8, 9.6, 10.6, 7.9, 9.2, 8.4, 10.0, 9.2, 0.0, 9.7, 10.2, 10.7],  # Jeremiyah Love
    '18219': [8.0, 8.5, 7.5, 0.0, 7.8, 7.3, 7.2, 7.7, 7.2, 7.7, 7.7, 7.9],  # DK Metcalf
    '27339': [7.6, 7.8, 7.6, 7.9, 7.6, 0.0, 8.1, 7.8, 7.5, 7.6, 7.5, 8.1],  # Denzel Boston
    '26122': [5.9, 0.0, 7.0, 7.5, 7.3, 7.5, 7.2, 7.4, 7.7, 7.0, 8.3, 7.1],  # Ladd McConkey
    '25298': [7.6, 7.2, 6.5, 6.2, 5.8, 0.0, 6.2, 6.7, 6.8, 7.4, 6.8, 6.6],  # Tucker Kraft
    '23062': [6.9, 7.0, 7.0, 7.5, 0.0, 8.0, 7.1, 7.2, 7.1, 7.4, 6.9, 6.9],  # Emeka Egbuka
    '23677': [0.0, 7.6, 6.7, 8.1, 7.1, 7.5, 7.7, 7.2, 7.6, 6.6, 7.5, 7.7],  # Jameson Williams
    '17270': [6.5, 7.5, 7.3, 6.8, 0.0, 6.9, 7.5, 7.5, 7.1, 6.3, 6.8, 6.4],  # Dallas Goedert
    '27142': [6.3, 7.5, 6.8, 7.6, 0.0, 7.9, 7.1, 8.1, 7.2, 7.7, 7.6, 7.0],  # RJ Harvey
    '23019': [6.6, 6.0, 6.1, 6.7, 6.4, 7.5, 6.8, 6.3, 6.9, 6.5, 6.3, 6.6],  # Xavier Worthy
    '27050': [6.5, 6.8, 6.6, 6.7, 6.6, 0.0, 7.0, 6.8, 6.4, 6.5, 6.4, 7.0],  # Harold Fannin Jr.
    '25411': [7.0, 6.9, 6.6, 7.3, 0.0, 7.3, 7.8, 7.0, 7.9, 7.3, 7.4, 7.9],  # Rome Odunze
    '25345': [6.0, 0.0, 6.7, 6.3, 6.6, 6.4, 6.7, 6.6, 6.6, 6.3, 7.1, 7.0],  # Brenton Strange
    '18239': [0.0, 11.1, 0.0, 9.8, 9.9, 11.1, 9.5, 9.3, 10.1, 8.6, 9.2, 10.1],  # David Montgomery
    '18290': [0.0, 5.9, 6.2, 5.8, 5.6, 5.1, 5.4, 5.4, 5.2, 6.3, 6.2, 5.5],  # T.J. Hockenson
    '19562': [6.4, 6.7, 0.0, 6.1, 6.2, 6.6, 6.4, 6.5, 6.1, 6.3, 7.1, 6.1],  # Juwan Johnson
    '13981': [6.3, 0.0, 6.4, 6.3, 6.6, 6.8, 7.4, 6.8, 6.6, 6.4, 5.7, 6.2],  # Stefon Diggs
    '23152': [0.0, 7.5, 10.0, 10.8, 10.2, 0.0, 8.3, 10.8, 10.5, 8.9, 9.2, 9.9],  # Zach Charbonnet
    '26314': [0.0, 6.5, 6.3, 7.3, 6.8, 6.7, 6.7, 6.0, 6.9, 6.7, 6.6, 6.8],  # Malik Washington
    '17269': [5.9, 6.7, 6.6, 6.3, 6.5, 6.3, 6.5, 0.0, 6.6, 6.6, 6.3, 6.7],  # Mark Andrews
    '16673': [0.0, 8.5, 8.4, 8.0, 7.7, 6.7, 7.4, 8.4, 7.3, 8.6, 8.1, 7.5],  # Aaron Jones Sr.
    '22985': [5.9, 5.4, 5.8, 0.0, 5.4, 6.4, 5.4, 6.4, 6.3, 5.9, 5.9, 5.9],  # Wan'Dale Robinson
    '26341': [0.0, 10.4, 10.2, 10.4, 10.0, 9.9, 10.2, 10.3, 9.9, 10.5, 10.3, 9.9],  # Will Reichard
    '26763': [10.7, 9.7, 9.8, 10.5, 10.5, 9.9, 10.3, 0.0, 10.0, 10.1, 10.2, 10.0],  # Spencer Shrader
    '24706': [6.7, 5.2, 5.5, 6.6, 6.5, 5.7, 5.8, 0.0, 5.4, 6.0, 6.0, 5.5],  # Josh Downs
    '23107': [0.0, 6.8, 7.1, 6.7, 6.5, 5.9, 6.3, 6.4, 6.1, 7.2, 7.1, 6.3],  # Jordan Addison
    '25395': [7.1, 0.0, 8.3, 8.5, 8.3, 9.1, 9.0, 8.0, 9.8, 8.1, 9.8, 8.2],  # Omarion Hampton
    '20163': [5.8, 6.5, 5.8, 0.0, 6.0, 5.7, 5.6, 6.0, 5.6, 6.0, 6.0, 6.2],  # Pat Freiermuth
    '15756': [9.5, 10.0, 0.0, 9.7, 9.7, 10.2, 9.9, 9.6, 9.9, 9.7, 9.7, 9.8],  # Ka'imi Fairbairn
    '19231': [0.0, 0.0, 0.0, 9.5, 10.2, 9.7, 10.0, 9.7, 9.9, 8.5, 10.3, 8.7],  # Travis Etienne Jr.
    '13891': [15.6, 12.8, 0.0, 12.6, 15.8, 12.1, 6.3, 5.7, 5.5, 5.9, 7.6, 7.4],  # Jameis Winston
    '19245': [7.7, 9.6, 8.1, 8.7, 0.0, 9.3, 7.6, 9.3, 8.0, 8.9, 8.7, 7.8],  # J.K. Dobbins
    '18598': [5.7, 0.0, 6.4, 6.0, 6.3, 6.1, 6.3, 6.3, 6.2, 6.0, 6.8, 6.7],  # Jakobi Meyers
    '27331': [5.1, 5.7, 5.5, 5.7, 5.4, 0.0, 6.0, 5.7, 5.4, 5.5, 5.3, 6.0],  # KC Concepcion
    '27131': [6.3, 8.4, 7.2, 8.0, 0.0, 9.2, 9.0, 7.4, 9.0, 8.5, 8.7, 9.3],  # Kyle Monangai
    '26068': [9.5, 9.4, 9.9, 9.6, 9.5, 9.7, 9.6, 9.2, 0.0, 9.4, 9.4, 9.7],  # Brandon Aubrey
    '26006': [5.7, 5.5, 5.2, 5.9, 0.0, 5.8, 6.3, 5.6, 6.4, 5.8, 5.9, 6.3],  # Colston Loveland
    '16712': [8.7, 8.3, 8.4, 8.7, 8.5, 9.0, 8.8, 8.4, 8.4, 8.7, 8.5, 8.5],  # Harrison Butker
    '28013': [0.0, 0.0, 0.0, 0.0, 0.0, 15.4, 15.9, 0.0, 17.2, 16.4, 17.8, 19.3],  # Fernando Mendoza
    '25247': [6.0, 0.0, 5.7, 5.2, 5.8, 6.3, 5.5, 5.5, 5.9, 6.0, 5.4, 6.3],  # Dalton Kincaid
    '22969': [0.0, 8.8, 8.5, 8.3, 7.9, 7.0, 7.7, 8.7, 7.5, 8.8, 8.3, 7.7],  # Jordan Mason
    '26475': [8.9, 0.0, 9.3, 8.9, 9.0, 9.0, 9.2, 8.9, 9.0, 8.8, 9.1, 9.3],  # Cam Little
    '25533': [9.0, 9.1, 9.3, 9.1, 9.4, 0.0, 9.4, 9.0, 8.4, 9.8, 8.3, 8.5],  # Blake Grupe
    '28176': [9.2, 8.8, 0.0, 8.9, 9.3, 8.9, 9.1, 8.9, 8.7, 9.1, 9.2, 9.1],  # Dominic Zvada
    '25337': [6.5, 5.9, 6.1, 5.7, 5.6, 5.5, 5.7, 0.0, 6.1, 5.6, 6.3, 6.8],  # Tre Tucker
    '23794': [7.1, 7.2, 6.6, 6.0, 6.4, 0.0, 5.8, 6.1, 5.3, 5.5, 5.8, 5.5],  # Romeo Doubs
    '27534': [8.8, 9.0, 9.2, 8.9, 9.1, 9.0, 8.8, 0.0, 9.0, 8.8, 9.1, 8.8],  # Tyler Loop
    '17253': [5.2, 6.7, 5.5, 5.7, 0.0, 6.2, 6.0, 6.7, 5.9, 6.1, 6.1, 5.6],  # Courtland Sutton
    '27520': [10.2, 7.6, 0.0, 6.7, 6.7, 7.5, 6.5, 6.4, 6.9, 5.9, 6.3, 6.9],  # Woody Marks
    '18244': [0.0, 0.0, 0.0, 7.9, 8.2, 6.7, 6.9, 7.6, 7.3, 7.4, 7.0, 7.3],  # Deebo Samuel Sr.
    '14104': [4.8, 5.2, 5.2, 4.8, 5.2, 5.1, 5.1, 4.6, 5.2, 5.3, 5.2, 4.7],  # Darren Waller
    '14003': [8.2, 8.7, 8.8, 9.2, 8.7, 0.0, 8.3, 9.1, 8.9, 8.6, 8.7, 8.7],  # Jason Myers
    '27261': [7.8, 8.0, 8.0, 8.0, 8.1, 8.1, 7.8, 7.6, 8.4, 8.0, 7.8, 7.9],  # Ryan Fitzgerald
    '23748': [5.4, 0.0, 5.1, 4.6, 5.2, 5.8, 4.9, 4.9, 5.3, 5.4, 4.7, 5.7],  # Khalil Shakir
    '9491': [9.1, 8.5, 8.3, 8.5, 8.6, 0.0, 8.1, 9.0, 8.4, 8.6, 8.6, 9.0],  # Nick Folk
    '13029': [8.3, 8.9, 8.7, 0.0, 8.4, 8.5, 8.7, 8.6, 8.3, 8.7, 8.8, 8.6],  # Chris Boswell
    '16406': [4.8, 4.9, 4.9, 5.2, 0.0, 5.6, 5.0, 5.1, 4.9, 5.2, 4.8, 4.8],  # Chris Godwin Jr.
    '15561': [5.5, 5.4, 5.8, 5.4, 5.8, 0.0, 5.2, 5.4, 4.8, 4.9, 5.3, 4.9],  # Hunter Henry
    '26075': [0.0, 8.7, 8.4, 8.7, 8.5, 8.5, 8.6, 8.5, 8.7, 8.1, 8.7, 8.4],  # Jake Bates
    '26335': [4.9, 5.0, 5.6, 6.0, 5.5, 0.0, 4.9, 6.0, 5.4, 5.0, 5.1, 5.2],  # AJ Barner
    '26035': [0.0, 0.0, 0.0, 6.2, 6.4, 6.8, 6.7, 6.8, 6.4, 6.6, 7.4, 6.3],  # Jordyn Tyson
    '19760': [8.6, 0.0, 8.5, 8.0, 8.3, 8.8, 8.4, 8.3, 8.4, 8.5, 8.3, 8.6],  # Tyler Bass
    '23018': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 16.5, 13.9, 12.1, 15.1, 17.3, 17.5],  # J.J. McCarthy
    '23791': [0.0, 0.0, 5.8, 7.6, 7.5, 6.8, 7.0, 0.0, 6.6, 7.0, 7.1, 6.6],  # Alec Pierce
    '23901': [7.4, 0.0, 8.2, 8.4, 8.3, 8.5, 8.5, 8.2, 8.5, 8.4, 8.6, 8.4],  # Cameron Dicker
    '28175': [7.8, 0.0, 8.5, 8.4, 8.4, 8.4, 8.6, 8.4, 8.4, 8.5, 7.9, 8.1],  # Drew Stevens
    '13731': [8.3, 8.2, 7.8, 8.1, 0.0, 8.5, 8.3, 8.0, 8.3, 8.3, 8.3, 8.5],  # Cairo Santos
    '23065': [0.0, 4.4, 8.5, 8.2, 8.6, 0.0, 7.8, 8.2, 7.1, 7.0, 7.8, 7.9],  # TreVeyon Henderson
    '17575': [8.7, 8.1, 0.0, 8.5, 8.3, 7.9, 8.1, 8.1, 8.2, 8.1, 7.9, 8.3],  # Eddy Pineiro
    '28507': [8.8, 8.2, 8.3, 7.9, 7.8, 0.0, 7.9, 8.2, 8.4, 8.4, 7.9, 8.0],  # Trey Smack
    '17349': [4.3, 4.5, 0.0, 4.4, 4.2, 4.7, 4.4, 4.6, 4.9, 4.3, 4.2, 4.6],  # Dalton Schultz
    '11616': [5.1, 4.0, 4.1, 5.1, 5.0, 4.3, 4.4, 0.0, 4.1, 4.5, 4.5, 4.1],  # Keenan Allen
    '19058': [7.8, 8.1, 8.2, 7.9, 0.0, 8.3, 8.3, 8.2, 7.9, 8.5, 8.0, 8.1],  # Chase McLaughlin
    '16421': [7.5, 7.2, 0.0, 5.4, 5.7, 5.6, 5.7, 5.5, 5.6, 5.0, 6.0, 5.0],  # Alvin Kamara
    '25323': [5.7, 5.4, 5.6, 0.0, 4.8, 6.0, 4.6, 5.7, 5.9, 5.9, 5.7, 5.3],  # Tyjae Spears
    '23310': [5.6, 5.6, 5.2, 5.8, 0.0, 6.0, 5.8, 5.5, 5.1, 5.7, 5.1, 5.2],  # Kenny Gainwell
    '16026': [7.6, 8.2, 8.0, 8.0, 0.0, 8.2, 7.8, 8.3, 7.9, 8.0, 8.3, 7.9],  # Wil Lutz
    '23181': [4.3, 4.3, 4.3, 4.7, 0.0, 4.9, 4.3, 4.5, 4.4, 4.6, 4.2, 4.2],  # Cade Otton
    '18269': [0.0, 1.3, 5.0, 4.4, 3.9, 0.0, 8.7, 9.9, 9.8, 10.4, 9.7, 8.9],  # Josh Jacobs
    '26398': [4.9, 4.6, 5.5, 5.0, 4.6, 5.1, 4.7, 4.4, 0.0, 4.6, 4.7, 4.9],  # Ryan Flournoy
    '26039': [6.2, 6.4, 4.6, 0.0, 4.8, 4.6, 4.5, 4.7, 4.4, 4.8, 4.8, 4.9],  # Germie Bernard
    '23297': [0.0, 7.6, 7.9, 7.8, 7.8, 7.8, 8.1, 7.6, 7.8, 7.8, 7.9, 7.9],  # Evan McPherson
    '22718': [4.5, 4.4, 5.2, 4.8, 4.4, 4.8, 4.4, 4.2, 0.0, 4.4, 4.5, 4.6],  # Jake Ferguson
    '27316': [6.8, 0.0, 7.1, 7.1, 8.0, 8.1, 7.9, 7.4, 6.9, 7.2, 6.1, 5.9],  # Jacory Croskey-Merritt
    '20164': [5.4, 4.6, 4.9, 5.0, 4.6, 0.0, 4.3, 5.3, 4.6, 5.2, 4.9, 5.0],  # Kyle Pitts Sr.
    '24687': [4.8, 5.1, 5.6, 6.1, 5.6, 0.0, 4.9, 6.1, 5.5, 5.1, 5.2, 5.3],  # Rashid Shaheed
    '24354': [4.7, 5.4, 5.1, 4.8, 0.0, 4.9, 5.3, 5.3, 5.0, 4.4, 4.7, 4.4],  # Dontayvion Wicks
    '23013': [7.5, 7.0, 6.7, 6.6, 7.0, 0.0, 6.9, 6.1, 5.8, 7.4, 5.5, 6.0],  # Blake Corum
    '28135': [0.0, 4.7, 4.6, 5.2, 4.9, 4.8, 4.8, 4.3, 5.0, 4.8, 4.7, 4.9],  # Caleb Douglas
    '17066': [4.2, 4.0, 5.0, 3.9, 4.2, 4.0, 5.0, 4.2, 0.0, 4.5, 4.5, 4.7],  # Kendrick Bourne
    '24346': [11.0, 6.0, 5.9, 4.7, 5.6, 5.3, 5.8, 0.0, 5.3, 5.8, 5.3, 4.6],  # Braelon Allen
    '26055': [3.5, 5.0, 4.6, 4.1, 4.6, 4.4, 5.0, 0.0, 4.1, 5.0, 4.3, 4.0],  # Kenyon Sadiq
    '24357': [0.0, 6.0, 5.6, 4.9, 5.6, 5.2, 5.9, 0.0, 4.9, 6.0, 5.1, 4.7],  # Adonai Mitchell
    '27291': [7.4, 7.2, 7.5, 7.5, 7.6, 0.0, 7.2, 7.7, 7.1, 7.1, 7.2, 7.4],  # Andy Borregales
    '25360': [5.7, 0.0, 5.3, 5.4, 5.3, 5.7, 5.6, 5.2, 6.0, 5.1, 6.0, 5.2],  # Keaton Mitchell
    '16081': [4.1, 4.0, 3.8, 4.2, 0.0, 4.3, 4.6, 4.0, 4.6, 4.3, 4.3, 4.6],  # Kalif Raymond
    '23056': [4.3, 3.9, 4.1, 3.7, 3.7, 3.6, 3.8, 0.0, 4.1, 3.7, 4.2, 4.6],  # Michael Mayer
    '17272': [0.0, 4.5, 4.7, 4.4, 4.7, 4.9, 4.7, 4.4, 4.3, 4.4, 4.7, 4.5],  # Mike Gesicki
    '26215': [0.0, 0.0, 4.6, 5.0, 4.8, 5.0, 4.7, 5.0, 5.1, 4.6, 5.6, 4.6],  # Tre' Harris
    '27329': [0.0, 0.0, 9.6, 7.9, 7.6, 0.0, 6.1, 8.0, 7.9, 6.6, 6.9, 7.4],  # Jadarian Price
    '23982': [3.9, 0.0, 3.9, 3.9, 4.1, 4.3, 4.7, 4.2, 4.1, 4.0, 3.5, 3.9],  # Chig Okonkwo
    '23000': [4.4, 0.0, 4.9, 4.6, 4.9, 4.7, 4.9, 4.9, 4.9, 4.7, 5.3, 5.2],  # Brian Thomas Jr.
    '17420': [6.9, 7.0, 0.0, 7.2, 7.3, 7.0, 6.9, 7.3, 7.1, 6.9, 7.5, 7.0],  # Daniel Carlson
    '8260': [7.2, 5.7, 6.5, 8.0, 7.1, 0.0, 6.5, 7.1, 8.8, 7.7, 6.6, 6.9],  # Seattle Seahawks
    '26019': [4.9, 0.0, 4.7, 4.3, 4.7, 5.2, 4.5, 4.5, 4.8, 4.9, 4.4, 5.1],  # Keon Coleman
    '26409': [6.5, 6.7, 0.0, 3.6, 3.7, 3.9, 3.8, 3.9, 3.7, 3.7, 4.3, 3.6],  # Devaughn Vele
    '25354': [6.7, 7.1, 7.2, 6.7, 7.1, 6.8, 7.4, 7.2, 0.0, 7.1, 7.2, 7.4],  # Chad Ryland
    '19794': [4.4, 4.9, 4.9, 4.6, 4.8, 4.7, 4.7, 0.0, 4.8, 4.8, 4.6, 4.9],  # Rashod Bateman
    '8120': [5.8, 7.7, 0.0, 7.7, 7.9, 7.4, 6.0, 7.3, 6.5, 7.0, 7.6, 6.7],  # Houston Texans
    '23123': [4.6, 0.0, 4.6, 4.9, 4.8, 5.0, 4.7, 4.9, 5.1, 4.6, 5.5, 4.6],  # Quentin Johnston
    '24172': [4.5, 4.8, 5.5, 4.1, 4.7, 4.3, 5.3, 4.7, 0.0, 5.0, 5.2, 5.5],  # Tyler Allgeier
    '17533': [6.7, 7.3, 7.2, 6.7, 7.3, 6.8, 7.1, 0.0, 7.0, 7.1, 7.0, 6.7],  # Jason Sanders
    '23891': [5.0, 0.0, 5.0, 4.9, 5.4, 5.6, 5.6, 5.2, 4.9, 5.0, 4.4, 4.3],  # Rachaad White
    '28896': [0.0, 0.0, 5.5, 0.0, 5.7, 5.4, 5.3, 5.6, 5.3, 5.7, 5.7, 5.8],  # Roman Wilson
    '16540': [6.7, 7.2, 6.9, 7.0, 0.0, 6.9, 7.0, 7.1, 7.2, 6.7, 6.8, 6.6],  # Jake Elliott
    '8280': [7.7, 6.2, 7.5, 6.1, 7.6, 0.0, 7.7, 6.6, 6.1, 6.3, 6.4, 7.3],  # Los Angeles Rams
    '19647': [5.4, 5.9, 5.7, 0.0, 6.0, 5.1, 5.5, 5.2, 4.6, 5.3, 6.1, 5.6],  # Rico Dowdle
    '23118': [9.7, 8.7, 7.0, 6.1, 5.6, 0.0, 3.7, 4.2, 4.1, 4.4, 4.2, 3.8],  # MarShawn Lloyd
    '8170': [0.0, 7.7, 5.8, 5.8, 7.1, 5.9, 6.4, 7.1, 7.1, 6.4, 7.3, 8.0],  # Minnesota Vikings
    '8150': [8.1, 6.0, 5.9, 7.3, 5.1, 6.9, 5.2, 5.5, 6.0, 6.5, 5.1, 7.0],  # Kansas City Chiefs
    '8140': [7.1, 0.0, 7.2, 5.2, 7.3, 7.1, 7.3, 6.2, 7.2, 6.8, 5.9, 6.6],  # Jacksonville Jaguars
    '8240': [7.6, 6.7, 8.0, 0.0, 5.6, 6.5, 6.7, 6.2, 5.7, 5.1, 6.5, 7.6],  # Pittsburgh Steelers
    '8020': [8.3, 5.6, 4.4, 5.9, 7.3, 6.1, 6.5, 0.0, 7.2, 6.7, 8.4, 5.7],  # Baltimore Ravens
    '23153': [0.0, 3.7, 3.6, 4.1, 3.8, 3.7, 3.8, 3.4, 3.9, 3.8, 3.7, 3.8],  # Greg Dulcich
    '22967': [3.7, 4.1, 3.8, 0.0, 3.9, 3.7, 3.6, 3.9, 3.6, 3.9, 3.9, 4.0],  # Darnell Washington
    '25420': [3.6, 4.3, 4.1, 3.8, 0.0, 3.9, 4.3, 4.3, 4.0, 3.4, 3.7, 3.4],  # Makai Lemon
    '8090': [6.0, 6.7, 5.1, 6.2, 0.0, 6.9, 6.9, 7.3, 7.2, 7.1, 4.8, 5.9],  # Denver Broncos
    '19028': [6.3, 6.4, 6.1, 0.0, 6.2, 6.4, 6.0, 6.5, 6.5, 6.6, 6.3, 6.3],  # Joey Slye
    '8180': [7.8, 5.0, 7.3, 6.4, 4.9, 0.0, 7.3, 5.3, 6.6, 4.9, 7.4, 6.5],  # New England Patriots
    '26038': [0.0, 4.0, 3.8, 4.5, 4.2, 4.1, 4.1, 3.7, 4.3, 4.1, 4.0, 4.1],  # Chris Bell
    '8230': [6.1, 5.2, 5.2, 7.1, 0.0, 6.7, 6.0, 7.0, 7.3, 6.5, 6.6, 4.3],  # Philadelphia Eagles
    '20094': [6.1, 5.0, 5.0, 5.9, 5.1, 0.0, 4.7, 6.2, 5.5, 5.6, 5.2, 6.2],  # Brian Robinson Jr.
    '16423': [0.0, 4.3, 4.8, 4.4, 4.5, 4.7, 4.9, 4.5, 4.2, 4.8, 4.9, 4.4],  # Samaje Perine
    '8270': [6.1, 5.2, 0.0, 6.7, 5.2, 5.9, 6.7, 6.9, 5.6, 6.8, 5.0, 7.1],  # San Francisco 49ers
    '16433': [3.7, 3.7, 4.1, 4.4, 4.1, 0.0, 3.6, 4.4, 4.0, 3.7, 3.7, 3.8],  # Cooper Kupp
    '8040': [6.5, 6.1, 5.1, 5.6, 5.6, 4.4, 5.7, 5.2, 5.6, 5.1, 5.7, 5.6],  # Carolina Panthers
    '8100': [0.0, 5.6, 6.1, 7.1, 5.8, 6.4, 5.6, 4.9, 6.9, 5.7, 6.9, 5.1],  # Detroit Lions
    '18406': [4.2, 0.0, 3.9, 3.6, 4.0, 4.3, 3.8, 3.9, 4.1, 4.3, 3.8, 4.3],  # Ty Johnson
    '18256': [4.1, 4.6, 4.3, 3.9, 4.3, 4.4, 4.1, 0.0, 4.2, 4.2, 4.3, 4.5],  # Justice Hill
    '8070': [4.9, 7.1, 6.1, 6.2, 5.9, 0.0, 6.4, 5.5, 4.7, 6.9, 4.2, 6.8],  # Cleveland Browns
    '27122': [3.7, 4.0, 0.0, 3.9, 3.8, 4.1, 3.9, 4.0, 4.2, 3.7, 3.7, 4.0],  # Jaylin Noel
    '27446': [0.0, 4.1, 3.6, 4.3, 3.9, 4.0, 4.2, 3.9, 4.1, 3.6, 4.1, 4.1],  # Isaac TeSlaa
    '8010': [5.4, 4.3, 6.3, 5.7, 4.9, 0.0, 5.9, 4.9, 7.0, 5.3, 7.1, 6.6],  # Atlanta Falcons
    '27994': [4.1, 3.6, 0.0, 3.5, 4.2, 3.5, 3.9, 3.5, 3.3, 3.6, 4.1, 4.2],  # Malachi Fields
    '16411': [2.8, 3.6, 3.0, 3.1, 0.0, 3.4, 3.2, 3.6, 3.1, 3.3, 3.3, 3.0],  # Evan Engram
    '8210': [5.1, 7.2, 6.3, 4.3, 4.2, 5.6, 6.1, 0.0, 5.3, 6.4, 5.3, 5.8],  # New York Jets
    '8130': [7.1, 5.0, 4.3, 4.5, 6.4, 4.8, 6.0, 0.0, 5.6, 6.5, 4.5, 6.8],  # Indianapolis Colts
    '8030': [5.5, 0.0, 4.5, 5.7, 6.4, 6.3, 4.8, 5.2, 5.2, 4.9, 5.7, 7.1],  # Buffalo Bills
    '8110': [5.0, 4.1, 5.8, 5.2, 6.1, 0.0, 5.3, 6.1, 5.0, 7.5, 4.9, 6.2],  # Green Bay Packers
    '8200': [6.0, 5.2, 0.0, 6.3, 5.3, 5.3, 6.2, 4.7, 5.6, 7.7, 4.0, 4.5],  # New York Giants
    '8050': [4.6, 5.8, 5.6, 6.6, 0.0, 5.9, 4.6, 5.4, 6.5, 4.4, 6.0, 4.9],  # Chicago Bears
    '8060': [0.0, 4.3, 6.7, 4.6, 6.1, 4.7, 5.9, 6.6, 4.7, 5.4, 6.5, 4.6],  # Cincinnati Bengals
    '19810': [3.9, 3.5, 0.0, 3.4, 4.1, 3.4, 3.8, 3.4, 3.2, 3.5, 4.1, 4.1],  # Darnell Mooney
    '8310': [3.6, 0.0, 6.5, 4.8, 6.2, 5.2, 6.2, 6.1, 5.5, 4.9, 5.9, 4.8],  # Washington Commanders
    '8290': [6.1, 5.6, 5.0, 4.9, 0.0, 4.6, 5.9, 6.5, 4.5, 6.6, 4.6, 4.8],  # Tampa Bay Buccaneers
    '25418': [3.3, 0.0, 3.4, 3.3, 3.4, 3.6, 3.9, 3.5, 3.5, 3.4, 3.0, 3.3],  # Antonio Williams
    '27059': [3.7, 3.5, 3.7, 0.0, 3.5, 4.0, 3.4, 4.0, 3.9, 3.7, 3.7, 3.7],  # Elic Ayomanor
    '24177': [3.9, 3.5, 3.6, 3.4, 3.4, 3.3, 3.4, 0.0, 3.6, 3.3, 3.7, 4.0],  # Jalen Nailor
    '8220': [3.7, 4.3, 6.2, 3.7, 5.1, 4.7, 6.0, 0.0, 5.7, 5.2, 6.7, 5.8],  # Las Vegas Raiders
    '22989': [3.1, 2.5, 0.0, 3.3, 3.2, 3.5, 3.4, 3.4, 3.7, 3.2, 3.2, 3.4],  # Kayshon Boutte
    '28107': [3.9, 3.2, 3.6, 3.8, 3.6, 4.2, 3.8, 3.5, 3.9, 3.7, 3.4, 3.7],  # Emmett Johnson
    '25987': [0.0, 4.3, 4.3, 4.9, 4.7, 4.4, 4.4, 4.2, 4.7, 4.4, 4.4, 4.5],  # Jaylen Wright
    '8080': [4.4, 5.6, 5.9, 5.4, 3.9, 5.8, 5.9, 5.0, 0.0, 4.2, 4.4, 6.2],  # Dallas Cowboys
    '8250': [3.8, 0.0, 4.2, 5.6, 4.1, 6.2, 5.5, 5.8, 5.7, 4.4, 6.9, 4.5],  # Los Angeles Chargers
    '22986': [4.2, 0.0, 5.2, 4.4, 4.7, 5.1, 4.9, 4.9, 4.6, 4.3, 5.1, 4.9],  # Chris Rodriguez Jr.
    '23101': [3.7, 3.3, 3.4, 3.5, 3.3, 0.0, 3.0, 3.8, 3.3, 3.7, 3.4, 3.6],  # Jahan Dotson
    '25304': [4.8, 4.2, 3.5, 3.1, 3.3, 0.0, 3.0, 3.2, 2.7, 2.8, 2.9, 2.8],  # DeMario Douglas
    '27109': [2.8, 2.6, 2.8, 0.0, 2.6, 3.0, 2.5, 3.0, 3.0, 2.8, 2.8, 2.8],  # Gunnar Helm
    '19372': [3.6, 3.1, 3.1, 3.3, 3.4, 0.0, 3.1, 2.9, 2.8, 3.4, 2.8, 3.1],  # Colby Parkinson
    '28130': [3.1, 3.1, 3.1, 3.3, 0.0, 3.5, 3.2, 3.2, 3.1, 3.3, 3.1, 3.1],  # Ted Hurst III
    '8300': [5.2, 7.2, 4.2, 0.0, 4.4, 4.0, 4.3, 4.3, 3.5, 5.7, 5.4, 5.4],  # Tennessee Titans
    '28108': [4.1, 4.3, 4.3, 3.9, 3.9, 4.2, 4.3, 0.0, 4.4, 4.3, 4.5, 4.6],  # Mike Washington Jr.
    '8190': [5.5, 4.9, 0.0, 6.6, 4.7, 4.1, 4.2, 4.4, 4.2, 4.9, 5.8, 4.0],  # New Orleans Saints
    '19302': [5.7, 4.1, 0.0, 3.8, 4.3, 3.5, 4.5, 3.7, 3.4, 4.3, 4.5, 4.6],  # Najee Harris
    '25997': [2.4, 0.0, 2.8, 3.0, 2.9, 3.1, 2.9, 3.1, 3.1, 2.8, 3.4, 2.8],  # Oronde Gadsden II
    '26355': [4.8, 0.0, 3.3, 3.4, 3.3, 3.6, 3.5, 3.3, 3.9, 3.3, 3.9, 3.3],  # Kimani Vidal
    '28137': [2.9, 0.0, 3.4, 3.1, 3.2, 3.2, 3.3, 3.3, 3.2, 3.1, 3.5, 3.4],  # Josh Cameron
    '17527': [2.7, 2.9, 0.0, 2.7, 2.7, 2.9, 2.9, 2.9, 2.7, 2.8, 3.1, 2.7],  # Noah Fant
    '25332': [2.8, 3.2, 0.0, 3.1, 3.0, 3.3, 3.1, 3.1, 3.3, 3.0, 3.0, 3.1],  # Xavier Hutchinson
    '28401': [5.1, 4.3, 0.0, 4.7, 4.6, 3.7, 3.7, 4.6, 4.1, 4.2, 3.7, 4.1],  # Kaelon Black
    '15623': [3.6, 2.6, 2.5, 2.6, 2.8, 0.0, 2.5, 2.4, 2.3, 2.8, 2.2, 2.4],  # Tyler Higbee
    '19201': [2.9, 2.8, 2.7, 2.9, 2.7, 0.0, 3.0, 2.8, 2.7, 2.7, 2.7, 3.0],  # Jerry Jeudy
    '28154': [3.1, 2.8, 3.0, 2.8, 3.0, 0.0, 2.7, 2.8, 2.4, 2.5, 2.7, 2.5],  # Eli Raridon
    '19539': [2.4, 2.1, 2.2, 2.4, 2.3, 2.7, 2.4, 2.2, 2.4, 2.3, 2.2, 2.4],  # Noah Gray
    '22756': [2.2, 0.0, 2.4, 2.5, 2.5, 2.5, 2.4, 2.5, 2.6, 2.4, 2.8, 2.4],  # Charlie Kolar
    '22795': [2.2, 2.3, 2.3, 2.1, 2.4, 2.3, 2.2, 1.9, 2.4, 2.3, 2.3, 2.1],  # Tommy Tremble
    '8000': [3.0, 4.5, 3.7, 4.2, 3.7, 3.6, 3.8, 4.9, 0.0, 6.1, 4.7, 5.3],  # Arizona Cardinals
    '8160': [0.0, 5.8, 4.2, 3.5, 4.7, 3.4, 5.3, 4.0, 3.8, 3.9, 5.3, 3.5],  # Miami Dolphins
    '26309': [0.0, 3.5, 3.0, 3.6, 3.3, 3.2, 3.6, 3.2, 3.5, 2.9, 3.7, 3.5],  # Sione Vaki
    '23092': [3.0, 3.9, 2.5, 2.6, 0.0, 2.8, 2.6, 2.9, 2.6, 2.7, 2.8, 2.5],  # Troy Franklin
    '27076': [2.5, 2.3, 2.3, 2.5, 2.4, 2.9, 2.6, 2.4, 2.5, 2.5, 2.4, 2.5],  # Jalen Royals
    '23080': [2.3, 2.9, 2.4, 2.5, 0.0, 2.8, 2.6, 3.0, 2.6, 2.7, 2.7, 2.5],  # Marvin Mims Jr.
    '26005': [2.4, 2.5, 2.3, 1.9, 2.2, 2.1, 2.4, 0.0, 2.0, 2.5, 2.0, 1.8],  # Mason Taylor
    '16483': [2.9, 0.0, 3.1, 3.1, 3.4, 3.5, 3.4, 3.2, 3.1, 3.1, 2.7, 2.7],  # Austin Ekeler
    '19590': [0.0, 2.6, 2.8, 2.6, 2.6, 2.4, 2.4, 2.4, 2.4, 2.8, 2.9, 2.6],  # Jauan Jennings
    '25876': [3.7, 2.6, 2.4, 2.8, 2.5, 0.0, 2.9, 2.8, 2.6, 2.7, 2.4, 2.9],  # Jaleel McLaughlin
    '28067': [0.0, 2.4, 2.3, 2.7, 2.5, 2.5, 2.4, 2.2, 2.6, 2.4, 2.4, 2.6],  # Kevin Coleman Jr.
    '26011': [2.4, 2.8, 2.8, 2.7, 2.8, 2.7, 2.7, 0.0, 2.8, 2.8, 2.7, 2.8],  # Devontez Walker
    '26638': [0.0, 4.2, 4.2, 4.2, 4.2, 0.0, 4.2, 4.2, 4.2, 4.2, 4.2, 4.2],  # Harrison Mevis
    '22976': [3.0, 2.9, 3.5, 3.3, 2.9, 3.3, 3.0, 2.6, 0.0, 2.9, 2.7, 3.5],  # Tyler Goodson
    '25419': [2.6, 2.2, 2.3, 2.4, 2.2, 0.0, 2.1, 2.6, 2.3, 2.5, 2.4, 2.4],  # Zachariah Branch
    '18631': [2.4, 0.0, 2.3, 2.1, 2.3, 2.5, 2.2, 2.2, 2.3, 2.4, 2.2, 2.5],  # Dawson Knox
    '22915': [5.1, 3.3, 2.7, 0.9, 2.5, 0.0, 2.1, 2.6, 2.6, 2.0, 2.2, 2.6],  # George Holani
    '25968': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 6.9, 6.6, 5.6, 6.5, 6.1, 6.4],  # Shedeur Sanders
    '27147': [2.3, 2.3, 2.3, 2.4, 0.0, 2.6, 2.4, 2.4, 2.3, 2.5, 2.3, 2.3],  # Tez Johnson
    '27144': [3.7, 2.2, 2.0, 1.9, 1.7, 0.0, 1.8, 2.1, 2.2, 2.2, 2.0, 1.9],  # Savion Williams
    '17258': [2.2, 2.1, 2.2, 0.0, 2.1, 2.4, 2.1, 2.4, 2.4, 2.3, 2.2, 2.2],  # Calvin Ridley
    '19229': [2.0, 1.9, 1.8, 2.0, 0.0, 2.0, 2.2, 1.9, 2.2, 2.0, 2.0, 2.2],  # Cole Kmet
    '24360': [7.7, 6.1, 0.0, 1.7, 1.9, 1.8, 1.9, 1.8, 1.8, 1.6, 1.8, 1.6],  # Kendre Miller
    '27211': [0.0, 0.0, 0.0, 2.6, 0.0, 2.9, 3.0, 3.2, 2.9, 2.9, 2.7, 2.6],  # Pat Bryant
    '23812': [1.7, 2.2, 1.8, 1.9, 0.0, 2.1, 1.9, 2.2, 1.9, 2.0, 2.0, 1.8],  # Tyler Badie
    '23119': [1.7, 1.7, 2.0, 1.6, 1.8, 1.7, 2.0, 1.8, 0.0, 1.8, 1.9, 2.0],  # Elijah Higgins
    '22930': [0.0, 1.0, 2.6, 2.9, 0.0, 2.5, 2.9, 2.9, 2.9, 2.3, 2.4, 2.2],  # Will Shipley
    '23079': [3.1, 2.2, 2.2, 3.0, 2.9, 2.4, 3.0, 0.0, 2.4, 2.6, 2.9, 2.6],  # Seth McGowan
    '28168': [1.8, 2.0, 2.0, 1.9, 2.0, 1.9, 2.0, 0.0, 2.0, 2.0, 1.9, 2.0],  # Matt Hibner
    '23091': [0.0, 0.0, 2.4, 2.8, 2.8, 0.0, 2.5, 2.2, 2.3, 2.7, 2.2, 2.6],  # Terrance Ferguson
    '22845': [2.0, 0.0, 2.0, 1.7, 2.0, 2.2, 1.9, 1.9, 2.0, 2.1, 1.8, 2.2],  # Joshua Palmer
    '23837': [0.0, 1.7, 1.8, 1.7, 1.7, 1.8, 1.8, 1.6, 1.6, 1.7, 1.8, 1.7],  # Erick All Jr.
    '23104': [2.1, 1.8, 0.0, 1.8, 2.1, 1.8, 2.0, 1.7, 1.7, 1.8, 2.1, 2.1],  # Theo Johnson
    '15581': [1.9, 1.6, 1.7, 1.7, 1.6, 0.0, 1.5, 1.8, 1.6, 1.8, 1.7, 1.7],  # Austin Hooper
    '15665': [0.0, 0.0, 0.0, 2.4, 2.6, 2.2, 2.2, 2.4, 2.3, 2.4, 2.2, 2.2],  # Demarcus Robinson
    '28127': [1.7, 1.9, 1.7, 1.5, 1.6, 1.7, 1.9, 0.0, 1.5, 1.9, 1.6, 1.5],  # Omar Cooper Jr.
    '16460': [1.8, 1.7, 1.5, 1.4, 1.3, 0.0, 1.4, 1.5, 1.6, 1.7, 1.6, 1.5],  # Jonnu Smith
    '25885': [12.3, 6.1, 0.9, 1.5, 0.8, 0.0, 0.7, 0.8, 0.9, 0.7, 0.7, 0.9],  # Emanuel Wilson
    '16556': [3.1, 1.6, 1.6, 1.4, 1.6, 1.4, 1.6, 0.0, 1.4, 1.7, 1.5, 1.3],  # Tim Patrick
    '24351': [0.0, 2.0, 1.9, 2.0, 2.0, 0.0, 2.1, 2.3, 1.8, 2.1, 1.9, 2.1],  # Raheim Sanders
    '27487': [3.5, 2.5, 1.5, 1.7, 1.8, 0.0, 1.6, 1.7, 1.5, 1.5, 1.6, 1.5],  # Kyle Williams
    '19278': [0.0, 0.0, 0.0, 0.0, 2.4, 2.0, 1.9, 2.3, 2.1, 2.1, 2.0, 2.4],  # Michael Pittman Jr.
    '22905': [1.7, 0.0, 1.8, 1.8, 1.8, 1.8, 2.0, 1.8, 1.8, 1.8, 1.6, 1.7],  # Treylon Burks
    '26022': [1.6, 1.6, 1.8, 1.9, 1.8, 0.0, 1.6, 1.9, 1.7, 1.6, 1.6, 1.7],  # Tory Horton
    '23064': [1.6, 2.5, 0.0, 1.6, 1.6, 1.8, 1.7, 1.7, 1.8, 1.6, 1.6, 1.7],  # Marvin Harrison Jr.
    '27315': [1.6, 1.5, 1.5, 0.0, 1.5, 1.7, 1.4, 1.7, 1.7, 1.6, 1.6, 1.6],  # Chimere Dike
    '18561': [0.0, 1.3, 1.4, 1.3, 1.4, 1.5, 1.4, 1.3, 1.3, 1.3, 1.4, 1.3],  # Drew Sample
    '24238': [1.4, 1.3, 1.4, 0.0, 1.3, 1.5, 1.3, 1.5, 1.5, 1.4, 1.4, 1.4],  # Daniel Bellinger
    '18864': [1.7, 1.4, 1.5, 1.6, 1.4, 0.0, 1.3, 1.7, 1.5, 1.7, 1.5, 1.6],  # Olamide Zaccheaus
    '26148': [0.0, 0.0, 0.0, 2.1, 2.2, 2.1, 2.2, 1.9, 2.2, 2.6, 2.2, 1.9],  # Jonathon Brooks
    '18520': [1.7, 1.7, 1.9, 1.8, 1.7, 1.8, 1.7, 1.6, 0.0, 1.7, 1.7, 1.8],  # KaVontae Turpin
    '11798': [0.0, 0.0, 0.0, 1.7, 1.8, 1.5, 1.5, 1.6, 1.6, 1.6, 1.6, 1.6],  # Kyle Juszczyk
    '27224': [0.0, 0.0, 1.9, 1.7, 1.7, 1.7, 1.8, 0.0, 1.8, 1.6, 1.9, 2.1],  # Jack Bech
    '27337': [1.2, 1.6, 1.5, 1.5, 1.5, 1.4, 1.6, 0.0, 1.6, 1.6, 1.4, 1.6],  # Ja'Kobi Lane
    '25289': [4.2, 3.6, 0.0, 1.1, 1.1, 0.9, 1.0, 1.0, 1.0, 1.0, 1.0, 1.1],  # Jacob Cowing
    '27138': [1.1, 1.2, 1.2, 1.1, 1.2, 1.2, 1.2, 1.0, 1.2, 1.2, 1.2, 1.1],  # Mitchell Evans
    '28427': [1.2, 0.0, 1.4, 1.3, 1.3, 1.3, 1.4, 1.3, 1.3, 1.3, 1.4, 1.4],  # Nate Boerkircher
    '18590': [1.1, 1.4, 1.2, 1.2, 0.0, 1.4, 1.2, 1.4, 1.2, 1.3, 1.4, 1.2],  # Lil'Jordan Humphrey
    '24371': [1.4, 1.4, 1.3, 1.3, 1.3, 0.0, 1.4, 1.4, 1.3, 1.3, 1.3, 1.4],  # Isaiah Bond
    '26379': [2.7, 1.2, 1.0, 0.9, 1.0, 1.0, 1.2, 0.0, 0.9, 1.1, 0.9, 0.9],  # Isaiah Williams
    '23679': [1.8, 1.9, 1.9, 1.8, 0.0, 2.0, 2.1, 1.9, 1.6, 2.2, 1.7, 1.8],  # Sean Tucker
    '22980': [1.6, 1.3, 0.0, 1.3, 1.5, 1.2, 1.5, 1.3, 1.2, 1.4, 1.5, 1.5],  # Tyrone Tracy Jr.
    '28161': [1.1, 1.0, 1.0, 1.1, 0.0, 1.1, 1.2, 1.1, 1.3, 1.1, 1.1, 1.2],  # Sam Roush
    '23742': [0.9, 1.1, 1.0, 0.9, 1.0, 1.0, 1.1, 0.0, 0.9, 1.1, 0.9, 0.8],  # Jeremy Ruckert
    '22895': [1.0, 1.3, 1.0, 1.0, 1.2, 1.1, 1.0, 0.9, 1.2, 1.1, 1.0, 1.0],  # John Metchie III
    '25335': [0.0, 0.0, 0.0, 1.4, 1.5, 1.7, 1.4, 1.4, 1.3, 1.4, 1.5, 1.4],  # Andrei Iosivas
    '16489': [0.0, 0.0, 0.0, 1.6, 1.7, 0.0, 1.6, 1.5, 1.4, 1.4, 1.6, 1.4],  # Mack Hollins
    '20113': [1.1, 0.0, 1.1, 1.1, 1.1, 1.2, 1.3, 1.2, 1.1, 1.1, 1.0, 1.1],  # Dyami Brown
    '27320': [0.0, 0.7, 1.5, 1.7, 0.0, 1.6, 1.5, 1.7, 1.6, 1.6, 1.6, 1.6],  # Jonah Coleman
    '28403': [0.0, 1.0, 0.9, 1.1, 1.0, 1.0, 1.0, 0.9, 1.0, 1.0, 1.0, 1.0],  # Will Kacmarek
    '19423': [0.9, 1.1, 0.9, 1.0, 0.0, 1.0, 1.0, 1.1, 1.0, 1.0, 1.0, 0.9],  # Adam Trautman
    '28424': [1.0, 0.9, 0.9, 1.0, 1.0, 1.2, 1.1, 1.0, 1.0, 1.0, 1.0, 1.0],  # Cyrus Allen
    '27285': [0.9, 0.0, 1.0, 0.9, 0.9, 0.9, 0.9, 1.0, 0.9, 0.9, 1.0, 1.0],  # LeQuint Allen Jr.
    '16399': [0.0, 0.0, 0.3, 0.3, 1.2, 1.2, 1.1, 1.3, 1.2, 1.1, 1.4, 1.1],  # David Njoku
    '22947': [5.3, 3.1, 0.6, 0.7, 0.0, 0.6, 0.7, 0.7, 0.7, 0.6, 0.6, 0.5],  # Dameon Pierce
    '28129': [2.6, 2.8, 0.0, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.7, 0.6],  # Bryce Lance
    '26136': [0.0, 0.0, 1.4, 1.1, 0.0, 1.2, 1.4, 1.1, 1.2, 1.4, 1.4, 1.1],  # Xavier Legette
    '28133': [1.1, 0.0, 0.8, 0.9, 0.9, 0.9, 0.8, 0.9, 0.9, 0.8, 1.0, 0.8],  # Brenen Thompson
    '22833': [0.8, 0.0, 0.8, 0.8, 0.8, 0.9, 0.9, 0.9, 0.8, 0.8, 0.7, 0.8],  # John Bates
    '27449': [1.1, 0.9, 0.9, 1.1, 1.1, 0.0, 1.0, 0.8, 0.8, 1.0, 0.8, 1.0],  # Konata Mumpfield
    '23179': [0.0, 0.0, 0.0, 0.0, 0.0, 1.5, 1.2, 1.3, 1.3, 1.3, 1.3, 1.2],  # Jalen McMillan
    '26191': [1.1, 0.0, 1.0, 0.9, 1.0, 1.1, 1.0, 1.0, 1.1, 1.1, 1.0, 1.1],  # Ray Davis
    '25393': [0.0, 1.0, 1.1, 1.0, 1.1, 1.0, 1.0, 0.9, 1.1, 1.2, 1.0, 0.9],  # Trevor Etienne
    '26194': [1.8, 0.9, 0.9, 0.8, 0.8, 0.9, 0.9, 0.0, 0.9, 0.9, 1.0, 1.0],  # Dylan Laube
    '24370': [0.0, 0.6, 0.9, 0.8, 0.9, 1.0, 0.8, 0.8, 0.7, 0.8, 0.8, 0.8],  # Mitch Tinsley
    '27182': [0.0, 0.8, 0.8, 0.8, 0.8, 0.7, 0.7, 0.7, 0.7, 0.8, 0.8, 0.7],  # Tai Felton
    '23135': [0.9, 0.7, 0.7, 0.8, 0.8, 0.0, 0.7, 0.7, 0.7, 0.8, 0.6, 0.7],  # Jordan Whittington
    '20126': [0.8, 0.8, 0.7, 0.7, 0.8, 0.0, 0.7, 0.7, 0.7, 0.8, 0.6, 0.7],  # Tutu Atwell
    '26034': [0.7, 0.0, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.7, 0.8, 0.8],  # Travis Hunter
    '11689': [0.2, 0.8, 0.8, 0.7, 0.0, 0.8, 0.8, 0.8, 0.7, 0.6, 0.7, 0.6],  # Zach Ertz
    '25267': [0.7, 0.6, 0.8, 0.7, 0.6, 0.7, 0.6, 0.6, 0.0, 0.6, 0.7, 0.7],  # Luke Schoonmaker
    '22908': [0.0, 0.0, 0.0, 1.6, 0.0, 1.4, 1.6, 1.5, 1.6, 1.3, 1.4, 1.3],  # Tank Bigsby
    '27288': [0.8, 0.7, 0.7, 0.7, 0.7, 0.6, 0.7, 0.0, 0.7, 0.7, 0.7, 0.8],  # Dont'e Thornton Jr.
    '25941': [0.7, 0.7, 0.8, 0.8, 0.7, 0.8, 0.7, 0.6, 0.0, 0.7, 0.6, 0.8],  # Hunter Luepke
    '27486': [2.0, 1.2, 0.0, 0.5, 0.5, 0.4, 0.4, 0.5, 0.5, 0.5, 0.4, 0.5],  # Jordan Watkins
    '28132': [0.6, 0.6, 0.6, 0.6, 0.0, 0.6, 0.7, 0.6, 0.7, 0.6, 0.6, 0.7],  # Zavion Thomas
    '25823': [0.0, 0.3, 0.0, 0.8, 0.7, 0.0, 0.7, 0.8, 0.8, 0.9, 0.9, 0.8],  # Chris Brooks
    '28157': [0.5, 0.5, 0.0, 0.5, 0.5, 0.5, 0.5, 0.6, 0.6, 0.5, 0.5, 0.5],  # Marlin Klein
    '18656': [1.2, 0.9, 0.4, 0.5, 0.5, 0.5, 0.5, 0.0, 0.4, 0.5, 0.5, 0.4],  # Ashton Dulin
    '17145': [0.5, 0.4, 0.0, 0.4, 0.5, 0.4, 0.5, 0.4, 0.4, 0.4, 0.5, 0.5],  # Patrick Ricard
    '27810': [0.6, 0.6, 0.5, 0.6, 0.0, 0.6, 0.6, 0.6, 0.7, 0.6, 0.6, 0.6],  # Jahdae Walker
    '25977': [4.5, 0.3, 0.3, 0.2, 0.3, 0.3, 0.3, 0.0, 0.3, 0.3, 0.3, 0.2],  # Isaiah Davis
    '17528': [1.9, 0.0, 0.4, 0.4, 0.4, 0.4, 0.4, 0.5, 0.4, 0.4, 0.5, 0.4],  # Marquez Valdes-Scantling
    '17826': [0.0, 0.4, 0.5, 0.4, 0.5, 0.5, 0.4, 0.4, 0.4, 0.4, 0.5, 0.4],  # Tanner Hudson
    '23770': [0.0, 0.5, 0.5, 0.6, 0.5, 0.5, 0.5, 0.5, 0.6, 0.5, 0.5, 0.5],  # Jalen Tolbert
    '24343': [0.7, 0.7, 0.8, 0.0, 0.6, 0.8, 0.6, 0.7, 0.8, 0.8, 0.7, 0.7],  # Nicholas Singleton
    '25430': [0.4, 0.4, 0.0, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.5, 0.4],  # Oscar Delp
    '26012': [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.0, 0.5, 0.5, 0.5, 0.6],  # Malik Benson
    '27463': [0.3, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.0, 0.5, 0.5, 0.5, 0.5],  # Elijah Sarratt
    '28141': [0.4, 0.4, 0.5, 0.4, 0.5, 0.4, 0.5, 0.5, 0.0, 0.5, 0.5, 0.5],  # Reggie Virgil
    '23004': [0.4, 0.4, 0.5, 0.5, 0.5, 0.0, 0.4, 0.5, 0.5, 0.4, 0.4, 0.5],  # Elijah Arroyo
    '16447': [0.0, 0.6, 0.7, 0.6, 0.6, 0.6, 0.7, 0.6, 0.0, 0.7, 0.7, 0.7],  # James Conner
    '26310': [0.4, 0.0, 0.4, 0.4, 0.4, 0.4, 0.5, 0.4, 0.4, 0.4, 0.4, 0.4],  # Ben Sinnott
    '27499': [0.4, 0.0, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.5, 0.5],  # Tanner Koziol
    '25269': [0.4, 0.4, 0.0, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4],  # Cade Stover
    '27469': [0.4, 0.0, 0.4, 0.4, 0.4, 0.5, 0.4, 0.4, 0.4, 0.4, 0.4, 0.5],  # Jackson Hawes
    '27165': [2.2, 1.6, 0.4, 0.4, 0.3, 0.0, 0.2, 0.2, 0.2, 0.2, 0.3, 0.2],  # Kaleb Johnson
    '18706': [1.3, 0.3, 0.2, 0.3, 0.3, 0.3, 0.2, 0.0, 0.2, 0.3, 0.3, 0.2],  # Darius Slayton
    '25361': [0.7, 0.0, 0.0, 0.4, 0.3, 0.4, 0.3, 0.4, 0.4, 0.3, 0.3, 0.4],  # Tank Dell
    '17268': [0.0, 0.0, 0.0, 0.4, 0.5, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4],  # Christian Kirk
    '26037': [0.0, 0.5, 0.2, 0.2, 0.2, 0.3, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],  # Colbie Young
    '23144': [0.0, 0.3, 0.3, 0.3, 0.0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],  # Eli Stowers
    '18280': [0.0, 0.4, 0.0, 0.3, 0.4, 0.3, 0.4, 0.3, 0.3, 0.4, 0.4, 0.4],  # Devin Singletary
    '25416': [0.3, 0.3, 0.0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.4, 0.3],  # Barion Brown
    '18571': [0.2, 0.0, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],  # Alec Ingold
    '18585': [0.3, 0.0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],  # Greg Dortch
    '27259': [0.2, 0.2, 0.2, 0.2, 0.2, 0.3, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],  # Brashard Smith
    '23986': [0.0, 0.3, 0.2, 0.3, 0.3, 0.0, 0.3, 0.2, 0.2, 0.3, 0.2, 0.2],  # Ronnie Rivers
    '28084': [0.0, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],  # Demond Claiborne
    '27310': [0.3, 0.3, 0.0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],  # Jordan James
    '25322': [1.7, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Roschon Johnson
    '24372': [0.0, 0.0, 0.0, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.3, 0.2],  # KeAndre Lambert-Smith
    '28079': [0.0, 0.2, 0.1, 0.2, 0.1, 0.1, 0.2, 0.1, 0.2, 0.2, 0.1, 0.1],  # Justin Joly
    '26212': [0.0, 0.2, 0.3, 0.2, 0.2, 0.3, 0.3, 0.2, 0.2, 0.3, 0.3, 0.2],  # Tahj Brooks
    '23054': [2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Audric Estime
    '26328': [0.1, 0.0, 0.1, 0.1, 0.1, 0.2, 0.2, 0.2, 0.1, 0.1, 0.1, 0.1],  # Luke McCaffrey
    '24334': [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Ja'Tavion Sanders
    '22728': [0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Michael Carter
    '17598': [0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Tyler Conklin
    '25693': [0.0, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],  # Jacob Saylors
    '27102': [1.2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Dylan Sampson
    '20114': [0.1, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Elijah Moore
    '28126': [0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Skyler Bell
    '28138': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.4, 0.4],  # De'Zhaun Stribling
    '25983': [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1],  # Rasheen Ali
    '23249': [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1],  # Kene Nwangwu
    '27097': [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1],  # DJ Giddens
    '24367': [0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1],  # Adam Randall
    '28297': [0.0, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Eli Heidenreich
    '27093': [0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1],  # Deion Burks
    '28047': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Chris Brazzell II
    '19624': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Antonio Gibson
    '13971': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Tyler Lockett
    '11606': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # DeAndre Hopkins
    '26392': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Isaac Guerendo
    '23682': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Devin Neal
    '24333': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Isiah Pacheco
    '22953': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Carson Beck
    '24362': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Kaytron Allen
    '23893': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Michael Woods II
    '16425': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Kareem Hunt
    '24335': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jarquez Hunter
    '23093': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Moliki Matavao
    '25748': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Zavier Scott
    '24344': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jaydon Blue
    '22928': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Phil Mafah
    '19781': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Justin Fields
    '15802': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Tyreek Hill
    '10007': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Tyrod Taylor
    '26329': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jawhar Jordan
    '19844': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Reggie Gilliam
    '14191': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Michael Burton
    '28125': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # CJ Daniels
    '25373': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Riley Leonard
    '26964': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Terrell Jennings
    '22921': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jerome Ford
    '18562': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Gardner Minshew II
    '27454': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jaylin Lane
    '24205': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Malik Davis
    '19252': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Brandon Aiyuk
    '25654': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Emari Demercado
    '23020': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jayden Reed
    '13274': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Brandon McManus
    '24214': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Velus Jones Jr.
    '24901': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Bam Knight
    '25287': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Cedric Tillman
    '17508': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Durham Smythe
    '18226': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Hollywood Brown
    '16445': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jeremy McNichols
    '28434': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Max Bredeson
    '22684': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Chris Blair
    '16548': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Zane Gonzalez
    '25388': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Trey Benson
    '24347': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Anthony Richardson Sr.
    '25627': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Xavier Smith
    '25976': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Damien Martinez
    '25443': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Lew Nichols III
    '26748': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # British Brooks
    '20082': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Trey Lance
    '23680': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Quinn Ewers
    '24009': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jake Moody
    '28109': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # J'Mari Taylor
    '25282': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Luke Musgrave
    '28124': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Cash Jones
    '26444': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Charlie Smyth
    '24345': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Drew Allar
    '23136': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # De'Von Achane
    '12127': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Odell Beckham Jr.
    '23160': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jaxson Dart
    '27444': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Josh Williams
    '19483': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Van Jefferson
    '23451': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Adam Prentice
    '27066': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Xavier Restrepo
    '27326': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jam Miller
    '28115': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Noah Whittington
    '25967': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Joe Milton III
    '25368': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Ty Simpson
    '25321': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Tyson Bagent
    '20155': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jaret Patterson
    '28404': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Robert Henry Jr.
    '13890': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Marcus Mariota
    '20156': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Mac Jones
    '24778': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jake Tonges
    '19198': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Tua Tagovailoa
    '22763': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Khalil Herbert
    '9300': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Joe Flacco
    '27993': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Max Klare
    '16420': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Joe Mixon
    '25387': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Roman Hemby
    '19747': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Nick Westbrook-Ikhine
    '19219': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jalen Reagor
    '22913': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Tyquan Thornton
    '27294': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jimmy Horn Jr.
    '18557': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jake Browning
    '25394': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Jaydn Ott
    '27798': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Ben Sauls
    '18487': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Josh Oliver
    '25980': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Frank Gore Jr.
    '24361': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Cade Klubnik
}

EXTERNAL_WEEKLY_REC = {
    '22968': [0.0, 5.0, 4.7, 5.2, 5.1, 5.0, 5.0, 5.0, 5.1, 4.8, 5.1, 5.1],  # Jahmyr Gibbs
    '23021': [3.2, 3.4, 3.3, 3.3, 3.4, 3.3, 3.4, 3.3, 3.5, 3.5, 3.4, 3.4],  # Kenneth Walker III
    '23133': [3.7, 3.7, 3.7, 3.7, 3.7, 0.0, 3.4, 3.7, 3.5, 3.7, 3.6, 3.7],  # Bijan Robinson
    '23070': [6.6, 6.9, 6.9, 6.6, 6.9, 0.0, 7.1, 7.3, 6.9, 6.7, 6.5, 6.7],  # Jaxon Smith-Njigba
    '23180': [6.9, 7.1, 6.8, 7.2, 7.0, 0.0, 7.0, 7.1, 7.3, 7.5, 7.1, 7.2],  # Puka Nacua
    '16393': [4.0, 4.6, 0.0, 4.4, 4.8, 4.2, 4.4, 4.5, 4.2, 4.4, 4.5, 4.3],  # Christian McCaffrey
    '19788': [0.0, 7.2, 7.2, 6.9, 7.0, 7.1, 7.1, 6.8, 7.0, 6.8, 7.0, 7.1],  # Ja'Marr Chase
    '19799': [0.0, 6.9, 6.4, 7.2, 7.0, 6.8, 6.9, 6.8, 7.1, 6.5, 7.0, 7.0],  # Amon-Ra St. Brown
    '19217': [2.6, 2.5, 2.8, 2.8, 2.7, 2.7, 2.7, 0.0, 2.6, 2.8, 2.7, 2.6],  # Jonathan Taylor
    '19202': [7.2, 6.8, 6.8, 6.9, 7.0, 7.1, 6.7, 6.9, 0.0, 6.7, 7.1, 7.0],  # CeeDee Lamb
    '23059': [3.3, 3.5, 3.3, 3.5, 3.4, 0.0, 3.4, 3.5, 3.6, 3.6, 3.5, 3.5],  # Kyren Williams
    '15514': [1.3, 1.4, 1.4, 1.5, 1.4, 1.4, 1.5, 0.0, 1.4, 1.4, 1.3, 1.4],  # Derrick Henry
    '22916': [5.6, 5.9, 6.0, 6.1, 5.7, 5.8, 6.1, 0.0, 5.9, 6.0, 5.6, 6.0],  # Zay Flowers
    '19236': [0.0, 6.3, 6.7, 6.3, 6.5, 6.5, 6.3, 6.1, 6.6, 6.6, 6.4, 6.3],  # Justin Jefferson
    '22739': [2.8, 2.6, 2.6, 2.7, 2.7, 2.7, 2.6, 2.7, 0.0, 2.6, 2.7, 2.7],  # Javonte Williams
    '22936': [7.3, 7.2, 8.1, 7.6, 7.2, 7.7, 7.6, 7.3, 0.0, 7.4, 7.8, 7.5],  # Trey McBride
    '22958': [1.6, 0.0, 1.6, 1.5, 1.5, 1.6, 1.5, 1.6, 1.6, 1.5, 1.5, 1.6],  # James Cook III
    '20111': [6.5, 6.4, 0.0, 6.0, 6.0, 6.3, 6.3, 6.3, 6.1, 6.3, 6.1, 6.2],  # Chris Olave
    '22955': [6.7, 6.3, 6.6, 6.8, 6.6, 6.1, 6.1, 0.0, 6.2, 6.0, 6.5, 6.3],  # Brock Bowers
    '20130': [5.9, 5.8, 0.0, 5.7, 5.6, 5.7, 5.9, 5.8, 5.9, 5.9, 5.7, 5.8],  # Nico Collins
    '12119': [4.9, 5.2, 0.0, 5.0, 5.5, 4.7, 5.0, 5.2, 4.8, 5.0, 5.2, 4.9],  # Mike Evans
    '25417': [5.0, 5.2, 5.3, 4.9, 5.4, 5.3, 5.3, 4.8, 5.3, 5.2, 5.2, 5.1],  # Tetairoa McMillan
    '19792': [2.5, 2.5, 2.5, 2.4, 2.6, 2.5, 2.5, 2.4, 2.5, 2.5, 2.5, 2.5],  # Chuba Hubbard
    '25989': [3.7, 4.0, 4.2, 4.4, 4.2, 3.9, 4.0, 0.0, 4.0, 3.9, 4.2, 4.0],  # Ashton Jeanty
    '23163': [5.8, 5.8, 5.8, 5.8, 5.7, 0.0, 5.4, 5.9, 5.5, 5.8, 5.7, 5.8],  # Drake London
    '23106': [4.7, 0.0, 4.6, 4.9, 5.0, 4.9, 4.9, 4.8, 4.7, 4.8, 5.2, 4.7],  # Parker Washington
    '12123': [4.4, 4.7, 4.5, 4.8, 4.6, 0.0, 4.7, 4.7, 4.8, 5.1, 4.7, 4.8],  # Davante Adams
    '23113': [5.0, 5.3, 5.1, 5.1, 5.3, 5.1, 5.3, 5.1, 5.4, 5.4, 5.4, 5.2],  # Rashee Rice
    '23886': [5.0, 4.9, 4.5, 4.8, 4.3, 0.0, 4.4, 4.8, 4.6, 4.9, 4.7, 4.7],  # Christian Watson
    '25324': [0.0, 4.2, 4.2, 4.0, 4.1, 4.1, 4.1, 4.0, 4.1, 4.0, 4.1, 4.1],  # Chase Brown
    '23072': [6.1, 6.2, 5.9, 6.0, 5.9, 5.9, 6.3, 0.0, 5.6, 5.9, 6.1, 5.5],  # Garrett Wilson
    '24209': [3.2, 3.5, 3.3, 0.0, 3.5, 3.4, 3.3, 3.4, 3.5, 3.5, 3.3, 3.5],  # Jaylen Warren
    '25981': [2.8, 2.9, 2.9, 3.0, 0.0, 3.1, 2.9, 2.9, 3.1, 3.0, 3.0, 2.9],  # Bucky Irving
    '26403': [4.9, 5.1, 5.1, 4.8, 5.2, 5.2, 5.1, 4.8, 5.2, 5.1, 5.1, 5.0],  # Jalen Coker
    '25409': [5.5, 5.6, 0.0, 5.3, 5.4, 5.6, 5.3, 5.4, 5.3, 5.1, 5.6, 5.8],  # Malik Nabers
    '19211': [0.0, 5.1, 5.1, 4.9, 4.9, 5.0, 5.0, 4.8, 4.9, 4.8, 4.9, 5.0],  # Tee Higgins
    '19210': [2.3, 2.4, 2.4, 2.3, 0.0, 2.4, 2.5, 2.4, 2.5, 2.4, 2.4, 2.4],  # D'Andre Swift
    '16499': [4.9, 5.1, 0.0, 4.9, 5.3, 4.6, 4.9, 5.1, 4.7, 4.9, 5.0, 4.8],  # George Kittle
    '25333': [4.8, 4.8, 5.6, 5.1, 4.8, 5.2, 5.2, 5.0, 0.0, 5.0, 5.3, 5.1],  # Michael Wilson
    '18218': [1.9, 5.6, 5.3, 5.1, 5.3, 0.0, 5.0, 5.0, 4.7, 5.1, 5.0, 4.8],  # A.J. Brown
    '19222': [5.0, 5.6, 5.3, 5.3, 0.0, 5.2, 5.7, 5.2, 5.2, 5.2, 5.3, 5.4],  # DeVonta Smith
    '26024': [4.9, 4.7, 4.3, 4.6, 4.1, 0.0, 4.2, 4.6, 4.4, 4.7, 4.5, 4.5],  # Matthew Golden
    '27297': [1.8, 0.0, 1.8, 1.9, 1.9, 1.8, 1.8, 1.8, 1.8, 1.8, 1.9, 1.8],  # Bhayshul Tuten
    '17265': [4.3, 0.0, 4.3, 3.9, 4.2, 4.4, 4.2, 4.4, 4.3, 4.2, 4.0, 4.5],  # DJ Moore
    '25984': [0.0, 1.8, 1.8, 1.8, 1.8, 1.8, 1.7, 1.7, 1.8, 1.8, 1.7, 1.8],  # Ollie Gordon II
    '17240': [1.6, 2.1, 2.2, 2.0, 0.0, 1.9, 2.1, 1.9, 1.9, 1.9, 2.0, 2.0],  # Saquon Barkley
    '22963': [4.9, 4.7, 4.6, 4.7, 4.8, 4.9, 4.6, 4.7, 0.0, 4.5, 4.9, 4.8],  # George Pickens
    '22726': [3.1, 3.0, 3.0, 2.9, 3.0, 0.0, 2.8, 2.9, 2.7, 2.9, 2.9, 2.7],  # Rhamondre Stevenson
    '23781': [5.2, 5.3, 0.0, 5.1, 5.2, 5.4, 5.1, 5.2, 5.2, 4.9, 5.4, 5.5],  # Isaiah Likely
    '22982': [0.0, 2.9, 2.8, 2.8, 2.8, 2.8, 2.9, 0.0, 2.7, 2.8, 2.8, 2.6],  # Breece Hall
    '26009': [4.8, 4.6, 4.9, 0.0, 5.0, 5.3, 5.0, 4.9, 5.1, 4.8, 4.9, 4.8],  # Carnell Tate
    '11594': [4.1, 4.4, 4.2, 4.3, 4.4, 4.3, 4.4, 4.2, 4.5, 4.5, 4.4, 4.3],  # Travis Kelce
    '25391': [2.4, 2.6, 2.6, 2.6, 2.6, 0.0, 2.5, 2.6, 2.5, 2.6, 2.6, 2.5],  # Quinshon Judkins
    '22978': [0.0, 4.9, 4.5, 5.1, 5.0, 4.8, 4.9, 4.8, 5.0, 4.6, 4.9, 4.9],  # Sam LaPorta
    '27016': [4.3, 4.4, 4.3, 4.3, 0.0, 4.4, 4.5, 4.5, 4.6, 4.4, 4.3, 4.5],  # Luther Burden III
    '26434': [5.4, 5.1, 5.6, 5.7, 5.5, 5.5, 5.4, 0.0, 5.3, 5.6, 5.4, 5.2],  # Tyler Warren
    '18705': [1.7, 1.7, 1.8, 0.0, 1.8, 1.9, 1.8, 1.7, 1.8, 1.7, 1.8, 1.7],  # Tony Pollard
    '19790': [4.3, 4.4, 4.4, 4.3, 0.0, 4.4, 4.5, 4.7, 4.4, 4.4, 4.4, 4.6],  # Jaylen Waddle
    '18466': [4.1, 0.0, 3.9, 3.8, 4.2, 4.1, 4.0, 4.3, 4.1, 4.0, 3.7, 4.2],  # Terry McLaurin
    '27166': [1.9, 1.9, 0.0, 1.9, 1.9, 2.0, 1.9, 1.9, 1.9, 1.8, 2.0, 2.0],  # Cam Skattebo
    '25403': [1.9, 1.9, 2.1, 2.0, 1.9, 2.0, 2.0, 1.9, 0.0, 1.9, 2.0, 2.0],  # Jeremiyah Love
    '18219': [3.7, 4.0, 3.5, 0.0, 3.8, 3.6, 3.4, 3.7, 3.9, 3.8, 3.5, 4.0],  # DK Metcalf
    '27339': [3.6, 3.8, 3.6, 3.7, 3.6, 0.0, 3.5, 3.6, 3.5, 3.7, 3.7, 3.5],  # Denzel Boston
    '26122': [3.7, 0.0, 3.9, 4.1, 4.2, 4.0, 4.2, 4.1, 4.1, 4.1, 4.3, 4.1],  # Ladd McConkey
    '25298': [4.6, 4.5, 4.2, 4.5, 4.0, 0.0, 4.1, 4.5, 4.3, 4.5, 4.4, 4.4],  # Tucker Kraft
    '23062': [3.6, 3.8, 3.9, 4.0, 0.0, 4.1, 3.8, 3.8, 4.1, 4.0, 3.9, 3.7],  # Emeka Egbuka
    '23677': [0.0, 3.6, 3.3, 3.8, 3.7, 3.6, 3.6, 3.6, 3.8, 3.4, 3.7, 3.7],  # Jameson Williams
    '17270': [3.9, 4.2, 4.1, 4.0, 0.0, 4.0, 4.3, 4.0, 3.9, 3.9, 4.0, 4.1],  # Dallas Goedert
    '27142': [3.5, 3.6, 3.6, 3.5, 0.0, 3.6, 3.6, 3.7, 3.6, 3.6, 3.6, 3.7],  # RJ Harvey
    '23019': [3.2, 3.4, 3.2, 3.3, 3.4, 3.3, 3.4, 3.2, 3.5, 3.5, 3.4, 3.3],  # Xavier Worthy
    '27050': [4.1, 4.3, 4.1, 4.2, 4.1, 0.0, 4.0, 4.1, 4.0, 4.2, 4.2, 4.0],  # Harold Fannin Jr.
    '25411': [3.3, 3.4, 3.4, 3.4, 0.0, 3.5, 3.6, 3.5, 3.6, 3.4, 3.4, 3.5],  # Rome Odunze
    '25345': [4.1, 0.0, 4.0, 4.2, 4.3, 4.2, 4.2, 4.2, 4.1, 4.2, 4.4, 4.1],  # Brenton Strange
    '18239': [0.0, 1.8, 0.0, 1.8, 1.8, 1.8, 1.9, 1.8, 1.9, 1.9, 1.8, 1.8],  # David Montgomery
    '18290': [0.0, 4.8, 5.1, 4.9, 4.9, 5.0, 4.8, 4.7, 5.0, 5.0, 4.9, 4.8],  # T.J. Hockenson
    '19562': [4.3, 4.3, 0.0, 4.0, 4.0, 4.2, 4.2, 4.1, 4.1, 4.2, 4.1, 4.1],  # Juwan Johnson
    '13981': [4.2, 0.0, 3.9, 3.8, 4.2, 4.1, 4.0, 4.3, 4.1, 4.0, 3.8, 4.2],  # Stefon Diggs
    '23152': [0.0, 1.4, 2.1, 2.0, 2.1, 0.0, 2.1, 2.2, 2.1, 2.0, 2.0, 2.0],  # Zach Charbonnet
    '26314': [0.0, 3.7, 3.8, 3.8, 3.7, 3.7, 3.6, 3.5, 3.7, 3.8, 3.6, 3.7],  # Malik Washington
    '17269': [3.7, 3.9, 4.0, 4.0, 3.8, 3.9, 4.0, 0.0, 3.9, 4.0, 3.7, 4.0],  # Mark Andrews
    '16673': [0.0, 2.4, 2.6, 2.4, 2.5, 2.5, 2.4, 2.4, 2.5, 2.5, 2.4, 2.4],  # Aaron Jones Sr.
    '22985': [4.3, 4.1, 4.4, 0.0, 4.4, 4.7, 4.5, 4.3, 4.5, 4.3, 4.3, 4.3],  # Wan'Dale Robinson
    '24706': [4.3, 3.7, 4.1, 4.3, 4.2, 4.1, 4.0, 0.0, 3.9, 4.2, 4.0, 3.8],  # Josh Downs
    '23107': [0.0, 3.3, 3.5, 3.3, 3.4, 3.4, 3.2, 3.2, 3.4, 3.4, 3.3, 3.3],  # Jordan Addison
    '25395': [1.5, 0.0, 1.2, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3],  # Omarion Hampton
    '20163': [3.7, 4.1, 3.8, 0.0, 4.0, 3.9, 3.7, 4.0, 4.1, 4.0, 3.8, 4.1],  # Pat Freiermuth
    '19231': [0.0, 0.0, 0.0, 2.2, 2.3, 2.4, 2.4, 2.3, 2.3, 2.4, 2.3, 2.3],  # Travis Etienne Jr.
    '19245': [1.1, 1.2, 1.2, 1.2, 0.0, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2],  # J.K. Dobbins
    '18598': [3.3, 0.0, 3.3, 3.5, 3.5, 3.4, 3.4, 3.4, 3.3, 3.4, 3.6, 3.3],  # Jakobi Meyers
    '27331': [3.8, 4.2, 4.1, 4.1, 4.1, 0.0, 4.0, 4.0, 3.9, 4.1, 4.1, 4.0],  # KC Concepcion
    '27131': [1.0, 1.3, 1.3, 1.3, 0.0, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3],  # Kyle Monangai
    '26006': [3.6, 3.6, 3.6, 3.6, 0.0, 3.6, 3.7, 3.7, 3.8, 3.6, 3.6, 3.7],  # Colston Loveland
    '25247': [3.4, 0.0, 3.5, 3.2, 3.4, 3.5, 3.4, 3.5, 3.4, 3.4, 3.3, 3.6],  # Dalton Kincaid
    '22969': [0.0, 1.0, 1.1, 1.0, 1.0, 1.0, 1.0, 1.0, 1.1, 1.1, 1.0, 1.0],  # Jordan Mason
    '25337': [3.2, 3.0, 3.1, 3.3, 3.1, 2.9, 2.9, 0.0, 2.9, 2.8, 3.2, 3.0],  # Tre Tucker
    '23794': [3.2, 3.4, 3.1, 2.8, 2.9, 0.0, 2.7, 2.8, 2.6, 2.8, 2.8, 2.6],  # Romeo Doubs
    '17253': [2.8, 2.9, 2.9, 2.8, 0.0, 2.9, 3.0, 3.1, 2.9, 2.9, 2.9, 3.1],  # Courtland Sutton
    '27520': [2.0, 1.7, 0.0, 1.7, 1.7, 1.7, 1.8, 1.8, 1.8, 1.8, 1.7, 1.8],  # Woody Marks
    '18244': [0.0, 0.0, 0.0, 3.4, 3.7, 3.2, 3.4, 3.5, 3.2, 3.4, 3.5, 3.3],  # Deebo Samuel Sr.
    '14104': [2.9, 3.0, 3.0, 2.8, 3.1, 3.0, 3.0, 2.8, 3.0, 3.0, 3.0, 2.9],  # Darren Waller
    '23748': [3.5, 0.0, 3.5, 3.2, 3.4, 3.5, 3.4, 3.5, 3.5, 3.4, 3.3, 3.6],  # Khalil Shakir
    '16406': [3.3, 3.5, 3.5, 3.7, 0.0, 3.8, 3.5, 3.5, 3.7, 3.6, 3.6, 3.4],  # Chris Godwin Jr.
    '15561': [3.2, 3.3, 3.4, 3.2, 3.3, 0.0, 3.2, 3.2, 3.0, 3.3, 3.2, 3.1],  # Hunter Henry
    '26335': [3.0, 3.2, 3.2, 3.1, 3.2, 0.0, 3.3, 3.4, 3.2, 3.1, 3.0, 3.1],  # AJ Barner
    '26035': [0.0, 0.0, 0.0, 3.5, 3.6, 3.8, 3.8, 3.8, 3.7, 3.8, 3.7, 3.7],  # Jordyn Tyson
    '23791': [0.0, 0.0, 3.0, 3.5, 3.4, 3.3, 3.3, 0.0, 3.2, 3.4, 3.3, 3.1],  # Alec Pierce
    '23065': [0.0, 0.8, 1.6, 1.6, 1.6, 0.0, 1.6, 1.6, 1.5, 1.6, 1.6, 1.5],  # TreVeyon Henderson
    '17349': [3.7, 3.7, 0.0, 3.6, 3.6, 3.6, 3.7, 3.7, 3.7, 3.8, 3.6, 3.7],  # Dalton Schultz
    '11616': [3.8, 3.5, 3.7, 3.8, 3.7, 3.7, 3.6, 0.0, 3.5, 3.8, 3.6, 3.5],  # Keenan Allen
    '16421': [2.4, 2.6, 0.0, 2.1, 2.1, 2.2, 2.2, 2.2, 2.1, 2.2, 2.1, 2.2],  # Alvin Kamara
    '25323': [2.5, 2.4, 2.5, 0.0, 2.6, 2.7, 2.6, 2.5, 2.6, 2.5, 2.5, 2.5],  # Tyjae Spears
    '23310': [2.4, 2.5, 2.5, 2.6, 0.0, 2.7, 2.5, 2.5, 2.6, 2.6, 2.5, 2.4],  # Kenny Gainwell
    '23181': [3.3, 3.5, 3.5, 3.6, 0.0, 3.7, 3.4, 3.4, 3.7, 3.6, 3.5, 3.3],  # Cade Otton
    '18269': [0.0, 0.3, 0.8, 0.8, 0.7, 0.0, 2.0, 2.1, 2.1, 2.1, 2.1, 2.1],  # Josh Jacobs
    '26398': [3.2, 3.0, 3.0, 3.1, 3.1, 3.2, 3.0, 3.1, 0.0, 2.9, 3.2, 3.1],  # Ryan Flournoy
    '26039': [3.6, 3.6, 2.6, 0.0, 2.8, 2.7, 2.6, 2.8, 2.9, 2.8, 2.7, 2.9],  # Germie Bernard
    '22718': [3.3, 3.2, 3.2, 3.2, 3.3, 3.3, 3.1, 3.2, 0.0, 3.1, 3.3, 3.3],  # Jake Ferguson
    '27316': [0.7, 0.0, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6],  # Jacory Croskey-Merritt
    '20164': [2.9, 2.9, 2.8, 2.8, 2.8, 0.0, 2.6, 2.9, 2.7, 2.9, 2.8, 2.8],  # Kyle Pitts Sr.
    '24687': [2.1, 2.3, 2.3, 2.2, 2.3, 0.0, 2.4, 2.5, 2.3, 2.2, 2.1, 2.2],  # Rashid Shaheed
    '24354': [2.6, 2.8, 2.7, 2.7, 0.0, 2.6, 2.9, 2.6, 2.6, 2.6, 2.7, 2.7],  # Dontayvion Wicks
    '23013': [0.9, 1.0, 0.9, 1.0, 1.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],  # Blake Corum
    '28135': [0.0, 2.7, 2.8, 2.9, 2.8, 2.8, 2.7, 2.6, 2.8, 2.8, 2.7, 2.7],  # Caleb Douglas
    '17066': [3.0, 3.0, 3.4, 3.1, 3.0, 3.2, 3.2, 3.1, 0.0, 3.1, 3.2, 3.1],  # Kendrick Bourne
    '24346': [1.9, 1.5, 1.4, 1.5, 1.4, 1.4, 1.5, 0.0, 1.4, 1.4, 1.5, 1.3],  # Braelon Allen
    '26055': [2.6, 3.2, 3.0, 3.1, 3.0, 3.0, 3.2, 0.0, 2.9, 3.0, 3.1, 2.8],  # Kenyon Sadiq
    '24357': [0.0, 2.9, 2.7, 2.8, 2.7, 2.7, 2.9, 0.0, 2.6, 2.7, 2.8, 2.5],  # Adonai Mitchell
    '25360': [2.1, 0.0, 1.7, 1.8, 1.8, 1.7, 1.8, 1.8, 1.8, 1.8, 1.8, 1.8],  # Keaton Mitchell
    '16081': [3.0, 3.0, 3.0, 3.0, 0.0, 3.0, 3.1, 3.1, 3.1, 3.0, 3.0, 3.1],  # Kalif Raymond
    '23056': [3.4, 3.2, 3.3, 3.5, 3.3, 3.1, 3.1, 0.0, 3.1, 3.1, 3.3, 3.2],  # Michael Mayer
    '17272': [0.0, 2.7, 2.7, 2.6, 2.6, 2.7, 2.6, 2.5, 2.6, 2.6, 2.6, 2.7],  # Mike Gesicki
    '26215': [0.0, 0.0, 2.8, 3.0, 3.1, 2.9, 3.0, 3.0, 3.0, 3.0, 3.2, 3.0],  # Tre' Harris
    '27329': [0.0, 0.0, 1.7, 1.1, 1.2, 0.0, 1.2, 1.2, 1.2, 1.1, 1.1, 1.1],  # Jadarian Price
    '23982': [3.2, 0.0, 3.0, 2.9, 3.2, 3.1, 3.1, 3.2, 3.1, 3.0, 2.9, 3.2],  # Chig Okonkwo
    '23000': [2.2, 0.0, 2.2, 2.3, 2.3, 2.3, 2.3, 2.2, 2.2, 2.3, 2.4, 2.2],  # Brian Thomas Jr.
    '26019': [2.4, 0.0, 2.4, 2.2, 2.3, 2.4, 2.4, 2.4, 2.4, 2.4, 2.3, 2.5],  # Keon Coleman
    '26409': [4.4, 4.2, 0.0, 2.4, 2.4, 2.5, 2.5, 2.5, 2.4, 2.5, 2.4, 2.5],  # Devaughn Vele
    '19794': [2.2, 2.4, 2.4, 2.4, 2.3, 2.3, 2.4, 0.0, 2.4, 2.4, 2.2, 2.4],  # Rashod Bateman
    '23123': [2.3, 0.0, 2.0, 2.2, 2.2, 2.1, 2.2, 2.2, 2.2, 2.2, 2.4, 2.2],  # Quentin Johnston
    '24172': [2.1, 2.1, 2.3, 2.2, 2.1, 2.2, 2.2, 2.1, 0.0, 2.2, 2.2, 2.2],  # Tyler Allgeier
    '23891': [2.1, 0.0, 1.9, 1.9, 2.0, 1.9, 1.9, 2.0, 2.0, 1.9, 1.9, 2.0],  # Rachaad White
    '28896': [0.0, 0.0, 2.8, 0.0, 3.0, 2.9, 2.8, 3.0, 3.1, 3.0, 2.9, 3.2],  # Roman Wilson
    '19647': [1.3, 1.4, 1.3, 0.0, 1.4, 1.4, 1.3, 1.4, 1.4, 1.4, 1.3, 1.4],  # Rico Dowdle
    '23118': [1.9, 1.8, 1.3, 1.4, 1.3, 0.0, 0.9, 0.9, 0.9, 1.0, 0.9, 0.9],  # MarShawn Lloyd
    '23153': [0.0, 2.7, 2.8, 2.8, 2.7, 2.8, 2.7, 2.6, 2.7, 2.8, 2.7, 2.7],  # Greg Dulcich
    '22967': [2.5, 2.7, 2.5, 0.0, 2.7, 2.6, 2.5, 2.7, 2.7, 2.7, 2.6, 2.8],  # Darnell Washington
    '25420': [2.4, 2.7, 2.6, 2.6, 0.0, 2.5, 2.7, 2.5, 2.5, 2.5, 2.6, 2.6],  # Makai Lemon
    '26038': [0.0, 2.1, 2.2, 2.2, 2.1, 2.1, 2.1, 2.0, 2.1, 2.2, 2.1, 2.1],  # Chris Bell
    '20094': [0.7, 0.7, 0.7, 0.7, 0.6, 0.0, 0.6, 0.7, 0.6, 0.7, 0.6, 0.7],  # Brian Robinson Jr.
    '16423': [0.0, 1.6, 1.6, 1.5, 1.5, 1.6, 1.6, 1.5, 1.5, 1.5, 1.5, 1.6],  # Samaje Perine
    '16433': [2.1, 2.2, 2.2, 2.1, 2.2, 0.0, 2.3, 2.3, 2.2, 2.1, 2.1, 2.1],  # Cooper Kupp
    '18406': [2.0, 0.0, 2.0, 1.8, 1.9, 1.9, 2.0, 2.0, 2.0, 1.9, 1.9, 2.0],  # Ty Johnson
    '18256': [1.6, 1.7, 1.7, 1.8, 1.7, 1.7, 1.8, 0.0, 1.7, 1.7, 1.6, 1.7],  # Justice Hill
    '27122': [2.0, 2.0, 0.0, 1.9, 1.9, 1.9, 2.0, 2.0, 2.0, 2.0, 1.9, 2.0],  # Jaylin Noel
    '27446': [0.0, 1.8, 1.7, 1.9, 1.9, 1.8, 1.8, 1.8, 1.9, 1.7, 1.9, 1.9],  # Isaac TeSlaa
    '27994': [2.0, 2.0, 0.0, 1.9, 2.0, 2.0, 1.9, 2.0, 1.9, 1.8, 2.0, 2.1],  # Malachi Fields
    '16411': [2.4, 2.5, 2.5, 2.4, 0.0, 2.4, 2.5, 2.6, 2.5, 2.5, 2.5, 2.6],  # Evan Engram
    '19810': [1.8, 1.8, 0.0, 1.7, 1.8, 1.8, 1.7, 1.8, 1.7, 1.6, 1.8, 1.9],  # Darnell Mooney
    '25418': [2.0, 0.0, 1.9, 1.9, 2.0, 1.9, 1.9, 2.0, 1.9, 1.9, 1.9, 2.0],  # Antonio Williams
    '27059': [1.5, 1.5, 1.6, 0.0, 1.6, 1.7, 1.6, 1.6, 1.6, 1.5, 1.5, 1.5],  # Elic Ayomanor
    '24177': [1.8, 1.6, 1.7, 1.8, 1.7, 1.6, 1.6, 0.0, 1.6, 1.6, 1.7, 1.6],  # Jalen Nailor
    '22989': [1.9, 1.5, 0.0, 2.0, 1.9, 2.0, 2.0, 2.0, 2.0, 2.0, 1.9, 2.0],  # Kayshon Boutte
    '28107': [1.0, 1.1, 1.0, 1.0, 1.1, 1.0, 1.1, 1.0, 1.1, 1.1, 1.1, 1.0],  # Emmett Johnson
    '25987': [0.0, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.6, 0.7, 0.7, 0.7, 0.7],  # Jaylen Wright
    '22986': [0.3, 0.0, 0.3, 0.4, 0.4, 0.4, 0.4, 0.4, 0.3, 0.4, 0.4, 0.4],  # Chris Rodriguez Jr.
    '23101': [1.7, 1.7, 1.7, 1.7, 1.6, 0.0, 1.5, 1.7, 1.6, 1.7, 1.6, 1.7],  # Jahan Dotson
    '25304': [2.6, 2.3, 1.8, 1.6, 1.7, 0.0, 1.6, 1.7, 1.5, 1.7, 1.6, 1.6],  # DeMario Douglas
    '27109': [2.2, 2.2, 2.3, 0.0, 2.3, 2.4, 2.3, 2.3, 2.4, 2.2, 2.3, 2.3],  # Gunnar Helm
    '19372': [1.8, 1.9, 1.8, 1.9, 1.9, 0.0, 1.9, 1.9, 1.9, 2.0, 1.9, 1.9],  # Colby Parkinson
    '28130': [1.6, 1.7, 1.7, 1.8, 0.0, 1.8, 1.7, 1.7, 1.8, 1.8, 1.7, 1.6],  # Ted Hurst III
    '28108': [0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.0, 0.6, 0.6, 0.6, 0.6],  # Mike Washington Jr.
    '19302': [0.6, 0.6, 0.0, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6],  # Najee Harris
    '25997': [1.6, 0.0, 1.7, 1.8, 1.9, 1.8, 1.9, 1.8, 1.8, 1.8, 1.9, 1.8],  # Oronde Gadsden II
    '26355': [1.5, 0.0, 1.0, 1.0, 1.1, 1.0, 1.1, 1.0, 1.0, 1.1, 1.1, 1.1],  # Kimani Vidal
    '28137': [1.4, 0.0, 1.4, 1.5, 1.5, 1.5, 1.4, 1.5, 1.4, 1.5, 1.5, 1.4],  # Josh Cameron
    '17527': [1.9, 1.8, 0.0, 1.8, 1.8, 1.9, 1.9, 1.9, 1.8, 1.9, 1.8, 1.8],  # Noah Fant
    '25332': [1.5, 1.6, 0.0, 1.5, 1.5, 1.5, 1.6, 1.6, 1.6, 1.6, 1.5, 1.6],  # Xavier Hutchinson
    '28401': [0.5, 0.4, 0.0, 0.4, 0.4, 0.3, 0.3, 0.4, 0.3, 0.4, 0.4, 0.3],  # Kaelon Black
    '15623': [2.2, 2.0, 1.9, 2.0, 1.9, 0.0, 1.9, 2.0, 2.0, 2.0, 2.0, 1.9],  # Tyler Higbee
    '19201': [1.8, 1.8, 1.7, 1.8, 1.7, 0.0, 1.7, 1.7, 1.7, 1.7, 1.8, 1.7],  # Jerry Jeudy
    '28154': [1.9, 1.7, 1.8, 1.7, 1.8, 0.0, 1.7, 1.7, 1.6, 1.7, 1.7, 1.6],  # Eli Raridon
    '19539': [1.6, 1.7, 1.6, 1.7, 1.7, 1.7, 1.7, 1.6, 1.7, 1.7, 1.7, 1.7],  # Noah Gray
    '22756': [1.9, 0.0, 1.8, 1.9, 1.9, 1.8, 1.9, 1.9, 1.9, 1.9, 1.9, 1.9],  # Charlie Kolar
    '22795': [1.7, 1.7, 1.8, 1.7, 1.8, 1.8, 1.8, 1.7, 1.8, 1.7, 1.8, 1.7],  # Tommy Tremble
    '26309': [0.0, 0.9, 0.8, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.8, 0.9, 0.9],  # Sione Vaki
    '23092': [1.7, 1.8, 1.3, 1.3, 0.0, 1.3, 1.4, 1.4, 1.3, 1.3, 1.3, 1.4],  # Troy Franklin
    '27076': [1.3, 1.4, 1.3, 1.3, 1.4, 1.3, 1.4, 1.3, 1.4, 1.4, 1.4, 1.3],  # Jalen Royals
    '23080': [1.5, 1.5, 1.5, 1.5, 0.0, 1.5, 1.6, 1.6, 1.5, 1.5, 1.5, 1.6],  # Marvin Mims Jr.
    '26005': [2.3, 2.0, 1.9, 2.0, 1.9, 1.9, 2.0, 0.0, 1.8, 1.9, 2.0, 1.8],  # Mason Taylor
    '16483': [1.1, 0.0, 0.9, 0.9, 1.0, 0.9, 0.9, 1.0, 1.0, 0.9, 0.9, 1.0],  # Austin Ekeler
    '19590': [0.0, 1.4, 1.5, 1.4, 1.4, 1.4, 1.4, 1.4, 1.5, 1.5, 1.4, 1.4],  # Jauan Jennings
    '25876': [1.5, 1.1, 1.1, 1.1, 1.1, 0.0, 1.1, 1.1, 1.1, 1.1, 1.1, 1.1],  # Jaleel McLaughlin
    '28067': [0.0, 1.4, 1.4, 1.5, 1.4, 1.4, 1.4, 1.4, 1.4, 1.5, 1.4, 1.4],  # Kevin Coleman Jr.
    '26011': [1.0, 1.1, 1.1, 1.2, 1.1, 1.1, 1.2, 0.0, 1.1, 1.1, 1.0, 1.1],  # Devontez Walker
    '22976': [0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.0, 0.7, 0.7, 0.7],  # Tyler Goodson
    '25419': [1.4, 1.4, 1.4, 1.4, 1.4, 0.0, 1.3, 1.4, 1.3, 1.4, 1.3, 1.3],  # Zachariah Branch
    '18631': [1.3, 0.0, 1.3, 1.2, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3, 1.2, 1.3],  # Dawson Knox
    '22915': [1.9, 1.2, 0.8, 0.3, 0.8, 0.0, 0.8, 0.9, 0.8, 0.8, 0.8, 0.8],  # George Holani
    '27147': [1.0, 1.1, 1.1, 1.1, 0.0, 1.1, 1.1, 1.1, 1.1, 1.1, 1.1, 1.1],  # Tez Johnson
    '27144': [2.0, 1.3, 1.2, 1.3, 1.1, 0.0, 1.2, 1.3, 1.2, 1.2, 1.3, 1.2],  # Savion Williams
    '17258': [1.2, 1.1, 1.2, 0.0, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2],  # Calvin Ridley
    '19229': [1.3, 1.3, 1.3, 1.3, 0.0, 1.4, 1.4, 1.4, 1.4, 1.3, 1.3, 1.4],  # Cole Kmet
    '24360': [1.5, 1.5, 0.0, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4],  # Kendre Miller
    '27211': [0.0, 0.0, 0.0, 1.6, 0.0, 1.6, 1.6, 1.8, 1.6, 1.6, 1.5, 1.6],  # Pat Bryant
    '23812': [1.2, 1.3, 1.3, 1.2, 0.0, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3],  # Tyler Badie
    '23119': [1.3, 1.3, 1.5, 1.4, 1.3, 1.4, 1.4, 1.3, 0.0, 1.3, 1.4, 1.4],  # Elijah Higgins
    '22930': [0.0, 0.3, 1.1, 1.1, 0.0, 1.1, 1.1, 1.0, 1.0, 1.1, 1.1, 1.1],  # Will Shipley
    '23079': [0.6, 0.4, 0.5, 0.5, 0.5, 0.5, 0.5, 0.0, 0.4, 0.5, 0.5, 0.4],  # Seth McGowan
    '28168': [1.1, 1.2, 1.2, 1.2, 1.1, 1.1, 1.2, 0.0, 1.1, 1.2, 1.1, 1.2],  # Matt Hibner
    '23091': [0.0, 0.0, 1.2, 1.3, 1.3, 0.0, 1.3, 1.2, 1.2, 1.4, 1.2, 1.3],  # Terrance Ferguson
    '22845': [1.0, 0.0, 1.1, 1.0, 1.0, 1.1, 1.0, 1.1, 1.1, 1.0, 1.0, 1.1],  # Joshua Palmer
    '23837': [0.0, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3, 1.2, 1.3, 1.2, 1.3, 1.3],  # Erick All Jr.
    '23104': [1.1, 1.1, 0.0, 1.0, 1.1, 1.1, 1.0, 1.1, 1.0, 1.0, 1.1, 1.1],  # Theo Johnson
    '15581': [1.1, 1.1, 1.1, 1.1, 1.1, 0.0, 1.1, 1.1, 1.1, 1.1, 1.1, 1.1],  # Austin Hooper
    '15665': [0.0, 0.0, 0.0, 1.1, 1.2, 1.0, 1.1, 1.1, 1.0, 1.1, 1.1, 1.1],  # Demarcus Robinson
    '28127': [1.2, 1.1, 1.0, 1.0, 1.0, 1.0, 1.1, 0.0, 1.0, 1.1, 1.1, 1.0],  # Omar Cooper Jr.
    '16460': [1.2, 1.2, 1.1, 1.2, 1.1, 0.0, 1.1, 1.2, 1.1, 1.2, 1.2, 1.2],  # Jonnu Smith
    '25885': [1.5, 1.0, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Emanuel Wilson
    '16556': [2.0, 0.9, 0.9, 1.0, 0.9, 0.9, 1.0, 0.0, 0.9, 0.9, 0.9, 0.9],  # Tim Patrick
    '24351': [0.0, 0.9, 0.9, 0.9, 0.9, 0.0, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9],  # Raheim Sanders
    '27487': [1.5, 1.0, 0.7, 0.7, 0.7, 0.0, 0.7, 0.7, 0.6, 0.7, 0.7, 0.7],  # Kyle Williams
    '19278': [0.0, 0.0, 0.0, 0.0, 1.5, 1.4, 1.4, 1.5, 1.5, 1.5, 1.4, 1.6],  # Michael Pittman Jr.
    '22905': [0.9, 0.0, 0.8, 0.8, 0.9, 0.8, 0.8, 0.9, 0.9, 0.8, 0.8, 0.9],  # Treylon Burks
    '26022': [0.9, 0.9, 0.9, 0.9, 0.9, 0.0, 0.9, 1.0, 0.9, 0.9, 0.9, 0.9],  # Tory Horton
    '23064': [0.8, 1.2, 0.0, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8],  # Marvin Harrison Jr.
    '27315': [1.0, 0.9, 1.0, 0.0, 1.0, 1.1, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],  # Chimere Dike
    '18561': [0.0, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2],  # Drew Sample
    '24238': [1.1, 1.1, 1.1, 0.0, 1.2, 1.2, 1.2, 1.1, 1.2, 1.1, 1.1, 1.1],  # Daniel Bellinger
    '18864': [0.9, 0.9, 0.9, 0.9, 0.9, 0.0, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9],  # Olamide Zaccheaus
    '26148': [0.0, 0.0, 0.0, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8],  # Jonathon Brooks
    '18520': [0.7, 0.6, 0.6, 0.7, 0.7, 0.7, 0.6, 0.7, 0.0, 0.6, 0.7, 0.7],  # KaVontae Turpin
    '11798': [0.0, 0.0, 0.0, 1.3, 1.4, 1.3, 1.3, 1.3, 1.3, 1.3, 1.4, 1.3],  # Kyle Juszczyk
    '27224': [0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 1.0, 1.0, 1.1, 1.0],  # Jack Bech
    '27337': [0.6, 0.8, 0.7, 0.8, 0.8, 0.7, 0.8, 0.0, 0.8, 0.8, 0.7, 0.8],  # Ja'Kobi Lane
    '25289': [1.9, 1.8, 0.0, 0.4, 0.5, 0.4, 0.4, 0.4, 0.4, 0.4, 0.5, 0.4],  # Jacob Cowing
    '27138': [0.8, 0.8, 0.9, 0.8, 0.9, 0.9, 0.9, 0.8, 0.9, 0.9, 0.9, 0.8],  # Mitchell Evans
    '28427': [0.8, 0.0, 0.8, 0.9, 0.9, 0.8, 0.8, 0.8, 0.8, 0.8, 0.9, 0.8],  # Nate Boerkircher
    '18590': [0.8, 0.7, 0.8, 0.7, 0.0, 0.8, 0.8, 0.8, 0.7, 0.8, 0.8, 0.8],  # Lil'Jordan Humphrey
    '24371': [0.8, 0.7, 0.7, 0.7, 0.7, 0.0, 0.7, 0.7, 0.6, 0.7, 0.7, 0.7],  # Isaiah Bond
    '26379': [2.3, 0.8, 0.7, 0.7, 0.7, 0.7, 0.8, 0.0, 0.7, 0.7, 0.7, 0.7],  # Isaiah Williams
    '23679': [0.1, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Sean Tucker
    '22980': [0.5, 0.6, 0.0, 0.5, 0.5, 0.6, 0.5, 0.6, 0.5, 0.5, 0.6, 0.6],  # Tyrone Tracy Jr.
    '28161': [0.8, 0.8, 0.8, 0.8, 0.0, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8],  # Sam Roush
    '23742': [0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.0, 0.8, 0.9, 0.9, 0.8],  # Jeremy Ruckert
    '22895': [0.6, 0.8, 0.6, 0.6, 0.6, 0.7, 0.6, 0.6, 0.6, 0.6, 0.7, 0.6],  # John Metchie III
    '25335': [0.0, 0.0, 0.0, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8],  # Andrei Iosivas
    '16489': [0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 1.0, 0.9, 0.9, 0.9, 1.0, 0.9],  # Mack Hollins
    '20113': [0.7, 0.0, 0.6, 0.6, 0.6, 0.6, 0.6, 0.7, 0.6, 0.6, 0.6, 0.7],  # Dyami Brown
    '27320': [0.0, 0.3, 0.4, 0.4, 0.0, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4],  # Jonah Coleman
    '28403': [0.0, 0.7, 0.7, 0.8, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7],  # Will Kacmarek
    '19423': [0.7, 0.7, 0.7, 0.7, 0.0, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7],  # Adam Trautman
    '28424': [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.6, 0.5],  # Cyrus Allen
    '27285': [0.7, 0.0, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7],  # LeQuint Allen Jr.
    '16399': [0.0, 0.0, 0.2, 0.2, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 1.0, 0.9],  # David Njoku
    '22947': [1.0, 0.9, 0.2, 0.2, 0.0, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],  # Dameon Pierce
    '28129': [1.6, 1.6, 0.0, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4],  # Bryce Lance
    '26136': [0.0, 0.0, 0.7, 0.6, 0.0, 0.7, 0.7, 0.6, 0.7, 0.7, 0.7, 0.6],  # Xavier Legette
    '28133': [0.7, 0.0, 0.5, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6],  # Brenen Thompson
    '22833': [0.6, 0.0, 0.6, 0.6, 0.7, 0.6, 0.6, 0.7, 0.6, 0.6, 0.6, 0.7],  # John Bates
    '27449': [0.4, 0.4, 0.4, 0.4, 0.4, 0.0, 0.4, 0.4, 0.4, 0.5, 0.4, 0.4],  # Konata Mumpfield
    '23179': [0.0, 0.0, 0.0, 0.0, 0.0, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8],  # Jalen McMillan
    '26191': [0.3, 0.0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],  # Ray Davis
    '25393': [0.0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],  # Trevor Etienne
    '26194': [0.8, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.0, 0.3, 0.3, 0.3, 0.3],  # Dylan Laube
    '24370': [0.0, 0.4, 0.6, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],  # Mitch Tinsley
    '27182': [0.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],  # Tai Felton
    '23135': [0.4, 0.5, 0.4, 0.5, 0.5, 0.0, 0.5, 0.5, 0.5, 0.5, 0.4, 0.5],  # Jordan Whittington
    '20126': [0.4, 0.5, 0.4, 0.5, 0.4, 0.0, 0.4, 0.5, 0.5, 0.5, 0.5, 0.4],  # Tutu Atwell
    '26034': [0.4, 0.0, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4],  # Travis Hunter
    '11689': [0.2, 0.6, 0.5, 0.5, 0.0, 0.5, 0.6, 0.5, 0.5, 0.5, 0.5, 0.5],  # Zach Ertz
    '25267': [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.0, 0.5, 0.5, 0.5],  # Luke Schoonmaker
    '22908': [0.0, 0.0, 0.0, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Tank Bigsby
    '27288': [0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.0, 0.4, 0.4, 0.3, 0.4],  # Dont'e Thornton Jr.
    '25941': [0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.0, 0.3, 0.3, 0.3],  # Hunter Luepke
    '27486': [0.9, 0.6, 0.0, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],  # Jordan Watkins
    '28132': [0.3, 0.3, 0.3, 0.3, 0.0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],  # Zavion Thomas
    '25823': [0.0, 0.3, 0.0, 0.4, 0.3, 0.0, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4],  # Chris Brooks
    '28157': [0.4, 0.4, 0.0, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4],  # Marlin Klein
    '18656': [0.7, 0.6, 0.3, 0.3, 0.3, 0.3, 0.3, 0.0, 0.2, 0.3, 0.3, 0.2],  # Ashton Dulin
    '17145': [0.4, 0.5, 0.0, 0.4, 0.4, 0.5, 0.4, 0.5, 0.5, 0.4, 0.5, 0.5],  # Patrick Ricard
    '27810': [0.3, 0.3, 0.3, 0.3, 0.0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],  # Jahdae Walker
    '25977': [1.4, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1],  # Isaiah Davis
    '17528': [1.0, 0.0, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.3, 0.2],  # Marquez Valdes-Scantling
    '17826': [0.0, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4],  # Tanner Hudson
    '23770': [0.0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],  # Jalen Tolbert
    '24343': [0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Nicholas Singleton
    '25430': [0.4, 0.4, 0.0, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4],  # Oscar Delp
    '26012': [0.3, 0.2, 0.3, 0.3, 0.3, 0.2, 0.3, 0.0, 0.3, 0.2, 0.3, 0.3],  # Malik Benson
    '27463': [0.2, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.0, 0.3, 0.3, 0.3, 0.3],  # Elijah Sarratt
    '28141': [0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.0, 0.3, 0.3, 0.3],  # Reggie Virgil
    '23004': [0.3, 0.3, 0.3, 0.3, 0.3, 0.0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],  # Elijah Arroyo
    '16447': [0.0, 0.1, 0.2, 0.1, 0.1, 0.1, 0.2, 0.1, 0.0, 0.2, 0.2, 0.1],  # James Conner
    '26310': [0.3, 0.0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.4, 0.3, 0.3, 0.3, 0.3],  # Ben Sinnott
    '27499': [0.2, 0.0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.2, 0.3, 0.3, 0.3, 0.3],  # Tanner Koziol
    '25269': [0.3, 0.3, 0.0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],  # Cade Stover
    '27469': [0.2, 0.0, 0.2, 0.2, 0.2, 0.3, 0.2, 0.3, 0.3, 0.2, 0.2, 0.3],  # Jackson Hawes
    '27165': [0.2, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Kaleb Johnson
    '18706': [0.7, 0.2, 0.2, 0.2, 0.2, 0.1, 0.2, 0.0, 0.1, 0.2, 0.1, 0.1],  # Darius Slayton
    '25361': [0.4, 0.0, 0.0, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],  # Tank Dell
    '17268': [0.0, 0.0, 0.0, 0.2, 0.3, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],  # Christian Kirk
    '26037': [0.0, 0.4, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],  # Colbie Young
    '23144': [0.0, 0.2, 0.2, 0.2, 0.0, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],  # Eli Stowers
    '18280': [0.0, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Devin Singletary
    '25416': [0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Barion Brown
    '18571': [0.2, 0.0, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],  # Alec Ingold
    '18585': [0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Greg Dortch
    '27259': [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Brashard Smith
    '23986': [0.0, 0.1, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Ronnie Rivers
    '28084': [0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Demond Claiborne
    '25322': [0.4, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Roschon Johnson
    '24372': [0.0, 0.0, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # KeAndre Lambert-Smith
    '28079': [0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Justin Joly
    '23054': [0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Audric Estime
    '26328': [0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Luke McCaffrey
    '24334': [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Ja'Tavion Sanders
    '22728': [0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Michael Carter
    '17598': [0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # Tyler Conklin
    '27102': [0.8, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # Dylan Sampson
    '20114': [0.0, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1],  # Elijah Moore
    '28126': [0.1, 0.0, 0.1, 0.0, 0.1, 0.1, 0.0, 0.1, 0.1, 0.0, 0.0, 0.1],  # Skyler Bell
    '28138': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.2, 0.2],  # De'Zhaun Stribling
}
