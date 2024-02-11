package common

import (
	"github.com/mitchwebster/botblitz/pkg/common/pb"
)

func ValidateLandscape(landscape *pb.FantasyLandscape) bool {
	if landscape.MatchNumber < 1 {
		return false
	}

	return true
}
