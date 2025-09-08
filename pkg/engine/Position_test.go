package engine

import (
	"reflect"
	"testing"
)

func TestPositionFromStringArr(t *testing.T) {
	tests := []struct {
		name      string
		input     []string
		want      []Position
		expectErr bool
	}{
		{
			name:      "valid positions",
			input:     []string{"QB", "RB", "WR"},
			want:      []Position{QB, RB, WR},
			expectErr: false,
		},
		{
			name:      "mixed case positions",
			input:     []string{"qb", "Rb", "wR"},
			want:      []Position{QB, RB, WR},
			expectErr: false,
		},
		{
			name:      "all positions",
			input:     []string{"QB", "RB", "WR", "K", "DST", "TE", "SUPERFLEX", "FLEX", "BENCH"},
			want:      []Position{QB, RB, WR, K, DST, TE, SUPERFLEX, FLEX, BENCH},
			expectErr: false,
		},
		{
			name:      "invalid position",
			input:     []string{"QB", "INVALID", "WR"},
			want:      nil,
			expectErr: true,
		},
		{
			name:      "empty input",
			input:     []string{},
			want:      []Position{},
			expectErr: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := PositionFromStringArr(tt.input)
			if (err != nil) != tt.expectErr {
				t.Errorf("PositionFromStringArr() error = %v, expectErr %v", err, tt.expectErr)
				return
			}
			if !tt.expectErr {
				if !reflect.DeepEqual(got, tt.want) {
					t.Errorf("PositionFromStringArr() = %v, want %v", got, tt.want)
				}
			}
		})
	}
}
func TestFindIntersection(t *testing.T) {
	tests := []struct {
		name     string
		a        []Position
		b        []Position
		expected []Position
	}{
		{
			name:     "no intersection",
			a:        []Position{QB, RB},
			b:        []Position{WR, K},
			expected: []Position{},
		},
		{
			name:     "partial intersection",
			a:        []Position{QB, RB, WR},
			b:        []Position{WR, K, TE},
			expected: []Position{WR},
		},
		{
			name:     "full intersection",
			a:        []Position{QB, RB},
			b:        []Position{QB, RB},
			expected: []Position{QB, RB},
		},
		{
			name:     "intersection with duplicates in b",
			a:        []Position{QB, RB},
			b:        []Position{QB, QB, RB, RB},
			expected: []Position{QB, RB},
		},
		{
			name:     "intersection with duplicates in a",
			a:        []Position{QB, QB, RB, RB},
			b:        []Position{QB, RB},
			expected: []Position{QB, RB},
		},
		{
			name:     "empty a",
			a:        []Position{},
			b:        []Position{QB, RB},
			expected: []Position{},
		},
		{
			name:     "empty b",
			a:        []Position{QB, RB},
			b:        []Position{},
			expected: []Position{},
		},
		{
			name:     "both empty",
			a:        []Position{},
			b:        []Position{},
			expected: []Position{},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := FindIntersection(tt.a, tt.b)

			// Compare as sets: order doesn't matter
			gotMap := make(map[Position]struct{}, len(got))
			for _, v := range got {
				gotMap[v] = struct{}{}
			}
			expectedMap := make(map[Position]struct{}, len(tt.expected))
			for _, v := range tt.expected {
				expectedMap[v] = struct{}{}
			}
			if !reflect.DeepEqual(gotMap, expectedMap) {
				t.Errorf("FindIntersection(%v, %v) = %v, want %v", tt.a, tt.b, got, tt.expected)
			}
		})
	}
}
