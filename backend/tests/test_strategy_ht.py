"""Pure tests for Strategy HT: the hysteresis state machine, the books, the universe."""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategy_ht import (  # noqa: E402
    DEFAULTS, _f, _i, _s, ht_targets, out_book, strategy_ht_universe, trend_state,
)


def cfg(**overrides):
    value = dict(DEFAULTS)
    value.update(overrides)
    return value


def series(ma_level=100.0, n=200, tail=()):
    """`n` flat closes at `ma_level`, then the `tail` closes appended."""
    return [ma_level] * n + list(tail)


def test_the_default_universe_is_the_three_legs():
    assert strategy_ht_universe(DEFAULTS) == ["BIL", "SPY", "SSO"]


def test_insufficient_history_is_parked():
    st = trend_state([100.0] * 150, DEFAULTS)
    assert st.in_market is False and "insufficient" in st.reason


def test_a_bad_close_is_parked():
    assert trend_state([100.0] * 199 + [float("nan")], DEFAULTS).in_market is False
    assert trend_state([100.0] * 199 + [0.0], DEFAULTS).in_market is False


def test_no_decisive_crossing_is_parked_and_undecided():
    st = trend_state(series(tail=[101.0, 102.0, 98.5]), DEFAULTS)
    assert st.in_market is False and st.decided is False


def test_enter_above_the_upper_band_and_keep_through_the_middle():
    st = trend_state(series(tail=[104.0]), DEFAULTS)
    assert st.in_market is True and st.decided
    # dips back to +1% and even -2%: inside the bands, state is KEPT
    st = trend_state(series(tail=[104.0, 101.0, 98.0]), DEFAULTS)
    assert st.in_market is True
    assert st.crossings == 1


def test_exit_below_the_lower_band_and_keep_through_the_middle():
    st = trend_state(series(tail=[104.0, 96.0]), DEFAULTS)
    assert st.in_market is False and st.decided
    st = trend_state(series(tail=[104.0, 96.0, 99.0, 102.0]), DEFAULTS)
    assert st.in_market is False
    assert st.crossings == 2


def test_the_bands_are_configurable_and_a_zero_band_is_a_plain_ma_filter():
    c = cfg(enter_band=0.0, exit_band=0.0)
    assert trend_state(series(tail=[100.5]), c).in_market is True
    assert trend_state(series(tail=[100.5, 99.5]), c).in_market is False


def test_in_market_targets_are_the_levered_leg_only():
    targets, notes = ht_targets(True, DEFAULTS)
    assert targets == {"SSO": 1.0}
    assert "100% SSO" in notes[0]


def test_parked_targets_are_the_out_book():
    targets, _ = ht_targets(False, DEFAULTS)
    assert targets == {"SPY": 0.5, "BIL": 0.5}


def test_in_weight_is_clamped_to_one():
    targets, _ = ht_targets(True, cfg(in_weight=3.0))
    assert targets == {"SSO": 1.0}


def test_out_book_never_exceeds_full_exposure_and_tolerates_junk():
    assert sum(out_book(cfg(out_symbols={"SPY": 0.8, "BIL": 0.8})).values()) == 1.0
    assert out_book(cfg(out_symbols="SPY")) == DEFAULTS["out_symbols"]
    assert out_book(cfg(out_symbols={"SPY": "x", "BIL": -1})) == DEFAULTS["out_symbols"]
    assert out_book(cfg(out_symbols={" spy ": 0.4})) == {"SPY": 0.4}


def test_the_parsers_fail_toward_the_defaults():
    for bad in (None, "", "wide", float("nan"), float("inf")):
        assert _f(cfg(enter_band=bad), "enter_band") == 0.03, bad
        assert _i(cfg(ma_bars=bad), "ma_bars") == 200, bad
    assert _s({"in_symbol": " sso "}, "in_symbol") == "SSO"
    assert _s({"in_symbol": None}, "in_symbol") == "SSO"
    assert _f(cfg(enter_band=0.05), "enter_band") == 0.05
    assert _i(cfg(ma_bars=150), "ma_bars") == 150


def test_the_defaults_opt_into_the_wide_single_position_cap():
    """Without BOTH keys the engine trims a 100% SSO buy to the 15% failsafe
    and the whole backtest measures an inert strategy (BT102936)."""
    assert DEFAULTS["broker_max_single_position_pct"] == 1.0
    assert DEFAULTS["honour_single_position_cap"] is True
    assert DEFAULTS["strategy_ht_enabled"] is False
