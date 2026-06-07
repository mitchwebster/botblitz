package engine

import (
	"fmt"
	"math/rand"

	gamestate "github.com/mitchwebster/botblitz/pkg/gamestate"
)

// OrderBotsWithPinnedSlot returns the draft order with candidateID placed at the given
// 1-based slot, while every other bot fills the remaining slots in a seeded-shuffled
// order. The draft consumes the slice in index order (snake draft), so slot 1 == index 0.
//
// It is deterministic and reproducible: the same (bots, candidateID, slot, seed) always
// yields the same order. This is the pinned counterpart to GameStateHandler's
// GetBotsInRandomOrder (which uses SQLite RANDOM() and is intentionally non-reproducible).
//
// Errors loudly rather than clamping when slot is out of range [1, len(bots)] or when
// candidateID is not present in bots.
func OrderBotsWithPinnedSlot(bots []gamestate.Bot, candidateID string, slot int, seed int64) ([]gamestate.Bot, error) {
	n := len(bots)
	if n == 0 {
		return nil, fmt.Errorf("cannot order an empty bot list")
	}
	if slot < 1 || slot > n {
		return nil, fmt.Errorf("draft slot %d out of range [1, %d]", slot, n)
	}

	// Separate the candidate from the rest, preserving input order for the others so the
	// seeded shuffle is the only source of variability.
	others := make([]gamestate.Bot, 0, n-1)
	var candidate *gamestate.Bot
	for i := range bots {
		if bots[i].ID == candidateID {
			c := bots[i]
			candidate = &c
			continue
		}
		others = append(others, bots[i])
	}
	if candidate == nil {
		return nil, fmt.Errorf("candidate bot %q not found in bot list", candidateID)
	}

	// Seeded shuffle of the non-candidate bots so the surrounding field is reproducible.
	rng := rand.New(rand.NewSource(seed))
	rng.Shuffle(len(others), func(i, j int) { others[i], others[j] = others[j], others[i] })

	ordered := make([]gamestate.Bot, 0, n)
	ordered = append(ordered, others[:slot-1]...)
	ordered = append(ordered, *candidate)
	ordered = append(ordered, others[slot-1:]...)
	return ordered, nil
}
