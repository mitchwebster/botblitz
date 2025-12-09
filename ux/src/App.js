import React, { useEffect, useState } from "react";

function App() {
  const [db, setDb] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("current");
  const [data, setData] = useState([]);
  const [columns, setColumns] = useState([]);
  const [sortColumn, setSortColumn] = useState(null);
  const [sortDirection, setSortDirection] = useState("asc");
  const [filterText, setFilterText] = useState("");
  const [selectedWeek, setSelectedWeek] = useState(null);
  const [currentWeek, setCurrentWeek] = useState(null);
  const [error, setError] = useState(null);
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
          // Use local database from public folder
          dbUrl = '/gs-season.db';
        } else {
          // Get branch from URL parameter (e.g., ?branch=chris-bot-add-drop)
          // Defaults to 'main' if not specified
          const urlParams = new URLSearchParams(window.location.search);
          const branch = urlParams.get('branch') || 'main';
          dbUrl = `https://raw.githubusercontent.com/mitchwebster/botblitz/${branch}/data/game_states/2025/gs-season.db`;
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

  useEffect(() => {
    if (!db || selectedWeek === null) return;

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
        LEFT JOIN weekly_stats ws ON p.id = ws.fantasypros_id AND ws.week = m.week
        LEFT JOIN weekly_projections wp ON p.id = wp.fantasypros_id AND wp.week = m.week
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
          INNER JOIN weekly_stats AS wk ON p.id = wk.fantasypros_id
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
        LEFT JOIN weekly_injuries AS wi ON p.id = wi.fantasypros_id AND wi.week = ${selectedWeek}
        LEFT JOIN weekly_projections AS wp ON p.id = wp.fantasypros_id AND wp.week = ${selectedWeek}
        ORDER BY b.name, p.full_name
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
  }, [db, activeTab, selectedWeek]);

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
            {tableColumns.map((col) => (
              <td key={col} style={{ border: `1px solid ${vars.border}`, padding: "0.5rem" }}>
                {row[col]}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <div style={{ padding: "2rem", fontFamily: "sans-serif", background: vars.background, color: vars.foreground, minHeight: "100vh", overflowX: "auto"}}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1rem" }}>
        <h1 style={{ margin: 0 }}>Botblitz 2025</h1>
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
      ) : (
        renderTable(sortData(data), columns)
      )}
    </div>
  );
}

export default App;
