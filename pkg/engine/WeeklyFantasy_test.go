package engine

import (
	"testing"

	"github.com/mitchwebster/botblitz/pkg/common"
	"github.com/mitchwebster/botblitz/pkg/gamestate"
)

func TestPerformFAABAddDropInternal(t *testing.T) {
	// Setup bots
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 100},
		{ID: "bot2", RemainingWaiverBudget: 100},
	}

	// Setup add/drop selections
	botSelectionMap := map[string][]*common.AddDropSelection{
		"bot1": {
			{
				PlayerToDropId: "playerA",
				PlayerToAddId:  "playerX",
				BidAmount:      50,
			},
		},
		"bot2": {
			{
				PlayerToDropId: "playerB",
				PlayerToAddId:  "playerX",
				BidAmount:      60,
			},
		},
	}

	// Call the function under test
	winningClaims := performFAABAddDropInternal(bots, botSelectionMap)

	// Validate the results
	winningClaimMap, exists := winningClaims["playerX"]
	if !exists {
		t.Error("Expected winning claims to be non-empty for playerX")
	}

	winningClaim, exists := winningClaimMap["bot2"]
	if !exists || len(winningClaimMap) != 1 {
		t.Error("Failed assertion: exactly one winning player claim from bot2")
	}

	if winningClaim.BidAmount != 60 {
		t.Error("Failed assertion: winning bid amount from bot2 is not 60")
	}

	if winningClaim.PlayerToDropId != "playerB" {
		t.Error("Failed assertion: dropped player should be playerB")
	}
}

func TestFAABWithCompetingBids(t *testing.T) {
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 100},
		{ID: "bot2", RemainingWaiverBudget: 100},
		{ID: "bot3", RemainingWaiverBudget: 100},
	}

	botSelectionMap := map[string][]*common.AddDropSelection{
		"bot1": {{PlayerToDropId: "playerA", PlayerToAddId: "playerX", BidAmount: 50}},
		"bot2": {{PlayerToDropId: "playerB", PlayerToAddId: "playerX", BidAmount: 60}},
		"bot3": {{PlayerToDropId: "playerC", PlayerToAddId: "playerX", BidAmount: 40}},
	}

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap)

	// Bot2 should win with highest bid
	if claim, exists := winningClaims["playerX"]["bot2"]; !exists || claim.BidAmount != 60 {
		t.Errorf("Expected bot2 to win playerX with bid 60")
	}

	// Check correct player was dropped
	if claim := winningClaims["playerX"]["bot2"]; claim.PlayerToDropId != "playerB" {
		t.Errorf("Expected playerB to be dropped, got %s", claim.PlayerToDropId)
	}
}

func TestFAABWithInsufficientBudget(t *testing.T) {
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 30}, // Not enough budget
		{ID: "bot2", RemainingWaiverBudget: 100},
	}

	botSelectionMap := map[string][]*common.AddDropSelection{
		"bot1": {{PlayerToDropId: "playerA", PlayerToAddId: "playerX", BidAmount: 50}},
		"bot2": {{PlayerToDropId: "playerB", PlayerToAddId: "playerX", BidAmount: 40}},
	}

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap)

	// Bot2 should win despite lower bid since bot1 has insufficient funds
	claim, exists := winningClaims["playerX"]["bot2"]
	if !exists || claim.BidAmount != 40 {
		t.Errorf("Expected bot2 to win playerX with bid 40")
	}
	if claim.PlayerToDropId != "playerB" {
		t.Errorf("Expected playerB to be dropped, got %s", claim.PlayerToDropId)
	}
}

func TestFAABWithMultiplePlayerClaims(t *testing.T) {
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 100},
		{ID: "bot2", RemainingWaiverBudget: 100},
	}

	botSelectionMap := map[string][]*common.AddDropSelection{
		"bot1": {
			{PlayerToDropId: "playerA", PlayerToAddId: "playerX", BidAmount: 50},
			{PlayerToDropId: "playerB", PlayerToAddId: "playerY", BidAmount: 30},
		},
		"bot2": {
			{PlayerToDropId: "playerC", PlayerToAddId: "playerX", BidAmount: 40},
			{PlayerToDropId: "playerD", PlayerToAddId: "playerY", BidAmount: 35},
		},
	}

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap)

	// Check bot1 wins playerX with correct drop
	claim, exists := winningClaims["playerX"]["bot1"]
	if !exists || claim.BidAmount != 50 {
		t.Errorf("Expected bot1 to win playerX with bid 50")
	}
	if claim.PlayerToDropId != "playerA" {
		t.Errorf("Expected playerA to be dropped for playerX, got %s", claim.PlayerToDropId)
	}

	// Check bot2 wins playerY with correct drop
	claim, exists = winningClaims["playerY"]["bot2"]
	if !exists || claim.BidAmount != 35 {
		t.Errorf("Expected bot2 to win playerY with bid 35")
	}
	if claim.PlayerToDropId != "playerD" {
		t.Errorf("Expected playerD to be dropped for playerY, got %s", claim.PlayerToDropId)
	}
}

