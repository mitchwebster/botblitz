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
