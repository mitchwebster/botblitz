package main

import (
	"fmt"
	"os"
	"strings"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"github.com/mitchwebster/botblitz/pkg/engine"
)

func main() {
	fmt.Println("Starting up...")

	isInteractive := enableInteractiveMode()
	bots := fetchBotList()

	engineSettings := engine.BotEngineSettings{
		VerboseLoggingEnabled: true,
		IsInteractiveMode:     isInteractive,
	}

	engine := engine.NewBotEngine(engineSettings, bots)

	fmt.Println(engine.Summarize())

	engine.Run()
}

func enableInteractiveMode() bool {
	envVariableName := "BB_ENABLE_INTERACTIVE_ENGINE"
	envVariableValue := os.Getenv(envVariableName)

	if strings.ToLower(envVariableValue) == "true" {
		return true
	}

	// default option is automated
	return false
}

func fetchBotList() []*common.Bot {
	return []*common.Bot{
		{
			Id:                 "Bigbot",
			SourceRepoUsername: "mitchwebster",
			SourceRepoName:     "testagent",
			FantasyTeamId:      0,
		},
	}
}
