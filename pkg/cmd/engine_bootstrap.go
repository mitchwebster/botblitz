package main

import (
	"context"
	"encoding/csv"
	"flag"
	"fmt"
	"log"
	"math/rand"
	"os"
	"os/signal"
	"strconv"
	"strings"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"github.com/mitchwebster/botblitz/pkg/engine"
)

var (
	enableGoogleSheets = flag.Bool("enable_google_sheets", true, "If enabled, draft results are written to Google Sheets")
	gameMode           = flag.String("game_mode", "Draft", "Used to determine which GameMode the engine should run")
)

// Define the enum type
type GameMode int

// Define constants for the enum values
const (
	Draft GameMode = iota
	WeeklyFantasy
)

func (s GameMode) String() string {
	return [...]string{"Draft", "WeeklyFantasy"}[s]
}

func GameModeFromString(s string) (GameMode, error) {
	switch strings.ToLower(s) {
	case "draft":
		return Draft, nil
	case "weeklyfantasy":
		return WeeklyFantasy, nil
	default:
		return -1, fmt.Errorf("invalid status: %s", s)
	}
}

func main() {
	fmt.Println("Starting up...")

	flag.Parse()

	mode, err := GameModeFromString(*gameMode)
	if err != nil {
		fmt.Println("Failed to determine GameMode")
		fmt.Println(err)
		os.Exit(1) // Crash hard
	}

	if mode == Draft {
		bootstrapDraft()
	} else if mode == WeeklyFantasy {
		bootstrapWeeklyFantasy()
	} else {
		fmt.Println("Invalid GameMode provided")
		os.Exit(1)
	}
}

func bootstrapWeeklyFantasy() {
	lastGameState, err := engine.LoadLastGameState()
	if err != nil {
		fmt.Println("Failed to load last game state")
		os.Exit(1)
	}

	fmt.Println(lastGameState.LeagueSettings.Year)
}

