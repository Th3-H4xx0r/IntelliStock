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
    # +0.5% and -4%: inside the default +1% / -5% bands
    st = trend_state(series(tail=[100.5, 99.0, 96.0]), DEFAULTS)
    assert st.in_market is False and st.decided is False


def test_the_default_bands_are_asymmetric_enter_1_exit_5():
    assert DEFAULTS["enter_band"] == 0.01 and DEFAULTS["exit_band"] == 0.05
    assert trend_state(series(tail=[101.5]), DEFAULTS).in_market is True
    assert trend_state(series(tail=[101.5, 95.5]), DEFAULTS).in_market is True    # -4.5% keeps
    assert trend_state(series(tail=[101.5, 94.5]), DEFAULTS).in_market is False   # -5.5% exits


def test_enter_above_the_upper_band_and_keep_through_the_middle():
    st = trend_state(series(tail=[104.0]), DEFAULTS)
    assert st.in_market is True and st.decided
    # dips back to +1% and even -2%: inside the bands, state is KEPT
    st = trend_state(series(tail=[104.0, 101.0, 98.0]), DEFAULTS)
    assert st.in_market is True
    assert st.crossings == 1


def test_exit_below_the_lower_band_and_keep_through_the_middle():
    st = trend_state(series(tail=[104.0, 94.0]), DEFAULTS)
    assert st.in_market is False and st.decided
    # back to -1% and +0.5%: inside the bands, state is KEPT out
    st = trend_state(series(tail=[104.0, 94.0, 99.0, 100.5]), DEFAULTS)
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


def test_exposure_maps_to_the_three_legs():
    from strategy_ht import exposure_targets
    assert exposure_targets(2.0, DEFAULTS) == {"SSO": 1.0}
    assert exposure_targets(1.5, DEFAULTS) == {"SSO": 0.5, "SPY": 0.5}
    assert exposure_targets(1.0, DEFAULTS) == {"SPY": 1.0}
    assert exposure_targets(0.5, DEFAULTS) == {"SPY": 0.5, "BIL": 0.5}
    assert exposure_targets(0.0, DEFAULTS) == {"BIL": 1.0}
    assert abs(sum(exposure_targets(1.3, DEFAULTS).values()) - 1.0) < 1e-9


def test_ht_targets_uses_the_exposure_when_in_market():
    targets, notes = ht_targets(True, DEFAULTS, 1.25)
    assert targets == {"SSO": 0.25, "SPY": 0.75}
    assert "1.25x" in notes[0]


def test_in_exposure_vol_target_scales_down_in_violent_markets():
    from strategy_ht import in_exposure, realised_vol
    calm = [100.0 * (1.001 ** i) for i in range(60)]                     # ~1.6% annualised vol
    e, why = in_exposure(calm, DEFAULTS)
    assert e == 2.0 and "vol" in why
    violent = [100.0 * (1.0 + (0.04 if i % 2 else -0.04)) ** i for i in range(60)]   # ~60%+ annualised
    e, why = in_exposure(violent, DEFAULTS)
    assert e == 0.5                                                      # 0.20 / 0.6 -> clipped to exp_min
    mid = [100.0]
    for i in range(60):
        mid.append(mid[-1] * (1.0 + (0.0126 if i % 2 else -0.0126)))     # ~20% annualised
    e, _ = in_exposure(mid, DEFAULTS)
    assert 0.9 < e < 1.1
    assert realised_vol([100.0] * 10, 21) is None                       # too short


def test_in_exposure_fails_toward_less_and_can_be_switched_off():
    from strategy_ht import in_exposure
    e, why = in_exposure([100.0] * 10, DEFAULTS)                          # not enough history for vol(21)
    assert e == 0.5 and "fail-closed" in why
    e, why = in_exposure([100.0] * 60, cfg(vol_target=0))
    assert e == 2.0 and "off" in why
    e, _ = in_exposure([100.0] * 60, DEFAULTS)                            # zero vol -> exp_max
    assert e == 2.0


def test_bounce_overlay_forces_full_exposure_for_one_day():
    from strategy_ht import in_exposure
    closes = [100.0 * (1.0 + (0.03 if i % 2 else -0.03)) ** i for i in range(59)] + [50.0]
    closes[-1] = closes[-2] * 0.94                                       # a -6% close
    e, why = in_exposure(closes, DEFAULTS)
    assert e == 2.0 and why.startswith("bounce")
    e, why = in_exposure(closes, cfg(bounce_drop=0))                      # overlay off -> vol target rules
    assert e == 0.5 and not why.startswith("bounce")


def test_out_book_never_exceeds_full_exposure_and_tolerates_junk():
    assert sum(out_book(cfg(out_symbols={"SPY": 0.8, "BIL": 0.8})).values()) == 1.0
    assert out_book(cfg(out_symbols="SPY")) == DEFAULTS["out_symbols"]
    assert out_book(cfg(out_symbols={"SPY": "x", "BIL": -1})) == DEFAULTS["out_symbols"]
    assert out_book(cfg(out_symbols={" spy ": 0.4})) == {"SPY": 0.4}


def test_the_parsers_fail_toward_the_defaults():
    for bad in (None, "", "wide", float("nan"), float("inf")):
        assert _f(cfg(enter_band=bad), "enter_band") == 0.01, bad
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
