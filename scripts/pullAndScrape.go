package main

import (
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
)

func main() {
	repoInput := findRepoName()
	fmt.Println("You entered:", repoInput)

	rawCodeUrl := getRawCodeUrl(repoInput)
	fmt.Printf("Fetching from %s ...", rawCodeUrl)

	agentCode, err := getContent(rawCodeUrl)
	if err != nil {
		fmt.Println("Failed to retrieve agent code")
		fmt.Println(err)
		return
	}

	fmt.Println("Retrieved agent code: ")
	fmt.Println(agentCode)

	filePath, err := writeCodeToFile(agentCode)
	if err != nil {
		fmt.Println("Failed to write file")
		fmt.Println(err)
		return
	}

	output, err := executePythonCode(filePath)
	if err != nil {
		fmt.Println("Failed to execute file")
		fmt.Println(err)
		return
	}

	fmt.Printf("Python script output: %s", output)
}

func findRepoName() string {
	var userInput string

	fmt.Print("Enter a Github repo name (i.e. mitchwebster/myagent ):")
	fmt.Scanln(&userInput)

	return userInput
}

func getRawCodeUrl(repoName string) string {
	var rawLocationUrl string = "https://raw.githubusercontent.com/"
	var agentLocation string = "/main/dist/agent.py"

	return rawLocationUrl + repoName + agentLocation
}

func getContent(url string) (string, error) {
	// Perform the GET request
	response, err := http.Get(url)
	if err != nil {
		fmt.Println("Error:", err)
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

	// Print the response body
	return string(body), nil
}

func writeCodeToFile(rawcode string) (string, error) {
	content := []byte(rawcode)
	filename := "code-submission-a.py"

	// Writing to a file
	err := os.WriteFile(filename, content, 0755)
	if err != nil {
		fmt.Println("Error writing to file:", err)
		return "", err
	}

	return filename, nil
}

func executePythonCode(scriptName string) (string, error) {
	// Command to run the Python script
	cmd := exec.Command("python3", scriptName)

	// CombinedOutput runs the command and returns its combined standard output and standard error.
	output, err := cmd.CombinedOutput()
	if err != nil {
		fmt.Println("Error running Python script:", err)
		return "", err
	}

	return string(output), nil
}