func bootstrapDraft() {
	year := 2024
	bots := fetchBotList()
	fantasyTeams := fetchFantasyTeams()

	shuffleFantasyTeamsAndBots(fantasyTeams, bots)

	gameState, err := genDraftGameState(year, fantasyTeams)
	if err != nil {
		fmt.Println("Failed to build gameState unexpectedly")
		fmt.Println(err)
		os.Exit(1) // Crash hard
	}

	var sheetClient *engine.SheetsClient = nil
	if *enableGoogleSheets {
		draftName := "Draft_" + engine.GenerateRandomString(6)
		fmt.Printf("Starting Draft Sheet: %s\n", draftName)
		sheetClient, err = initializeDraftSheet(draftName, gameState)
		if err != nil {
			fmt.Println("Failed to setup Google Sheet connection")
			fmt.Println(err)
			os.Exit(1) // Crash hard
		}
	}

	engineSettings := engine.BotEngineSettings{
		VerboseLoggingEnabled: false,
		SheetsClient:          sheetClient,
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

	engine := engine.NewBotEngine(gameState, bots, engineSettings)

	fmt.Println(engine.Summarize())

	err = engine.Run(ctx)
	if err != nil {
		fmt.Println("Engine failed unexpectedly")
		fmt.Println(err)
		os.Exit(1) // Crash hard
	} else {
		engine.PrintResults()
	}
}

func fetchFantasyTeams() []*common.FantasyTeam {
	return []*common.FantasyTeam{
		{Id: "0", Name: "Seattle's Best", Owner: "Mitch"},
		{Id: "1", Name: "Tyler's Team", Owner: "Tyler"},
		{Id: "2", Name: "Jon's Team", Owner: "Jon"},
		{Id: "3", Name: "Chris's Team", Owner: "Chris"},
		{Id: "4", Name: "Harry's Team", Owner: "Harry"},
		{Id: "5", Name: "Butker School for Women", Owner: "Parker"},
		{Id: "6", Name: "Matt's Team", Owner: "Matt"},
		{Id: "7", Name: "Justin's Team", Owner: "Justin"},
		{Id: "8", Name: "Ryan's Team", Owner: "Ryan"},
	}
}

func fetchBotList() []*common.Bot {
	return []*common.Bot{
		{
			Id:            "Standard NFL Bot",
			SourceType:    common.Bot_LOCAL,
			SourcePath:    "/bots/nfl/mitch-bot.py",
			FantasyTeamId: "0",
		},
		{
			Id:            "Request Bot Bot",
			SourceType:    common.Bot_LOCAL,
			SourcePath:    "/bots/nfl/tyler-bot.py",
			FantasyTeamId: "1",
		},
		{
			Id:            "Jon Bot",
			SourceType:    common.Bot_LOCAL,
			SourcePath:    "/bots/nfl/jon-bot.py",
			FantasyTeamId: "2",
		},
		{
			Id:            "Chris's Bot",
			SourceType:    common.Bot_LOCAL,
			SourcePath:    "/bots/nfl/chris-bot.py",
			FantasyTeamId: "3",
		},
		{
			Id:            "Harry's Bot",
			SourceType:    common.Bot_LOCAL,
			SourcePath:    "/bots/nfl/harry-bot.py",
			FantasyTeamId: "4",
		},
		{
			Id:            "Parker's Bot",
			SourceType:    common.Bot_LOCAL,
			SourcePath:    "/bots/nfl/parker-bot.py",
			FantasyTeamId: "5",
		},
		{
			Id:            "Matt's Bot",
			SourceType:    common.Bot_LOCAL,
			SourcePath:    "/bots/nfl/matt-bot.py",
			FantasyTeamId: "6",
		},
		{
			Id:            "Justin's Bot",
			SourceType:    common.Bot_LOCAL,
			SourcePath:    "/bots/nfl/justin-bot.py",
			FantasyTeamId: "7",
		},
		{
			Id:            "Ryans's Bot",
			SourceType:    common.Bot_LOCAL,
			SourcePath:    "/bots/nfl/ryan-bot.py",
			FantasyTeamId: "8",
		},
	}
}

func shuffleFantasyTeamsAndBots(teams []*common.FantasyTeam, bots []*common.Bot) {
	n := len(teams)
	indices := make([]int, 0, n)

	for i := 0; i < n; i++ {
		indices = append(indices, i)
	}

	rand.Shuffle(len(indices), func(i, j int) {
		teams[i], teams[j] = teams[j], teams[i]
		bots[i], bots[j] = bots[j], bots[i]
	})

	for i := 0; i < n; i++ {
		id := strconv.Itoa(i)
		teams[i].Id = id
		bots[i].FantasyTeamId = id

		fmt.Println(teams[i])
		fmt.Println(bots[i])
		fmt.Println()
	}
}

func genDraftGameState(year int, fantasyTeams []*common.FantasyTeam) (*common.GameState, error) {
	players, err := loadPlayers(year)
	if err != nil {
		return nil, err
	}

	player_slots := []*common.PlayerSlot{
		{AllowedPlayerPositions: []string{"QB"}},
		{AllowedPlayerPositions: []string{"RB"}},
		{AllowedPlayerPositions: []string{"RB"}},
		{AllowedPlayerPositions: []string{"WR"}},
		{AllowedPlayerPositions: []string{"WR"}},
		{AllowedPlayerPositions: []string{"TE"}},
		{AllowedPlayerPositions: []string{"RB", "WR", "TE"}},
		{AllowedPlayerPositions: []string{"K"}},
		{AllowedPlayerPositions: []string{"DST"}},
		{AllowedPlayerPositions: []string{"Bench"}},
		{AllowedPlayerPositions: []string{"Bench"}},
		{AllowedPlayerPositions: []string{"Bench"}},
		{AllowedPlayerPositions: []string{"Bench"}},
		{AllowedPlayerPositions: []string{"Bench"}},
		{AllowedPlayerPositions: []string{"Bench"}},
	}

	settings := common.LeagueSettings{
		NumTeams:           uint32(len(fantasyTeams)),
		IsSnakeDraft:       true,
		TotalRounds:        1,
		PointsPerReception: 1.0,
		Year:               uint32(year),
		SlotsPerTeam:       player_slots,
	}

	game_state := common.GameState{
		CurrentDraftPick: 1,
		CurrentBotTeamId: "0",
		LeagueSettings:   &settings,
		Teams:            fantasyTeams,
		Players:          players,
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

		draft_status := common.DraftStatus{
			Availability: common.DraftStatus_AVAILABLE,
			PickChosen:   0,
			TeamIdChosen: "",
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
			DraftStatus:      &draft_status,
		}

		players = append(players, &player)
	}

	return players, nil
}

// func genLandscape() *common.FantasyLandscape {
// 	player := common.Player{
// 		FullName: "Kevin Durant",
// 	}

// 	bet := common.Bet{
// 		Player:               &player,
// 		ProfessionalHomeTeam: "Golden State Warriors",
// 		ProfessionalAwayTeam: "Phoenix Suns",
// 		Type:                 common.Bet_UNDER,
// 		Points:               25.5,
// 		Price:                -115.0,
// 	}

// 	landscape := common.FantasyLandscape{
// 		Bet:     &bet,
// 		Players: []*common.Player{&player},
// 	}

// 	return &landscape
// }

func initializeDraftSheet(sheetName string, gameState *common.GameState) (*engine.SheetsClient, error) {
	client, err := engine.CreateSheetsClient(sheetName)
	if err != nil {
		return nil, err
	}

	err = engine.CreateNewDraftSheet(client)
	if err != nil {
		return nil, err
	}

	fmt.Println("Created Draft Sheet")

	err = engine.WriteContentToCell(engine.IntialRow, engine.InitialCol, "Round / Team", client)
	if err != nil {
		return nil, err
	}

	for i := 1; i <= int(gameState.LeagueSettings.TotalRounds); i++ {
		content := strconv.Itoa(i)
		err = engine.WriteContentToCell(engine.IntialRow+i, engine.InitialCol, content, client)
		if err != nil {
			return nil, err
		}
	}

	for i := 1; i <= len(gameState.Teams); i++ {
		team := gameState.Teams[i-1]
		content := team.Name + "(" + team.Owner + ")"
		newCol := rune(int(engine.InitialCol) + i)
		err = engine.WriteContentToCell(engine.IntialRow, newCol, content, client)
		if err != nil {
			return nil, err
		}
	}

	return client, nil
}
