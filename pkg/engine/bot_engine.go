package engine

import (
	"context"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/docker/docker/api/types/container"
	"github.com/docker/docker/api/types/mount"
	"github.com/docker/docker/client"
	"github.com/docker/go-connections/nat"
	common "github.com/mitchwebster/botblitz/pkg/common"
	"golang.org/x/exp/slices"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/protobuf/proto"
)

const pyServerHostAndPort = "localhost:8080"
const botResourceFolderName = "/tmp"
const botFileRelativePath = botResourceFolderName + "/bot.py" // source code name passed in resource folder
const containerServerPort = "8080"
const botResourceFolderNameInContainer = "/botblitz"
const letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
const digits = "0123456789"

type BotEngineSettings struct {
	VerboseLoggingEnabled bool
	SheetsClient          *SheetsClient
}

type BotEngine struct {
	botResults      map[string]map[string][]*common.FantasySelections // Bot -> Simulation -> Simulation Selections per iteration
	settings        BotEngineSettings
	bots            []*common.Bot
	sourceCodeCache map[string][]byte
	gameState       *common.GameState
}

func NewBotEngine(gameState *common.GameState, bots []*common.Bot, settings BotEngineSettings) *BotEngine {
	return &BotEngine{
		settings:        settings,
		bots:            bots,
		botResults:      make(map[string]map[string][]*common.FantasySelections),
		sourceCodeCache: make(map[string][]byte),
		gameState:       gameState,
	}
}

func (e *BotEngine) Summarize() string {
	var builder strings.Builder

	// write configs
	fmt.Fprintf(&builder, "Engine Summary\n\n")

	// Print settings
	fmt.Fprintf(&builder, "Settings:\n")
	// fmt.Fprintf(&builder, "\tNumSimulations: %d\n", len(e.simulations))
	fmt.Fprintf(&builder, "\tVerboseLoggingEnabled: %t\n", e.settings.VerboseLoggingEnabled)

	fmt.Fprintf(&builder, "\nBots:\n")
	fmt.Fprintf(&builder, "\tCount: %d\n", len(e.bots))
	for _, obj := range e.bots {
		fmt.Fprintf(&builder, "\t - %s\n", obj.Id)
	}

	return builder.String()
}

func (e *BotEngine) SaveGameState() error {
	// Serialize the Person object to a binary format
	serializedData, err := proto.Marshal(e.gameState)
	if err != nil {
		return err
	}

	// Save the serialized data to a file
	saveFolder := "data/game_states"
	fileName := saveFolder + "/gameState-" + strconv.Itoa(int(e.gameState.LeagueSettings.Year)) + "-" + GenerateRandomString(6) + ".bin"

	fullFilePath, err := BuildLocalAbsolutePath(fileName)
	if err != nil {
		return err
	}

	file, err := os.Create(fullFilePath)
	if err != nil {
		return err
	}
	defer file.Close()

	_, err = file.Write(serializedData)
	if err != nil {
		return err
	}

	return nil
}

func (e *BotEngine) Run() error {
	err := e.performValidations()
	if err != nil {
		return err
	}

	return e.run()
}

func (e *BotEngine) PrintResults() {
	for botId, simulationMap := range e.botResults {
		fmt.Printf("%s:\n", botId)
		for simulationId, results := range simulationMap {
			fmt.Printf("\t%s: %s\n", simulationId, results)
		}
	}
}

func (e *BotEngine) performValidations() error {
	botValidation := common.ValidateBotConfigs(e.bots)
	if !botValidation {
		return errors.New("Bot validation failed, please check provided bots")
	}

	draftValidation := e.validateDraftState()
	if draftValidation != nil {
		return draftValidation
	}

	// simulationValidation := common.ValidateSimulation(e.simulations)
	// if !simulationValidation {
	// 	return errors.New("Simulation validation failed, please check provided simulations")
	// }

	return nil
}

func (e *BotEngine) run() error {
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

	return e.runDraft()
}

func (e *BotEngine) runDraft() error {
	curRound := 1
	for curRound <= int(e.gameState.LeagueSettings.TotalRounds) {
		fmt.Printf("ROUND %d HAS STARTED!\n", curRound)

		index := 0
		increment := 1
		arrayEdge := len(e.gameState.Teams) - 1

		shouldUseReverseOrder := (curRound % 2) == 0
		if shouldUseReverseOrder {
			index = arrayEdge
			increment = -1
		}

		for index >= 0 && index <= arrayEdge {
			curBot := e.bots[index]
			e.gameState.DraftingTeamId = curBot.FantasyTeamId
			e.performDraftAction(curBot)
			index += increment
			e.gameState.CurrentPick += 1
		}

		curRound += 1
	}

	e.SaveGameState()

	return nil
}

