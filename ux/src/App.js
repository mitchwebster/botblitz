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

  useEffect(() => {
    if (!db) return;

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

  if (loading) return <p>Loading database...</p>;

  const tabs = [
    { key: "current", label: "Current Week" },
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
        border: "1px solid #ccc",
        marginBottom: "2rem",
      }}
    >
      <thead>
        <tr>
          {tableColumns.map((col) => (
            <th
              key={col}
              style={{
                border: "1px solid #ccc",
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
              <td key={col} style={{ border: "1px solid #ccc", padding: "0.5rem" }}>
                {row[col]}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <div style={{ padding: "2rem", fontFamily: "sans-serif" }}>
      <h1>Botblitz 2025</h1>

      {/* Tabs */}
      <div style={{ marginBottom: "1rem" }}>
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            style={{
              padding: "0.5rem 1rem",
              marginRight: "0.5rem",
              backgroundColor: activeTab === tab.key ? "#007bff" : "#eee",
              color: activeTab === tab.key ? "#fff" : "#000",
              border: "none",
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
          style={{ marginBottom: "1rem", padding: "0.5rem", width: "100%" }}
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
