package engine

import (
	"fmt"
)

func Hello() {
	fmt.Println("[engine]: Hello!")
}

func Multiply(scale int) int {
	return 1 * scale
}
