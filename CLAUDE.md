# Claude Code Documentation

This file contains important context and reminders for working on the BotBlitz codebase.

## Database Structure & Source of Truth

### **Source of Truth: `data/stats/{year}/stats.db`** ✅

- **Location**: `data/stats/{year}/stats.db` (e.g., `data/stats/2025/stats.db`)
- **Purpose**: Contains all the raw stats & projections data
- **Tables**:
  - `preseason_projections` - Preseason fantasy projections for all players
  - `season_stats` - Full season stats for all players
  - `weekly_stats` - Weekly stats for each player/week
  - `weekly_injuries` - Weekly injury reports from NFL.com (includes injury type, practice status, game status)
- **How it's created**:
  - Initially: `python3 blitz_env/collect_stats.py --db data/stats/{year}/stats.db --end-year {year} --years 10`
  - Weekly updates:
    - `python3 -m blitz_env.collect_weekly_stats --year {year} --week {week} --db data/stats/{year}/stats.db`
    - `python3 -m blitz_env.collect_weekly_injuries --year {year} --week {week} --db data/stats/{year}/stats.db`
- **When it updates**: GitHub action (`.github/workflows/update-scores.yml`) runs after each game to add current week's stats
- **Tracked in git**: ✅ Yes (checked into repo at `data/stats/`)

### **Game State: `data/game_states/{year}/gs-*.db`**

- **Location**: `data/game_states/{year}/gs-{draft|season}.db`
- **Purpose**: Contains the actual fantasy league state (who drafted who, matchups, scores, etc.)
- **Tables**:
  - `bots` - Bot/team information
  - `players` - Player information and roster assignments
  - `matchups` - Weekly matchup pairings
  - `league_settings` - League configuration
  - `game_statuses` - Current game state
  - **Copied from stats.db**: `season_stats`, `preseason_projections`, `weekly_stats`
- **How it works**:
  - When games are created/updated, Go code **ATTACHES** `data/stats/{year}/stats.db` as a separate database
  - It **COPIES** the stats tables into the game state DB (see `pkg/gamestate/handler.go:580-597`, `602-621`)
  - This way each game state has its own snapshot of stats at the time of creation
  - Stats can be refreshed using `RefreshWeeklyStats()` which re-copies from the source
- **Tracked in git**: ✅ Yes (checked into repo at `data/game_states/`)

### **Root Level Files (Leftover/Development)** ⚠️

- `./stats.db` - Old/development file, NOT used by production code
- `./gamestate.db` - Old/development file (in `.gitignore`), NOT used by production code
- **These should be ignored** - they're leftovers from development/testing

### **Key Code References**

- **Stats DB path construction**: `pkg/gamestate/handler.go:553-561` (`getStatsDatabaseFilePath`)
- **Game state DB path construction**: `pkg/gamestate/handler.go:740-754` (`getSaveFileName`)
- **Stats table population**: `pkg/gamestate/handler.go:602-650` (`populateStatsTables`)
- **Weekly stats refresh**: `pkg/gamestate/handler.go:563-600` (`RefreshWeeklyStats`)

### **Constants**

```go
const saveFolderRelativePath = "data/game_states"  // Game state DBs
const statsFolderRelativePath = "data/stats"       // Stats DBs (source of truth)
const statsDatabaseFileName = "stats.db"
```

## Historical Data Backfill Commands

### Collect All Historical Stats (10 years)
```bash
python3 blitz_env/collect_stats.py \
  --db data/stats/2025/stats.db \
  --end-year 2025 \
  --years 10 \
  --include-weekly \
  --include-injuries \
  --weeks 1:18
```

**Warning**: This makes ~180 HTTP requests to NFL.com for injury data (10 years × 18 weeks). Expect 5-10 minutes runtime and possible rate limiting.

### Collect Single Week (Testing)
```bash
python3 -m blitz_env.collect_weekly_injuries --year 2025 --week 6 --db data/stats/2025/stats.db
```

## Injury Data Details

- **Source**: Scraped from NFL.com official injury reports
- **Player matching**: Uses fuzzy matching (rapidfuzz) to match player names to FantasyPros IDs
- **Merge key**: `(year, week, player_name, position)` - matches on name and position, not ID initially
- **Fields**:
  - `player_name`, `team`, `position`
  - `injury` - injury type (e.g., "Ankle", "Hamstring")
  - `practice_status` - practice participation
  - `game_status` - game day status (e.g., "Questionable", "Out")
  - `fantasypros_id`, `gsis_id`, `sleeper_id` - matched player IDs

## GitHub Actions

### `.github/workflows/update-scores.yml`
Runs after games to update weekly data. Collections are prioritized:
- **Weekly stats** (mission critical) - Must succeed or action fails
- **Weekly projections** (important but non-critical) - Continues on failure
- **Weekly injuries** (important but non-critical) - Continues on failure

If projections or injuries fail to scrape (e.g., page format changes, data unavailable), the action logs a warning but continues successfully. This prevents breaking the core score update workflow.

### `.github/workflows/download-weekly-data.yml`
Downloads projections/stats to S3
