package main

import (
	"encoding/csv"
	"fmt"
	"log"
	"math/rand"
	"os"
	"strconv"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"github.com/mitchwebster/botblitz/pkg/engine"
)

const letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
const digits = "0123456789"

func main() {
	fmt.Println("Starting up...")

	year := 2024
	bots := fetchBotList()
	gameState, err := genDraftGameState(year)
	if err != nil {
		fmt.Println("Failed to build gameState unexpectedly")
		fmt.Println(err)
		os.Exit(1) // Crash hard
	}

	draftName := "Draft_" + generateRandomString(6)
	fmt.Printf("Starting Draft Sheet: %s\n", draftName)
	sheetClient, err := initializeDraftSheet(draftName, gameState)
	if err != nil {
		fmt.Println("Failed to setup Google Sheet connection")
		fmt.Println(err)
		os.Exit(1) // Crash hard
	}

	engineSettings := engine.BotEngineSettings{
		VerboseLoggingEnabled: false,
		SheetsClient:          sheetClient,
	}

	engine := engine.NewBotEngine(gameState, bots, engineSettings)

	fmt.Println(engine.Summarize())

	err = engine.Run()
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
		{Id: "1", Name: "Best Bots", Owner: "Tyler"},
		{Id: "2", Name: "Evil Bot", Owner: "Drew"},
	}
}

func fetchBotList() []*common.Bot {
	return []*common.Bot{
		{
			Id:            "Standard NFL Bot",
			SourceType:    common.Bot_LOCAL,
			SourcePath:    "/bots/nfl/standard-bot.py",
			FantasyTeamId: "0",
		},
		{
			Id:            "Request Bot Bot",
			SourceType:    common.Bot_LOCAL,
			SourcePath:    "/bots/nfl/request-bot.py",
			FantasyTeamId: "1",
		},
		{
			Id:            "Broken Bot",
			SourceType:    common.Bot_LOCAL,
			SourcePath:    "/bots/nfl/broken-bot.py",
			FantasyTeamId: "2",
		},
		// {
		// 	Id:                 "Remote bot",
		// 	SourceType:         common.Bot_REMOTE,
		// 	SourceRepoUsername: "mitchwebster",
		// 	SourceRepoName:     "testagent",
		// 	SourcePath:         "/dist/agent.py",
		// 	FantasyTeamId:      2,
		// },
	}
}

func genDraftGameState(year int) (*common.GameState, error) {
	players, err := loadPlayers(year)
	if err != nil {
		return nil, err
	}

	fantasy_teams := fetchFantasyTeams()

	player_slots := []*common.PlayerSlot{
		{Name: "QB"},
		{Name: "RB"},
	}

	settings := common.LeagueSettings{
		NumTeams:           uint32(len(fantasy_teams)),
		IsSnakeDraft:       true,
		TotalRounds:        3,
		PointsPerReception: 1.0,
		Year:               uint32(year),
		SlotsPerTeam:       player_slots,
	}

	game_state := common.GameState{
		CurrentPick:    1,
		DraftingTeamId: "0",
		LeagueSettings: &settings,
		Teams:          fantasy_teams,
		Players:        players,
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
		Bet:     &bet,
		Players: []*common.Player{&player},
	}

	return &landscape
}

func generateRandomString(length int) string {

	var result []rune
	// Combine letters and digits
	charSet := letters + digits

	for i := 0; i < length; i++ {
		// Get a random index from the character set
		randomIndex := rand.Intn(len(charSet))
		// Append the character at that index to the result
		result = append(result, rune(charSet[randomIndex]))
	}

	return string(result)
}

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
