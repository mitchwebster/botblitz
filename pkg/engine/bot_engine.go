package engine

import (
	"context"
	"errors"
	"fmt"
	"math/rand"
	"os"
	"strings"

	common "github.com/mitchwebster/botblitz/pkg/common"
	gamestate "github.com/mitchwebster/botblitz/pkg/gamestate"
	"golang.org/x/exp/slices"
)

const letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
const digits = "0123456789"

type BotEngineSettings struct {
	VerboseLoggingEnabled bool
	GameMode              GameMode
}

type BotContainerInfo struct {
	ContainerID string
	Port        string
}

type BotEngine struct {
	settings                    BotEngineSettings
	sourceCodeCache             map[string][]byte
	gameStateHandler            *gamestate.GameStateHandler
	sheetsClient                *SheetsClient
	weeklyFantasyTransactionLog strings.Builder
	dataBytes                   *DataBytes
	botContainers               map[string]*BotContainerInfo // map of bot ID to container info
}

func NewBotEngine(gameStateHandler *gamestate.GameStateHandler, settings BotEngineSettings, sheetsClient *SheetsClient, dataBytes *DataBytes) *BotEngine {
	var builder strings.Builder

	return &BotEngine{
		settings:                    settings,
		sourceCodeCache:             make(map[string][]byte),
		gameStateHandler:            gameStateHandler,
		sheetsClient:                sheetsClient,
		weeklyFantasyTransactionLog: builder,
		dataBytes:                   dataBytes,
		botContainers:               make(map[string]*BotContainerInfo),
	}
}

func (e *BotEngine) Summarize() string {
	var builder strings.Builder

	// write configs
	fmt.Fprintf(&builder, "Engine Summary\n\n")

	// Print settings
	fmt.Fprintf(&builder, "Settings:\n")
	fmt.Fprintf(&builder, "\tVerboseLoggingEnabled: %t\n", e.settings.VerboseLoggingEnabled)
	if e.sheetsClient == nil {
		fmt.Fprintf(&builder, "\tSheetsClient: Not Configured\n")
	} else {
		fmt.Fprintf(&builder, "\tSheetsClient: Configured!\n")
	}

	fmt.Fprintf(&builder, "\tGameMode: %s\n", e.settings.GameMode)

	return builder.String()
}

func (e *BotEngine) Run(ctx context.Context) error {
	err := e.performValidations()
	if err != nil {
		return err
	}

	return e.run(ctx)
}

func (e *BotEngine) performValidations() error {
	bots, err := e.gameStateHandler.GetBots()
	if err != nil {
		return err
	}

	botValidation := common.ValidateBotConfigs(bots)
	if !botValidation {
		return errors.New("Bot validation failed, please check provided bots")
	}

	if e.settings.GameMode == Draft {
		draftValidation := e.validateDraftState()
		if draftValidation != nil {
			return draftValidation
		}
	}

	return nil
}

func (e *BotEngine) run(ctx context.Context) error {
	if e.settings.VerboseLoggingEnabled {
		fmt.Println("Running engine")
	}

	err := e.collectBotResources()
	if err != nil {
		return err
	}

	err = e.initializeBots()
	if err != nil {
		return err
	}

	if e.settings.GameMode == Draft {
		return e.runDraft(ctx)
	}

	return e.runWeeklyFantasy(ctx)
}

func (e *BotEngine) collectBotResources() error {
	folderPath, err := common.BuildLocalAbsolutePath(botResourceFolderName)
	if err != nil {
		return err
	}

	// Clear temp folder
	err = os.RemoveAll(folderPath)
	if err != nil {
		// Non-existence errors are ok
		if !os.IsNotExist(err) {
			return err
		}
	}

	err = os.Mkdir(folderPath, os.ModePerm)
	if err != nil {
		return err
	}

	// TODO: put any resources we want to expose to the bot in this directory

	return nil
}

func (e *BotEngine) initializeBots() error {
	fmt.Printf("\n-----------------------------------------\n")
	fmt.Println("Initializing Bots")

	bots, err := e.gameStateHandler.GetBots()
	if err != nil {
		return err
	}

	for _, bot := range bots {
		byteCode, err := e.fetchSourceCode(bot)
		if err != nil {
			fmt.Printf("Failed to retrieve bot source code for (%s)\n", bot.Id)
			return err
		}

		e.sourceCodeCache[bot.Id] = byteCode
	}

	fmt.Printf("\n-----------------------------------------\n")

	return nil
}

func (e *BotEngine) fetchSourceCode(bot *common.Bot) ([]byte, error) {
	var botCode []byte

	if bot.SourceType == common.Bot_REMOTE {
		downloadedSourceCode, err := DownloadGithubSourceCode(bot.SourceRepoUsername, bot.SourceRepoName, bot.SourcePath, e.settings.VerboseLoggingEnabled)
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

func GenerateRandomString(length int) string {
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

func FindCurrentBotById(botId string, gameState *common.GameState) (*common.Bot, error) {
	teamIdx := slices.IndexFunc(gameState.Bots, func(t *common.Bot) bool { return t.Id == botId })
	if teamIdx < 0 {
		return nil, fmt.Errorf("Could not find team...concerning...")
	}

	return gameState.Bots[teamIdx], nil
}

func FindPlayerById(playerId string, gameState *common.GameState) (*common.Player, error) {
	if len(playerId) <= 0 {
		return nil, fmt.Errorf("Cannot find empty player id")
	}

	idx := slices.IndexFunc(gameState.Players, func(p *common.Player) bool { return p.Id == playerId })
	if idx < 0 {
		return nil, fmt.Errorf("Could not find player with selected id")
	}

	return gameState.Players[idx], nil
}
