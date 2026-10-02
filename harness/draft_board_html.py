"""Renders a completed mock draft (see harness.cli_draft) as a single static
HTML file: a real grid draft board (teams x rounds), not a per-team list.

Meant to be viewed in a browser, not printed to a terminal -- keeps repeated
draft runs from dumping a huge table into every conversation that needs one.
"""

import html
import os

from blitz_env.models import DatabaseManager
from harness.simulate_draft import get_picking_team_index

_POSITION_COLORS = {
    "QB": "#3b6fd4",
    "RB": "#1f9e6d",
    "WR": "#d4732e",
    "TE": "#8b5fc9",
    "K": "#b8952e",
    "DST": "#5b6470",
}


def build_board_data(db: DatabaseManager, user_bot_id: str) -> dict:
    bots = db.get_all_bots()
    settings = db.get_league_settings()
    players = db.get_all_players()
    sorted_bots = sorted(bots, key=lambda b: b.draft_order)
    bots_by_draft_order = {b.draft_order: b for b in bots}

    num_teams = len(sorted_bots)
    num_rounds = settings.total_rounds

    picks_by_number = {
        p.pick_chosen: p for p in players if p.availability == "DRAFTED" and p.pick_chosen
    }

    teams = [
        {"id": b.id, "name": b.name, "owner": b.owner, "is_user": b.id == user_bot_id}
        for b in sorted_bots
    ]

    grid = []
    for round_num in range(num_rounds):
        row = []
        for pos_in_round in range(num_teams):
            pick = round_num * num_teams + pos_in_round + 1
            team_index = get_picking_team_index(pick)
            bot = bots_by_draft_order.get(team_index + 1)
            player = picks_by_number.get(pick)
            if player:
                position = player.allowed_positions[0] if player.allowed_positions else "N/A"
                row.append({
                    "pick": pick,
                    "team_id": bot.id if bot else None,
                    "player_id": player.id,
                    "player": player.full_name,
                    "position": position,
                    "nfl_team": player.professional_team or "",
                })
            else:
                row.append({"pick": pick, "team_id": bot.id if bot else None, "player": None})
        grid.append(row)

    user_bot = next((b for b in sorted_bots if b.id == user_bot_id), None)
    roster = []
    if user_bot:
        my_players = sorted(
            (p for p in players if p.current_bot_id == user_bot_id),
            key=lambda p: p.pick_chosen or 0,
        )
        for p in my_players:
            position = p.allowed_positions[0] if p.allowed_positions else "N/A"
            roster.append({
                "pick": p.pick_chosen,
                "player": p.full_name,
                "position": position,
                "nfl_team": p.professional_team or "",
                "rank": p.rank,
            })

    position_counts: dict = {}
    for p in roster:
        position_counts[p["position"]] = position_counts.get(p["position"], 0) + 1

    return {
        "year": settings.year,
        "num_teams": num_teams,
        "num_rounds": num_rounds,
        "teams": teams,
        "grid": grid,
        "roster": roster,
        "position_counts": position_counts,
        "user_team_name": user_bot.name if user_bot else None,
    }


def _cell_html(cell: dict) -> str:
    if not cell.get("player"):
        return f'<div class="cell empty"><span class="pick-no">#{cell["pick"]}</span></div>'
    pos = cell["position"]
    color = _POSITION_COLORS.get(pos, "#5b6470")
    return (
        f'<div class="cell" style="--pos-color:{color}">'
        f'<span class="pick-no">#{cell["pick"]}</span>'
        f'<span class="pos-pill">{html.escape(pos)}</span>'
        f'<span class="player-name">{html.escape(cell["player"])}</span>'
        f'<span class="nfl-team">{html.escape(cell["nfl_team"])}</span>'
        f'</div>'
    )


