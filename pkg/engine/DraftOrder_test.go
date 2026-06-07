package engine

import (
	"testing"

	gamestate "github.com/mitchwebster/botblitz/pkg/gamestate"
)

func makeBots(n int) []gamestate.Bot {
	bots := make([]gamestate.Bot, n)
	for i := 0; i < n; i++ {
		bots[i] = gamestate.Bot{ID: itoa(i)}
	}
	return bots
}

func itoa(i int) string {
	if i == 0 {
		return "0"
	}
	digits := ""
	for i > 0 {
		digits = string(rune('0'+i%10)) + digits
		i /= 10
	}
	return digits
}

func idsOf(bots []gamestate.Bot) []string {
	out := make([]string, len(bots))
	for i, b := range bots {
		out[i] = b.ID
	}
	return out
}

// Candidate lands at the requested 1-based slot, and the full order is identical across
// repeated calls with the same seed (reproducible-when-set).
func TestOrderBotsWithPinnedSlot_PinnedAndReproducible(t *testing.T) {
	bots := makeBots(14)
	const candidate = "0"

	for _, slot := range []int{1, 7, 14} {
		first, err := OrderBotsWithPinnedSlot(bots, candidate, slot, 42)
		if err != nil {
			t.Fatalf("slot %d: unexpected error: %v", slot, err)
		}
		if len(first) != len(bots) {
			t.Fatalf("slot %d: expected %d bots, got %d", slot, len(bots), len(first))
		}
		if got := first[slot-1].ID; got != candidate {
			t.Fatalf("slot %d: candidate at index %d is %q, want %q", slot, slot-1, got, candidate)
		}

		// Same inputs => identical order on a repeated call.
		second, err := OrderBotsWithPinnedSlot(bots, candidate, slot, 42)
		if err != nil {
			t.Fatalf("slot %d: unexpected error on repeat: %v", slot, err)
		}
		a, b := idsOf(first), idsOf(second)
		for i := range a {
			if a[i] != b[i] {
				t.Fatalf("slot %d: order not reproducible at index %d: %v vs %v", slot, i, a, b)
			}
		}

		// Every bot appears exactly once.
		seen := map[string]bool{}
		for _, id := range a {
			if seen[id] {
				t.Fatalf("slot %d: bot %q appears more than once: %v", slot, id, a)
			}
			seen[id] = true
		}
		if len(seen) != len(bots) {
			t.Fatalf("slot %d: expected %d unique bots, got %d", slot, len(bots), len(seen))
		}
	}
}

// Different seeds generally produce different surrounding-field orders while keeping the
// candidate pinned, confirming the seed actually drives the field.
func TestOrderBotsWithPinnedSlot_SeedVaries(t *testing.T) {
	bots := makeBots(14)
	const candidate = "0"

	a, err := OrderBotsWithPinnedSlot(bots, candidate, 7, 1)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	b, err := OrderBotsWithPinnedSlot(bots, candidate, 7, 2)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if a[6].ID != candidate || b[6].ID != candidate {
		t.Fatalf("candidate not pinned at slot 7 for both seeds")
	}
	same := true
	ai, bi := idsOf(a), idsOf(b)
	for i := range ai {
		if ai[i] != bi[i] {
			same = false
			break
		}
	}
	if same {
		t.Fatalf("expected different field orders for different seeds, got identical: %v", ai)
	}
}

func TestOrderBotsWithPinnedSlot_Errors(t *testing.T) {
	bots := makeBots(14)

	if _, err := OrderBotsWithPinnedSlot(bots, "0", 0, 0); err == nil {
		t.Fatal("expected error for slot 0 (below range)")
	}
	if _, err := OrderBotsWithPinnedSlot(bots, "0", 15, 0); err == nil {
		t.Fatal("expected error for slot 15 (above range)")
	}
	if _, err := OrderBotsWithPinnedSlot(bots, "missing", 7, 0); err == nil {
		t.Fatal("expected error for candidate not present in bot list")
	}
	if _, err := OrderBotsWithPinnedSlot(nil, "0", 1, 0); err == nil {
		t.Fatal("expected error for empty bot list")
	}
}
