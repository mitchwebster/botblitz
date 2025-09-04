package main

import (
	"context"
	"flag"
	"fmt"
	"math"
	"math/rand"
	"os"
	"os/signal"
	"strings"
	"time"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"github.com/mitchwebster/botblitz/pkg/engine"
	gamestate "github.com/mitchwebster/botblitz/pkg/gamestate"
)

var (
	enableGoogleSheets   = flag.Bool("enable_google_sheets", true, "If enabled, draft results are written to Google Sheets")
	enableVerboseLogging = flag.Bool("enable_verbose_logging", false, "If enabled, additional logging is printed to the console and stdout+stderr is captured from each bot invocation and saved to files under /tmp/")
	gameMode             = flag.String("game_mode", "Draft", "Used to determine which GameMode the engine should run")
)

func main() {
	fmt.Println("Starting up...")

	flag.Parse()

	mode, err := engine.GameModeFromString(*gameMode)
	if err != nil {
		fmt.Println("Failed to determine GameMode")
		fmt.Println(err)
		os.Exit(1) // Crash hard
	}

	var botEngine *engine.BotEngine = nil
	if mode == engine.Draft {
		botEngine = bootstrapDraft()
	} else if mode == engine.WeeklyFantasy {
		botEngine = bootstrapWeeklyFantasy()
	} else {
		fmt.Println("Invalid GameMode provided")
		os.Exit(1)
	}

	// Use a context object to tell the engine to gracefully shutdown when the
	// process is signaled(i.e. when ctrl+c is pressed)
	ctx := context.Background()
	ctx, cancelFunc := context.WithCancel(ctx)

	// register for ctrl+c signal and make it call cancelFunc
	c := make(chan os.Signal, 1)
	signal.Notify(c, os.Interrupt)
	go func() {
		for _ = range c {
			cancelFunc()
		}
	}()
	defer cancelFunc()

	fmt.Println(botEngine.Summarize())

	// schedule cleanup to run right before the function returns
	defer func() {
		botEngine.CleanupAllPyGrpcServerContainers()
		if err != nil {
			fmt.Printf("CRITICAL!! Failed to clean after bot run %s\n", err)
		}
	}()

	err = botEngine.Run(ctx)
	if err != nil {
		fmt.Println("Engine failed unexpectedly")
		fmt.Println(err)
		os.Exit(1) // Crash hard
	}
}

func bootstrapWeeklyFantasy() *engine.BotEngine {
	// TODO: refactor after sqllite upgrade
	return nil
	// var year uint32 = 2024
	// lastGameState, err := engine.LoadLastGameState(year)
	// if err != nil {
	// 	fmt.Println("Failed to load last game state")
	// 	fmt.Println(err)
	// 	os.Exit(1)
	// }

	// var sheetClient *engine.SheetsClient = nil // not needed for weekly fantasy
	// engineSettings := engine.BotEngineSettings{
	// 	VerboseLoggingEnabled: *enableVerboseLogging,
	// 	GameMode:              engine.WeeklyFantasy,
	// }

	// curWeek := getCurrentWeek()

	// // Update the current fantasy week so bots know what week to use
	// lastGameState.CurrentFantasyWeek = uint32(curWeek)

	// dataBytes, err := engine.FetchDataBytes(int(year), curWeek)
	// if err != nil {
	// 	fmt.Println("Failed to load data")
	// 	fmt.Println(err)
	// 	os.Exit(1)
	// }

	// return engine.NewBotEngine(nil, engineSettings, sheetClient, dataBytes)
}

func bootstrapDraft() *engine.BotEngine {
	year := uint32(2025)
	bots := fetchBotList()
	sourceCodeMap, err := getSourceCodeMap(bots)
	if err != nil {
		fmt.Println("Failed to find source code for configured bot")
		fmt.Println(err)
		os.Exit(1) // Crash hard
	}

	envVarsMap, err := getEnvVarsMap(bots)
	if err != nil {
		fmt.Println("Failed to find env vars for configured bot")
		fmt.Println(err)
		os.Exit(1) // Crash hard
	}

	shuffleBotOrder(bots) // randomize draft order

	settings := fetchLeagueSettings(year, bots)
	gameStateHandler, err := gamestate.NewGameStateHandlerForDraft(bots, settings)
	if err != nil {
		fmt.Println("Failed to build gameState db unexpectedly")
		fmt.Println(err)
		os.Exit(1) // Crash hard
	}

	var sheetClient *engine.SheetsClient = nil
	if *enableGoogleSheets {
		draftName := "Draft_" + engine.GenerateRandomString(6)
		fmt.Printf("Starting Draft Sheet: %s\n", draftName)

		sheetClient, err = engine.CreateSheetsClient(draftName)
		if err != nil {
			fmt.Println("Failed to setup Google Sheet connection")
			fmt.Println(err)
			os.Exit(1) // Crash hard
		}
	}

	engineSettings := engine.BotEngineSettings{
		VerboseLoggingEnabled: *enableVerboseLogging,
		GameMode:              engine.Draft,
	}

	dataBytes, err := engine.FetchDataBytes(int(year), 1) // default to week 1 for draft
	if err != nil {
		fmt.Println("Failed to load data")
		fmt.Println(err)
		os.Exit(1)
	}

	return engine.NewBotEngine(gameStateHandler, engineSettings, sheetClient, sourceCodeMap, envVarsMap, dataBytes)
}

