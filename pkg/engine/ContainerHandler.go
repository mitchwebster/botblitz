package engine

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"io"
	"net"
	"os"
	"strconv"
	"time"

	"github.com/docker/docker/api/types"
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
	"google.golang.org/grpc/metadata"
	"google.golang.org/protobuf/types/known/emptypb"
)

const localhost = "localhost"
const botResourceFolderName = "/tmp"
const botResourceDataFolderName = botResourceFolderName + "/data"
const botFileRelativePath = botResourceFolderName + "/bot.py" // source code name passed in resource folder
const containerServerPort = "8080"
const botResourceFolderNameInContainer = "/botblitz"
const appServerFolderPath = "/app/py_grpc_server"

// botNetworkName is the bridge network every bot container joins. Inter-container
// communication is disabled on it, so a bot can still reach the internet (some call
// external APIs) but cannot reach another bot's gRPC server to spy on or flood it.
const botNetworkName = "botblitz_bots"

// botContainerPidsLimit caps processes/threads per container so a bot can't fork-bomb
// its container (or the host) — generous enough for the gRPC thread pool, the
// per-call python3 subprocess, and numeric libraries' worker threads.
const botContainerPidsLimit int64 = 256

// rpcTokenEnvVar / rpcTokenMetadataKey carry a per-container secret: the engine hands it
// to the container at start and sends it on every RPC, and the container's gRPC server
// rejects calls without it. Keep in sync with py_grpc_server/server.py. This is what
// stops one bot from calling another's server where the network can't block it — e.g.
// Docker Desktop, where host.docker.internal forwards into the host's loopback and so
// reaches every bot's published port.
const rpcTokenEnvVar = "BOTBLITZ_RPC_TOKEN"
const rpcTokenMetadataKey = "x-botblitz-token"

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

	if snapshotPath, ok := e.dbSnapshotPaths[containerId]; ok {
		if removeErr := os.Remove(snapshotPath); removeErr != nil && !os.IsNotExist(removeErr) {
			fmt.Printf("Warning: failed to remove db snapshot %q: %v\n", snapshotPath, removeErr)
		}
		delete(e.dbSnapshotPaths, containerId)
	}

	return nil
}

