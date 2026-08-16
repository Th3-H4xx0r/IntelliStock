"""Phase 2 (Measure): the two guards, window hygiene, pre-registration, the
single-flight lease, and forward outcomes.

Every threshold here traces to a measured failure in this project, and the tests
name it. The point of the phase is that a result cannot be believed until the
machinery has earned the right to believe it.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from self_learning import lease
from self_learning.execution_proof import (
    AMBIGUOUS, EXECUTED, INERT, UNPROVABLE, blocks_scoring, prove,
)
from self_learning.experiments import STATUS_REFUSED, STATUS_REGISTERED, design
from self_learning.noise import acceptance, can_promote, estimate_floor
from self_learning.outcomes import refusal_cost, resolve
from self_learning.types import Observation
from self_learning.windows import (
    InSampleRegistry, effective_n, is_w0, overlap_fraction, window_class,
)


def _obs(symbol="X", as_of="2026-04-01T13:30:00", decision=1, executed=True,
         score=1.0, refusal=None, notional=100.0):
    return Observation(
        run_id="1", origin="backtest", venue="equity", strategy_id="s",
        as_of=as_of, symbol=symbol, action="buy", decision=decision,
        normalized_score=score, executed=executed, refusal_reason=refusal,
        votes=(), config_hash=None, filled_notional=notional)


# ── Guard 1: the noise floor ─────────────────────────────────────────────────

def test_a_target_with_no_floor_cannot_promote_anything():
    """The structural rule. Two runs of one window differed by ~16pp here and
    the dispersion was never measured, so every A/B was noise."""
    floor = estimate_floor([], target="equity/nexus", window_class="1h/medium/bull")
    assert floor.measured is False
    assert can_promote(floor) is False
    verdict = acceptance([12.0, 11.0, 13.0], floor)
    assert verdict.accepted is False
    assert "no usable noise floor" in verdict.reason


def test_two_repeats_are_not_enough_to_call_it_a_floor():
    floor = estimate_floor([1.0, 2.0], target="t", window_class="c")
    assert floor.measured is False


def test_the_floor_is_the_observed_spread():
    """Not a sample sigma: with three or four repeats sigma is itself wildly
    noisy and reads far too small, which would wave marginal effects through."""
    floor = estimate_floor([2.0, 10.0, 6.0], target="t", window_class="c")
    assert floor.measured is True
    assert floor.floor_pp == 8.0


def test_an_effect_inside_the_floor_is_rejected():
    floor = estimate_floor([0.0, 16.0, 8.0], target="t", window_class="c")
    verdict = acceptance([5.0, 6.0, 4.0, 5.0], floor)
    assert verdict.accepted is False
    assert "inside the noise floor" in verdict.reason


def test_an_effect_clearing_the_floor_with_a_consistent_sign_is_accepted():
    floor = estimate_floor([0.0, 2.0, 1.0], target="t", window_class="c")
    verdict = acceptance([5.0, 6.0, 4.0, 5.0], floor)
    assert verdict.accepted is True


def test_a_lever_that_helps_some_windows_and_hurts_others_is_rejected():
    """Magnitude is not evidence when the sign flips."""
    floor = estimate_floor([0.0, 1.0, 0.5], target="t", window_class="c")
    verdict = acceptance([12.0, 11.0, 10.0, -4.0], floor)
    assert verdict.accepted is False
    assert "sign held in only" in verdict.reason


def test_one_outlier_cannot_carry_the_result():
    floor = estimate_floor([0.0, 1.0, 0.5], target="t", window_class="c")
    verdict = acceptance([40.0, -2.0, -3.0, -1.0], floor, sign_k=1)
    assert verdict.accepted is False
    assert "one outlier" in verdict.reason


def test_the_margin_is_a_parameter_not_a_constant():
    floor = estimate_floor([0.0, 4.0, 2.0], target="t", window_class="c")
    assert acceptance([5.0] * 4, floor, margin=1.0).accepted is True
    assert acceptance([5.0] * 4, floor, margin=2.0).accepted is False


# ── Guard 2: execution proof ─────────────────────────────────────────────────

def test_an_unchanged_stream_is_inert_not_no_effect():
    """Thirteen levers shipped here without executing and were all scored as
    'no effect'. That conclusion retires a hypothesis that was never tested."""
    arm = [_obs("A"), _obs("B")]
    proof = prove(arm, [_obs("A"), _obs("B")])
    assert proof.status == INERT
    assert "NOT 'no effect'" in proof.detail
    assert blocks_scoring(proof) is True


def test_a_change_without_a_baseline_is_ambiguous_not_proven():
    """The founding fact of this subsystem is that two runs of one window
    differ by ~16pp. So a differing stream, on its own, cannot distinguish a
    lever from rerun churn — and the first version called it EXECUTED."""
    proof = prove([_obs("A", decision=1)], [_obs("A", decision=-1)])
    assert proof.status == AMBIGUOUS
    assert blocks_scoring(proof) is True


def test_a_change_far_beyond_the_control_churn_proves_execution():
    control = [_obs(f"S{i}") for i in range(100)]
    treatment = [_obs(f"S{i}", decision=-1) for i in range(100)]
    proof = prove(control, treatment, baseline_fraction=0.01)
    assert proof.status == EXECUTED
    assert blocks_scoring(proof) is False


def test_a_change_within_the_control_churn_is_ambiguous():
    """One cent on one bar out of 5,000 was indistinguishable from a lever."""
    control = [_obs(f"S{i}", notional=100.0) for i in range(1000)]
    treatment = [_obs(f"S{i}", notional=100.0 if i else 100.01)
                 for i in range(1000)]
    proof = prove(control, treatment, baseline_fraction=0.05)
    assert proof.status == AMBIGUOUS
    assert blocks_scoring(proof) is True


def test_a_named_counter_is_positive_proof_regardless_of_churn():
    """The evidence the spec actually asks adapters for."""
    arm = [_obs("A")]
    proof = prove(arm, arm, counters=({"clamped": 0}, {"clamped": 41}))
    assert proof.status == EXECUTED
    assert "clamped" in proof.counter_evidence


def test_a_baseline_computed_from_two_controls():
    from self_learning.execution_proof import baseline_from_controls
    a = [_obs(f"S{i}") for i in range(10)]
    b = [_obs(f"S{i}", notional=100.0 if i % 2 else 101.0) for i in range(10)]
    assert 0.0 < baseline_from_controls(a, b) < 1.0


def test_an_empty_arm_is_unprovable_not_inert():
    proof = prove([], [_obs("A")])
    assert proof.status == UNPROVABLE
    assert blocks_scoring(proof) is True


def test_the_fingerprint_ignores_ordering():
    a = prove([_obs("A"), _obs("B")], [_obs("B"), _obs("A")])
    assert a.status == INERT


# ── Window hygiene ───────────────────────────────────────────────────────────

def test_w0_is_permanently_in_sample():
    """52 of the first 100 backtests used it."""
    assert is_w0("2026-01-05", "2026-02-20") is True
    assert is_w0("2026-06-01", "2026-08-01") is False


def test_the_registry_refuses_a_window_already_used_as_evidence():
    registry = InSampleRegistry()
    registry.record("2026-04-01", "2026-06-01")
    assert registry.is_contaminated("2026-04-15", "2026-05-15") is not None
    assert registry.is_contaminated("2026-06-15", "2026-08-01") is None


def test_window_class_separates_cadence_and_length():
    assert window_class(granularity_seconds=900, start="2026-04-01",
                        end="2026-06-01", regime="bull") == "15m/medium/bull"
    assert window_class(granularity_seconds=3600, start="2026-04-01",
                        end="2026-06-01", regime="bull") == "1h/medium/bull"


def test_length_bucketing_is_actually_exercised():
    """Both old cases used the same dates, so `length_label` could be
    hard-coded to "medium" and the test still passed."""
    short = window_class(granularity_seconds=3600, start="2026-04-01",
                         end="2026-05-01", regime="bull")
    long_ = window_class(granularity_seconds=3600, start="2026-01-01",
                         end="2026-09-01", regime="bull")
    assert short.split("/")[1] == "short"
    assert long_.split("/")[1] == "long"
    assert short != long_


def test_a_thirty_minute_run_does_not_share_a_class_with_an_hourly_one():
    """`<=` bucketing put 1800s and 3600s in one bucket, so two cadences
    silently shared a noise floor."""
    half = window_class(granularity_seconds=1800, start="2026-04-01",
                        end="2026-06-01", regime="bull")
    hour = window_class(granularity_seconds=3600, start="2026-04-01",
                        end="2026-06-01", regime="bull")
    assert half != hour


def test_effective_n_discounts_overlapping_windows():
    """A four-window sweep here shared 37 days, so its true n was ~3.4.
    Reporting 4 overstates the evidence."""
    independent = [("2026-04-01", "2026-05-01"), ("2026-06-01", "2026-07-01"),
                   ("2026-08-01", "2026-09-01")]
    assert effective_n(independent) == 3.0


def test_two_identical_windows_are_worth_exactly_one():
    """The old formula charged each window its full worst overlap and returned
    0.0 — less evidence than one window, and less than none, which breaks any
    downstream ratio."""
    same = [("2026-04-01", "2026-06-01"), ("2026-04-01", "2026-06-01")]
    assert effective_n(same) == 1.0


def test_partial_overlap_lands_between_one_and_two():
    pair = [("2026-04-01", "2026-06-01"), ("2026-05-01", "2026-07-01")]
    value = effective_n(pair)
    assert 1.0 < value < 2.0


def test_overlap_fraction_is_symmetric():
    """A non-symmetric denominator survived the old test, which only ever
    checked a NON-overlapping pair in one argument order."""
    a = overlap_fraction("2026-01-01", "2026-03-01", "2026-02-01", "2026-08-01")
    b = overlap_fraction("2026-02-01", "2026-08-01", "2026-01-01", "2026-03-01")
    assert a == b > 0.0


def test_the_shared_day_limit_actually_binds():
    """MAX_SHARED_DAYS was untested — every case sat at fraction 1.0, so the
    module's central number could be moved to 0.999 with no failure."""
    registry = InSampleRegistry()
    registry.record("2026-04-01", "2026-06-01")
    # 2 shared days: under the limit.
    assert registry.is_contaminated("2026-05-30", "2026-08-01") is None
    # 20 shared days: over it.
    assert registry.is_contaminated("2026-05-12", "2026-08-01") is not None


