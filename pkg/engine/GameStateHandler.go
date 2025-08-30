package engine

import (
	"os"
	"strconv"
	"time"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

const saveFolderRelativePath = "data/game_states"
const filePrefix = "gameState-"
const fileSuffix = ".db"

// GameStateDatabase encapsulates the SQLite database operations
type GameStateHandler struct {
	db *gorm.DB
}

func CheckDatabase() {
	// Open database
	db, err := gorm.Open(sqlite.Open("draft.db"), &gorm.Config{})
	if err != nil {
		panic(err)
	}

	// Auto migrate: creates "users" table if it doesn’t exist
	// db.AutoMigrate(&User{})

	// Insert
	// db.Create(&User{Name: "Alice", Email: "alice@example.com"})

	// Query one
	var user player
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

// UpdatePlayer updates multiple player fields at once
func (db *GameStateHandler) UpdatePlayer(playerID string, availability *string, pickChosen *int, botID *string) error {
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

	return db.db.Model(&player{}).Where("id = ?", playerID).Updates(updates).Error
}

func NewGameStateHandler(year int, bots []*common.Bot) (*GameStateHandler, error) {
	db, err := gorm.Open(sqlite.Open(dbPath), &gorm.Config{})
	if err != nil {
		return nil, err
	}

	// Auto migrate the database tables
	err = db.AutoMigrate(&bot{}, &gameStatus{}, &leagueSettings{}, &player{})
	if err != nil {
		return nil, err
	}

	return &GameStateHandler{db: db}, nil
}

func getSaveFileName(year uint32) (string, error) {
	absFolderPath, err := getSaveFolderPath(year)
	if err != nil {
		return "", err
	}

	err = os.MkdirAll(absFolderPath, os.ModePerm)
	if err != nil {
		return "", err
	}

	timestamp := time.Now().Unix()
	timestampStr := strconv.FormatInt(timestamp, 10)
	fileName := filePrefix + timestampStr + fileSuffix
	return absFolderPath + "/" + fileName, nil
}

func getSaveFolderPath(year uint32) (string, error) {
	folderName := saveFolderRelativePath + "/" + strconv.Itoa(int(year))
	absPath, err := BuildLocalAbsolutePath(folderName)
	if err != nil {
		return "", err
	}

	return absPath, nil
}

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
	Players      []player     `gorm:"foreignKey:CurrentBotID"`
}

// --------------------
// Table: game_status
// --------------------
type gameStatus struct {
	ID                 int     `gorm:"primaryKey;column:id"`
	CurrentBotID       *string `gorm:"column:current_bot_id"`
	CurrentDraftPick   *int    `gorm:"column:current_draft_pick"`
	CurrentFantasyWeek *int    `gorm:"column:current_fantasy_week"`

	// Relation
	CurrentBot *bot `gorm:"foreignKey:CurrentBotID;references:ID"`
}

// --------------------
// Table: league_settings
// --------------------
type leagueSettings struct {
	ID                 int     `gorm:"primaryKey;column:id"`
	Year               int     `gorm:"column:year"`
	PlayerSlots        string  `gorm:"type:json;column:player_slots"` // stored as JSON string
	IsSnakeDraft       bool    `gorm:"column:is_snake_draft"`
	TotalRounds        int     `gorm:"column:total_rounds"`
	PointsPerReception float64 `gorm:"column:points_per_reception"`
}

// --------------------
// Table: players
// --------------------
type player struct {
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

// func genDraftGameState(year int, bots []*common.Bot) (*common.GameState, error) {
// 	players, err := loadPlayers(year)
// 	if err != nil {
// 		return nil, err
// 	}

// 	player_slots := fetchPlayerSlots()

// 	settings := common.LeagueSettings{
// 		NumTeams:           uint32(len(bots)),
// 		IsSnakeDraft:       true,
// 		TotalRounds:        uint32(len(player_slots)),
// 		PointsPerReception: 1.0,
// 		Year:               uint32(year),
// 		SlotsPerTeam:       player_slots,
// 	}

// 	game_state := common.GameState{
// 		CurrentDraftPick:   1,
// 		CurrentBotTeamId:   "0",
// 		LeagueSettings:     &settings,
// 		Bots:               bots,
// 		Players:            players,
// 		CurrentFantasyWeek: 2, // Simulate week 2 (this has a bug kind of, in reality you won't see actual performance, so just need to ignore that you're getting that)
// 	}
// 	return &game_state, nil
// }
