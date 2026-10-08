"""Behavior checks for the shipped bot; no network or historical outcome fitting."""
import itertools
import json
import math
import shutil
import sqlite3
from collections import defaultdict
from types import SimpleNamespace

import pytest

from bots.nfl2025 import jon_bot as bot
from blitz_env.models import DatabaseManager


def player(pid, position, value, *, owner="", upcoming=None, rank=1):
    positions = position if isinstance(position, (tuple, list)) else [position]
    return bot.Candidate(str(pid), str(pid), frozenset(positions), rank, "BUF", 7,
                         owner, not owner, value, value if upcoming is None else upcoming)


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "gamestate.db"
    monkeypatch.setattr(DatabaseManager, "DB_URL", "sqlite:///" + str(path))
    db = DatabaseManager()
    with db.engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text("INSERT INTO league_settings (year, player_slots, num_teams) VALUES (2025, :slots, 12)"),
                     {"slots": json.dumps({"QB": 1, "RB": 1, "BENCH": 1})})
        conn.execute(text("INSERT INTO game_statuses (current_bot_id, current_fantasy_week) VALUES ('jon', 4)"))
        conn.execute(text("ALTER TABLE bots ADD remaining_waiver_budget INTEGER"))
        conn.execute(text("INSERT INTO bots (id, remaining_waiver_budget) VALUES ('jon', 100)"))
    db.close()
    return path


def insert_player(conn, pid, position, rank, owner=None, bye=7):
    conn.execute("INSERT INTO players (id, full_name, allowed_positions, rank, position_rank, "
                 "availability, current_bot_id, professional_team, player_bye_week) VALUES (?,?,?,?,?,?,?,?,?)",
                 (pid, pid, json.dumps([position]), rank, rank, "DRAFTED" if owner else "AVAILABLE", owner, "BUF", bye))


def test_slot_assignment_matches_brute_force_and_handles_flexible_players():
    roster = [player("flexible", ["QB", "RB"], 20), player("qb", "QB", 15), player("rb", "RB", 8)]
    settings = {"QB": 1, "RB": 1, "BENCH": 1}
    slots = bot._slots(settings)
    possibilities = []
    for assignment in itertools.permutations(range(3)):
        if all(roster[i].positions & slots[j][1] for i, j in enumerate(assignment)):
            possibilities.append(sum(roster[i].value * (.2 if slots[j][0] == "BENCH" else 1)
                                     for i, j in enumerate(assignment)))
    assert bot._roster_value(roster, settings) == pytest.approx(max(possibilities))
    assert bot._roster_value([player("x", "WR", 30)], {"QB": 1}) == -math.inf


def test_draft_finishes_required_slots_instead_of_taking_another_star():
    roster = [player("qb", "QB", 20, owner="jon"), player("bench", "WR", 20, owner="jon")]
    available = [player("rb", "RB", 7), player("star", "WR", 40)]
    state = SimpleNamespace(roster=roster, available=available, players=roster + available,
                            teams=12, slots={"QB": 1, "RB": 1, "BENCH": 1})
    assert bot._choose_draft(state).id == "rb"


def test_superflex_draft_values_a_second_starting_quarterback():
    qb = player("starter", "QB", 24, owner="jon")
    choices = [player("second-qb", "QB", 21), player("receiver", "WR", 14)]
    state = SimpleNamespace(roster=[qb], available=choices, players=[qb] + choices,
                            teams=12, slots={"QB": 1, "SUPERFLEX": 1})
    assert bot._choose_draft(state).id == "second-qb"


def test_empty_optional_data_still_produces_an_available_pick(database):
    with sqlite3.connect(database) as conn:
        insert_player(conn, "available", "QB", 1)
        insert_player(conn, "taken", "QB", 2, "other")
        insert_player(conn, "held", "RB", 3)
        conn.execute("UPDATE players SET availability='ON_HOLD' WHERE id='held'")
    assert bot.draft_player() == "available"
    assert not bot.perform_weekly_fantasy_actions().waiver_claims


def test_no_future_actuals_injury_reports_or_projections_leak(database):
    with sqlite3.connect(database) as conn:
        insert_player(conn, "qb", "QB", 1)
        conn.execute("CREATE TABLE weekly_stats (fantasypros_id TEXT, year INTEGER, week INTEGER, FPTS REAL)")
        conn.execute("CREATE TABLE season_stats (fantasypros_id TEXT, year INTEGER, G INTEGER, FPTS REAL)")
        conn.execute("CREATE TABLE weekly_injuries (fantasypros_id TEXT, year INTEGER, week INTEGER, game_status TEXT)")
        conn.execute("CREATE TABLE external_projections (fantasypros_id TEXT, year INTEGER, week INTEGER, source TEXT, points REAL)")
        conn.execute("INSERT INTO weekly_stats VALUES ('qb',2025,3,15)")
        conn.execute("INSERT INTO external_projections VALUES ('qb',2025,0,'ESPN',340)")
    db = DatabaseManager()
    before = bot.Snapshot(db, weekly=True).players[0]
    with sqlite3.connect(database) as conn:
        conn.executemany("INSERT INTO weekly_stats VALUES ('qb',?,?,9999)", [(2025,4), (2025,15), (2024,1)])
        conn.execute("INSERT INTO season_stats VALUES ('qb',2025,17,99999)")
        conn.execute("INSERT INTO weekly_injuries VALUES ('qb',2025,5,'Out')")
        conn.execute("INSERT INTO external_projections VALUES ('qb',2025,5,'ESPN',9999)")
    after = bot.Snapshot(db, weekly=True).players[0]
    db.close()
    assert before.value == after.value
    assert before.upcoming == after.upcoming
    assert before.value == pytest.approx(.88 * 20 + .12 * 15)


