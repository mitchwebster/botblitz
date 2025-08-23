package engine

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"time"

	common "github.com/mitchwebster/botblitz/pkg/common"
	_ "github.com/mattn/go-sqlite3"
)

const sqliteSaveFolderRelativePath = "data/game_states"
const sqliteFilePrefix = "gameState-"
const sqliteFileSuffix = ".db"
const allowedNumSqliteSaveFiles = 3

type SQLiteGameStateHandler struct {
	dbPath string
	db     *sql.DB
}

func NewSQLiteGameStateHandler(year uint32) (*SQLiteGameStateHandler, error) {
	dbPath, err := getSQLiteFileName(year)
	if err != nil {
		return nil, err
	}

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		return nil, err
	}

	handler := &SQLiteGameStateHandler{
		dbPath: dbPath,
		db:     db,
	}

	err = handler.initializeSchema()
	if err != nil {
		db.Close()
		return nil, err
	}

	return handler, nil
}

func (h *SQLiteGameStateHandler) Close() error {
	if h.db != nil {
		return h.db.Close()
	}
	return nil
}

func (h *SQLiteGameStateHandler) GetDbPath() string {
	return h.dbPath
}

func (h *SQLiteGameStateHandler) initializeSchema() error {
	schema := `
	CREATE TABLE IF NOT EXISTS game_state (
		id INTEGER PRIMARY KEY,
		current_draft_pick INTEGER NOT NULL,
		current_bot_team_id TEXT NOT NULL,
		current_fantasy_week INTEGER NOT NULL
	);

	CREATE TABLE IF NOT EXISTS league_settings (
		id INTEGER PRIMARY KEY,
		num_teams INTEGER NOT NULL,
		is_snake_draft BOOLEAN NOT NULL,
		total_rounds INTEGER NOT NULL,
		points_per_reception REAL NOT NULL,
		year INTEGER NOT NULL
	);

	CREATE TABLE IF NOT EXISTS player_slots (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		name TEXT,
		allowed_positions TEXT NOT NULL, -- JSON array
		assigned_player_id TEXT,
		allows_any_position BOOLEAN NOT NULL
	);

	CREATE TABLE IF NOT EXISTS fantasy_teams (
		id TEXT PRIMARY KEY,
		name TEXT NOT NULL,
		owner TEXT NOT NULL,
		current_waiver_priority INTEGER NOT NULL
	);

	CREATE TABLE IF NOT EXISTS players (
		id TEXT PRIMARY KEY,
		full_name TEXT NOT NULL,
		allowed_positions TEXT NOT NULL, -- JSON array
		professional_team TEXT NOT NULL,
		player_bye_week INTEGER NOT NULL,
		rank INTEGER NOT NULL,
		tier INTEGER NOT NULL,
		position_rank INTEGER NOT NULL,
		position_tier INTEGER NOT NULL,
		gsis_id TEXT NOT NULL,
		availability INTEGER NOT NULL, -- 0=AVAILABLE, 1=DRAFTED, 2=ON_HOLD
		pick_chosen INTEGER NOT NULL,
		current_fantasy_team_id TEXT
	);
	`

	_, err := h.db.Exec(schema)
	return err
}

