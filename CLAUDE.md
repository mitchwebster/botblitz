# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BotBlitz is a fantasy football automation platform that runs bot competitions using Go and Python. It supports two game modes: Draft simulations and Weekly Fantasy management. The system uses gRPC for communication between a Go engine and Python bots.

## Architecture

- **Go Engine** (`pkg/`): Core game engine handling draft logic, bot management, and game state
  - `engine/` - Main bot engine, draft handlers, weekly fantasy logic
  - `common/` - Shared protobuf definitions and validation logic
  - `cmd/` - Entry point bootstrap for running competitions
- **Python Bots** (`bots/nfl/`): Individual bot implementations that make fantasy decisions
- **gRPC Communication**: Protobuf-defined interface between Go engine and Python bots
- **Data Pipeline**: NFL player data fetching and ranking system

## Common Development Commands

```bash
# Generate protobuf classes from proto definitions
make gen

# Clean generated protobuf files
make clean

# Run tests across all Go modules
make test

# Run draft simulation
make run-draft

# Run weekly fantasy competition
make run-fantasy

# Build Python module (requires make gen first)
make build-py-module

# Build Docker container for Python server
make build-docker

# Debug Docker container
make debug-docker
```

## Key Components

### Game Engine (`pkg/engine/bot_engine.go`)
Central orchestrator that:
- Manages bot lifecycle and source code loading
- Executes draft rounds or weekly fantasy cycles
- Handles gRPC communication with Python bots
- Integrates with Google Sheets for results tracking

### Bot Configuration (`pkg/cmd/engine_bootstrap.go`)
Defines fantasy teams and bot mappings. Bot source paths reference files in `bots/nfl/`.

### Protocol Definitions (`pkg/common/proto/agent.proto`)
gRPC service defining:
- `DraftPlayer` - Bot selects a player during draft
- `SubmitFantasyActions` - Bot proposes add/drop transactions
- Game state structure with players, teams, and league settings

### Python Bot Interface
Bots must implement:
- `draft_player(game_state: GameState) -> str` - Return player ID to draft
- `propose_add_drop(game_state: GameState) -> AddDropSelection` - Return add/drop proposal

### Development Workflow

1. **Protocol Changes**: Modify `agent.proto`, run `make gen` to regenerate bindings
2. **Bot Development**: Create new bots in `bots/nfl/`, update bot list in `engine_bootstrap.go`
3. **Engine Changes**: Modify Go engine code, run `make test` to validate
4. **Data Updates**: Player rankings stored in `player_ranks_YYYY.csv` files

### Testing

- Go tests: `make test` runs across all modules with proper module management
- Bot validation occurs during engine startup
- Draft simulations can be run locally for testing bot behavior

### Data Sources

- NFL player data fetched via `nfl-data-py` Python library
- Player rankings stored as CSV files by year
- Game states serialized and stored in `data/game_states/`