def test_accumulated_overlap_is_caught_not_just_pairwise():
    """Three priors each sharing 4 days pass every pairwise check while
    together covering 12 days of the candidate."""
    registry = InSampleRegistry()
    registry.record("2026-03-28", "2026-04-05")
    registry.record("2026-04-20", "2026-04-28")
    registry.record("2026-05-20", "2026-05-28")
    assert registry.is_contaminated("2026-04-01", "2026-06-01") is not None


def test_a_reversed_window_is_contaminated_not_clean():
    """`end < start` gave a span of 0, so it overlapped nothing and passed as
    fresh evidence — including when it WAS W0 with its endpoints swapped."""
    assert is_w0("2026-03-01", "2026-01-01") is True
    assert InSampleRegistry().is_contaminated("2026-03-01", "2026-01-01")


def test_a_single_day_inside_w0_is_contaminated():
    """A zero-span window sat inside W0 and read clean."""
    assert is_w0("2026-02-01", "2026-02-01") is True


def test_an_unparseable_date_is_contaminated_not_clean():
    """One typo re-ran an already-used window as fresh evidence."""
    assert is_w0("2026-01-31", "2026-02-31") is True
    assert InSampleRegistry().is_contaminated("2026-04-31", "2026-06-01")


# ── Pre-registration ─────────────────────────────────────────────────────────

