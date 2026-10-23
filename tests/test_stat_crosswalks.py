import os

import pandas as pd
import pytest

from blitz_env import load_stats_nflreadr
from blitz_env.load_stats_nflreadr import _attach_dst_ids, _dst_team_crosswalk, _name_team_crosswalk


@pytest.fixture
def no_build_copy(monkeypatch):
    """Simulate a fresh CI checkout: the blitz_env/player_ranks_*.csv copies
    that `make build-py-module` creates don't exist."""
    real_isfile = os.path.isfile
    build_dir = os.path.join(load_stats_nflreadr._REPO_ROOT, "blitz_env")
    monkeypatch.setattr(
        os.path, "isfile",
        lambda p: False if os.path.dirname(os.path.abspath(p)) == build_dir else real_isfile(p),
    )


def test_dst_crosswalk_covers_every_team_without_build_copy(no_build_copy):
    crosswalk = _dst_team_crosswalk(2026)
    ids = dict(zip(crosswalk["team"], crosswalk["fantasypros_id"]))
    assert len(ids) == 32
    assert ids["DEN"] == "8090"
    assert ids["SEA"] == "8260"


def test_name_crosswalk_is_populated_without_build_copy(no_build_copy):
    assert not _name_team_crosswalk(2026).empty


def test_attach_dst_ids_normalizes_nflreadr_abbreviations():
    dst = pd.DataFrame({"team": ["JAX", "LA"]})
    assert _attach_dst_ids(dst, 2026)["team"].tolist() == ["JAC", "LAR"]
    assert _attach_dst_ids(dst, 2026)["fantasypros_id"].notna().all()


def test_attach_dst_ids_raises_on_unmatched_team():
    dst = pd.DataFrame({"team": ["DEN", "XYZ"]})
    with pytest.raises(ValueError, match="XYZ"):
        _attach_dst_ids(dst, 2026)
