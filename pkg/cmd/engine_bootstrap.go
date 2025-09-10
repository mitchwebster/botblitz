package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strings"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"github.com/mitchwebster/botblitz/pkg/engine"
	gamestate "github.com/mitchwebster/botblitz/pkg/gamestate"
)

var (
	enableGoogleSheets   = flag.Bool("enable_google_sheets", true, "If enabled, draft results are written to Google Sheets")
	enableVerboseLogging = flag.Bool("enable_verbose_logging", false, "If enabled, additional logging is printed to the console and stdout+stderr is captured from each bot invocation and saved to files under /tmp/")
	gameMode             = flag.String("game_mode", "Draft", "Used to determine which GameMode the engine should run")
	isRunningOnGithub    = flag.Bool("is_running_on_github", false, "If enabled, the engine is running on GitHub")
	year                 = flag.Int("year", 2025, "The year to run the engine against")
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
	} else if mode == engine.PerformWeeklyFantasyActions {
		botEngine = bootstrapWeeklyFantasy(mode)
	} else if mode == engine.FinishPreviousWeek {
		botEngine = bootstrapFinishWeek(mode)
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

func bootstrapWeeklyFantasy(gameMode engine.GameMode) *engine.BotEngine {
	year := uint32(*year)
	gameStateHandler, err := gamestate.LoadGameStateForWeeklyFantasy(year)
	if err != nil {
		fmt.Println("Failed to load game state for weekly fantasy")
		fmt.Println(err)
		os.Exit(1)
	}

	engineSettings := engine.BotEngineSettings{
		VerboseLoggingEnabled: *enableVerboseLogging,
		GameMode:              gameMode,
	}

	sourceCodeMap, envVarsMap, err := validateBotsAndFetchSourceCode(gameStateHandler)
	if err != nil {
		fmt.Println("Failed to validate bots and fetch source code")
		fmt.Println(err)
		os.Exit(1)
	}

	// TODO: load the actual weekly data into the db

	return engine.NewBotEngine(gameStateHandler, engineSettings, nil, sourceCodeMap, envVarsMap)
}

func bootstrapFinishWeek(gameMode engine.GameMode) *engine.BotEngine {
	year := uint32(*year)
	gameStateHandler, err := gamestate.LoadGameStateForWeeklyFantasy(year)
	if err != nil {
		fmt.Println("Failed to load game state for weekly fantasy")
		fmt.Println(err)
		os.Exit(1)
	}

	engineSettings := engine.BotEngineSettings{
		VerboseLoggingEnabled: *enableVerboseLogging,
		GameMode:              gameMode,
	}

	// Do not load any source code here as we should not be calling any bots when finishing the week
	return engine.NewBotEngine(gameStateHandler, engineSettings, nil, nil, nil)
}

func validateBotsAndFetchSourceCode(handler *gamestate.GameStateHandler) (map[string][]byte, map[string][]string, error) {
	botsFromDatabase, err := handler.GetBots()
	if err != nil {
		return nil, nil, err
	}

	definedBotMap := make(map[string]*common.Bot)
	definedBots := fetchBotList()

	for _, bot := range definedBots {
		definedBotMap[bot.Id] = bot
	}

	for _, bot := range botsFromDatabase {
		_, ok := definedBotMap[bot.ID]
		if !ok {
			return nil, nil, errors.New(fmt.Sprintf("The defined bot %s does not exist!", bot.ID))
		}
	}

	sourceCodeMap, envVarsMap, err := getCodeAndEnvs(definedBots)
	if err != nil {
		return nil, nil, err
	}

	return sourceCodeMap, envVarsMap, nil
}

func getCodeAndEnvs(bots []*common.Bot) (map[string][]byte, map[string][]string, error) {
	sourceCodeMap, err := getSourceCodeMap(bots)
	if err != nil {
		return nil, nil, err
	}

	envVarsMap, err := getEnvVarsMap(bots)
	if err != nil {
		return nil, nil, err
	}

	return sourceCodeMap, envVarsMap, nil
}

func bootstrapDraft() *engine.BotEngine {
	year := uint32(*year)
	bots := fetchBotList()
	sourceCodeMap, envVarsMap, err := getCodeAndEnvs(bots)
	if err != nil {
		fmt.Println("Failed to find source code / env vars for configured bot")
		fmt.Println(err)
		os.Exit(1) // Crash hard
	}

	settings := fetchLeagueSettings(year, uint32(len(bots)))
	gameStateHandler, err := gamestate.NewGameStateHandlerForDraft(bots, settings)
	if err != nil {
		fmt.Println("Failed to build gameState db unexpectedly")
		fmt.Println(err)
		os.Exit(1) // Crash hard
	}

	// Randomly initialize the bot cache for usage later on
	_, err = gameStateHandler.GetBotsInRandomOrder()
	if err != nil {
		fmt.Println("Failed to get bots from newly created db")
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

	return engine.NewBotEngine(gameStateHandler, engineSettings, sheetClient, sourceCodeMap, envVarsMap)
}

func fetchBotList() []*common.Bot {
	return []*common.Bot{
		{
			Id:              "0",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl2025/mitch_bot.py",
			Owner:           "Mitch",
			FantasyTeamName: "Seattle's Best",
		},
		{
			Id:              "1",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl2025/chris_bot.py",
			Owner:           "Chris A",
			FantasyTeamName: "Chris's team",
		},
		{
			Id:              "2",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl2025/justin_bot.py",
			Owner:           "Justin",
			FantasyTeamName: "Justin's team",
		},
		{
			Id:              "3",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl2025/ryan_bot.py",
			Owner:           "Ryan",
			FantasyTeamName: "Ryan's team",
			EnvPath:         "/bots/nfl/envs/ryan.env",
			GithubEnvName:   "RYAN_ENV",
		},
		{
			Id:              "4",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl2025/philip_bot.py",
			Owner:           "Philip",
			FantasyTeamName: "Philip's team",
			EnvPath:         "/bots/nfl/envs/philip.env",
			GithubEnvName:   "PHILIP_ENV",
		},
		{
			Id:              "5",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl2025/chrish_bot.py",
			Owner:           "Chris H",
			FantasyTeamName: "Chris H's team",
		},
		{
			Id:              "6",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl2025/parker_bot.py",
			Owner:           "Parker",
			FantasyTeamName: "Parker's team",
		},
		{
			Id:              "7",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl2025/standard-bot.py",
			Owner:           "Claude",
			FantasyTeamName: "Standard Bot",
		},
		{
			Id:              "8",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl2025/jack_bot.py",
			Owner:           "Jack",
			FantasyTeamName: "AVOCADOTOAST4U",
		},
		{
			Id:              "9",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl2025/ben_bot.py",
			Owner:           "Ben",
			FantasyTeamName: "Ben's bread",
		},
		{
			Id:              "10",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl2025/tyler_bot.py",
			Owner:           "Tyler",
			FantasyTeamName: "Tyler's team",
			EnvPath:         "/bots/nfl/envs/tyler.env",
			GithubEnvName:   "TYLER_ENV",
		},
		{
			Id:              "11",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl2025/matt_bot.py",
			Owner:           "Matt",
			FantasyTeamName: "Matt's team",
		},
		{
			Id:              "12",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl2025/jon_bot.py",
			Owner:           "Jon",
			FantasyTeamName: "Jon's team",
		},
		{
			Id:              "13",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl2025/harry_bot.py",
			Owner:           "Harry",
			FantasyTeamName: "Harry's team",
		},
	}
}

func fetchLeagueSettings(year uint32, numTeams uint32) *common.LeagueSettings {
	playerslots := make(map[string]uint32)
	playerslots[engine.QB.String()] = 1
	playerslots[engine.RB.String()] = 2
	playerslots[engine.WR.String()] = 2
	playerslots[engine.SUPERFLEX.String()] = 1
	playerslots[engine.FLEX.String()] = 1
	playerslots[engine.K.String()] = 1
	playerslots[engine.DST.String()] = 1
	playerslots[engine.BENCH.String()] = 3

	total_rounds := uint32(0)
	for _, v := range playerslots {
		total_rounds += v
	}

	settings := common.LeagueSettings{
		NumTeams:           numTeams,
		IsSnakeDraft:       true,
		TotalRounds:        uint32(total_rounds),
		PointsPerReception: 1.0,
		Year:               uint32(year),
		SlotsPerTeam:       playerslots,
	}

	return &settings
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
		if bot.EnvPath == "" && bot.GithubEnvName == "" {
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

	if isRunningOnGithub != nil && *isRunningOnGithub {
		if bot.GithubEnvName == "" {
			return env, errors.New(fmt.Sprintf("Bot %s is missing a github_env_name, cannot fetch env var from GitHub Actions", bot.Owner))
		}

		//Load env using name
		value := os.Getenv(bot.GithubEnvName)
		if value == "" {
			return env, errors.New(fmt.Sprintf("%s is not set", bot.GithubEnvName))
		}

		env = append(env, value)
		fmt.Printf("Retrieved env vars from Github: %s\n", bot.GithubEnvName)
	} else {
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
		fmt.Printf("Retrieved env vars from local file system: %s\n", bot.EnvPath)
	}

	fmt.Printf("Successfully found env vars for bot (%s)\n", bot.Id)
	return env, nil
}
