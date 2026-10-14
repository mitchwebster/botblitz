"""Ben's 2026 draft bot.

The 2026 league drafts mid-season and scores the already-played weeks retroactively, so a
player's season value is the points he has already scored (known) plus a projection for
the weeks still to come.

Each pick maximizes the expected gain in best-ball season points: every week the engine
starts my best possible lineup, so I simulate weeks 1-17 (actual points for the weeks
already played, projection x weekly noise for the rest) and score the optimal lineup each
week. Open roster spots are filled with replacement-level players (what I could still get
later), so a 3rd QB or 6th WR only counts for the weeks he'd actually beat that.
"""
import json

import numpy as np
import pandas as pd

from blitz_env import AttemptedFantasyActions
from blitz_env.models import DatabaseManager

FINAL_WEEK = 17
SOURCES = ("ESPN", "FFToday", "FantasySharks")

# Weight on a player's actual points-per-game so far vs. his projection, by position.
# Fit on 2025 (wk 1-4 ppg vs. preseason as predictors of wk 5-17 ppg); one season, so these
# are deliberately rounded starting points rather than tuned constants.
ACTUAL_WEIGHT = {"QB": 0.7, "RB": 0.6, "WR": 0.4, "TE": 0.4, "K": 0.3, "DST": 0.3}

# Slots filled in this order: dedicated slots first, then the flexible ones, so the best
# players land where only they can go (optimal for these nested eligibility sets).
FLEX_ELIGIBLE = {
    "FLEX": ("RB", "WR", "TE"),
    "SUPERFLEX": ("QB", "RB", "WR", "TE"),
}

# Weekly noise around a player's projected points-per-game: 0 with probability ZERO (injury,
# dud), otherwise Gamma with mean 1/(1-ZERO) and shape chosen so the overall coefficient of
# variation is CV. Fit on 2025 weekly scores of startable players.
WEEKLY_CV = {"QB": 0.56, "RB": 0.68, "WR": 0.70, "TE": 0.68, "K": 0.53, "DST": 0.75}
WEEKLY_ZERO = {"QB": 0.09, "RB": 0.07, "WR": 0.10, "TE": 0.07, "K": 0.05, "DST": 0.06}
N_SIMS = 200

# Replacement-level stand-ins per position: enough to fill every starting slot that
# position can play, so an empty slot is valued at what I could still draft later.
PAD_DEPTH = {"QB": 2, "RB": 4, "WR": 4, "TE": 2, "K": 1, "DST": 1}


def _position(allowed_positions: str) -> str:
    return json.loads(allowed_positions)[0] if allowed_positions else ""


def _load(db: DatabaseManager):
    settings = pd.read_sql("SELECT * FROM league_settings", db.engine).iloc[0]
    status = pd.read_sql("SELECT * FROM game_statuses", db.engine).iloc[0]
    year = int(settings["year"])
    # Guard against a league year with no data loaded (e.g. a scratch year): use the latest
    # year that has projections instead of drafting blind.
    has = pd.read_sql(f"SELECT 1 FROM external_projections WHERE CAST(year AS INTEGER) = {year} LIMIT 1", db.engine)
    if has.empty:
        year = int(pd.read_sql("SELECT MAX(CAST(year AS INTEGER)) AS y FROM external_projections", db.engine)["y"].iloc[0])
    players = pd.read_sql("SELECT * FROM players", db.engine)
    players["position"] = players["allowed_positions"].apply(_position)

    # week is TEXT in some season.db files: always CAST, never compare raw.
    weekly = pd.read_sql(
        f"SELECT fantasypros_id AS id, CAST(week AS INTEGER) AS week, MAX(FPTS) AS pts "
        f"FROM weekly_stats WHERE CAST(year AS INTEGER) = {year} "
        f"AND fantasypros_id IS NOT NULL GROUP BY 1, 2",
        db.engine,
    )
    proj = pd.read_sql(
        f"SELECT fantasypros_id AS id, CAST(week AS INTEGER) AS week, source, points "
        f"FROM external_projections WHERE CAST(year AS INTEGER) = {year}",
        db.engine,
    )
    return settings, status, players, weekly, proj


