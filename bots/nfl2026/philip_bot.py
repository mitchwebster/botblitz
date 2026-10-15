"""Philip's 2026 fantasy bot.

League shape (pkg/engine/LeagueSettings.go): 14 teams, 12-round snake, PPR, starting
QB, 2 RB, 2 WR, FLEX (RB/WR/TE), SUPERFLEX (QB/RB/WR/TE), K, DST, plus 3 bench. There is
no dedicated TE slot. The engine starts each team's best lineup *after* the week's
scores are in, so depth that boosts a week's ceiling is worth real points.

The 2026 draft runs mid-season (after week 5), and the played weeks are scored
retroactively. So a player's value is the points he has already scored plus a
projection for the weeks still to come.

Projection (per player, per week):
- Prior per-game rate: the mean of the ffanalytics preseason projections
  (external_projections, week 0), falling back to FantasyPros preseason_projections,
  then last season's per-game average. preseason_projections alone covers only ~10
  players per position in 2026, which is why the 2025 version of this bot scored
  every player as "no projection".
- Blended with this season's per-game average as games come in.
- Availability from the latest injury report, plus missed games where the player's
  team played without him (a good IR signal).
- Byes and free agents from the schedule.
- Future weeks are sampled with per-position noise (same draws for a player every
  call), so a bench player gets credit for the weeks he would outscore a starter.

Draft: value-based drafting done against the real lineup. Each pick takes the player
who adds the most best-lineup points over weeks 1-17 to the roster so far, with open
slots filled at replacement level. That covers position scarcity (SUPERFLEX makes QBs
scarce), team needs and bye overlap at once. Points already scored this season are
credited at full value (they count the moment the player is drafted), so a hot start
is banked, not just a signal. K and DST wait for the last rounds, and one of each is
guaranteed. Near-ties go to the player with the better consensus rank, since he is
the one least likely to come back at the next pick.

Fully deterministic and offline: no AI or network calls, and any missing optional
table (projections, schedule, injuries) just falls back to the next data source. If
the model itself fails, the pick falls back to best available by consensus rank.

Weekly: FAAB add/drops judged on the weeks still to play, using only weeks before
the current fantasy week. Each target gets a backup claim with the same drop
player, so the backup only fires if the first claim loses (the engine skips a claim
whose drop player is already gone).
"""

import ast
import json
import zlib
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from sqlalchemy import text

from blitz_env import AttemptedFantasyActions, WaiverClaim
from blitz_env.models import DatabaseManager


SEASON_WEEKS = 17
PLAYOFF_WEEKS = (15, 16, 17)
PLAYOFF_WEIGHT = 1.25
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")
MULTI_SLOTS = {"FLEX": ("RB", "WR", "TE"), "SUPERFLEX": ("QB", "RB", "WR", "TE")}

# Prior vs. this season: weight on actual per-game points is games / (games + PRIOR_GAMES).
PRIOR_GAMES = {"QB": 3.0, "RB": 3.5, "WR": 4.5, "TE": 4.5, "K": 6.0, "DST": 6.0}
# Per-game points for a player with no projection or history (deep bench level).
DEFAULT_RATE = {"QB": 10.0, "RB": 4.0, "WR": 4.0, "TE": 3.0, "K": 6.0, "DST": 5.0}
# Week-to-week spread of a player's score around his rate (coefficient of variation).
WEEKLY_CV = {"QB": 0.40, "RB": 0.60, "WR": 0.65, "TE": 0.70, "K": 0.45, "DST": 0.70}
SCENARIOS = 12  # noise draws per future week

# Replacement level for a 14-team league: the Nth best player at the position overall.
REPLACEMENT_RANK = {"QB": 24, "RB": 36, "WR": 38, "TE": 12, "K": 14, "DST": 14}
REFERENCE_TEAMS = 14

