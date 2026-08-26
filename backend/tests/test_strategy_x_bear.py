"""Pure policy tests for the default-off Strategy X bear overlay."""
import os
import sys


_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)


from strategy_x_bear import (  # noqa: E402
    advance_kicker,
    bear_system_mode,
    bear_system_universe,
    eligible_crisis_alpha,
    fast_crash_signal,
    plan_bear_overlay,
)


def cfg(**overrides):
    value = {
        "bear_system_mode": "off",
        "bear_cash_symbol": "BIL",
        "crisis_alpha_symbols": ["DBMF", "KMLM", "CTA"],
        "crisis_alpha_pct": 0.20,
        "crisis_alpha_min_history_bars": 60,
        "bear_kicker_symbol": "SQQQ",
        "bear_kicker_pct": 0.05,
        "bear_kicker_fast_ma_bars": 20,
        "bear_kicker_mid_ma_bars": 50,
        "bear_kicker_long_ma_bars": 200,
        "bear_kicker_max_bars": 5,
        "bear_kicker_cooldown_bars": 10,
        "core_filter_symbol": "QQQ",
        "core_bull_symbol": "TQQQ",
        "core_chop_symbol": "SPY",
        "core_bear_symbol": "",
    }
    value.update(overrides)
    return value


def test_invalid_mode_is_off_and_off_declares_no_extra_symbols():
    assert bear_system_mode(cfg(bear_system_mode="ACTIVE-ish")) == "off"
    assert bear_system_universe(cfg()) == []


def test_shadow_universe_normalizes_and_deduplicates_declared_order():
    got = bear_system_universe(cfg(
        bear_system_mode=" shadow ", bear_cash_symbol=" bil ",
        crisis_alpha_symbols=["dbmf", " BIL ", "kmlm", "dbmf"],
        bear_kicker_symbol=" sqqq "))
    assert got == ["BIL", "DBMF", "KMLM", "SQQQ"]


def test_shadow_universe_treats_a_single_manager_string_as_one_symbol():
    got = bear_system_universe(cfg(
        bear_system_mode="shadow", crisis_alpha_symbols=" dbmf "))
    assert got == ["BIL", "DBMF", "SQQQ"]


def test_fresh_breakdown_compares_two_point_in_time_states():
    closes = [300.0] * 201
    closes[-2] = 301.0
    closes[-1] = 100.0
    signal_value = fast_crash_signal(closes, cfg())
    assert signal_value.stacked is True
    assert signal_value.fresh is True
    assert signal_value.below_fast is True


def test_signal_refuses_insufficient_or_nonfinite_history():
    short = fast_crash_signal([100.0] * 200, cfg())
    bad = fast_crash_signal([100.0] * 200 + [float("nan")], cfg())
    bad_lookback = fast_crash_signal(
        [100.0] * 260, cfg(bear_kicker_fast_ma_bars=float("nan")))
    assert (short.stacked, short.fresh) == (False, False)
    assert (bad.stacked, bad.fresh) == (False, False)
    assert (bad_lookback.stacked, bad_lookback.fresh) == (False, False)


def test_huge_numeric_inputs_fail_closed_without_unsafe_integer_coercion():
    huge = 10 ** 1000
    assert fast_crash_signal([100.0] * 260,
                             cfg(bear_kicker_fast_ma_bars=huge)).stacked is False
    out = plan_bear_overlay({"SPY": 0.9, "GLD": 0.1}, risk_on=False,
        config=cfg(bear_system_mode="active", crisis_alpha_pct=huge),
        eligible_symbols=("DBMF",), kicker_engaged=False,
        prices={"BIL": 91.0, "DBMF": 10.0})
    assert out.targets == {"SPY": 0.9, "GLD": 0.1}
    assert out.applied is False


def signal(*, stacked=True, fresh=False, below_fast=True):
    from strategy_x_bear import FastCrashSignal
    return FastCrashSignal(stacked, fresh, below_fast, "fixture")