def season_values(players, weekly, proj) -> pd.DataFrame:
    """Known points so far + projected points for the remaining weeks, per player."""
    # Last fully-played week. A week only counts once it has at least half the rows of a
    # full week, so a Thursday-night game loaded early can't push the cutoff forward.
    rows = weekly.groupby("week").size()
    full = rows[rows >= 0.5 * rows.max()].index if not rows.empty else []
    cutoff = int(max(full)) if len(full) else 0
    played = weekly[weekly["week"] <= cutoff]

    known = played.groupby("id")["pts"].agg(known="sum", games="count", act_ppg="mean")

    proj = proj[proj["source"].isin(SOURCES)]
    pre_ppg = proj[proj["week"] == 0].groupby("id")["points"].mean() / 17
    near_ppg = proj[proj["week"] > cutoff].groupby("id")["points"].mean()

    df = players.set_index("id").join(known).join(pre_ppg.rename("pre_ppg")).join(near_ppg.rename("near_ppg"))
    df[["known", "games"]] = df[["known", "games"]].fillna(0)

    # Projection: average of the preseason and near-term views that exist.
    df["proj_ppg"] = df[["pre_ppg", "near_ppg"]].mean(axis=1)

    # Shrink toward the projection when the player missed games (small sample).
    w_pos = df["position"].map(ACTUAL_WEIGHT).fillna(0.5)
    w = w_pos * (df["games"] / max(cutoff, 1)).clip(upper=1)
    no_proj = df["proj_ppg"].isna()
    df["ros_ppg"] = np.where(
        no_proj,
        df["act_ppg"].fillna(0) * 0.8,  # unprojected: trust actuals, discounted
        w * df["act_ppg"].fillna(df["proj_ppg"]) + (1 - w) * df["proj_ppg"],
    )

    remaining = np.arange(cutoff + 1, FINAL_WEEK + 1)
    bye = pd.to_numeric(df["player_bye_week"], errors="coerce")
    df["games_left"] = len(remaining) - bye.isin(remaining).astype(int)
    df["value"] = df["known"] + df["ros_ppg"] * df["games_left"]
    df.attrs["cutoff"] = cutoff
    return df.reset_index()


def replacement_players(df: pd.DataFrame, num_teams: int, slots: dict) -> dict:
    """Index (in df) of the best player per position left after every team fills its starting lineup."""
    pool = df.sort_values("value", ascending=False)
    taken = set()
    for slot in [s for s in slots if s not in FLEX_ELIGIBLE and s != "BENCH"] + list(FLEX_ELIGIBLE):
        eligible = FLEX_ELIGIBLE.get(slot, (slot,))
        need = slots.get(slot, 0) * num_teams
        picks = pool[pool["position"].isin(eligible) & ~pool["id"].isin(taken)].head(need)
        taken.update(picks["id"])
    left = pool[~pool["id"].isin(taken)]
    return left.groupby("position")["value"].idxmax().to_dict()


def simulate_weeks(df: pd.DataFrame, weekly: pd.DataFrame, cutoff: int, n_sims: int = N_SIMS,
                   seed: int = 0) -> np.ndarray:
    """Weekly points, shape (len(df), n_sims, FINAL_WEEK). Weeks <= cutoff are the actual
    (known) scores in every sim; later weeks are ros_ppg x noise, 0 on the bye."""
    rng = np.random.default_rng(seed)
    n, weeks = len(df), FINAL_WEEK
    out = np.zeros((n, n_sims, weeks))

    if cutoff > 0:
        played = weekly[(weekly["week"] >= 1) & (weekly["week"] <= cutoff)]
        known = played.pivot_table(index="id", columns="week", values="pts", aggfunc="max")
        known = known.reindex(index=df["id"], columns=range(1, cutoff + 1)).fillna(0).to_numpy()
        out[:, :, :cutoff] = known[:, None, :]

    future = weeks - cutoff
    if future > 0:
        cv = df["position"].map(WEEKLY_CV).fillna(0.7).to_numpy()[:, None, None]
        zero = df["position"].map(WEEKLY_ZERO).fillna(0.08).to_numpy()[:, None, None]
        shape = 1.0 / np.maximum((cv ** 2 + 1) * (1 - zero) - 1, 0.05)
        noise = rng.gamma(shape, 1.0 / (shape * (1 - zero)), size=(n, n_sims, future))
        noise *= rng.random((n, n_sims, future)) >= zero
        out[:, :, cutoff:] = df["ros_ppg"].fillna(0).clip(lower=0).to_numpy()[:, None, None] * noise
        bye = pd.to_numeric(df["player_bye_week"], errors="coerce").to_numpy()
        for i in np.flatnonzero((bye > cutoff) & (bye <= weeks)):
            out[i, :, int(bye[i]) - 1] = 0.0
    return out


def _top(points: np.ndarray, depth: int) -> np.ndarray:
    """Best `depth` rows per (sim, week), highest first, zero-padded."""
    if len(points) < depth:
        points = np.concatenate([points, np.zeros((depth - len(points),) + points.shape[1:])])
    return -np.sort(-points, axis=0)


