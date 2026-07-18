"""Task 7A: strict trading-calendar horizon outcome evaluator.

Every registered forecast — eligible, rejected, traded or not — resolves to
an outcome from point-in-time ADJUSTED data on exact exchange sessions.
Missing evidence produces a conservative missing outcome that stays in the
denominator; corrections are new revisions; the July diagnostic sample is
hypothesis-generating and can never become a sealed holdout.
"""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark_alpha.outcomes import (
    HoldoutContaminationError,
    OutcomeEvaluator,
    StrictCalendarError,
    StrictTradingCalendar,
    assert_holdout_eligible,
    forecast_from_legacy_row,
    strict_calendar_from_live_calendar,
)
from benchmark_alpha.records import Forecast
from benchmark_alpha.types import EvidenceClass, RunOrigin

AS_OF = datetime(2026, 7, 1, 15, 0, tzinfo=timezone.utc)

# 2026-07-03 is a holiday (July 4 observed) and is NOT a session; the list is
# the exchange-authoritative truth, so a weekday absent here never counts.
SESSIONS = ("2026-06-30", "2026-07-01", "2026-07-02", "2026-07-06",
            "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10")


def _series(prices, adjusted=None, actions=None):
    out = {}
    for session, raw in prices.items():
        out[session] = {
            "close_raw": raw,
            "close_adjusted": (adjusted or {}).get(session, raw),
            "corporate_action": (actions or {}).get(session),
        }
    return out


SPY = _series({s: 500.0 + i for i, s in enumerate(SESSIONS)})


def _forecast(**kw):
    base = dict(
        producer="graph_nexus", model_version="gn-1",
        evidence_class=EvidenceClass.DIRECT, symbol="AAPL", as_of=AS_OF,
        horizon_trading_days=3, expected_excess_return=0.01,
        probability_outperform=0.6, confidence=0.5, feature_version="f1",
        data_snapshot_id="snap-1", eligibility=True, gate_reasons=(),
        revision=0, instance_id="alpaca-main", origin=RunOrigin.LIVE,
        run_id="run-1")
    base.update(kw)
    return Forecast(**base)


def _calendar():
    return StrictTradingCalendar(sessions=SESSIONS)


def test_entry_and_exit_land_on_exact_sessions_skipping_the_holiday():
    stock = _series({s: 100.0 + i for i, s in enumerate(SESSIONS)})
    outcome = OutcomeEvaluator().resolve(
        _forecast(), _calendar(), stock, SPY)
    # as_of is during 2026-07-01; entry = next session (2026-07-02); exit =
    # exactly 3 sessions later, skipping the July-3 holiday: 07-08.
    assert outcome.entry_session == "2026-07-02"
    assert outcome.exit_session == "2026-07-08"
    stock_ret = (105.0 / 102.0) - 1
    spy_ret = (505.0 / 502.0) - 1
    assert outcome.benchmark_relative_return == pytest.approx(stock_ret - spy_ret)
    assert outcome.missing_reason is None


def test_rejected_and_untraded_forecasts_still_resolve():
    stock = _series({s: 100.0 for s in SESSIONS})
    rejected = _forecast(eligibility=False, gate_reasons=("no_graph_signal",))
    outcome = OutcomeEvaluator().resolve(rejected, _calendar(), stock, SPY)
    assert outcome.forecast_id == rejected.record_id
    assert outcome.missing_reason is None  # resolved, not dropped


def test_missing_bar_or_delisting_is_a_conservative_missing_outcome():
    stock = _series({"2026-07-02": 100.0})  # no exit bar (delisted)
    outcome = OutcomeEvaluator().resolve(_forecast(), _calendar(), stock, SPY)
    assert outcome.missing_reason == "missing_exit_observation"
    assert outcome.benchmark_relative_return is None
    assert outcome.entry_session == "2026-07-02"  # stays in the denominator


def test_split_uses_adjusted_prices_never_raw_extremes():
    # 10:1 split between entry and exit: raw collapses 400 -> 41.2 (a fake
    # -89.7%), adjusted shows the true +3%.
    stock = _series(
        {"2026-07-02": 400.0, "2026-07-08": 41.2},
        adjusted={"2026-07-02": 40.0, "2026-07-08": 41.2},
        actions={"2026-07-08": "split_10_for_1"})
    outcome = OutcomeEvaluator().resolve(_forecast(), _calendar(), stock, SPY)
    stock_ret = (41.2 / 40.0) - 1
    spy_ret = (505.0 / 502.0) - 1
    assert outcome.benchmark_relative_return == pytest.approx(stock_ret - spy_ret)
    assert abs(outcome.benchmark_relative_return) < 0.10  # not a KLAC/FEMY artifact
    assert outcome.corporate_action_state == "split_10_for_1"


def test_sell_direction_semantics_are_not_inverted():
    # A negative-direction forecast on a stock that RISES stores the actual
    # positive relative return; correctness judgment is the consumer's job.
    stock = _series({s: 100.0 + 2 * i for i, s in enumerate(SESSIONS)})
    bearish = _forecast(expected_excess_return=-0.02)
    outcome = OutcomeEvaluator().resolve(bearish, _calendar(), stock, SPY)
    assert outcome.benchmark_relative_return > 0


def test_corrected_data_creates_a_new_revision_not_a_mutation():
    stock = _series({s: 100.0 + i for i, s in enumerate(SESSIONS)})
    ev = OutcomeEvaluator()
    o0 = ev.resolve(_forecast(), _calendar(), stock, SPY, data_revision=0)
    corrected = _series({s: 100.0 + i for i, s in enumerate(SESSIONS)},
                        adjusted={"2026-07-08": 104.5})
    o1 = ev.resolve(_forecast(), _calendar(), corrected, SPY, data_revision=1)
    assert o1.record_id != o0.record_id
    assert o0.benchmark_relative_return != o1.benchmark_relative_return


def test_legacy_unknown_intent_is_rejected_at_the_boundary():
    with pytest.raises(ValueError):
        forecast_from_legacy_row({"action_intent": "unknown", "symbol": "AAPL"})


def test_hypothesis_sample_cannot_be_registered_as_sealed_holdout():
    stock = _series({s: 100.0 + i for i, s in enumerate(SESSIONS)})
    ev = OutcomeEvaluator()
    diagnostic = ev.resolve(_forecast(), _calendar(), stock, SPY,
                            hypothesis_generating=True)
    assert diagnostic.hypothesis_generating is True
    with pytest.raises(HoldoutContaminationError):
        assert_holdout_eligible([diagnostic])
    clean = ev.resolve(_forecast(revision=1), _calendar(), stock, SPY)
    assert_holdout_eligible([clean])  # must not raise


def test_strict_calendar_refuses_the_weekday_fallback():
    import live_calendar
    original = live_calendar._HAS_LIB
    try:
        live_calendar._HAS_LIB = False
        with pytest.raises(StrictCalendarError):
            strict_calendar_from_live_calendar("2026-06-01", "2026-07-31")
    finally:
        live_calendar._HAS_LIB = original


def test_calendar_rejects_unknown_sessions_and_out_of_range_exit():
    cal = _calendar()
    with pytest.raises(ValueError):
        cal.session_index("2026-07-03")  # holiday is not a session
    late = _forecast(as_of=datetime(2026, 7, 9, 15, 0, tzinfo=timezone.utc))
    stock = _series({s: 100.0 for s in SESSIONS})
    outcome = OutcomeEvaluator().resolve(late, _calendar(), stock, SPY)
    assert outcome.missing_reason == "exit_session_beyond_calendar"
