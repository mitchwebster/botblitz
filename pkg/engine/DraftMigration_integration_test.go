package engine

import (
	"io"
	"os"
	"path/filepath"
	"testing"

	"github.com/mitchwebster/botblitz/pkg/common"
	"github.com/mitchwebster/botblitz/pkg/gamestate"
)

func strptr(s string) *string { return &s }

// TestDraftPreservesPrebuiltPlayersData opens the *real* shipped season.db (built by the
// Python `build-season` step) for a draft and asserts the engine neither corrupts the
// prebuilt player data nor loses the ability to write roster assignments. A blanket
// AutoMigrate rebuilds that Python-built `players` table (VARCHAR -> text) and the rebuild
// NULLs out column data — so the engine must create only its league-state tables.
func TestDraftPreservesPrebuiltPlayersData(t *testing.T) {
	cwd, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	// The shipped season.db lives at the repo root (two levels up from pkg/engine).
	realDB := filepath.Join(cwd, "..", "..", "data", "game_states", "2025", "season.db")
	if _, err := os.Stat(realDB); err != nil {
		t.Skipf("shipped 2025 season.db not present (%v); skipping", err)
	}

	// Copy it to the engine-resolved path for a throwaway year, so the test never mutates
	// the tracked file.
	dir := filepath.Join(cwd, "data", "game_states", "2994")
	if err := os.MkdirAll(dir, os.ModePerm); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	if err := copyFile(realDB, filepath.Join(dir, "season.db")); err != nil {
		t.Fatalf("copy season.db: %v", err)
	}

	// Capture a known prebuilt value to compare after the draft setup.
	bots := []*common.Bot{
		{Id: "0", FantasyTeamName: "A", Owner: "A"},
		{Id: "1", FantasyTeamName: "B", Owner: "B"},
	}
	settings := BuildDefaultLeagueSettings(uint32(2994), 2)

	handler, err := gamestate.NewGameStateHandlerForDraft(bots, settings)
	if err != nil {
		t.Fatalf("NewGameStateHandlerForDraft: %v", err)
	}

	// Prebuilt ranks must survive (Ja'Marr Chase, id 19788, is rank 1 in the shipped pool).
	var rank *int
	if err := handler.GetDB().Raw(`SELECT rank FROM players WHERE id = '19788'`).Row().Scan(&rank); err != nil {
		t.Fatalf("scan rank: %v", err)
	}
	if rank == nil || *rank != 1 {
		t.Fatalf("expected prebuilt rank 1 to survive the draft setup, got %v", rank)
	}

	// And the engine must still WRITE the draft assignment to the player row.
	if err := handler.UpdatePlayer("19788", nil, nil, strptr("0")); err != nil {
		t.Fatalf("UpdatePlayer (draft write): %v", err)
	}
	var botID *string
	if err := handler.GetDB().Raw(`SELECT current_bot_id FROM players WHERE id = '19788'`).Row().Scan(&botID); err != nil {
		t.Fatalf("scan current_bot_id: %v", err)
	}
	if botID == nil || *botID != "0" {
		t.Fatalf("expected current_bot_id '0' written, got %v", botID)
	}
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	return out.Close()
}
