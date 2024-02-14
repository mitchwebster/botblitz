package engine

import (
	"errors"
	"fmt"
	"io"
	"net/http"
)

func DownloadGithubSourceCode(username string, repoName string, verboseLoggingEnabled bool) error {
	rawCodeUrl := getRawCodeUrl(username, repoName)
	fmt.Printf("Fetching from %s ...", rawCodeUrl)

	agentCode, err := getContent(rawCodeUrl)
	if err != nil {
		if verboseLoggingEnabled {
			fmt.Println("Failed to retrieve agent code")
			fmt.Println(err)
		}

		return err
	}

	if verboseLoggingEnabled {
		fmt.Println("Retrieved agent code: ")
		fmt.Println(agentCode)
	}

	return nil
}

func getRawCodeUrl(username string, repoName string) string {
	var rawLocationUrl string = "https://raw.githubusercontent.com"
	var agentLocation string = "main/dist/agent.py"

	return fmt.Sprintf("%s/%s/%s/%s", rawLocationUrl, username, repoName, agentLocation)
}

func getContent(url string) (string, error) {
	// Perform the GET request
	response, err := http.Get(url)
	if err != nil {
		return "", err
	}
	defer response.Body.Close()

	// Read the response body
	body, err := io.ReadAll(response.Body)
	if err != nil {
		return "", err
	}

	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return "", errors.New(fmt.Sprintf("Recieved an invalid status code: %d", response.StatusCode))
	}

	return string(body), nil
}
