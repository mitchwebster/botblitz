"""Actual season/weekly stats sourced from nflverse (via R's nflreadr).

Replaces the retired Python FantasyPros HTML scraper (`blitz_env/stats_db.py`):
one `Rscript fetch_stats.R` call per season pulls per-player offense actuals
(`nflreadr::load_player_stats`) and team-defense/DST actuals
(`nflreadr::load_team_stats` + `load_schedules` for points allowed), instead of
hand-rolled requests/BeautifulSoup parsing of FantasyPros' stats pages.

Column policy: nflreadr's native schema (`passing_yards`, `fantasy_points_ppr`, ...)
is kept, and every legacy FantasyPros-scrape column (`PASSING_YDS`, `FPTS`, ...)
that has a clear equivalent is added as an alias, *alongside* the new columns,
so existing bots keep working. A few legacy columns have no nflreadr equivalent
(consensus/derived metrics like `ROST`, `Y/A`, `LG`, `20+`, `pos_rank`, `FPTS/G`)
and are intentionally dropped — no bot in bots/nfl2025 reads them (verified before
this migration). DST `YDS AGN` (yards allowed) is also dropped for the same reason;
DST `FPTS` is approximated with a standard sacks/turnovers/points-allowed formula
since nflreadr has no fantasy-scoring endpoint for team defenses. Kicker `FPTS`
adds FG/PAT points on top of `fantasy_points_ppr`, which leaves kicking out
(see `_KICKING_POINTS`); the native column is left as nflverse reports it.
"""

import os
import subprocess
import tempfile

import pandas as pd

from blitz_env.player_id_crosswalk import load_player_id_crosswalk

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FETCH_SCRIPT = os.path.join(_REPO_ROOT, "fetch_stats.R")


# nflreadr and the FantasyPros-derived player pool disagree on a couple of
# team abbreviations; normalize nflreadr's to match the pool before joining.
_TEAM_ABBR_TO_POOL = {"JAX": "JAC", "LA": "LAR"}


def _normalize_name(name) -> str:
    """Lowercase, alphanumeric-only key for matching names across sources that
    format them differently (nflreadr's "player_display_name" vs the player
    pool's "full_name") -- strips punctuation/spacing/suffix differences like
    "A.J. Brown" vs "AJ Brown" or "Michael Pittman Jr." vs "Michael Pittman"."""
    if not isinstance(name, str):
        return ""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _name_team_crosswalk(year: int) -> pd.DataFrame:
    """Fallback for players load_player_id_crosswalk() has no gsis_id match
    for (a nflreadr/nflverse ID-table gap -- seen for a handful of kickers
    and other fringe players, not limited to any one position) -- matches on
    normalized (full_name, team) against this year's draftable pool instead.
    Same fallback-year behavior as _dst_team_crosswalk if `year`'s pool CSV
    isn't shipped."""
    from blitz_env.load_players import load_players

    try:
        players = load_players(year)
    except FileNotFoundError:
        import glob
        candidates = sorted(glob.glob(os.path.join(_REPO_ROOT, "blitz_env", "player_ranks_*.csv")))
        if not candidates:
            return pd.DataFrame(columns=["_name_key", "team", "fantasypros_id"])
        fallback_year = int(os.path.basename(candidates[-1]).removeprefix("player_ranks_").removesuffix(".csv"))
        players = load_players(fallback_year)

    rows = [
        {"_name_key": _normalize_name(p.full_name), "team": p.professional_team, "fantasypros_id": p.id}
        for p in players
    ]
    return pd.DataFrame(rows).drop_duplicates(subset=["_name_key", "team"])


def _dst_team_crosswalk(year: int) -> pd.DataFrame:
    """
    team abbreviation -> fantasypros_id, from the player ranks pool (DST rows).
    Team DST fantasypros_ids are effectively static year over year (32
    franchises), so if `year`'s rank CSV isn't available (common for older
    backfill years -- only recent years ship a player_ranks_{year}.csv), fall
    back to whichever rank CSV *is* available rather than failing outright.
    """
    from blitz_env.load_players import load_players

    try:
        players = load_players(year)
    except FileNotFoundError:
        import glob
        candidates = sorted(glob.glob(os.path.join(_REPO_ROOT, "blitz_env", "player_ranks_*.csv")))
        if not candidates:
            return pd.DataFrame(columns=["team", "fantasypros_id"])
        fallback_year = int(os.path.basename(candidates[-1]).removeprefix("player_ranks_").removesuffix(".csv"))
        players = load_players(fallback_year)

    rows = [
        {"team": p.professional_team, "fantasypros_id": p.id}
        for p in players if "DST" in p.allowed_positions
    ]
    return pd.DataFrame(rows).drop_duplicates(subset=["team"])