func TestFAABWithMultiplePlayerClaimsWithLowPriorities(t *testing.T) {
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 100},
		{ID: "bot2", RemainingWaiverBudget: 100},
	}

	botSelectionMap := map[string][]*common.AddDropSelection{
		"bot1": {
			{PlayerToDropId: "playerA", PlayerToAddId: "playerX", BidAmount: 30},
			{PlayerToDropId: "playerB", PlayerToAddId: "playerY", BidAmount: 50},
		},
		"bot2": {
			{PlayerToDropId: "playerC", PlayerToAddId: "playerX", BidAmount: 40},
			{PlayerToDropId: "playerD", PlayerToAddId: "playerY", BidAmount: 35},
		},
	}

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap)

	// Check bot2 wins playerX with correct drop
	claim, exists := winningClaims["playerX"]["bot2"]
	if !exists || claim.BidAmount != 40 {
		t.Errorf("Expected bot2 to win playerX with bid 40")
	}
	if claim.PlayerToDropId != "playerC" {
		t.Errorf("Expected playerC to be dropped for playerX, got %s", claim.PlayerToDropId)
	}

	// Check bot1 wins playerY with correct drop
	claim, exists = winningClaims["playerY"]["bot1"]
	if !exists || claim.BidAmount != 50 {
		t.Errorf("Expected bot1 to win playerY with bid 50")
	}
	if claim.PlayerToDropId != "playerB" {
		t.Errorf("Expected playerB to be dropped for playerY, got %s", claim.PlayerToDropId)
	}
}

func TestFAABWithMultiplePlayerClaimsWithManyBotsAndPlayers(t *testing.T) {
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 100},
		{ID: "bot2", RemainingWaiverBudget: 100},
		{ID: "bot3", RemainingWaiverBudget: 100},
		{ID: "bot4", RemainingWaiverBudget: 100},
	}

	botSelectionMap := map[string][]*common.AddDropSelection{
		"bot1": {
			{PlayerToDropId: "playerA", PlayerToAddId: "playerX", BidAmount: 30},
			{PlayerToDropId: "playerB", PlayerToAddId: "playerY", BidAmount: 50},
		},
		"bot2": {
			{PlayerToDropId: "playerC", PlayerToAddId: "playerX", BidAmount: 40},
			{PlayerToDropId: "playerD", PlayerToAddId: "playerY", BidAmount: 35},
		},
		"bot3": {
			{PlayerToDropId: "playerE", PlayerToAddId: "playerX", BidAmount: 70},
			{PlayerToDropId: "playerF", PlayerToAddId: "playerY", BidAmount: 55},
			{PlayerToDropId: "playerG", PlayerToAddId: "playerV", BidAmount: 30},
		},
		"bot4": {
			{PlayerToDropId: "playerH", PlayerToAddId: "playerV", BidAmount: 60},
		},
	}

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap)

	// Check bot3 wins playerX with correct drop
	claim, exists := winningClaims["playerX"]["bot3"]
	if !exists || claim.BidAmount != 70 {
		t.Errorf("Expected bot3 to win playerX with bid 70")
	}
	if claim.PlayerToDropId != "playerE" {
		t.Errorf("Expected playerE to be dropped for playerX, got %s", claim.PlayerToDropId)
	}

	// Check bot3 wins playerY with correct drop
	claim, exists = winningClaims["playerY"]["bot1"]
	if !exists || claim.BidAmount != 50 {
		t.Errorf("Expected bot1 to win playerY with bid 50")
	}
	if claim.PlayerToDropId != "playerB" {
		t.Errorf("Expected playerB to be dropped for playerY, got %s", claim.PlayerToDropId)
	}

	// Check bot4 wins playerV with correct drop
	claim, exists = winningClaims["playerV"]["bot4"]
	if !exists || claim.BidAmount != 60 {
		t.Errorf("Expected bot4 to win playerV with bid 60")
	}
	if claim.PlayerToDropId != "playerH" {
		t.Errorf("Expected playerH to be dropped for playerV, got %s", claim.PlayerToDropId)
	}
}

