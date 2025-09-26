package engine

import (
	"fmt"
	"strings"
)

// Define the enum type
type GameMode int

// Define constants for the enum values
const (
	Draft GameMode = iota
	PerformWeeklyFantasyActions
	UpdateWeeklyScores
	FinishPreviousWeek
)

func (s GameMode) String() string {
	return [...]string{"Draft", "PerformWeeklyFantasyActions", "UpdateWeeklyScores", "FinishPreviousWeek"}[s]
}

func GameModeFromString(s string) (GameMode, error) {
	switch strings.ToLower(s) {
	case "draft":
		return Draft, nil
	case "performweeklyfantasyactions":
		return PerformWeeklyFantasyActions, nil
	case "updateweeklyscores":
		return UpdateWeeklyScores, nil
	case "finishpreviousweek":
		return FinishPreviousWeek, nil
	default:
		return -1, fmt.Errorf("invalid status: %s", s)
	}
}
