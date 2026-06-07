package engine

import common "github.com/mitchwebster/botblitz/pkg/common"

// BuildDefaultLeagueSettings returns the league configuration the project uses: a
// SUPERFLEX roster (QB, RB×2, WR×2, SUPERFLEX, FLEX, K, DST, BENCH×3), snake draft, full PPR.
func BuildDefaultLeagueSettings(year, numTeams uint32) *common.LeagueSettings {
	slots := map[string]uint32{
		QB.String():        1,
		RB.String():        2,
		WR.String():        2,
		SUPERFLEX.String(): 1,
		FLEX.String():      1,
		K.String():         1,
		DST.String():       1,
		BENCH.String():     3,
	}
	var totalRounds uint32
	for _, v := range slots {
		totalRounds += v
	}
	return &common.LeagueSettings{
		NumTeams:           numTeams,
		IsSnakeDraft:       true,
		TotalRounds:        totalRounds,
		PointsPerReception: 1.0,
		Year:               year,
		SlotsPerTeam:       slots,
	}
}