func (h *SQLiteGameStateHandler) SaveGameState(gameState *common.GameState) error {
	tx, err := h.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	// Clear existing data
	_, err = tx.Exec("DELETE FROM game_state")
	if err != nil {
		return err
	}
	_, err = tx.Exec("DELETE FROM league_settings")
	if err != nil {
		return err
	}
	_, err = tx.Exec("DELETE FROM player_slots")
	if err != nil {
		return err
	}
	_, err = tx.Exec("DELETE FROM fantasy_teams")
	if err != nil {
		return err
	}
	_, err = tx.Exec("DELETE FROM players")
	if err != nil {
		return err
	}

	// Insert game state
	_, err = tx.Exec(`
		INSERT INTO game_state (id, current_draft_pick, current_bot_team_id, current_fantasy_week)
		VALUES (1, ?, ?, ?)
	`, gameState.CurrentDraftPick, gameState.CurrentBotTeamId, gameState.CurrentFantasyWeek)
	if err != nil {
		return err
	}

	// Insert league settings
	settings := gameState.LeagueSettings
	_, err = tx.Exec(`
		INSERT INTO league_settings (id, num_teams, is_snake_draft, total_rounds, points_per_reception, year)
		VALUES (1, ?, ?, ?, ?, ?)
	`, settings.NumTeams, settings.IsSnakeDraft, settings.TotalRounds, settings.PointsPerReception, settings.Year)
	if err != nil {
		return err
	}

	// Insert player slots
	for _, slot := range settings.SlotsPerTeam {
		allowedPositionsJson, err := json.Marshal(slot.AllowedPlayerPositions)
		if err != nil {
			return err
		}
		_, err = tx.Exec(`
			INSERT INTO player_slots (name, allowed_positions, assigned_player_id, allows_any_position)
			VALUES (?, ?, ?, ?)
		`, slot.Name, string(allowedPositionsJson), slot.AssignedPlayerId, slot.AllowsAnyPosition)
		if err != nil {
			return err
		}
	}

	// Insert fantasy teams
	for _, team := range gameState.Teams {
		_, err = tx.Exec(`
			INSERT INTO fantasy_teams (id, name, owner, current_waiver_priority)
			VALUES (?, ?, ?, ?)
		`, team.Id, team.Name, team.Owner, team.CurrentWaiverPriority)
		if err != nil {
			return err
		}
	}

	// Insert players
	for _, player := range gameState.Players {
		allowedPositionsJson, err := json.Marshal(player.AllowedPositions)
		if err != nil {
			return err
		}
		_, err = tx.Exec(`
			INSERT INTO players (id, full_name, allowed_positions, professional_team, player_bye_week, 
				rank, tier, position_rank, position_tier, gsis_id, availability, pick_chosen, current_fantasy_team_id)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		`, player.Id, player.FullName, string(allowedPositionsJson), player.ProfessionalTeam, player.PlayerByeWeek,
			player.Rank, player.Tier, player.PositionRank, player.PositionTier, player.GsisId,
			int(player.Status.Availability), player.Status.PickChosen, player.Status.CurrentFantasyTeamId)
		if err != nil {
			return err
		}
	}

	return tx.Commit()
}

func LoadLastGameStateFromSQLite(year uint32) (*common.GameState, string, error) {
	dbPath, err := findLastSQLiteFilePath(year)
	if err != nil {
		return nil, "", err
	}

	handler, err := LoadSQLiteGameStateHandler(dbPath)
	if err != nil {
		return nil, "", err
	}
	defer handler.Close()

	gameState, err := handler.LoadGameState()
	if err != nil {
		return nil, "", err
	}

	return gameState, dbPath, nil
}

func LoadSQLiteGameStateHandler(dbPath string) (*SQLiteGameStateHandler, error) {
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		return nil, err
	}

	return &SQLiteGameStateHandler{
		dbPath: dbPath,
		db:     db,
	}, nil
}

