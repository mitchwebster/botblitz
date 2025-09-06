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
	settings         BotEngineSettings
	sourceCodeCache  map[string][]byte
	envVarsCache     map[string][]string
	gameStateHandler *gamestate.GameStateHandler
	sheetsClient     *SheetsClient
	botContainers    map[string]*BotContainerInfo // map of bot ID to container info
}

func NewBotEngine(gameStateHandler *gamestate.GameStateHandler, settings BotEngineSettings, sheetsClient *SheetsClient, sourceCodeCache map[string][]byte, envVarsCache map[string][]string) *BotEngine {

	return &BotEngine{
		settings:         settings,
		sourceCodeCache:  sourceCodeCache,
		envVarsCache:     envVarsCache,
		gameStateHandler: gameStateHandler,
		sheetsClient:     sheetsClient,
		botContainers:    make(map[string]*BotContainerInfo),
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

	botValidation := validateBotConfigs(bots)
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

	if e.settings.GameMode == Draft {
		return e.runDraft(ctx)
	}

	if e.settings.GameMode == PerformAddDrop {
		return e.performFAABAddDrop(ctx)
	}

	// Add scoring methods

	return errors.New("Unknown game mode")
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

func validateBotConfigs(bots []gamestate.Bot) bool {
	uniquenessMap := make(map[string]bool)

	for _, bot := range bots {
		if bot.ID == "" {
			return false
		}

		_, exists := uniquenessMap[bot.ID]
		if exists {
			return false
		}

		uniquenessMap[bot.ID] = true
	}

	return true
}
