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

	botRankingMap := map[string]BotRanking{
		"bot1": BotRanking{
			Ranking:      1,
			TotalPoints:  10,
			MatchupsWon:  1,
			MatchupsLost: 0,
		},
		"bot2": BotRanking{
			Ranking:      2,
			TotalPoints:  5,
			MatchupsWon:  0,
			MatchupsLost: 1,
		},
	}

	// Setup add/drop selections
	botSelectionMap := map[string][]*common.WaiverClaim{
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
	winningClaims := performFAABAddDropInternal(bots, botSelectionMap, botRankingMap)

	// Validate the results
	winningClaimsArr, exists := winningClaims["bot2"]
	if !exists {
		t.Error("Expected winning claims to be non-empty for bot2")
	}

	for i, v := range winningClaimsArr {
		print(i, v)
	}

	if len(winningClaimsArr) != 1 {
		t.Error("Failed assertion: exactly one winning player claim from bot2")
	}

	if winningClaimsArr[0].BidAmount != 60 {
		t.Error("Failed assertion: winning bid amount from bot2 is not 60")
	}

	if winningClaimsArr[0].PlayerToDropId != "playerB" {
		t.Error("Failed assertion: dropped player should be playerB")
	}
}

func TestFAABWithCompetingBids(t *testing.T) {
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 100},
		{ID: "bot2", RemainingWaiverBudget: 100},
		{ID: "bot3", RemainingWaiverBudget: 100},
	}

	botRankingMap := map[string]BotRanking{
		"bot1": BotRanking{
			Ranking:      1,
			TotalPoints:  10,
			MatchupsWon:  1,
			MatchupsLost: 0,
		},
		"bot2": BotRanking{
			Ranking:      2,
			TotalPoints:  5,
			MatchupsWon:  0,
			MatchupsLost: 1,
		},
		"bot3": BotRanking{
			Ranking:      3,
			TotalPoints:  5,
			MatchupsWon:  0,
			MatchupsLost: 1,
		},
	}

	botSelectionMap := map[string][]*common.WaiverClaim{
		"bot1": {{PlayerToDropId: "playerA", PlayerToAddId: "playerX", BidAmount: 50}},
		"bot2": {{PlayerToDropId: "playerB", PlayerToAddId: "playerX", BidAmount: 60}},
		"bot3": {{PlayerToDropId: "playerC", PlayerToAddId: "playerX", BidAmount: 40}},
	}

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap, botRankingMap)

	// Bot2 should win with highest bid
	if claim, exists := winningClaims["bot2"]; !exists || len(claim) != 1 || claim[0].BidAmount != 60 {
		t.Errorf("Expected bot2 to win playerX with bid 60")
	}

	// Check correct player was dropped
	if claim := winningClaims["bot2"]; claim[0].PlayerToDropId != "playerB" {
		t.Errorf("Expected playerB to be dropped, got %s", claim[0].PlayerToDropId)
	}
}

func TestFAABWithInsufficientBudget(t *testing.T) {
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 30}, // Not enough budget
		{ID: "bot2", RemainingWaiverBudget: 100},
	}

	botRankingMap := map[string]BotRanking{
		"bot1": BotRanking{
			Ranking:      1,
			TotalPoints:  10,
			MatchupsWon:  1,
			MatchupsLost: 0,
		},
		"bot2": BotRanking{
			Ranking:      2,
			TotalPoints:  5,
			MatchupsWon:  0,
			MatchupsLost: 1,
		},
	}

	botSelectionMap := map[string][]*common.WaiverClaim{
		"bot1": {{PlayerToDropId: "playerA", PlayerToAddId: "playerX", BidAmount: 50}},
		"bot2": {{PlayerToDropId: "playerB", PlayerToAddId: "playerX", BidAmount: 40}},
	}

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap, botRankingMap)

	// Bot2 should win despite lower bid since bot1 has insufficient funds
	claim, exists := winningClaims["bot2"]
	if !exists || len(claim) != 1 || claim[0].BidAmount != 40 {
		t.Errorf("Expected bot2 to win playerX with bid 40")
	}
	if claim[0].PlayerToDropId != "playerB" {
		t.Errorf("Expected playerB to be dropped, got %s", claim[0].PlayerToDropId)
	}
}

