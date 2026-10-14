"""Simulates a regular season on top of a completed mock draft (see
harness.cli_draft) -- matchup schedule, best-lineup-per-week scoring, and
win/loss records, for one question: how does the drafted team actually do.

No add/drops: rosters are frozen at whatever the mock draft produced, for
every simulated week. That's a deliberate scope cut, not an oversight -- see
harness/cli_draft.py's --simulate-season flag.

Only simulates weeks we have real weekly_stats for that year (e.g. 3 weeks
for a season still in progress, all played weeks for a finished one) --
there's no projection fallback here, so a week with no stats yet is simply
not simulated.

The matchup schedule and lineup-scoring logic are 1:1 ports of the Go
engine's authoritative versions (so results here match what the real engine
would produce for the same roster/scores), not reimplementations from
scratch:
  - generate_schedule <- pkg/gamestate/handler.go's generateSchedule
    (circle method round robin, same bye handling for an odd team count)
  - best_lineup <- pkg/engine/EndOfWeekHandler.go's scoreTeam +
    convertSlotToPositionMap (process players highest-score-first, each
    taking the first/narrowest still-empty eligible slot)
"""

from typing import Dict, List, Tuple

import pandas as pd
from sqlalchemy import text

from blitz_env.models import DatabaseManager, Player

BYE_ID = "BYE"

# Mirrors pkg/engine/Position.go's GetAllowedPlayerPositionsForSlot. Any slot
# not listed here is single-position (its own name is the only eligible
# position) -- that's true for every real slot except these two synthetic
# ones. BENCH is deliberately absent: it never scores, see
# slots_sorted_by_eligibility_breadth.
_WIDE_SLOT_ELIGIBILITY = {
    "SUPERFLEX": ["QB", "RB", "WR", "TE"],
    "FLEX": ["RB", "WR", "TE"],
}


def allowed_positions_for_slot(slot: str) -> List[str]:
    return _WIDE_SLOT_ELIGIBILITY.get(slot, [slot])


