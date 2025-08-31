package gamestate

import (
	"encoding/csv"
	"encoding/json"
	"fmt"
	"os"
	"strconv"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

const saveFolderRelativePath = "data/game_states"
const filePrefix = "gs-"
const draftDesc = "draft"
const weekDesc = "week"
const fileSuffix = ".db"
const singleRowTableId = 1

// GameStateDatabase encapsulates the SQLite database operations
type GameStateHandler struct {
	db *gorm.DB

	// Cache the invariants to avoid repeated DB queries for no reason
	cachedBotList        []*common.Bot
	cachedLeagueSettings *common.LeagueSettings
}

type PlayerStatus int

const (
	Available PlayerStatus = iota
	OnHold
	Drafted
)

// String returns a human-readable version of the enum
func (s PlayerStatus) String() string {
	switch s {
	case Drafted:
		return "DRAFTED"
	case OnHold:
		return "ON_HOLD"
	case Available:
		return "AVAILABLE"
	default:
		return "UNKNOWN"
	}
}

func CheckDatabase() {
	// Open database
	db, err := gorm.Open(sqlite.Open("draft.db"), &gorm.Config{})
	if err != nil {
		panic(err)
	}

	// Auto migrate: creates "users" table if it doesn't exist
	// db.AutoMigrate(&User{})

	// Insert
	// db.Create(&User{Name: "Alice", Email: "alice@example.com"})

	// Query one
	var user Player
	db.First(&user, 16393) // find user with integer primary key 1
	println(user.FullName)

	// // Query all
	// var users []User
	// db.Find(&users)
	// for _, u := range users {
	//     println(u.Name, u.Email)
	// }

	// // Update
	// db.Model(&user).Update("Email", "newalice@example.com")

	// // Delete
	// db.Delete(&user)
}

func (handler *GameStateHandler) GetPlayerById(playerId string) (*Player, error) {
	var player Player
	result := handler.db.First(&player, playerId)

	if result.Error != nil {
		// real DB error
		return nil, fmt.Errorf("failed to get player from DB: %w", result.Error)
	}

	return &player, nil
}

func (handler *GameStateHandler) GetRandomPlayer() (*Player, error) {
	var player Player
	result := handler.db.Order("RANDOM()").First(&player)

	if result.Error != nil {
		return nil, fmt.Errorf("failed to get player from DB: %w", result.Error)
	}

	return &player, nil
}

func (handler *GameStateHandler) GetBots() ([]*common.Bot, error) {
	if handler.cachedBotList != nil {
		return handler.cachedBotList, nil
	}

	// list all of the bots from the database and convert to the common bot
	var dbBots []bot
	result := handler.db.Order("id ASC").Find(&dbBots)
	if result.Error != nil {
		return nil, fmt.Errorf("failed to fetch bots from database: %v", result.Error)
	}

	commonBots := make([]*common.Bot, len(dbBots))
	for i, dbBot := range dbBots {
		commonBot := &common.Bot{
			Id:                    dbBot.ID,
			FantasyTeamName:       dbBot.Name,
			Owner:                 dbBot.Owner,
			CurrentWaiverPriority: uint32(dbBot.CurrentWaiverPriority),
		}
		commonBots[i] = commonBot
	}

	// Cache the result
	handler.cachedBotList = commonBots

	return handler.cachedBotList, nil
}

func (handler *GameStateHandler) GetLeagueSettings() (*common.LeagueSettings, error) {
	if handler.cachedLeagueSettings != nil {
		return handler.cachedLeagueSettings, nil
	}

	// list all of the bots from the database and convert to the common bot
	var settings leagueSettings
	result := handler.db.First(&settings, singleRowTableId)

	if result.Error != nil {
		return nil, result.Error
	}

	leagueSettings := &common.LeagueSettings{
		NumTeams:           uint32(settings.NumTeams),
		SlotsPerTeam:       nil,
		IsSnakeDraft:       settings.IsSnakeDraft,
		TotalRounds:        uint32(settings.TotalRounds),
		PointsPerReception: float32(settings.PointsPerReception),
		Year:               uint32(settings.Year),
	}
	// Cache the result
	handler.cachedLeagueSettings = leagueSettings

	return handler.cachedLeagueSettings, nil
}

func (handler *GameStateHandler) SetCurrentBotTeamId(botId string) error {
	var gameStatus gameStatus
	result := handler.db.First(&gameStatus, singleRowTableId)

	if result.Error != nil {
		return result.Error
	}

	result = handler.db.Model(&gameStatus).Update("CurrentBotID", botId)
	if result.Error != nil {
		return result.Error
	}

	return nil
}

func (handler *GameStateHandler) IncrementDraftPick() error {
	var gameStatus gameStatus
	result := handler.db.First(&gameStatus, singleRowTableId)

	if result.Error != nil {
		return result.Error
	}

	nextPick := gameStatus.CurrentDraftPick + 1

	result = handler.db.Model(&gameStatus).Update("CurrentDraftPick", nextPick)
	if result.Error != nil {
		return result.Error
	}

	return nil
}

func (handler *GameStateHandler) GetCurrentDraftPick() (int, error) {

	var gameStatus gameStatus
	result := handler.db.First(&gameStatus, singleRowTableId)

	if result.Error != nil {
		return -1, result.Error
	}

	return gameStatus.CurrentDraftPick, nil
}

// UpdatePlayer updates multiple player fields at once
func (handler *GameStateHandler) UpdatePlayer(playerID string, availability *string, pickChosen *int, botID *string) error {
	updates := make(map[string]interface{})

	if availability != nil {
		updates["availability"] = *availability
	}
	if pickChosen != nil {
		updates["pick_chosen"] = *pickChosen
	}
	if botID != nil {
		updates["current_bot_id"] = *botID
	}

	return handler.db.Model(&Player{}).Where("id = ?", playerID).Updates(updates).Error
}

func NewGameStateHandlerForDraft(bots []*common.Bot, settings *common.LeagueSettings) (*GameStateHandler, error) {
	if len(bots) < 1 {
		return nil, fmt.Errorf("Cannot create game for %d bots", len(bots))
	}

	saveFileName, err := getSaveFileName(settings.Year, draftDesc)
	if err != nil {
		return nil, err
	}

	err = verifyFileDoesNotExistAndCreate(saveFileName)
	if err != nil {
		return nil, err
	}

	db, err := gorm.Open(sqlite.Open(saveFileName), &gorm.Config{})
	if err != nil {
		return nil, err
	}

	// Auto migrate the database tables
	err = db.AutoMigrate(&bot{}, &gameStatus{}, &leagueSettings{}, &Player{})
	if err != nil {
		return nil, err
	}

	err = populateDatabase(db, bots, settings)
	if err != nil {
		return nil, err
	}

	return &GameStateHandler{db: db, cachedBotList: nil, cachedLeagueSettings: nil}, nil
}

func populateDatabase(db *gorm.DB, bots []*common.Bot, settings *common.LeagueSettings) error {
	err := populateBotsTable(db, bots)
	if err != nil {
		return err
	}

	err = populateLeagueSettingsTable(db, settings)
	if err != nil {
		return err
	}

	err = populateGameStatusTable(db, bots)
	if err != nil {
		return err
	}

	err = populatePlayersTable(db, settings.Year)
	if err != nil {
		return err
	}

	return nil
}

func populateBotsTable(db *gorm.DB, bots []*common.Bot) error {
	for i, commonBot := range bots {
		dbBot := bot{
			ID:                    commonBot.Id,
			Name:                  commonBot.FantasyTeamName,
			Owner:                 commonBot.Owner,
			CurrentWaiverPriority: 0,     // Decided later
			DraftOrder:            i + 1, // Assign draft order based on array position
		}

		// Use Create to insert the bot record
		result := db.Create(&dbBot)
		if result.Error != nil {
			return fmt.Errorf("failed to insert bot %s: %v", commonBot.Id, result.Error)
		}
	}

	return nil
}

func populateLeagueSettingsTable(db *gorm.DB, settings *common.LeagueSettings) error {
	playerSlotsJSON, err := json.Marshal(settings.SlotsPerTeam)
	if err != nil {
		return fmt.Errorf("failed to marshal player slots to JSON: %v", err)
	}

	dbLeagueSettings := leagueSettings{
		Year:               int(settings.Year),
		PlayerSlots:        string(playerSlotsJSON),
		IsSnakeDraft:       settings.IsSnakeDraft,
		TotalRounds:        int(settings.TotalRounds),
		PointsPerReception: float64(settings.PointsPerReception),
		NumTeams:           int(settings.NumTeams),
	}

	result := db.Create(&dbLeagueSettings)
	if result.Error != nil {
		return fmt.Errorf("failed to insert league settings: %v", result.Error)
	}

	return nil
}

func populatePlayersTable(db *gorm.DB, year uint32) error {
	players, err := loadPlayers(year)
	if err != nil {
		return err
	}

	for _, player := range players {
		result := db.Create(&player)
		if result.Error != nil {
			return fmt.Errorf("failed to insert player %s: %v", player.ID, result.Error)
		}
	}

	return nil
}

func populateGameStatusTable(db *gorm.DB, bots []*common.Bot) error {
	currentBotID := bots[0].Id

	dbGameStatus := gameStatus{
		CurrentBotID:       &currentBotID,
		CurrentDraftPick:   1,
		CurrentFantasyWeek: 0,
	}

	result := db.Create(&dbGameStatus)
	if result.Error != nil {
		return fmt.Errorf("failed to insert game status: %v", result.Error)
	}

	return nil
}

func loadPlayers(year uint32) ([]Player, error) {
	player_rank_file := fmt.Sprintf("player_ranks_%d.csv", year)
	csv_file_path, err := common.BuildLocalAbsolutePath("blitz_env/" + player_rank_file)
	if err != nil {
		return nil, err
	}

	// Open the CSV file
	file, err := os.Open(csv_file_path)
	if err != nil {
		return nil, fmt.Errorf("failed to open CSV file: %v", err)
	}
	defer file.Close()

	// Create a new CSV reader
	reader := csv.NewReader(file)

	// Skip the first line (the header)
	_, err = reader.Read() // Read the first line and ignore it
	if err != nil {
		return nil, fmt.Errorf("failed to read CSV header: %v", err)
	}

	players := []Player{}

	// Read the file line by line
	for {
		record, err := reader.Read() // Read one record (a []string)
		if err != nil {
			// Check if we've reached the end of the file
			if err.Error() == "EOF" {
				break
			}
			return nil, fmt.Errorf("failed to read CSV record: %v", err)
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

		// Convert allowed positions to JSON string
		allowedPositionsJSON, err := json.Marshal([]string{record[2]})
		if err != nil {
			return nil, fmt.Errorf("failed to marshal allowed positions: %v", err)
		}

		dbPlayer := Player{
			ID:               record[0],
			FullName:         record[1],
			AllowedPositions: string(allowedPositionsJSON),
			ProfessionalTeam: record[3],
			PlayerByeWeek:    byeWeek,
			Rank:             rank,
			Tier:             tier,
			PositionRank:     pos_rank,
			PositionTier:     pos_tier,
			GSISID:           record[9],
			Availability:     "AVAILABLE",
			PickChosen:       nil,
			CurrentBotID:     nil,
		}

		players = append(players, dbPlayer)
	}

	return players, nil
}

func verifyFileDoesNotExistAndCreate(filePath string) error {
	_, err := os.Stat(filePath)
	if err != nil && os.IsNotExist(err) {
		file, err := os.Create(filePath)
		if err != nil {
			return err
		}
		file.Close()

		return nil
	}

	return fmt.Errorf("Draft file already exists, please delete before drafting again")
}

func getSaveFileName(year uint32, description string) (string, error) {
	absFolderPath, err := getSaveFolderPath(year)
	if err != nil {
		return "", err
	}

	err = os.MkdirAll(absFolderPath, os.ModePerm)
	if err != nil {
		return "", err
	}

	// we want files named like gs-draft.db or gs-week1.db
	fileName := filePrefix + description + fileSuffix
	return absFolderPath + "/" + fileName, nil
}

func getSaveFolderPath(year uint32) (string, error) {
	folderName := saveFolderRelativePath + "/" + strconv.Itoa(int(year))
	absPath, err := common.BuildLocalAbsolutePath(folderName)
	if err != nil {
		return "", err
	}

	return absPath, nil
}

// Below are the database representations of the gameState, these do not get autogenerated and need to be updated if we want to store more info in gamestate

// --------------------
// Table: bots
// --------------------
type bot struct {
	ID                    string `gorm:"primaryKey;column:id"`
	DraftOrder            int    `gorm:"column:draft_order"`
	Name                  string `gorm:"column:name"`
	Owner                 string `gorm:"column:owner"`
	CurrentWaiverPriority int    `gorm:"column:current_waiver_priority"`

	// Relations
	GameStatuses []gameStatus `gorm:"foreignKey:CurrentBotID"`
	Players      []Player     `gorm:"foreignKey:CurrentBotID"`
}

// --------------------
// Table: game_status
// --------------------
type gameStatus struct {
	ID                 int     `gorm:"primaryKey;column:id"`
	CurrentBotID       *string `gorm:"column:current_bot_id"`
	CurrentDraftPick   int     `gorm:"column:current_draft_pick"`
	CurrentFantasyWeek int     `gorm:"column:current_fantasy_week"`

	// Relation
	CurrentBot *bot `gorm:"foreignKey:CurrentBotID;references:ID"`
}

// --------------------
// Table: league_settings
// --------------------
type leagueSettings struct {
	ID                 int     `gorm:"primaryKey;column:id"`
	NumTeams           int     `gorm:"column:num_teams"`
	Year               int     `gorm:"column:year"`
	PlayerSlots        string  `gorm:"type:json;column:player_slots"` // stored as JSON string
	IsSnakeDraft       bool    `gorm:"column:is_snake_draft"`
	TotalRounds        int     `gorm:"column:total_rounds"`
	PointsPerReception float64 `gorm:"column:points_per_reception"`
}

// --------------------
// Table: players
// --------------------
type Player struct {
	ID               string  `gorm:"primaryKey;column:id"`
	FullName         string  `gorm:"column:full_name"`
	ProfessionalTeam string  `gorm:"column:professional_team"`
	PlayerByeWeek    int     `gorm:"column:player_bye_week"`
	Rank             int     `gorm:"column:rank"`
	Tier             int     `gorm:"column:tier"`
	PositionRank     int     `gorm:"column:position_rank"`
	PositionTier     int     `gorm:"column:position_tier"`
	GSISID           string  `gorm:"column:gsis_id"`
	AllowedPositions string  `gorm:"type:json;column:allowed_positions"` // stored as JSON string
	Availability     string  `gorm:"column:availability"`
	PickChosen       *int    `gorm:"column:pick_chosen"`
	CurrentBotID     *string `gorm:"column:current_bot_id"`

	// Relation
	CurrentBot *bot `gorm:"foreignKey:CurrentBotID;references:ID"`
}
