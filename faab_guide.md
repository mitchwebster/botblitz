# FAAB (Free Agent Acquisition Budget) FAQ

## What is FAAB?
FAAB is a system where players use a budget to bid on available players during the waiver wire period. Each player starts with a set budget of 100 that they can use throughout the season. You will need to code your bot to submit waiver claims.

## How do I submit waiver claims?
Your bot needs to implement the `perform_weekly_fantasy_actions()` function which returns an `AttemptedFantasyActions` object containing a list of `WaiverClaim` objects. Each `WaiverClaim` must have:
- `player_to_add_id`: ID of the player you want to add
- `player_to_drop_id`: ID of the player you want to drop
- `bid_amount`: How much FAAB you want to bid (integer)

## Basic Rules
- All bots will be asked for their Waiver Claims at the same time (likely sometime on Wednesday)
- You can submit at most 10 Waiver Claims, any additional claims will be ignored
- Within a Waiver Claim, the player you add must not already be drafted by another team, and the player you drop must currently be on your team. You must always supply a player to add and a player to drop.
- Bids must be >= 0 and <= your remaining budget. Your budget does not replenish during the season
- Waiver Claims with the highest bid amount for a given player will be completed. After which, that team's budget will be reduced by the bid amount

## Example Claim Submission
```python
def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    claims = [ 
        WaiverClaim(
            player_to_add_id="123",
            player_to_drop_id="456",
            bid_amount=10
        )
    ]

    actions = AttemptedFantasyActions(
        waiver_claims=claims
    )
```
 The above bot submits one `WaiverClaim` for player 123 with a bid of 10. This claim will be valid if:
1. Player 123 is available to be added (not already on another team).
2. Player 456 is currently on this bot's team.
3. The bot has at least 10 FAAB remaining in its budget.

## Getting started
There are some example functions for getting player stats from previous weeks, matchups, remaining budget, etc. available in `bots/nfl2025/standard-bot.py`, feel free to use them for writing your add drop.

If you'd like to test your waiver submission code, you can locally run `make build-docker` and then `make run-weekly-fantasy` assuming you've setup go, docker, protoc, etc.

## More Advanced Rules

### How are ties handled?
Ties are awarded to the worse team, which is decided first based on record (Wins vs. Losses) and then based on total points.

### Waiver Claims are evaluated in order
Each waiver claim you submit will be evaluated in order. This matters when you are submitting multiple claims as early claims may deplete your budget and cause later claims to become invalid. For example, consider the following scenario:

```python
def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    claims = [ 
        WaiverClaim(
            player_to_add_id="123",
            player_to_drop_id="456",
            bid_amount=10
        ),
        WaiverClaim(
            player_to_add_id="789",
            player_to_drop_id="012",
            bid_amount=40
        )
    ]

    actions = AttemptedFantasyActions(
        waiver_claims=claims
    )
```
Imagine that this bot has a remaining budget of 40. If their first claim for player 123 is successful, their second claim will be considered invalid (as they only have 30 remaining). You should be careful about how you order your claims to ensure earlier claims don't prevent later claims from being accepted.

### Multiple claims with the same dropped player
You may submit multiple claims with the same dropped player but be aware that once one of those claims is accepted, all claims referencing that dropped player will now be considered invalid.

### Multiple claims totaling more than the remaining budget
As shown in the example above with multiple claims, you can submit a series of claims that sum to more than your remaining budget.

### The 0 bid is allowed
Bids of 0 are allowed and may be used when you do not believe there will be contention on a given player