#!/usr/bin/env node
/**
 * `npm run start:local [-- <path-to-season.db>]`
 *
 * Copies the given season.db into public/season.db and starts the dev
 * server pointed at it (REACT_APP_USE_LOCAL_DB=true). Defaults to the live
 * 2026 season.db when no path is given.
 *
 * The path can be a mock draft's scratch copy (see harness.cli_draft's
 * printed instructions, e.g. data/mock_drafts/2025/season.db) or any other
 * year's tracked season.db -- anything sql.js can open.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const uxDir = path.join(__dirname, "..");
const repoRoot = path.join(uxDir, "..");

const argPath = process.argv[2];
const srcAbsolute = argPath
  ? path.resolve(uxDir, argPath)
  : path.join(repoRoot, "data", "game_states", "2026", "season.db");

if (!fs.existsSync(srcAbsolute)) {
  console.error(`No such file: ${srcAbsolute}`);
  process.exit(1);
}

const dest = path.join(uxDir, "public", "season.db");
fs.copyFileSync(srcAbsolute, dest);

const displayPath = path.relative(repoRoot, srcAbsolute);
console.log(`Copied ${displayPath} -> ux/public/season.db`);

const result = spawnSync("react-scripts", ["start"], {
  stdio: "inherit",
  shell: true,
  cwd: uxDir,
  env: {
    ...process.env,
    REACT_APP_USE_LOCAL_DB: "true",
    REACT_APP_DB_SOURCE: displayPath,
  },
});

process.exit(result.status == null ? 0 : result.status);
