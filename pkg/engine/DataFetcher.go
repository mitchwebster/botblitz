package engine

import (
	"bufio"
	"bytes"
	"context"
	"encoding/csv"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/smithy-go"
)

// DataBytes holds the CSV data in byte format
type DataBytes struct {
	SeasonProjections []byte
	WeeklyProjections []byte
	SeasonStats       []byte
	WeeklyStats       []byte
}

// FetchDataBytes loads data from S3, combines to byte array, returns data
func FetchDataBytes(year int, week int) (*DataBytes, error) {
	cfg, err := config.LoadDefaultConfig(context.TODO())
	if err != nil {
		return nil, err
	}
	s3Client := s3.NewFromConfig(cfg)
	bucketName := "botblitz"
	years := []int{year, year - 1, year - 2} // Years in descending order

	var seasonProjectionsHeader []string
	var seasonStatsHeader []string
	var weeklyProjectionsHeader []string
	var weeklyStatsHeader []string

	var seasonProjectionsData [][]string
	var seasonStatsData [][]string
	var weeklyProjectionsData [][]string
	var weeklyStatsData [][]string

	for _, y := range years {
		loadSeasonData(s3Client, bucketName, y,
			&seasonProjectionsHeader, &seasonProjectionsData,
			&seasonStatsHeader, &seasonStatsData)
		weeks, err := getWeeksToProcess(s3Client, bucketName, y, year, week)
		if err != nil {
			return nil, err
		}
		for _, w := range weeks {
			loadWeeklyData(s3Client, bucketName, y, w,
				&weeklyProjectionsHeader, &weeklyProjectionsData,
				&weeklyStatsHeader, &weeklyStatsData)
		}
	}

	// Store data in byte format
	seasonProjectionsBytes := convertToCSVBytes(seasonProjectionsHeader, seasonProjectionsData)
	weeklyProjectionsBytes := convertToCSVBytes(weeklyProjectionsHeader, weeklyProjectionsData)
	seasonStatsBytes := convertToCSVBytes(seasonStatsHeader, seasonStatsData)
	weeklyStatsBytes := convertToCSVBytes(weeklyStatsHeader, weeklyStatsData)

	// Return the byte data
	return &DataBytes{
		SeasonProjections: seasonProjectionsBytes,
		WeeklyProjections: weeklyProjectionsBytes,
		SeasonStats:       seasonStatsBytes,
		WeeklyStats:       weeklyStatsBytes,
	}, nil
}

// convertToCSVBytes converts header and data to CSV byte format
func convertToCSVBytes(header []string, data [][]string) []byte {
	var buf bytes.Buffer
	writer := csv.NewWriter(&buf)

	writer.Write(header)
	writer.WriteAll(data)

	writer.Flush()
	return buf.Bytes()
}

// WriteDataToFolder writes the byte data to CSV files in the specified folder
func WriteDataToFolder(data *DataBytes, outputFolder string) error {
	err := os.MkdirAll(outputFolder, 0755)
	if err != nil {
		return fmt.Errorf("failed to create output folder: %v", err)
	}

	files := map[string][]byte{
		"season_projections.csv": data.SeasonProjections,
		"weekly_projections.csv": data.WeeklyProjections,
		"season_stats.csv":       data.SeasonStats,
		"weekly_stats.csv":       data.WeeklyStats,
	}

	for filename, content := range files {
		err := os.WriteFile(filepath.Join(outputFolder, filename), content, 0644)
		if err != nil {
			return fmt.Errorf("failed to write %s: %v", filename, err)
		}
	}

	return nil
}

// loadSeasonData loads season projections and stats for a given year.
func loadSeasonData(s3Client *s3.Client, bucketName string, year int,
	projectionsHeader *[]string, projectionsData *[][]string,
	statsHeader *[]string, statsData *[][]string) {

	projKey := fmt.Sprintf("projections/%d/draft-projections.csv", year)
	header, data, err := loadCSVFromS3(s3Client, bucketName, projKey, year, nil)
	if err == nil && data != nil {
		if len(*projectionsHeader) == 0 {
			*projectionsHeader = header
		}
		*projectionsData = append(*projectionsData, data...)
	} else if err != nil {
		log.Printf("Error reading %s: %v", projKey, err)
	}

	statsKey := fmt.Sprintf("stats/%d/season-stats.csv", year)
	header, data, err = loadCSVFromS3(s3Client, bucketName, statsKey, year, nil)
	if err == nil && data != nil {
		if len(*statsHeader) == 0 {
			*statsHeader = header
		}
		*statsData = append(*statsData, data...)
	} else if err != nil {
		log.Printf("Error reading %s: %v", statsKey, err)
	}
}

