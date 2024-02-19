package common

func ValidateLandscape(landscape *FantasyLandscape) bool {
	if landscape.MatchNumber < 1 {
		return false
	}

	return true
}

func ValidateBotConfigs(bots []*Bot) bool {
	uniquenessMap := make(map[string]bool)

	for _, bot := range bots {
		if bot.Id == "" {
			return false
		}

		_, exists := uniquenessMap[bot.Id]
		if exists {
			return false
		}

		uniquenessMap[bot.Id] = true
	}

	return true
}

func ValidateSimulation(simulations []*Simulation) bool {
	uniquenessMap := make(map[string]bool)

	for _, simulation := range simulations {
		if simulation.Id == "" {
			return false
		}

		_, exists := uniquenessMap[simulation.Id]
		if exists {
			return false
		}

		uniquenessMap[simulation.Id] = true
	}

	return true
}