# Draft guardrails.
MAX_DRAFTED = {"QB": 3, "TE": 2, "K": 1, "DST": 1}
MIN_DRAFTED = {"K": 1, "DST": 1}
SPECIALIST_ROUNDS = 3  # K/DST only in the last N rounds (unless forced)
TIE_BAND = 0.02  # within 2% (or 2 points) of the best gain counts as a near-tie
TIE_POINTS = 2.0

# Weekly guardrails.
MAX_CLAIMS = 10
DROP_CANDIDATES = 4
FA_PER_POSITION = 15
MIN_GAIN_PER_WEEK = 0.75
BID_PER_POINT = 3.0  # FAAB per point of weekly gain
MAX_BID_SHARE = 0.35  # of spendable budget, per claim
RESERVE_SHARE = 0.25  # held back until RESERVE_UNTIL_WEEK
RESERVE_UNTIL_WEEK = 12


def safe_positions(x) -> List[str]:
    """Safely parse allowed_positions field, handling various formats"""
    if x is None:
        return []
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        if not x:
            return []
        try:
            return json.loads(x)
        except json.JSONDecodeError:
            try:
                return ast.literal_eval(x)
            except Exception:
                return [p.strip() for p in x.split(",") if p.strip()]
    return []


def norm_id(value) -> Optional[str]:
    """Player ids are text in `players` but can read back as int or float elsewhere."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(value)


class PhilipFantasyBot:
    """
    Philip's Fantasy Football Bot (2026 edition)

    Strategy:
    - Value-based drafting measured as best-lineup points added
    - Position scarcity through replacement levels (SUPERFLEX-aware)
    - Team needs, byes and injuries handled by the lineup simulation
    - Near-ties broken by consensus rank
    - Risk management: K/DST late, position caps, conservative FAAB
    """

    def __init__(self):
        self.db = DatabaseManager()

    def close(self):
        if self.db:
            self.db.close()

    # ------------------------------------------------------------------ data

    def _read(self, sql: str, columns: List[str]) -> pd.DataFrame:
        """Optional table read: an empty frame (with the expected columns) on any error,
        so a missing or malformed table degrades the projection instead of the pick."""
        try:
            return pd.read_sql(sql, self.db.engine)
        except Exception as e:
            print(f"Could not read optional data ({e.__class__.__name__}); continuing without it")
            return pd.DataFrame(columns=columns)

    def load(self, known_upto: Optional[int] = None) -> Dict:
        """Read everything once. Weeks after `known_upto` are treated as unplayed."""
        settings = self.db.get_league_settings()
        status = self.db.get_game_status()
        year = int(settings.year)
        engine = self.db.engine

        players = pd.read_sql(
            "SELECT id, full_name, professional_team, player_bye_week, rank, "
            "allowed_positions, availability, current_bot_id FROM players",
            engine,
        )
        players["pid"] = players["id"].map(norm_id)
        players["pos"] = players["allowed_positions"].map(lambda v: (safe_positions(v) or [None])[0])
        players["rank"] = pd.to_numeric(players["rank"], errors="coerce")
        players = players[players["pos"].isin(POSITIONS) & players["pid"].notna()].copy()
        players = players.drop_duplicates("pid").reset_index(drop=True)

        stats = self._read(
            f"SELECT fantasypros_id, year, week, team, FPTS FROM weekly_stats "
            f"WHERE year IN ({year - 1}, {year})",
            ["fantasypros_id", "year", "week", "team", "FPTS"],
        )
        stats["pid"] = stats["fantasypros_id"].map(norm_id)
        stats["week"] = pd.to_numeric(stats["week"], errors="coerce")
        stats["year"] = pd.to_numeric(stats["year"], errors="coerce")
        stats["FPTS"] = pd.to_numeric(stats["FPTS"], errors="coerce")
        stats = stats[stats["week"].between(1, SEASON_WEEKS) & stats["pid"].notna()]
        this_year = stats[stats["year"] == year]
        last_played = int(this_year["week"].max()) if not this_year.empty else 0
        if known_upto is None:
            known_upto = last_played
        known_upto = max(0, min(known_upto, last_played))
        this_year = this_year[this_year["week"] <= known_upto]

        # The engine scores the max row per player-week (weekly_stats can repeat a row).
        actual = this_year.groupby(["pid", "week"])["FPTS"].max().dropna()
        actual = {(pid, int(wk)): float(v) for (pid, wk), v in actual.items()}
        team_weeks = {(t, int(w)) for t, w in zip(this_year["team"], this_year["week"])}

        last_year = stats[stats["year"] == year - 1].groupby("pid")["FPTS"].agg(["mean", "count"])
        prev_rate = (last_year[last_year["count"] >= 4]["mean"] * 0.9).to_dict()

        ext = self._read(
            f"SELECT fantasypros_id, source, position, points FROM external_projections "
            f"WHERE year = {year} AND week = 0",
            ["fantasypros_id", "source", "position", "points"],
        )
        ext["pid"] = ext["fantasypros_id"].map(norm_id)
        # FFToday's kicker season totals leave out field goals.
        ext = ext[~((ext["position"] == "K") & (ext["source"] == "FFToday"))]
        ext_rate = (pd.to_numeric(ext["points"], errors="coerce").groupby(ext["pid"]).mean()
                    / SEASON_WEEKS).dropna().to_dict()

        pre = self._read(
            f"SELECT fantasypros_id, FPTS FROM preseason_projections WHERE year = {year}",
            ["fantasypros_id", "FPTS"],
        )
        pre["pid"] = pre["fantasypros_id"].map(norm_id)
        pre_rate = (pd.to_numeric(pre["FPTS"], errors="coerce").groupby(pre["pid"]).mean()
                    / SEASON_WEEKS).dropna().to_dict()

        sched = self._read(f"SELECT team, week FROM schedule WHERE year = {year}", ["team", "week"])
        team_games: Dict[str, set] = {}
        for team, week in zip(sched["team"], sched["week"]):
            if team and pd.notna(week):
                team_games.setdefault(team, set()).add(int(week))

        inj = self._read(
            f"SELECT fantasypros_id, week, game_status FROM weekly_injuries "
            f"WHERE year = {year} AND week <= {max(known_upto, 1)}",
            ["fantasypros_id", "week", "game_status"],
        )
        injuries: Dict[str, Dict[int, str]] = {}
        for pid, week, gs in zip(inj["fantasypros_id"].map(norm_id), inj["week"], inj["game_status"]):
            if pid and pd.notna(week):
                injuries.setdefault(pid, {})[int(week)] = gs if isinstance(gs, str) else ""

        slots = settings.player_slots
        if isinstance(slots, str):
            slots = json.loads(slots)

        return {
            "year": year,
            "num_teams": int(settings.num_teams or REFERENCE_TEAMS),
            "total_rounds": int(settings.total_rounds),
            "slots": dict(slots or {}),
            "pick": int(status.current_draft_pick or 1),
            "current_week": status.current_fantasy_week,
            "bot_id": status.current_bot_id,
            "known_upto": known_upto,
            "last_played": last_played,
            "players": players,
            "actual": actual,
            "team_weeks": team_weeks,
            "team_games": team_games,
            "prev_rate": prev_rate,
            "ext_rate": ext_rate,
            "pre_rate": pre_rate,
            "injuries": injuries,
        }

    # ------------------------------------------------------------ projection

    def project(self, data: Dict, first_week: int = 1) -> Dict:
        """Per-player score matrix over weeks first_week..17.

        Returns {"cols": weights per column, "values": {pid: array}, "rate": {pid: rate},
        "avail": {pid: avail}}. Played weeks are one column each (actual points); future
        weeks are SCENARIOS columns each.
        """
        known = data["known_upto"]
        past = [w for w in range(first_week, known + 1)]
        future = [w for w in range(max(first_week, known + 1), SEASON_WEEKS + 1)]

        def weight(w):
            return PLAYOFF_WEIGHT if w in PLAYOFF_WEEKS else 1.0

        cols = [weight(w) for w in past]
        for w in future:
            cols += [weight(w) / SCENARIOS] * SCENARIOS
        cols = np.array(cols)

        games: Dict[str, List[float]] = {}
        for (pid, wk), pts in data["actual"].items():
            games.setdefault(pid, []).append(pts)

        values, rates, avails = {}, {}, {}
        for pid, team, bye, pos in zip(data["players"]["pid"], data["players"]["professional_team"],
                                       data["players"]["player_bye_week"], data["players"]["pos"]):
            prior = data["ext_rate"].get(pid) or data["pre_rate"].get(pid) \
                or data["prev_rate"].get(pid) or DEFAULT_RATE[pos]
            played = games.get(pid, [])
            n = len(played)
            if n:
                w_act = n / (n + PRIOR_GAMES[pos])
                rate = w_act * (sum(played) / n) + (1 - w_act) * prior
            else:
                rate = prior
            rate = max(rate, 0.0)
            avail = self._availability(pid, team, data)

            vec = np.zeros(len(cols))
            i = 0
            for w in past:
                if (pid, w) in data["actual"]:
                    vec[i] = data["actual"][(pid, w)]
                elif (team, w) not in data["team_weeks"] and self._has_game(team, bye, w, data):
                    vec[i] = rate  # scheduled game with no stats at all: data gap
                i += 1
            if future:
                rng = np.random.default_rng(zlib.crc32(f"{pid}-{data['year']}".encode()))
                k = 1.0 / WEEKLY_CV[pos] ** 2
                noise = rng.gamma(k, 1.0 / k, size=(len(future), SCENARIOS))
                plays = rng.random((len(future), SCENARIOS)) < avail
                for j, w in enumerate(future):
                    if self._has_game(team, bye, w, data):
                        vec[i:i + SCENARIOS] = rate * noise[j] * plays[j]
                    i += SCENARIOS
            values[pid] = vec
            rates[pid] = rate
            avails[pid] = avail
        return {"cols": cols, "values": values, "rate": rates, "avail": avails}

    @staticmethod
    def _has_game(team, bye, week, data) -> bool:
        tg = data["team_games"]
        if tg:
            return team in tg and week in tg[team]  # 'FA' and byes play no game
        if not isinstance(team, str) or team in ("", "FA"):
            return False
        return not (bye is not None and pd.notna(bye) and int(bye) == week)

    @staticmethod
    def _availability(pid, team, data) -> float:
        """Share of future games a player is expected to play."""
        known = data["known_upto"]
        reports = data["injuries"].get(pid, {})
        now, before = reports.get(known), reports.get(known - 1)
        avail = 1.0
        if now == "Out" and before == "Out":
            avail = 0.3
        elif now == "Out":
            avail = 0.6
        elif now == "Doubtful":
            avail = 0.65
        elif now == "Questionable":
            avail = 0.9

        # Team played the last two weeks and he didn't: likely IR, which drops him off
        # the injury report entirely.
        recent = [w for w in (known, known - 1) if w >= 1 and (team, w) in data["team_weeks"]]
        if len(recent) == 2 and not any((pid, w) in data["actual"] for w in recent):
            played_before = any((pid, w) in data["actual"] for w in range(1, known - 1))
            avail = min(avail, 0.35 if played_before else 0.5)
        return avail

    # ---------------------------------------------------------------- lineup

    @staticmethod
    def starting_slots(player_slots: Dict) -> List[tuple]:
        """One eligibility tuple per starting slot, narrowest first. BENCH never scores."""
        slots = []
        for name, count in player_slots.items():
            if name != "BENCH":
                slots += [MULTI_SLOTS.get(name, (name,))] * int(count)
        return sorted(slots, key=len)

    @staticmethod
    def lineup_points(slots: List[tuple], positions: List[str], matrix: np.ndarray,
                      cols: np.ndarray) -> float:
        """Weighted best-lineup points. matrix: players x columns. Each slot (narrowest
        first) takes the best remaining eligible player in every column, which is optimal
        for these nested slot sets and matches the engine's scoreTeam."""
        if matrix.shape[0] == 0:
            return 0.0
        m = np.maximum(matrix, 0.0).copy()  # a bad week is benched, never forced in
        pos = np.array(positions)
        total = np.zeros(m.shape[1])
        rows = np.arange(m.shape[1])
        for allowed in slots:
            elig = np.isin(pos, allowed)
            if not elig.any():
                continue
            masked = np.where(elig[:, None], m, -1.0)
            best = masked.argmax(axis=0)
            gain = masked[best, rows]
            take = gain > 0
            total += np.where(take, gain, 0.0)
            m[best[take], rows[take]] = -1.0  # used
        return float(total @ cols)

    # ----------------------------------------------------------------- draft

    def replacement_rows(self, slots, available: pd.DataFrame, data, proj) -> List[tuple]:
        """Stand-ins for open starting slots: per position, the player at replacement
        rank among those still available (shifted by how many were already drafted)."""
        players = data["players"]
        scale = data["num_teams"] / REFERENCE_TEAMS
        totals = {pid: float(v @ proj["cols"]) for pid, v in proj["values"].items()}
        rows = []
        for pos in POSITIONS:
            copies = sum(pos in allowed for allowed in slots)
            if not copies:
                continue
            drafted = int(((players["pos"] == pos) & (players["availability"] != "AVAILABLE")).sum())
            pool = sorted(available[available["pos"] == pos]["pid"], key=lambda p: -totals[p])
            if not pool:
                continue
            idx = max(0, round(REPLACEMENT_RANK[pos] * scale) - drafted)
            group = pool[min(idx, len(pool) - 1):][:3] or pool[-3:]
            value = np.mean([proj["values"][p] for p in group], axis=0)
            rows += [(pos, value)] * copies
        return rows

    def draft_player(self) -> str:
        data = self.load()
        proj = self.project(data, first_week=1)
        slots = self.starting_slots(data["slots"])
        cols = proj["cols"]
        players = data["players"]
        num_teams, total_rounds, pick = data["num_teams"], data["total_rounds"], data["pick"]
        current_round = (pick - 1) // num_teams + 1
        print(f"=== Draft Round {current_round} of {total_rounds} (pick {pick}) ===")

        available = players[players["availability"] == "AVAILABLE"]
        mine = players[(players["current_bot_id"] == data["bot_id"]) & (players["availability"] != "AVAILABLE")]
        my_pos = list(mine["pos"])
        print("Current roster: " + (", ".join(f"{n} ({p})" for n, p in zip(mine["full_name"], mine["pos"])) or "Empty"))

        # Candidates after guardrails.
        turns_left = self._turns_remaining(pick, num_teams, total_rounds)
        missing = [p for p, n in MIN_DRAFTED.items() if my_pos.count(p) < n]
        capped = {p for p, n in MAX_DRAFTED.items() if my_pos.count(p) >= n}
        candidates = available[~available["pos"].isin(capped)]
        if missing and len(missing) >= turns_left:
            forced = candidates[candidates["pos"].isin(missing)]
            if not forced.empty:
                candidates = forced
        elif current_round <= total_rounds - SPECIALIST_ROUNDS:
            candidates = candidates[~candidates["pos"].isin(["K", "DST"])]
        if candidates.empty:
            candidates = available
        if candidates.empty:
            return ""

        fill = self.replacement_rows(slots, available, data, proj)
        roster_pos = my_pos + [p for p, _ in fill]
        roster_mat = [proj["values"][p] for p in mine["pid"]] + [v for _, v in fill]
        base_mat = np.array(roster_mat) if roster_mat else np.zeros((0, len(cols)))
        base = self.lineup_points(slots, roster_pos, base_mat, cols)

        gains = []
        for pid, pos in zip(candidates["pid"], candidates["pos"]):
            mat = np.vstack([base_mat, proj["values"][pid][None, :]])
            gains.append((self.lineup_points(slots, roster_pos + [pos], mat, cols) - base, pid))
        gains.sort(reverse=True)

        info = players.set_index("pid")
        print("Top 5 by lineup points added:")
        for g, pid in gains[:5]:
            print(f"  {info.at[pid, 'full_name']} ({info.at[pid, 'pos']}) +{g:.1f} "
                  f"rate {proj['rate'][pid]:.1f} avail {proj['avail'][pid]:.2f} rank {info.at[pid, 'rank']}")

        # Near-ties: take the one the rest of the league will grab first.
        best_gain = gains[0][0]
        band = [pid for g, pid in gains if g >= best_gain - max(TIE_POINTS, TIE_BAND * abs(best_gain))]
        choice = min(band, key=lambda pid: (np.nan_to_num(info.at[pid, "rank"], nan=1e9), band.index(pid)))
        print(f"Drafting {info.at[choice, 'full_name']} ({info.at[choice, 'pos']}) (ID: {choice})")
        return choice

    @staticmethod
    def _turns_remaining(pick: int, num_teams: int, total_rounds: int) -> int:
        """Our turns from `pick` to the end of the draft, counting this one."""
        def seat(p):
            rnd, k = divmod(p - 1, num_teams)
            return k if rnd % 2 == 0 else num_teams - 1 - k
        me = seat(pick)
        return sum(1 for p in range(pick, num_teams * total_rounds + 1) if seat(p) == me)

    def fallback_pick(self) -> str:
        """Best available by consensus rank, K/DST last."""
        players = pd.read_sql("SELECT id, rank, allowed_positions, availability FROM players", self.db.engine)
        players = players[players["availability"] == "AVAILABLE"].copy()
        players["pos"] = players["allowed_positions"].map(lambda v: (safe_positions(v) or [None])[0])
        players["rank"] = pd.to_numeric(players["rank"], errors="coerce")
        early = players[~players["pos"].isin(["K", "DST"])]
        pool = early if not early.empty else players
        if pool.empty:
            return ""
        return norm_id(pool.sort_values("rank", na_position="last").iloc[0]["id"]) or ""

    # ---------------------------------------------------------------- weekly

    def remaining_budget(self, bot_id) -> int:
        try:
            value = self.db.session.execute(
                text("SELECT remaining_waiver_budget FROM bots WHERE id = :id"), {"id": bot_id}
            ).scalar()
            return max(0, int(value or 0))
        except Exception:
            self.db.session.rollback()
            return 0

    def weekly_claims(self) -> List[WaiverClaim]:
        status = self.db.get_game_status()
        week, bot_id = status.current_fantasy_week, status.current_bot_id
        if not bot_id or not week or int(week) > SEASON_WEEKS:
            return []
        week = int(week)

        # No hindsight: only weeks before the current fantasy week are known.
        data = self.load(known_upto=week - 1)
        proj = self.project(data, first_week=week)
        cols, vals = proj["cols"], proj["values"]
        slots = self.starting_slots(data["slots"])
        players = data["players"]
        info = players.set_index("pid")
        weeks_left = SEASON_WEEKS - week + 1

        roster = {pid: pos for pid, pos, a, b in zip(players["pid"], players["pos"], players["availability"],
                                                     players["current_bot_id"]) if b == bot_id and a != "AVAILABLE"}
        if not roster:
            return []
        free = players[players["availability"] == "AVAILABLE"]
        shortlist = []
        for pos in POSITIONS:
            ids = sorted(free[free["pos"] == pos]["pid"], key=lambda p: -float(vals[p] @ cols))
            shortlist += [(p, pos) for p in ids[:FA_PER_POSITION]]

        def points(ros: Dict[str, str]) -> float:
            ids = list(ros)
            return self.lineup_points(slots, [ros[p] for p in ids], np.array([vals[p] for p in ids]), cols)

        budget = self.remaining_budget(bot_id)
        reserve = int(budget * RESERVE_SHARE) if week < RESERVE_UNTIL_WEEK else 0
        spendable = max(0, budget - reserve)
        cap = int(spendable * MAX_BID_SHARE) if week < RESERVE_UNTIL_WEEK else spendable

        spent = 0  # by the primary claims so far; a backup shares its primary's money

        def bid_for(gain):
            return max(0, min(int(round(BID_PER_POINT * gain / weeks_left)), cap, spendable - spent))

        current = dict(roster)
        droppable = set(roster)  # the engine checks drops against the roster at submission
        targeted = set()
        claims = []
        while len(claims) < MAX_CLAIMS and droppable:
            base = points(current)
            losses = {p: base - points({k: v for k, v in current.items() if k != p})
                      for p in current if p in droppable}
            drops = sorted(losses, key=losses.get)[:DROP_CANDIDATES]

            options = []  # (gain, add, pos, drop)
            for fa, pos in shortlist:
                if fa in targeted:
                    continue
                for drop in drops:
                    trial = {k: v for k, v in current.items() if k != drop}
                    trial[fa] = pos
                    options.append((points(trial) - base, fa, pos, drop))
            options.sort(reverse=True)
            if not options or options[0][0] < MIN_GAIN_PER_WEEK * weeks_left:
                break

            gain, fa, pos, drop = options[0]
            bid = bid_for(gain)
            claims.append(WaiverClaim(player_to_add_id=fa, player_to_drop_id=drop, bid_amount=bid))
            print(f"Claim: add {info.at[fa, 'full_name']} ({pos}) drop {info.at[drop, 'full_name']} "
                  f"+{gain / weeks_left:.2f}/wk bid {bid}")
            # Backup: same drop, so it only goes through if the claim above loses.
            backup = next((o for o in options[1:] if o[3] == drop and o[1] != fa
                           and o[0] >= MIN_GAIN_PER_WEEK * weeks_left), None)
            if backup and len(claims) < MAX_CLAIMS:
                # Not marked as targeted: he can still headline a later claim with another
                # drop. If he arrives here first, the engine skips that later claim.
                claims.append(WaiverClaim(player_to_add_id=backup[1], player_to_drop_id=drop,
                                          bid_amount=bid_for(backup[0])))
                print(f"  backup: add {info.at[backup[1], 'full_name']} ({backup[2]}) +{backup[0] / weeks_left:.2f}/wk")

            spent += bid
            targeted.add(fa)
            droppable.discard(drop)
            del current[drop]
            current[fa] = pos
        return claims


def draft_player() -> str:
    """
    Entry point function required by the bot interface

    Returns:
        str: Player ID to draft
    """
    bot = None
    try:
        bot = PhilipFantasyBot()
        return bot.draft_player()
    except Exception as e:
        print(f"Error in draft_player: {e}; falling back to best available by rank")
        try:
            return bot.fallback_pick() if bot else ""
        except Exception:
            return ""  # empty string makes the engine pick for us
    finally:
        if bot:
            bot.close()


def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    """Entry point: this week's waiver claims. No claims on any error."""
    bot = None
    claims = []
    try:
        bot = PhilipFantasyBot()
        claims = bot.weekly_claims()
    except Exception as e:
        print(f"Error in perform_weekly_fantasy_actions: {e}")
        claims = []
    finally:
        if bot:
            bot.close()
    return AttemptedFantasyActions(waiver_claims=claims)


# Test the bot if run directly
if __name__ == "__main__":
    print("Testing Philip's Fantasy Football Bot...")
    result = draft_player()
    print(f"Draft result: {result}")