// getWeeksToProcess determines weeks to process for a given year.
func getWeeksToProcess(s3Client *s3.Client, bucketName string, y int, currentYear int, currentWeek int) ([]int, error) {
	if y == currentYear {
		// Weeks for the current year, in descending order
		weeks := make([]int, currentWeek)
		for i := 0; i < currentWeek; i++ {
			weeks[i] = currentWeek - i
		}
		return weeks, nil
	} else {
		// List all available weeks and sort them in descending order
		weeksProj, _ := listAvailableWeeks(s3Client, bucketName, fmt.Sprintf("projections/%d/week/", y), "-projections.csv")
		weeksStats, _ := listAvailableWeeks(s3Client, bucketName, fmt.Sprintf("stats/%d/week/", y), "-stats.csv")
		weeksMap := make(map[int]bool)
		for _, w := range weeksProj {
			weeksMap[w] = true
		}
		for _, w := range weeksStats {
			weeksMap[w] = true
		}
		var weeks []int
		for w := range weeksMap {
			weeks = append(weeks, w)
		}
		if len(weeks) == 0 {
			log.Printf("No weekly data found for year %d.", y)
		}
		// Sort weeks in descending order
		sort.Sort(sort.Reverse(sort.IntSlice(weeks)))
		return weeks, nil
	}
}

// loadWeeklyData loads weekly projections and stats for a given year and week.
func loadWeeklyData(s3Client *s3.Client, bucketName string, y int, w int,
	projectionsHeader *[]string, projectionsData *[][]string,
	statsHeader *[]string, statsData *[][]string) {

	projKey := fmt.Sprintf("projections/%d/week/%d-projections.csv", y, w)
	header, data, err := loadCSVFromS3(s3Client, bucketName, projKey, y, &w)
	if err == nil && data != nil {
		if len(*projectionsHeader) == 0 {
			*projectionsHeader = header
		}
		*projectionsData = append(*projectionsData, data...)
	} else if err != nil {
		log.Printf("Error reading %s: %v", projKey, err)
	}

	statsKey := fmt.Sprintf("stats/%d/week/%d-stats.csv", y, w)
	header, data, err = loadCSVFromS3(s3Client, bucketName, statsKey, y, &w)
	if err == nil && data != nil {
		if len(*statsHeader) == 0 {
			*statsHeader = header
		}
		*statsData = append(*statsData, data...)
	} else if err != nil {
		log.Printf("Error reading %s: %v", statsKey, err)
	}
}

// listAvailableWeeks lists available weeks in S3 under a given prefix.
func listAvailableWeeks(s3Client *s3.Client, bucketName string, prefix string, fileSuffix string) ([]int, error) {
	keys, err := listS3Objects(s3Client, bucketName, prefix)
	if err != nil {
		return nil, err
	}
	var weeks []int
	for _, key := range keys {
		if strings.HasSuffix(key, fileSuffix) {
			filename := filepath.Base(key)
			weekStr := strings.Split(filename, "-")[0]
			weekNum, err := strconv.Atoi(weekStr)
			if err == nil {
				weeks = append(weeks, weekNum)
			}
		}
	}
	return weeks, nil
}

// listS3Objects lists all S3 objects under a given prefix.
func listS3Objects(s3Client *s3.Client, bucketName string, prefix string) ([]string, error) {
	var keys []string
	paginator := s3.NewListObjectsV2Paginator(s3Client, &s3.ListObjectsV2Input{
		Bucket: aws.String(bucketName),
		Prefix: aws.String(prefix),
	})

	for paginator.HasMorePages() {
		output, err := paginator.NextPage(context.TODO())
		if err != nil {
			return nil, err
		}
		for _, obj := range output.Contents {
			keys = append(keys, aws.ToString(obj.Key))
		}
	}
	return keys, nil
}

// loadCSVFromS3 loads a CSV file from S3 and adds year/week columns.
func loadCSVFromS3(s3Client *s3.Client, bucketName string, key string, year int, week *int) ([]string, [][]string, error) {
	input := &s3.GetObjectInput{
		Bucket: aws.String(bucketName),
		Key:    aws.String(key),
	}
	result, err := s3Client.GetObject(context.TODO(), input)
	if err != nil {
		var apiErr smithy.APIError
		if errors.As(err, &apiErr) && apiErr.ErrorCode() == "NoSuchKey" {
			return nil, nil, nil // File does not exist
		}
		return nil, nil, err
	}
	defer result.Body.Close()
	headers, records, err := readCSV(result.Body)
	if err != nil {
		return nil, nil, err
	}
	if records == nil {
		return nil, nil, nil // No data
	}
	// Add "year" and "week" to headers
	headers = append(headers, "year")
	if week != nil {
		headers = append(headers, "week")
	}
	// Add "year" and "week" to each record
	for i := range records {
		records[i] = append(records[i], strconv.Itoa(year))
		if week != nil {
			records[i] = append(records[i], strconv.Itoa(*week))
		}
	}
	return headers, records, nil
}

// readCSV reads CSV data from an io.Reader.
func readCSV(reader io.Reader) ([]string, [][]string, error) {
	csvReader := csv.NewReader(bufio.NewReader(reader))
	data, err := csvReader.ReadAll()
	if err != nil {
		return nil, nil, err
	}
	if len(data) == 0 {
		return nil, nil, nil // No data
	}
	headers := data[0]
	records := data[1:]
	return headers, records, nil
}
