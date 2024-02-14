package engine

import (
	"fmt"
	"strings"

	common "github.com/mitchwebster/botblitz/pkg/common"
)

type BotEngineSettings struct {
	VerboseLoggingEnabled bool
	IsInteractiveMode     bool
}

type BotEngine struct {
	settings BotEngineSettings
	bots     []*common.Bot
}

func NewBotEngine(settings BotEngineSettings, bots []*common.Bot) *BotEngine {
	return &BotEngine{
		settings: settings,
		bots:     bots,
	}
}

func (e BotEngine) Summarize() string {
	var builder strings.Builder

	// write configs
	fmt.Fprintf(&builder, "Engine Summary\n\n")

	// Print settings
	fmt.Fprintf(&builder, "Settings:\n")
	fmt.Fprintf(&builder, "\tIsInteractiveMode: %t\n", e.settings.IsInteractiveMode)
	fmt.Fprintf(&builder, "\tVerboseLoggingEnabled: %t\n", e.settings.VerboseLoggingEnabled)

	fmt.Fprintf(&builder, "Bots:\n")
	fmt.Fprintf(&builder, "\tCount: %d\n", len(e.bots))

	return builder.String()
}

func (e BotEngine) Run() {
	if e.settings.IsInteractiveMode {
		runInteractively(e)
	} else {
		runAutomated(e)
	}
}

func runInteractively(e BotEngine) {
	if e.settings.VerboseLoggingEnabled {
		fmt.Println("Running interactively")
	}

	fmt.Println("Instructions: ")
	fmt.Println("	'exit' : close engine ")
	fmt.Println("	'clear' : remove current settings ")

	nextPrompt := "Awaiting input:"

	for {
		fmt.Println(nextPrompt)

		// Read user input
		var input string
		fmt.Scanln(&input)
		input = strings.ToLower(input)

		// Check if the user wants to exit
		if input == "exit" {
			fmt.Println("Shutting down engine...")
			break // Exit the loop
		}

		// Process the user input
		nextPrompt = processInteractiveCommand(input)
	}
}

func processInteractiveCommand(command string) string {
	// TODO: add processs logic
	return "Enter another command"
}

func runAutomated(e BotEngine) {
	if e.settings.VerboseLoggingEnabled {
		fmt.Println("Running automated")
	}

	for _, obj := range e.bots {
		runBot(obj, e.settings)
	}
}

func runBot(bot *common.Bot, settings BotEngineSettings) error {
	fmt.Printf("Running bot -- Id: %s, Username: %s, Repo: %s, Fantasy Team Id: %d\n", bot.Id, bot.SourceRepoUsername, bot.SourceRepoName, bot.FantasyTeamId)

	err := DownloadGithubSourceCode(bot.SourceRepoUsername, bot.SourceRepoName, settings.VerboseLoggingEnabled)
	if err != nil {
		fmt.Println("Failed to retrieve agent code")
		fmt.Println(err)
		return err
	}

	return nil
}