def best_ball(points: np.ndarray, positions: np.ndarray, slots: dict) -> float:
    """Expected season total of the optimal lineup, chosen fresh each week.

    Dedicated slots take the best at their position; FLEX then takes the best remaining
    FLEX-eligible players, and SUPERFLEX the best of what's left (optimal because FLEX's
    eligibility is a subset of SUPERFLEX's)."""
    flex, sflex = slots.get("FLEX", 0), slots.get("SUPERFLEX", 0)
    total = 0.0
    leftover = {}
    for pos in set(positions) | {p for p in slots if p not in FLEX_ELIGIBLE and p != "BENCH"}:
        need = slots.get(pos, 0)
        ranked = _top(points[positions == pos], need + flex + sflex)
        total += ranked[:need].sum()
        leftover[pos] = ranked[need:need + flex + sflex]
    empty = np.zeros((0,) + points.shape[1:])
    pool = _top(np.concatenate([leftover.get(p, empty) for p in FLEX_ELIGIBLE["FLEX"]]), flex + sflex)
    total += pool[:flex].sum()
    sf_only = [p for p in FLEX_ELIGIBLE["SUPERFLEX"] if p not in FLEX_ELIGIBLE["FLEX"]]
    pool = _top(np.concatenate([pool[flex:]] + [leftover.get(p, empty) for p in sf_only]), sflex)
    total += pool[:sflex].sum()
    return total / points.shape[1]


LOOKAHEAD_FIRST = 10   # candidates (by immediate gain) to look ahead from
LOOKAHEAD_NEXT = 5     # next-pick options per position


def _picks_until_next(pick: int, num_teams: int, rounds: int):
    """Picks other teams make between my current pick (1-based overall, snake) and my
    next one; None when this is my last pick."""
    rnd, k = divmod(pick - 1, num_teams)
    if rnd + 1 >= rounds:
        return None
    seat = k if rnd % 2 == 0 else num_teams - 1 - k
    nxt = (rnd + 1) * num_teams + (num_teams - 1 - seat if rnd % 2 == 0 else seat) + 1
    return nxt - pick - 1


def _next_pick_gain(candidates, available, mine, status, settings, slots, points, positions,
                    base_rows, n_mine, weekly, cutoff) -> pd.Series:
    """Expected extra best-ball gain from my *next* pick if I take each candidate now.

    Opponents are predicted to take the top available players either by consensus rank
    (ADP-style bots) or by my own value (bots that also use the played weeks); the two
    scenarios are averaged. Maximizing gain now + gain next is what makes the bot take a
    position that will dry up before my next turn over one that will still be there."""
    out = pd.Series(0.0, index=candidates.index)
    rounds = int(settings["total_rounds"])
    between = _picks_until_next(int(status["current_draft_pick"]), int(settings["num_teams"]), rounds)
    if between is None or len(mine) + 1 >= rounds:
        return out
    rounds_left = rounds - len(mine) - 1

    # Next-pick options: deeper than this pick's candidate list, since some will be gone.
    row = {idx: n_mine + j for j, idx in enumerate(candidates.index)}
    pool = available[~available["position"].isin(["K", "DST"])] if rounds_left > 2 else available
    extra = pool.sort_values("value", ascending=False).groupby("position").head(LOOKAHEAD_NEXT + between)
    extra = extra[~extra.index.isin(row)]
    if not extra.empty:
        points = np.concatenate([points, simulate_weeks(extra, weekly, cutoff, seed=1)])
        positions = np.concatenate([positions, extra["position"].to_numpy()])
        row.update({idx: len(positions) - len(extra) + j for j, idx in enumerate(extra.index)})
    options = available.loc[list(row)]

    rank = pd.to_numeric(available["rank"], errors="coerce").where(lambda r: r > 0)
    orders = (list(rank.sort_values(na_position="last").index),
              list(available["value"].sort_values(ascending=False).index))

    first = candidates.nlargest(LOOKAHEAD_FIRST, "gain").index
    for i in first:
        with_i = np.r_[base_rows, row[i]]
        now = best_ball(points[with_i], positions[with_i], slots)
        my_pos = list(mine["position"]) + [candidates.at[i, "position"]]
        missing_kd = [p for p in ("K", "DST") if my_pos.count(p) < slots.get(p, 0)]
        total = 0.0
        for order in orders:
            gone = set([x for x in order if x != i][:between]) | {i}
            nxt = options[~options.index.isin(gone)]
            if rounds_left <= len(missing_kd):
                nxt = nxt[nxt["position"].isin(missing_kd)]
            else:
                nxt = nxt[~nxt["position"].isin(["K", "DST"])]
            nxt = nxt.sort_values("value", ascending=False).groupby("position").head(LOOKAHEAD_NEXT)
            best = 0.0
            for x in nxt.index:
                rows = np.r_[with_i, row[x]]
                best = max(best, best_ball(points[rows], positions[rows], slots) - now)
            total += best
        out[i] = total / len(orders)
    # Candidates not looked ahead from get the weakest lookahead seen, so they aren't favored.
    rest = ~out.index.isin(first)
    out[rest] = out[first].min()
    return out