_OFFENSE_LEGACY_ALIASES = {
    "PASSING_CMP": "completions",
    "PASSING_ATT": "attempts",
    "PASSING_YDS": "passing_yards",
    "PASSING_TD": "passing_tds",
    "PASSING_INT": "passing_interceptions",
    "RUSHING_ATT": "carries",
    "RUSHING_YDS": "rushing_yards",
    "RUSHING_TD": "rushing_tds",
    "RECEIVING_REC": "receptions",
    "RECEIVING_TGT": "targets",
    "RECEIVING_YDS": "receiving_yards",
    "RECEIVING_TD": "receiving_tds",
    "FG": "fg_made",
    "FGA": "fg_att",
    "XPT": "pat_made",
    "FPTS": "fantasy_points_ppr",
}

_DST_LEGACY_ALIASES = {
    "SACK": "def_sacks",
    "INT": "def_interceptions",
    "FR": "def_fumbles",
    "TD": "def_tds",
    "SAFETY": "def_safeties",
    "PA": "points_allowed",
}

# Standard DST fantasy scoring, since nflreadr has no fantasy-points endpoint
# for team defenses. Matches common league defaults; not FantasyPros' exact
# (private) weighting, but no bot reads FPTS for DST-specific tuning today.
_PA_TIERS = [(0, 10), (6, 7), (13, 4), (20, 1), (27, 0), (34, -1), (float("inf"), -4)]


def _pa_points(points_allowed: float) -> float:
    if pd.isna(points_allowed):
        return float("nan")
    for threshold, pts in _PA_TIERS:
        if points_allowed <= threshold:
            return pts
    return _PA_TIERS[-1][1]


# nflverse's fantasy_points_ppr is an offense-only formula, so kickers score 0
# there. Kicking is added on top using ffanalytics' default kicker scoring (the
# same rules behind external_projections' K points): FG 0-39 = 3, 40-49 = 4,
# 50+ = 5, PAT = 1, misses = 0.
_KICKING_POINTS = {
    "fg_made_0_19": 3,
    "fg_made_20_29": 3,
    "fg_made_30_39": 3,
    "fg_made_40_49": 4,
    "fg_made_50_59": 5,
    "fg_made_60_": 5,
    "pat_made": 1,
}


