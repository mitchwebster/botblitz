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
	WeeklyFantasy
)

func (s GameMode) String() string {
	return [...]string{"Draft", "WeeklyFantasy"}[s]
}

func GameModeFromString(s string) (GameMode, error) {
	switch strings.ToLower(s) {
	case "draft":
		return Draft, nil
	case "weeklyfantasy":
		return WeeklyFantasy, nil
	default:
		return -1, fmt.Errorf("invalid status: %s", s)
	}
}
