package engine

import (
	"fmt"
	"time"
)

// LogElapsed starts a stopwatch and returns a function that, when called, prints how
// long elapsed since LogElapsed was invoked. It is meant for profiling where time goes
// during an evaluation/engine run (draft vs season, per week, waivers vs scoring,
// container builds). Typical uses:
//
//	defer LogElapsed("draft")()           // whole-function timing
//
//	done := LogElapsed("week %d scoring", week)
//	... work ...
//	done()                                // explicit step timing
//
// Durations are rounded to the millisecond and tagged with ⏱ so the timing lines are
// easy to grep out of the engine's otherwise verbose stdout.
func LogElapsed(format string, args ...any) func() {
	start := time.Now()
	label := fmt.Sprintf(format, args...)
	return func() {
		fmt.Printf("⏱  %s took %s\n", label, time.Since(start).Round(time.Millisecond))
	}
}