func TestFAABWithMultiplePlayerClaims(t *testing.T) {
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 100},
		{ID: "bot2", RemainingWaiverBudget: 100},
	}

	botRankingMap := map[string]BotRanking{
		"bot1": BotRanking{
			Ranking:      1,
			TotalPoints:  10,
			MatchupsWon:  1,
			MatchupsLost: 0,
		},
		"bot2": BotRanking{
			Ranking:      2,
			TotalPoints:  5,
			MatchupsWon:  0,
			MatchupsLost: 1,
		},
	}

	botSelectionMap := map[string][]*common.WaiverClaim{
		"bot1": {
			{PlayerToDropId: "playerA", PlayerToAddId: "playerX", BidAmount: 50},
			{PlayerToDropId: "playerB", PlayerToAddId: "playerY", BidAmount: 30},
		},
		"bot2": {
			{PlayerToDropId: "playerC", PlayerToAddId: "playerX", BidAmount: 40},
			{PlayerToDropId: "playerD", PlayerToAddId: "playerY", BidAmount: 35},
		},
	}

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap, botRankingMap)

	// Check bot1 wins playerX with correct drop
	claim, exists := winningClaims["bot1"]
	if !exists || len(claim) != 1 || claim[0].BidAmount != 50 {
		t.Errorf("Expected bot1 to win playerX with bid 50")
	}
	if claim[0].PlayerToDropId != "playerA" {
		t.Errorf("Expected playerA to be dropped for playerX, got %s", claim[0].PlayerToDropId)
	}

	// Check bot2 wins playerY with correct drop
	claim, exists = winningClaims["bot2"]
	if !exists || len(claim) != 1 || claim[0].BidAmount != 35 {
		t.Errorf("Expected bot2 to win playerY with bid 35")
	}
	if claim[0].PlayerToDropId != "playerD" {
		t.Errorf("Expected playerD to be dropped for playerY, got %s", claim[0].PlayerToDropId)
	}
}

func TestFAABWithMultiplePlayerClaimsWithLowPriorities(t *testing.T) {
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 100},
		{ID: "bot2", RemainingWaiverBudget: 100},
	}

	botRankingMap := map[string]BotRanking{
		"bot1": BotRanking{
			Ranking:      1,
			TotalPoints:  10,
			MatchupsWon:  1,
			MatchupsLost: 0,
		},
		"bot2": BotRanking{
			Ranking:      2,
			TotalPoints:  5,
			MatchupsWon:  0,
			MatchupsLost: 1,
		},
	}

	botSelectionMap := map[string][]*common.WaiverClaim{
		"bot1": {
			{PlayerToDropId: "playerA", PlayerToAddId: "playerX", BidAmount: 30},
			{PlayerToDropId: "playerB", PlayerToAddId: "playerY", BidAmount: 50},
		},
		"bot2": {
			{PlayerToDropId: "playerC", PlayerToAddId: "playerX", BidAmount: 40},
			{PlayerToDropId: "playerD", PlayerToAddId: "playerY", BidAmount: 35},
		},
	}

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap, botRankingMap)

	// Check bot2 wins playerX with correct drop
	claim, exists := winningClaims["bot2"]
	if !exists || len(claim) != 1 || claim[0].BidAmount != 40 {
		t.Errorf("Expected bot2 to win playerX with bid 40")
	}
	if claim[0].PlayerToDropId != "playerC" {
		t.Errorf("Expected playerC to be dropped for playerX, got %s", claim[0].PlayerToDropId)
	}

	// Check bot1 wins playerY with correct drop
	claim, exists = winningClaims["bot1"]
	if !exists || len(claim) != 1 || claim[0].BidAmount != 50 {
		t.Errorf("Expected bot1 to win playerY with bid 50")
	}
	if claim[0].PlayerToDropId != "playerB" {
		t.Errorf("Expected playerB to be dropped for playerY, got %s", claim[0].PlayerToDropId)
	}
}

func TestFAABWithMultiplePlayerClaimsWithManyBotsAndPlayers(t *testing.T) {
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 100},
		{ID: "bot2", RemainingWaiverBudget: 100},
		{ID: "bot3", RemainingWaiverBudget: 100},
		{ID: "bot4", RemainingWaiverBudget: 100},
	}

	botRankingMap := map[string]BotRanking{
		"bot1": BotRanking{
			Ranking:      1,
			TotalPoints:  10,
			MatchupsWon:  2,
			MatchupsLost: 0,
		},
		"bot2": BotRanking{
			Ranking:      2,
			TotalPoints:  5,
			MatchupsWon:  2,
			MatchupsLost: 0,
		},
		"bot3": BotRanking{
			Ranking:      3,
			TotalPoints:  5,
			MatchupsWon:  1,
			MatchupsLost: 1,
		},
		"bot4": BotRanking{
			Ranking:      4,
			TotalPoints:  5,
			MatchupsWon:  1,
			MatchupsLost: 1,
		},
	}

	botSelectionMap := map[string][]*common.WaiverClaim{
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

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap, botRankingMap)

	// Check bot3 wins playerX with correct drop
	claim, exists := winningClaims["bot3"]
	if !exists || len(claim) != 1 || claim[0].BidAmount != 70 {
		t.Errorf("Expected bot3 to win playerX with bid 70")
	}
	if claim[0].PlayerToDropId != "playerE" {
		t.Errorf("Expected playerE to be dropped for playerX, got %s", claim[0].PlayerToDropId)
	}

	// Check bot3 wins playerY with correct drop
	claim, exists = winningClaims["bot1"]
	if !exists || len(claim) != 1 || claim[0].BidAmount != 50 {
		t.Errorf("Expected bot1 to win playerY with bid 50")
	}
	if claim[0].PlayerToDropId != "playerB" {
		t.Errorf("Expected playerB to be dropped for playerY, got %s", claim[0].PlayerToDropId)
	}

	// Check bot4 wins playerV with correct drop
	claim, exists = winningClaims["bot4"]
	if !exists || len(claim) != 1 || claim[0].BidAmount != 60 {
		t.Errorf("Expected bot4 to win playerV with bid 60")
	}
	if claim[0].PlayerToDropId != "playerH" {
		t.Errorf("Expected playerH to be dropped for playerV, got %s", claim[0].PlayerToDropId)
	}
}

