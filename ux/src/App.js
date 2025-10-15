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

        const response = await fetch(
          "https://raw.githubusercontent.com/mitchwebster/botblitz/main/data/game_states/2025/gs-season.db"
        );
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

  useEffect(() => {
    if (!db) return;

    const fetchCurrentWeek = () => {
      if (!db) return null;
      try {
        const result = db.exec(
          "SELECT current_fantasy_week FROM game_statuses LIMIT 1;"
        );
        if (result.length > 0 && result[0].values.length > 0) {
          return result[0].values[0][0]; 
        }
      } catch (err) {
        console.error("Failed to fetch current week:", err);
      }
      return null;
    };

    const week = fetchCurrentWeek();
    if (week == null) return;

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
        WHERE week = ${week}
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
        WHERE week = ${week - 1}
      `,
      leaderboard: `
        WITH botScores AS (
          SELECT 
            b.Name,
            SUM(IIF(b.id = m.home_bot_id, home_score, 0) + IIF(b.id = m.visitor_bot_id, visitor_score, 0)) AS totalPoints,
            SUM(IIF(b.id = m.winning_bot_id, 1, 0)) AS numWins,
            SUM(IIF(b.id != m.winning_bot_id, 1, 0)) AS numLosses
          FROM bots AS b
          INNER JOIN matchups AS m
          ON (b.id = m.visitor_bot_id OR b.id = m.home_bot_id) 
          WHERE week < ${week}
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
        SELECT p.id, p.full_name, p.allowed_positions, b.name AS teamName, totalPoints
        FROM playerPoints AS p
        LEFT JOIN bots AS b ON p.current_bot_id = b.id
        ORDER BY b.name, p.full_name
      `,
    };

    const query = queries[activeTab];
    if (!query) return;

    try {
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
    }
  }, [db, activeTab]);

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

  useEffect(() => {
    document.body.style.backgroundColor = vars.background;
    document.body.style.color = vars.foreground;
  }, [effectiveTheme]);

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
    <div style={{ padding: "2rem", fontFamily: "sans-serif", background: vars.background, color: vars.foreground, minHeight: "100vh", overflowX: "auto" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1rem" }}>
        <h1 style={{ margin: 0 }}>Botblitz 2025</h1>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
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
      ) : (
        renderTable(sortData(data), columns)
      )}
    </div>
  );
}

export default App;
