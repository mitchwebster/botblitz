package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"strings"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"github.com/mitchwebster/botblitz/pkg/engine"
)

func main() {
	fmt.Println("Starting up...")

	makeGRPCcall()

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

func genLandscape() *common.FantasyLandscape {
	player := common.Player{
		FullName: "Kevin Durant",
	}

	bet := common.Bet{
		Player:               &player,
		ProfessionalHomeTeam: "Golden State Warriors",
		ProfessionalAwayTeam: "Phoenix Suns",
		Type:                 common.Bet_UNDER,
		Points:               25.5,
		Price:                -115.0,
	}

	landscape := common.FantasyLandscape{
		Bet: &bet,
	}

	return &landscape
}

func makeGRPCcall() error {
	var opts []grpc.DialOption
	opts = append(opts, grpc.WithTransportCredentials(insecure.NewCredentials()))

	conn, err := grpc.Dial("localhost:8080", opts...)
	if err != nil {
		log.Fatalf("fail to dial: %v", err)
	}

	defer conn.Close()
	client := common.NewAgentServiceClient(conn)

	landscape := genLandscape()

	selections, err := client.PerformFantasyActions(context.Background(), landscape)
	if err != nil {
		fmt.Println("Failed to get selections")
		fmt.Println(err)
		return err
	}

	fmt.Printf("Got selections %d\n", len(selections.Slots))
	fmt.Println(selections)

	return nil
}
