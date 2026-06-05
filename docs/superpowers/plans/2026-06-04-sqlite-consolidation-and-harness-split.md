# SQLite Consolidation & Runtime/Harness Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the dead CSV draft/scoring backend from `blitz_env`, extract the one live helper (`is_drafted`) into a lean module, move the SQLite simulation code into a new top-level `harness/` package (not shipped in the wheel), and capture the resulting architecture in a consolidated `CLAUDE.md`.

**Architecture:** `blitz_env` becomes a lean **runtime SDK** imported inside the container (no matplotlib, no `bots.*`, no simulation). A new top-level **`harness/`** package holds local testing/simulation (`simulate_draft`, `score_game`) and depends on `blitz_env` one-way. The dependency is strictly `harness → blitz_env`, never the reverse.

**Tech Stack:** Python 3 (setuptools wheel, sqlalchemy, protobuf), Docker (`py_grpc_server` image), Jupyter (`SimulateDraft.ipynb`), Make.

**Spec:** `docs/superpowers/specs/2026-06-04-sqlite-consolidation-and-harness-split-design.md`

**Branch:** `sqlite-consolidation-harness-split` (already created; the spec commit is its first commit).

---

## Key facts the implementer must not violate

- `blitz_env/__init__.py` must **never** import `harness`, `matplotlib`, or anything under `bots/`. It is loaded inside the container where no `bots` package exists.
- `is_drafted` must stay importable as `from blitz_env import is_drafted` — `py_grpc_server/bot.py:1` depends on it. It must be the **protobuf** version (`player.status.availability == PlayerStatus.Availability.DRAFTED`), not the sqlite-`models.Player` string version.
- `load_players.py` is shared (the harness imports it). Do **not** delete or move it.
- Do not edit user bot files (`bots/nfl2025/*.py`, `bots/archive/**`) except adding the new `bots/archive/2024/README.md` in Task 7.

---

## File structure (what changes)

**Create:**
- `blitz_env/player_utils.py` — protobuf `is_drafted` (deps: `agent_pb2` only).
- `harness/__init__.py` — new package marker.
- `harness/simulate_draft.py` — moved from `blitz_env/simulate_draft_sqlite.py`, `chris_bot` coupling removed.
- `harness/score_game.py` — moved from `blitz_env/score_game_sqlite.py`.
- `bots/archive/2024/README.md` — archive note.
- `CLAUDE.md` — rewritten/consolidated (replaces the current one).

**Modify:**
- `blitz_env/__init__.py` — drop CSV import, add `player_utils`, rewrite header comment.
- `setup.py` — constrain `find_packages` so `harness/` is excluded from the wheel.
- `SimulateDraft.ipynb` — repoint 3 import lines to `harness`.

**Delete:**
- `blitz_env/simulate_draft.py` (CSV)
- `blitz_env/score_game.py` (CSV)
- `blitz_env/simulate_draft_sqlite.py` (moved to harness)
- `blitz_env/score_game_sqlite.py` (moved to harness)
- `ARCHITECTURE_NOTES.tmp.md` (untracked scratch, folded into CLAUDE.md)

---

## Task 1: Extract `is_drafted` into a lean SDK module and slim `__init__.py`

**Files:**
- Create: `blitz_env/player_utils.py`
- Modify: `blitz_env/__init__.py`

- [ ] **Step 1: Create `blitz_env/player_utils.py`**

```python
from blitz_env.agent_pb2 import Player, PlayerStatus


def is_drafted(player: Player) -> bool:
    """True if a (protobuf) player has been drafted or is on hold.

    Operates on agent_pb2.Player (status.availability enum). This is the runtime-SDK
    helper imported by py_grpc_server/bot.py; keep it dependency-light (agent_pb2 only).
    """
    return player.status.availability in (
        PlayerStatus.Availability.DRAFTED,
        PlayerStatus.Availability.ON_HOLD,
    )
```

- [ ] **Step 2: Rewrite the header comment + CSV import in `blitz_env/__init__.py`**

Replace this exact block (lines 1–11):

