# Design: SQLite consolidation, runtime/harness split, and CLAUDE.md

**Date:** 2026-06-04
**Status:** Approved for planning

## Background

BotBlitz is a bot-vs-bot fantasy football engine: human-written Python bots draft players
and make weekly roster decisions; a Go engine orchestrates them, each running sandboxed in
its own Docker container. It has always been a fantasy-football project (a brief NBA
exploration was abandoned and left only stray artifacts — not the repo's origin).

The project evolved across seasons:
- **2024** — draft only.
- **2025** — added season gameplay + add/drop, and moved the draft/scoring data model to a
  **SQLite-backed** implementation.

That 2025 migration is why `blitz_env` currently ships **two parallel backends**:

| Backend | Modules | Entry |
|---------|---------|-------|
| CSV / in-memory (2024-era) | `load_players`, `simulate_draft`, `score_game` | `init_game_state(year)` |
| SQLite-backed (2025) | `*_sqlite` + `models.DatabaseManager` | `init_database(year)` |

The CSV path is now dead weight for live gameplay, and the package conflates two distinct
roles — the **runtime SDK** that bots import inside the container, and the **local
simulation harness** authors use to test their bots. This design removes the dead CSV path
and separates those two roles.

## Goals

1. Consolidate to the single SQLite backend; delete the dead CSV draft/scoring path.
2. Establish a clean boundary: `blitz_env` = lean **runtime SDK**; a new top-level
   `harness/` package = **local testing/simulation** (not shipped in the wheel/image).
3. Record the change for posterity (archive note) and capture the architecture in a
   consolidated `CLAUDE.md` for future human + agentic development.

## Non-goals

- Trimming heavy deps (matplotlib/rich/etc.) from the runtime Docker image. The structural
  split makes this *possible* later but it requires a dependency audit; deferred.
- Making the archived 2024 bots (`bots/archive/2024/*`) importable against the current
  wheel. They are historical reference only and are expected to break on `import blitz_env`.
- Editing user bot files (`bots/nfl2025/*.py`) beyond leaving them untouched.

## Verified facts (the dependency survey)

These shaped the design and were confirmed against the tree:

- `blitz_env/__init__.py` loads, in order: `load_players`, `stats_db`, **`simulate_draft`
  (CSV)**, `agent_pb2`, `models`, `projections_db`. It does **not** import `score_game`,
  `simulate_draft_sqlite`, or `score_game_sqlite`.
- **`score_game.py` (CSV)** has zero importers anywhere — fully orphaned.
- **`simulate_draft.py` (CSV)** is imported only by `__init__.py:11`
  (`is_drafted, simulate_draft, visualize_draft_board`).
- The only **live** consumer of any CSV-path symbol is **`py_grpc_server/bot.py:1`**:
  `from blitz_env import is_drafted` (used on protobuf `game_state.players`). This runs in
  the container (it is the default `bot.py` baked into the image, overwritten at runtime by
  the user's bot). Therefore `is_drafted` must remain a top-level `blitz_env` export.
- The two `is_drafted` bodies are **not** interchangeable:
  - CSV: protobuf `Player` → `player.status.availability == PlayerStatus.Availability.DRAFTED`
  - SQLite: `models.Player` → `player.availability in ('DRAFTED','ON_HOLD')` (string)
  `bot.py` passes protobuf players, so the **protobuf** version must be preserved.
- `is_drafted` **cannot** be re-sourced from `simulate_draft_sqlite` in `__init__.py`,
  because that module top-level-imports `from bots.nfl2025.chris_bot import draft_player`;
  importing it at package load would crash `import blitz_env` in the container (no `bots`
  package present).
- `load_players.py` is **shared** — `simulate_draft_sqlite.py:3` imports it. Keep it in the SDK.
- The **container runtime imports neither `simulate_draft*` nor `score_game*`.** Both
  `*_sqlite` modules are used only by `SimulateDraft.ipynb` (plus commented-out convenience
  lines in 3 bots). Both carry heavy deps (matplotlib, numpy, rich, pandas, nfl_data_py).
- `simulate_draft()` already defaults **every** bot to the built-in `default_draft_strategy`;
  `chris_draft_player` is only assigned to one random "second bot" as flavor.
- `setup.py` uses `find_packages()` — it would auto-include a new top-level `harness/`.

## Target architecture

```
┌─────────────────────────────────────────┐     ┌──────────────────────────────────────┐
│  blitz_env  (RUNTIME SDK)                │     │  harness/  (LOCAL TEST/SIM ONLY)     │
│  imported inside the container           │ <───│  never imported at runtime           │
│                                          │ dep │                                      │
│  agent_pb2        models.DatabaseManager │     │  simulate_draft / run_draft          │
│  load_players     stats_db / StatsDB     │     │  init_database / preseason setup     │
│  projections_db   player_utils.is_drafted│     │  visualize_draft_board               │
│                                          │     │  score_draft_for_visualization       │
│  (+ collect_*/download_* CI scripts)     │     │  SimulateDraft.ipynb (entry point)   │
│                                          │     │  opponent strategy = default/param   │
│  __init__.py exports ONLY runtime API    │     │  heavy deps: matplotlib/pandas/rich/ │
│  no matplotlib · no bots.* · no sim      │     │  numpy/nfl_data_py                   │
└─────────────────────────────────────────┘     └──────────────────────────────────────┘
        ▲                                                  │
        └──────────────── harness imports blitz_env ───────┘   (one-way dependency)
```

**Invariant:** the dependency is one-way — `harness` → `blitz_env`, never the reverse.
`blitz_env/__init__.py` must never import `harness`, `matplotlib`, or anything under `bots/`.

## Part A — Runtime SDK / harness split

**A1. Extract the live helper.** New `blitz_env/player_utils.py` containing the protobuf
`is_drafted` (imports `agent_pb2` only):

```python
from blitz_env.agent_pb2 import Player, PlayerStatus

def is_drafted(player: Player) -> bool:
    return player.status.availability in (
        PlayerStatus.Availability.DRAFTED,
        PlayerStatus.Availability.ON_HOLD,
    )
```

**A2. Slim `blitz_env/__init__.py`.**
- Replace `from .simulate_draft import is_drafted, simulate_draft, visualize_draft_board`
  with `from .player_utils import is_drafted`.
- Remove `simulate_draft` and `visualize_draft_board` from the top-level API.
- Rewrite the header comment: single SQLite/`DatabaseManager` backend; simulation lives in
  the external `harness/` package; `load_players` + `is_drafted` retained as runtime SDK.

**A3. Delete the dead CSV path.** Remove `blitz_env/simulate_draft.py` and
`blitz_env/score_game.py`.

**A4. Create the `harness/` package** (top-level, local-only):
- `harness/__init__.py`
- `harness/simulate_draft.py` ← moved from `blitz_env/simulate_draft_sqlite.py`:
  - Remove `from bots.nfl2025.chris_bot import draft_player` and the `second_bot` /
    `chris_draft_player` assignment block in `simulate_draft()` (opponents already default to
    `default_draft_strategy`).
  - Keep its internal `is_drafted` (operates on `models.Player`).
  - Imports resolve from `blitz_env` (`from blitz_env.models import ...`, etc.).
- `harness/score_game.py` ← moved from `blitz_env/score_game_sqlite.py`.
- Drop the now-redundant `_sqlite` suffix.
- Delete the two `*_sqlite` originals from `blitz_env/`.

**A5. Packaging & entry point.**
- `setup.py`: `packages=find_packages(include=["blitz_env*"])` so `harness/` is excluded
  from the wheel.
- `SimulateDraft.ipynb`: update imports to
  `from harness.simulate_draft import init_database, simulate_draft, visualize_draft_board`
  and `from harness.score_game import score_draft_for_visualization`.
- Leave the commented-out `_sqlite` import lines in `bots/nfl2025/*` untouched (user files).

**A6. Deferred (document only):** trim matplotlib/rich/etc. from the runtime image after a
dependency audit.

### Part A verification (build ≠ proof)

1. `make build-py-module`; `unzip -l dist/blitz_env-0.1.0-py3-none-any.whl` shows **no
   `harness/`**.
2. `python3 -c "import blitz_env; from blitz_env import is_drafted"` succeeds; confirm
   matplotlib is not imported as a side effect of `import blitz_env`.
3. Container: `docker run --rm py_grpc_server python3 -c "import blitz_env; from blitz_env
   import is_drafted; from blitz_env.models import DatabaseManager"`.
4. Harness from repo root: `python3 -c "from harness.simulate_draft import simulate_draft,
   visualize_draft_board; from harness.score_game import score_draft_for_visualization"`.

## Part B — Archive note

Add `bots/archive/2024/README.md` recording: these 2024 draft-only bots used the legacy
CSV/in-memory `blitz_env` backend (`simulate_draft`, `score_game`, top-level
`visualize_draft_board`), removed in the 2025 SQLite consolidation. Kept for historical
reference; **not expected to import against the current wheel.**

## Part C — Consolidated `CLAUDE.md`

Single file, reflecting the post-cleanup state. Sections in order:

1. **What this repo is** — fantasy-football bot engine; 2024 = draft-only, 2025 = added
   season gameplay + add/drop and the SQLite migration; NBA was a brief abandoned
   side-exploration (explains stray artifacts only).
2. **Languages & ownership** — Go engine (`pkg/`); Python SDK + gRPC server + stats scripts
   (`blitz_env/`, `py_grpc_server/`, `bots/`); local harness (`harness/`); R legacy ranks
   (`fetch_ranks.R`); React Datasette viewer (`ux/`).
3. **⚠️ Guardrails** —
   - `blitz_env` is the public wheel API (bots import it, some from pinned GitHub URLs) →
     renaming/removing top-level names is a breaking change.
   - **Single SQLite/`DatabaseManager` backend.** The CSV path was removed in the 2025
     consolidation; `is_drafted` (`player_utils`) and `load_players` are retained.
   - **Runtime SDK vs harness:** `blitz_env` is imported inside the container and must stay
     lean (no matplotlib, no `bots.*`, no simulation). Simulation/testing lives in the
     top-level `harness/` package, which depends on `blitz_env` one-way and is **not** in the
     wheel.
   - Don't edit user bot files (`bots/nfl2025/*.py`, `bots/archive/**`).
4. **Runtime model** — bot delivery: Go writes source → `/tmp/bot.py` → bind-mount
   `/tmp`→`/botblitz` → `bootstrap.sh` copies in → `server.py` imports `bot` + `blitz_env`.
5. **Build & codegen** — `make gen` / `gen-python-only` / `build-py-module` /
   `build-docker`; tracked vs generated artifacts; `make clean` deletes the tracked Go proto
   (regenerate or `git checkout`).
6. **Running the engine** — entry point (`pkg/cmd/engine_bootstrap.go`), game modes, Make
   wrappers, flags, the hardcoded bot roster, gitignored per-bot env files, Docker daemon
   requirement.
7. **Local simulation / harness** — `harness/` + `SimulateDraft.ipynb`;
   `make launch-simulator`.
8. **Verification** — build ≠ proof; run the engine or exercise the container directly; Go
   tests only in `pkg/engine` (`pkg/gamestate` excluded from `go.work`).
9. **Data & source of truth** — preserved from the current `CLAUDE.md`: `stats.db`,
   `game_states`, archive snapshots, key code refs, constants, backfill commands, injury
   details.
10. **CI / GitHub Actions** — workflows + stats-critical / projections-injuries-best-effort
    priority; offseason triggers disabled.
11. **Caveats** — git history is not trustworthy for secrets (rotate, don't rely on tree
    removal); ~120 MB of old binaries linger in history (out of scope to purge).

## Implementation order

1. **Part A** (SDK/harness split + CSV deletion) → run Part A verification.
2. **Part B** (archive note).
3. **Part C** (consolidated `CLAUDE.md`).
4. Delete the temporary `ARCHITECTURE_NOTES.tmp.md` (untracked scratch notes, folded in).

## Risks

- **Breaking external/historical bots** that import `simulate_draft` / `visualize_draft_board`
  from `blitz_env` top level. Accepted: 2024 bots are reference-only; the names move to
  `harness`. `is_drafted` is preserved, covering the live gRPC-server dependency.
- **Wheel accidentally shipping `harness/`** if `find_packages` isn't constrained —
  covered by verification step A.1 (`unzip -l`).
- **Hidden runtime importer of a moved symbol** — mitigated by the survey above and the
  container import test (A.3).