def _add_kicking_points(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    kicking = pd.Series(0.0, index=df.index)
    for col, points in _KICKING_POINTS.items():
        if col in df.columns:
            kicking += df[col].fillna(0) * points
    df["FPTS"] = df["FPTS"].add(kicking, fill_value=0)
    return df


def _free_case_collision(df: pd.DataFrame, legacy_name: str) -> None:
    """See the identical helper in load_projections_ffpros.py: SQLite column
    names are case-insensitive, so a native column can't coexist with a
    same-spelled-different-case legacy alias. Rename the native one out of
    the way so the exact-case legacy name (which bots depend on) stays free."""
    for col in list(df.columns):
        if col != legacy_name and col.lower() == legacy_name.lower():
            df.rename(columns={col: f"{col}_native"}, inplace=True)


def _add_legacy_aliases(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    df = df.copy()
    for legacy_name, source in mapping.items():
        if source in df.columns:
            value = df[source]
            _free_case_collision(df, legacy_name)
            df[legacy_name] = value
    return df


def _add_offense_fumbles_lost(df: pd.DataFrame) -> pd.DataFrame:
    fl_cols = [c for c in ("rushing_fumbles_lost", "receiving_fumbles_lost", "sack_fumbles_lost")
               if c in df.columns]
    if fl_cols:
        df = df.copy()
        df["FL"] = df[fl_cols].sum(axis=1, skipna=True)
    return df


def fetch_season_stats(year: int, summary_level: str = "week") -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Pull offense + DST actuals for `year` at the given nflreadr summary_level
    ("week" for weekly rows, "reg" for season totals), with legacy column
    aliases. Returns (offense_df, dst_df).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        off_week = os.path.join(tmpdir, "off_week.csv")
        off_season = os.path.join(tmpdir, "off_season.csv")
        dst_week = os.path.join(tmpdir, "dst_week.csv")
        dst_season = os.path.join(tmpdir, "dst_season.csv")

        try:
            subprocess.run(
                ["Rscript", _FETCH_SCRIPT, str(year), off_week, off_season, dst_week, dst_season],
                check=True, capture_output=True, text=True, timeout=300,
            )
        except subprocess.CalledProcessError as e:
            # capture_output hides Rscript's own stderr/stdout from the
            # caller entirely -- on CI this showed only a bare "returned
            # non-zero exit status 1" with zero information about why.
            # Print what R actually said before failing the same way.
            print(f"fetch_stats.R failed (exit {e.returncode}). stdout:\n{e.stdout}\nstderr:\n{e.stderr}")
            raise

        off_path = off_week if summary_level == "week" else off_season
        dst_path = dst_week if summary_level == "week" else dst_season

        offense = pd.read_csv(off_path, low_memory=False) if os.path.getsize(off_path) > 0 else pd.DataFrame()
        dst = pd.read_csv(dst_path, low_memory=False) if os.path.getsize(dst_path) > 0 else pd.DataFrame()

    if not offense.empty:
        ids = load_player_id_crosswalk()
        offense = offense.rename(columns={"player_id": "gsis_id"})
        offense = offense.merge(ids, on="gsis_id", how="left")

        # The gsis_id crosswalk doesn't cover every player (an nflverse ID-table
        # gap, not specific to any one position) -- for whatever's left unmatched,
        # fall back to a name+team match against this year's draftable pool rather
        # than leaving fantasypros_id blank. A blank id can never join back to
        # players.id, so those rows would otherwise silently score 0 forever no
        # matter how well the player actually did.
        #
        # Only applicable at the weekly level: the season-aggregate ("reg")
        # frame has no single "team" (a player who changed teams mid-season
        # has no one answer), just "recent_team" -- too unreliable to match a
        # specific week's team against, so skip the fallback there rather than
        # risk a wrong match.
        still_missing = offense["fantasypros_id"].isna()
        if still_missing.any() and "team" in offense.columns and "player_display_name" in offense.columns:
            fallback = _name_team_crosswalk(year)
            name_key = offense["player_display_name"].apply(_normalize_name)
            team_pool = offense["team"].replace(_TEAM_ABBR_TO_POOL)
            lookup = dict(zip(zip(fallback["_name_key"], fallback["team"]), fallback["fantasypros_id"]))
            offense.loc[still_missing, "fantasypros_id"] = [
                lookup.get((n, t)) for n, t in zip(name_key[still_missing], team_pool[still_missing])
            ]

        offense = _add_offense_fumbles_lost(offense)
        offense = _add_legacy_aliases(offense, _OFFENSE_LEGACY_ALIASES)
        if "FPTS" in offense.columns:
            offense = _add_kicking_points(offense)
        if "position" in offense.columns:
            offense["position"] = offense["position"].str.upper()

    if not dst.empty:
        try:
            crosswalk = _dst_team_crosswalk(year)
            dst["team"] = dst["team"].replace(_TEAM_ABBR_TO_POOL)
            dst = dst.merge(crosswalk, on="team", how="left")
            dst = _add_legacy_aliases(dst, _DST_LEGACY_ALIASES)
            dst["FPTS"] = dst["points_allowed"].apply(_pa_points) + (
                dst.get("def_sacks", 0).fillna(0) * 1
                + dst.get("def_interceptions", 0).fillna(0) * 2
                + dst.get("def_fumbles", 0).fillna(0) * 2
                + dst.get("def_tds", 0).fillna(0) * 6
                + dst.get("def_safeties", 0).fillna(0) * 2
            )
            dst["position"] = "DST"
        except Exception as e:
            print(f"Warning: Failed to process DST stats for year {year}: {e}")
            dst = pd.DataFrame()

    return offense, dst
