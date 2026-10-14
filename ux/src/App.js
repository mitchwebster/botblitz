import React, { useEffect, useState } from "react";

// Shared position color coding (draft board, players list, projections).
const POSITION_COLORS = {
  QB: "#3b6fd4",
  RB: "#1f9e6d",
  WR: "#d4732e",
  TE: "#8b5fc9",
  K: "#b8952e",
  DST: "#5b6470",
};

function PositionPill({ position }) {
  const color = POSITION_COLORS[position] || "#5b6470";
  return <span style={{ color, fontWeight: 700 }}>{position}</span>;
}

// weekly_stats' curated legacy-alias columns (see blitz_env/load_stats_nflreadr.py) --
// a player profile only shows whichever of these actually have a nonzero
// value for that player, so QBs get passing stats, DST gets defensive stats, etc.
const STAT_COLUMNS = [
  ["PASSING_CMP", "Cmp"], ["PASSING_ATT", "Pass Att"], ["PASSING_YDS", "Pass Yds"], ["PASSING_TD", "Pass TD"], ["PASSING_INT", "Int"],
  ["RUSHING_ATT", "Rush Att"], ["RUSHING_YDS", "Rush Yds"], ["RUSHING_TD", "Rush TD"],
  ["RECEIVING_REC", "Rec"], ["RECEIVING_TGT", "Tgt"], ["RECEIVING_YDS", "Rec Yds"], ["RECEIVING_TD", "Rec TD"],
  ["FL", "Fum Lost"],
  ["FG", "FG"], ["FGA", "FGA"], ["XPT", "XP"],
  ["SACK", "Sack"], ["INT", "Def Int"], ["FR", "FR"], ["TD", "Def TD"], ["SAFETY", "Saf"], ["PA", "Pts Allowed"],
];

// weekly_stats/weekly_injuries hold every year (2017-2026) for the same
// fantasypros_id -- a player profile has to scope to one season, or "week 18"
// from a past year silently collides with "week 18" of the current one.
const CURRENT_SEASON_YEAR = 2026; // fallback only, while dbYear is still resolving

