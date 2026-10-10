# Jon's bot

The submission is the single file `bots/nfl2025/jon_bot.py`. Its path is kept to
match the engine's participant configuration; it reads the season year and roster
rules from the database, so it can also run against the 2026 database.

## Agreed defaults

- Balanced strategy, using only data supplied by Botblitz. No API keys, downloads,
  LLM calls, extra dependencies, or files outside the bound season database.
- Both runtime entry points: `draft_player()` and
  `perform_weekly_fantasy_actions()`. The engine chooses scoring lineups.
- Stateless: every call reconstructs its inputs, including remaining FAAB.

## Draft decisions

Use the median of available preseason projection sources, converted to points
per healthy week. Missing projections fall back to a position-rank curve blended
with the previous three seasons' per-game production. Rookie players can use
rank-based estimates without historical stats. Preseason source groups whose
maximum is below 50 are ignored as suspicious weekly-sized annual projections;
this handles a limitation in the shipped 2026 FantasyPros data without guessing
its units.

Evaluate the resulting roster using an exact weighted slot assignment. FLEX and
SUPERFLEX eligibility is respected, each player occupies one slot, and bench
players receive partial value for depth. Spare kickers and defenses receive very
little depth credit. Add a positional scarcity bonus relative to the remaining
league-wide starter demand and a small penalty for matching quarterback byes.
This prevents a high-ranked player from taking the last spot needed for a legal
roster when a feasible selection exists.

These are initial heuristics, not learned or validated performance claims. In
particular, the rank fallback and projection-source scoring differences should
be revisited after engine evaluations.

## Weekly decisions

Blend the preseason estimate with recent completed-game production, gradually
increasing its influence as more games are available. Combine current-week
projections across supplied sources. Current injury reports and byes reduce the
immediate estimate; a one-week absence does not erase a player's long-term value.

Score swaps against both durable roster value (65%) and immediate value (35%).
Reject changes that surrender substantial long-term value merely to cover one
week. Only free agents marked AVAILABLE and players on this bot's roster are
eligible for claims.

Submit up to two upgrade groups, with up to three ordered alternatives for each
player being dropped (six claims maximum). Only one alternative in each group
can win. Every later swap is checked against every possible earlier win/loss
outcome, so claims remain useful and positionally valid in combination.

A single claim bids at most 30% of the remaining budget, based on roster value
gain. Worst-case spending across winning groups is capped at 35% of the remaining
budget before week 12, 60% in weeks 12–14, and the remaining budget in weeks 15–17.
Positive budgets allow a minimum one-dollar total allocation. Zero-dollar claims
remain possible after the budget or this week's allocation is exhausted. No
claims are sent when there is no worthwhile upgrade.

## Fair historical testing

Drafts ignore all current-season actual results. Weekly actuals are restricted to
the current season and weeks strictly before the decision week. Historical season
totals must precede the current season. Weekly projections and injury reports
must match the decision week. The future schedule is legitimate known information.
No module-level cache can carry information across evaluation runs.

## Local checks

A project-local `venv/` contains the dependencies for the bot's correctness checks,
and Python protobuf files have been generated locally. Run:

```sh
venv/bin/python -m pytest tests/test_jon_bot.py -q
```

Tests cover both public functions using scratch copies of the shipped 2025 and
2026 databases, slot assignment, missing optional data, SUPERFLEX, injury/bye
handling, protection against future-data leakage, and legal/budgeted waiver
alternatives. They do not measure competitiveness.

## Verification evidence (October 10, 2026)

The full Python suite passed: **25 tests**. Initial setup failures were resolved
by copying the ranking CSVs into the SDK (the normal build step) and installing
the harness's matplotlib dependency in the local virtual environment. Remaining
warnings are deprecations in the upstream SDK and plotting dependencies.

`scripts/verify_jon_bot.py` adds a reproducible packaged-runtime check. It extracts
the built SDK wheel into an isolated temporary directory and copies only this
bot, the real server, the real subprocess runner, and generated RPC stubs into it.
It calls the production server methods with inline evaluation disabled; each
decision runs in a fresh Python subprocess and passes through the production
protobuf/JSON response parsing. No repository `bots` or `harness` imports are
available there. Socket transport and Docker are not exercised.

| Check | 2025 | 2026 |
|---|---:|---:|
| Successive valid draft picks; final roster fills all slots | 12 | 12 |
| Nonempty waiver submissions from a deliberately weak legal roster | 4 | 4 |
| Budgets exercised in week 4 | 100, 1, 0 | 100, 1, 0 |
| Late-season scenario | Week 17 | Week 17 |
| Worst-case week-4 spend with 100 remaining | 35 | 35 |
| Slowest complete subprocess invocation | 1.515 s | 1.518 s |
| Source season database unchanged (SHA-256 checked) | Yes | Yes |

This is 32 successful production-runner invocations. The consecutive draft picks
do not include opponent turns; this is a compatibility and roster-validity check,
not a competitive draft simulation. Each waiver fixture returns six claims, with
legal add/drop IDs and bounded worst-case spending. The 2026 first draft pick in
this controlled scenario was Jahmyr Gibbs.

