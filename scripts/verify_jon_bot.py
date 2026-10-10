"""Verify the shipped bot through the actual server/subprocess path.

Build the wheel first, then run with the project Python. This checks compatibility
and legal actions, not competitiveness or Go scoring. --docker-image additionally
checks Linux containers and real gRPC transport using the built production image.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SLOTS = {"QB": 1, "RB": 2, "WR": 2, "SUPERFLEX": 1,
         "FLEX": 1, "K": 1, "DST": 1, "BENCH": 3}

DRIVER = '''
import json, os, sys
from google.protobuf.empty_pb2 import Empty
if os.environ.get("BOTBLITZ_VERIFY_RPC_TARGET"):
    import grpc
    from agent_pb2_grpc import AgentServiceStub
    channel = grpc.insecure_channel(os.environ["BOTBLITZ_VERIFY_RPC_TARGET"])
    grpc.channel_ready_future(channel).result(timeout=30)
    service = AgentServiceStub(channel)
else:
    from server import AgentServiceServicer
    service = AgentServiceServicer()
if sys.argv[1] == "draft":
    result = service.DraftPlayer(Empty(), timeout=60) if os.environ.get("BOTBLITZ_VERIFY_RPC_TARGET") else service.DraftPlayer(Empty(), None)
    response = {"player_id": result.player_id}
else:
    result = service.PerformWeeklyFantasyActions(Empty(), timeout=60) if os.environ.get("BOTBLITZ_VERIFY_RPC_TARGET") else service.PerformWeeklyFantasyActions(Empty(), None)
    response = {"claims": [{"add": c.player_to_add_id, "drop": c.player_to_drop_id,
                            "bid": c.bid_amount} for c in result.waiver_claims]}
print("VERIFIED_RESPONSE=" + json.dumps(response))
'''


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def legal_roster(rows):
    """Independent eligibility-only matching; doesn't use the bot's evaluator."""
    slots = []
    for name, count in SLOTS.items():
        allowed = {"FLEX": {"RB", "WR", "TE"}, "SUPERFLEX": {"QB", "RB", "WR", "TE"},
                   "BENCH": {"QB", "RB", "WR", "TE", "K", "DST"}}.get(name, {name})
        slots.extend([allowed] * count)
    matched = {}

    def assign(index, seen):
        positions = {"DST" if p.upper() in ("DEF", "D/ST") else p.upper()
                     for p in json.loads(rows[index]["allowed_positions"])}
        for slot, allowed in enumerate(slots):
            if slot not in seen and positions & allowed:
                seen.add(slot)
                if slot not in matched or assign(matched[slot], seen):
                    matched[slot] = index
                    return True
        return False

    return all(assign(i, set()) for i in range(len(rows)))


def invoke(runtime, action):
    env = dict(os.environ)
    env.pop("BOTBLITZ_EVAL_INLINE", None)  # always use production subprocess isolation
    env["PYTHONPATH"] = str(runtime)
    target_file = runtime / "rpc-target.txt"
    if target_file.exists():
        env["BOTBLITZ_VERIFY_RPC_TARGET"] = target_file.read_text().strip()
    else:
        env.pop("BOTBLITZ_VERIFY_RPC_TARGET", None)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.defpath
    started = time.monotonic()
    process = subprocess.run([sys.executable, "verify_driver.py", action], cwd=runtime,
                             env=env, text=True, capture_output=True, timeout=60)
    require(process.returncode == 0, process.stdout + process.stderr)
    response = [s for s in process.stdout.splitlines() if s.startswith("VERIFIED_RESPONSE=")]
    require(len(response) == 1, "Missing typed server response: " + process.stdout + process.stderr)
    return json.loads(response[0].split("=", 1)[1]), round(time.monotonic() - started, 3)


def seed_state(path, year):
    with sqlite3.connect(path) as conn:
        # The server's SDK sees the same table names and fields as initialized
        # production state. Reference data is untouched in this disposable copy.
        conn.executescript('''
            DROP TABLE IF EXISTS league_settings;
            DROP TABLE IF EXISTS game_statuses;
            DROP TABLE IF EXISTS bots;
            CREATE TABLE league_settings (id INTEGER PRIMARY KEY, year INTEGER,
                player_slots JSON, num_teams INTEGER, is_snake_draft BOOLEAN,
                total_rounds INTEGER, points_per_reception FLOAT);
            CREATE TABLE game_statuses (id INTEGER PRIMARY KEY, current_bot_id TEXT,
                current_draft_pick INTEGER, current_fantasy_week INTEGER);
            CREATE TABLE bots (id TEXT PRIMARY KEY, draft_order INTEGER, name TEXT,
                owner TEXT, current_waiver_priority INTEGER, remaining_waiver_budget INTEGER);
            UPDATE players SET availability='AVAILABLE', current_bot_id=NULL, pick_chosen=NULL;
        ''')
        conn.execute("INSERT INTO league_settings VALUES (1,?,?,13,1,12,1.0)", (year, json.dumps(SLOTS)))
        conn.execute("INSERT INTO game_statuses VALUES (1,'12',1,1)")
        conn.execute("INSERT INTO bots VALUES ('12',1,'Jon','Jon',0,100)")


def verify_year(runtime, year):
    source = ROOT / ("data/game_states/%d/season.db" % year)
    before = sha256(source)
    database = runtime / "gamestate.db"
    shutil.copyfile(source, database)
    seed_state(database, year)
    timings, picks = [], []
    # Twelve successive standalone invocations. Opponents are not simulated here;
    # this proves pick validity, slot coverage, and reconstruction across calls.
    for pick in range(1, 13):
        response, elapsed = invoke(runtime, "draft")
        timings.append(elapsed)
        with sqlite3.connect(database) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM players WHERE id=?", (response["player_id"],)).fetchone()
            require(row is not None and row["availability"] == "AVAILABLE" and not row["current_bot_id"],
                    "Draft returned an unavailable or unknown player")
            picks.append({"pick": pick, "id": row["id"], "name": row["full_name"]})
            conn.execute("UPDATE players SET availability='DRAFTED',current_bot_id='12',pick_chosen=? WHERE id=?",
                         (pick, row["id"]))
            conn.execute("UPDATE game_statuses SET current_draft_pick=?", (pick + 1,))
            roster = conn.execute("SELECT * FROM players WHERE current_bot_id='12'").fetchall()
            require(legal_roster(roster), "Draft produced an infeasible roster")
    # Deliberately weak but legal roster: require meaningful, nonempty upgrades.
    # This is a controlled scenario, not the quality of the roster drafted above.
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("UPDATE players SET availability='AVAILABLE',current_bot_id=NULL,pick_chosen=NULL")
        pool = conn.execute("SELECT * FROM players ORDER BY rank DESC,id").fetchall()
        own_ids = set()
        for pos, count in (("QB", 2), ("RB", 4), ("WR", 4), ("K", 1), ("DST", 1)):
            choices = [r for r in pool if pos in json.loads(r["allowed_positions"])][:count]
            require(len(choices) == count, "Insufficient players for weak-roster fixture")
            for row in choices:
                own_ids.add(row["id"])
                conn.execute("UPDATE players SET availability='DRAFTED',current_bot_id='12' WHERE id=?", (row["id"],))
    waiver_runs = []
    for week, budget in ((4, 100), (4, 1), (4, 0), (17, 100)):
        with sqlite3.connect(database) as conn:
            conn.execute("UPDATE game_statuses SET current_fantasy_week=?", (week,))
            conn.execute("UPDATE bots SET remaining_waiver_budget=?", (budget,))
        response, elapsed = invoke(runtime, "weekly")
        timings.append(elapsed)
        claims = response["claims"]
        require(0 < len(claims) <= 6, "Weak roster should produce 1–6 useful claims")
        groups, additions = {}, set()
        with sqlite3.connect(database) as conn:
            conn.row_factory = sqlite3.Row
            for claim in claims:
                add = conn.execute("SELECT * FROM players WHERE id=?", (claim["add"],)).fetchone()
                require(add is not None and add["availability"] == "AVAILABLE" and not add["current_bot_id"],
                        "Claim targets an unavailable player")
                require(claim["drop"] in own_ids, "Claim drops another team's player")
                require(claim["add"] not in additions, "Duplicate waiver target")
                require(0 <= claim["bid"] <= budget, "Invalid bid")
                additions.add(claim["add"])
                groups[claim["drop"]] = max(groups.get(claim["drop"], 0), claim["bid"])
        cap = min(budget, max(1, int(budget * (.35 if week < 12 else 1.0)))) if budget else 0
        require(sum(groups.values()) <= cap, "Possible winning claims overspend weekly allocation")
        waiver_runs.append({"week": week, "budget": budget, "claims": claims,
                            "maximum_possible_spend": sum(groups.values()), "seconds": elapsed})
    require(sha256(source) == before, "Tracked season database changed")
    return {"year": year, "status": "PASS", "draft_picks": picks, "waiver_runs": waiver_runs,
            "max_invocation_seconds": max(timings), "source_database_sha256": before,
            "source_database_unchanged": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, default=ROOT / "dist/blitz_env-0.1.0-py3-none-any.whl")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--docker-image", help="Built production image, e.g. py_grpc_server")
    args = parser.parse_args()
    require(args.wheel.is_file(), "Build the runtime SDK wheel first (make build-py-module)")
    report = {"bot_sha256": sha256(ROOT / "bots/nfl2025/jon_bot.py"),
              "wheel_sha256": sha256(args.wheel), "execution": "production server methods + isolated subprocesses; no Docker or socket transport",
              "seasons": []}
    if args.docker_image:
        report["execution"] = "production Docker image + gRPC + isolated subprocesses; controlled fixtures, no Go engine"
        report["docker_image"] = {"name": args.docker_image, "id": subprocess.check_output(
            ["docker", "image", "inspect", "--format", "{{.Id}}", args.docker_image], text=True).strip()}
        # Colima mounts the repository; its guest does not share macOS /tmp.
        (ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jon-bot-runtime-", dir=ROOT / "tmp" if args.docker_image else None) as folder:
        runtime = Path(folder)
        with zipfile.ZipFile(args.wheel) as wheel:
            wheel.extractall(runtime)
        shutil.copyfile(ROOT / "bots/nfl2025/jon_bot.py", runtime / "bot.py")
        for filename in ("server.py", "isolate_action.py"):
            shutil.copyfile(ROOT / "py_grpc_server" / filename, runtime / filename)
        for filename in ("agent_pb2.py", "agent_pb2_grpc.py"):
            shutil.copyfile(runtime / "blitz_env" / filename, runtime / filename)
        (runtime / "verify_driver.py").write_text(DRIVER)
        for year in (2025, 2026):
            container_id = None
            try:
                if args.docker_image:
                    (runtime / "gamestate.db").touch()
                    resources = runtime / "resources"
                    resources.mkdir(exist_ok=True)
                    shutil.copyfile(runtime / "bot.py", resources / "bot.py")
                    container_id = subprocess.check_output([
                        "docker", "run", "-d", "-p", "127.0.0.1::8080",
                        "-v", str(resources) + ":/botblitz:ro",
                        "-v", str(runtime / "gamestate.db") + ":/app/py_grpc_server/gamestate.db:ro",
                        args.docker_image], text=True).strip()
                    target = subprocess.check_output(["docker", "port", container_id, "8080/tcp"], text=True).strip()
                    (runtime / "rpc-target.txt").write_text(target)
                report["seasons"].append(verify_year(runtime, year))
            finally:
                if container_id:
                    subprocess.run(["docker", "rm", "-f", container_id], check=True, stdout=subprocess.DEVNULL)
    output = json.dumps(report, indent=2)
    if args.report:
        args.report.write_text(output + "\n")
    print(output)


if __name__ == "__main__":
    main()
