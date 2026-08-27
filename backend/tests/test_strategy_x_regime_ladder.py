"""Pure policy tests for the four-state Strategy X regime ladder.

The ladder exists because of one measurement: in EVERY bear window, by the time
the MA200 + vol gate flips risk-off, TQQQ has already lost 21-25%, while the
MA50 break fires at -6% to -9%.

    window          TQQQ at first risk-OFF   at first QQQ<MA50   at QQQ<MA20
    2018 Q4                 -25.1%                 -9.4%            -6.6%
    2020 covid              -21.1%                -20.7%           -13.7%
    2022 H1                 -25.4%                 -9.0%            -7.6%
    2025 spring             -22.9%                 -5.8%            -2.3%

CAUTION halves the levered leg on the mid break, DEFENSIVE hands the core to
the bear sleeves on the slow break, and RECOVERING re-enters at half size on the
fast reclaim rather than waiting for MA200 — the 2022 summer recovery is where
the deployed system lost -1.31% against SPY's +12.61% by staying defensive.
"""
import math
import os
import sys


_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)


from strategy_x_bear import (  # noqa: E402
    REGIME_STATES,
    FastCrashSignal,
    RegimeSignal,
    RegimeState,
    advance_kicker,
    advance_regime_state,
    blend_target_books,
    decode_regime_state,
    dual_timescale_signal,
    encode_regime_state,
    plan_bear_overlay,
)


def cfg(**overrides):
    value = {
        "bear_regime_enabled": True,
        "bear_regime_fast_ma_bars": 20,
        "bear_regime_mid_ma_bars": 50,
        "bear_regime_confirm_bars": 2,
        "bear_regime_transition_risk_fraction": 0.5,
    }
    value.update(overrides)
    return value


def ramp(count, start=100.0, step=0.0):
    return [start + step * i for i in range(count)]


def signal(*, fast_bad=False, fast_good=False, slow_on=True, vol_unsafe=False,
           emergency=False):
    """Build a signal directly, without going through close arithmetic."""
    return RegimeSignal(fast_bad=fast_bad, fast_good=fast_good, slow_on=slow_on,
                        vol_unsafe=vol_unsafe, emergency=emergency,
                        reason="synthetic")


def step(state, sig, *, observation_id, config=None, times=1):
    decision = None
    for _ in range(times):
        decision = advance_regime_state(sig, state, observation_id=observation_id,
                                        config=config or cfg())
        state = RegimeState(decision.state, decision.confirm_kind,
                            decision.confirm_count, decision.observation_id)
        observation_id += 1
    return decision, state


# ── the fractions the whole design turns on ───────────────────────────────────

def test_the_four_states_map_to_one_half_zero_half():
    fractions = {}
    for name in REGIME_STATES:
        state = RegimeState(name, "", 0, 10)
        # A repeated observation cannot move the state, so this reads the
        # fraction the state itself carries.
        decision = advance_regime_state(signal(), state, observation_id=10,
                                        config=cfg())
        fractions[name] = decision.risk_fraction
    assert fractions == {"full": 1.0, "caution": 0.5,
                         "defensive": 0.0, "recovering": 0.5}


def test_the_transition_fraction_is_configurable_for_both_transition_states():
    for name in ("caution", "recovering"):
        state = RegimeState(name, "", 0, 4)
        decision = advance_regime_state(
            signal(), state, observation_id=4,
            config=cfg(bear_regime_transition_risk_fraction=0.25))
        assert decision.risk_fraction == 0.25


# ── every allowed transition, and the prohibited ones ─────────────────────────

def test_full_falls_to_caution_only_after_the_mid_break_confirms():
    state = RegimeState("full", "", 0, 0)
    first, state = step(state, signal(fast_bad=True), observation_id=1)
    assert first.state == "full" and first.confirm_count == 1
    second, state = step(state, signal(fast_bad=True), observation_id=2)
    assert second.state == "caution" and second.confirm_count == 0


def test_caution_climbs_back_to_full_only_after_the_reclaim_confirms():
    state = RegimeState("caution", "", 0, 0)
    first, state = step(state, signal(fast_good=True), observation_id=1)
    assert first.state == "caution"
    second, state = step(state, signal(fast_good=True), observation_id=2)
    assert second.state == "full"


