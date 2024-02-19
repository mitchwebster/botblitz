package engine

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/docker/docker/api/types/container"
	"github.com/docker/docker/api/types/mount"
	"github.com/docker/docker/client"
	"github.com/docker/go-connections/nat"
	common "github.com/mitchwebster/botblitz/pkg/common"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

const pyServerHostAndPort = "localhost:8080"
const pyServerPath = "./py-grpc-server/server.py"   // py gRPC server code
const pyServerBotFilePath = "py-grpc-server/bot.py" // source code filepath

type BotEngineSettings struct {
	VerboseLoggingEnabled bool
}

type BotEngine struct {
	botResults      map[string]map[string][]*common.FantasySelections // Bot -> Simulation -> Simulation Selections per iteration
	settings        BotEngineSettings
	bots            []*common.Bot
	simulations     []*common.Simulation
	sourceCodeCache map[string][]byte
}

func NewBotEngine(simulations []*common.Simulation, bots []*common.Bot, settings BotEngineSettings) *BotEngine {
	return &BotEngine{
		settings:        settings,
		bots:            bots,
		simulations:     simulations,
		botResults:      make(map[string]map[string][]*common.FantasySelections),
		sourceCodeCache: make(map[string][]byte),
	}
}

func (e BotEngine) Summarize() string {
	var builder strings.Builder

	// write configs
	fmt.Fprintf(&builder, "Engine Summary\n\n")

	// Print settings
	fmt.Fprintf(&builder, "Settings:\n")
	fmt.Fprintf(&builder, "\tNumSimulations: %d\n", len(e.simulations))
	fmt.Fprintf(&builder, "\tVerboseLoggingEnabled: %t\n", e.settings.VerboseLoggingEnabled)

	fmt.Fprintf(&builder, "\nBots:\n")
	fmt.Fprintf(&builder, "\tCount: %d\n", len(e.bots))
	for _, obj := range e.bots {
		fmt.Fprintf(&builder, "\t - %s\n", obj.Id)
	}

	return builder.String()
}

func (e BotEngine) Run() error {
	err := performValidations(e)
	if err != nil {
		return err
	}

	val, err := CreateNewContainer(e.simulations[0].Landscape)
	fmt.Println(val)

	return nil
	// return run(e)
}

func (e BotEngine) PrintResults() {
	for botId, simulationMap := range e.botResults {
		fmt.Printf("%s:\n", botId)
		for simulationId, results := range simulationMap {
			fmt.Printf("\t%s: %s\n", simulationId, results)
		}
	}
}

func performValidations(e BotEngine) error {
	botValidation := common.ValidateBotConfigs(e.bots)
	if !botValidation {
		return errors.New("Bot validation failed, please check provided bots")
	}

	simulationValidation := common.ValidateSimulation(e.simulations)
	if !simulationValidation {
		return errors.New("Simulation validation failed, please check provided simulations")
	}

	return nil
}

func run(e BotEngine) error {
	if e.settings.VerboseLoggingEnabled {
		fmt.Println("Running engine")
	}

	err := initializeBots(e)
	if err != nil {
		return err
	}

	for _, bot := range e.bots {
		cmd, stdout, stderr, err := startServerForBot(bot, e)
		if err != nil {
			fmt.Printf("Server output logs: %s\n", stdout)
			fmt.Printf("Server error logs: %s\n", stderr)
			return err
		}

		fmt.Printf("Setup bot: %s\n", bot.Id)
		fmt.Printf("Bot details: Username: %s, Repo: %s, Fantasy Team Id: %d\n", bot.SourceRepoUsername, bot.SourceRepoName, bot.FantasyTeamId)
		fmt.Printf("Using a %s source to find %s\n", bot.SourceType, bot.SourcePath)

		err = runSimulationsOnBot(bot, e)
		if err != nil {
			fmt.Println("Failed to run simulations on bot")
			fmt.Println(err)
		}

		err = shutDownAndCleanBotServer(bot, cmd)
		if err != nil {
			fmt.Println("CRITICAL!! Failed to clean after bot run")
			return err
		}
	}

	return nil
}

func initializeBots(e BotEngine) error {
	fmt.Printf("\n-----------------------------------------\n")
	fmt.Println("Initializing Bots")

	for _, bot := range e.bots {
		e.botResults[bot.Id] = make(map[string][]*common.FantasySelections)
		byteCode, err := fetchSourceCode(bot, e)
		if err != nil {
			fmt.Printf("Failed to retrieve bot source code for (%s)\n", bot.Id)
			return err
		}

		e.sourceCodeCache[bot.Id] = byteCode
	}

	fmt.Printf("\n-----------------------------------------\n")

	return nil
}

func shutDownAndCleanBotServer(bot *common.Bot, cmd *exec.Cmd) error {
	fmt.Println("Shutting down gRPC server")
	err := cmd.Process.Kill()
	if err != nil {
		return err
	}

	fmt.Println("Shut down gRPC server")

	err = cleanAfterRun()
	if err != nil {
		return err
	}

	fmt.Printf("Finished cleaning server for bot (%s)\n", bot.Id)
	fmt.Printf("\n-----------------------------------------\n")

	return nil
}

