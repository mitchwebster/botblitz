# Backfill Transactions and Weekly Lineups

This directory contains scripts and data for backfilling historical transaction and lineup data.

## Overview

The backfill process reconstructs historical weekly rosters by working backwards from the current roster state and reversing all future transactions. This allows us to populate the `weekly_lineups` table with accurate historical data for each week.

## Files

- `backfill_transactions.sql` - SQL script containing all historical waiver transactions from weeks 3-14
- `../../pkg/cmd/backfill_lineups.go` - Go script that reconstructs weekly rosters and populates weekly_lineups table

## Steps to Run Backfill

### 1. Populate Transactions Table

First, populate the `transactions` table with historical waiver data:

```bash
sqlite3 data/game_states/2025/gs-season.db < backfill_transactions.sql
```

Verify transactions were loaded:

```bash
sqlite3 data/game_states/2025/gs-season.db "SELECT COUNT(*) FROM transactions"
# Should return: 55
```

### 2. Run Weekly Lineups Backfill

Execute the backfill script to reconstruct historical rosters and populate weekly_lineups:

```bash
go run pkg/cmd/backfill_lineups.go
```

This script will:
- Load all transactions from the database
- Work backwards from week 14 to week 1
- For each week, reverse all future transactions to reconstruct the roster
- Compute optimal lineups using the greedy algorithm (same as the engine)
- Save lineup data to the `weekly_lineups` table
- Validate computed scores against the `matchups` table

### 3. Verify Results

Check that a specific bot has 12 players for a given week:

```bash
sqlite3 data/game_states/2025/gs-season.db "SELECT COUNT(*) FROM weekly_lineups WHERE bot_id = '10' AND week = 3"
# Should return: 12
```

Verify scores match matchups table:

```bash
sqlite3 data/game_states/2025/gs-season.db "
SELECT SUM(points) FROM weekly_lineups
WHERE week = 3 AND bot_id = '8' AND slot <> 'BENCH'
"
# Compare with:
sqlite3 data/game_states/2025/gs-season.db "
SELECT home_score, visitor_score FROM matchups
WHERE (home_bot_id = '8' OR visitor_bot_id = '8') AND week = 3
"
```

## How the Backfill Works

### Transaction Reversing Logic

To find the roster for week X:
1. Start with the current roster (as of week 14)
2. Reverse all transactions from weeks (X+1) through 14
3. Reversing a transaction means:
   - Remove the "added" player from the roster
   - Add back the "dropped" player to the roster

**Important**: Add/drops that happen during week X happen BEFORE week X games are played, so they're already reflected in week X's roster.

### Lineup Optimization

For each reconstructed roster:
1. Query all players with their week-specific stats
2. Include rostered players without stats with 0.0 points (injured/bye)
3. Sort players by FPTS descending (critical for greedy algorithm)
4. Run the greedy slot-filling algorithm:
   - Slots are sorted by restrictiveness (QB, RB, WR, K, DST, TE, FLEX, SUPERFLEX)
   - Players are assigned to the first available slot they're eligible for
   - Highest-scoring players fill slots first
5. Remaining players go to BENCH

## Notes

- The backfill script validates all computed scores against the matchups table
- All weeks should show ✅ when validation completes
- Players without weekly_stats entries (injured, bye week) are included with 0.0 points
- The greedy algorithm matches the engine's implementation exactly