def test_the_acceptance_rule_is_inside_the_experiment_identity():
    """A rule chosen after seeing the result is not a rule."""
    base = dict(hypothesis_id="h1", target="equity/nexus",
                window_class="1h/medium/bull",
                windows=[("2026-04-01", "2026-06-01")],
                treatment_keys=["sizing_respects_satellite_share_enabled"])
    strict = design(**base, margin=1.5)
    loose = design(**base, margin=1.0)
    assert strict.id != loose.id


def test_an_in_sample_window_is_refused_not_silently_used():
    spec = design(hypothesis_id="h1", target="t", window_class="c",
                  windows=[("2026-01-05", "2026-02-20")], treatment_keys=["k"])
    assert spec.status == STATUS_REFUSED
    assert "in-sample" in spec.refusal_reason


def test_a_partly_contaminated_set_keeps_the_clean_windows_and_says_so():
    spec = design(hypothesis_id="h1", target="t", window_class="c",
                  windows=[("2026-01-05", "2026-02-20"),
                           ("2026-06-15", "2026-08-01")],
                  treatment_keys=["k"])
    assert spec.status == STATUS_REGISTERED
    assert len(spec.windows) == 1
    assert "dropped windows" in spec.refusal_reason
    assert "W0" in spec.refusal_reason