func TestFAABWithTiedBids(t *testing.T) {
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 100},
		{ID: "bot2", RemainingWaiverBudget: 100},
		{ID: "bot3", RemainingWaiverBudget: 100},
		{ID: "bot4", RemainingWaiverBudget: 100},
	}

	botRankingMap := map[string]BotRanking{
		"bot1": BotRanking{
			Ranking:      1,
			TotalPoints:  10,
			MatchupsWon:  2,
			MatchupsLost: 0,
		},
		"bot2": BotRanking{
			Ranking:      2,
			TotalPoints:  5,
			MatchupsWon:  2,
			MatchupsLost: 0,
		},
		"bot3": BotRanking{
			Ranking:      3,
			TotalPoints:  5,
			MatchupsWon:  1,
			MatchupsLost: 1,
		},
		"bot4": BotRanking{
			Ranking:      4,
			TotalPoints:  5,
			MatchupsWon:  1,
			MatchupsLost: 1,
		},
	}

	botSelectionMap := map[string][]*common.WaiverClaim{
		"bot1": {{PlayerToDropId: "playerA", PlayerToAddId: "playerX", BidAmount: 50}}, // Same bid amount
		"bot2": {{PlayerToDropId: "playerB", PlayerToAddId: "playerX", BidAmount: 50}}, // Same bid amount
		"bot3": {{PlayerToDropId: "playerC", PlayerToAddId: "playerX", BidAmount: 30}},
		"bot4": {{PlayerToDropId: "playerC", PlayerToAddId: "playerX", BidAmount: 50}}, // Same bid amount
	}

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap, botRankingMap)

	// Bot4 should win with equal bid but higher priority (lower ranking)
	claim, exists := winningClaims["bot4"]
	if !exists || len(claim) != 1 || claim[0].BidAmount != 50 {
		t.Errorf("Expected bot4 to win playerX with bid 50 due to higher waiver priority")
	}

	// Check correct player was dropped
	if claim[0].PlayerToDropId != "playerC" {
		t.Errorf("Expected playerC to be dropped, got %s", claim[0].PlayerToDropId)
	}
}

func TestFAABWithRepeatedClaimsFromSameBot(t *testing.T) {
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 100},
		{ID: "bot2", RemainingWaiverBudget: 100},
	}

	botRankingMap := map[string]BotRanking{
		"bot1": BotRanking{
			Ranking:      1,
			TotalPoints:  10,
			MatchupsWon:  1,
			MatchupsLost: 0,
		},
		"bot2": BotRanking{
			Ranking:      2,
			TotalPoints:  5,
			MatchupsWon:  0,
			MatchupsLost: 1,
		},
	}

	botSelectionMap := map[string][]*common.WaiverClaim{
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

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap, botRankingMap)

	// Should use bot1's highest bid (50)
	claim, exists := winningClaims["bot1"]
	if !exists || len(claim) != 1 || claim[0].BidAmount != 50 {
		t.Errorf("Expected bot1 to win playerX with highest bid of 50")
	}

	// Check correct player was dropped (should be playerB associated with highest bid)
	if claim[0].PlayerToDropId != "playerB" {
		t.Errorf("Expected playerB to be dropped, got %s", claim[0].PlayerToDropId)
	}
}