func (e *BotEngine) startBotContainer(bot *gamestate.Bot, port int, rpcToken string) (string, error) {
	if e.settings.VerboseLoggingEnabled {
		fmt.Printf("Bootstrapping server for bot (%s)\n", bot.ID)
	}

	botCode := e.sourceCodeCache[bot.ID]

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

	envVarsFromCache, ok := e.envVarsCache[bot.ID]
	if ok {
		env = envVarsFromCache
	}

	if e.settings.InlineExecution {
		env = append(env, "BOTBLITZ_EVAL_INLINE=1")
	}

	// Copy rather than append onto the cached slice, which other calls share.
	env = append(append([]string{}, env...), rpcTokenEnvVar+"="+rpcToken)

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

// refreshDbSnapshot overwrites the CONTENTS of the already-mounted
// snapshotPath file with the current state of the live DB, in place (same
// inode) rather than deleting and recreating it at that path.
//
// This matters because a bot's container gets reused across every pick it
// makes for the rest of the draft (see getOrCreateBotContainer) -- a Docker
// bind mount of a single file is pinned to the inode present at mount time,
// so a container's very first pick sees a correct, fresh snapshot, but every
// later pick on that same (reused) container would otherwise keep seeing
// that exact same frozen, round-1-era snapshot forever: bots kept trying to
// re-draft players they (or someone else) had already taken in round 2+.
// Truncating and rewriting the existing file's content -- rather than
// swapping in a new file at the same path -- keeps the already-established
// mount pointed at live data on every call, not just the first.
//
// VACUUM INTO can't target a path that already exists, so the fresh snapshot
// is built at a scratch path first and then copied over the existing
// (mounted) file. Uses the engine's normal shared DB connection -- there was
// no actual reason this needed its own dedicated connection, that was a
// wrong theory from an earlier pass at this bug.
func (e *BotEngine) refreshDbSnapshot(snapshotPath string) error {
	tmpFile, err := os.CreateTemp("", "botblitz-db-snapshot-refresh-*.db")
	if err != nil {
		return fmt.Errorf("unable to create refresh temp file: %v", err)
	}
	tmpPath := tmpFile.Name()
	tmpFile.Close()
	if err := os.Remove(tmpPath); err != nil {
		return fmt.Errorf("unable to clear refresh temp file: %v", err)
	}
	defer os.Remove(tmpPath)

	if err := e.gameStateHandler.GetDB().Exec("VACUUM INTO ?", tmpPath).Error; err != nil {
		return fmt.Errorf("unable to snapshot database: %v", err)
	}

	data, err := os.ReadFile(tmpPath)
	if err != nil {
		return fmt.Errorf("unable to read refreshed snapshot: %v", err)
	}

	return os.WriteFile(snapshotPath, data, 0644)
}

// refreshDbSnapshotForContainer refreshes the DB snapshot bind-mounted into
// containerId, if one is tracked for it. A no-op (not an error) for a
// container with no tracked snapshot, which shouldn't normally happen since
// every container created by createAndStartContainer gets one.
func (e *BotEngine) refreshDbSnapshotForContainer(containerId string) error {
	snapshotPath, ok := e.dbSnapshotPaths[containerId]
	if !ok {
		return nil
	}
	return e.refreshDbSnapshot(snapshotPath)
}

func (e *BotEngine) createAndStartContainer(env []string, port int) (string, error) {
	apiClient, err := client.NewClientWithOpts(client.FromEnv)
	if err != nil {
		return "", err
	}
	defer apiClient.Close()

	apiClient.NegotiateAPIVersion(context.Background())

	if err := ensureBotNetwork(apiClient); err != nil {
		return "", err
	}

	// Bind to loopback only: the engine dials localhost, and nothing else (other
	// containers via the bridge gateway, or other hosts on the network) should be
	// able to reach a bot's gRPC server.
	hostBinding := nat.PortBinding{
		HostIP:   "127.0.0.1",
		HostPort: strconv.Itoa(port),
	}

	// Define resource limits
	pidsLimit := botContainerPidsLimit
	resources := container.Resources{
		Memory:    512 * 1024 * 1024, // 512 MB
		NanoCPUs:  1e9,               // 1 CPU
		PidsLimit: &pidsLimit,
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

	// Bind-mount a disposable per-container snapshot file instead of the live DB
	// directly. The host keeps writing picks to the live file between containers,
	// and Docker Desktop's macOS file-sharing layer doesn't reliably propagate
	// POSIX file locks across the host<->container boundary for a bind mount -- so
	// a container could otherwise start reading at the exact moment the host is
	// mid-write and catch a torn page, which SQLite reports as "database disk
	// image is malformed" even though the file is fine a moment later.
	//
	// Just needs to exist for Docker to mount; refreshDbSnapshot (called before
	// every RPC, see startContainerAndPerformDraftAction) fills in and keeps
	// refreshing its real content for as long as this container lives --
	// cleaned up in shutDownAndCleanBotServer.
	snapshotFile, err := os.CreateTemp("", "botblitz-db-snapshot-*.db")
	if err != nil {
		return "", fmt.Errorf("unable to create db snapshot file: %v", err)
	}
	snapshotPath := snapshotFile.Name()
	snapshotFile.Close()

	createResponse, err := apiClient.ContainerCreate(
		context.Background(),
		&container.Config{
			Image: "py_grpc_server",
			Env:   env,
		},
		&container.HostConfig{
			PortBindings: portBinding,
			NetworkMode:  container.NetworkMode(botNetworkName),
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
					Source:   snapshotPath,
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
		os.Remove(snapshotPath)
		return "", fmt.Errorf("unable to create container: %v", err)
	}

	e.dbSnapshotPaths[createResponse.ID] = snapshotPath

	err = apiClient.ContainerStart(context.Background(), createResponse.ID, container.StartOptions{})
	if err != nil {
		fmt.Println(err)
		// TODO: delete the container we created if we can't start it?
		return "", fmt.Errorf("couldn't start container: %v", err)
	}

	return createResponse.ID, nil
}

// ensureBotNetwork creates the shared bot network (with inter-container communication
// disabled) if it doesn't already exist.
func ensureBotNetwork(apiClient *client.Client) error {
	ctx := context.Background()
	_, err := apiClient.NetworkInspect(ctx, botNetworkName, types.NetworkInspectOptions{})
	if err == nil {
		return nil
	}
	if !errdefs.IsNotFound(err) {
		return fmt.Errorf("unable to inspect network %s: %v", botNetworkName, err)
	}

	_, err = apiClient.NetworkCreate(ctx, botNetworkName, types.NetworkCreate{
		Driver: "bridge",
		Options: map[string]string{
			"com.docker.network.bridge.enable_icc": "false",
		},
	})
	if err != nil && !errdefs.IsConflict(err) {
		return fmt.Errorf("unable to create network %s: %v", botNetworkName, err)
	}

	return nil
}

func (e *BotEngine) callWeeklyFantasyActionsRPC(ctx context.Context, port string, rpcToken string) (*common.AttemptedFantasyActions, error) {
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
	ctx = metadata.AppendToOutgoingContext(ctx, rpcTokenMetadataKey, rpcToken)
	selection, err := client.PerformWeeklyFantasyActions(ctx, nil)
	if err != nil {
		fmt.Println("Failed calling bot")
		return nil, err
	}

	return selection, nil
}

func (e *BotEngine) callDraftRPC(ctx context.Context, port string, rpcToken string) (*common.DraftSelection, error) {
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
	ctx = metadata.AppendToOutgoingContext(ctx, rpcTokenMetadataKey, rpcToken)
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

func newRPCToken() (string, error) {
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		return "", fmt.Errorf("unable to generate rpc token: %v", err)
	}
	return hex.EncodeToString(buf), nil
}

func findAvailablePort() (int, error) {
	// Listen on port 0 - OS will assign an available port
	listener, err := net.Listen("tcp", ":0")
	if err != nil {
		return 0, err
	}
	defer listener.Close()

	// Get the assigned port
	port := listener.Addr().(*net.TCPAddr).Port
	return port, nil
}

func (e *BotEngine) getOrCreateBotContainer(bot *gamestate.Bot) (*BotContainerInfo, error) {
	// Check if container already exists for this bot
	if containerInfo, exists := e.botContainers[bot.ID]; exists {
		if e.settings.VerboseLoggingEnabled {
			fmt.Printf("Found existing container for bot (%s): %s\n", bot.ID, containerInfo.ContainerID)
		}

		// Check if the container is still running
		if isRunning, err := e.isContainerRunning(containerInfo.ContainerID); err == nil && isRunning {
			if e.settings.VerboseLoggingEnabled {
				fmt.Printf("Using existing container for bot (%s): %s\n", bot.ID, containerInfo.ContainerID)
			}
			return containerInfo, nil
		} else {
			if e.settings.VerboseLoggingEnabled {
				fmt.Printf("Container for bot (%s) is not running, cleaning up and will recreate\n", bot.ID)
			}

			// Clean up the non-running container
			err := e.shutDownAndCleanBotServer(containerInfo.ContainerID)
			if err != nil {
				if e.settings.VerboseLoggingEnabled {
					fmt.Printf("Warning: Failed to clean up non-running container for bot %s: %v\n", bot.ID, err)
				}

				return nil, err
			}

			// Remove the invalid container ID from the map
			delete(e.botContainers, bot.ID)
		}
	}

	// Container doesn't exist or is not running, create a new one
	if e.settings.VerboseLoggingEnabled {
		fmt.Printf("Creating new container for bot (%s)\n", bot.ID)
	}

	// Find an available port
	port, err := findAvailablePort()
	if err != nil {
		return nil, err
	}

	// Use the existing startBotContainer function. Time only actual builds: in the
	// container-reuse path this fires once per bot, so it isolates true container-start
	// cost (vs. the RPC time that dominates once a container is warm).
	buildDone := LogElapsed("container build for bot %s", bot.ID)
	rpcToken, err := newRPCToken()
	if err != nil {
		buildDone()
		return nil, err
	}
	containerId, err := e.startBotContainer(bot, port, rpcToken)
	buildDone()
	if err != nil {
		return nil, err
	}

	containerInfo := &BotContainerInfo{
		ContainerID: containerId,
		Port:        strconv.Itoa(port),
		RPCToken:    rpcToken,
	}
	e.botContainers[bot.ID] = containerInfo

	return containerInfo, nil
}

func (e *BotEngine) startContainerAndPerformDraftAction(ctx context.Context, bot *gamestate.Bot) (playerId string, returnError error) {
	containerInfo, err := e.getOrCreateBotContainer(bot)
	if err != nil {
		return "", err
	}

	// Refresh on every call, not just the first: this container may be a
	// reused one from an earlier pick (see getOrCreateBotContainer), whose
	// bind-mounted snapshot would otherwise still reflect the DB as it stood
	// when the container was first created.
	if err := e.refreshDbSnapshotForContainer(containerInfo.ContainerID); err != nil {
		return "", err
	}

	if e.settings.VerboseLoggingEnabled {
		fmt.Printf("Setup bot: %s\n", bot.ID)
	}

	draftPick, err := e.callDraftRPC(ctx, containerInfo.Port, containerInfo.RPCToken)
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

func (e *BotEngine) startContainerAndPerformWeeklyFantasyActions(ctx context.Context, bot *gamestate.Bot) (selections *common.AttemptedFantasyActions, returnError error) {
	containerInfo, err := e.getOrCreateBotContainer(bot)
	if err != nil {
		return nil, err
	}

	if err := e.refreshDbSnapshotForContainer(containerInfo.ContainerID); err != nil {
		return nil, err
	}

	if e.settings.VerboseLoggingEnabled {
		fmt.Printf("Setup bot: %s\n", bot.ID)
	}

	selections, err = e.callWeeklyFantasyActionsRPC(ctx, containerInfo.Port, containerInfo.RPCToken)
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