```python
# blitz_env is the public package that bots import at runtime (shipped as the
# blitz_env-0.1.0 wheel baked into the py_grpc_server Docker image). Two draft/scoring
# backends coexist on purpose and must both remain importable:
#   - CSV / in-memory:  load_players, simulate_draft, score_game  (init_game_state(year))
#   - sqlite-backed:    *_sqlite modules + models.DatabaseManager  (init_database(year))
# The 2025 engine uses the sqlite/DatabaseManager path; the CSV path is kept for
# backward compatibility with existing and historically-fetched bots. Do not remove
# either backend without an intentional, versioned API deprecation.
from .load_players import load_players
from .stats_db import StatsDB
from .simulate_draft import is_drafted, simulate_draft, visualize_draft_board
```

with:

```python
# blitz_env is the public runtime SDK that bots import inside the container (shipped as
# the blitz_env-0.1.0 wheel baked into the py_grpc_server Docker image). Keep it LEAN:
# do NOT import matplotlib, anything under bots/, or simulation code here. Local
# testing/simulation lives in the top-level `harness/` package, which depends on
# blitz_env one-way and is NOT shipped in the wheel.
# The single data backend is sqlite via models.DatabaseManager (init_database). The
# legacy CSV/in-memory backend (simulate_draft.py, score_game.py) was removed in the
# 2025 consolidation; load_players and is_drafted (player_utils) are retained helpers.
from .load_players import load_players
from .stats_db import StatsDB
from .player_utils import is_drafted
```

- [ ] **Step 3: Verify `import blitz_env` and `is_drafted` still resolve**

Run:
```bash
cd /Users/mitch/Documents/botblitz && python3 -c "import blitz_env; from blitz_env import is_drafted; print('is_drafted', is_drafted)"
```
Expected: prints `is_drafted <function is_drafted at 0x...>` with no ImportError.

Note: at this point `blitz_env/simulate_draft.py` still exists but is no longer imported by `__init__.py`. That's fine — it's deleted in Task 2.

- [ ] **Step 4: Commit**

