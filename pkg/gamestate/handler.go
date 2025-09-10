package gamestate

import (
	"encoding/csv"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

const AppDatabaseName = "gamestate" + fileSuffix

const saveFolderRelativePath = "data/game_states"
const statsFolderRelativePath = "data/stats"
const filePrefix = "gs-"
const draftDesc = "draft"
const seasonDesc = "season"
const fileSuffix = ".db"
const statsDatabaseFileName = "stats.db"
const singleRowTableId = 1

// GameStateDatabase encapsulates the SQLite database operations
type GameStateHandler struct {
	db             *gorm.DB
	dbSaveFilePath string

	// Cache the invariants to avoid repeated DB queries for no reason
	cachedBotList        []Bot
	cachedLeagueSettings *LeagueSettings
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

func (handler *GameStateHandler) GetDBSaveFilePath() string {
	return handler.dbSaveFilePath
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

func (handler *GameStateHandler) GetBots() ([]Bot, error) {
	if handler.cachedBotList != nil {
		return handler.cachedBotList, nil
	}

	// list all of the bots from the database and convert to the common bot
	var dbBots []Bot
	result := handler.db.Order("id ASC").Find(&dbBots)
	if result.Error != nil {
		return nil, fmt.Errorf("failed to fetch bots from database: %v", result.Error)
	}

	// Cache the result
	handler.cachedBotList = dbBots

	return handler.cachedBotList, nil
}

func (handler *GameStateHandler) GetBotsInRandomOrder() ([]Bot, error) {
	if handler.cachedBotList != nil {
		return handler.cachedBotList, nil
	}

	// list all of the bots from the database and convert to the common bot
	var dbBots []Bot
	result := handler.db.Order("RANDOM()").Find(&dbBots)
	if result.Error != nil {
		return nil, fmt.Errorf("failed to fetch bots from database: %v", result.Error)
	}

	// Cache the result
	handler.cachedBotList = dbBots

	return handler.cachedBotList, nil
}

func (handler *GameStateHandler) GetMatchupsForWeek(week uint32) ([]Matchup, error) {
	var matchups []Matchup
	result := handler.db.Where("week = ?", week).Find(&matchups)
	if result.Error != nil {
		return nil, fmt.Errorf("failed to fetch matchups from database: %v", result.Error)
	}

	return matchups, nil
}

// UpdateMatchup updates the winner and points for each team in a matchup within a transaction
func (handler *GameStateHandler) SetMatchResult(matchupID uint, homeScore, visitorScore float64, winningBotID string) error {
	return handler.db.Transaction(func(tx *gorm.DB) error {
		updates := map[string]interface{}{
			"home_score":     homeScore,
			"visitor_score":  visitorScore,
			"winning_bot_id": winningBotID,
		}
		if err := tx.Model(&Matchup{}).Where("id = ?", matchupID).Updates(updates).Error; err != nil {
			return err
		}
		return nil
	})
}

func (handler *GameStateHandler) GetLeagueSettings() (*LeagueSettings, error) {
	if handler.cachedLeagueSettings != nil {
		return handler.cachedLeagueSettings, nil
	}

	// list all of the bots from the database and convert to the common bot
	var settings LeagueSettings
	result := handler.db.First(&settings, singleRowTableId)

	if result.Error != nil {
		return nil, result.Error
	}

	// Cache the result
	handler.cachedLeagueSettings = &settings

	return handler.cachedLeagueSettings, nil
}

func (handler *GameStateHandler) PerformAddDrop(botId string, playerToAdd string, playerToDrop string, budgetReduction int) error {
	return handler.db.Transaction(func(tx *gorm.DB) error {
		if err := tx.Model(&Player{}).Where("id = ?", playerToAdd).Update("CurrentBotID", botId).Error; err != nil {
			return err // rollback
		}

		if err := tx.Model(&Player{}).Where("id = ?", playerToAdd).Update("Availability", Drafted.String()).Error; err != nil {
			return err // rollback
		}

		if err := tx.Model(&Player{}).Where("id = ?", playerToDrop).Update("CurrentBotID", nil).Error; err != nil {
			return err // rollback
		}

		if err := tx.Model(&Player{}).Where("id = ?", playerToDrop).Update("Availability", Available.String()).Error; err != nil {
			return err // rollback
		}

		result := tx.Model(&Bot{}).Where("id = ?", botId).Update("RemainingWaiverBudget", gorm.Expr("remaining_waiver_budget - ?", budgetReduction))
		if result.Error != nil {
			return result.Error
		}

		return nil // commit
	})
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

func (handler *GameStateHandler) IncrementFantasyWeek() error {
	var gameStatus gameStatus
	result := handler.db.First(&gameStatus, singleRowTableId)

	if result.Error != nil {
		return result.Error
	}

	nextWeek := gameStatus.CurrentFantasyWeek + 1

	result = handler.db.Model(&gameStatus).Update("CurrentFantasyWeek", nextWeek)
	if result.Error != nil {
		return result.Error
	}

	return nil
}

func (handler *GameStateHandler) GetCurrentFantasyWeek() (int, error) {
	var gameStatus gameStatus
	result := handler.db.First(&gameStatus, singleRowTableId)

	if result.Error != nil {
		return -1, result.Error
	}

	return gameStatus.CurrentFantasyWeek, nil
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

func LoadGameStateForWeeklyFantasy(year uint32) (*GameStateHandler, error) {
	saveFileName, err := getSaveFileName(year, seasonDesc)
	if err != nil {
		return nil, err
	}

	db, err := gorm.Open(sqlite.Open(saveFileName), &gorm.Config{})
	if err != nil {
		return nil, err
	}

	var gameStatus gameStatus
	result := db.First(&gameStatus, gameStatus)
	if result.Error != nil {
		return nil, result.Error
	}

	if gameStatus.CurrentFantasyWeek == 0 {
		err := initSeason(db)
		if err != nil {
			return nil, err
		}

		db.Model(&gameStatus).Update("CurrentFantasyWeek", 1)
	}

	return &GameStateHandler{db: db, dbSaveFilePath: saveFileName, cachedBotList: nil, cachedLeagueSettings: nil}, nil
}

func initSeason(db *gorm.DB) error {
	exists := db.Migrator().HasTable(&Matchup{})
	if !exists {
		err := db.AutoMigrate(&Matchup{})
		if err != nil {
			return err
		}
	}

	var dbBots []Bot
	result := db.Order("RANDOM()").Find(&dbBots)
	if result.Error != nil {
		return result.Error
	}

	botIds := []string{}
	// Set the initial waiver priority based on the random order
	for index, dbBot := range dbBots {
		fmt.Printf("Bot %d: ID: %s, Name: %s\n", index+1, dbBot.ID, dbBot.Name)
		botIds = append(botIds, dbBot.ID)
		result = db.Model(&dbBot).Update("CurrentWaiverPriority", index)
		if result.Error != nil {
			return result.Error
		}
	}
	totalWeeks := 14
	matchups := generateSchedule(botIds, totalWeeks)
	for _, weekMatchups := range matchups {
		for _, matchup := range weekMatchups {
			fmt.Printf("Week %d: %s vs %s\n", matchup.Week, matchup.HomeBotID, matchup.VisitorBotID)

			result := db.Create(&matchup)
			if result.Error != nil {
				return fmt.Errorf("failed to insert matchup %v", result.Error)
			}
		}
	}

	return nil
}

func generateSchedule(botIds []string, weeks int) [][]Matchup {
	n := len(botIds)

	if n%2 != 0 {
		botIds = append(botIds, "BYE")
		n++
	}

	schedule := make([][]Matchup, weeks)

	// fixed first bot
	for w := 0; w < weeks; w++ {
		var weekMatchups []Matchup
		for i := 0; i < n/2; i++ {
			home := botIds[i]
			away := botIds[n-1-i]

			if home != "BYE" && home != "BYE" {
				weekMatchups = append(weekMatchups, Matchup{
					Week:         w + 1,
					HomeBotID:    home,
					VisitorBotID: away,
					HomeScore:    0,
					VisitorScore: 0,
					WinningBotID: nil,
				})
			}
		}
		schedule[w] = weekMatchups

		botIds = append([]string{botIds[0]}, append([]string{botIds[n-1]}, botIds[1:n-1]...)...)
	}

	return schedule
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
	err = db.AutoMigrate(&Bot{}, &gameStatus{}, &LeagueSettings{}, &Player{})
	if err != nil {
		return nil, err
	}

	err = populateDatabase(db, bots, settings)
	if err != nil {
		return nil, err
	}

	return &GameStateHandler{db: db, dbSaveFilePath: saveFileName, cachedBotList: nil, cachedLeagueSettings: nil}, nil
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

	err = populateStatsTables(db, settings.Year)
	if err != nil {
		return err
	}

	return nil
}

func populateBotsTable(db *gorm.DB, bots []*common.Bot) error {
	for i, commonBot := range bots {
		dbBot := Bot{
			ID:                    commonBot.Id,
			Name:                  commonBot.FantasyTeamName,
			Owner:                 commonBot.Owner,
			CurrentWaiverPriority: 0, // Decided later
			RemainingWaiverBudget: 100,
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

	dbLeagueSettings := LeagueSettings{
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

func getStatsDatabaseFilePath(year uint32) (string, error) {
	statsFolderName := statsFolderRelativePath + "/" + strconv.Itoa(int(year))
	absPath, err := common.BuildLocalAbsolutePath(statsFolderName)
	if err != nil {
		return "", err
	}

	return absPath + "/" + statsDatabaseFileName, nil
}

func (handler *GameStateHandler) RefreshWeeklyStats() error {
	settings, err := handler.GetLeagueSettings()
	if err != nil {
		return err
	}

	// Get the data for this
	statsDBFile, err := getStatsDatabaseFilePath(uint32(settings.Year))
	if err != nil {
		return err
	}

	if !fileExists(statsDBFile) {
		return fmt.Errorf("stats.db file does not exist, it will need to be created using collect_stats.py and collect_weekly_stats.py")
	}

	// Remove the weekly stats table from the game state
	if err := handler.db.Exec(`DROP TABLE IF EXISTS weekly_stats;`).Error; err != nil {
		return err
	}

	// Attach the other DB
	attachQuery := fmt.Sprintf("ATTACH DATABASE '%s' AS other;", statsDBFile)
	println(attachQuery)
	if err := handler.db.Exec(attachQuery).Error; err != nil {
		return err
	}

	if err := handler.db.Exec(`CREATE TABLE weekly_stats AS SELECT * FROM other.weekly_stats;`).Error; err != nil {
		return err
	}

	if err := handler.db.Exec(`DETACH DATABASE other;`).Error; err != nil {
		return err
	}

	return nil
}

func populateStatsTables(db *gorm.DB, year uint32) error {
	// Get the data for this
	statsDBFile, err := getStatsDatabaseFilePath(year)
	if err != nil {
		return err
	}

	if !fileExists(statsDBFile) {
		return fmt.Errorf("stats.db file does not exist, it will need to be created using collect_stats.py and collect_weekly_stats.py")
	}

	// Attach the other DB
	attachQuery := fmt.Sprintf("ATTACH DATABASE '%s' AS other;", statsDBFile)
	if err := db.Exec(attachQuery).Error; err != nil {
		return err
	}

	if err := db.Exec(`CREATE TABLE season_stats AS SELECT * FROM other.season_stats;`).Error; err != nil {
		return err
	}

	if err := db.Exec(`CREATE TABLE preseason_projections AS SELECT * FROM other.preseason_projections;`).Error; err != nil {
		return err
	}

	if err := db.Exec(`DETACH DATABASE other;`).Error; err != nil {
		return err
	}

	return nil
}

func fileExists(path string) bool {
	_, err := os.Stat(path)
	if err == nil {
		return true // file exists
	}

	if os.IsNotExist(err) {
		return false // file does not exist
	}

	return false
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

	return fmt.Errorf("Draft file already exists, please delete before drafting again: %q", filePath)
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
type Bot struct {
	ID                    string `gorm:"primaryKey;column:id"`
	DraftOrder            int    `gorm:"column:draft_order"`
	Name                  string `gorm:"column:name"`
	Owner                 string `gorm:"column:owner"`
	CurrentWaiverPriority int    `gorm:"column:current_waiver_priority"`
	RemainingWaiverBudget int    `gorm:"column:remaining_waiver_budget"`

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
	CurrentBot *Bot `gorm:"foreignKey:CurrentBotID;references:ID"`
}

// --------------------
// Table: league_settings
// --------------------
type LeagueSettings struct {
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
	CurrentBot *Bot `gorm:"foreignKey:CurrentBotID;references:ID"`
}

func (p *Player) GetPositionSummary() (string, error) {
	var allowedPositions []string
	err := json.Unmarshal([]byte(p.AllowedPositions), &allowedPositions)
	if err != nil {
		return "", fmt.Errorf("failed to unmarshal allowed positions: %v", err)
	}

	return strings.Join(allowedPositions, ","), nil
}

// --------------------
// Table: matchups
// --------------------
type Matchup struct {
	ID           uint    `gorm:"primaryKey;column:id"`
	Week         int     `gorm:"column:week"`
	HomeBotID    string  `gorm:"column:home_bot_id"`
	VisitorBotID string  `gorm:"column:visitor_bot_id"`
	HomeScore    float64 `gorm:"column:home_score"`
	VisitorScore float64 `gorm:"column:visitor_score"`
	WinningBotID *string `gorm:"column:winning_bot_id"`

	HomeBot    Bot  `gorm:"foreignKey:HomeBotID;references:ID"`
	VisitorBot Bot  `gorm:"foreignKey:VisitorBotID;references:ID"`
	WinningBot *Bot `gorm:"foreignKey:WinningBotID;references:ID"`
}

// NOT A TABLE - just a query result
type PlayerWeeklyScore struct {
	ID               string  `gorm:"column:id"`
	FullName         string  `gorm:"column:full_name"`
	AllowedPositions string  `gorm:"column:allowed_positions"`
	FPTS             float64 `gorm:"column:FPTS"`
	CurrentBotID     string  `gorm:"column:current_bot_id"`
}

func (handler *GameStateHandler) GetPlayerScoresForCurrentWeek() ([]PlayerWeeklyScore, int, error) {
	var gameStatus gameStatus
	result := handler.db.First(&gameStatus, singleRowTableId)

	if result.Error != nil {
		return nil, -1, result.Error
	}

	var results []PlayerWeeklyScore

	err := handler.db.Raw(`
		SELECT 
			p.id, 
			p.full_name, 
			p.allowed_positions, 
			p.current_bot_id,
			max(w.FPTS) as FPTS
		FROM players AS p
		INNER JOIN weekly_stats AS w
			ON p.id = w.fantasypros_id
		WHERE w.week = ?
		AND p.current_bot_id IS NOT NULL
		GROUP BY 1, 2, 3, 4
		ORDER BY FPTS desc
	`, gameStatus.CurrentFantasyWeek).Scan(&results).Error

	if err != nil {
		return nil, -1, err
	}

	return results, gameStatus.CurrentFantasyWeek, nil
}