func runSimulationsOnBot(bot *common.Bot, e BotEngine) error {

	for _, simulation := range e.simulations {
		fmt.Printf("\n-----------------------------------------\n")
		fmt.Printf("Running simulation (%s) on bot (%s)\n", simulation.Id, bot.Id)
		selectionsForSimulation := []*common.FantasySelections{}

		for iteration := uint32(1); iteration <= simulation.NumIterations; iteration++ {
			fmt.Printf("\n\tIteration (%d): Making gRPC call\n", iteration)
			selectionsForIteration, err := callBotRPC(simulation.Landscape)
			if err != nil {
				fmt.Printf("\tIteration (%d): Failed to make gRPC call\n", iteration)
				fmt.Println(err)
				selectionsForSimulation = append(selectionsForSimulation, nil)
			} else {
				fmt.Printf("\tIteration (%d): bot ran successfully!\n", iteration)
				selectionsForSimulation = append(selectionsForSimulation, selectionsForIteration)
			}

		}

		e.botResults[bot.Id][simulation.Id] = selectionsForSimulation
		fmt.Printf("\n-----------------------------------------\n") // Add formatting to make separate runs clear
	}

	return nil
}

func fetchSourceCode(bot *common.Bot, e BotEngine) ([]byte, error) {
	var botCode []byte

	if bot.SourceType == common.Bot_REMOTE {
		downloadedSourceCode, err := DownloadGithubSourceCode(bot.SourceRepoUsername, bot.SourceRepoName, bot.SourcePath, e.settings.VerboseLoggingEnabled)
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

	fmt.Printf("Successfully retrieved source code for bot (%s)\n", bot.Id)
	return botCode, nil
}

func startServerForBot(bot *common.Bot, e BotEngine) (*exec.Cmd, *bytes.Buffer, *bytes.Buffer, error) {
	fmt.Printf("\n-----------------------------------------\n")
	fmt.Printf("Bootstrapping server for bot (%s)\n", bot.Id)

	botCode := e.sourceCodeCache[bot.Id]

	fmt.Println("Creating source code file")
	// absPath, err := buildLocalAbsolutePath(pyServerBotFilePath)
	// if err != nil {
	// 	return nil, bytes.NewBuffer([]byte{}), bytes.NewBuffer([]byte{}), err
	// }
	absPath, err := buildLocalAbsolutePath("tmp/bot.py")
	if err != nil {
		return nil, bytes.NewBuffer([]byte{}), bytes.NewBuffer([]byte{}), err
	}

	err = os.WriteFile(absPath, botCode, 0755)
	if err != nil {
		return nil, bytes.NewBuffer([]byte{}), bytes.NewBuffer([]byte{}), err
	}

	// _, err = CreateNewContainer("")
	// if err != nil {
	// 	return nil, bytes.NewBuffer([]byte{}), bytes.NewBuffer([]byte{}), err
	// }

	cmd := exec.Command("python", pyServerPath)
	var outb, errb bytes.Buffer
	cmd.Stdout = &outb
	cmd.Stderr = &errb

	err = cmd.Start()
	if err != nil {
		return nil, bytes.NewBuffer([]byte{}), bytes.NewBuffer([]byte{}), err
	}

	time.Sleep(1 * time.Second) // Allow 1s for gRPC server startup

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

func CreateNewContainer(landscape *common.FantasyLandscape) (string, error) {
	apiClient, err := client.NewClientWithOpts(client.FromEnv)
	if err != nil {
		panic(err)
	}
	defer apiClient.Close()

	apiClient.NegotiateAPIVersion(context.Background())

	hostBinding := nat.PortBinding{
		HostIP:   "0.0.0.0",
		HostPort: "8080",
	}

	containerPort, err := nat.NewPort("tcp", "8080")
	if err != nil {
		panic("Unable to get the port")
	}

	portBinding := nat.PortMap{containerPort: []nat.PortBinding{hostBinding}}

	hostMountPath, err := buildLocalAbsolutePath("/tmp")
	if err != nil {
		panic(err)
	}

	createResponse, _ := apiClient.ContainerCreate(
		context.Background(),
		&container.Config{
			Image: "py-grpc-server",
		},
		&container.HostConfig{
			PortBindings: portBinding,
			Mounts: []mount.Mount{
				{
					Type:     mount.TypeBind,
					ReadOnly: true,
					Source:   hostMountPath,
					Target:   "/botblitz",
				},
			},
		},
		nil,
		nil,
		"",
	)

	fmt.Println(createResponse)

	err = apiClient.ContainerStart(context.Background(), createResponse.ID, container.StartOptions{})
	if err != nil {
		fmt.Println(err)
	}

	time.Sleep(2 * time.Second)

	fmt.Println("Making RPC call")

	selections, err := callBotRPC(landscape)
	fmt.Println(selections)

	fmt.Println("Killing container")
	err = apiClient.ContainerKill(context.Background(), createResponse.ID, "")
	if err != nil {
		panic(err)
	}

	fmt.Println("Force deleting container")
	err = apiClient.ContainerRemove(context.Background(), createResponse.ID, container.RemoveOptions{Force: true})
	if err != nil {
		panic(err)
	}

	return "", nil
}
