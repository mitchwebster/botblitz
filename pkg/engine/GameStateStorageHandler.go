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
	"google.golang.org/protobuf/proto"
)

const saveFolderRelativePath = "data/game_states"
const filePrefix = "gameState-"
const fileSuffix = ".bin"
const allowedNumSaveFiles = 3

func LoadLastGameState(year uint32) (*common.GameState, error) {
	filePath, err := findLastSaveFilePath(year)
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
	saveFilePath, err := getSaveFileName(gameState.LeagueSettings.Year)
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

func CleanOldGameStates(gameState *common.GameState) error {
	paths, err := findAllSaveFiles(gameState.LeagueSettings.Year)
	if err != nil {
		return err
	}

	if len(paths) <= allowedNumSaveFiles {
		fmt.Println("Nothing to clean!")
		return nil
	}

	numFilesToRemove := len(paths) - allowedNumSaveFiles
	for i := 0; i < numFilesToRemove; i++ {
		err := os.Remove(paths[i])
		if err != nil {
			fmt.Println("Error removing file:", err)
			return err
		}
	}

	return nil
}

func getSaveFileName(year uint32) (string, error) {
	absFolderPath, err := getSaveFolderPath(year)
	if err != nil {
		return "", err
	}

	err = os.MkdirAll(absFolderPath, os.ModePerm)
	if err != nil {
		return "", err
	}

	timestamp := time.Now().Unix()
	timestampStr := strconv.FormatInt(timestamp, 10)
	fileName := filePrefix + timestampStr + fileSuffix
	return absFolderPath + "/" + fileName, nil
}

func getSaveFolderPath(year uint32) (string, error) {
	folderName := saveFolderRelativePath + "/" + strconv.Itoa(int(year))
	absPath, err := BuildLocalAbsolutePath(folderName)
	if err != nil {
		return "", err
	}

	return absPath, nil
}

func findLastSaveFilePath(year uint32) (string, error) {
	saveFiles, err := findAllSaveFiles(year)
	if err != nil {
		return "", err
	}

	if len(saveFiles) == 0 {
		return "", fmt.Errorf("Found no files")
	}

	return saveFiles[len(saveFiles)-1], nil
}

func findAllSaveFiles(year uint32) ([]string, error) {
	fileNames := []string{}

	absPath, err := getSaveFolderPath(year)
	if err != nil {
		return fileNames, err
	}

	// Read the directory
	entries, err := os.ReadDir(absPath)
	if err != nil {
		return fileNames, err
	}

	// Iterate over directory entries and print file names
	for _, entry := range entries {
		name := entry.Name()
		if strings.HasPrefix(name, filePrefix) {
			fileNames = append(fileNames, entry.Name())
		}
	}

	sort.Strings(fileNames)

	sortedAbsPaths := []string{}
	for _, fileName := range fileNames {
		filePath := absPath + "/" + fileName
		sortedAbsPaths = append(sortedAbsPaths, filePath)
	}

	return sortedAbsPaths, nil
}

// func FindAvailableYears() ([]string, error) {
// 	var subdirs []string

// 	absPath, err := BuildLocalAbsolutePath(saveFolderRelativePath)
// 	if err != nil {
// 		return subdirs, err
// 	}

// 	// Open the directory
// 	file, err := os.Open(absPath)
// 	if err != nil {
// 		return nil, err
// 	}
// 	defer file.Close()

// 	// Read the directory entries
// 	entries, err := file.Readdir(-1) // -1 means read all files
// 	if err != nil {
// 		return nil, err
// 	}

// 	// Filter out only the directories
// 	for _, entry := range entries {
// 		if entry.IsDir() {
// 			subdirs = append(subdirs, entry.Name())
// 		}
// 	}

// 	return subdirs, nil
// }