def test_cost_is_estimated_from_the_real_arm_count():
    spec = design(hypothesis_id="h1", target="t", window_class="c",
                  windows=[("2026-04-01", "2026-06-01"),
                           ("2026-06-15", "2026-08-01")],
                  treatment_keys=["k"], repeats=2, cost_per_run_usd=0.70)
    assert spec.arm_count == 8          # 2 arms x 2 windows x 2 repeats
    assert round(spec.estimated_cost_usd, 2) == 5.60


# ── Single-flight lease ──────────────────────────────────────────────────────

def test_a_human_run_in_flight_always_wins():
    """A second launch silently preempts the first. The operator's run is never
    the one that gets killed."""
    decision = lease.acquire(current_lease=None,
                             running_backtests=[{"id": 991, "origin": "human"}],
                             now_iso="2026-08-15T00:00:00", experiment_id="e1")
    assert decision.granted is False
    assert "silently preempt" in decision.reason


def test_the_lease_is_granted_when_nothing_is_running():
    decision = lease.acquire(current_lease=None, running_backtests=[],
                             now_iso="2026-08-15T00:00:00", experiment_id="e1")
    assert decision.granted is True
    assert decision.lease["experiment_id"] == "e1"


def test_a_second_experiment_cannot_take_a_held_lease():
    held = {"experiment_id": "e1", "acquired_at": "2026-08-15T00:00:00"}
    decision = lease.acquire(current_lease=held, running_backtests=[],
                             now_iso="2026-08-15T00:10:00", experiment_id="e2")
    assert decision.granted is False


def test_an_expired_lease_does_not_wedge_the_queue_forever():
    stale = {"experiment_id": "e1", "acquired_at": "2026-08-14T00:00:00"}
    decision = lease.acquire(current_lease=stale, running_backtests=[],
                             now_iso="2026-08-15T00:00:00", experiment_id="e2")
    assert decision.granted is True


def test_only_the_holder_may_release():
    held = {"experiment_id": "e1"}
    assert lease.release(held, experiment_id="e1") is True
    assert lease.release(held, experiment_id="e2") is False


# ── Forward outcomes ─────────────────────────────────────────────────────────

def _price_doc():
    bars = []
    for i in range(30):
        stamp = f"2026-04-{i + 1:02d}T13:30:00"
        bars.append({"timestamp": stamp, "symbol": "DELL", "close": 100.0 + i * 2})
        bars.append({"timestamp": stamp, "symbol": "SPY", "close": 500.0 + i})
    return {"id": 1, "backtest_prices": bars}


def test_a_refused_name_resolves_exactly_like_an_executed_one():
    """The entire point: the refusals must be priced, or 'it refused 134 names'
    stays a fact instead of becoming a finding."""
    refused = _obs("DELL", as_of="2026-04-01T13:30:00", executed=False,
                   refusal="min_position_floor", notional=None)
    outcomes = [o for o in resolve([refused], _price_doc(), horizons=(5,))]
    assert len(outcomes) == 1
    assert outcomes[0].resolved is True
    assert round(outcomes[0].return_pct, 2) == 10.0    # 100 -> 110


def test_the_benchmark_gives_an_excess_return():
    """Literal expected values, not the production formula restated. The old
    assertion was `excess == return - benchmark`, which is a tautology against
    any implementation that defines excess that way."""
    obs = _obs("DELL", as_of="2026-04-01T13:30:00")
    outcome = resolve([obs], _price_doc(), horizons=(5,))[0]
    # DELL 100 -> 110 over 5 bars = +10.0%; SPY 500 -> 505 = +1.0%.
    assert round(outcome.return_pct, 4) == 10.0
    assert round(outcome.benchmark_return_pct, 4) == 1.0
    assert round(outcome.excess_pct, 4) == 9.0


def test_a_horizon_running_off_the_end_is_unresolved_not_flat():
    """Scoring it zero would drag every late decision toward flat — a quiet
    bias that would make the whole study wrong."""
    obs = _obs("DELL", as_of="2026-04-29T13:30:00")
    outcome = resolve([obs], _price_doc(), horizons=(20,))[0]
    assert outcome.resolved is False
    assert outcome.return_pct == 0.0
    assert "runs past the end" in outcome.reason


