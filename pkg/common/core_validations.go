package common

func ValidateLandscape(landscape *FantasyLandscape) bool {
	if landscape.MatchNumber < 1 {
		return false
	}

	return true
}