func TestFAABWithTiedBids(t *testing.T) {
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 100},
		{ID: "bot2", RemainingWaiverBudget: 100},
		{ID: "bot3", RemainingWaiverBudget: 100},
		{ID: "bot4", RemainingWaiverBudget: 100},
	}

	botSelectionMap := map[string][]*common.AddDropSelection{
		"bot1": {{PlayerToDropId: "playerA", PlayerToAddId: "playerX", BidAmount: 50}}, // Same bid amount
		"bot2": {{PlayerToDropId: "playerB", PlayerToAddId: "playerX", BidAmount: 50}}, // Same bid amount
		"bot3": {{PlayerToDropId: "playerC", PlayerToAddId: "playerX", BidAmount: 30}},
		"bot4": {{PlayerToDropId: "playerC", PlayerToAddId: "playerX", BidAmount: 50}}, // Same bid amount
	}

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap)

	// TODO: change once we fix tie break rules

	// Bot4 should win with equal bid but higher priority
	claim, exists := winningClaims["playerX"]["bot4"]
	if !exists || claim.BidAmount != 50 {
		t.Errorf("Expected bot4 to win playerX with bid 50 due to higher waiver priority")
	}

	// Check correct player was dropped
	if claim.PlayerToDropId != "playerC" {
		t.Errorf("Expected playerC to be dropped, got %s", claim.PlayerToDropId)
	}
}

func TestFAABWithRepeatedClaimsFromSameBot(t *testing.T) {
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 100},
		{ID: "bot2", RemainingWaiverBudget: 100},
	}

	botSelectionMap := map[string][]*common.AddDropSelection{
		"bot1": {
			// Multiple claims for same player with different amounts and drop players
			{PlayerToDropId: "playerA", PlayerToAddId: "playerX", BidAmount: 30},
			{PlayerToDropId: "playerB", PlayerToAddId: "playerX", BidAmount: 50},
			{PlayerToDropId: "playerC", PlayerToAddId: "playerX", BidAmount: 40},
		},
		"bot2": {
			{PlayerToDropId: "playerD", PlayerToAddId: "playerX", BidAmount: 45},
		},
	}

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap)

	// Should use bot1's highest bid (50)
	claim, exists := winningClaims["playerX"]["bot1"]
	if !exists || claim.BidAmount != 50 {
		t.Errorf("Expected bot1 to win playerX with highest bid of 50")
	}

	// Check correct player was dropped (should be playerB associated with highest bid)
	if claim.PlayerToDropId != "playerB" {
		t.Errorf("Expected playerB to be dropped, got %s", claim.PlayerToDropId)
	}
}

func TestFAABWithRepeatedClaimsAndMultiplePlayers(t *testing.T) {
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 100},
		{ID: "bot2", RemainingWaiverBudget: 100},
	}

	botSelectionMap := map[string][]*common.AddDropSelection{
		"bot1": {
			// Multiple claims for playerX
			{PlayerToDropId: "playerA", PlayerToAddId: "playerX", BidAmount: 30},
			{PlayerToDropId: "playerB", PlayerToAddId: "playerX", BidAmount: 50},
			// Also bidding on playerY
			{PlayerToDropId: "playerC", PlayerToAddId: "playerY", BidAmount: 40},
		},
		"bot2": {
			// Competing for both players
			{PlayerToDropId: "playerD", PlayerToAddId: "playerX", BidAmount: 45},
			{PlayerToDropId: "playerE", PlayerToAddId: "playerY", BidAmount: 60},
		},
	}

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap)

	// Check playerX goes to bot1 with highest bid
	if claim, exists := winningClaims["playerX"]["bot1"]; !exists || claim.BidAmount != 50 {
		t.Errorf("Expected bot1 to win playerX with bid 50")
	}

	// Check playerY goes to bot2
	if claim, exists := winningClaims["playerY"]["bot2"]; !exists || claim.BidAmount != 60 {
		t.Errorf("Expected bot2 to win playerY with bid 60")
	}

	// Verify correct players were dropped
	if winningClaims["playerX"]["bot1"].PlayerToDropId != "playerB" {
		t.Error("Expected playerB to be dropped for winning playerX claim")
	}
	if winningClaims["playerY"]["bot2"].PlayerToDropId != "playerE" {
		t.Error("Expected playerE to be dropped for winning playerY claim")
	}
}