func fetchBotList() []*common.Bot {
	return []*common.Bot{
		// {
		// 	Id:              "0",
		// 	SourceType:      common.Bot_LOCAL,
		// 	SourcePath:      "/bots/nfl/mitch-bot.py",
		// 	Owner:           "Mitch",
		// 	FantasyTeamName: "Seattle's Best",
		// },
		// {
		// 	Id:              "1",
		// 	SourceType:      common.Bot_LOCAL,
		// 	SourcePath:      "/bots/nfl/tyler-bot.py",
		// 	Owner:           "Tyler",
		// 	FantasyTeamName: "Tyler's team",
		// },
		// {
		// 	Id:              "2",
		// 	SourceType:      common.Bot_LOCAL,
		// 	SourcePath:      "/bots/nfl/jon-bot.py",
		// 	Owner:           "Jon",
		// 	FantasyTeamName: "Jon's team",
		// },
		{
			Id:              "3",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl2025/chris_bot.py",
			Owner:           "Chris A",
			FantasyTeamName: "Chris's team",
		},
		// {
		// 	Id:              "4",
		// 	SourceType:      common.Bot_LOCAL,
		// 	SourcePath:      "/bots/nfl/harry-bot.py",
		// 	Owner:           "Harry",
		// 	FantasyTeamName: "Harry's team",
		// },
		// {
		// 	Id:              "5",
		// 	SourceType:      common.Bot_LOCAL,
		// 	SourcePath:      "/bots/nfl/parker-bot.py",
		// 	Owner:           "Parker",
		// 	FantasyTeamName: "Butker School for Women",
		// },
		// {
		// 	Id:              "6",
		// 	SourceType:      common.Bot_LOCAL,
		// 	SourcePath:      "/bots/nfl/matt-bot.py",
		// 	Owner:           "Matt",
		// 	FantasyTeamName: "Matt's team",
		// },
		{
			Id:              "7",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl2025/justin_bot.py",
			Owner:           "Justin",
			FantasyTeamName: "Justin's team",
		},
		{
			Id:              "8",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl2025/ryan_bot.py",
			Owner:           "Ryan",
			FantasyTeamName: "Ryan's team",
			EnvPath:         "/bots/nfl/envs/ryan.env",
		},
		{
			Id:              "9",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl2025/philip_bot.py",
			Owner:           "Philip",
			FantasyTeamName: "Philip's team",
			EnvPath:         "/bots/nfl/envs/philip.env",
		},
		// {
		// 	Id:              "10",
		// 	SourceType:      common.Bot_LOCAL,
		// 	SourcePath:      "/bots/nfl/standard-bot.py",
		// 	Owner:           "Chris H",
		// 	FantasyTeamName: "Chris H's team",
		// },
		// {
		// 	Id:              "11",
		// 	SourceType:      common.Bot_LOCAL,
		// 	SourcePath:      "/bots/nfl/standard-bot.py",
		// 	Owner:           "Jack",
		// 	FantasyTeamName: "Jack's team",
		// },
		// {
		// 	Id:              "12",
		// 	SourceType:      common.Bot_LOCAL,
		// 	SourcePath:      "/bots/nfl/standard-bot.py",
		// 	Owner:           "Ben",
		// 	FantasyTeamName: "Ben's team",
		// },
		{
			Id:              "13",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl/standard-bot.py",
			Owner:           "User",
			FantasyTeamName: "Your Bot",
		},
	}
}

func fetchPlayerSlots() []*common.PlayerSlot {
	return []*common.PlayerSlot{
		{AllowedPlayerPositions: []string{"QB"}, AllowsAnyPosition: false},
		// {AllowedPlayerPositions: []string{"RB"}, AllowsAnyPosition: false},
		// {AllowedPlayerPositions: []string{"RB"}, AllowsAnyPosition: false},
		// {AllowedPlayerPositions: []string{"WR"}, AllowsAnyPosition: false},
		// {AllowedPlayerPositions: []string{"WR"}, AllowsAnyPosition: false},
		// {AllowedPlayerPositions: []string{"TE"}, AllowsAnyPosition: false},
		// {AllowedPlayerPositions: []string{"RB", "WR", "TE"}, AllowsAnyPosition: false},
		// {AllowedPlayerPositions: []string{"K"}, AllowsAnyPosition: false},
		// {AllowedPlayerPositions: []string{"DST"}, AllowsAnyPosition: false},
		// {AllowedPlayerPositions: []string{"Bench"}, AllowsAnyPosition: true},
		// {AllowedPlayerPositions: []string{"Bench"}, AllowsAnyPosition: true},
		// {AllowedPlayerPositions: []string{"Bench"}, AllowsAnyPosition: true},
		// {AllowedPlayerPositions: []string{"Bench"}, AllowsAnyPosition: true},
		// {AllowedPlayerPositions: []string{"Bench"}, AllowsAnyPosition: true},
		// {AllowedPlayerPositions: []string{"Bench"}, AllowsAnyPosition: true},
	}
}

