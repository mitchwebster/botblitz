package engine

import (
	"fmt"
	"strings"
)

// Define the enum type
type Position int

// Define constants for the enum values
const (
	QB Position = iota
	RB
	WR
	K
	DST
	TE
	SUPERFLEX // QB/RB/WR/TE
	FLEX      // RB/WR/TE
	BENCH
)

func (s Position) String() string {
	return [...]string{"QB", "RB", "WR", "K", "DST", "TE", "SUPERFLEX", "FLEX", "BENCH"}[s]
}

func PositionFromStringArr(s []string) ([]Position, error) {
	endArr := make([]Position, len(s))
	for i, val := range s {
		position, err := PositionFromString(val)
		if err != nil {
			return nil, err
		}

		endArr[i] = position
	}

	return endArr, nil
}

func PositionFromString(s string) (Position, error) {
	switch strings.ToLower(s) {
	case "qb":
		return QB, nil
	case "rb":
		return RB, nil
	case "wr":
		return WR, nil
	case "k":
		return K, nil
	case "dst":
		return DST, nil
	case "te":
		return TE, nil
	case "superflex":
		return SUPERFLEX, nil
	case "flex":
		return FLEX, nil
	case "bench":
		return BENCH, nil
	default:
		return -1, fmt.Errorf("invalid status: %s", s)
	}
}

func GetAllowedPlayerPositionsForSlot(position Position) ([]Position, error) {
	if position == SUPERFLEX {
		return []Position{QB, RB, WR, TE}, nil
	} else if position == FLEX {
		return []Position{RB, WR, TE}, nil
	} else if position == BENCH {
		return []Position{QB, RB, WR, K, DST, TE}, nil
	} else {
		return []Position{position}, nil
	}
}

func FindIntersection(a, b []Position) []Position {
	set := make(map[Position]struct{})
	for _, pos := range a {
		set[pos] = struct{}{}
	}

	resultSet := make(map[Position]struct{})
	for _, pos := range b {
		if _, ok := set[pos]; ok {
			resultSet[pos] = struct{}{}
		}
	}

	var result []Position
	for pos := range resultSet {
		result = append(result, pos)
	}

	return result
}
