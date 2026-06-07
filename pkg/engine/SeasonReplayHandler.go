package engine

import (
	"context"
	"fmt"
	"sort"
)

// FinalSeasonWeek is the last scored week. It must stay consistent with the playoff
// schedule in PlayoffHandler.go: a 14-week regular season, then the bracket runs from
// StartingPlayoffWeek (15) for log2(NumPlayoffTeams+TeamsReceivingByes) rounds
// (6+2 teams => 3 rounds => weeks 15, 16, 17).
const FinalSeasonWeek = 17

// Standing is one team's end-of-season result.
type Standing struct {
	BotID        string
	Name         string
	Rank         uint32
	Wins         uint32
	Losses       uint32
	Points       float32
	MadePlayoffs bool
	IsChampion   bool
}

// ReplaySeason advances the loaded season to completion, running each week's waiver
// actions (bot containers) and then scoring + finishing that week (engine code).
func (e *BotEngine) ReplaySeason(ctx context.Context) error {
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		week, err := e.gameStateHandler.GetCurrentFantasyWeek()
		if err != nil {
			return err
		}
		if week > FinalSeasonWeek {
			return nil
		}
		fmt.Printf("\n===== Replaying fantasy week %d =====\n", week)
		// Waivers require running bot containers; skip when no bot source is
		// configured (e.g. unit tests that only exercise the scoring replay).
		if len(e.sourceCodeCache) > 0 {
			if err := e.performWeeklyFantasyActions(ctx); err != nil {
				return err
			}
		}
		if err := e.updateWeeklyScores(ctx, true); err != nil {
			return err
		}
	}
}

// seasonChampion returns the bot id that won the final playoff matchup, or "" if
// the playoffs have not produced a decided final.
func (e *BotEngine) seasonChampion() (string, error) {
	matchups, err := e.gameStateHandler.GetMatchupsForWeek(FinalSeasonWeek)
	if err != nil {
		return "", err
	}
	for _, m := range matchups {
		if m.IsPlayoffMatchup && m.WinningBotID != nil {
			return *m.WinningBotID, nil
		}
	}
	return "", nil
}

// FinalStandings returns every team's regular-season placement (record + points,
// via the engine's own leaderboard) plus playoff outcome.
func (e *BotEngine) FinalStandings() ([]Standing, error) {
	bots, err := e.gameStateHandler.GetBots()
	if err != nil {
		return nil, err
	}
	regularSeasonMatchups, err := e.gameStateHandler.GetPastMatchups(StartingPlayoffWeek)
	if err != nil {
		return nil, err
	}
	// getLeaderboard is built from the same GetBots() slice, so every bot has an entry;
	// a missing bot would zero-value to Rank 0 (sorts first, MadePlayoffs true).
	leaderboard := getLeaderboard(bots, regularSeasonMatchups)

	championID, err := e.seasonChampion()
	if err != nil {
		return nil, err
	}

	standings := make([]Standing, 0, len(bots))
	for _, b := range bots {
		r := leaderboard[b.ID]
		standings = append(standings, Standing{
			BotID:        b.ID,
			Name:         b.Name,
			Rank:         r.Ranking,
			Wins:         r.MatchupsWon,
			Losses:       r.MatchupsLost,
			Points:       r.TotalPoints,
			MadePlayoffs: r.Ranking <= NumPlayoffTeams,
			IsChampion:   b.ID == championID,
		})
	}
	sort.Slice(standings, func(i, j int) bool { return standings[i].Rank < standings[j].Rank })
	return standings, nil
}
