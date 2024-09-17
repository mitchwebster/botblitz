package engine

import (
	"context"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/docker/docker/api/types/container"
	"github.com/docker/docker/api/types/mount"
	"github.com/docker/docker/client"
	"github.com/docker/docker/pkg/stdcopy"
	"github.com/docker/go-connections/nat"
	common "github.com/mitchwebster/botblitz/pkg/common"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

const pyServerHostAndPort = "localhost:8080"
const botResourceFolderName = "/tmp"
const botFileRelativePath = botResourceFolderName + "/bot.py" // source code name passed in resource folder
const containerServerPort = "8080"
const botResourceFolderNameInContainer = "/botblitz"

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

	// Create temp files.
	// TODO: include bot name and draft round in file name
	outf, err := os.CreateTemp("", "draft.*.stdout")
	if err != nil {
		return err
	}
	defer outf.Close()
	errf, err := os.CreateTemp("", "draft.*.stderr")
	if err != nil {
		return err
	}
	defer errf.Close()

	// Copy logs to temp file.
	_, err = stdcopy.StdCopy(outf, errf, reader)
	if err != nil && err != io.EOF {
		return err
	}
	fmt.Printf("Wrote bot logs to %q, %q\n", outf.Name(), errf.Name())

	return nil
}

func (e *BotEngine) shutDownAndCleanBotServer(bot *common.Bot, containerId string, isVerboseLoggingEnabled bool) error {
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

func (e *BotEngine) callDraftRPC(ctx context.Context, gameState *common.GameState) (*common.DraftSelection, error) {
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

	ctx, _ = context.WithTimeout(ctx, 60*time.Second)
	selections, err := client.DraftPlayer(ctx, gameState)
	if err != nil {
		fmt.Println("Failed calling bot")
		return nil, err
	}

	return selections, nil
}