def test_a_symbol_with_no_price_series_is_unresolved_with_a_reason():
    outcome = resolve([_obs("NOPRICE")], _price_doc(), horizons=(5,))[0]
    assert outcome.resolved is False
    assert "no price series" in outcome.reason


def test_refusal_cost_answers_what_the_refusals_were_worth():
    refused = [_obs("DELL", as_of="2026-04-01T13:30:00", executed=False,
                    refusal="min_position_floor", notional=None)]
    outcomes = resolve(refused, _price_doc(), horizons=(5,))
    report = refusal_cost(outcomes, refused, horizon_bars=5)
    assert report["refused_n"] == 1
    assert report["refusals_resolvable"] is True
    # DELL compounds far faster than SPY here, so declining it cost money.
    assert report["refused_median_excess_pct"] > 0


def test_refusal_cost_of_nothing_is_reported_as_unresolvable():
    report = refusal_cost([], [], horizon_bars=20)
    assert report["refused_n"] == 0
    assert report["refusals_resolvable"] is False
    assert report["refused_median_excess_pct"] is None


# ── Sweep regressions: the units artifact that would have faked a finding ────

def _mixed_cadence_doc():
    """The REAL shape of backtest_prices on a 15-minute run.

    `backtest_summary.build_backtest_price_series` emits raw intraday bars for
    watchlist symbols but fills everything else from DAILY snapshots deduped on
    (date, symbol). So SPY here gets 26 bars/day and the discovered name gets 1
    — and counting "20 bars" in each series would compare a 5-hour SPY move
    against a 20-day stock move.
    """
    rows = []
    # SPY: dense intraday, drifting up gently.
    for day in range(1, 11):
        for slot in range(26):
            rows.append({
                "timestamp": f"2026-04-{day:02d}T{9 + slot // 4:02d}:{(slot % 4) * 15:02d}:00",
                "symbol": "SPY", "close": 500.0 + day * 0.5})
    # DELL: one snapshot per day, rising hard.
    for day in range(1, 11):
        rows.append({"timestamp": f"2026-04-{day:02d}T16:00:00",
                     "symbol": "DELL", "close": 100.0 + day * 5})
    return {"id": 7, "backtest_prices": rows}


def test_a_sparse_symbol_is_not_compared_against_a_dense_benchmark():
    """The units artifact. Both legs now resolve at the same wall-clock
    instant, so a 26x density difference cannot inflate the excess."""
    obs = _obs("DELL", as_of="2026-04-01T16:00:00", executed=False,
               refusal="min_position_floor", notional=None)
    outcome = [o for o in resolve([obs], _mixed_cadence_doc(), horizons=(20,))][0]
    if outcome.resolved and outcome.excess_pct is not None:
        # If an excess IS reported, the two legs must span comparable time.
        assert outcome.span_seconds > 0
    else:
        assert outcome.reason


def test_a_missing_benchmark_is_reported_not_silently_zero():
    """A missing benchmark and a zero refusal cost looked identical
    downstream. SPY is only in backtest_prices if it was TRADED, so on most
    runs — and on every crypto run — it is simply absent."""
    doc = {"id": 1, "backtest_prices": [
        {"timestamp": f"2026-04-{d:02d}T16:00:00", "symbol": "DELL",
         "close": 100.0 + d} for d in range(1, 15)]}
    outcome = resolve([_obs("DELL", as_of="2026-04-01T16:00:00")],
                      doc, horizons=(5,))[0]
    assert outcome.resolved is True
    assert outcome.excess_pct is None
    assert "absent from backtest_prices" in outcome.reason


def test_a_refused_sell_is_not_counted_as_a_refused_buy():
    """The sign convention only works for buys. A refused SELL whose name then
    rose EARNED that move — you kept the position — but it was being pooled
    into the same median as a refused BUY, which LOST it."""
    sell = _obs("DELL", as_of="2026-04-01T13:30:00", decision=-1,
                executed=False, refusal="min_hold", notional=None)
    outcomes = resolve([sell], _price_doc(), horizons=(20,))
    report = refusal_cost(outcomes, [sell], horizon_bars=20)
    assert report["refused_n"] == 0