def test_bye_and_current_injury_zero_this_week_without_erasing_durable_value(database):
    with sqlite3.connect(database) as conn:
        insert_player(conn, "bye", "QB", 1, bye=4)
        insert_player(conn, "hurt", "RB", 2)
        conn.execute("CREATE TABLE weekly_injuries (fantasypros_id TEXT, year INTEGER, week INTEGER, game_status TEXT)")
        conn.execute("INSERT INTO weekly_injuries VALUES ('hurt',2025,4,'Out')")
    db = DatabaseManager()
    state = bot.Snapshot(db, weekly=True)
    db.close()
    assert all(p.upcoming == 0 and p.value > 0 for p in state.players)


def test_do_not_drop_a_star_for_a_temporary_bye_replacement():
    roster = [player("star", "QB", 25, owner="jon", upcoming=0)]
    add = player("streamer", "QB", 12)
    assert bot._upgrade_gain(roster, add, "star", {"QB": 1}) is None


@pytest.mark.parametrize("budget", [0, 1, 100])
def test_claims_are_legal_ordered_alternatives_with_bounded_worst_case_spending(budget):
    roster = [player("old-qb", "QB", 4, owner="jon"), player("old-rb", "RB", 4, owner="jon")]
    available = [player("q1", "QB", 22), player("q2", "QB", 20), player("q3", "QB", 19),
                 player("r1", "RB", 16), player("r2", "RB", 15)]
    state = SimpleNamespace(roster=roster, available=available, week=4, budget=budget,
                            slots={"QB": 1, "RB": 1})
    claims = bot._waiver_claims(state)
    assert 1 <= len(claims) <= 10
    assert len({c.player_to_add_id for c in claims}) == len(claims)
    grouped = defaultdict(list)
    for claim in claims:
        assert claim.player_to_add_id in {p.id for p in available}
        assert claim.player_to_drop_id in {p.id for p in roster}
        assert 0 <= claim.bid_amount <= budget
        grouped[claim.player_to_drop_id].append(claim)
    assert len(grouped) == 2
    cap = min(budget, max(1, int(.35 * budget))) if budget else 0
    assert sum(max(c.bid_amount for c in group) for group in grouped.values()) <= cap
    # Every possible combination of wins and losses leaves a valid, improved roster.
    by_id = {p.id: p for p in roster + available}
    for winners in itertools.product(*[[None] + group for group in grouped.values()]):
        result = list(roster)
        for claim in winners:
            if claim:
                result = [p for p in result if p.id != claim.player_to_drop_id] + [by_id[claim.player_to_add_id]]
        assert bot._roster_value(result, state.slots) >= bot._roster_value(roster, state.slots)


@pytest.mark.parametrize("year", [2025, 2026])
def test_runtime_functions_against_shipped_database_copy(tmp_path, monkeypatch, year):
    """Smoke test both public functions without changing the tracked season DB."""
    path = tmp_path / "season.db"
    shutil.copyfile("data/game_states/%d/season.db" % year, path)
    monkeypatch.setattr(DatabaseManager, "DB_URL", "sqlite:///" + str(path))
    db = DatabaseManager()
    with db.engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text("INSERT INTO league_settings (year, player_slots, num_teams) VALUES (:year, :slots, 13)"),
                     {"year": year, "slots": json.dumps(bot.DEFAULT_SLOTS)})
        conn.execute(text("INSERT INTO game_statuses (current_bot_id, current_draft_pick, current_fantasy_week) VALUES ('jon',1,4)"))
        conn.execute(text("ALTER TABLE bots ADD remaining_waiver_budget INTEGER"))
        conn.execute(text("INSERT INTO bots (id, remaining_waiver_budget) VALUES ('jon',100)"))
    selected = bot.draft_player()
    assert db.get_player_by_id(selected).availability == "AVAILABLE"
    db.draft_player(selected, "jon", 1)
    # Fill a legal roster; this isn't a performance evaluation of the strategy.
    for pick in range(2, 13):
        selected = bot.draft_player()
        assert selected
        db.draft_player(selected, "jon", pick)
    actions = bot.perform_weekly_fantasy_actions()
    assert isinstance(actions, bot.AttemptedFantasyActions)
    assert len(actions.waiver_claims) <= 10
    state = bot.Snapshot(db, weekly=True)
    assert math.isfinite(bot._roster_value(state.roster, state.slots))
    db.close()
