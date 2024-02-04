package common

import (
	"testing"
)

func TestMultiply(t *testing.T) {
	if got := ReturnFive(); got != 5 {
		t.Errorf("ReturnFive() = %v (expectedValue: %d)", got, 5)
	}
}