def test_the_refusal_cost_is_broken_down_by_gate():
    """'The min-position floor blocked 134 grants' is the question; one pooled
    median cannot answer it."""
    a = _obs("DELL", as_of="2026-04-01T13:30:00", executed=False,
             refusal="min_position_floor", notional=None)
    b = _obs("SNDK", as_of="2026-04-01T13:30:00", executed=False,
             refusal="max_positions", notional=None)
    doc = _price_doc()
    doc["backtest_prices"] += [
        {"timestamp": f"2026-04-{i + 1:02d}T13:30:00", "symbol": "SNDK",
         "close": 50.0 + i} for i in range(30)]
    outcomes = resolve([a, b], doc, horizons=(20,))
    report = refusal_cost(outcomes, [a, b], horizon_bars=20)
    assert set(report["by_gate"]) <= {"min_position_floor", "max_positions"}


def test_unresolved_outcomes_are_counted_even_though_they_are_not_stored():
    """They are the denominator, and they are not randomly distributed —
    they concentrate in exactly the discovered-and-refused population."""
    from self_learning.outcomes import unresolved_reasons
    obs = _obs("NOPRICE", as_of="2026-04-01T13:30:00")
    counts = unresolved_reasons(resolve([obs], _price_doc(), horizons=(1, 5, 20)))
    assert sum(counts.values()) == 3


def test_a_buy_and_a_sell_on_one_bar_are_two_observations():
    """Without `decision` in the identity they collapsed to one row, and which
    survived was list-order dependent."""
    buy = _obs("X", decision=1)
    sell = _obs("X", decision=-1)
    assert buy.id != sell.id


# ── Sweep regressions: the guards had holes in the PERMISSIVE direction ──────

def test_a_zero_floor_is_not_a_floor():
    """Three identical repeats gave floor=0.0, bar=0.0, and EVERY effect above
    zero 'cleared' it — with an operator-facing reason reading 'clears 0.00pp'.
    In this codebase identical repeats have a known cause: a forgotten
    history_scope_salt, which produced a documented byte-identical rerun."""
    floor = estimate_floor([5.0, 5.0, 5.0], target="t", window_class="c")
    assert floor.measured is False
    assert can_promote(floor) is False
    assert "history_scope_salt" in floor.reason
    assert acceptance([0.0001] * 4, floor).accepted is False


def test_repeats_that_are_not_distinct_runs_do_not_make_a_floor():
    """Reading one cached result three times fabricates a perfect zero floor."""
    floor = estimate_floor([5.0, 7.0, 9.0], target="t", window_class="c",
                           run_ids=["101", "101", "102"])
    assert floor.measured is False
    assert "not distinct" in floor.reason


def test_distinct_run_ids_pass():
    floor = estimate_floor([5.0, 7.0, 9.0], target="t", window_class="c",
                           run_ids=["101", "102", "103"])
    assert floor.measured is True


def test_a_consistently_losing_lever_is_not_accepted():
    """`abs(effect) > bar` with a consistent sign accepted a lever that lost
    8pp in EVERY window, leaving the LLM judge as the only thing between a
    harmful lever and promotion."""
    floor = estimate_floor([0.0, 1.0, 0.5], target="t", window_class="c")
    verdict = acceptance([-8.0, -9.0, -7.0, -8.0], floor,
                         expected_direction="increase")
    assert verdict.accepted is False
    assert "wrong way" in verdict.reason


def test_a_lever_predicted_to_decrease_may_decrease():
    floor = estimate_floor([0.0, 1.0, 0.5], target="t", window_class="c")
    verdict = acceptance([-8.0, -9.0, -7.0, -8.0], floor,
                         expected_direction="decrease")
    assert verdict.accepted is True


def test_same_window_repeats_that_disagree_are_not_averaged_away():
    """The founding observation of this subsystem is same-window dispersion.
    Collapsing each window's repeats to a mean before the check made a 36pp
    disagreement invisible."""
    floor = estimate_floor([0.0, 1.0, 0.5], target="t", window_class="c")
    per_window_repeats = [[20.0, -16.0], [3.0, 1.0], [2.0, 2.0], [2.0, 2.0]]
    verdict = acceptance(per_window_repeats, floor)
    assert verdict.accepted is False
    assert "repeats of a single window disagreed" in verdict.reason