function App() {
  const [db, setDb] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("current");
  // Title: "Botblitz - {dbSourceLabel} ({dbYear})" -- which file is actually
  // loaded, and its year read from the db itself rather than hardcoded.
  const [dbSourceLabel, setDbSourceLabel] = useState(null);
  const [dbYear, setDbYear] = useState(null);
  const [data, setData] = useState([]);
  const [columns, setColumns] = useState([]);
  const [sortColumn, setSortColumn] = useState(null);
  const [sortDirection, setSortDirection] = useState("asc");
  const [filterText, setFilterText] = useState("");
  const [selectedWeek, setSelectedWeek] = useState(null);
  const [currentWeek, setCurrentWeek] = useState(null);
  const [error, setError] = useState(null);
  // Projections tab: independent week/source filters + position checkboxes,
  // populated from whatever's actually in external_projections (not tied to
  // game_statuses.current_fantasy_week, which only tracks league progress).
  const [projWeeks, setProjWeeks] = useState([]);
  const [projSources, setProjSources] = useState([]);
  const [projWeek, setProjWeek] = useState(null);
  const [projSource, setProjSource] = useState(null);
  const [projPositions, setProjPositions] = useState(["QB", "RB", "WR", "TE", "K", "DST"]);
  // Players tab: reuses projWeek/projSource above for "as of which week/source"
  // (projected points + injuries are both week-scoped), plus its own search
  // and actual-vs-projected toggle.
  const [playerSearch, setPlayerSearch] = useState("");
  const [pointsMode, setPointsMode] = useState("actual"); // 'actual' | 'projected'
  const [playerPositions, setPlayerPositions] = useState(["QB", "RB", "WR", "TE", "K", "DST"]);
  // Player profile modal: click a player anywhere to see their full weekly
  // history -- actual points + stat line + all three projection sources.
  const [profilePlayer, setProfilePlayer] = useState(null); // { id, name }
  const [profileRows, setProfileRows] = useState([]);
  // weekly_stats' actual column set varies by db (older archived snapshots use
  // different legacy names, e.g. DEF_TD/SFTY instead of TD/SAFETY, and some
  // lack PA entirely) -- discovered at load time so the profile query only
  // ever asks for columns that actually exist.
  const [weeklyStatsCols, setWeeklyStatsCols] = useState(null);
  const [profileByeWeek, setProfileByeWeek] = useState(null);
  // Theme: 'light' | 'dark' | 'system'
  const [themePref, setThemePref] = useState(() => {
    try {
      return localStorage.getItem("themePref") || "system";
    } catch (e) {
      return "system";
    }
  });
  const [systemDark, setSystemDark] = useState(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  });

  // effective theme is what we actually apply
  const effectiveTheme = themePref === "system" ? (systemDark ? "dark" : "light") : themePref;

  useEffect(() => {
    const loadDb = async () => {
      try {
        if (!window.initSqlJs) {
          await new Promise((resolve, reject) => {
            const script = document.createElement("script");
            script.src =
              "https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.13.0/sql-wasm.js";
            script.onload = resolve;
            script.onerror = reject;
            document.body.appendChild(script);
          });
        }

        const SQL = await window.initSqlJs({
          locateFile: (file) =>
            `https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.13.0/${file}`,
        });

        // Check if we should use local database (set via REACT_APP_USE_LOCAL_DB env var)
        const useLocalDb = process.env.REACT_APP_USE_LOCAL_DB === 'true';
        
        let dbUrl;
        if (useLocalDb) {
          // Use local database from public folder. Whichever file
          // `npm run start:local -- <path>` copied in (scripts/start-local.js)
          // is recorded in REACT_APP_DB_SOURCE for display -- defaults to the
          // live 2026 season.db when started with no path.
          dbUrl = '/season.db';
          setDbSourceLabel(process.env.REACT_APP_DB_SOURCE || 'data/game_states/2026/season.db');
        } else {
          // Get branch from URL parameter (e.g., ?branch=chris-bot-add-drop)
          // Defaults to 'main' if not specified
          const urlParams = new URLSearchParams(window.location.search);
          const branch = urlParams.get('branch') || 'main';
          const dbPath = 'data/game_states/2026/season.db';
          dbUrl = `https://raw.githubusercontent.com/mitchwebster/botblitz/${branch}/${dbPath}`;
          setDbSourceLabel(branch === 'main' ? dbPath : `${dbPath} (${branch})`);
        }

        const response = await fetch(dbUrl);
        const buffer = await response.arrayBuffer();
        const dbInstance = new SQL.Database(new Uint8Array(buffer));
        setDb(dbInstance);
        setLoading(false);
      } catch (err) {
        console.error("Failed to load DB:", err);
        setLoading(false);
      }
    };

    loadDb();
  }, []);

  // Fetch and initialize current week
  useEffect(() => {
    if (!db) return;

    try {
      const result = db.exec(
        "SELECT current_fantasy_week FROM game_statuses LIMIT 1;"
      );
      if (result.length > 0 && result[0].values.length > 0) {
        const week = result[0].values[0][0];
        setCurrentWeek(week);
        if (selectedWeek === null) {
          setSelectedWeek(week);
        }
      }
    } catch (err) {
      console.error("Failed to fetch current week:", err);
    }
  }, [db, selectedWeek]);

  // Year for the title, computed from the db itself rather than hardcoded --
  // league_settings.year when a draft has actually been run, else whichever
  // year this db's reference data covers (a stats-only db with no league
  // state yet still has a real, computable year).
  useEffect(() => {
    if (!db) return;
    try {
      const result = db.exec("SELECT year FROM league_settings LIMIT 1;");
      if (result.length > 0 && result[0].values.length > 0) {
        setDbYear(result[0].values[0][0]);
        return;
      }
    } catch (err) {
      // league_settings doesn't exist on a pure stats/reference db -- fall through.
    }
    for (const table of ["external_projections", "weekly_stats"]) {
      try {
        const result = db.exec(`SELECT MAX(year) FROM ${table};`);
        if (result.length > 0 && result[0].values.length > 0 && result[0].values[0][0] != null) {
          setDbYear(result[0].values[0][0]);
          return;
        }
      } catch (err) {
        // table doesn't exist either -- try the next one.
      }
    }
  }, [db]);

  // Populate the Projections tab's week/source options from whatever rows
  // external_projections actually has (a table written by
  // blitz_env.collect_ffanalytics_projections, may not exist on an older db).
  useEffect(() => {
    if (!db) return;
    try {
      const tableCheck = db.exec("SELECT name FROM sqlite_master WHERE type='table' AND name='external_projections';");
      if (tableCheck.length === 0) return;

      const weeksResult = db.exec("SELECT DISTINCT week FROM external_projections ORDER BY week;");
      const weeks = weeksResult.length > 0 ? weeksResult[0].values.map((r) => r[0]) : [];
      setProjWeeks(weeks);
      if (weeks.length > 0) setProjWeek((prev) => (prev === null ? weeks[weeks.length - 1] : prev));

      const sourcesResult = db.exec("SELECT DISTINCT source FROM external_projections ORDER BY source;");
      const sources = sourcesResult.length > 0 ? sourcesResult[0].values.map((r) => r[0]) : [];
      setProjSources(sources);
      if (sources.length > 0) setProjSource((prev) => (prev === null ? sources[0] : prev));
    } catch (err) {
      console.error("Failed to fetch projection weeks/sources:", err);
    }
  }, [db]);

  // Discover which of weekly_stats' columns actually exist on this db --
  // schema varies between an archived snapshot and a freshly-built season.db.
  useEffect(() => {
    if (!db) return;
    try {
      const result = db.exec("PRAGMA table_info(weekly_stats);");
      const cols = result.length > 0 ? new Set(result[0].values.map((r) => r[1])) : new Set();
      setWeeklyStatsCols(cols);
    } catch (err) {
      console.error("Failed to inspect weekly_stats columns:", err);
      setWeeklyStatsCols(new Set());
    }
  }, [db]);

  // Player profile: full weekly history for whichever player was clicked --
  // actual points + raw stat line (weekly_stats) alongside every source's
  // projection for that same week (external_projections).
  useEffect(() => {
    if (!db || !profilePlayer || weeklyStatsCols === null) {
      if (!profilePlayer) {
        setProfileRows([]);
        setProfileByeWeek(null);
      }
      return;
    }
    // Each piece (stats, projections, injuries) can be missing or
    // schema-mismatched on any given db -- one failing shouldn't blank the
    // whole profile, so each gets its own try/catch instead of one shared one.
    const byWeek = {};
    let playerTeam = null;

    try {
      const byeResult = db.exec(`SELECT player_bye_week, professional_team FROM players WHERE id = '${profilePlayer.id}'`);
      if (byeResult.length > 0) {
        setProfileByeWeek(byeResult[0].values[0][0]);
        playerTeam = byeResult[0].values[0][1];
      } else {
        setProfileByeWeek(null);
      }
    } catch (err) {
      console.error("Failed to load player bye week:", err);
      setProfileByeWeek(null);
    }

    // Schedule covers every week of the season up front (unlike weekly_stats'
    // opponent, which only exists for games already played) -- this is what
    // lets future weeks show a real opponent, and also seeds byWeek with
    // every week 1-18 so the table isn't just "however many weeks have stats."
    if (playerTeam) {
      try {
        const schedResult = db.exec(`
          SELECT week, opponent FROM schedule
          WHERE team = '${playerTeam}' AND year = ${dbYear ?? CURRENT_SEASON_YEAR}
          ORDER BY week
        `);
        if (schedResult.length > 0) {
          for (const [week, opponent] of schedResult[0].values) {
            byWeek[week] = { week, actual: null, opponent, stats: {} };
          }
        }
      } catch (err) {
        console.error("Failed to load schedule:", err);
      }
    }

    try {
      const availableStatCols = STAT_COLUMNS.filter(([col]) => weeklyStatsCols.has(col));
      const statCols = availableStatCols.map(([col]) => `"${col}"`).join(", ");
      const statsResult = db.exec(`
        SELECT week, FPTS AS actual${statCols ? ", " + statCols : ""}
        FROM weekly_stats
        WHERE fantasypros_id = '${profilePlayer.id}' AND year = ${dbYear ?? CURRENT_SEASON_YEAR}
        ORDER BY week
      `);
      if (statsResult.length > 0) {
        const cols = statsResult[0].columns;
        for (const row of statsResult[0].values) {
          const obj = Object.fromEntries(row.map((v, i) => [cols[i], v]));
          const existingOpponent = byWeek[obj.week]?.opponent;
          byWeek[obj.week] = { week: obj.week, actual: obj.actual, opponent: existingOpponent, stats: obj };
        }
      }
    } catch (err) {
      console.error("Failed to load player weekly stats:", err);
    }

    try {
      const projResult = db.exec(`
        SELECT week, source, points
        FROM external_projections
        WHERE fantasypros_id = '${profilePlayer.id}' AND year = ${dbYear ?? CURRENT_SEASON_YEAR}
        ORDER BY week
      `);
      if (projResult.length > 0) {
        for (const [week, source, points] of projResult[0].values) {
          if (!byWeek[week]) byWeek[week] = { week, actual: null, stats: {} };
          byWeek[week][source] = points;
        }
      }
    } catch (err) {
      console.error("Failed to load player projections:", err);
    }

    try {
      const injuryResult = db.exec(`
        SELECT week, injury, practice_status, game_status
        FROM weekly_injuries
        WHERE fantasypros_id = '${profilePlayer.id}' AND year = ${dbYear ?? CURRENT_SEASON_YEAR}
        ORDER BY week
      `);
      if (injuryResult.length > 0) {
        for (const [week, injury, , gameStatus] of injuryResult[0].values) {
          if (!byWeek[week]) byWeek[week] = { week, actual: null, stats: {} };
          byWeek[week].injury = injury
            ? `${injury}${gameStatus ? ` (${gameStatus})` : ""}`
            : (gameStatus || "");
        }
      }
    } catch (err) {
      console.error("Failed to load player injuries:", err);
    }

    setProfileRows(Object.values(byWeek).sort((a, b) => a.week - b.week));
  }, [db, profilePlayer, weeklyStatsCols, dbYear]);

  useEffect(() => {
    if (!db) return;
    if (activeTab === "projections" || activeTab === "players") {
      if (projWeek === null || projSource === null) return;
    } else if (activeTab !== "draftBoard" && selectedWeek === null) {
      return;
    }

    // Check if weekly_lineups table exists (needed for matchupDetails)
    let weeklyLineupsExists = false;
    if (activeTab === "matchupDetails") {
      try {
        const tableCheck = db.exec("SELECT name FROM sqlite_master WHERE type='table' AND name='weekly_lineups';");
        weeklyLineupsExists = tableCheck.length > 0 && tableCheck[0].values.length > 0;
      } catch (e) {
        weeklyLineupsExists = false;
      }
      
      if (!weeklyLineupsExists) {
        setError("The weekly_lineups table is not available in this database. This feature requires a database with lineup data. The table may need to be created using the backfill_lineups command.");
        setColumns([]);
        setData([]);
        return;
      }
    }

    // players doesn't strictly need league-state tables (bots/matchups/etc) --
    // a stats-only db (e.g. a freshly-built season.db with no draft run yet)
    // should still show the player list, just without a fantasy team column.
    let botsExists = false;
    if (activeTab === "players") {
      try {
        const tableCheck = db.exec("SELECT name FROM sqlite_master WHERE type='table' AND name='bots';");
        botsExists = tableCheck.length > 0 && tableCheck[0].values.length > 0;
      } catch (e) {
        botsExists = false;
      }
    }

    if (activeTab === "draftBoard") {
      let mockDraftExists = false;
      try {
        const tableCheck = db.exec("SELECT name FROM sqlite_master WHERE type='table' AND name='mock_draft_picks';");
        mockDraftExists = tableCheck.length > 0 && tableCheck[0].values.length > 0;
      } catch (e) {
        mockDraftExists = false;
      }
      if (!mockDraftExists) {
        setError("No mock draft has been run against this db yet. Run harness.cli_draft (writes to season.db's mock_draft_picks table by default).");
        setColumns([]);
        setData([]);
        return;
      }
    }

    const queries = {
      current: `
        SELECT
          week,
          home_bot.name as home_bot_name,
          home_score,
          visitor_bot.name as visitor_bot_name,
          visitor_score,
          winning_bot.name as winning_bot_name
        FROM matchups as m
        LEFT JOIN bots AS home_bot ON m.home_bot_id = home_bot.id
        LEFT JOIN bots AS visitor_bot ON m.visitor_bot_id = visitor_bot.id
        LEFT JOIN bots AS winning_bot ON m.winning_bot_id = winning_bot.id
        WHERE week = ${selectedWeek}
      `,
      last: `
        SELECT
          week,
          home_bot.name as home_bot_name,
          home_score,
          visitor_bot.name as visitor_bot_name,
          visitor_score,
          winning_bot.name as winning_bot_name
        FROM matchups as m
        LEFT JOIN bots AS home_bot ON m.home_bot_id = home_bot.id
        LEFT JOIN bots AS visitor_bot ON m.visitor_bot_id = visitor_bot.id
        LEFT JOIN bots AS winning_bot ON m.winning_bot_id = winning_bot.id
        WHERE week = ${selectedWeek - 1}
      `,
      matchupDetails: `
        SELECT
          m.id as matchup_id,
          m.home_bot_id,
          m.visitor_bot_id,
          home_bot.name as home_team,
          visitor_bot.name as visitor_team,
          m.home_score,
          m.visitor_score,
          p.id as player_id,
          p.full_name,
          p.allowed_positions,
          wl.bot_id,
          CASE WHEN wl.bot_id = m.home_bot_id THEN 'home' ELSE 'visitor' END as side,
          ws.FPTS as actual_points,
          wp.FPTS as projected_points,
          wl.slot as slot
        FROM matchups m
        INNER JOIN bots as home_bot ON m.home_bot_id = home_bot.id
        INNER JOIN bots as visitor_bot ON m.visitor_bot_id = visitor_bot.id
        INNER JOIN weekly_lineups wl ON wl.week = m.week AND (wl.bot_id = m.home_bot_id OR wl.bot_id = m.visitor_bot_id)
        INNER JOIN players p ON p.id = wl.player_id
        LEFT JOIN weekly_stats ws ON p.id = ws.fantasypros_id AND ws.week = m.week AND ws.year = ${dbYear ?? CURRENT_SEASON_YEAR}
        LEFT JOIN weekly_projections wp ON p.id = wp.fantasypros_id AND wp.week = m.week AND wp.year = ${dbYear ?? CURRENT_SEASON_YEAR}
        WHERE m.week = ${selectedWeek}
        ORDER BY m.id, side,
          CASE wl.slot
            WHEN 'QB' THEN 1
            WHEN 'RB' THEN 2
            WHEN 'WR' THEN 3
            WHEN 'SUPERFLEX' THEN 4
            WHEN 'FLEX' THEN 5
            WHEN 'K' THEN 6
            WHEN 'DST' THEN 7
            WHEN 'BENCH' THEN 8
            ELSE 9
          END,
          p.full_name
      `,
      leaderboard: `
        WITH botScores AS (
          SELECT
            b.Name,
            SUM(IIF(b.id = m.home_bot_id, home_score, 0) + IIF(b.id = m.visitor_bot_id, visitor_score, 0)) AS totalPoints,
            SUM(IIF(b.id = m.home_bot_id, visitor_score, 0) + IIF(b.id = m.visitor_bot_id, home_score, 0)) AS pointsAgainst,
            SUM(IIF(b.id = m.winning_bot_id, 1, 0)) AS numWins,
            SUM(IIF(b.id != m.winning_bot_id, 1, 0)) AS numLosses
          FROM bots AS b
          INNER JOIN matchups AS m
          ON (b.id = m.visitor_bot_id OR b.id = m.home_bot_id)
          WHERE week < ${selectedWeek}
          GROUP BY 1
        )
        SELECT
          ROW_NUMBER() OVER (ORDER BY numWins DESC, totalPoints DESC) AS rank,
          *
        FROM botScores
        ORDER BY numWins DESC, totalPoints DESC
      `,
      rosters: `
        WITH playerPoints AS (
          SELECT p.id, p.full_name, p.allowed_positions, p.current_bot_id, SUM(wk.FPTS) AS totalPoints
          FROM players AS p
          INNER JOIN weekly_stats AS wk ON p.id = wk.fantasypros_id AND wk.year = ${dbYear ?? CURRENT_SEASON_YEAR}
          GROUP BY 1,2,3,4
        )
        SELECT
          p.id,
          p.full_name,
          p.allowed_positions,
          b.name AS teamName,
          totalPoints,
          wi.game_status AS injury_status,
          wp.FPTS AS projected_points
        FROM playerPoints AS p
        LEFT JOIN bots AS b ON p.current_bot_id = b.id
        LEFT JOIN weekly_injuries AS wi ON p.id = wi.fantasypros_id AND wi.week = ${selectedWeek} AND wi.year = ${dbYear ?? CURRENT_SEASON_YEAR}
        LEFT JOIN weekly_projections AS wp ON p.id = wp.fantasypros_id AND wp.week = ${selectedWeek} AND wp.year = ${dbYear ?? CURRENT_SEASON_YEAR}
        ORDER BY b.name, p.full_name
      `,
      projections: `
        SELECT
          p.full_name AS player,
          p.professional_team AS team,
          ep.position,
          ep.source,
          ep.week,
          ep.points
        FROM external_projections ep
        JOIN players p ON p.id = ep.fantasypros_id
        WHERE ep.week = ${projWeek}
          AND ep.source = '${projSource}'
          AND ep.year = ${dbYear ?? CURRENT_SEASON_YEAR}
          AND ep.position IN (${(projPositions.length ? projPositions : ["__none__"]).map((p) => `'${p}'`).join(",")})
        ORDER BY ep.points DESC
      `,
      players: `
        SELECT
          p.id,
          p.full_name AS player,
          p.allowed_positions,
          p.professional_team AS team,
          ${botsExists ? "b.name AS fantasyTeam," : "NULL AS fantasyTeam,"}
          COALESCE(ap.actualPoints, 0) AS actualPoints,
          ep.points AS projectedPoints,
          wi.injury,
          wi.practice_status,
          wi.game_status
        FROM players p
        ${botsExists ? "LEFT JOIN bots b ON p.current_bot_id = b.id" : ""}
        LEFT JOIN (
          SELECT fantasypros_id, SUM(FPTS) AS actualPoints FROM weekly_stats WHERE year = ${dbYear ?? CURRENT_SEASON_YEAR} GROUP BY fantasypros_id
        ) ap ON ap.fantasypros_id = p.id
        LEFT JOIN external_projections ep ON ep.fantasypros_id = p.id AND ep.week = ${projWeek} AND ep.source = '${projSource}' AND ep.year = ${dbYear ?? CURRENT_SEASON_YEAR}
        LEFT JOIN weekly_injuries wi ON wi.fantasypros_id = p.id AND wi.week = ${projWeek} AND wi.year = ${dbYear ?? CURRENT_SEASON_YEAR}
        ORDER BY actualPoints DESC
      `,
      draftBoard: `
        SELECT
          mdp.pick, mdp.round, mdp.team_slot, mdp.team_name, mdp.team_owner, mdp.is_user,
          mdp.fantasypros_id, mdp.player_name, mdp.position, mdp.nfl_team, mdp.bot_label,
          COALESCE(ap.actualPoints, 0) AS actualPoints
        FROM mock_draft_picks mdp
        LEFT JOIN (
          SELECT fantasypros_id, year, SUM(FPTS) AS actualPoints FROM weekly_stats GROUP BY fantasypros_id, year
        ) ap ON ap.fantasypros_id = mdp.fantasypros_id AND ap.year = mdp.year
        ORDER BY mdp.pick
      `,
    };

    const query = queries[activeTab];
    if (!query) return;

    try {
      setError(null);
      const result = db.exec(query);
      if (result.length > 0) {
        setColumns(result[0].columns);
        const formatted = result[0].values.map((row) =>
          Object.fromEntries(row.map((val, i) => [result[0].columns[i], val]))
        );
        setData(formatted);
      } else {
        setColumns([]);
        setData([]);
      }
    } catch (err) {
      console.error("Query failed:", err);
      setColumns([]);
      setData([]);
      // Check if it's a missing table error
      if (err.message && err.message.includes("no such table")) {
        if (err.message.includes("weekly_lineups")) {
          setError("The weekly_lineups table is not available in this database. This feature requires a database with lineup data.");
        } else {
          setError(`Database error: ${err.message}`);
        }
      } else {
        setError(`Query failed: ${err.message || err}`);
      }
    }
  }, [db, activeTab, selectedWeek, projWeek, projSource, projPositions, dbYear]);

  const lightVars = {
    background: "#ffffff",
    foreground: "#111827",
    primary: "#007bff",
    muted: "#eee",
    border: "#ccc",
  };

  const darkVars = {
    background: "#0b1220",
    foreground: "#e6eef8",
    primary: "#3b82f6",
    muted: "#1f2937",
    border: "#263244",
  };

  const vars = effectiveTheme === "dark" ? darkVars : lightVars;

  // Listen to system theme changes
  useEffect(() => {
    if (!window || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = (e) => setSystemDark(e.matches);
    try {
      if (mq.addEventListener) mq.addEventListener("change", handler);
      else mq.addListener(handler);
    } catch (e) {
      // ignore
    }
    return () => {
      try {
        if (mq.removeEventListener) mq.removeEventListener("change", handler);
        else mq.removeListener(handler);
      } catch (e) {
        // ignore
      }
    };
  }, []);

  const { background: bodyBackground, foreground: bodyForeground } = vars;

  useEffect(() => {
    document.body.style.backgroundColor = bodyBackground;
    document.body.style.color = bodyForeground;
  }, [bodyBackground, bodyForeground]);

  const toggleTheme = () => {
    const next = effectiveTheme === "dark" ? "light" : "dark";
    try {
      localStorage.setItem("themePref", next);
    } catch (e) {
      // ignore
    }
    setThemePref(next);
  };

  if (loading) return <p>Loading database...</p>;

  const tabs = [
    { key: "current", label: "Current Week" },
    { key: "last", label: "Last Week" },
    { key: "matchupDetails", label: "Matchup Details" },
    { key: "leaderboard", label: "Leaderboard" },
    { key: "rosters", label: "Rosters" },
    { key: "projections", label: "Projections" },
    { key: "players", label: "Players" },
    { key: "draftBoard", label: "Draft Board" },
  ];

  const handleSort = (col) => {
    if (sortColumn === col) setSortDirection(sortDirection === "asc" ? "desc" : "asc");
    else {
      setSortColumn(col);
      setSortDirection("asc");
    }
  };

  const sortData = (dataToSort) => {
    if (!sortColumn) return dataToSort;
    return [...dataToSort].sort((a, b) => {
      const valA = a[sortColumn];
      const valB = b[sortColumn];
      if (!isNaN(valA) && !isNaN(valB)) return sortDirection === "asc" ? valA - valB : valB - valA;
      return sortDirection === "asc" ? String(valA).localeCompare(String(valB)) : String(valB).localeCompare(String(valA));
    });
  };

  const getGroupedData = () => {
    // Initialize all teams
    const groups = {};
    data.forEach((row) => {
      const team = row.teamName || "Undrafted";
      if (!groups[team]) groups[team] = [];
      groups[team].push(row);
    });

    // Ensure "Undrafted" always exists
    if (!groups["Undrafted"]) groups["Undrafted"] = [];

    // Apply filter per team
    if (activeTab === "rosters" && filterText) {
      Object.keys(groups).forEach((team) => {
        groups[team] = sortData(groups[team]).filter((row) =>
          columns.some((col) =>
            String(row[col]).toLowerCase().includes(filterText.toLowerCase())
          )
        );
      });
    } else {
      Object.keys(groups).forEach((team) => {
        groups[team] = sortData(groups[team]);
      });
    }

    return groups;
  };

  const groupedData = activeTab === "rosters" ? getGroupedData() : null;

  const getMatchupData = () => {
    // Group players by matchup
    const matchups = {};
    data.forEach((row) => {
      const matchupId = row.matchup_id;
      if (!matchups[matchupId]) {
        matchups[matchupId] = {
          id: matchupId,
          homeTeam: row.home_team,
          homeBotId: row.home_bot_id,
          visitorTeam: row.visitor_team,
          visitorBotId: row.visitor_bot_id,
          homeScore: row.home_score || 0,
          visitorScore: row.visitor_score || 0,
          homePlayers: [],
          visitorPlayers: [],
        };
      }

      // Parse allowed_positions to get primary position
      let position = '';
      try {
        const positions = JSON.parse(row.allowed_positions || '[]');
        position = positions[0] || '';
      } catch (e) {
        position = '';
      }

      const player = {
        name: row.full_name,
        position: position,
        slot: row.slot,
        projected: row.projected_points || 0,
        actual: row.actual_points || 0,
      };

      if (row.side === 'home') {
        matchups[matchupId].homePlayers.push(player);
      } else {
        matchups[matchupId].visitorPlayers.push(player);
      }
    });

    return Object.values(matchups);
  };

  const renderTable = (tableData, tableColumns) => (
    <table
      style={{
        width: "100%",
        borderCollapse: "collapse",
        border: `1px solid ${vars.border}`,
        marginBottom: "2rem",
      }}
    >
      <thead>
        <tr>
          {tableColumns.map((col) => (
            <th
              key={col}
              style={{
                border: `1px solid ${vars.border}`,
                padding: "0.5rem",
                cursor: "pointer",
              }}
              onClick={() => handleSort(col)}
            >
              {col} {sortColumn === col ? (sortDirection === "asc" ? "↑" : "↓") : ""}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {tableData.map((row, idx) => (
          <tr key={idx}>
            {tableColumns.map((col) => {
              // Rosters rows carry both id + full_name -- make the name a link into the player profile.
              const isPlayerName = col === "full_name" && row.id != null;
              return (
                <td
                  key={col}
                  onClick={isPlayerName ? () => setProfilePlayer({ id: row.id, name: row.full_name }) : undefined}
                  style={{
                    border: `1px solid ${vars.border}`,
                    padding: "0.5rem",
                    ...(isPlayerName ? { cursor: "pointer", textDecoration: "underline" } : {}),
                  }}
                >
                  {col === "position" && POSITION_COLORS[row[col]] ? <PositionPill position={row[col]} /> : row[col]}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );

  const renderPlayersTable = () => {
    const withPoints = data.map((row) => ({
      ...row,
      position: (() => {
        try {
          return (JSON.parse(row.allowed_positions || "[]")[0]) || "";
        } catch (e) {
          return "";
        }
      })(),
      points: pointsMode === "projected" ? row.projectedPoints : row.actualPoints,
      injurySummary: row.injury
        ? `${row.injury}${row.game_status ? ` (${row.game_status})` : ""}`
        : "",
    }));

    const query = playerSearch.trim().toLowerCase();
    const filtered = withPoints.filter((row) =>
      playerPositions.includes(row.position) &&
      (!query || String(row.player).toLowerCase().includes(query))
    );

    const sorted = sortData(filtered);

    const col = (key, label) => (
      <th
        key={key}
        style={{ border: `1px solid ${vars.border}`, padding: "0.5rem", cursor: "pointer" }}
        onClick={() => handleSort(key)}
      >
        {label} {sortColumn === key ? (sortDirection === "asc" ? "↑" : "↓") : ""}
      </th>
    );

    return (
      <table style={{ width: "100%", borderCollapse: "collapse", border: `1px solid ${vars.border}`, marginBottom: "2rem" }}>
        <thead>
          <tr>
            {col("player", "Player")}
            {col("position", "Pos")}
            {col("team", "NFL Team")}
            {col("fantasyTeam", "Fantasy Team")}
            {col("points", pointsMode === "projected" ? "Projected Points" : "Actual Points")}
            {col("injurySummary", "Injury")}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={row.id}>
              <td
                onClick={() => setProfilePlayer({ id: row.id, name: row.player })}
                style={{ border: `1px solid ${vars.border}`, padding: "0.5rem", cursor: "pointer", textDecoration: "underline" }}
              >
                {row.player}
              </td>
              <td style={{ border: `1px solid ${vars.border}`, padding: "0.5rem" }}><PositionPill position={row.position} /></td>
              <td style={{ border: `1px solid ${vars.border}`, padding: "0.5rem" }}>{row.team}</td>
              <td style={{ border: `1px solid ${vars.border}`, padding: "0.5rem" }}>{row.fantasyTeam || "Undrafted"}</td>
              <td style={{ border: `1px solid ${vars.border}`, padding: "0.5rem" }}>
                {row.points != null ? Number(row.points).toFixed(1) : "—"}
              </td>
              <td style={{ border: `1px solid ${vars.border}`, padding: "0.5rem", color: row.injurySummary ? "#dc2626" : "inherit" }}>
                {row.injurySummary}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  };

  const renderDraftBoard = () => {
    if (data.length === 0) return <p>No draft data.</p>;

    const teamsMap = new Map();
    data.forEach((r) => { if (!teamsMap.has(r.team_slot)) teamsMap.set(r.team_slot, r); });
    const teams = [...teamsMap.values()].sort((a, b) => a.team_slot - b.team_slot);
    const teamTotals = new Map();
    data.forEach((r) => {
      teamTotals.set(r.team_slot, (teamTotals.get(r.team_slot) || 0) + Number(r.actualPoints || 0));
    });
    const numRounds = Math.max(...data.map((r) => r.round));
    const cellMap = new Map(data.map((r) => [`${r.round}-${r.team_slot}`, r]));
    const botLabel = data[0]?.bot_label;
    const leaderboard = [...teams]
      .map((t) => ({ ...t, total: teamTotals.get(t.team_slot) || 0 }))
      .sort((a, b) => b.total - a.total);

    return (
      <>
        <table style={{ width: "100%", maxWidth: "480px", borderCollapse: "collapse", border: `1px solid ${vars.border}`, marginBottom: "1.5rem" }}>
          <thead>
            <tr>
              <th style={{ border: `1px solid ${vars.border}`, padding: "0.5rem", textAlign: "left" }}>Rank</th>
              <th style={{ border: `1px solid ${vars.border}`, padding: "0.5rem", textAlign: "left" }}>Team</th>
              <th style={{ border: `1px solid ${vars.border}`, padding: "0.5rem", textAlign: "right" }}>Total Points</th>
            </tr>
          </thead>
          <tbody>
            {leaderboard.map((t, idx) => (
              <tr key={t.team_slot} style={{ background: t.is_user ? vars.primary : "transparent", color: t.is_user ? "#fff" : vars.foreground }}>
                <td style={{ border: `1px solid ${vars.border}`, padding: "0.5rem" }}>{idx + 1}</td>
                <td style={{ border: `1px solid ${vars.border}`, padding: "0.5rem" }}>{t.team_name} ({t.team_owner})</td>
                <td style={{ border: `1px solid ${vars.border}`, padding: "0.5rem", textAlign: "right", fontWeight: 600 }}>{t.total.toFixed(1)}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <p style={{ marginBottom: "0.75rem" }}><strong>{botLabel}</strong>'s column is highlighted below.</p>
        <div style={{ overflowX: "auto" }}>
          <table style={{ borderCollapse: "collapse", border: `1px solid ${vars.border}`, marginBottom: "2rem" }}>
            <thead>
              <tr>
                <th style={{ border: `1px solid ${vars.border}`, padding: "0.5rem", background: vars.muted }}>Round</th>
                {teams.map((t) => (
                  <th
                    key={t.team_slot}
                    style={{
                      border: `1px solid ${vars.border}`,
                      padding: "0.5rem",
                      minWidth: "130px",
                      background: t.is_user ? vars.primary : vars.muted,
                      color: t.is_user ? "#fff" : vars.foreground,
                    }}
                  >
                    <div>{t.team_name}</div>
                    <div style={{ fontSize: "0.75rem", opacity: 0.85 }}>{t.team_owner}</div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: numRounds }, (_, i) => i + 1).map((round) => (
                <tr key={round}>
                  <td style={{ border: `1px solid ${vars.border}`, padding: "0.5rem", fontWeight: "bold", background: vars.muted }}>R{round}</td>
                  {teams.map((t) => {
                    const cell = cellMap.get(`${round}-${t.team_slot}`);
                    return (
                      <td
                        key={t.team_slot}
                        style={{
                          border: `1px solid ${vars.border}`,
                          padding: "0.5rem",
                          background: t.is_user ? vars.muted : "transparent",
                        }}
                      >
                        {cell && cell.player_name ? (
                          <>
                            <div
                              onClick={cell.fantasypros_id != null ? () => setProfilePlayer({ id: cell.fantasypros_id, name: cell.player_name }) : undefined}
                              style={{ fontWeight: 600, ...(cell.fantasypros_id != null ? { cursor: "pointer", textDecoration: "underline" } : {}) }}
                            >
                              {cell.player_name}
                            </div>
                            <div style={{ fontSize: "0.75rem" }}><PositionPill position={cell.position} /> &middot; {cell.nfl_team}</div>
                            <div style={{ fontSize: "0.75rem", fontWeight: 600 }}>{Number(cell.actualPoints || 0).toFixed(1)} pts</div>
                            <div style={{ fontSize: "0.7rem", opacity: 0.6 }}>#{cell.pick}</div>
                          </>
                        ) : "-"}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>
    );
  };

  const renderProfileModal = () => {
    if (!profilePlayer) return null;

    const visibleStatCols = STAT_COLUMNS.filter(([col]) =>
      profileRows.some((row) => Number(row.stats?.[col]) > 0)
    );

    return (
      <div
        onClick={() => setProfilePlayer(null)}
        style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}
      >
        <div
          onClick={(e) => e.stopPropagation()}
          style={{ background: vars.background, color: vars.foreground, border: `1px solid ${vars.border}`, borderRadius: "8px", padding: "1.5rem", maxWidth: "95vw", maxHeight: "85vh", overflow: "auto" }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
            <h2 style={{ margin: 0 }}>{profilePlayer.name}</h2>
            <button
              onClick={() => setProfilePlayer(null)}
              style={{ background: vars.muted, color: vars.foreground, border: `1px solid ${vars.border}`, borderRadius: "4px", padding: "0.4rem 0.8rem", cursor: "pointer" }}
            >
              Close
            </button>
          </div>

          {profileRows.length === 0 ? (
            <p>No weekly data found for this player.</p>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
              <thead>
                <tr>
                  {["Week", "Opponent", "Actual", "FFToday", "FantasySharks", "ESPN"].map((h) => (
                    <th key={h} style={{ border: `1px solid ${vars.border}`, padding: "0.4rem 0.6rem", textAlign: h === "Opponent" ? "left" : "right", whiteSpace: "nowrap" }}>{h}</th>
                  ))}
                  {visibleStatCols.map(([col, label]) => (
                    <th key={col} style={{ border: `1px solid ${vars.border}`, padding: "0.4rem 0.6rem", textAlign: "right", whiteSpace: "nowrap" }}>{label}</th>
                  ))}
                  <th style={{ border: `1px solid ${vars.border}`, padding: "0.4rem 0.6rem", textAlign: "left", whiteSpace: "nowrap" }}>Injury</th>
                </tr>
              </thead>
              <tbody>
                {profileRows.map((row) => (
                  <tr key={row.week}>
                    <td style={{ border: `1px solid ${vars.border}`, padding: "0.4rem 0.6rem", textAlign: "right" }}>{row.week === 0 ? "Pre" : row.week}</td>
                    <td style={{ border: `1px solid ${vars.border}`, padding: "0.4rem 0.6rem", fontWeight: row.week === profileByeWeek ? "bold" : "normal", color: row.week === profileByeWeek ? vars.foreground : "inherit" }}>
                      {row.week === profileByeWeek ? "BYE" : (row.opponent ? `vs ${row.opponent}` : "—")}
                    </td>
                    <td style={{ border: `1px solid ${vars.border}`, padding: "0.4rem 0.6rem", textAlign: "right", fontWeight: "bold" }}>
                      {row.actual != null ? Number(row.actual).toFixed(1) : "—"}
                    </td>
                    <td style={{ border: `1px solid ${vars.border}`, padding: "0.4rem 0.6rem", textAlign: "right" }}>
                      {row.FFToday != null ? Number(row.FFToday).toFixed(1) : "—"}
                    </td>
                    <td style={{ border: `1px solid ${vars.border}`, padding: "0.4rem 0.6rem", textAlign: "right" }}>
                      {row.FantasySharks != null ? Number(row.FantasySharks).toFixed(1) : "—"}
                    </td>
                    <td style={{ border: `1px solid ${vars.border}`, padding: "0.4rem 0.6rem", textAlign: "right" }}>
                      {row.ESPN != null ? Number(row.ESPN).toFixed(1) : "—"}
                    </td>
                    {visibleStatCols.map(([col]) => (
                      <td key={col} style={{ border: `1px solid ${vars.border}`, padding: "0.4rem 0.6rem", textAlign: "right" }}>
                        {row.stats?.[col] ?? "—"}
                      </td>
                    ))}
                    <td style={{ border: `1px solid ${vars.border}`, padding: "0.4rem 0.6rem", color: row.injury ? "#dc2626" : "inherit", whiteSpace: "nowrap" }}>
                      {row.injury || ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    );
  };

  return (
    <div style={{ padding: "2rem", fontFamily: "sans-serif", background: vars.background, color: vars.foreground, minHeight: "100vh", overflowX: "auto"}}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1rem" }}>
        <h1 style={{ margin: 0 }}>
          Botblitz{dbSourceLabel ? ` - ${dbSourceLabel}` : ""}{dbYear != null ? ` (${dbYear})` : ""}
        </h1>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <label htmlFor="week-select" style={{ fontSize: "0.9rem" }}>Week:</label>
            <select
              id="week-select"
              value={selectedWeek || ""}
              onChange={(e) => setSelectedWeek(Number(e.target.value))}
              style={{
                padding: "0.4rem 0.6rem",
                background: vars.muted,
                color: vars.foreground,
                border: `1px solid ${vars.border}`,
                borderRadius: "4px",
                cursor: "pointer",
                fontSize: "0.9rem",
              }}
            >
              {currentWeek && Array.from({ length: currentWeek }, (_, i) => i + 1).map(week => (
                <option key={week} value={week}>
                  {week}
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={toggleTheme}
            aria-label={effectiveTheme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            title={effectiveTheme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 36,
              height: 36,
              padding: 6,
              background: vars.muted,
              color: vars.foreground,
              border: `1px solid ${vars.border}`,
              borderRadius: 8,
              cursor: "pointer",
            }}
          >
            {effectiveTheme === "dark" ? (
              // sun icon to indicate switching to light
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden>
                <path d="M12 4V2" stroke={vars.foreground} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                <path d="M12 22v-2" stroke={vars.foreground} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                <path d="M4 12H2" stroke={vars.foreground} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                <path d="M22 12h-2" stroke={vars.foreground} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                <path d="M5 5L3.5 3.5" stroke={vars.foreground} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                <path d="M20.5 20.5L19 19" stroke={vars.foreground} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                <path d="M19 5l1.5-1.5" stroke={vars.foreground} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                <path d="M4.5 19.5L6 18" stroke={vars.foreground} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                <circle cx="12" cy="12" r="4" stroke={vars.foreground} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            ) : (
              // moon icon to indicate switching to dark
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden>
                <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" stroke={vars.foreground} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            )}
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div style={{ marginBottom: "1rem" }}>
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            style={{
              padding: "0.5rem 1rem",
              marginRight: "0.5rem",
              backgroundColor: activeTab === tab.key ? vars.primary : vars.muted,
              color: activeTab === tab.key ? "#fff" : vars.foreground,
              border: `1px solid ${vars.border}`,
              borderRadius: "4px",
              cursor: "pointer",
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Filter only for rosters */}
      {activeTab === "rosters" && (
        <input
          type="text"
          placeholder="Search players..."
          value={filterText}
          onChange={(e) => setFilterText(e.target.value)}
          style={{ marginBottom: "1rem", padding: "0.5rem", width: "100%", background: vars.background, color: vars.foreground, border: `1px solid ${vars.border}` }}
        />
      )}

      {/* Controls for projections: week, source, position -- click column headers to rank/sort */}
      {activeTab === "projections" && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "1rem", alignItems: "center", marginBottom: "1rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <label style={{ fontSize: "0.9rem" }}>Week:</label>
            <select
              value={projWeek ?? ""}
              onChange={(e) => setProjWeek(Number(e.target.value))}
              style={{ padding: "0.4rem 0.6rem", background: vars.muted, color: vars.foreground, border: `1px solid ${vars.border}`, borderRadius: "4px" }}
            >
              {projWeeks.map((w) => (
                <option key={w} value={w}>{w === 0 ? "0 (preseason)" : w}</option>
              ))}
            </select>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <label style={{ fontSize: "0.9rem" }}>Source:</label>
            <select
              value={projSource ?? ""}
              onChange={(e) => setProjSource(e.target.value)}
              style={{ padding: "0.4rem 0.6rem", background: vars.muted, color: vars.foreground, border: `1px solid ${vars.border}`, borderRadius: "4px" }}
            >
              {projSources.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
            <label style={{ fontSize: "0.9rem" }}>Position:</label>
            {["QB", "RB", "WR", "TE", "K", "DST"].map((pos) => (
              <label key={pos} style={{ fontSize: "0.85rem", display: "inline-flex", alignItems: "center", gap: "0.25rem" }}>
                <input
                  type="checkbox"
                  checked={projPositions.includes(pos)}
                  onChange={(e) => {
                    setProjPositions((prev) =>
                      e.target.checked ? [...prev, pos] : prev.filter((p) => p !== pos)
                    );
                  }}
                />
                {pos}
              </label>
            ))}
          </div>
        </div>
      )}

      {/* Controls for players: search, actual/projected toggle, week+source (shared with Projections tab) */}
      {activeTab === "players" && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "1rem", alignItems: "center", marginBottom: "1rem" }}>
          <input
            type="text"
            placeholder="Search players..."
            value={playerSearch}
            onChange={(e) => setPlayerSearch(e.target.value)}
            style={{ padding: "0.5rem", minWidth: "220px", background: vars.background, color: vars.foreground, border: `1px solid ${vars.border}`, borderRadius: "4px" }}
          />
          <div style={{ display: "inline-flex", border: `1px solid ${vars.border}`, borderRadius: "4px", overflow: "hidden" }}>
            {[
              { key: "actual", label: "Actual" },
              { key: "projected", label: "Projected" },
            ].map((mode) => (
              <button
                key={mode.key}
                onClick={() => setPointsMode(mode.key)}
                style={{
                  padding: "0.5rem 0.9rem",
                  border: "none",
                  cursor: "pointer",
                  background: pointsMode === mode.key ? vars.primary : vars.muted,
                  color: pointsMode === mode.key ? "#fff" : vars.foreground,
                  fontSize: "0.85rem",
                }}
              >
                {mode.label}
              </button>
            ))}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <label style={{ fontSize: "0.9rem" }}>
              {pointsMode === "projected" ? "Projected as of week:" : "Injuries as of week:"}
            </label>
            <select
              value={projWeek ?? ""}
              onChange={(e) => setProjWeek(Number(e.target.value))}
              style={{ padding: "0.4rem 0.6rem", background: vars.muted, color: vars.foreground, border: `1px solid ${vars.border}`, borderRadius: "4px" }}
            >
              {projWeeks.map((w) => (
                <option key={w} value={w}>{w === 0 ? "0 (preseason)" : w}</option>
              ))}
            </select>
          </div>
          {pointsMode === "projected" && (
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <label style={{ fontSize: "0.9rem" }}>Source:</label>
              <select
                value={projSource ?? ""}
                onChange={(e) => setProjSource(e.target.value)}
                style={{ padding: "0.4rem 0.6rem", background: vars.muted, color: vars.foreground, border: `1px solid ${vars.border}`, borderRadius: "4px" }}
              >
                {projSources.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
          )}
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
            <label style={{ fontSize: "0.9rem" }}>Position:</label>
            {["QB", "RB", "WR", "TE", "K", "DST"].map((pos) => (
              <label key={pos} style={{ fontSize: "0.85rem", display: "inline-flex", alignItems: "center", gap: "0.25rem" }}>
                <input
                  type="checkbox"
                  checked={playerPositions.includes(pos)}
                  onChange={(e) => {
                    setPlayerPositions((prev) =>
                      e.target.checked ? [...prev, pos] : prev.filter((p) => p !== pos)
                    );
                  }}
                />
                {pos}
              </label>
            ))}
          </div>
        </div>
      )}

      {/* Error message */}
      {error && (
        <div style={{
          padding: "1rem",
          marginBottom: "1rem",
          background: effectiveTheme === "dark" ? "rgba(239, 68, 68, 0.2)" : "rgba(239, 68, 68, 0.1)",
          border: `1px solid ${effectiveTheme === "dark" ? "#ef4444" : "#dc2626"}`,
          borderRadius: "4px",
          color: effectiveTheme === "dark" ? "#fca5a5" : "#991b1b",
        }}>
          {error}
        </div>
      )}

      {/* Render tables */}
      {activeTab === "rosters" ? (
        // Sort teams alphabetically and place "Undrafted" last
        Object.entries(groupedData)
          .sort(([a], [b]) => {
            if (a === "Undrafted") return 1;
            if (b === "Undrafted") return -1;
            return a.localeCompare(b);
          })
          .map(([team, players]) => (
            <div key={team}>
              <h2>{team}</h2>
              {renderTable(players, columns)}
            </div>
          ))
      ) : activeTab === "matchupDetails" ? (
        data.length === 0 ? (
          <div style={{ padding: "2rem", textAlign: "center", color: vars.foreground }}>
            No matchup data available for this week.
          </div>
        ) : (
          getMatchupData().map((matchup) => {
            // Determine winner
          const homeWon = matchup.homeScore > matchup.visitorScore;
          const visitorWon = matchup.visitorScore > matchup.homeScore;
          const homeScoreColor = homeWon ? "#22c55e" : (visitorWon ? "#ef4444" : vars.primary);
          const visitorScoreColor = visitorWon ? "#22c55e" : (homeWon ? "#ef4444" : vars.primary);

          return (
            <div
              key={matchup.id}
              style={{
                marginBottom: "2rem",
                border: `2px solid ${vars.border}`,
                borderRadius: "8px",
                overflow: "hidden",
              }}
            >
              {/* Matchup Header */}
              <div
                style={{
                  background: vars.muted,
                  padding: "1rem",
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: "1rem",
                  fontWeight: "bold",
                  fontSize: "1.1rem",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span>
                    {matchup.homeTeam}
                    <span style={{ fontSize: "0.8rem", opacity: 0.7, marginLeft: "0.5rem" }}>
                      (ID: {matchup.homeBotId})
                    </span>
                  </span>
                  <span style={{ color: homeScoreColor }}>
                    {matchup.homeScore.toFixed(2)}
                  </span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ color: visitorScoreColor }}>
                    {matchup.visitorScore.toFixed(2)}
                  </span>
                  <span>
                    {matchup.visitorTeam}
                    <span style={{ fontSize: "0.8rem", opacity: 0.7, marginLeft: "0.5rem" }}>
                      (ID: {matchup.visitorBotId})
                    </span>
                  </span>
                </div>
              </div>

              {/* Two-column layout for rosters */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr" }}>
                {/* Home Team */}
                <div style={{ borderRight: `1px solid ${vars.border}`, padding: "1rem" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse" }}>
                    <thead>
                      <tr style={{ borderBottom: `1px solid ${vars.border}` }}>
                        <th style={{ textAlign: "left", padding: "0.5rem", fontSize: "0.9rem" }}>Player</th>
                        <th style={{ textAlign: "left", padding: "0.5rem", fontSize: "0.9rem" }}>Slot</th>
                        <th style={{ textAlign: "right", padding: "0.5rem", fontSize: "0.9rem" }}>Actual</th>
                      </tr>
                    </thead>
                    <tbody>
                      {matchup.homePlayers.map((player, idx) => {
                        const isBench = player.slot === 'BENCH';
                        return (
                          <tr key={idx} style={{
                            borderBottom: `1px solid ${vars.border}`,
                            opacity: isBench ? 0.5 : 1,
                            background: isBench ? vars.muted : 'transparent'
                          }}>
                            <td style={{ padding: "0.5rem", fontSize: "0.85rem" }}>
                              {player.name}
                              {player.position && <span style={{ opacity: 0.6 }}> ({player.position})</span>}
                            </td>
                            <td style={{ padding: "0.5rem", fontSize: "0.85rem" }}>{player.slot}</td>
                            <td style={{ padding: "0.5rem", textAlign: "right", fontSize: "0.85rem", fontWeight: isBench ? "normal" : "bold" }}>
                              {player.actual ? player.actual.toFixed(1) : "-"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                {/* Visitor Team */}
                <div style={{ padding: "1rem" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse" }}>
                    <thead>
                      <tr style={{ borderBottom: `1px solid ${vars.border}` }}>
                        <th style={{ textAlign: "left", padding: "0.5rem", fontSize: "0.9rem" }}>Player</th>
                        <th style={{ textAlign: "left", padding: "0.5rem", fontSize: "0.9rem" }}>Slot</th>
                        <th style={{ textAlign: "right", padding: "0.5rem", fontSize: "0.9rem" }}>Actual</th>
                      </tr>
                    </thead>
                    <tbody>
                      {matchup.visitorPlayers.map((player, idx) => {
                        const isBench = player.slot === 'BENCH';
                        return (
                          <tr key={idx} style={{
                            borderBottom: `1px solid ${vars.border}`,
                            opacity: isBench ? 0.5 : 1,
                            background: isBench ? vars.muted : 'transparent'
                          }}>
                            <td style={{ padding: "0.5rem", fontSize: "0.85rem" }}>
                              {player.name}
                              {player.position && <span style={{ opacity: 0.6 }}> ({player.position})</span>}
                            </td>
                            <td style={{ padding: "0.5rem", fontSize: "0.85rem" }}>{player.slot}</td>
                            <td style={{ padding: "0.5rem", textAlign: "right", fontSize: "0.85rem", fontWeight: isBench ? "normal" : "bold" }}>
                              {player.actual ? player.actual.toFixed(1) : "-"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          );
        })
        )
      ) : activeTab === "players" ? (
        renderPlayersTable()
      ) : activeTab === "draftBoard" ? (
        renderDraftBoard()
      ) : (
        renderTable(sortData(data), columns)
      )}
      {renderProfileModal()}
    </div>
  );
}

export default App;