func (h *SQLiteGameStateHandler) LoadGameState() (*common.GameState, error) {
	gameState := &common.GameState{}

	// Load game state
	row := h.db.QueryRow("SELECT current_draft_pick, current_bot_team_id, current_fantasy_week FROM game_state WHERE id = 1")
	err := row.Scan(&gameState.CurrentDraftPick, &gameState.CurrentBotTeamId, &gameState.CurrentFantasyWeek)
	if err != nil {
		return nil, err
	}

	// Load league settings
	settings := &common.LeagueSettings{}
	row = h.db.QueryRow("SELECT num_teams, is_snake_draft, total_rounds, points_per_reception, year FROM league_settings WHERE id = 1")
	err = row.Scan(&settings.NumTeams, &settings.IsSnakeDraft, &settings.TotalRounds, &settings.PointsPerReception, &settings.Year)
	if err != nil {
		return nil, err
	}

	// Load player slots
	rows, err := h.db.Query("SELECT name, allowed_positions, assigned_player_id, allows_any_position FROM player_slots ORDER BY id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var slots []*common.PlayerSlot
	for rows.Next() {
		slot := &common.PlayerSlot{}
		var allowedPositionsJson string
		err = rows.Scan(&slot.Name, &allowedPositionsJson, &slot.AssignedPlayerId, &slot.AllowsAnyPosition)
		if err != nil {
			return nil, err
		}
		err = json.Unmarshal([]byte(allowedPositionsJson), &slot.AllowedPlayerPositions)
		if err != nil {
			return nil, err
		}
		slots = append(slots, slot)
	}
	settings.SlotsPerTeam = slots

	gameState.LeagueSettings = settings

	// Load fantasy teams
	rows, err = h.db.Query("SELECT id, name, owner, current_waiver_priority FROM fantasy_teams")
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var teams []*common.FantasyTeam
	for rows.Next() {
		team := &common.FantasyTeam{}
		err = rows.Scan(&team.Id, &team.Name, &team.Owner, &team.CurrentWaiverPriority)
		if err != nil {
			return nil, err
		}
		teams = append(teams, team)
	}
	gameState.Teams = teams

	// Load players
	rows, err = h.db.Query(`
		SELECT id, full_name, allowed_positions, professional_team, player_bye_week, 
			rank, tier, position_rank, position_tier, gsis_id, availability, pick_chosen, current_fantasy_team_id
		FROM players
	`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var players []*common.Player
	for rows.Next() {
		player := &common.Player{
			Status: &common.PlayerStatus{},
		}
		var allowedPositionsJson string
		var availability int
		err = rows.Scan(&player.Id, &player.FullName, &allowedPositionsJson, &player.ProfessionalTeam, &player.PlayerByeWeek,
			&player.Rank, &player.Tier, &player.PositionRank, &player.PositionTier, &player.GsisId,
			&availability, &player.Status.PickChosen, &player.Status.CurrentFantasyTeamId)
		if err != nil {
			return nil, err
		}
		err = json.Unmarshal([]byte(allowedPositionsJson), &player.AllowedPositions)
		if err != nil {
			return nil, err
		}
		player.Status.Availability = common.PlayerStatus_Availability(availability)
		players = append(players, player)
	}
	gameState.Players = players

	return gameState, nil
}

func getSQLiteFileName(year uint32) (string, error) {
	absFolderPath, err := getSQLiteFolderPath(year)
	if err != nil {
		return "", err
	}

	err = os.MkdirAll(absFolderPath, os.ModePerm)
	if err != nil {
		return "", err
	}

	timestamp := time.Now().Unix()
	timestampStr := strconv.FormatInt(timestamp, 10)
	fileName := sqliteFilePrefix + timestampStr + sqliteFileSuffix
	return filepath.Join(absFolderPath, fileName), nil
}

func getSQLiteFolderPath(year uint32) (string, error) {
	folderName := sqliteSaveFolderRelativePath + "/" + strconv.Itoa(int(year))
	absPath, err := BuildLocalAbsolutePath(folderName)
	if err != nil {
		return "", err
	}
	return absPath, nil
}

func findLastSQLiteFilePath(year uint32) (string, error) {
	saveFiles, err := findAllSQLiteFiles(year)
	if err != nil {
		return "", err
	}

	if len(saveFiles) == 0 {
		return "", fmt.Errorf("Found no SQLite database files")
	}

	return saveFiles[len(saveFiles)-1], nil
}

func findAllSQLiteFiles(year uint32) ([]string, error) {
	absPath, err := getSQLiteFolderPath(year)
	if err != nil {
		return nil, err
	}

	entries, err := os.ReadDir(absPath)
	if err != nil {
		return nil, err
	}

	var dbFiles []string
	for _, entry := range entries {
		name := entry.Name()
		if !entry.IsDir() && filepath.Ext(name) == sqliteFileSuffix && 
		   len(name) > len(sqliteFilePrefix) && name[:len(sqliteFilePrefix)] == sqliteFilePrefix {
			dbFiles = append(dbFiles, filepath.Join(absPath, name))
		}
	}

	return dbFiles, nil
}

func CleanOldSQLiteGameStates(year uint32) error {
	paths, err := findAllSQLiteFiles(year)
	if err != nil {
		return err
	}

	if len(paths) <= allowedNumSqliteSaveFiles {
		fmt.Println("Nothing to clean!")
		return nil
	}

	numFilesToRemove := len(paths) - allowedNumSqliteSaveFiles
	for i := 0; i < numFilesToRemove; i++ {
		err := os.Remove(paths[i])
		if err != nil {
			fmt.Println("Error removing file:", err)
			return err
		}
	}

	return nil
}