def test_kicker_arms_then_holds_for_exactly_five_target_sessions():
    c = cfg()
    armed = advance_kicker(signal(fresh=True), state="idle", bars=0,
        cooldown=0, risk_on=False, bull_held=True, kicker_held=False,
        kicker_priceable=True, shadow=False, prior_targeted=False, config=c)
    assert (armed.state, armed.engaged, armed.bars) == ("armed", False, 0)
    held = advance_kicker(signal(), state=armed.state, bars=0,
        cooldown=0, risk_on=False, bull_held=False, kicker_held=False,
        kicker_priceable=True, shadow=False, prior_targeted=False, config=c)
    assert (held.state, held.engaged, held.bars) == ("holding", True, 1)
    for expected in (2, 3, 4, 5):
        held = advance_kicker(signal(), state=held.state, bars=held.bars,
            cooldown=held.cooldown, risk_on=False, bull_held=False,
            kicker_held=True, kicker_priceable=True, shadow=False,
            prior_targeted=True, config=c)
        assert (held.state, held.engaged, held.bars) == ("holding", True, expected)
    exited = advance_kicker(signal(), state=held.state, bars=held.bars,
        cooldown=0, risk_on=False, bull_held=False, kicker_held=True,
        kicker_priceable=True, shadow=False, prior_targeted=True, config=c)
    assert (exited.state, exited.engaged, exited.cooldown) == ("cooldown", False, 10)


def test_recovery_exits_and_cooldown_spends_ten_decision_sessions():
    out = advance_kicker(signal(stacked=False, below_fast=False),
        state="holding", bars=2, cooldown=0, risk_on=False, bull_held=False,
        kicker_held=True, kicker_priceable=True, shadow=False,
        prior_targeted=True, config=cfg())
    assert (out.state, out.engaged, out.cooldown) == ("cooldown", False, 10)
    for remaining in range(9, -1, -1):
        out = advance_kicker(signal(stacked=False, below_fast=False),
            state=out.state, bars=out.bars, cooldown=out.cooldown,
            risk_on=False, bull_held=False, kicker_held=False,
            kicker_priceable=True, shadow=False, prior_targeted=False,
            config=cfg())
        assert out.cooldown == remaining
    assert out.state == "idle"


def test_cache_reset_adopts_a_real_kicker_holding_but_never_invents_one():
    adopted = advance_kicker(signal(fresh=False), state="idle", bars=0,
        cooldown=0, risk_on=False, bull_held=False, kicker_held=True,
        kicker_priceable=True, shadow=False, prior_targeted=False, config=cfg())
    absent = advance_kicker(signal(fresh=False), state="idle", bars=0,
        cooldown=0, risk_on=False, bull_held=False, kicker_held=False,
        kicker_priceable=True, shadow=False, prior_targeted=False, config=cfg())
    assert (adopted.state, adopted.engaged, adopted.cooldown) == \
        ("cooldown", False, 10)
    assert (absent.state, absent.engaged) == ("idle", False)


def test_armed_state_never_flips_directly_from_tqqq_to_sqqq():
    out = advance_kicker(signal(), state="armed", bars=0, cooldown=0,
        risk_on=False, bull_held=True, kicker_held=False,
        kicker_priceable=True, shadow=False, prior_targeted=False, config=cfg())
    assert (out.state, out.engaged) == ("cooldown", False)


def test_stale_active_holding_state_cannot_buy_mid_event():
    out = advance_kicker(signal(fresh=False), state="holding", bars=-4,
        cooldown=0, risk_on=False, bull_held=False, kicker_held=False,
        kicker_priceable=True, shadow=False, prior_targeted=False, config=cfg())
    assert (out.state, out.engaged, out.cooldown) == ("cooldown", False, 10)


