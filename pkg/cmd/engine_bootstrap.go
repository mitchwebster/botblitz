package main

import (
	"context"
	"encoding/csv"
	"flag"
	"fmt"
	"log"
	"math"
	"math/rand"
	"os"
	"os/signal"
	"strconv"
	"time"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"github.com/mitchwebster/botblitz/pkg/engine"
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
	var year uint32 = 2024
	lastGameState, err := engine.LoadLastGameState(year)
	if err != nil {
		fmt.Println("Failed to load last game state")
		fmt.Println(err)
		os.Exit(1)
	}

	var sheetClient *engine.SheetsClient = nil // not needed for weekly fantasy
	engineSettings := engine.BotEngineSettings{
		VerboseLoggingEnabled: *enableVerboseLogging,
		GameMode:              engine.WeeklyFantasy,
	}

	curWeek := getCurrentWeek()

	// Update the current fantasy week so bots know what week to use
	lastGameState.CurrentFantasyWeek = uint32(curWeek)

	dataBytes, err := engine.FetchDataBytes(int(year), curWeek)
	if err != nil {
		fmt.Println("Failed to load data")
		fmt.Println(err)
		os.Exit(1)
	}

	return engine.NewBotEngine(lastGameState, engineSettings, sheetClient, dataBytes)
}