def _pick(db: DatabaseManager) -> str:
    settings, status, players, weekly, proj = _load(db)
    slots = json.loads(settings["player_slots"])
    num_teams = int(settings["num_teams"])
    rounds = int(settings["total_rounds"])

    df = season_values(players, weekly, proj)
    cutoff = df.attrs["cutoff"]
    repl = replacement_players(df, num_teams, slots)
    df["vorp"] = df["value"] - df["position"].map({p: df.at[i, "value"] for p, i in repl.items()}).fillna(0)

    me = status["current_bot_id"]
    mine = df[df["current_bot_id"] == me]
    available = df[df["availability"] == "AVAILABLE"]

    # K and DST barely separate from replacement, so take exactly one each, last.
    rounds_left = rounds - len(mine)
    missing_kd = [p for p in ("K", "DST") if (mine["position"] == p).sum() < slots.get(p, 0)]
    if rounds_left <= len(missing_kd):
        available = available[available["position"].isin(missing_kd)]
    else:
        available = available[~available["position"].isin(["K", "DST"])]

    # Only the top candidates per position can be the best pick.
    candidates = available.sort_values("value", ascending=False).groupby("position").head(8)

    # One simulation for everyone involved (common random numbers keep comparisons fair).
    pads = [i for p, i in repl.items() for _ in range(PAD_DEPTH.get(p, 0))]
    sim_df = pd.concat([mine, candidates, df.loc[pads]], ignore_index=True)
    points = simulate_weeks(sim_df, weekly, cutoff)
    positions = sim_df["position"].to_numpy()
    n_mine, n_cand = len(mine), len(candidates)
    base_rows = np.r_[0:n_mine, n_mine + n_cand:len(sim_df)]

    base = best_ball(points[base_rows], positions[base_rows], slots)
    gains = []
    for j in range(n_cand):
        rows = np.r_[base_rows, n_mine + j]
        gains.append(best_ball(points[rows], positions[rows], slots) - base)
    candidates = candidates.assign(gain=gains)
    candidates["total"] = candidates["gain"] + _next_pick_gain(
        candidates, available, mine, status, settings, slots, points, positions, base_rows, n_mine, weekly, cutoff)
    best = candidates.sort_values(["total", "gain"], ascending=False).iloc[0]
    print(f"[ben_bot] wk<= {cutoff} known | pick {best['full_name']} "
          f"({best['position']}) value={best['value']:.1f} vorp={best['vorp']:.1f} gain={best['gain']:.1f} total={best['total']:.1f}")
    return str(best["id"])


def _fallback_pick(db: DatabaseManager) -> str:
    """Best-ranked available player, saving the last rounds for a K and a DST. The engine
    drafts a *random* player if we fail, so any sane pick beats raising."""
    players = pd.read_sql("SELECT id, allowed_positions, availability, current_bot_id, rank FROM players", db.engine)
    players["position"] = players["allowed_positions"].apply(_position)
    rank = pd.to_numeric(players["rank"], errors="coerce")
    players["rank"] = rank.where(rank > 0)
    available = players[players["availability"] == "AVAILABLE"].sort_values("rank", na_position="last")
    try:
        me = pd.read_sql("SELECT current_bot_id FROM game_statuses", db.engine)["current_bot_id"].iloc[0]
        rounds = int(pd.read_sql("SELECT total_rounds FROM league_settings", db.engine)["total_rounds"].iloc[0])
        mine = players[players["current_bot_id"] == me]
        missing = [p for p in ("K", "DST") if not (mine["position"] == p).any()]
        late = rounds - len(mine) <= len(missing)
        pool = available[available["position"].isin(missing)] if late else available[~available["position"].isin(["K", "DST"])]
        if not pool.empty:
            available = pool
    except Exception:  # noqa: BLE001
        pass
    return str(available["id"].iloc[0])


def draft_player() -> str:
    db = DatabaseManager()
    try:
        try:
            return _pick(db)
        except Exception as e:  # noqa: BLE001 - never hand the pick to the engine's random auto-draft
            print(f"[ben_bot] model failed ({e!r}); using rank fallback")
            return _fallback_pick(db)
    finally:
        db.close()


def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    """No-op: this bot does not make any waiver claims or roster moves."""
    return AttemptedFantasyActions(waiver_claims=[])