func shuffleBotOrder(bots []*common.Bot) {
	n := len(bots)
	indices := make([]int, 0, n)

	for i := 0; i < n; i++ {
		indices = append(indices, i)
	}

	rand.Shuffle(len(indices), func(i, j int) {
		bots[i], bots[j] = bots[j], bots[i]
	})

	fmt.Println("New Bot Order:")
	for i := 0; i < n; i++ {
		fmt.Println(bots[i])
	}
}

func fetchLeagueSettings(year uint32, bots []*common.Bot) *common.LeagueSettings {
	playerslots := make(map[string]uint32)
	playerslots["QB"] = 1
	playerslots["RB"] = 2
	playerslots["WR"] = 2
	playerslots["SUPERFLEX"] = 1 // QB/RB/WR/TE
	playerslots["FLEX"] = 1      // RB/WR/TE
	playerslots["K"] = 1
	playerslots["DST"] = 1
	playerslots["BENCH"] = 3

	total_rounds := uint32(0)
	for _, v := range playerslots {
		total_rounds += v
	}

	settings := common.LeagueSettings{
		NumTeams:           uint32(len(bots)),
		IsSnakeDraft:       true,
		TotalRounds:        uint32(total_rounds),
		PointsPerReception: 1.0,
		Year:               uint32(year),
		SlotsPerTeam:       playerslots,
	}

	return &settings
}

func getCurrentWeek() int {
	// Weeks since firt day of football: 9/5/24 at 8am UTC (roughly when this runs)
	pastDate := time.Date(2024, 9, 5, 8, 0, 0, 0, time.UTC)
	now := time.Now()
	duration := now.Sub(pastDate)
	return int(math.Ceil(duration.Hours()/(24*7))) + 1
}

func getSourceCodeMap(bots []*common.Bot) (map[string][]byte, error) {
	fmt.Printf("\n-----------------------------------------\n")
	fmt.Println("Initializing Bots")

	sourceCodeMap := make(map[string][]byte)

	for _, bot := range bots {
		byteCode, err := fetchSourceCode(bot)
		if err != nil {
			fmt.Printf("Failed to retrieve bot source code for (%s)\n", bot.Id)
			return nil, err
		}

		sourceCodeMap[bot.Id] = byteCode
	}

	fmt.Printf("\n-----------------------------------------\n")

	return sourceCodeMap, nil
}

func fetchSourceCode(bot *common.Bot) ([]byte, error) {
	var botCode []byte

	if bot.SourceType == common.Bot_REMOTE {
		downloadedSourceCode, err := engine.DownloadGithubSourceCode(bot.SourceRepoUsername, bot.SourceRepoName, bot.SourcePath, false /*TODO: pass in setting*/)
		if err != nil {
			return nil, err
		}

		botCode = downloadedSourceCode
	} else {
		absPath, err := common.BuildLocalAbsolutePath(bot.SourcePath)
		if err != nil {
			return nil, err
		}

		bytes, err := os.ReadFile(absPath)
		if err != nil {
			return nil, err
		}

		botCode = bytes
	}

	fmt.Printf("Successfully retrieved source code for bot (%s)\n", bot.Id)
	return botCode, nil
}

func getEnvVarsMap(bots []*common.Bot) (map[string][]string, error) {
	fmt.Printf("\n-----------------------------------------\n")
	fmt.Println("Initializing Bots")

	envVarsMap := make(map[string][]string)

	for _, bot := range bots {
		if bot.EnvPath == "" {
			continue
		}

		vars, err := fetchEnvVars(bot)
		if err != nil {
			return nil, err
		}

		envVarsMap[bot.Id] = vars
	}

	fmt.Printf("\n-----------------------------------------\n")

	return envVarsMap, nil
}

func fetchEnvVars(bot *common.Bot) ([]string, error) {
	env := []string{}

	fmt.Printf("Grabbing env vars from: %s\n", bot.EnvPath)
	envAbsPath, err := common.BuildLocalAbsolutePath(bot.EnvPath)
	if err != nil {
		return env, err
	}

	envContent, err := os.ReadFile(envAbsPath)
	if err != nil {
		return env, err
	}

	// Assuming env file is formatted properly (key=value), TODO: Add validation at a later time
	env = append(strings.Split(string(envContent), "\n"))

	return env, nil
}