Full responses, selected player names, timings, and the bot/wheel/input hashes
are saved in `docs/jon-bot-verification.json`. To reproduce after activating the
project virtual environment and installing the repo's dependencies:

```sh
make gen-python-only
make build-py-module
python -m pytest tests -q
python scripts/verify_jon_bot.py --report /tmp/jon-bot-verification.json
```

Review: no blocking bot defect was found. Security: internal SQL identifiers,
bound values, no secrets or network calls. Performance: bounded shortlists and
two claim groups, with measured runtime well below the engine's 60-second RPC
deadline. Correctness: the Python and Go tests and both packaged and Docker runtime
scenarios pass. Maintainability: one standalone
submission file, named tuning constants, and documented heuristics.

## Docker verification (October 10, 2026)

The original `py-server-dockerfile` built successfully on Linux arm64 with its
pinned requirements. The repository build also passed with the new `.dockerignore`,
which keeps local VMs and databases out of the context. The built image ID is
recorded in `docs/jon-bot-docker-verification.json`.

The same controlled fixtures passed over real gRPC into this image, using the
production bootstrap and a fresh Python subprocess per decision. For **each** of
2025 and 2026: 12 legal draft picks and four valid waiver scenarios passed. The
slowest complete call was 1.670 seconds for 2025 and 1.653 seconds for 2026. Both
tracked input databases were unchanged. This proves container/SDK/transport
compatibility; the fixtures do not simulate opposing teams or Go scoring.

With the Docker image already built and its daemon selected:

```sh
venv/bin/python scripts/verify_jon_bot.py --docker-image py_grpc_server \
  --report /tmp/jon-bot-docker-verification.json
```

## Full engine verification (October 10, 2026)

The authoritative Go/Docker evaluator completed one full 2025 season against 13
standard-bot opponents, with production execution settings (no fast flag). It
built separate container sets for the draft and season, and each bot decision
used the production subprocess runner.

- 12 candidate draft picks; no fallback or draft RPC errors. An independent
  eligibility matcher confirmed the drafted roster fills all 12 default slots.
- All 17 weekly candidate responses succeeded. The engine accepted 69 valid
  claims, applied 30 winning claims, and received one intentional no-claims response.
  No candidate claim was rejected and no candidate weekly RPC failed.
- All 17 weeks were scored, including playoffs. Candidate finished **5th of 14**
  in the regular season: **8–6, 1,651.5 points**, made playoffs, did not win the
  championship. Total run time: **3 minutes 21.897 seconds**.
- SHA-256 checks confirmed every tracked season database remained unchanged.

Structured evidence, source hashes, draft picks, and the exact command are in
`docs/jon-bot-engine-verification.json`. The raw local log is retained at
`tmp/jon-bot-verification/engine-2025.log` (gitignored), with its hash in the report.

The standard opponents emitted 192 invalid empty example-claim lines and 29
weekly RPC failures when no playoff opponent existed. Candidate had neither
problem. This is an integration pass and one initial benchmark, not evidence of
superiority over other submitted bots or a statistically reliable performance
estimate. No other user's bot was modified.

## Season configuration and reproduction

The engine defaults to 2025; production season runs must explicitly select 2026.
Upstream main `0bfee07` includes the scoring correction: weekly stats now match
both league year and week, and the evaluator keeps the real year while writing
to a scratch folder. It was merged into this branch as `1d07046`. The Go engine
test suite passes, including the regressions for these fixes. The shipped 2026
database has actual stats only for weeks 1–3; use a complete historical season
for a meaningful full-season result.

The submission uses the existing runtime API. The upstream merge supplies the
engine correction, and workflow defaults are unchanged. Testing uses scratch
databases; tracked season data must not be modified.

For a full historical season, use the existing authoritative Go/Docker evaluator:

```sh
make evaluate-bot BOT=bots/nfl2025/jon_bot.py YEAR=2025 RUNS=3
```

This requires a running Docker daemon and the repo's Go/Python build tools on
PATH. This Mac now has Docker CLI and Colima installed. Its isolated `botblitz`
profile uses two CPUs, 4 GiB RAM, and a repository mount. From this project root,
restart it and select its socket before running the commands above:

```sh
export PATH="$PWD/venv/bin:/usr/local/go/bin:/opt/homebrew/bin:$PATH"
export DOCKER_CONFIG="$PWD/venv/docker-config"
COLIMA_HOME="$PWD/venv/colima" LIMA_HOME="$PWD/venv/lima" \
  colima start botblitz --activate=false
export DOCKER_HOST="unix://$HOME/.colima/botblitz/docker.sock"
export DOCKER_BUILDKIT=0
```

The `COLIMA_HOME` and `LIMA_HOME` paths hold local VM configuration; they are not
submission dependencies. Stop this profile after use with:

```sh
COLIMA_HOME="$PWD/venv/colima" LIMA_HOME="$PWD/venv/lima" \
  colima stop botblitz
```

Use `make evaluate-bot-fast` for iteration after establishing a baseline, and
validate finalists with the production-mode `evaluate-bot` command. Run a completed
historical season before evaluating the incomplete 2026 season. More randomized
runs and stronger opponents are needed to judge competitiveness.
