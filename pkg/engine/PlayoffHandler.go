package engine

import (
	"fmt"

	"github.com/mitchwebster/botblitz/pkg/gamestate"
)

const StartingPlayoffWeek = 15
const TeamsReceivingByes = 2 // Must be even number
const NumPlayoffTeams = 6    // Must be even number
const UndeterminedBots = "TBD"
const playoffMatchupStartingIndex = 1000

func (e *BotEngine) handlePlayoffsIfNeeded(nextWeek int) error {
	if nextWeek < StartingPlayoffWeek {
		return nil
	}

	if nextWeek == StartingPlayoffWeek {
		err := e.generatePlayoffMatchups()
		if err != nil {
			return err
		}

		return nil
	}

	err := e.updatePlayoffMatchups(nextWeek)
	if err != nil {
		return err
	}

	return nil
}

func (e *BotEngine) getEndOfSeasonRankings() (map[uint32]string, error) {
	bots, err := e.gameStateHandler.GetBots()
	if err != nil {
		return nil, err
	}

	pastMatchups, err := e.gameStateHandler.GetPastMatchups(StartingPlayoffWeek)
	if err != nil {
		return nil, err
	}

	endOfSeasonMap := make(map[uint32]string)
	rankingsMap := getLeaderboard(bots, pastMatchups)
	for i, botRanking := range rankingsMap {
		if botRanking.Ranking <= NumPlayoffTeams {
			endOfSeasonMap[botRanking.Ranking] = i
		}
	}

	return endOfSeasonMap, nil
}

func (e *BotEngine) generatePlayoffMatchups() error {
	fmt.Println("Generating next playoff matchups...")

	endOfSeasonRankings, err := e.getEndOfSeasonRankings()
	if err != nil {
		return err
	}

	fmt.Println("Teams that made the playoffs...")
	for rank, teamId := range endOfSeasonRankings {
		fmt.Printf("\tRank %d: Team %s\n", rank, teamId)
	}

	playoffMatchups, err := generatePlayoffMatchupsInternal(StartingPlayoffWeek, NumPlayoffTeams, TeamsReceivingByes, endOfSeasonRankings)
	if err != nil {
		return err
	}

	return e.gameStateHandler.AddMatchups(playoffMatchups)
}

func generatePlayoffMatchupsInternal(startingPlayoffWeek int, numPlayoffTeams uint32, teamsReceivingByes uint32, endOfSeasonRankings map[uint32]string) ([]gamestate.Matchup, error) {
	var playoffMatchups []gamestate.Matchup
	effectiveTeamsThisRound := uint32(numPlayoffTeams + teamsReceivingByes)
	for i := uint32(0); i < effectiveTeamsThisRound/2; i++ {
		homeTeamId, _ := endOfSeasonRankings[i+1]
		if i+1 <= teamsReceivingByes {
			playoffMatchups = append(playoffMatchups, gamestate.Matchup{
				ID:                     uint(playoffMatchupStartingIndex + len(playoffMatchups)),
				Week:                   startingPlayoffWeek,
				HomeBotID:              homeTeamId,
				VisitorBotID:           gamestate.ByeId,
				HomeScore:              0,
				VisitorScore:           0,
				WinningBotID:           nil,
				IsPlayoffMatchup:       true,
				HomePlayInMatchupID:    nil,
				VisitorPlayInMatchupID: nil,
			})
		} else {
			visitingTeam, _ := endOfSeasonRankings[effectiveTeamsThisRound-i]
			playoffMatchups = append(playoffMatchups, gamestate.Matchup{
				ID:                     uint(playoffMatchupStartingIndex + len(playoffMatchups)),
				Week:                   startingPlayoffWeek,
				HomeBotID:              homeTeamId,
				VisitorBotID:           visitingTeam,
				HomeScore:              0,
				VisitorScore:           0,
				WinningBotID:           nil,
				IsPlayoffMatchup:       true,
				HomePlayInMatchupID:    nil,
				VisitorPlayInMatchupID: nil,
			})
		}
	}

	startingIndex := 0
	endingIndex := len(playoffMatchups) - 1
	curWeek := startingPlayoffWeek

	for startingIndex != endingIndex {
		curWeek++
		numMatchesThisRound := (endingIndex - startingIndex + 1) / 2
		if numMatchesThisRound == 0 {
			continue
		}

		for i := 0; i < numMatchesThisRound; i++ {
			homePlayInMatchupID := playoffMatchups[startingIndex+i].ID
			visitorPlayInMatchupID := playoffMatchups[endingIndex-i].ID

			playoffMatchups = append(playoffMatchups, gamestate.Matchup{
				ID:                     uint(playoffMatchupStartingIndex + len(playoffMatchups)),
				Week:                   curWeek,
				HomeBotID:              UndeterminedBots,
				VisitorBotID:           UndeterminedBots,
				HomeScore:              0,
				VisitorScore:           0,
				WinningBotID:           nil,
				IsPlayoffMatchup:       true,
				HomePlayInMatchupID:    &homePlayInMatchupID,
				VisitorPlayInMatchupID: &visitorPlayInMatchupID,
			})
		}

		startingIndex = endingIndex + 1
		endingIndex = len(playoffMatchups) - 1
	}

	return playoffMatchups, nil
}

func (e *BotEngine) updatePlayoffMatchups(nextWeek int) error {
	playoffMatchups, err := e.gameStateHandler.GetMatchupsForWeek(nextWeek)
	if err != nil {
		return err
	}

	for _, matchup := range playoffMatchups {
		prevHomeMatchup, err := e.gameStateHandler.GetMatchupById(matchup.HomePlayInMatchupID)
		if err != nil {
			return err
		}

		prevVisitorMatchup, err := e.gameStateHandler.GetMatchupById(matchup.VisitorPlayInMatchupID)
		if err != nil {
			return err
		}

		err = e.gameStateHandler.UpdateMatchupBots(matchup.ID, *prevHomeMatchup.WinningBotID, *prevVisitorMatchup.WinningBotID)
		if err != nil {
			return err
		}
	}

	return nil
}