def test_nonfinite_hold_and_cooldown_settings_fail_safely():
    out = advance_kicker(signal(fresh=True), state="idle", bars=float("inf"),
        cooldown=float("nan"), risk_on=False, bull_held=False,
        kicker_held=False, kicker_priceable=True, shadow=False,
        prior_targeted=False,
        config=cfg(bear_kicker_max_bars=float("nan"),
                   bear_kicker_cooldown_bars=float("inf")))
    assert out.engaged is False
    assert out.state == "cooldown"


def test_invalid_runtime_counters_fail_to_safe_cooldown():
    bad_bars = advance_kicker(signal(), state="holding", bars=float("nan"),
        cooldown=0, risk_on=False, bull_held=False, kicker_held=True,
        kicker_priceable=True, shadow=False, prior_targeted=True, config=cfg())
    bad_cooldown = advance_kicker(signal(), state="cooldown", bars=0,
        cooldown="ten", risk_on=False, bull_held=False, kicker_held=False,
        kicker_priceable=True, shadow=False, prior_targeted=False, config=cfg())
    assert (bad_bars.state, bad_bars.engaged, bad_bars.cooldown) == \
        ("cooldown", False, 10)
    assert (bad_cooldown.state, bad_cooldown.engaged, bad_cooldown.cooldown) == \
        ("cooldown", False, 10)


def test_allocator_equal_weights_only_eligible_funds_and_uses_bil_residual():
    histories = {"DBMF": [10.0] * 60, "KMLM": [20.0] * 60, "CTA": [30.0] * 59}
    prices = {"DBMF": 10.0, "KMLM": 20.0, "CTA": 30.0, "BIL": 91.0, "SQQQ": 8.0}
    eligible = eligible_crisis_alpha(histories, prices, cfg())
    out = plan_bear_overlay({"SPY": 0.90, "GLD": 0.10}, risk_on=False,
        config=cfg(bear_system_mode="active"), eligible_symbols=eligible,
        kicker_engaged=True, prices=prices)
    assert eligible == ("DBMF", "KMLM")
    assert out.targets == {
        "GLD": 0.10, "DBMF": 0.10, "KMLM": 0.10,
        "SQQQ": 0.05, "BIL": 0.65,
    }
    assert sum(out.targets.values()) == 1.0


def test_missing_managers_route_their_budget_to_bil():
    out = plan_bear_overlay({"SPY": 0.9}, risk_on=False,
        config=cfg(bear_system_mode="active"), eligible_symbols=(),
        kicker_engaged=False, prices={"BIL": 91.0})
    assert out.targets == {"BIL": 0.9}


def test_invalid_manager_history_does_not_disqualify_other_managers():
    histories = {"DBMF": None, "KMLM": [20.0] * 60}
    prices = {"DBMF": 10.0, "KMLM": 20.0}
    assert eligible_crisis_alpha(histories, prices, cfg()) == ("KMLM",)


def test_nonfinite_manager_history_is_ineligible_and_budget_returns_to_bil():
    histories = {"DBMF": [float("nan")] * 60, "KMLM": [20.0] * 60}
    prices = {"DBMF": 10.0, "KMLM": 20.0, "BIL": 91.0}
    eligible = eligible_crisis_alpha(histories, prices, cfg())
    out = plan_bear_overlay({"SPY": 0.9}, risk_on=False,
        config=cfg(bear_system_mode="active"), eligible_symbols=eligible,
        kicker_engaged=False, prices=prices)
    assert eligible == ("KMLM",)
    assert out.targets == {"KMLM": 0.2, "BIL": 0.7}


def test_missing_bil_or_legacy_conflict_returns_baseline_unchanged():
    baseline = {"SPY": 0.9, "GLD": 0.1}
    missing = plan_bear_overlay(baseline, risk_on=False,
        config=cfg(bear_system_mode="active"), eligible_symbols=("DBMF",),
        kicker_engaged=True, prices={"DBMF": 10.0, "SQQQ": 8.0})
    conflict = plan_bear_overlay(baseline, risk_on=False,
        config=cfg(bear_system_mode="active", core_bear_symbol="SQQQ"),
        eligible_symbols=("DBMF",), kicker_engaged=True,
        prices={"BIL": 91.0, "DBMF": 10.0, "SQQQ": 8.0})
    assert missing.targets == baseline and missing.applied is False
    assert conflict.targets == baseline and conflict.applied is False


