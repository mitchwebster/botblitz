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
	// InlineExecution tells the container's gRPC server to call the bot's functions
	// directly (in-process) rather than forking a fresh Python subprocess per call.
	// Eliminates the ~700-1000ms cold-start overhead on every draft pick and waiver RPC.
	// Only safe for evaluation: a crashing bot will kill the container's gRPC server,
	// which is acceptable when the run is already invalid. Never set in production.
	InlineExecution bool
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

// UseSharedContainers makes this engine reuse an externally-owned container map
// instead of its own freshly-allocated one. Only the evaluator (simulation) uses this,
// and only behind its opt-in --optimize-by-reusing-containers-wont-match-prod flag, to
// keep a single set of bot containers alive across the draft and season phases and across
// independent simulation runs — the bot source is identical throughout, so the per-phase,
// per-run teardown/rebuild is pure overhead. The production engine never calls this and
// keeps its own per-instance map, so its container lifecycle is unchanged.
func (e *BotEngine) UseSharedContainers(containers map[string]*BotContainerInfo) {
	e.botContainers = containers
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

	if e.settings.GameMode == PerformWeeklyFantasyActions {
		return e.performWeeklyFantasyActions(ctx)
	}

	if e.settings.GameMode == UpdateWeeklyScores {
		return e.updateWeeklyScores(ctx, false)
	}

	if e.settings.GameMode == FinishPreviousWeek {
		return e.updateWeeklyScores(ctx, true)
	}

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
