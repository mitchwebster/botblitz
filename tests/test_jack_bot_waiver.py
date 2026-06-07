"""Offline unit tests for the forward-looking waiver value + FAAB bid sizing
pure functions in jack_bot (issue #265).

These exercise ONLY the pure functions (player_forward_value, size_bid) with
plain inline numbers / lists — no DB, no engine, fully offline.
"""
import importlib.util


def _load_jack_bot():
    spec = importlib.util.spec_from_file_location(
        "jack_bot", "bots/nfl2025/jack_bot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# player_forward_value
# --------------------------------------------------------------------------- #
def test_forward_value_blends_trailing_and_projections():
    bot = _load_jack_bot()
    # Equal trailing and projections -> value equals that common average.
    val = bot.player_forward_value([10.0, 10.0], [10.0, 10.0])
    assert abs(val - 10.0) < 1e-9
    # A blend of two different averages sits strictly between them.
    val = bot.player_forward_value([0.0, 0.0], [20.0, 20.0])
    assert 0.0 < val < 20.0


def test_forward_value_monotonic_in_trailing_actuals():
    bot = _load_jack_bot()
    low = bot.player_forward_value([5.0, 5.0], [12.0])
    mid = bot.player_forward_value([10.0, 10.0], [12.0])
    high = bot.player_forward_value([20.0, 20.0], [12.0])
    assert low <= mid <= high
    assert high > low  # strictly increasing when trailing actually rises


def test_forward_value_monotonic_in_projections():
    bot = _load_jack_bot()
    low = bot.player_forward_value([10.0], [5.0, 5.0])
    mid = bot.player_forward_value([10.0], [10.0, 10.0])
    high = bot.player_forward_value([10.0], [20.0, 20.0])
    assert low <= mid <= high
    assert high > low


def test_forward_value_out_gates_toward_zero():
    bot = _load_jack_bot()
    healthy = bot.player_forward_value([15.0, 15.0], [15.0, 15.0], "ACTIVE")
    out = bot.player_forward_value([15.0, 15.0], [15.0, 15.0], "OUT")
    assert healthy > 0
    assert out == 0.0
    # Case-insensitive: 'Out' gates identically to 'OUT'.
    assert bot.player_forward_value([15.0], [15.0], "Out") == 0.0


def test_forward_value_partial_injury_discounts_between_zero_and_full():
    bot = _load_jack_bot()
    full = bot.player_forward_value([12.0], [12.0], None)
    questionable = bot.player_forward_value([12.0], [12.0], "Questionable")
    doubtful = bot.player_forward_value([12.0], [12.0], "Doubtful")
    out = bot.player_forward_value([12.0], [12.0], "Out")
    assert out < doubtful < questionable < full
    assert out == 0.0


def test_forward_value_empty_inputs_are_safe():
    bot = _load_jack_bot()
    assert bot.player_forward_value([], []) == 0.0
    # Unknown / None status keeps full value (no spurious gating).
    assert bot.player_forward_value([10.0], [10.0], "SomethingWeird") == 10.0


# --------------------------------------------------------------------------- #
# size_bid
# --------------------------------------------------------------------------- #
def test_bid_scales_proportionally_with_upgrade():
    bot = _load_jack_bot()
    # Late season + large budget so neither the reserve cap nor the budget clamp
    # binds; bids should grow with the upgrade's share of the max.
    week = bot.FAAB_SEASON_WEEKS
    budget = 1000
    small = bot.size_bid(upgrade=2.0, upgrade_max=10.0, remaining_budget=budget, week=week)
    medium = bot.size_bid(upgrade=5.0, upgrade_max=10.0, remaining_budget=budget, week=week)
    large = bot.size_bid(upgrade=9.0, upgrade_max=10.0, remaining_budget=budget, week=week)
    assert small < medium < large


def test_bid_reserve_clamp_caps_early_season(monkeypatch):
    bot = _load_jack_bot()
    monkeypatch.delenv("BID_AMOUNT", raising=False)
    budget = 100
    early = bot.size_bid(upgrade=10.0, upgrade_max=10.0, remaining_budget=budget, week=1)
    late = bot.size_bid(upgrade=10.0, upgrade_max=10.0, remaining_budget=budget, week=bot.FAAB_SEASON_WEEKS)
    # Early-season bid is held back to preserve a late-season reserve.
    assert early < late
    assert early <= budget


def test_bid_reserve_clamp_caps_low_budget():
    bot = _load_jack_bot()
    # A low budget shrinks the absolute reserve cap even late in the season.
    week = bot.FAAB_SEASON_WEEKS
    low = bot.size_bid(upgrade=10.0, upgrade_max=10.0, remaining_budget=5, week=week)
    high = bot.size_bid(upgrade=10.0, upgrade_max=10.0, remaining_budget=500, week=week)
    assert low < high
    assert low <= 5


def test_bid_never_exceeds_budget_and_never_below_one():
    bot = _load_jack_bot()
    # Max upgrade, tiny budget, late season: clamp to budget, still >= 1.
    bid = bot.size_bid(upgrade=10.0, upgrade_max=10.0, remaining_budget=1, week=bot.FAAB_SEASON_WEEKS)
    assert bid == 1
    # Zero budget -> cannot bid.
    assert bot.size_bid(upgrade=10.0, upgrade_max=10.0, remaining_budget=0, week=10) == 0
    # Positive upgrade always yields at least 1 when budget remains.
    bid = bot.size_bid(upgrade=0.01, upgrade_max=10.0, remaining_budget=50, week=1)
    assert bid >= 1
    # Never exceeds budget across a sweep of weeks/upgrades.
    for week in range(1, bot.FAAB_SEASON_WEEKS + 2):
        for up in (0.0, 1.0, 5.0, 10.0):
            b = bot.size_bid(upgrade=up, upgrade_max=10.0, remaining_budget=30, week=week)
            assert 0 <= b <= 30


def test_bid_outbids_on_tie_margin():
    bot = _load_jack_bot()
    # A strictly-positive upgrade rounds up + adds the tie-break margin so a
    # better-ranked team clearly outbids an equal-value opponent.
    week = bot.FAAB_SEASON_WEEKS
    bid = bot.size_bid(upgrade=5.0, upgrade_max=10.0, remaining_budget=1000, week=week)
    # raw share = 0.5 -> 0.5 * 1000 = 500; ceil + margin = 501.
    assert bid == 501


def test_bid_amount_env_scales_aggressiveness(monkeypatch):
    bot = _load_jack_bot()
    week = bot.FAAB_SEASON_WEEKS
    budget = 1000
    monkeypatch.setenv("BID_AMOUNT", "1.0")
    neutral = bot.size_bid(upgrade=3.0, upgrade_max=10.0, remaining_budget=budget, week=week)
    monkeypatch.setenv("BID_AMOUNT", "2.0")
    aggressive = bot.size_bid(upgrade=3.0, upgrade_max=10.0, remaining_budget=budget, week=week)
    monkeypatch.setenv("BID_AMOUNT", "0.5")
    passive = bot.size_bid(upgrade=3.0, upgrade_max=10.0, remaining_budget=budget, week=week)
    assert passive < neutral < aggressive


def test_bid_amount_env_default_when_unset(monkeypatch):
    bot = _load_jack_bot()
    monkeypatch.delenv("BID_AMOUNT", raising=False)
    # Unset env falls back to DEFAULT_BID_AMOUNT (no crash, neutral sizing).
    bid = bot.size_bid(upgrade=3.0, upgrade_max=10.0, remaining_budget=1000, week=bot.FAAB_SEASON_WEEKS)
    assert bid >= 1
