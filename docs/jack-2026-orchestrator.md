# Orchestrator: implement jack_bot 2026 rewrite (issues #262–#268, serial)

Paste this into a fresh Claude Code chat opened in this repo to drive the implementation of
PRD #261 (closed) end-to-end. It breaks into a **strict serial chain** of 7 GitHub issues, each
handled by its own dedicated sub-agent, one at a time. Do NOT run any two issues in parallel.

## Repo facts you can rely on
- Shared repo: `mitchwebster/botblitz`. The only git remote is `upstream` (there is no fork).
- `gh` is authenticated and its default repo is `mitchwebster/botblitz`. Git push auth is
  already wired through `gh` (`gh auth setup-git`).
- Integration branch: **`jack/2026`** (already created & pushed). ALL work branches from and
  merges back into `jack/2026` — never `main`.
- Read `CLAUDE.md` before touching code; it overrides defaults. Key guardrails:
  - `jack_bot.py` must stay a **single self-contained file** (only `bot.py` ships to the container).
  - Never edit other user bots (`bots/nfl2025/*` besides `jack_bot.py`, `bots/archive/**`),
    `blitz_env` public SDK, the wheel, proto, or the data backend.
  - "Build ≠ proof." Real proof is running tests / the engine, per CLAUDE.md §8.

## The chain (do them in this exact order)
262 → 263 → 264 → 265 → 266 → 267 → 268

- #262 Dynamic team identity (kill hardcoded `bot_id = 8`) — AFK
- #263 VORP draft valuation core (baselines + pool scoring) — AFK
- #264 Draft pick + guardrails + `draft_player()` + integration test — AFK
- #265 Forward-looking waiver value + FAAB bid sizing — AFK
- #266 Waiver claim selection + weekly wiring — AFK
- #267 Evaluator deterministic draft-slot pinning (Go) — AFK
- #268 QB-run fixture + full-season validation (Scenarios A & B) — **HITL, stop here**

Label invariant: exactly one issue carries `ready-for-agent` at a time. At kickoff that is
**#262**. The handoff below preserves the invariant.

## Per-issue procedure
For each issue N in the chain order, spawn ONE fresh sub-agent dedicated to that issue and
have it do the following. Wait for it to fully finish before starting the next.

1. `gh issue view N` — read "What to build" and every acceptance-criteria checkbox.
2. Sync and branch:
   `git checkout jack/2026 && git pull upstream jack/2026 && git checkout -b feat/issue-N`
3. Implement the slice so that **every** acceptance checkbox is satisfied. Honor all
   CLAUDE.md guardrails above.
4. Verify (proportional to the slice; build is not proof):
   - Python slices (#262–#266): run `pytest` for the new/affected tests; #264 must pass its
     integration test against the `season_db_2025` fixture.
   - #267 (Go): `cd pkg/engine && go test ./...`.
   - If a slice changes the runtime path, also confirm the container import per CLAUDE.md §8:
     `docker run --rm py_grpc_server sh -c "cd /app/py_grpc_server && python3 -c 'import bot'"`
     (rebuild the image first with `make build-docker` if needed).
5. Commit, push, PR, merge into `jack/2026`:
   - Commit with a clear message ending with:
     `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
   - `git push -u upstream feat/issue-N`
   - `gh pr create --base jack/2026 --head feat/issue-N --title "<short>" --body "Closes #N"`
   - `gh pr merge --merge --delete-branch` (only if checks pass and the diff matches the issue)
6. Close + hand off the label (do this AFTER the merge):
   ```
   gh issue close N --comment "Implemented on jack/2026 via <PR link>."
   gh issue edit N --remove-label ready-for-agent
   gh issue edit <N+1> --add-label ready-for-agent      # skip if N is the last (268)
   ```
7. Report a one-line result, then proceed to the next issue.

## Stop condition
After #267 merges, add `ready-for-agent` to **#268** but DO NOT implement it. #268 is HITL:
it needs Docker + human judgment of the median final standing across Scenarios A and B.
Stop and report that #268 is ready for the human to run/judge.

## If anything blocks you
If an agent can't satisfy an acceptance criterion, or the diff would violate a guardrail
(e.g. needs to touch a forbidden file), STOP that issue, leave its `ready-for-agent` label in
place, do not advance the chain, and report the blocker. Never skip ahead.

Begin with #262.

---

## Notes / dials
- **Autonomy level.** As written, each agent *merges its own PR into `jack/2026`*. To review
  each PR manually instead, stop after `gh pr create` and defer step 6's close/handoff until
  you've merged — the label handoff then becomes manual.
- **`main` is untouched.** Everything lands on `jack/2026`; promoting it to `main` is a
  separate human step (a final PR you control).
- **#268 stays HITL.** The orchestrator labels it and halts, so no agent burns Docker time or
  makes a "did it improve?" judgment call.
