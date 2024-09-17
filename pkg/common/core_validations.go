package common

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