```bash
cd /Users/mitch/Documents/botblitz
git add blitz_env/player_utils.py blitz_env/__init__.py
git commit -m "Extract is_drafted into blitz_env.player_utils; drop CSV import from __init__

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Delete the dead CSV backend

**Files:**
- Delete: `blitz_env/simulate_draft.py`, `blitz_env/score_game.py`

- [ ] **Step 1: Confirm nothing live still imports the CSV modules**

Run:
```bash
cd /Users/mitch/Documents/botblitz
grep -rnE "from blitz_env\.simulate_draft import|from blitz_env\.score_game import|from \.simulate_draft import|from \.score_game import" . --include="*.py" --include="*.ipynb" | grep -v __pycache__ | grep -v _sqlite | grep -v "/archive/"
```
Expected: **no output** (the only former importer, `__init__.py`, was rewired in Task 1). Matches in `bots/archive/2024/*` are acceptable and out of scope — they are reference-only.

- [ ] **Step 2: Delete the two CSV modules**

```bash
cd /Users/mitch/Documents/botblitz
git rm blitz_env/simulate_draft.py blitz_env/score_game.py
```

- [ ] **Step 3: Verify `import blitz_env` still succeeds after deletion**

Run:
```bash
cd /Users/mitch/Documents/botblitz && python3 -c "import blitz_env; from blitz_env import is_drafted, load_players, StatsDB; from blitz_env.models import DatabaseManager; print('blitz_env OK')"
```
Expected: prints `blitz_env OK`.

- [ ] **Step 4: Commit**

```bash
cd /Users/mitch/Documents/botblitz
git commit -m "Delete dead CSV backend (simulate_draft.py, score_game.py)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Create the `harness/` package and move the SQLite simulation modules

**Files:**
- Create: `harness/__init__.py`
- Move: `blitz_env/simulate_draft_sqlite.py` → `harness/simulate_draft.py`
- Move: `blitz_env/score_game_sqlite.py` → `harness/score_game.py`
- Modify: `harness/simulate_draft.py` (remove `chris_bot` coupling)

- [ ] **Step 1: Create the package and move both modules (preserving git history)**

```bash
cd /Users/mitch/Documents/botblitz
mkdir -p harness
git mv blitz_env/simulate_draft_sqlite.py harness/simulate_draft.py
git mv blitz_env/score_game_sqlite.py harness/score_game.py
printf '"""Local testing/simulation harness for BotBlitz bots.\n\nDepends on blitz_env (one-way). Not shipped in the blitz_env wheel / Docker image.\n"""\n' > harness/__init__.py
```

- [ ] **Step 2: Remove the `chris_bot` top-level import in `harness/simulate_draft.py`**

Delete this exact line:

```python
from bots.nfl2025.chris_bot import draft_player as chris_draft_player
```

- [ ] **Step 3: Remove the hardcoded `chris` opponent block in `simulate_draft()`**

In `harness/simulate_draft.py`, replace this exact block:

```python
        draft_strategy_map[user_bot.id] = draft_player

        available_bots = [bot for bot in bots if bot is not user_bot]
        second_bot = random.choice(available_bots)
        second_bot.owner = "Bot Chris"
        second_bot.name = "Chris's real bot"
        draft_strategy_map[second_bot.id] = chris_draft_player

        db.session.commit()
```

with:

```python
        draft_strategy_map[user_bot.id] = draft_player

        # All other bots use the built-in default_draft_strategy (set above).

        db.session.commit()
```

- [ ] **Step 4: Verify the harness imports cleanly from the repo root**

Run:
```bash
cd /Users/mitch/Documents/botblitz && python3 -c "from harness.simulate_draft import init_database, simulate_draft, visualize_draft_board, default_draft_strategy; from harness.score_game import score_draft_for_visualization; print('harness OK')"
```
Expected: prints `harness OK`. (Requires the harness's heavy deps — matplotlib, nfl_data_py, pandas, rich — to be installed locally; install from `requirements.txt` if missing.)

- [ ] **Step 5: Verify `blitz_env` is NOT affected by the harness still — no `bots`/`harness` leak**

Run:
```bash
cd /Users/mitch/Documents/botblitz && python3 -c "import sys, blitz_env; assert 'matplotlib' not in sys.modules, 'matplotlib leaked into import blitz_env'; assert not any(m=='bots' or m.startswith('bots.') for m in sys.modules), 'bots leaked'; print('blitz_env import surface clean')"
```
Expected: prints `blitz_env import surface clean`.

- [ ] **Step 6: Commit**

```bash
cd /Users/mitch/Documents/botblitz
git add harness/__init__.py harness/simulate_draft.py harness/score_game.py
git commit -m "Move SQLite simulation into top-level harness/; drop chris_bot coupling

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Exclude `harness/` from the wheel and prove it

**Files:**
- Modify: `setup.py`

- [ ] **Step 1: Constrain `find_packages` in `setup.py`**

Replace this exact line:

```python
    packages=find_packages(),  # Automatically find packages in subfolders
```

with:

```python
    packages=find_packages(include=["blitz_env", "blitz_env.*"]),  # ship only the runtime SDK; harness/ stays out of the wheel
```

- [ ] **Step 2: Build the wheel**

Run:
```bash
cd /Users/mitch/Documents/botblitz && make build-py-module
```
Expected: build succeeds and produces `dist/blitz_env-0.1.0-py3-none-any.whl`.

- [ ] **Step 3: Verify the wheel contains `blitz_env` but NOT `harness`**

Run:
```bash
cd /Users/mitch/Documents/botblitz && unzip -l dist/blitz_env-0.1.0-py3-none-any.whl | grep -E "blitz_env/|harness/" | head -40
echo "--- harness count (must be 0) ---"
unzip -l dist/blitz_env-0.1.0-py3-none-any.whl | grep -c "harness/" || true
```
Expected: many `blitz_env/...` entries; the `harness/` count line prints `0`.

- [ ] **Step 4: Commit**

```bash
cd /Users/mitch/Documents/botblitz
git add setup.py
git commit -m "Exclude harness/ from the blitz_env wheel (find_packages include)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Repoint `SimulateDraft.ipynb` imports to `harness`

**Files:**
- Modify: `SimulateDraft.ipynb`

- [ ] **Step 1: Update the `init_database` import**

Replace this exact string in the notebook:

```
from blitz_env.simulate_draft_sqlite import init_database\n
```

with:

```
from harness.simulate_draft import init_database\n
```

- [ ] **Step 2: Update the `simulate_draft`/`visualize_draft_board` import**

Replace this exact string:

```
from blitz_env.simulate_draft_sqlite import simulate_draft, visualize_draft_board\n
```

with:

```
from harness.simulate_draft import simulate_draft, visualize_draft_board\n
```

- [ ] **Step 3: Update the `score_draft_for_visualization` import**

Replace this exact string:

```
from blitz_env.score_game_sqlite import score_draft_for_visualization\n
```

with:

```
from harness.score_game import score_draft_for_visualization\n
```

- [ ] **Step 4: Verify the notebook JSON is still valid and references only `harness`**

Run:
```bash
cd /Users/mitch/Documents/botblitz
python3 -c "import json; json.load(open('SimulateDraft.ipynb')); print('notebook JSON valid')"
grep -nE "simulate_draft_sqlite|score_game_sqlite" SimulateDraft.ipynb && echo "STALE REF FOUND" || echo "no stale _sqlite refs"
```
Expected: prints `notebook JSON valid` then `no stale _sqlite refs`.

- [ ] **Step 5: Commit**

```bash
cd /Users/mitch/Documents/botblitz
git add SimulateDraft.ipynb
git commit -m "Repoint SimulateDraft.ipynb imports to harness package

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Container runtime proof (the real verification)

**Files:** none (verification only). Requires a running Docker daemon.

- [ ] **Step 1: Rebuild the runtime image with the new wheel**

Run:
```bash
cd /Users/mitch/Documents/botblitz && make build-docker
```
Expected: image `py_grpc_server` builds successfully (runs `gen-python-only` → `build-py-module` → `docker build`).

- [ ] **Step 2: Verify the runtime SDK imports inside the container**

Run:
```bash
docker run --rm py_grpc_server python3 -c "import blitz_env; from blitz_env import is_drafted, load_players, StatsDB; from blitz_env.models import DatabaseManager; print('container blitz_env OK')"
```
Expected: prints `container blitz_env OK`.

- [ ] **Step 3: Verify the default `bot.py` imports inside the container (its `from blitz_env import is_drafted` path)**

Run:
```bash
docker run --rm py_grpc_server sh -c "cd /app/py_grpc_server && python3 -c 'import bot; print(\"bot.py imports OK\")'"
```
Expected: prints `bot.py imports OK`.

- [ ] **Step 4: Confirm `harness` is NOT present in the container**

Run:
```bash
docker run --rm py_grpc_server python3 -c "import importlib.util; print('harness present' if importlib.util.find_spec('harness') else 'harness absent (correct)')"
```
Expected: prints `harness absent (correct)`.

- [ ] **Step 5: Commit (no-op marker)**

No file changes in this task. If Docker is unavailable in the execution environment, record that Steps 1–4 were deferred and must be run before merge; do not mark the task complete on a green build alone.

---

## Task 7: Archive note (Part B)

**Files:**
- Create: `bots/archive/2024/README.md`

- [ ] **Step 1: Write the archive note**

```markdown
# 2024 bots (historical reference)

These are the **2024 draft-only** BotBlitz bots. They target the legacy **CSV/in-memory**
`blitz_env` backend — top-level imports like `from blitz_env import simulate_draft,
visualize_draft_board, is_drafted` and the `init_game_state(year)` entry point.

That backend was **removed in the 2025 SQLite consolidation** (see
`docs/superpowers/specs/2026-06-04-sqlite-consolidation-and-harness-split-design.md`).
Simulation now lives in the top-level `harness/` package and the data model is sqlite via
`blitz_env.models.DatabaseManager`.

**These bots are kept for historical reference only and are NOT expected to import or run
against the current `blitz_env` wheel.** Do not "fix" them to the new API — if you need a
2025-era example, see `bots/nfl2025/`.
```

- [ ] **Step 2: Commit**

```bash
cd /Users/mitch/Documents/botblitz
git add bots/archive/2024/README.md
git commit -m "Document 2024 archive bots target the removed CSV backend

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Consolidated `CLAUDE.md` (Part C)

**Files:**
- Modify (full rewrite): `CLAUDE.md`

- [ ] **Step 1: Replace `CLAUDE.md` with the consolidated version**

Overwrite `CLAUDE.md` with the following content:

```markdown
# Claude Code Documentation — BotBlitz

Context and rules for working in this repo (human + agentic). These instructions take
precedence over default behavior.

## 1. What this repo is

A bot-vs-bot **fantasy football engine**. Human-written Python "bots" draft players and make
weekly roster decisions; a Go engine orchestrates them, each running sandboxed in its own
Docker container. It has always been a fantasy-football project — a brief NBA exploration was
abandoned and left only stray artifacts (not the repo's origin).

Season evolution: **2024 = draft only. 2025 = added season gameplay + add/drop**, and moved
the draft/scoring data model to **SQLite**.

## 2. Languages & ownership

- **Go (`pkg/`)** — the engine: orchestration, Docker container lifecycle, game state /
  SQLite, draft + weekly-fantasy + playoff logic, Google Sheets output.
- **Python (`blitz_env/`, `py_grpc_server/`, `bots/`)** — the runtime bot SDK (`blitz_env`),
  the gRPC server that runs inside each container, stats-collection scripts, and user bots.
- **Python (`harness/`)** — local testing/simulation (NOT shipped to the container).
- **R (`fetch_ranks.R`)** — legacy ranking scraper; `player_ranks_*.csv` are the artifacts.
- **JS/React (`ux/`)** — a Datasette-backed web viewer (create-react-app).

## 3. ⚠️ Guardrails (read before refactoring)

- **`blitz_env` is the public runtime SDK / wheel API.** It is built into a wheel
  (`blitz_env-0.1.0-py3-none-any.whl`) baked into the `py_grpc_server` Docker image, and bots
  import it at runtime (some historical bots fetch source from pinned GitHub raw URLs).
  Renaming/removing a top-level name is a breaking change.
- **Keep `blitz_env` lean.** `blitz_env/__init__.py` must never import `matplotlib`, anything
  under `bots/`, or simulation code — it is loaded inside the container where no `bots`
  package exists. `is_drafted` lives in `blitz_env/player_utils.py` (protobuf, dependency-light)
  precisely so `import blitz_env` stays light; `py_grpc_server/bot.py` depends on it.
- **Single data backend: sqlite via `models.DatabaseManager` (`init_database`).** The legacy
  CSV/in-memory backend was removed in the 2025 consolidation; `load_players` and `is_drafted`
  are retained helpers.
- **Runtime SDK vs harness.** Local simulation/testing (`simulate_draft`, `visualize_draft_board`,
  `score_game`) lives in the top-level **`harness/`** package. The dependency is one-way
  (`harness` → `blitz_env`), and `harness/` is **not** in the wheel. `harness/simulate_draft.py`
  pulls in heavy deps (matplotlib, nfl_data_py, pandas) so it must stay out of the runtime SDK.
- **Don't edit user bot files** (`bots/nfl2025/*.py`, `bots/archive/**`) to satisfy a refactor.

## 4. Runtime model (how a bot reaches a container)

1. The Go engine writes the selected bot's source to host `/tmp/bot.py`
   (`pkg/engine/ContainerHandler.go`, const `botFileRelativePath = /tmp/bot.py`).
2. It bind-mounts host `/tmp` → container `/botblitz`.
3. `py_grpc_server/bootstrap.sh` runs `cp -r /botblitz/* /app/py_grpc_server` then
   `python3 -u server.py`.
4. `server.py` / `isolate_action.py` do `from bot import draft_player,
   perform_weekly_fantasy_actions` and `import blitz_env`.

So the user's `bot.py` overwrites the default `py_grpc_server/bot.py` at runtime, and every
bot runs against the current wheel.

## 5. Build & codegen

- `make gen` — regenerates proto for **both** Go (`pkg/common/agent*.pb.go`) and Python.
- `make gen-python-only` — Python stubs only; copies them into `py_grpc_server/`.
- `make build-py-module` — copies root `player_ranks_*.csv` into `blitz_env/`, builds the
  wheel into `dist/`.
- `make build-docker` — runs `gen-python-only` → `build-py-module` → `docker build`.
- **Generated, NOT tracked:** `blitz_env/agent_pb2*.py`, `py_grpc_server/agent_pb2*.py`,
  `blitz_env/player_ranks_*.csv`. **Exception:** the Go proto `pkg/common/agent*.pb.go` **is**
  tracked — and `make clean` deletes it, so after `make clean` you must `make gen` or
  `git checkout` to restore.

## 6. Running the engine

Entry: `pkg/cmd/engine_bootstrap.go` (module name is `pkg/runner`, not `pkg/cmd`).

Game modes (`pkg/engine/GameMode.go`): `Draft`, `PerformWeeklyFantasyActions`,
`UpdateWeeklyScores`, `FinishPreviousWeek`. Makefile wrappers: `run-draft`,
`run-weekly-fantasy`, `update-scores`, `run-finish-week`.

Flags: `-game_mode`, `-year` (default 2025), `-enable_google_sheets` (default true),
`-enable_verbose_logging`, `-is_running_on_github`.

- The bot roster is **hardcoded** in `engine_bootstrap.go`: it loads bot **source** from
  `bots/nfl2025/*.py` and **env vars** from `bots/nfl/envs/*.env`.
- `bots/nfl/envs/*.env` is **gitignored** (per-bot secrets). A full local draft needs **every**
  configured bot's env file or it exits — an env-setup limitation, not a code bug.
- Requires a running **Docker daemon**; the engine expects the `py_grpc_server` image to
  already exist (`make build-docker`).

## 7. Local simulation / harness

`harness/` + `SimulateDraft.ipynb` are the local way to test a bot's draft logic without the
full engine. `make launch-simulator` builds the wheel, installs it, and opens the notebook +
a Datasette browser on a dev snapshot. The harness imports `blitz_env` and takes the bot's
`draft_player` as a parameter; other teams use `default_draft_strategy`.

## 8. Verification (how to actually prove a change works)

- **Build ≠ proof.** Building the wheel/image does not exercise the runtime path.
- Real proof = run the engine, or exercise the container directly:
  - `docker run --rm py_grpc_server python3 -c "import blitz_env; from blitz_env import
    is_drafted; from blitz_env.models import DatabaseManager"`
  - `docker run --rm py_grpc_server sh -c "cd /app/py_grpc_server && python3 -c 'import bot'"`
- Go tests live only in `pkg/engine` (`go test ./...` there). `pkg/gamestate` has its own
  `go.mod` but is **not** in `go.work`; running `./...` from it errors and it has no tests.

## 9. Data & source of truth

### Source of truth: `data/stats/{year}/stats.db` ✅
Raw stats & projections. Tables: `preseason_projections`, `season_stats`, `weekly_stats`,
`weekly_injuries`. Created by `python3 blitz_env/collect_stats.py --db
data/stats/{year}/stats.db --end-year {year} --years 10`; weekly updates via
`python3 -m blitz_env.collect_weekly_stats --year {year} --week {week} --db
data/stats/{year}/stats.db` and `collect_weekly_injuries`. Updated by the
`update-scores.yml` action. Tracked in git.

### Game state: `data/game_states/{year}/gs-{draft|season}.db`
The actual league state (bots, players, matchups, league_settings, game_statuses) **plus
copies** of the stats tables. When games are created/updated, Go **ATTACHES**
`data/stats/{year}/stats.db` and **COPIES** the stats tables in
(`pkg/gamestate/handler.go:580-621`), so each game snapshots stats at creation time;
`RefreshWeeklyStats()` re-copies. Tracked in git.

### Archived dev snapshots
`data/archive/2025/{stats,gamestate}.db` — old dev snapshots, used only by
`make launch-simulator`, not production.

### Key code references
- Stats DB path: `pkg/gamestate/handler.go:553-561` (`getStatsDatabaseFilePath`)
- Game state DB path: `pkg/gamestate/handler.go:740-754` (`getSaveFileName`)
- Stats table population: `pkg/gamestate/handler.go:602-650` (`populateStatsTables`)
- Weekly stats refresh: `pkg/gamestate/handler.go:563-600` (`RefreshWeeklyStats`)

### Constants
```go
const saveFolderRelativePath = "data/game_states"  // Game state DBs
const statsFolderRelativePath = "data/stats"       // Stats DBs (source of truth)
const statsDatabaseFileName = "stats.db"
```

### Injury data
Scraped from NFL.com; fuzzy-matched (rapidfuzz) on `(year, week, player_name, position)` to
FantasyPros IDs. Fields: `player_name`, `team`, `position`, `injury`, `practice_status`,
`game_status`, `fantasypros_id`, `gsis_id`, `sleeper_id`.

### Historical backfill
```bash
python3 blitz_env/collect_stats.py --db data/stats/2025/stats.db --end-year 2025 \
  --years 10 --include-weekly --include-injuries --weeks 1:18
```
Warning: ~180 HTTP requests to NFL.com for injury data; 5–10 min, possible rate limiting.

## 10. CI / GitHub Actions

`.github/workflows/`: `update-scores.yml`, `weekly-fantasy.yml`, `finish-week.yml`,
`download-weekly-data.yml`, `core-validations.yml`. Several scheduled triggers are disabled
for the offseason. `update-scores` treats weekly **stats** as mission-critical (must succeed)
and **projections/injuries** as best-effort (continue-on-failure).

## 11. Caveats

- `make clean` deletes the tracked Go proto — regenerate (`make gen`) or `git checkout` after.
- Git history is **not** trustworthy for secrets: a defunct workflow's Discord webhook +
  odds-API key were committed and later removed from the tree but **remain in history** —
  rotation is the real fix. `sheets-creds.json` (live Google key) is gitignored and was never
  committed.
- ~120 MB of old binaries/DBs linger in git history (out of scope to purge; would need
  `git filter-repo` + force-push).
```

- [ ] **Step 2: Verify the file is valid markdown and references the new architecture**

Run:
```bash
cd /Users/mitch/Documents/botblitz
grep -nE "harness/|player_utils|single data backend|SQLite consolidation|Runtime SDK vs harness" CLAUDE.md | head
```
Expected: matches showing the harness/SDK split and player_utils are documented.

- [ ] **Step 3: Commit**

```bash
cd /Users/mitch/Documents/botblitz
git add CLAUDE.md
git commit -m "Consolidate CLAUDE.md: runtime SDK vs harness, single sqlite backend

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: Remove the scratch notes and do a final sweep

**Files:**
- Delete: `ARCHITECTURE_NOTES.tmp.md`

- [ ] **Step 1: Delete the untracked scratch notes (folded into CLAUDE.md)**

```bash
cd /Users/mitch/Documents/botblitz && rm -f ARCHITECTURE_NOTES.tmp.md
```

- [ ] **Step 2: Final repo-wide sweep for stale references**

Run:
```bash
cd /Users/mitch/Documents/botblitz
echo "--- stale _sqlite / CSV-module references outside archive (expect none) ---"
grep -rnE "simulate_draft_sqlite|score_game_sqlite|blitz_env\.simulate_draft\b|blitz_env\.score_game\b" . \
  --include="*.py" --include="*.ipynb" --include="*.go" --include="Makefile" \
  | grep -v __pycache__ | grep -v "/archive/" | grep -v "docs/superpowers/" || echo "clean"
```
Expected: prints `clean` (references inside `bots/archive/**` and `docs/superpowers/**` are acceptable).

- [ ] **Step 3: Confirm working tree is clean and review the branch**

Run:
```bash
cd /Users/mitch/Documents/botblitz
git status --short
git log --oneline main..HEAD
```
Expected: `git status --short` is empty; the log shows the spec commit plus the Task 1–8 commits.

---

## Self-review (completed by plan author)

- **Spec coverage:** Part A → Tasks 1–6; Part B → Task 7; Part C → Task 8; tmp-notes cleanup → Task 9. All spec sections map to a task.
- **`is_drafted` constraint:** preserved as protobuf version in `player_utils` (Task 1) and proven via container `bot.py` import (Task 6).
- **Wheel exclusion of `harness/`:** enforced (Task 4 Step 1) and proven (Task 4 Step 3).
- **One-way dependency invariant:** proven by the import-surface check (Task 3 Step 5).
- **Type/name consistency:** `is_drafted`, `default_draft_strategy`, `simulate_draft`,
  `visualize_draft_board`, `score_draft_for_visualization`, `init_database` are used
  consistently across tasks and match the source modules.
```