def render_html(data: dict) -> str:
    teams = data["teams"]
    grid = data["grid"]
    roster = data["roster"]
    counts = data["position_counts"]

    col_headers = "".join(
        f'<div class="col-head{" user" if t["is_user"] else ""}">'
        f'<span class="team-name">{html.escape(t["name"])}</span>'
        f'<span class="team-owner">{html.escape(t["owner"])}</span>'
        f'</div>'
        for t in teams
    )

    rows_html = []
    for round_idx, row in enumerate(grid):
        cells = "".join(
            f'<div class="grid-cell{" user-col" if teams[i]["is_user"] else ""}">{_cell_html(row[i])}</div>'
            for i in range(len(teams))
        )
        rows_html.append(
            f'<div class="row-label">R{round_idx + 1}</div>'
            f'<div class="board-row">{cells}</div>'
        )

    roster_rows = "".join(
        f'<tr>'
        f'<td class="mono">{r["pick"]}</td>'
        f'<td>{html.escape(r["player"])}</td>'
        f'<td>{html.escape(r["nfl_team"])}</td>'
        f'<td><span class="pos-pill" style="--pos-color:{_POSITION_COLORS.get(r["position"], "#5b6470")}">{html.escape(r["position"])}</span></td>'
        f'<td class="mono">{r["rank"] if r["rank"] is not None else "—"}</td>'
        f'</tr>'
        for r in roster
    )

    count_chips = "".join(
        f'<div class="chip" style="--pos-color:{_POSITION_COLORS.get(pos, "#5b6470")}">'
        f'<span class="chip-pos">{html.escape(pos)}</span><span class="chip-n">{n}</span></div>'
        for pos, n in sorted(counts.items())
    )

    year = data["year"]
    user_team_name = html.escape(data["user_team_name"] or "")
    num_teams = data["num_teams"]

    return f"""<title>{year} Draft Board</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@700;800&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500&display=swap">
<style>
:root {{
  --bg: #f5f3ee;
  --surface: #ffffff;
  --surface-2: #ebe7de;
  --ink: #1b1d22;
  --ink-dim: #666b74;
  --line: #d8d3c7;
  --accent: #b3742c;
  --accent-soft: #b3742c22;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #14171c;
    --surface: #1b1f26;
    --surface-2: #20242c;
    --ink: #eceff2;
    --ink-dim: #9aa1ac;
    --line: #2c313a;
    --accent: #e0a75e;
    --accent-soft: #e0a75e26;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #14171c;
  --surface: #1b1f26;
  --surface-2: #20242c;
  --ink: #eceff2;
  --ink-dim: #9aa1ac;
  --line: #2c313a;
  --accent: #e0a75e;
  --accent-soft: #e0a75e26;
}}

* {{ box-sizing: border-box; }}
body {{
  background: var(--bg);
  color: var(--ink);
  font-family: 'IBM Plex Sans', system-ui, sans-serif;
  padding: 28px clamp(12px, 3vw, 40px) 60px;
}}
h1, .band-title {{
  font-family: 'Big Shoulders Display', system-ui, sans-serif;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.02em;
}}
.mono {{ font-family: 'IBM Plex Mono', ui-monospace, monospace; font-variant-numeric: tabular-nums; }}

header {{
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  border-bottom: 2px solid var(--line);
  padding-bottom: 16px;
  margin-bottom: 20px;
}}
h1 {{
  font-size: clamp(28px, 4vw, 44px);
  margin: 0;
  text-wrap: balance;
}}
h1 .year {{ color: var(--accent); }}
.subtitle {{ color: var(--ink-dim); font-size: 14px; margin-top: 4px; }}

.summary {{
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}}
.summary-label {{
  font-family: 'IBM Plex Mono', monospace;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--ink-dim);
  margin-right: 4px;
}}
.chip {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-left: 3px solid var(--pos-color);
  border-radius: 3px;
  padding: 4px 10px;
  font-size: 13px;
}}
.chip-pos {{ font-weight: 600; }}
.chip-n {{ font-family: 'IBM Plex Mono', monospace; color: var(--ink-dim); }}

.board-scroll {{
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface);
}}
.header-row {{
  display: grid;
  grid-template-columns: 44px repeat({num_teams}, minmax(120px, 1fr));
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--surface-2);
  border-bottom: 1px solid var(--line);
}}
.corner {{ border-right: 1px solid var(--line); }}
.col-head {{
  display: flex;
  flex-direction: column;
  padding: 10px 8px;
  border-right: 1px solid var(--line);
  min-width: 0;
}}
.col-head.user {{ background: var(--accent-soft); }}
.team-name {{
  font-weight: 700;
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.team-owner {{ font-size: 11px; color: var(--ink-dim); }}

.rows-wrap {{ display: grid; grid-template-columns: 44px 1fr; }}
.row-label {{
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 11px;
  color: var(--ink-dim);
  border-right: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  background: var(--surface-2);
}}
.board-row {{
  display: grid;
  grid-template-columns: repeat({num_teams}, minmax(120px, 1fr));
  border-bottom: 1px solid var(--line);
}}
.grid-cell {{
  border-right: 1px solid var(--line);
  min-width: 0;
}}
.grid-cell.user-col {{ background: var(--accent-soft); }}
.cell {{
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 8px;
  min-height: 62px;
  border-left: 3px solid var(--pos-color, transparent);
}}
.cell.empty {{ border-left-color: var(--line); justify-content: center; }}
.cell.empty .pick-no {{ color: var(--ink-dim); }}
.pick-no {{
  font-family: 'IBM Plex Mono', monospace;
  font-size: 10px;
  color: var(--ink-dim);
}}
.pos-pill {{
  align-self: flex-start;
  font-size: 10px;
  font-weight: 700;
  color: var(--pos-color);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}}
.player-name {{
  font-size: 13px;
  font-weight: 600;
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}}
.nfl-team {{ font-size: 11px; color: var(--ink-dim); }}

.roster-section {{ margin-top: 36px; }}
.roster-section h2 {{
  font-family: 'Big Shoulders Display', sans-serif;
  font-weight: 700;
  text-transform: uppercase;
  font-size: 20px;
  margin-bottom: 10px;
}}
table.roster {{
  width: 100%;
  max-width: 640px;
  border-collapse: collapse;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 6px;
  overflow: hidden;
}}
table.roster th {{
  text-align: left;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ink-dim);
  padding: 8px 12px;
  background: var(--surface-2);
  border-bottom: 1px solid var(--line);
}}
table.roster td {{
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
  font-size: 13px;
}}
table.roster tr:last-child td {{ border-bottom: none; }}
</style>

<header>
  <div>
    <h1><span class="year">{year}</span> Draft Board</h1>
    <div class="subtitle">{num_teams}-team snake draft &middot; highlighted column is {user_team_name}</div>
  </div>
  <div class="summary">
    <span class="summary-label">{user_team_name} roster</span>
    {count_chips}
  </div>
</header>

<div class="board-scroll">
  <div class="header-row">
    <div class="corner"></div>
    {col_headers}
  </div>
  <div class="rows-wrap">
    {''.join(rows_html)}
  </div>
</div>

<div class="roster-section">
  <h2>{user_team_name} — Full Roster</h2>
  <table class="roster">
    <thead><tr><th>Pick</th><th>Player</th><th>NFL Team</th><th>Pos</th><th>Rank</th></tr></thead>
    <tbody>{roster_rows}</tbody>
  </table>
</div>
"""


def write_html(db: DatabaseManager, user_bot_id: str, out_path: str) -> str:
    data = build_board_data(db, user_bot_id)
    page = render_html(data)
    os.makedirs(os.path.dirname(out_path), exist_ok=True) if os.path.dirname(out_path) else None
    with open(out_path, "w") as f:
        f.write(page)
    return out_path