func TestFAABWithOneBotWinningMultipleTimes(t *testing.T) {
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 100},
		{ID: "bot2", RemainingWaiverBudget: 100},
	}

	botRankingMap := map[string]BotRanking{
		"bot1": BotRanking{
			Ranking:      1,
			TotalPoints:  10,
			MatchupsWon:  1,
			MatchupsLost: 0,
		},
		"bot2": BotRanking{
			Ranking:      2,
			TotalPoints:  5,
			MatchupsWon:  0,
			MatchupsLost: 1,
		},
	}

	botSelectionMap := map[string][]*common.WaiverClaim{
		"bot1": {
			// Multiple claims for same player with different amounts and drop players
			{PlayerToDropId: "playerA", PlayerToAddId: "playerX", BidAmount: 30},
			{PlayerToDropId: "playerB", PlayerToAddId: "playerY", BidAmount: 20},
		},
		"bot2": {
			{PlayerToDropId: "playerD", PlayerToAddId: "playerX", BidAmount: 10},
		},
	}

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap, botRankingMap)

	// Should use bot1's highest bid (50)
	claims, exists := winningClaims["bot1"]
	if !exists || len(claims) != 2 {
		t.Errorf("Expected bot1 to win twice")
	}

	correctMatches := 0
	for _, claim := range claims {
		if claim.PlayerToAddId == "playerX" && claim.PlayerToDropId == "playerA" && claim.BidAmount == 30 {
			correctMatches++
		}

		if claim.PlayerToAddId == "playerY" && claim.PlayerToDropId == "playerB" && claim.BidAmount == 20 {
			correctMatches++
		}
	}

	if correctMatches != 2 {
		t.Errorf("Failed to get correct matches for bot1, got %d", correctMatches)
	}
}

func TestFAABWithRepeatedClaimsAndMultiplePlayers(t *testing.T) {
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 100},
		{ID: "bot2", RemainingWaiverBudget: 100},
	}

	botRankingMap := map[string]BotRanking{
		"bot1": BotRanking{
			Ranking:      1,
			TotalPoints:  10,
			MatchupsWon:  1,
			MatchupsLost: 0,
		},
		"bot2": BotRanking{
			Ranking:      2,
			TotalPoints:  5,
			MatchupsWon:  0,
			MatchupsLost: 1,
		},
	}

	botSelectionMap := map[string][]*common.WaiverClaim{
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

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap, botRankingMap)

	// Check playerX goes to bot1 with highest bid
	if claim, exists := winningClaims["bot1"]; !exists || len(claim) != 1 || claim[0].BidAmount != 50 {
		t.Errorf("Expected bot1 to win playerX with bid 50")
	}

	// Check playerY goes to bot2
	if claim, exists := winningClaims["bot2"]; !exists || len(claim) != 1 || claim[0].BidAmount != 60 {
		t.Errorf("Expected bot2 to win playerY with bid 60")
	}

	// Verify correct players were dropped
	if winningClaims["bot1"][0].PlayerToDropId != "playerB" {
		t.Error("Expected playerB to be dropped for winning playerX claim")
	}
	if winningClaims["bot2"][0].PlayerToDropId != "playerE" {
		t.Error("Expected playerE to be dropped for winning playerY claim")
	}
}

func TestBugReport(t *testing.T) {
	bots := []gamestate.Bot{
		{ID: "bot1", RemainingWaiverBudget: 100},
		{ID: "bot2", RemainingWaiverBudget: 100},
	}

	botRankingMap := map[string]BotRanking{
		"bot1": BotRanking{
			Ranking:      1,
			TotalPoints:  10,
			MatchupsWon:  1,
			MatchupsLost: 0,
		},
		"bot2": BotRanking{
			Ranking:      2,
			TotalPoints:  5,
			MatchupsWon:  0,
			MatchupsLost: 1,
		},
	}

	botSelectionMap := map[string][]*common.WaiverClaim{
		"bot1": {
			// Multiple claims for playerX
			{PlayerToDropId: "playerM", PlayerToAddId: "playerN", BidAmount: 0},
			{PlayerToDropId: "playerB", PlayerToAddId: "playerA", BidAmount: 15},
			{PlayerToDropId: "playerB", PlayerToAddId: "playerC", BidAmount: 10},
			{PlayerToDropId: "playerB", PlayerToAddId: "playerD", BidAmount: 5},
		},
		"bot2": {
			// Competing for both players
			{PlayerToDropId: "playerG", PlayerToAddId: "playerA", BidAmount: 50},
			{PlayerToDropId: "playerE", PlayerToAddId: "playerY", BidAmount: 60},
		},
	}

	winningClaims := performFAABAddDropInternal(bots, botSelectionMap, botRankingMap)

	// Check playerX goes to bot1 with highest bid
	if claims, exists := winningClaims["bot1"]; !exists || len(claims) != 2 || claims[0].BidAmount != 0 || claims[1].BidAmount != 10 {
		t.Errorf("Expected bot1 to win playerX with bid 50")
	}

	// Check playerY goes to bot2
	if claim, exists := winningClaims["bot2"]; !exists || len(claim) != 1 || claim[0].BidAmount != 50 {
		t.Errorf("Expected bot2 to win playerY with bid 60")
	}
}