def test_defensive_rises_to_recovering_on_the_fast_reclaim_not_the_slow_one():
    state = RegimeState("defensive", "", 0, 0)
    decision, state = step(state, signal(slow_on=False, fast_good=True),
                           observation_id=1, times=2)
    assert decision.state == "recovering"
    assert decision.risk_fraction == 0.5


def test_recovering_falls_back_to_defensive_when_the_fast_signal_breaks():
    state = RegimeState("recovering", "", 0, 0)
    decision, state = step(state, signal(slow_on=False, fast_bad=True),
                           observation_id=1, times=2)
    assert decision.state == "defensive"


def test_the_slow_signal_returning_promotes_straight_to_full():
    for name in ("defensive", "recovering"):
        state = RegimeState(name, "", 0, 0)
        decision, _ = step(state, signal(slow_on=True, fast_good=True),
                           observation_id=1, times=2)
        assert decision.state == "full", name


def test_the_slow_signal_breaking_drops_straight_to_defensive_from_either_side():
    for name in ("full", "caution"):
        state = RegimeState(name, "", 0, 0)
        decision, _ = step(state, signal(slow_on=False), observation_id=1,
                           times=2)
        assert decision.state == "defensive", name


def test_full_can_never_reach_defensive_while_the_slow_signal_holds():
    state = RegimeState("full", "", 0, 0)
    decision, _ = step(state, signal(fast_bad=True, slow_on=True),
                       observation_id=1, times=20)
    assert decision.state == "caution"


def test_defensive_can_never_reach_full_on_the_fast_signal_alone():
    state = RegimeState("defensive", "", 0, 0)
    decision, _ = step(state, signal(slow_on=False, fast_good=True),
                       observation_id=1, times=20)
    assert decision.state == "recovering"


# ── confirmation counting ─────────────────────────────────────────────────────

def test_opposite_evidence_resets_the_confirmation_counter():
    state = RegimeState("full", "", 0, 0)
    first, state = step(state, signal(fast_bad=True), observation_id=1)
    assert first.confirm_count == 1
    reset, state = step(state, signal(fast_good=True), observation_id=2)
    assert reset.confirm_count == 0 and reset.state == "full"
    again, state = step(state, signal(fast_bad=True), observation_id=3)
    assert again.state == "full" and again.confirm_count == 1


def test_a_duplicate_observation_leaves_state_and_counters_untouched():
    state = RegimeState("full", "mid_break", 1, 7)
    decision = advance_regime_state(signal(fast_bad=True), state,
                                    observation_id=7, config=cfg())
    assert (decision.state, decision.confirm_kind, decision.confirm_count) == (
        "full", "mid_break", 1)
    assert decision.reason == "observation unchanged"


def test_an_observation_that_moves_backwards_is_refused_as_corruption():
    state = RegimeState("full", "mid_break", 1, 9)
    decision = advance_regime_state(signal(fast_bad=True), state,
                                    observation_id=4, config=cfg())
    assert decision.state == "defensive" and decision.risk_fraction == 0.0


def test_one_confirmation_bar_transitions_immediately():
    state = RegimeState("full", "", 0, 0)
    decision, _ = step(state, signal(fast_bad=True), observation_id=1,
                       config=cfg(bear_regime_confirm_bars=1))
    assert decision.state == "caution"


# ── failure must always be toward less exposure ───────────────────────────────

def test_an_emergency_drops_to_defensive_without_waiting_for_confirmation():
    state = RegimeState("full", "", 0, 0)
    decision, _ = step(state, signal(slow_on=False, vol_unsafe=True,
                                     emergency=True), observation_id=1)
    assert decision.state == "defensive" and decision.risk_fraction == 0.0


def test_a_corrupt_state_name_fails_defensive_rather_than_inferring_full():
    decision = advance_regime_state(signal(), RegimeState("euphoric", "", 0, 0),
                                    observation_id=2, config=cfg())
    assert decision.state == "defensive" and decision.risk_fraction == 0.0


def test_a_nonfinite_counter_cannot_raise_the_risk_fraction():
    for bad in (float("nan"), float("inf"), -3, "2", True):
        decision = advance_regime_state(
            signal(fast_good=True), RegimeState("caution", "mid_reclaim", bad, 1),
            observation_id=2, config=cfg())
        assert decision.risk_fraction <= 0.5, bad


