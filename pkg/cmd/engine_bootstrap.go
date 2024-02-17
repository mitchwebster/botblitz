package main

import (
	"fmt"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"github.com/mitchwebster/botblitz/pkg/engine"
)

func main() {
	fmt.Println("Starting up...")

	bots := fetchBotList()

	engineSettings := engine.BotEngineSettings{
		VerboseLoggingEnabled: true,
		NumSimulations:        1,
	}

	engine := engine.NewBotEngine(engineSettings, bots)

	fmt.Println(engine.Summarize())

	err := engine.Run()
	if err != nil {
		fmt.Println("Engine failed unexpectedly")
		fmt.Println(err)
	}
}

func fetchBotList() []*common.Bot {
	return []*common.Bot{
		{
			Id:            "Aggro NBA Bot",
			SourceType:    common.Bot_LOCAL,
			SourcePath:    "/bots/nba/aggro-bot.py",
			FantasyTeamId: 0,
		},
		{
			Id:            "Random NBA Bot",
			SourceType:    common.Bot_LOCAL,
			SourcePath:    "/bots/nba/random-bot.py",
			FantasyTeamId: 1,
		},
		{
			Id:                 "Remote bot",
			SourceType:         common.Bot_REMOTE,
			SourceRepoUsername: "mitchwebster",
			SourceRepoName:     "testagent",
			SourcePath:         "/dist/agent.py",
			FantasyTeamId:      2,
		},
	}
}