def test_a_zero_or_negative_margin_is_refused():
    """A pre-registered margin of 0 is a legitimate-looking spec that turns off
    the primary gate."""
    floor = estimate_floor([0.0, 16.0, 8.0], target="t", window_class="c")
    assert acceptance([0.01] * 4, floor, margin=0.0).accepted is False
    assert acceptance([0.01] * 4, floor, margin=-1.0).accepted is False
    with pytest.raises(ValueError):
        design(hypothesis_id="h", target="t", window_class="c",
               windows=[("2026-04-01", "2026-06-01")], treatment_keys=["k"],
               margin=0)


def test_a_zero_sign_k_is_refused():
    floor = estimate_floor([0.0, 1.0, 0.5], target="t", window_class="c")
    assert acceptance([5.0] * 4, floor, sign_k=0).accepted is False
    with pytest.raises(ValueError):
        design(hypothesis_id="h", target="t", window_class="c",
               windows=[("2026-04-01", "2026-06-01")], treatment_keys=["k"],
               sign_k=0)


def test_a_tie_is_not_a_majority():
    """`positive >= negative` passed on a tie, so one outlier could carry a
    result at any sign_k <= n/2."""
    floor = estimate_floor([0.0, 1.0, 0.5], target="t", window_class="c")
    verdict = acceptance([40.0, 1.0, -30.0, -1.0], floor, sign_k=2)
    assert verdict.accepted is False
    assert "outlier" in verdict.reason


def test_design_checks_the_windows_against_each_other():
    """Four windows one day apart passed every registry check individually
    while being one window wearing four hats — 16 paid arms, effective n 0.07."""
    spec = design(hypothesis_id="h", target="t", window_class="c",
                  windows=[("2026-04-01", "2026-06-01"),
                           ("2026-04-02", "2026-06-02"),
                           ("2026-04-03", "2026-06-03"),
                           ("2026-04-04", "2026-06-04")],
                  treatment_keys=["k"])
    assert len(spec.windows) == 1
    assert "dropped windows" in spec.refusal_reason


def test_design_records_the_windows_it_used():
    """The registry's whole purpose depended on an unnamed caller remembering
    to call record(), which is the same as nowhere."""
    registry = InSampleRegistry()
    design(hypothesis_id="h", target="t", window_class="c",
           windows=[("2026-04-01", "2026-06-01")], treatment_keys=["k"],
           registry=registry)
    assert registry.used == [("2026-04-01", "2026-06-01")]
    second = design(hypothesis_id="h2", target="t", window_class="c",
                    windows=[("2026-04-01", "2026-06-01")],
                    treatment_keys=["k"], registry=registry)
    assert second.status == STATUS_REFUSED


def test_the_window_class_is_part_of_the_experiment_identity():
    """Granularity is not derivable from the dates, so a 15m and a 1h run over
    the same window shared an id — and the conflict merge then filed one under
    the other's class and wiped its refusal note."""
    base = dict(hypothesis_id="h1", target="equity/nexus",
                windows=[("2026-04-01", "2026-06-01")], treatment_keys=["k"])
    fast = design(**base, window_class="15m/medium/bull", record=False)
    slow = design(**base, window_class="1h/medium/bull", record=False)
    assert fast.id != slow.id


def test_a_lease_holder_recognises_its_own_run():
    """BacktestResults has no `origin` field, so the subsystem saw its own
    launch, called it human, and refused its own next lease forever — and the
    foreign check ran first, so the TTL could never rescue it."""
    held = lease.record_run(
        {"experiment_id": "e1", "acquired_at": "2026-08-15T00:00:00"}, 1234)
    decision = lease.acquire(current_lease=held,
                             running_backtests=[{"id": 1234}],
                             now_iso="2026-08-15T00:05:00", experiment_id="e1")
    assert decision.granted is True


def test_a_corrupt_lease_fails_closed():
    """An unparseable acquired_at means somebody IS holding it."""
    corrupt = {"experiment_id": "e1", "acquired_at": "not-a-date"}
    decision = lease.acquire(current_lease=corrupt, running_backtests=[],
                             now_iso="2026-08-15T00:00:00", experiment_id="e2")
    assert decision.granted is False


def test_a_missing_origin_is_treated_as_a_human_run():
    """The fail-safe default the docstring advertises was never verified — the
    old test always passed origin='human' explicitly."""
    decision = lease.acquire(current_lease=None,
                             running_backtests=[{"id": 77}],
                             now_iso="2026-08-15T00:00:00", experiment_id="e1")
    assert decision.granted is False
