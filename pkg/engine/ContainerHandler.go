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
	"github.com/docker/docker/errdefs"
	"github.com/docker/docker/pkg/stdcopy"
	"github.com/docker/go-connections/nat"
	common "github.com/mitchwebster/botblitz/pkg/common"
	"github.com/mitchwebster/botblitz/pkg/gamestate"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/protobuf/types/known/emptypb"
)

const localhost = "localhost"
const botResourceFolderName = "/tmp"
const botResourceDataFolderName = botResourceFolderName + "/data"
const botFileRelativePath = botResourceFolderName + "/bot.py" // source code name passed in resource folder
const containerServerPort = "8080"
const botResourceFolderNameInContainer = "/botblitz"
const appServerFolderPath = "/app/py_grpc_server"

func (e *BotEngine) saveBotLogsToFile(bot *common.Bot, containerId string) error {
	// Connect to docker api.
	apiClient, err := client.NewClientWithOpts(client.FromEnv)
	if err != nil {
		return err
	}
	defer apiClient.Close()
	apiClient.NegotiateAPIVersion(context.Background())

	pickNum, err := e.gameStateHandler.GetCurrentDraftPick()
	if err != nil {
		return err
	}

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
	outf, err := os.CreateTemp("", fmt.Sprintf("draft-pick%d-%s-*.stdout", pickNum, bot.Id))
	if err != nil {
		return err
	}
	defer outf.Close()
	errf, err := os.CreateTemp("", fmt.Sprintf("draft-pick%d-%s-*.stderr", pickNum, bot.Id))
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

func (e *BotEngine) shutDownAndCleanBotServer(containerId string) error {
	apiClient, err := client.NewClientWithOpts(client.FromEnv)
	if err != nil {
		return err
	}
	defer apiClient.Close()

	apiClient.NegotiateAPIVersion(context.Background())

	if e.settings.VerboseLoggingEnabled {
		fmt.Println("Killing container")
	}

	err = apiClient.ContainerKill(context.Background(), containerId, "")
	if err != nil {
		if errdefs.IsConflict(err) {
			fmt.Printf("Container %s is not running, continuing\n", containerId)
		} else {
			return err
		}
	}

	if e.settings.VerboseLoggingEnabled {
		fmt.Println("Force deleting container")
	}

	err = apiClient.ContainerRemove(context.Background(), containerId, container.RemoveOptions{Force: true})
	if err != nil {
		print(err)
		return err
	}

	if e.settings.VerboseLoggingEnabled {
		fmt.Println("Force deleted container")
	}

	err = cleanBotResources()
	if err != nil {
		return err
	}

	return nil
}

func (e *BotEngine) startBotContainer(bot *common.Bot, port string) (string, error) {
	if e.settings.VerboseLoggingEnabled {
		fmt.Printf("Bootstrapping server for bot (%s)\n", bot.Id)
	}

	botCode := e.sourceCodeCache[bot.Id]

	if e.settings.VerboseLoggingEnabled {
		fmt.Println("Creating source code file")
	}

	absPath, err := common.BuildLocalAbsolutePath(botFileRelativePath)
	if err != nil {
		return "", err
	}

	err = os.WriteFile(absPath, botCode, 0755)
	if err != nil {
		return "", err
	}

	// absDataPath, err := common.BuildLocalAbsolutePath(botResourceDataFolderName)
	// if err != nil {
	// 	return "", err
	// }

	// err = WriteDataToFolder(e.dataBytes, absDataPath)
	// if err != nil {
	// 	return "", fmt.Errorf("failed to write data to folder: %s", err)
	// }

	env := []string{}

	envVarsFromCache, ok := e.envVarsCache[bot.Id]
	if ok {
		env = envVarsFromCache
	}

	containerId, err := e.createAndStartContainer(env, port)
	if err != nil {
		return "", err
	}

	return containerId, nil
}

func cleanBotResources() error {
	absPath, err := common.BuildLocalAbsolutePath(botFileRelativePath)
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

	absDataPath, err := common.BuildLocalAbsolutePath(botResourceDataFolderName)
	if err != nil {
		return err
	}

	err = os.RemoveAll(absDataPath)
	if err != nil {
		return fmt.Errorf("failed to remove data folder: %v", err)
	}

	return nil
}

func (e *BotEngine) createAndStartContainer(env []string, port string) (string, error) {
	apiClient, err := client.NewClientWithOpts(client.FromEnv)
	if err != nil {
		return "", err
	}
	defer apiClient.Close()

	apiClient.NegotiateAPIVersion(context.Background())

	hostBinding := nat.PortBinding{
		HostIP:   "0.0.0.0",
		HostPort: port,
	}

	// Define resource limits
	resources := container.Resources{
		Memory:   512 * 1024 * 1024, // 512 MB
		NanoCPUs: 1e9,               // 1 CPU
	}

	containerPort, err := nat.NewPort("tcp", containerServerPort)
	if err != nil {
		return "", fmt.Errorf("unable to get the port: %s", err)
	}

	portBinding := nat.PortMap{containerPort: []nat.PortBinding{hostBinding}}

	hostMountPath, err := common.BuildLocalAbsolutePath(botResourceFolderName)
	if err != nil {
		return "", err
	}

	databaseFilePath := appServerFolderPath + "/" + gamestate.AppDatabaseName

	createResponse, err := apiClient.ContainerCreate(
		context.Background(),
		&container.Config{
			Image: "py_grpc_server",
			Env:   env,
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
				{
					Type:     mount.TypeBind,
					ReadOnly: true,
					Source:   e.gameStateHandler.GetDBSaveFilePath(),
					Target:   databaseFilePath,
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

func (e *BotEngine) callAddDropRPC(ctx context.Context, port string) (*common.AttemptedFantasyActions, error) {
	var opts []grpc.DialOption
	opts = append(opts, grpc.WithTransportCredentials(insecure.NewCredentials()))
	opts = append(opts, grpc.WithTimeout(10*time.Second))
	// container port may not be listening yet - wait for it
	opts = append(opts, grpc.WithBlock())

	pyServerHostAndPort := localhost + ":" + port
	conn, err := grpc.Dial(pyServerHostAndPort, opts...)
	if err != nil {
		return nil, err
	}

	defer conn.Close()
	client := common.NewAgentServiceClient(conn)

	ctx, _ = context.WithTimeout(ctx, 60*time.Second)
	selection, err := client.PerformAddDrop(ctx, nil)
	if err != nil {
		fmt.Println("Failed calling bot")
		return nil, err
	}

	return selection, nil
}

func (e *BotEngine) callDraftRPC(ctx context.Context, port string) (*common.DraftSelection, error) {
	var opts []grpc.DialOption
	opts = append(opts, grpc.WithTransportCredentials(insecure.NewCredentials()))
	opts = append(opts, grpc.WithTimeout(10*time.Second))
	// container port may not be listening yet - wait for it
	opts = append(opts, grpc.WithBlock())

	pyServerHostAndPort := localhost + ":" + port
	// print(pyServerHostAndPort)
	conn, err := grpc.Dial(pyServerHostAndPort, opts...)
	if err != nil {
		return nil, err
	}

	defer conn.Close()
	client := common.NewAgentServiceClient(conn)

	ctx, _ = context.WithTimeout(ctx, 60*time.Second)
	selections, err := client.DraftPlayer(ctx, &emptypb.Empty{})
	if err != nil {
		fmt.Println("Failed calling bot")
		return nil, err
	}

	return selections, nil
}

func (e *BotEngine) isContainerRunning(containerId string) (bool, error) {
	apiClient, err := client.NewClientWithOpts(client.FromEnv)
	if err != nil {
		return false, err
	}
	defer apiClient.Close()

	apiClient.NegotiateAPIVersion(context.Background())

	containerInfo, err := apiClient.ContainerInspect(context.Background(), containerId)
	if err != nil {
		return false, err
	}

	return containerInfo.State.Running, nil
}

func (e *BotEngine) findAvailablePort() (string, error) {
	basePort := 8080
	usedPorts := make(map[string]bool)
	for _, info := range e.botContainers {
		usedPorts[info.Port] = true
	}
	port := basePort
	for {
		strPort := fmt.Sprintf("%d", port)
		if !usedPorts[strPort] {
			return strPort, nil
		}

		port++
		if port > 9000 { // Prevent infinite loop
			return "", fmt.Errorf("no available ports found")
		}
	}
}

func (e *BotEngine) getOrCreateBotContainer(bot *common.Bot) (*BotContainerInfo, error) {
	// Check if container already exists for this bot
	if containerInfo, exists := e.botContainers[bot.Id]; exists {
		if e.settings.VerboseLoggingEnabled {
			fmt.Printf("Found existing container for bot (%s): %s\n", bot.Id, containerInfo.ContainerID)
		}

		// Check if the container is still running
		if isRunning, err := e.isContainerRunning(containerInfo.ContainerID); err == nil && isRunning {
			if e.settings.VerboseLoggingEnabled {
				fmt.Printf("Using existing container for bot (%s): %s\n", bot.Id, containerInfo.ContainerID)
			}
			return containerInfo, nil
		} else {
			if e.settings.VerboseLoggingEnabled {
				fmt.Printf("Container for bot (%s) is not running, cleaning up and will recreate\n", bot.Id)
			}

			// Clean up the non-running container
			err := e.shutDownAndCleanBotServer(containerInfo.ContainerID)
			if err != nil {
				if e.settings.VerboseLoggingEnabled {
					fmt.Printf("Warning: Failed to clean up non-running container for bot %s: %v\n", bot.Id, err)
				}

				return nil, err
			}

			// Remove the invalid container ID from the map
			delete(e.botContainers, bot.Id)
		}
	}

	// Container doesn't exist or is not running, create a new one
	if e.settings.VerboseLoggingEnabled {
		fmt.Printf("Creating new container for bot (%s)\n", bot.Id)
	}

	// Find an available port
	port, err := e.findAvailablePort()
	if err != nil {
		return nil, err
	}

	// Use the existing startBotContainer function
	containerId, err := e.startBotContainer(bot, port)
	if err != nil {
		return nil, err
	}

	containerInfo := &BotContainerInfo{
		ContainerID: containerId,
		Port:        port,
	}
	e.botContainers[bot.Id] = containerInfo

	return containerInfo, nil
}

func (e *BotEngine) startContainerAndPerformDraftAction(ctx context.Context, bot *common.Bot) (playerId string, returnError error) {
	containerInfo, err := e.getOrCreateBotContainer(bot)
	if err != nil {
		return "", err
	}

	if e.settings.VerboseLoggingEnabled {
		fmt.Printf("Setup bot: %s\n", bot.Id)
		fmt.Printf("Bot details: Id: %s, Username: %s, Repo: %s\n", bot.Id, bot.SourceRepoUsername, bot.SourceRepoName)
		fmt.Printf("Using a %s source to find %q\n", bot.SourceType, bot.SourcePath)
	}

	draftPick, err := e.callDraftRPC(ctx, containerInfo.Port)
	if err != nil {
		return "", err
	}

	// if e.settings.VerboseLoggingEnabled {
	// 	if err := e.saveBotLogsToFile(bot, containerId); err != nil {
	// 		return "", err
	// 	}
	// }

	return draftPick.PlayerId, returnError
}

func (e *BotEngine) startContainerAndPerformAddDropAction(ctx context.Context, bot *common.Bot) (selections *common.AttemptedFantasyActions, returnError error) {
	containerInfo, err := e.getOrCreateBotContainer(bot)
	if err != nil {
		return nil, err
	}

	if e.settings.VerboseLoggingEnabled {
		fmt.Printf("Setup bot: %s\n", bot.Id)
		fmt.Printf("Bot details: Id: %s, Username: %s, Repo: %s\n", bot.Id, bot.SourceRepoUsername, bot.SourceRepoName)
		fmt.Printf("Using a %s source to find %q\n", bot.SourceType, bot.SourcePath)
	}

	selections, err = e.callAddDropRPC(ctx, containerInfo.Port)
	if err != nil {
		return nil, err
	}

	// if e.settings.VerboseLoggingEnabled {
	// 	if err := e.saveBotLogsToFile(bot, containerId); err != nil {
	// 		return nil, err
	// 	}
	// }

	return selections, returnError
}

func (e *BotEngine) CleanupAllPyGrpcServerContainers() error {
	apiClient, err := client.NewClientWithOpts(client.FromEnv)
	if err != nil {
		return err
	}
	defer apiClient.Close()

	apiClient.NegotiateAPIVersion(context.Background())

	// List all containers
	containers, err := apiClient.ContainerList(context.Background(), container.ListOptions{All: true})
	if err != nil {
		return fmt.Errorf("failed to list containers: %v", err)
	}

	cleanedCount := 0
	for _, container := range containers {
		// Check if this container is using the py_grpc_server image
		if container.Image == "py_grpc_server" {
			if e.settings.VerboseLoggingEnabled {
				fmt.Printf("Cleaning up py_grpc_server container: %s\n", container.ID)
			}

			err := e.shutDownAndCleanBotServer(container.ID)
			if err != nil {
				fmt.Printf("Error type: %T\n", err)
				fmt.Printf("Warning: Failed to kill container %s: %v\n", container.ID, err)
			}
		}
	}

	if e.settings.VerboseLoggingEnabled {
		fmt.Printf("Cleaned up %d py_grpc_server containers\n", cleanedCount)
	}

	return nil
}
