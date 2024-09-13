package main

import (
	"encoding/csv"
	"fmt"
	"log"
	"os"
	"strconv"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"github.com/mitchwebster/botblitz/pkg/engine"
)

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

	print(gameState)

	engineSettings := engine.BotEngineSettings{
		VerboseLoggingEnabled: true,
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

func fetchBotList() []*common.Bot {
	return []*common.Bot{
		{
			Id:            "Standard NFL Bot",
			SourceType:    common.Bot_LOCAL,
			SourcePath:    "/bots/nfl/standard-bot.py",
			FantasyTeamId: 0,
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

	fantasy_teams := []*common.FantasyTeam{
		{Id: "0", Name: "Seattle's Best", Owner: "Mitch"},
		{Id: "1", Name: "Best Bots", Owner: "Tyler"},
	}

	player_slots := []*common.PlayerSlot{
		{Name: "QB"},
		{Name: "RB"},
	}

	settings := common.LeagueSettings{
		NumTeams:           uint32(len(fantasy_teams)),
		IsSnakeDraft:       true,
		TotalRounds:        15,
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
			PickChosen:   -1,
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