def test_a_nonfinite_transition_fraction_falls_back_to_the_default():
    for bad in (float("nan"), float("inf"), None, "half"):
        decision = advance_regime_state(
            signal(), RegimeState("caution", "", 0, 0), observation_id=2,
            config=cfg(bear_regime_transition_risk_fraction=bad))
        assert decision.risk_fraction == 0.5, bad


def test_the_transition_fraction_can_never_exceed_one_or_go_negative():
    for value, expected in ((5.0, 1.0), (-2.0, 0.0)):
        decision = advance_regime_state(
            signal(), RegimeState("caution", "", 0, 0), observation_id=2,
            config=cfg(bear_regime_transition_risk_fraction=value))
        assert decision.risk_fraction == expected


def test_a_disabled_ladder_is_binary_exactly_as_the_shipped_code_is():
    off = cfg(bear_regime_enabled=False)
    on_state = RegimeState("full", "", 0, 0)
    assert advance_regime_state(signal(fast_bad=True), on_state,
                                observation_id=2, config=off).risk_fraction == 1.0
    assert advance_regime_state(signal(slow_on=False), on_state,
                                observation_id=2, config=off).state == "defensive"
    assert advance_regime_state(signal(slow_on=False, fast_good=True),
                                RegimeState("defensive", "", 0, 0),
                                observation_id=2, config=off).state == "defensive"


# ── the signal itself ─────────────────────────────────────────────────────────

def test_the_mid_break_is_measured_against_the_mid_moving_average():
    closes = ramp(60, 100.0, 1.0) + [90.0]
    sig = dual_timescale_signal(closes, slow_on=True, vol_unsafe=False,
                                config=cfg())
    assert sig.fast_bad and not sig.fast_good


def test_the_fast_reclaim_is_measured_against_the_fast_moving_average():
    closes = ramp(60, 200.0, -1.0) + [400.0]
    sig = dual_timescale_signal(closes, slow_on=False, vol_unsafe=False,
                                config=cfg())
    assert sig.fast_good and not sig.fast_bad


def test_a_price_between_the_two_averages_asserts_neither_direction():
    # Falling tape: MA20 sits below MA50, so a close between them is neither a
    # confirmed break nor a confirmed reclaim.
    closes = ramp(60, 200.0, -1.0)
    fast = sum(closes[-20:]) / 20
    mid = sum(closes[-50:]) / 50
    closes = closes + [(fast + mid) / 2]
    sig = dual_timescale_signal(closes, slow_on=False, vol_unsafe=False,
                                config=cfg())
    assert not sig.fast_bad and not sig.fast_good


def test_insufficient_history_asserts_no_direction_and_is_not_an_emergency():
    sig = dual_timescale_signal(ramp(10), slow_on=True, vol_unsafe=False,
                                config=cfg())
    assert not sig.fast_bad and not sig.fast_good and not sig.emergency
    assert "insufficient" in sig.reason


def test_a_nonfinite_close_reads_as_an_emergency_not_as_a_reclaim():
    closes = ramp(60) + [float("nan")]
    sig = dual_timescale_signal(closes, slow_on=True, vol_unsafe=False,
                                config=cfg())
    assert sig.emergency and not sig.fast_good


def test_a_breached_vol_gate_is_an_emergency():
    sig = dual_timescale_signal(ramp(60, 100.0, 1.0), slow_on=False,
                                vol_unsafe=True, config=cfg())
    assert sig.emergency and not sig.slow_on


# ── convex blending of the two books ──────────────────────────────────────────

def test_blending_preserves_the_total_weight_of_both_books():
    risk = {"TQQQ": 0.8, "SPY": 0.1, "GLD": 0.1}
    defensive = {"BIL": 0.7, "DBMF": 0.2, "GLD": 0.1}
    blended = blend_target_books(risk, defensive, 0.5)
    assert math.isclose(sum(blended.values()), 1.0, abs_tol=1e-9)
    assert math.isclose(blended["TQQQ"], 0.4, abs_tol=1e-9)
    assert math.isclose(blended["BIL"], 0.35, abs_tol=1e-9)
    assert math.isclose(blended["GLD"], 0.1, abs_tol=1e-9)


