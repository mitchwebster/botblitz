package engine

import (
	"fmt"
	"io/ioutil"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	common "github.com/mitchwebster/botblitz/pkg/common"
	"github.com/golang/protobuf/proto"
)

const saveFolderRelativePath = "data/game_states"
const filePrefix = "gameState-"
const fileSuffix = ".bin"

func LoadLastGameState() (*common.GameState, error) {
	filePath, err := findLastSaveFilePath()
	if err != nil {
		return nil, err
	}

	// Read the serialized data from the file
	serializedData, err := ioutil.ReadFile(filePath)
	if err != nil {
		return nil, err
	}

	// Create a new Person object
	gameState := &common.GameState{}

	// Deserialize the data into the Person object
	err = proto.Unmarshal(serializedData, gameState)
	if err != nil {
		return nil, err
	}

	return gameState, nil
}

func SaveGameState(gameState *common.GameState) error {
	saveFilePath, err := getSaveFileName()
	if err != nil {
		return err
	}

	serializedData, err := proto.Marshal(gameState)
	if err != nil {
		return err
	}

	file, err := os.Create(saveFilePath)
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

func getSaveFileName() (string, error) {
	timestamp := time.Now().Unix()
	timestampStr := strconv.FormatInt(timestamp, 10)
	fileName := filePrefix + timestampStr + fileSuffix
	relativePath := saveFolderRelativePath + "/" + fileName
	absPath, err := BuildLocalAbsolutePath(relativePath)
	if err != nil {
		return "", err
	}

	return absPath, nil
}

func findLastSaveFilePath() (string, error) {
	absPath, err := BuildLocalAbsolutePath(saveFolderRelativePath)
	if err != nil {
		return "", err
	}

	// Read the directory
	entries, err := os.ReadDir(absPath)
	if err != nil {
		return "", err
	}

	fileNames := []string{}

	// Iterate over directory entries and print file names
	for _, entry := range entries {
		name := entry.Name()
		if strings.HasPrefix(name, filePrefix) {
			fileNames = append(fileNames, entry.Name())
		}
	}

	sort.Strings(fileNames)

	if len(fileNames) == 0 {
		return "", fmt.Errorf("Found no files")
	}

	relativePath := saveFolderRelativePath + "/" + fileNames[len(fileNames)-1]
	lastFilePath, err := BuildLocalAbsolutePath(relativePath)
	if err != nil {
		return "", err
	}

	return lastFilePath, nil
}
