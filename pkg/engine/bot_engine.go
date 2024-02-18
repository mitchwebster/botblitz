package engine

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

const pyServerHostAndPort = "localhost:8080"
const pyServerPath = "./py-grpc-server/server.py"   // py gRPC server code
const pyServerBotFilePath = "py-grpc-server/bot.py" // source code filepath

type BotEngineSettings struct {
	VerboseLoggingEnabled bool
	NumSimulations        int
}

type BotEngine struct {
	settings  BotEngineSettings
	bots      []*common.Bot
	landscape *common.FantasyLandscape
}

func NewBotEngine(settings BotEngineSettings, bots []*common.Bot, landscape *common.FantasyLandscape) *BotEngine {
	return &BotEngine{
		settings:  settings,
		bots:      bots,
		landscape: landscape,
	}
}

func (e BotEngine) Summarize() string {
	var builder strings.Builder

	// write configs
	fmt.Fprintf(&builder, "Engine Summary\n\n")

	// Print settings
	fmt.Fprintf(&builder, "Settings:\n")
	fmt.Fprintf(&builder, "\tNumSimulations: %d\n", e.settings.NumSimulations)
	fmt.Fprintf(&builder, "\tVerboseLoggingEnabled: %t\n", e.settings.VerboseLoggingEnabled)

	fmt.Fprintf(&builder, "\nBots:\n")
	fmt.Fprintf(&builder, "\tCount: %d\n", len(e.bots))
	for _, obj := range e.bots {
		fmt.Fprintf(&builder, "\t - %s\n", obj.Id)
	}

	fmt.Fprintf(&builder, "\nLandscape:\n")
	fmt.Fprintf(&builder, "\tPlayer Count: %d\n", len(e.landscape.Players))

	return builder.String()
}

func (e BotEngine) Run() error {
	return runAutomated(e)
}

func runAutomated(e BotEngine) error {
	if e.settings.VerboseLoggingEnabled {
		fmt.Println("Running automated")
	}

	// Initialize results
	results := make(map[string][]*common.FantasySelections)
	for _, obj := range e.bots {
		results[obj.Id] = []*common.FantasySelections{}
	}

	// Run bots
	for iteration := 1; iteration <= e.settings.NumSimulations; iteration++ {
		for _, obj := range e.bots {
			fmt.Printf("\n-----------------------------------------\n")
			fmt.Printf("[%s] - simulation: %d\n", obj.Id, iteration)
			fmt.Printf("Bot details: Username: %s, Repo: %s, Fantasy Team Id: %d\n", obj.SourceRepoUsername, obj.SourceRepoName, obj.FantasyTeamId)
			fmt.Printf("Using a %s source to find %s\n", obj.SourceType, obj.SourcePath)

			selections, err := runBot(obj, e.landscape, e.settings.VerboseLoggingEnabled)
			if err != nil {
				if e.settings.VerboseLoggingEnabled {
					fmt.Println(err)
				}

				fmt.Printf("Failed to run bot %s\n", obj.Id)
				results[obj.Id] = append(results[obj.Id], nil)
			} else {
				fmt.Printf("Ran bot %s successfully!\n", obj.Id)
				results[obj.Id] = append(results[obj.Id], selections)
			}

			// if we fail to clean after a bot run, crash the engine!
			// for correctness / security reasons, we do not want to continue
			err = cleanAfterRun()
			if err != nil {
				fmt.Println("CRITICAL!! Failed to clean after bot run")
				return err
			}

			fmt.Printf("\n-----------------------------------------\n") // Add formatting to make separate runs clear
		}
	}

	for k, v := range results {
		fmt.Printf("%s: %v\n", k, v)
	}

	return nil
}

func runBot(bot *common.Bot, landscape *common.FantasyLandscape, verboseLoggingEnabled bool) (*common.FantasySelections, error) {
	botCode, err := fetchSourceCode(bot, verboseLoggingEnabled)
	if err != nil {
		return nil, err
	}

	cmd, stdout, stderr, err := startServerForBot(botCode)
	if err != nil {
		fmt.Printf("Server output logs: %s\n", stdout)
		fmt.Printf("Server error logs: %s\n", stderr)
		return nil, err
	}

	fmt.Println("Making gRPC call")
	selections, err := callBotRPC(landscape)
	if err != nil {
		fmt.Println("Failed to make gRPC call")
		fmt.Println(err)
		fmt.Printf("Server output logs: %s\n", stdout)
		fmt.Printf("Server error logs: %s\n", stderr)
	}

	fmt.Println("Shutting down gRPC server")
	err = cmd.Process.Kill()
	if err != nil {
		return nil, err
	}

	fmt.Println("Shut down gRPC server")

	return selections, nil
}

func fetchSourceCode(bot *common.Bot, verboseLoggingEnabled bool) ([]byte, error) {
	var botCode []byte

	if bot.SourceType == common.Bot_REMOTE {
		downloadedSourceCode, err := DownloadGithubSourceCode(bot.SourceRepoUsername, bot.SourceRepoName, bot.SourcePath, verboseLoggingEnabled)
		if err != nil {
			return nil, err
		}

		botCode = downloadedSourceCode
	} else {
		absPath, err := buildLocalAbsolutePath(bot.SourcePath)
		if err != nil {
			return nil, err
		}

		bytes, err := os.ReadFile(absPath)
		if err != nil {
			return nil, err
		}

		botCode = bytes
	}

	fmt.Println("Successfully retrieved source code")
	return botCode, nil
}

func startServerForBot(botCode []byte) (*exec.Cmd, *bytes.Buffer, *bytes.Buffer, error) {

	fmt.Println("Creating source code file")
	absPath, err := buildLocalAbsolutePath(pyServerBotFilePath)
	if err != nil {
		return nil, bytes.NewBuffer([]byte{}), bytes.NewBuffer([]byte{}), err
	}

	err = os.WriteFile(absPath, botCode, 0755)
	if err != nil {
		return nil, bytes.NewBuffer([]byte{}), bytes.NewBuffer([]byte{}), err
	}

	cmd := exec.Command("python", pyServerPath)
	var outb, errb bytes.Buffer
	cmd.Stdout = &outb
	cmd.Stderr = &errb

	err = cmd.Start()
	if err != nil {
		return nil, bytes.NewBuffer([]byte{}), bytes.NewBuffer([]byte{}), err
	}

	time.Sleep(2 * time.Second) // Allow 2s for gRPC server startup

	return cmd, &outb, &errb, nil
}

func buildLocalAbsolutePath(relativePath string) (string, error) {
	directory, err := os.Getwd()
	if err != nil {
		return "", err
	}

	var trimmedPath = strings.Trim(relativePath, "/")
	return fmt.Sprintf("%s/%s", directory, trimmedPath), nil
}

func cleanAfterRun() error {
	absPath, err := buildLocalAbsolutePath(pyServerBotFilePath)
	if err != nil {
		return err
	}

	err = os.Remove(absPath)
	if err != nil {
		// Non-existence errors are ok
		if !os.IsNotExist(err) {
			return err
		}
	}

	return nil
}

func callBotRPC(landscape *common.FantasyLandscape) (*common.FantasySelections, error) {
	var opts []grpc.DialOption
	opts = append(opts, grpc.WithTransportCredentials(insecure.NewCredentials()))

	conn, err := grpc.Dial(pyServerHostAndPort, opts...)
	if err != nil {
		return nil, err
	}

	defer conn.Close()
	client := common.NewAgentServiceClient(conn)

	selections, err := client.PerformFantasyActions(context.Background(), landscape)
	if err != nil {
		return nil, err
	}

	return selections, nil
}