def test_a_fraction_of_one_returns_the_risk_book_and_zero_the_defensive_one():
    risk = {"TQQQ": 0.8, "SPY": 0.2}
    defensive = {"BIL": 1.0}
    assert blend_target_books(risk, defensive, 1.0) == risk
    assert blend_target_books(risk, defensive, 0.0) == defensive


def test_blending_drops_nothing_and_never_emits_a_negative_weight():
    blended = blend_target_books({"TQQQ": 0.8, "SPY": 0.2}, {"BIL": 1.0}, 0.25)
    assert set(blended) == {"TQQQ", "SPY", "BIL"}
    assert all(weight >= 0 for weight in blended.values())


def test_a_nonfinite_weight_in_either_book_refuses_the_blend():
    for risk, defensive in (({"TQQQ": float("nan")}, {"BIL": 1.0}),
                            ({"TQQQ": 1.0}, {"BIL": float("inf")}),
                            ({"TQQQ": -0.5}, {"BIL": 1.0})):
        assert blend_target_books(risk, defensive, 0.5) is None


def test_a_nonfinite_fraction_refuses_the_blend():
    for bad in (float("nan"), float("inf"), None, "half", 1.5, -0.1):
        assert blend_target_books({"TQQQ": 1.0}, {"BIL": 1.0}, bad) is None


# ── the explicit defensive budget ─────────────────────────────────────────────

def overlay_cfg(**overrides):
    value = {
        "bear_system_mode": "active",
        "bear_cash_symbol": "BIL",
        "crisis_alpha_symbols": ["DBMF"],
        "crisis_alpha_pct": 0.20,
        "bear_kicker_symbol": "SQQQ",
        "bear_kicker_pct": 0.05,
        "core_chop_symbol": "SPY",
        "core_bull_symbol": "TQQQ",
        "core_bear_symbol": "",
        "core_filter_symbol": "QQQ",
        "commodity_symbols": ["GLD"],
    }
    value.update(overrides)
    return value


def test_an_explicit_budget_leaves_the_unfilled_stock_sleeve_in_spy():
    # 10% commodities, 80% core, 10% Graph fallback — all of it in one SPY
    # target because plan_targets merges the core residual with the fallback.
    allocation = plan_bear_overlay(
        {"SPY": 0.9, "GLD": 0.1}, risk_on=False, config=overlay_cfg(),
        eligible_symbols=("DBMF",), kicker_engaged=False,
        prices={"BIL": 91.0, "DBMF": 26.0}, defensive_budget=0.8)
    assert allocation.applied
    assert math.isclose(allocation.targets["SPY"], 0.1, abs_tol=1e-9)
    assert math.isclose(allocation.targets["GLD"], 0.1, abs_tol=1e-9)
    assert math.isclose(sum(allocation.targets.values()), 1.0, abs_tol=1e-9)


def test_omitting_the_budget_preserves_the_deployed_whole_chop_replacement():
    allocation = plan_bear_overlay(
        {"SPY": 0.9, "GLD": 0.1}, risk_on=False, config=overlay_cfg(),
        eligible_symbols=("DBMF",), kicker_engaged=False,
        prices={"BIL": 91.0, "DBMF": 26.0})
    assert allocation.applied and "SPY" not in allocation.targets


def test_a_budget_larger_than_the_chop_target_is_refused():
    allocation = plan_bear_overlay(
        {"SPY": 0.5}, risk_on=False, config=overlay_cfg(),
        eligible_symbols=(), kicker_engaged=False, prices={"BIL": 91.0},
        defensive_budget=0.8)
    assert not allocation.applied


def test_a_nonfinite_budget_is_refused():
    for bad in (float("nan"), float("inf"), -0.1, "0.8"):
        allocation = plan_bear_overlay(
            {"SPY": 0.9}, risk_on=False, config=overlay_cfg(),
            eligible_symbols=(), kicker_engaged=False, prices={"BIL": 91.0},
            defensive_budget=bad)
        assert not allocation.applied, bad


# ── the kicker's observation clock ────────────────────────────────────────────

def kicker_cfg(**overrides):
    value = {
        "bear_kicker_fast_ma_bars": 20,
        "bear_kicker_mid_ma_bars": 50,
        "bear_kicker_long_ma_bars": 200,
        "bear_kicker_max_bars": 5,
        "bear_kicker_cooldown_bars": 10,
    }
    value.update(overrides)
    return value


