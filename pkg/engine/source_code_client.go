package engine

import (
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
)

func DownloadGithubSourceCode(username string, repoName string, path string, verboseLoggingEnabled bool) ([]byte, error) {
	rawCodeUrl := getRawCodeUrl(username, repoName, path)

	if verboseLoggingEnabled {
		fmt.Printf("Fetching from %s ...\n", rawCodeUrl)
	}

	agentCode, err := getContent(rawCodeUrl)
	if err != nil {
		if verboseLoggingEnabled {
			fmt.Println("Failed to retrieve agent code")
			fmt.Println(err)
		}

		return nil, err
	}

	if verboseLoggingEnabled {
		fmt.Println("Retrieved agent code: ")
		fmt.Println(agentCode)
	}

	return agentCode, nil
}

func getRawCodeUrl(username string, repoName string, botCodeRelativePath string) string {
	var rawLocationUrl string = "https://raw.githubusercontent.com"

	var trimedUser = strings.Trim(username, "/")
	var trimedRepo = strings.Trim(repoName, "/")
	var trimedBranch = strings.Trim("main", "/") // TODO: update to allow branch flexibility
	var trimedBotCodePath = strings.Trim(botCodeRelativePath, "/")

	return fmt.Sprintf("%s/%s/%s/%s/%s", rawLocationUrl, trimedUser, trimedRepo, trimedBranch, trimedBotCodePath)
}

func getContent(url string) ([]byte, error) {
	// Perform the GET request
	response, err := http.Get(url)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()

	// Read the response body
	body, err := io.ReadAll(response.Body)
	if err != nil {
		return nil, err
	}

	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, errors.New(fmt.Sprintf("Recieved an invalid status code: %d", response.StatusCode))
	}

	return body, nil
}
