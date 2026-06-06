# Bot data schema

Your bot programs against **`blitz_env` only**. You get one `DatabaseManager`
bound to the season's DB and read these tables with SQL (or the typed accessors).
You never open files or know how many DBs exist.

## Getting a handle

    from blitz_env.models import DatabaseManager
    db = DatabaseManager()
    import pandas as pd
    pd.read_sql("SELECT * FROM players WHERE availability = 'AVAILABLE'", db.engine)

## Typed accessors (FantasyPros schema, keyed by Player.id)

- `db.get_seasonal_data(player, seasons=None)`  -> rows from `season_stats`
- `db.get_weekly_data(player, seasons=None)`    -> rows from `weekly_stats`
- `db.get_preseason_projections(player, season)`-> rows from `preseason_projections`
- `db.get_weekly_projections(player, season, week)` -> rows from `weekly_projections`

## Tables

| Table | Meaning |
|-------|---------|
| `players` | Draftable pool + draft status (`availability`, `current_bot_id`, `pick_chosen`, `rank`, `allowed_positions`). |
| `season_stats` | Per-season actuals, all historical years. `FPTS`, `RUSHING_YDS`, ... keyed by `fantasypros_id`. |
| `weekly_stats` | Per-week actuals for the current season (rolls in week over week). |
| `preseason_projections` | Preseason projections. |
| `weekly_projections` | Per-week projections. |
| `weekly_injuries` | Per-week injury report. |
| `bots`, `league_settings`, `game_statuses` | League state (created by the engine during the draft). |
| `matchups`, `transactions`, `weekly_lineups` | Season league state (created by the engine during the season). |

Note: league-state tables exist once a draft/season has been run by the engine
or harness; the bootstrapped `season.db` ships with only the reference tables and
`players`.