def test_unique_legacy_bear_configuration_returns_baseline_unchanged():
    baseline = {"SPY": 0.9, "GLD": 0.1}
    out = plan_bear_overlay(baseline, risk_on=False,
        config=cfg(bear_system_mode="active", core_bear_symbol="SH"),
        eligible_symbols=("DBMF",), kicker_engaged=True,
        prices={"BIL": 91.0, "DBMF": 10.0, "SQQQ": 8.0})
    assert out.targets == baseline and out.applied is False


def test_allocator_clamps_percentages_and_never_exceeds_defensive_budget():
    out = plan_bear_overlay({"SPY": 0.12, "GLD": 0.88}, risk_on=False,
        config=cfg(bear_system_mode="active", crisis_alpha_pct=2,
                   bear_kicker_pct=2), eligible_symbols=("DBMF",),
        kicker_engaged=True, prices={"BIL": 91, "DBMF": 10, "SQQQ": 8})
    assert out.targets == {"GLD": 0.88, "DBMF": 0.12}
    assert sum(out.targets.values()) == 1.0


def test_kicker_is_suppressed_when_its_full_fixed_weight_does_not_fit():
    out = plan_bear_overlay({"SPY": 0.22}, risk_on=False,
        config=cfg(bear_system_mode="active"), eligible_symbols=("DBMF",),
        kicker_engaged=True, prices={"BIL": 91.0, "DBMF": 10.0, "SQQQ": 8.0})
    assert out.targets == {"DBMF": 0.2, "BIL": 0.02}
    assert "SQQQ" not in out.targets


def test_kicker_receives_its_fixed_weight_at_the_six_decimal_exact_fit():
    out = plan_bear_overlay({"SPY": 0.25}, risk_on=False,
        config=cfg(bear_system_mode="active"), eligible_symbols=("DBMF",),
        kicker_engaged=True, prices={"BIL": 91.0, "DBMF": 10.0, "SQQQ": 8.0})
    assert out.targets == {"DBMF": 0.2, "SQQQ": 0.05}
    assert "BIL" not in out.targets


def test_nonfinite_baseline_returns_the_original_targets_unchanged():
    baseline = {"SPY": float("nan"), "GLD": 0.1}
    out = plan_bear_overlay(baseline, risk_on=False,
        config=cfg(bear_system_mode="active"), eligible_symbols=("DBMF",),
        kicker_engaged=False, prices={"BIL": 91.0, "DBMF": 10.0})
    assert set(out.targets) == {"SPY", "GLD"}
    assert out.targets["SPY"] != out.targets["SPY"]
    assert out.targets["GLD"] == 0.1
    assert out.applied is False


def test_colliding_roles_or_nonfinite_weights_fail_closed():
    baseline = {"SPY": 0.9, "GLD": 0.1}
    collision = plan_bear_overlay(baseline, risk_on=False,
        config=cfg(bear_system_mode="active", bear_cash_symbol="SPY"),
        eligible_symbols=("DBMF",), kicker_engaged=False,
        prices={"SPY": 500.0, "DBMF": 10.0})
    nonfinite = plan_bear_overlay(baseline, risk_on=False,
        config=cfg(bear_system_mode="active", crisis_alpha_pct=float("nan")),
        eligible_symbols=("DBMF",), kicker_engaged=False,
        prices={"BIL": 91.0, "DBMF": 10.0})
    assert collision.targets == baseline and collision.applied is False
    assert nonfinite.targets == baseline and nonfinite.applied is False
    assert eligible_crisis_alpha({"DBMF": [10.0] * 100}, {"DBMF": 10.0},
        cfg(crisis_alpha_min_history_bars=float("inf"))) == ()