func (e *BotEngine) performDraftAction(bot *common.Bot) error {
	containerId, err := e.startBotContainer(bot)
	if err != nil {
		return err
	}

	if e.settings.VerboseLoggingEnabled {
		fmt.Printf("Setup bot: %s\n", bot.Id)
		fmt.Printf("Bot details: Fantasy Team Id: %s, Username: %s, Repo: %s\n", bot.FantasyTeamId, bot.SourceRepoUsername, bot.SourceRepoName)
		fmt.Printf("Using a %s source to find %s\n", bot.SourceType, bot.SourcePath)
	}

	summary, err := e.performDraftPick(bot)
	if err != nil {
		fmt.Println("Failed to run draft using bot")
		fmt.Println(err)
		summary, err = draftPlayerOnInvalidResponse(bot.FantasyTeamId, e.gameState)
		if err != nil {
			return err
		}
		summary += string('*')
	}

	err = registerPickInSheets(summary, int(e.gameState.CurrentPick), len(e.gameState.Teams), bot.FantasyTeamId, e.settings.SheetsClient)
	if err != nil {
		fmt.Println("Failed to write content to Google Sheets")
		return err
	}

	if err := e.saveBotLogsToFile(containerId); err != nil {
		return err
	}

	err = shutDownAndCleanBotServer(bot, containerId, e.settings.VerboseLoggingEnabled)
	if err != nil {
		fmt.Println("CRITICAL!! Failed to clean after bot run")
		return err
	}

	return nil
}

// TODO: the resulting log file has some binary bs at the beginning of each
// line - unsure why. I think the docs here explain it:
// https://pkg.go.dev/github.com/moby/moby/client#Client.ContainerLogs.
//
// TODO: the bots seem to be dropping stdout (i.e. calling print() in your bot
// doesn't result in anything coming out of the container). This needs to be
// fixed/changed on the container/python side of things.
func (e *BotEngine) saveBotLogsToFile(containerId string) error {
	// Connect to docker api.
	apiClient, err := client.NewClientWithOpts(client.FromEnv)
	if err != nil {
		return err
	}
	defer apiClient.Close()
	apiClient.NegotiateAPIVersion(context.Background())

	// Request logs for this container.
	ctx := context.Background()
	reader, err := apiClient.ContainerLogs(ctx, containerId, container.LogsOptions{
		ShowStdout: true,
		ShowStderr: true,
	})
	if err != nil {
		return err
	}

	// Create temp file.
	// TODO: include bot name and draft round in file name
	f, err := os.CreateTemp("", "draft.*.txt")
	if err != nil {
		return err
	}
	defer f.Close()

	// Copy logs to temp file.
	_, err = io.Copy(f, reader)
	if err != nil && err != io.EOF {
		return err
	}
	fmt.Printf("Wrote bot logs to %q\n", f.Name())

	return nil
}

func (e *BotEngine) validateDraftState() error {
	if !e.gameState.LeagueSettings.IsSnakeDraft {
		return fmt.Errorf("I only know how to snake draft")
	}

	if e.gameState.LeagueSettings.TotalRounds <= 0 {
		return fmt.Errorf("Must have at least one round")
	}

	if len(e.gameState.Teams) <= 0 {
		return fmt.Errorf("Must have have at least one team")
	}

	if len(e.bots) <= 0 {
		return fmt.Errorf("Must have have at least one bot")
	}

	if len(e.bots) != len(e.gameState.Teams) {
		return fmt.Errorf("Must have a bot for every team")
	}

	return nil
}

