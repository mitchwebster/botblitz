"""Preseason and weekly projections sourced from FantasyPros via R's ffpros.

Replaces the retired Python FantasyPros HTML scraper (`blitz_env/projections_db.py`):
one `Rscript fetch_projections.R` call per (year, week-range) via `ffpros::fp_projections`
instead of hand-rolled requests/BeautifulSoup parsing.

Column policy: ffpros returns clean lowercase columns (e.g. `passing_yds`, `misc_fpts`).
To avoid breaking bots written against the legacy FantasyPros-scrape schema
(`PASSING_YDS`, `FPTS`, ...), every legacy uppercase column is kept as an alias
computed from the new source, *alongside* the new lowercase columns (not instead of).
New bot code should prefer the lowercase ffpros-native names.
"""

import os
import subprocess
import tempfile

import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FETCH_SCRIPT = os.path.join(_REPO_ROOT, "fetch_projections.R")

# legacy_uppercase_name -> new ffpros-native column(s) to alias it from.
# A tuple means "first non-null of these" (misc_fpts for skill positions,
# fpts for K/DST, which don't have a `misc_` prefix).
_LEGACY_ALIASES = {
    "PASSING_ATT": "passing_att",
    "PASSING_CMP": "passing_cmp",
    "PASSING_YDS": "passing_yds",
    "PASSING_TDS": "passing_tds",
    "PASSING_INTS": "passing_ints",
    "RUSHING_ATT": "rushing_att",
    "RUSHING_YDS": "rushing_yds",
    "RUSHING_TDS": "rushing_tds",
    "FL": "misc_fl",
    "FPTS": ("misc_fpts", "fpts"),
    "RECEIVING_REC": "receiving_rec",
    "RECEIVING_YDS": "receiving_yds",
    "RECEIVING_TDS": "receiving_tds",
    "FG": "fg",
    "FGA": "fga",
    "XPT": "xpt",
    "SACK": "sack",
    "INT": "int",
    "FR": "fr",
    "FF": "ff",
    "TD": "td",
    "SAFETY": "safety",
    "PA": "pa",
    "YDS AGN": "yds_agn",
}


def _free_case_collision(df: pd.DataFrame, legacy_name: str) -> None:
    """
    SQLite (and this DB's bot-facing column lookups) treat column names as
    case-insensitive, so a lowercase native column (e.g. `fpts`) and an
    uppercase legacy alias (`FPTS`) can't coexist. If a native column would
    collide with the legacy name we're about to add, rename it out of the way
    (`fpts` -> `fpts_native`) so the exact-case legacy name stays queryable --
    existing bots depend on the exact case (`row["FPTS"]`).
    """
    for col in list(df.columns):
        if col != legacy_name and col.lower() == legacy_name.lower():
            df.rename(columns={col: f"{col}_native"}, inplace=True)


def _add_legacy_aliases(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for legacy_name, source in _LEGACY_ALIASES.items():
        if isinstance(source, tuple):
            cols = [c for c in source if c in df.columns]
            if not cols:
                continue
            combined = df[cols[0]]
            for c in cols[1:]:
                combined = combined.combine_first(df[c])
            _free_case_collision(df, legacy_name)
            df[legacy_name] = combined
        elif source in df.columns:
            value = df[source]
            _free_case_collision(df, legacy_name)
            df[legacy_name] = value
    return df


def fetch_projections(year: int, weeks) -> pd.DataFrame:
    """
    Pull projections for `year` across `weeks` (an iterable of ints, and/or
    the string "draft" for preseason) via ffpros, with legacy column aliases.
    """
    weeks_arg = ",".join(str(w) for w in weeks)

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        out_path = tmp.name

    try:
        subprocess.run(
            ["Rscript", _FETCH_SCRIPT, str(year), weeks_arg, out_path],
            check=True, capture_output=True, text=True, timeout=300,
        )
        if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            return pd.DataFrame()
        raw = pd.read_csv(out_path)
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)

    if raw.empty:
        return raw

    raw["fantasypros_id"] = raw["fantasypros_id"].astype(str)
    return _add_legacy_aliases(raw)
