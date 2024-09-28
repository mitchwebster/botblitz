package engine

import (
	"context"
	"log"
	"strconv"

	"google.golang.org/api/option"
	"google.golang.org/api/sheets/v4"
)

const InitialCol = 'A'
const IntialRow = 2
const spreadsheetId = "1qenk4OLl4xUgivZXdoi6qB1LUCecSx15HQmyPZQGU_c"

type SheetsClient struct {
	SheetTitle   string
	SheetService *sheets.Service
}

func CreateSheetsClient(sheetTitle string) (*SheetsClient, error) {
	credentialsFile := "sheets-creds.json"

	ctx := context.Background()
	sheetsService, err := sheets.NewService(ctx, option.WithCredentialsFile(credentialsFile))
	if err != nil {
		return nil, err
	}

	sheetClient := SheetsClient{
		SheetTitle:   sheetTitle,
		SheetService: sheetsService,
	}

	return &sheetClient, nil
}

func (client *SheetsClient) CreateNewDraftSheet() error {
	// Create a request to add a new sheet
	addSheetRequest := &sheets.AddSheetRequest{
		Properties: &sheets.SheetProperties{
			Title: client.SheetTitle,
		},
	}

	// Prepare the batch update request
	batchUpdateRequest := &sheets.BatchUpdateSpreadsheetRequest{
		Requests: []*sheets.Request{
			{
				AddSheet: addSheetRequest,
			},
		},
	}

	// Send the batch update request to create the new sheet
	_, err := client.SheetService.Spreadsheets.BatchUpdate(spreadsheetId, batchUpdateRequest).Do()
	if err != nil {
		log.Fatalf("Unable to create new sheet: %v", err)
	}

	return nil
}

func (client *SheetsClient) WriteContentToCell(row int, column rune, text string) error {
	writeRange := client.SheetTitle + "!" + string(column) + strconv.Itoa(row)

	// Create the value to write into the cell
	valueRange := &sheets.ValueRange{
		Values: [][]interface{}{
			{text}, // The value to update in the cell (A1)
		},
	}

	// Specify that we are going to overwrite the value in the cell
	_, err := client.SheetService.Spreadsheets.Values.Update(spreadsheetId, writeRange, valueRange).
		ValueInputOption("RAW").Do()
	if err != nil {
		log.Fatalf("Unable to update cell: %v", err)
	}

	return nil
}

// func batchUpdateCells(srv *sheets.Service, spreadsheetId, sheetId string) error {
// 	requests := []*sheets.Request{
// 		// Update cell values
// 		{
// 			UpdateCells: &sheets.UpdateCellsRequest{
// 				Range: &sheets.GridRange{
// 					SheetId:          sheetId,
// 					StartRowIndex:    0,
// 					EndRowIndex:      2,
// 					StartColumnIndex: 0,
// 					EndColumnIndex:   2,
// 				},
// 				Rows: []*sheets.RowData{
// 					{
// 						Values: []*sheets.CellData{
// 							{UserEnteredValue: &sheets.ExtendedValue{StringValue: "Value1"}},
// 							{UserEnteredValue: &sheets.ExtendedValue{StringValue: "Value2"}},
// 						},
// 					},
// 					{
// 						Values: []*sheets.CellData{
// 							{UserEnteredValue: &sheets.ExtendedValue{StringValue: "Value3"}},
// 							{UserEnteredValue: &sheets.ExtendedValue{StringValue: "Value4"}},
// 						},
// 					},
// 				},
// 				Fields: "*",
// 			},
// 		},
// 		// Apply cell formatting
// 		{
// 			RepeatCell: &sheets.RepeatCellRequest{
// 				Range: &sheets.GridRange{
// 					SheetId:          sheetId,
// 					StartRowIndex:    0,
// 					EndRowIndex:      2,
// 					StartColumnIndex: 0,
// 					EndColumnIndex:   2,
// 				},
// 				Cell: &sheets.CellData{
// 					UserEnteredFormat: &sheets.CellFormat{
// 						BackgroundColor: &sheets.Color{
// 							Red:   1.0, // Red color
// 							Green: 1.0, // Green color
// 							Blue:  0.0, // Blue color
// 						},
// 					},
// 				},
// 				Fields: "userEnteredFormat(backgroundColor)",
// 			},
// 		},
// 	}

// 	batchUpdateRequest := &sheets.BatchUpdateSpreadsheetRequest{
// 		Requests: requests,
// 	}

// 	_, err := srv.Spreadsheets.BatchUpdate(spreadsheetId, batchUpdateRequest).Do()
// 	if err != nil {
// 		return err
// 	}

// 	return nil
// }