func (e *BotEngine) collectBotResources() error {
	folderPath, err := BuildLocalAbsolutePath(botResourceFolderName)
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

	for _, bot := range e.bots {
		e.botResults[bot.Id] = make(map[string][]*common.FantasySelections)
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

func shutDownAndCleanBotServer(bot *common.Bot, containerId string, isVerboseLoggingEnabled bool) error {
	apiClient, err := client.NewClientWithOpts(client.FromEnv)
	if err != nil {
		return err
	}
	defer apiClient.Close()

	apiClient.NegotiateAPIVersion(context.Background())

	if isVerboseLoggingEnabled {
		fmt.Println("Killing container")
	}

	err = apiClient.ContainerKill(context.Background(), containerId, "")
	if err != nil {
		return err
	}

	if isVerboseLoggingEnabled {
		fmt.Println("Force deleting container")
	}

	err = apiClient.ContainerRemove(context.Background(), containerId, container.RemoveOptions{Force: true})
	if err != nil {
		return err
	}

	if isVerboseLoggingEnabled {
		fmt.Println("Force deleted container")
	}

	err = cleanBotResources()
	if err != nil {
		return err
	}

	if isVerboseLoggingEnabled {
		fmt.Printf("Finished cleaning server for bot (%s)\n", bot.Id)
	}

	fmt.Printf("\n-----------------------------------------\n")

	return nil
}

// func runSimulationsOnBot(bot *common.Bot, e BotEngine) error {

// 	for _, simulation := range e.simulations {
// 		fmt.Printf("\n-----------------------------------------\n")
// 		fmt.Printf("Running simulation (%s) on bot (%s)\n", simulation.Id, bot.Id)
// 		selectionsForSimulation := []*common.FantasySelections{}

// 		for iteration := uint32(1); iteration <= simulation.NumIterations; iteration++ {
// 			fmt.Printf("\n\tIteration (%d): Making gRPC call\n", iteration)
// 			selectionsForIteration, err := callBotRPC(simulation.Landscape)
// 			if err != nil {
// 				fmt.Printf("\tIteration (%d): Failed to make gRPC call\n", iteration)
// 				fmt.Println(err)
// 				selectionsForSimulation = append(selectionsForSimulation, nil)
// 			} else {
// 				fmt.Printf("\tIteration (%d): bot ran successfully!\n", iteration)
// 				selectionsForSimulation = append(selectionsForSimulation, selectionsForIteration)
// 			}

// 		}

// 		e.botResults[bot.Id][simulation.Id] = selectionsForSimulation
// 		fmt.Printf("\n-----------------------------------------\n") // Add formatting to make separate runs clear
// 	}

// 	return nil
// }

func (e *BotEngine) performDraftPick(bot *common.Bot) (string, error) {
	team, err := findCurrentTeamById(bot.FantasyTeamId, e.gameState)
	if err != nil {
		return "", err
	}

	fmt.Printf("[Pick: %d] %s (%s) will choose next...", e.gameState.CurrentPick, team.Name, team.Owner)

	draftPick, err := callBotRPC(e.gameState)
	if err != nil {
		return "", err
	}

	fmt.Println("Received response from bot")
	summary, err := validateAndMakeDraftPick(bot.FantasyTeamId, draftPick.PlayerId, e.gameState)
	if err != nil {
		return "", err
	}

	return summary, nil
}

func findCurrentTeamById(fantasyTeamId string, gameState *common.GameState) (*common.FantasyTeam, error) {
	teamIdx := slices.IndexFunc(gameState.Teams, func(t *common.FantasyTeam) bool { return t.Id == fantasyTeamId })
	if teamIdx < 0 {
		return nil, fmt.Errorf("Could not find team...concerning...")
	}

	return gameState.Teams[teamIdx], nil
}

func validateAndMakeDraftPick(fantasyTeamId string, playerId string, gameState *common.GameState) (string, error) {
	if len(playerId) <= 0 {
		return "", fmt.Errorf("Cannot draft empty player id")
	}

	idx := slices.IndexFunc(gameState.Players, func(p *common.Player) bool { return p.Id == playerId })
	if idx < 0 {
		return "", fmt.Errorf("Could not find player with selected id")
	}

	player := gameState.Players[idx]

	if player.DraftStatus.Availability == common.DraftStatus_DRAFTED {
		return "", fmt.Errorf("Cannot draft player again")
	}

	team, err := findCurrentTeamById(fantasyTeamId, gameState)
	if err != nil {
		return "", err
	}

	player.DraftStatus.TeamIdChosen = team.Id
	player.DraftStatus.Availability = common.DraftStatus_DRAFTED
	player.DraftStatus.PickChosen = gameState.CurrentPick

	fmt.Printf("With the %d pick of the bot draft, %s (%s) has selected: %s\n", gameState.CurrentPick, team.Name, team.Owner, player.FullName)

	summary := player.FullName + "(" + player.AllowedPositions[0] + ")"

	return summary, nil
}

func draftPlayerOnInvalidResponse(fantasyTeamId string, gameState *common.GameState) (string, error) {
	fmt.Println("Auto-drafting due to failure")
	playerCount := len(gameState.Players)
	index := rand.Intn(playerCount)
	hasLooped := false
	for index < playerCount && !hasLooped {
		player := gameState.Players[index]
		if player.DraftStatus.Availability == common.DraftStatus_AVAILABLE {
			summary, err := validateAndMakeDraftPick(fantasyTeamId, player.Id, gameState)
			return summary, err
		}

		index += 1
		if index == playerCount && !hasLooped {
			hasLooped = true
			index = 0
		}
	}

	return "", fmt.Errorf("Could not find a valid player to auto-draft")
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
		absPath, err := BuildLocalAbsolutePath(bot.SourcePath)
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

func (e *BotEngine) startBotContainer(bot *common.Bot) (string, error) {
	if e.settings.VerboseLoggingEnabled {
		fmt.Printf("\n-----------------------------------------\n")
		fmt.Printf("Bootstrapping server for bot (%s)\n", bot.Id)
	}

	botCode := e.sourceCodeCache[bot.Id]

	if e.settings.VerboseLoggingEnabled {
		fmt.Println("Creating source code file")
	}

	absPath, err := BuildLocalAbsolutePath(botFileRelativePath)
	if err != nil {
		return "", err
	}

	err = os.WriteFile(absPath, botCode, 0755)
	if err != nil {
		return "", err
	}

	containerId, err := createAndStartContainer()
	if err != nil {
		return "", err
	}

	return containerId, nil
}

func BuildLocalAbsolutePath(relativePath string) (string, error) {
	directory, err := os.Getwd()
	if err != nil {
		return "", err
	}

	var trimmedPath = strings.Trim(relativePath, "/")
	return fmt.Sprintf("%s/%s", directory, trimmedPath), nil
}

func cleanBotResources() error {
	absPath, err := BuildLocalAbsolutePath(botFileRelativePath)
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

func callBotRPC(gameState *common.GameState) (*common.DraftSelection, error) {
	var opts []grpc.DialOption
	opts = append(opts, grpc.WithTransportCredentials(insecure.NewCredentials()))
	opts = append(opts, grpc.WithTimeout(10*time.Second))
	// container port may not be listening yet - wait for it
	opts = append(opts, grpc.WithBlock())

	conn, err := grpc.Dial(pyServerHostAndPort, opts...)
	if err != nil {
		return nil, err
	}

	defer conn.Close()
	client := common.NewAgentServiceClient(conn)

	ctx, _ := context.WithTimeout(context.Background(), 60*time.Second)
	selections, err := client.PerformFantasyActions(ctx, gameState)
	if err != nil {
		fmt.Println("Failed calling bot")
		return nil, err
	}

	return selections, nil
}

// func callBotRPC(landscape *common.FantasyLandscape) (*common.FantasySelections, error) {
// 	var opts []grpc.DialOption
// 	opts = append(opts, grpc.WithTransportCredentials(insecure.NewCredentials()))

// 	conn, err := grpc.Dial(pyServerHostAndPort, opts...)
// 	if err != nil {
// 		return nil, err
// 	}

// 	defer conn.Close()
// 	client := common.NewAgentServiceClient(conn)

// 	selections, err := client.PerformFantasyActions(context.Background(), landscape)
// 	if err != nil {
// 		return nil, err
// 	}

// 	return selections, nil
// }

func createAndStartContainer() (string, error) {
	apiClient, err := client.NewClientWithOpts(client.FromEnv)
	if err != nil {
		return "", err
	}
	defer apiClient.Close()

	apiClient.NegotiateAPIVersion(context.Background())

	hostBinding := nat.PortBinding{
		HostIP:   "0.0.0.0",
		HostPort: "8080",
	}

	// Define resource limits
	resources := container.Resources{
		Memory:   512 * 1024 * 1024, // 512 MB
		NanoCPUs: 1e9,               // 1 CPU
	}

	containerPort, err := nat.NewPort("tcp", containerServerPort)
	if err != nil {
		return "", fmt.Errorf("Unable to get the port: %v", err)
	}

	portBinding := nat.PortMap{containerPort: []nat.PortBinding{hostBinding}}

	hostMountPath, err := BuildLocalAbsolutePath(botResourceFolderName)
	if err != nil {
		return "", err
	}

	createResponse, err := apiClient.ContainerCreate(
		context.Background(),
		&container.Config{
			Image: "py_grpc_server",
		},
		&container.HostConfig{
			PortBindings: portBinding,
			Mounts: []mount.Mount{
				{
					Type:     mount.TypeBind,
					ReadOnly: true,
					Source:   hostMountPath,
					Target:   botResourceFolderNameInContainer,
				},
			},
			Resources: resources,
		},
		nil,
		nil,
		"",
	)
	if err != nil {
		return "", fmt.Errorf("unable to create container: %v", err)
	}

	err = apiClient.ContainerStart(context.Background(), createResponse.ID, container.StartOptions{})
	if err != nil {
		fmt.Println(err)
		// TODO: delete the container we created if we can't start it?
		return "", fmt.Errorf("couldn't start container: %v", err)
	}

	return createResponse.ID, nil
}

func registerPickInSheets(summary string, currentPick int, teamCount int, fantasyTeamId string, client *SheetsClient) error {
	if client == nil {
		return nil
	}

	zero_based_round := ((currentPick - 1) / teamCount) + 1
	indexOfFantasyTeam, _ := strconv.Atoi(fantasyTeamId)
	newCol := rune(int(InitialCol) + indexOfFantasyTeam + 1)

	err := WriteContentToCell(IntialRow+zero_based_round, newCol, summary, client)
	return err
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