def crash(**overrides):
    value = {"stacked": True, "fresh": False, "below_fast": True,
             "reason": "synthetic"}
    value.update(overrides)
    return FastCrashSignal(**value)


def test_a_duplicate_observation_does_not_spend_a_kicker_hold_bar():
    decision = advance_kicker(
        crash(), state="holding", bars=2, cooldown=0, risk_on=False,
        bull_held=False, kicker_held=True, kicker_priceable=True, shadow=False,
        prior_targeted=False, config=kicker_cfg(), observation_id=5,
        last_observation_id=5)
    assert decision.state == "holding" and decision.bars == 2


def test_a_duplicate_observation_does_not_spend_a_cooldown_bar():
    decision = advance_kicker(
        crash(stacked=False, below_fast=False), state="cooldown", bars=0,
        cooldown=4, risk_on=False, bull_held=False, kicker_held=False,
        kicker_priceable=True, shadow=True, prior_targeted=False,
        config=kicker_cfg(), observation_id=5, last_observation_id=5)
    assert decision.state == "cooldown" and decision.cooldown == 4


def test_a_safety_exit_still_fires_on_a_duplicate_observation():
    decision = advance_kicker(
        crash(), state="holding", bars=2, cooldown=0, risk_on=True,
        bull_held=False, kicker_held=True, kicker_priceable=True, shadow=False,
        prior_targeted=False, config=kicker_cfg(), observation_id=5,
        last_observation_id=5)
    assert decision.state == "cooldown" and not decision.engaged


def test_an_unpriceable_kicker_still_exits_on_a_duplicate_observation():
    decision = advance_kicker(
        crash(), state="holding", bars=2, cooldown=0, risk_on=False,
        bull_held=False, kicker_held=True, kicker_priceable=False, shadow=False,
        prior_targeted=False, config=kicker_cfg(), observation_id=5,
        last_observation_id=5)
    assert decision.state == "cooldown" and not decision.engaged


def test_a_new_observation_spends_a_hold_bar_exactly_as_before():
    decision = advance_kicker(
        crash(), state="holding", bars=2, cooldown=0, risk_on=False,
        bull_held=False, kicker_held=True, kicker_priceable=True, shadow=False,
        prior_targeted=False, config=kicker_cfg(), observation_id=6,
        last_observation_id=5)
    assert decision.state == "holding" and decision.bars == 3


def test_omitting_the_observation_clock_preserves_the_deployed_behaviour():
    decision = advance_kicker(
        crash(), state="holding", bars=2, cooldown=0, risk_on=False,
        bull_held=False, kicker_held=True, kicker_priceable=True, shadow=False,
        prior_targeted=False, config=kicker_cfg())
    assert decision.state == "holding" and decision.bars == 3


# ── the persisted envelope ────────────────────────────────────────────────────

def test_a_round_trip_returns_the_same_state():
    state = RegimeState("caution", "mid_reclaim", 1, 4210)
    decoded = decode_regime_state(
        encode_regime_state(state, authority="active", fingerprint="QQQ:20:50:2"),
        authority="active", fingerprint="QQQ:20:50:2")
    assert decoded == state


def test_a_shadow_envelope_is_never_read_as_active_authority():
    envelope = encode_regime_state(RegimeState("full", "", 0, 10),
                                   authority="shadow", fingerprint="f")
    assert decode_regime_state(envelope, authority="active",
                               fingerprint="f") is None


def test_a_changed_fingerprint_invalidates_the_counters():
    envelope = encode_regime_state(RegimeState("full", "", 0, 10),
                                   authority="active", fingerprint="QQQ:20:50:2")
    assert decode_regime_state(envelope, authority="active",
                               fingerprint="QQQ:20:50:3") is None


def test_a_missing_envelope_is_initialization_not_corruption():
    assert decode_regime_state(None, authority="active", fingerprint="f") is None
    assert decode_regime_state({}, authority="active", fingerprint="f") is None


def test_a_malformed_envelope_decodes_to_nothing_rather_than_to_full():
    for bad in ({"v": 1}, {"v": 2, "state": "full"}, {"v": 1, "state": 3},
                {"v": 1, "state": "full", "obs": "x"}, [1, 2], "full"):
        assert decode_regime_state(bad, authority="active",
                                   fingerprint="f") is None
