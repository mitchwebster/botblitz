# botblitz

A bot-vs-bot fantasy football engine. You write a Python bot; it drafts a team
and makes weekly roster decisions against everyone else's bots.

## Getting Started

1. Clone this repo
2. Run `make launch-simulator` (builds the SDK, installs it, opens the notebook +
   a Datasette browser on a dev snapshot)
3. Write your first bot — see **Anatomy of a bot** below

## Anatomy of a bot

A bot is a single Python file that defines up to two functions. You program
against the `blitz_env` SDK and read the season database with SQL (or the typed
accessors). You never open files or worry about how many databases exist.

```python
from blitz_env.models import DatabaseManager
import pandas as pd

def draft_player() -> str:
    """Called on your clock during the draft. Return the id of the player to draft."""
    db = DatabaseManager()
    try:
        avail = pd.read_sql(
            "SELECT id, full_name, rank FROM players "
            "WHERE availability = 'AVAILABLE' ORDER BY rank",
            db.engine,
        )
        return avail.iloc[0]["id"] if not avail.empty else ""
    finally:
        db.close()
```

The second function, `perform_weekly_fantasy_actions() -> AttemptedFantasyActions`,
runs each week during the season to set your lineup and submit FAAB waiver claims.
See `bots/nfl2025/standard-bot.py` for a worked example of both.

**Where the data lives.** `DatabaseManager()` binds to the season's SQLite DB.
The tables you'll use most:

| Table | What's in it |
|-------|--------------|
| `players` | The draftable pool + draft status (`availability`, `rank`, `allowed_positions`, `current_bot_id`, `pick_chosen`). |
| `preseason_projections` | Preseason projected stats (incl. `FPTS`), keyed by `fantasypros_id` (== `players.id`). |
| `season_stats` | Per-season actuals, all historical years. |
| `weekly_stats` / `weekly_projections` / `weekly_injuries` | Per-week actuals / projections / injury reports. |
| `league_settings`, `bots`, `game_statuses`, `matchups` | League state. |

Full schema and the typed accessors (`db.get_weekly_data`, `db.get_preseason_projections`, …)
are in [`docs/bot-data-schema.md`](docs/bot-data-schema.md).

**Tip — `allowed_positions`.** It's a JSON column, so the ORM
(`Player.allowed_positions`) gives you a list while raw `pd.read_sql` gives you
the JSON *string* (`'["WR"]'`). Use `blitz_env.parse_positions(...)`, which
accepts either shape and always returns a list.

## Helpers, joins, and historical data

You have three ways to read the database, from highest to lowest level.

**1. Typed accessors on `DatabaseManager`.** These take a player object (anything
with an `.id`, e.g. from `db.get_player_by_id(...)` or `db.get_all_players()`) and
return a pandas DataFrame, normalized so `season`/`week` are numeric:

```python
db = DatabaseManager()
chase = db.get_player_by_id("19788")               # Ja'Marr Chase

db.get_seasonal_data(chase)                         # every season in season_stats
db.get_seasonal_data(chase, seasons=[2022, 2023, 2024])
db.get_weekly_data(chase, seasons=[2025])           # week-by-week, current season
db.get_preseason_projections(chase, season=2025)
db.get_weekly_projections(chase, season=2025, week=5)
```

Other convenience methods: `get_all_players()`, `get_player_by_id(id)`,
`get_all_bots()`, `get_league_settings()`, `get_game_status()`. Top-level SDK
helpers: `parse_positions(...)`, `is_drafted(player)`, `load_players(...)`.

**2. Raw SQL via `db.engine` + pandas.** For anything the accessors don't cover —
joins, filters, aggregations — pass `db.engine` to `pd.read_sql`. The draft pool
(`players`) joins to every stats/projection table on
`players.id = <table>.fantasypros_id`:

```python
# Best available WRs by 2025 projection
avail_wr = pd.read_sql("""
    SELECT p.id, p.full_name, p.rank, pr.FPTS AS proj
    FROM players p
    JOIN preseason_projections pr
      ON pr.fantasypros_id = p.id AND pr.year = 2025
    WHERE p.availability = 'AVAILABLE' AND pr.position = 'wr'
    ORDER BY pr.FPTS DESC
""", db.engine)
```

**3. Historical operations.** `season_stats` holds **every year (2016–present)**, so
it's where you do multi-season analysis. (`weekly_stats`, `weekly_projections`, and
`weekly_injuries` cover the **current season only**.) For example, find the most
consistent scorers over the last three seasons:

```python
hist = pd.read_sql("""
    SELECT fantasypros_id, AVG(FPTS) AS avg_fpts, COUNT(*) AS seasons
    FROM season_stats
    WHERE year IN (2022, 2023, 2024)
    GROUP BY fantasypros_id
    HAVING seasons = 3
    ORDER BY avg_fpts DESC
""", db.engine)
```

Always `db.close()` when you're done (the example bots use `try/finally`).

## How drafts are scored

The simulator scores a draft by each team's **best-possible-season-score** — the
ceiling of the roster you drafted (see `harness/score_game.py`):

- For each week (1–17), it builds your **optimal** lineup from your roster: it
  fills the most restrictive starting slots first and assigns the highest-scoring
  eligible player to each slot (each player used once). `FLEX` accepts RB/WR/TE;
  `SUPERFLEX` accepts QB/RB/WR/TE.
- Per-player weekly points come from `weekly_stats` (PPR).
- **An unfilled or unfillable slot scores 0** for that week.
- Summing every week's optimal lineup gives the season score; teams are ranked by
  the total (with weekly 1st/2nd/3rd-place tallies as a tiebreaker view).

Because the score is the *best possible* lineup with empty slots counting as 0,
there's no hidden penalty for an unbalanced roster — only the opportunity cost of
the points you leave on the bench. That makes punting a position a real, legal
strategy: if you think being "the house" on stacked QBs/WRs beats taking zeros
elsewhere, the scoring will let you make that bet. (One human owner roughly did
exactly that last season.)

## Engine Commands

- `make clean` — removes generated proto classes
- `make gen` — generates proto classes from proto
- `make test` — runs tests
- `make run-draft` — run a draft with the real engine