func bootstrapDraft() *engine.BotEngine {
	year := 2025
	bots := fetchBotList()

	shuffleBotOrder(bots) // randomize draft order

	gameState, err := genDraftGameState(year, bots)
	if err != nil {
		fmt.Println("Failed to build gameState unexpectedly")
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

	return engine.NewBotEngine(gameState, engineSettings, sheetClient, dataBytes)
}

func fetchBotList() []*common.Bot {
	return []*common.Bot{
		{
			Id:              "0",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl/mitch-bot.py",
			Owner:           "Mitch",
			FantasyTeamName: "Seattle's Best",
		},
		{
			Id:              "1",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl/tyler-bot.py",
			Owner:           "Tyler",
			FantasyTeamName: "Tyler's team",
		},
		{
			Id:              "2",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl/jon-bot.py",
			Owner:           "Jon",
			FantasyTeamName: "Jon's team",
		},
		{
			Id:              "3",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl/chris-bot.py",
			Owner:           "Chris",
			FantasyTeamName: "Chris's team",
		},
		{
			Id:              "4",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl/harry-bot.py",
			Owner:           "Harry",
			FantasyTeamName: "Harry's team",
		},
		{
			Id:              "5",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl/parker-bot.py",
			Owner:           "Parker",
			FantasyTeamName: "Butker School for Women",
		},
		{
			Id:              "6",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl/matt-bot.py",
			Owner:           "Matt",
			FantasyTeamName: "Matt's team",
		},
		{
			Id:              "7",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl/justin-bot.py",
			Owner:           "Justin",
			FantasyTeamName: "Justin's team",
		},
		{
			Id:              "8",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl/ryan/ryan-bot.py",
			Owner:           "Ryan",
			FantasyTeamName: "Ryans's team",
			EnvPath:         "/bots/nfl/ryan/ryan.env",
		},
		{
			Id:              "9",
			SourceType:      common.Bot_LOCAL,
			SourcePath:      "/bots/nfl/philip-bot.py",
			Owner:           "Philip",
			FantasyTeamName: "Philip's team",
		},
	}
}

func fetchPlayerSlots() []*common.PlayerSlot {
	return []*common.PlayerSlot{
		{AllowedPlayerPositions: []string{"QB"}, AllowsAnyPosition: false},
		{AllowedPlayerPositions: []string{"RB"}, AllowsAnyPosition: false},
		{AllowedPlayerPositions: []string{"RB"}, AllowsAnyPosition: false},
		{AllowedPlayerPositions: []string{"WR"}, AllowsAnyPosition: false},
		{AllowedPlayerPositions: []string{"WR"}, AllowsAnyPosition: false},
		{AllowedPlayerPositions: []string{"TE"}, AllowsAnyPosition: false},
		{AllowedPlayerPositions: []string{"RB", "WR", "TE"}, AllowsAnyPosition: false},
		{AllowedPlayerPositions: []string{"K"}, AllowsAnyPosition: false},
		{AllowedPlayerPositions: []string{"DST"}, AllowsAnyPosition: false},
		{AllowedPlayerPositions: []string{"Bench"}, AllowsAnyPosition: true},
		{AllowedPlayerPositions: []string{"Bench"}, AllowsAnyPosition: true},
		{AllowedPlayerPositions: []string{"Bench"}, AllowsAnyPosition: true},
		{AllowedPlayerPositions: []string{"Bench"}, AllowsAnyPosition: true},
		{AllowedPlayerPositions: []string{"Bench"}, AllowsAnyPosition: true},
		{AllowedPlayerPositions: []string{"Bench"}, AllowsAnyPosition: true},
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

func genDraftGameState(year int, bots []*common.Bot) (*common.GameState, error) {
	players, err := loadPlayers(year)
	if err != nil {
		return nil, err
	}

	player_slots := fetchPlayerSlots()

	settings := common.LeagueSettings{
		NumTeams:           uint32(len(bots)),
		IsSnakeDraft:       true,
		TotalRounds:        uint32(len(player_slots)),
		PointsPerReception: 1.0,
		Year:               uint32(year),
		SlotsPerTeam:       player_slots,
	}

	game_state := common.GameState{
		CurrentDraftPick:   1,
		CurrentBotTeamId:   "0",
		LeagueSettings:     &settings,
		Bots:               bots,
		Players:            players,
		CurrentFantasyWeek: 2, // Simulate week 2 (this has a bug kind of, in reality you won't see actual performance, so just need to ignore that you're getting that)
	}
	return &game_state, nil
}

func loadPlayers(year int) ([]*common.Player, error) {
	fmt.Println(year)
	player_rank_file := fmt.Sprintf("player_ranks_%d.csv", year)
	csv_file_path, err := engine.BuildLocalAbsolutePath("blitz_env/" + player_rank_file)
	if err != nil {
		return nil, err
	}

	// Open the CSV file
	file, err := os.Open(csv_file_path)
	if err != nil {
		log.Fatal(err)
	}
	defer file.Close()

	// Create a new CSV reader
	reader := csv.NewReader(file)

	// Skip the first line (the header)
	_, err = reader.Read() // Read the first line and ignore it
	if err != nil {
		log.Fatal(err)
	}

	players := []*common.Player{}

	// Read the file line by line
	for {
		record, err := reader.Read() // Read one record (a []string)
		if err != nil {
			// Check if we've reached the end of the file
			if err.Error() == "EOF" {
				break
			}
			log.Fatal(err)
		}

		byeWeek, _ := strconv.Atoi(record[4])
		rank, _ := strconv.Atoi(record[5])
		tier, _ := strconv.Atoi(record[6])

		pos_rank, pos_rank_err := strconv.Atoi(record[7])
		if pos_rank_err != nil {
			pos_rank = 0
		}

		pos_tier, pos_tier_err := strconv.Atoi(record[8])
		if pos_tier_err != nil {
			pos_tier = 0
		}

		player_status := common.PlayerStatus{
			Availability:     common.PlayerStatus_AVAILABLE,
			PickChosen:       0,
			CurrentTeamBotId: "",
		}

		player := common.Player{
			Id:               record[0],
			FullName:         record[1],
			AllowedPositions: []string{record[2]},
			ProfessionalTeam: record[3],
			PlayerByeWeek:    uint32(byeWeek),
			Rank:             uint32(rank),
			Tier:             uint32(tier),
			PositionRank:     uint32(pos_rank),
			PositionTier:     uint32(pos_tier),
			GsisId:           record[9],
			Status:           &player_status,
		}

		players = append(players, &player)
	}

	return players, nil
}

func getCurrentWeek() int {
	// Weeks since firt day of football: 9/5/24 at 8am UTC (roughly when this runs)
	pastDate := time.Date(2024, 9, 5, 8, 0, 0, 0, time.UTC)
	now := time.Now()
	duration := now.Sub(pastDate)
	return int(math.Ceil(duration.Hours()/(24*7))) + 1
}