def generate_schedule(bot_ids: List[str], weeks: int) -> List[List[Tuple[str, str]]]:
    """Round-robin ("circle method") schedule: one list of (home, away) bot
    id pairs per week. An odd number of teams gets a synthetic BYE slot --
    whichever real team is paired against it simply doesn't play that week.
    """
    ids = list(bot_ids)
    n = len(ids)
    if n % 2 != 0:
        ids.append(BYE_ID)
        n += 1

    schedule: List[List[Tuple[str, str]]] = []
    for _ in range(weeks):
        week_matchups = []
        for i in range(n // 2):
            home = ids[i]
            away = ids[n - 1 - i]
            if home != BYE_ID and away != BYE_ID:
                week_matchups.append((home, away))
        schedule.append(week_matchups)
        ids = [ids[0], ids[n - 1]] + ids[1:n - 1]

    return schedule


def slots_sorted_by_eligibility_breadth(player_slots: Dict[str, int]) -> List[str]:
    """Expands {slot: count} into one entry per starting slot, narrowest
    eligibility first (QB before FLEX before SUPERFLEX) so a single-position
    slot never loses a player it needed to a wide slot that could have taken
    someone else instead. BENCH is excluded -- it doesn't score."""
    slots = []
    for slot_name, count in player_slots.items():
        if slot_name == "BENCH":
            continue
        slots.extend([slot_name] * count)
    slots.sort(key=lambda s: len(allowed_positions_for_slot(s)))
    return slots


def best_lineup(player_slots: Dict[str, int], roster: pd.DataFrame) -> Tuple[float, List[dict]]:
    """`roster` needs columns id, full_name, allowed_positions (list),
    FPTS -- one row per rostered player who has a score for the week.
    Returns (total starting score, [{slot, id, full_name, FPTS} per
    starting slot, players that didn't make the lineup excluded]).
    """
    slot_names = slots_sorted_by_eligibility_breadth(player_slots)
    starters = [None] * len(slot_names)

    if not roster.empty:
        for _, player in roster.sort_values("FPTS", ascending=False).iterrows():
            player_positions = set(player["allowed_positions"] or [])
            for i, slot in enumerate(slot_names):
                if starters[i] is not None:
                    continue
                if player_positions & set(allowed_positions_for_slot(slot)):
                    starters[i] = player
                    break

    total = sum(float(s["FPTS"]) for s in starters if s is not None)
    lineup = [
        {
            "slot": slot_names[i],
            "id": starters[i]["id"] if starters[i] is not None else None,
            "full_name": starters[i]["full_name"] if starters[i] is not None else "None",
            "FPTS": float(starters[i]["FPTS"]) if starters[i] is not None else 0.0,
        }
        for i in range(len(slot_names))
    ]
    return total, lineup


def _real_weeks_with_stats(db: DatabaseManager, year: int) -> List[int]:
    df = pd.read_sql(
        text("SELECT DISTINCT CAST(week AS INTEGER) AS week FROM weekly_stats WHERE year = :year ORDER BY week"),
        db.engine,
        params={"year": year},
    )
    return [int(w) for w in df["week"].tolist()]


def _week_fpts_by_player(db: DatabaseManager, year: int, week: int) -> Dict[str, float]:
    df = pd.read_sql(
        text(
            "SELECT fantasypros_id AS id, MAX(FPTS) AS FPTS FROM weekly_stats "
            "WHERE year = :year AND CAST(week AS INTEGER) = :week GROUP BY fantasypros_id"
        ),
        db.engine,
        params={"year": year, "week": week},
    )
    return dict(zip(df["id"], df["FPTS"]))


def simulate_season(db: DatabaseManager, year: int) -> dict:
    """Simulates every week that has real weekly_stats for `year` against
    the draft-day rosters currently in the db. Returns a dict with:
      weeks: the simulated week numbers
      weekly_results: [{week, matchups: [{home, away, home_score,
        away_score, winner, home_lineup, away_lineup}]}], where each
        *_lineup is {"starters": [...], "bench": [...]}
      records: {bot_id: {wins, losses, ties, points_for, points_against}}
      standings: bot ids ordered by wins desc, points_for desc
    """
    bots = sorted(db.get_all_bots(), key=lambda b: b.draft_order)
    bot_ids = [b.id for b in bots]

    settings = db.get_league_settings()
    player_slots = dict(settings.player_slots)

    weeks = _real_weeks_with_stats(db, year)
    if not weeks:
        raise ValueError(f"No weekly_stats found for year {year}; nothing to simulate.")

    schedule = generate_schedule(bot_ids, len(weeks))

    players = db.get_all_players()
    player_by_id: Dict[str, Player] = {p.id: p for p in players}
    roster_by_bot: Dict[str, List[str]] = {bid: [] for bid in bot_ids}
    for p in players:
        if p.current_bot_id in roster_by_bot:
            roster_by_bot[p.current_bot_id].append(p.id)

    records = {
        bid: {"wins": 0, "losses": 0, "ties": 0, "points_for": 0.0, "points_against": 0.0}
        for bid in bot_ids
    }
    weekly_results = []

    for week, week_matchups in zip(weeks, schedule):
        fpts_by_player = _week_fpts_by_player(db, year, week)

        team_scores: Dict[str, float] = {}
        team_lineups: Dict[str, List[dict]] = {}
        for bid in bot_ids:
            rows = [
                {
                    "id": pid,
                    "full_name": player_by_id[pid].full_name,
                    "allowed_positions": player_by_id[pid].allowed_positions,
                    "FPTS": fpts_by_player[pid],
                }
                for pid in roster_by_bot[bid]
                if pid in fpts_by_player
            ]
            roster_df = pd.DataFrame(rows)
            total, lineup = best_lineup(player_slots, roster_df)
            starter_ids = {s["id"] for s in lineup if s["id"] is not None}
            bench = [r for r in rows if r["id"] not in starter_ids]
            team_scores[bid] = total
            team_lineups[bid] = {"starters": lineup, "bench": bench}

        week_matchup_results = []
        for home, away in week_matchups:
            hs, aws = team_scores.get(home, 0.0), team_scores.get(away, 0.0)
            records[home]["points_for"] += hs
            records[home]["points_against"] += aws
            records[away]["points_for"] += aws
            records[away]["points_against"] += hs

            if hs > aws:
                records[home]["wins"] += 1
                records[away]["losses"] += 1
                winner = home
            elif aws > hs:
                records[away]["wins"] += 1
                records[home]["losses"] += 1
                winner = away
            else:
                records[home]["ties"] += 1
                records[away]["ties"] += 1
                winner = None

            week_matchup_results.append({
                "home": home,
                "away": away,
                "home_score": hs,
                "away_score": aws,
                "winner": winner,
                "home_lineup": team_lineups[home],
                "away_lineup": team_lineups[away],
            })

        weekly_results.append({"week": week, "matchups": week_matchup_results})

    standings = sorted(
        bot_ids,
        key=lambda bid: (records[bid]["wins"], records[bid]["points_for"]),
        reverse=True,
    )

    return {
        "weeks": weeks,
        "weekly_results": weekly_results,
        "records": records,
        "standings": standings,
    }
