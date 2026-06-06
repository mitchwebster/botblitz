# Bot migration: off the removed `StatsDB` / `ProjectionsDB`

The 2025→2026 data consolidation **removed the `StatsDB` and `ProjectionsDB`
classes** (their methods were folded into `DatabaseManager`). Bots that imported
those classes will raise `ImportError` at runtime. Per the repo guardrail we do
**not** edit user bot files automatically — this doc flags the affected bots and
shows the port. The owner migrates each bot when ready.

## Affected bots (import the removed classes)

| Bot | Notes |
|-----|-------|
| `bots/nfl2025/ryan_bot.py` | imports `StatsDB` |
| `bots/nfl2025/justin_bot.py` | imports `StatsDB` |
| `bots/archive/2024/ryan-bot.py` | archived; imports `StatsDB` |
| `bots/archive/2024/mitch-bot.py` | archived; imports `StatsDB` |

Bots that use `pd.read_sql(...)` against `DatabaseManager` (most of `bots/nfl2025/`)
are **not** affected — that path is unchanged.

## How to port

Replace direct `StatsDB` / `ProjectionsDB` usage with the `DatabaseManager`
accessors (all return the single FantasyPros schema keyed by `Player.id`):

| Old | New |
|-----|-----|
| `StatsDB(...).get_seasonal_data(player)` | `db.get_seasonal_data(player, seasons=None)` |
| `StatsDB(...).get_weekly_data(player)` | `db.get_weekly_data(player, seasons=None)` |
| `ProjectionsDB(...).get_preseason_projections(player, season)` | `db.get_preseason_projections(player, season)` |
| `ProjectionsDB(...).get_weekly_projections(player, season, week)` | `db.get_weekly_projections(player, season, week)` |

Before:

    from blitz_env import StatsDB
    stats = StatsDB(year=2025)
    df = stats.get_weekly_data(player)

After:

    from blitz_env.models import DatabaseManager
    db = DatabaseManager()
    df = db.get_weekly_data(player)

The returned columns use the FantasyPros schema (`FPTS`, `RUSHING_YDS`, …) with
`season`/`week` normalized to numeric. See `docs/bot-data-schema.md` for the full
table/column contract. Bots may still drop to raw SQL via `db.engine` for complex
joins.

## Note on column names

The removed classes historically returned some `nfl_data_py`-style columns (e.g.
`fantasy_points_ppr`). The `DatabaseManager` accessors return the FantasyPros
schema instead. A ported bot that read `fantasy_points_ppr` should read `FPTS`.
