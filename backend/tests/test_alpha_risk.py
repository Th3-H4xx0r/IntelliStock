"""Task 6: flow-adjusted persistent drawdown state + mark health + legacy block."""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark_alpha.risk import (
    RiskLevel,
    RiskState,
    evaluate_mark_health,
    legacy_live_order_block,
    reset_peak,
    update_risk_state,
)

TS = datetime(2026, 7, 11, tzinfo=timezone.utc)


def test_drawdown_state_preserves_peak_and_crosses_all_thresholds():
    ts = datetime(2026, 7, 11, tzinfo=timezone.utc)
    s = RiskState.initial(10000.0, ts)
    assert update_risk_state(s, 9200.0, [], ts).level is RiskLevel.SOFT
    assert update_risk_state(s, 8800.0, [], ts).level is RiskLevel.HARD
    killed = update_risk_state(s, 8500.0, [], ts)
    assert killed.level is RiskLevel.KILL
    assert killed.peak_adjusted_equity == 10000.0


def test_normal_below_soft_threshold_and_new_high_raises_peak():
    s = RiskState.initial(10000.0, TS)
    ok = update_risk_state(s, 9300.0, [], TS)
    assert ok.level is RiskLevel.NORMAL
    high = update_risk_state(s, 10500.0, [], TS)
    assert high.peak_adjusted_equity == 10500.0
    assert high.drawdown_magnitude == 0.0


def test_deposit_adjusts_capital_without_performance_high_or_drawdown():
    s = RiskState.initial(2000.0, TS)
    after = update_risk_state(
        s, 6000.0, [{"type": "deposit", "amount": 4000.0}], TS)
    assert after.level is RiskLevel.NORMAL
    assert after.drawdown_magnitude == 0.0
    assert after.peak_adjusted_equity == 6000.0  # capital adjustment, not a high
    assert after.cumulative_external_flow == 4000.0


def test_withdrawal_creates_no_false_drawdown():
    s = RiskState.initial(10000.0, TS)
    after = update_risk_state(
        s, 8000.0, [{"type": "withdrawal", "amount": 2000.0}], TS)
    assert after.drawdown_magnitude == 0.0
    assert after.level is RiskLevel.NORMAL
    assert after.cumulative_external_flow == -2000.0


def test_dividends_and_fees_remain_economic_return():
    s = RiskState.initial(10000.0, TS)
    after = update_risk_state(
        s, 10050.0, [{"type": "dividend", "amount": 50.0}], TS)
    assert after.peak_adjusted_equity == 10050.0  # a real performance high
    fee = update_risk_state(s, 9995.0, [{"type": "fee", "amount": 5.0}], TS)
    assert fee.drawdown_magnitude == pytest.approx(0.0005)


def test_split_preserves_value_and_unknown_flow_quarantines_peak():
    s = RiskState.initial(10000.0, TS)
    split = update_risk_state(s, 10000.0, [{"type": "split", "amount": 0.0}], TS)
    assert split.peak_adjusted_equity == 10000.0
    mystery = update_risk_state(
        s, 15000.0, [{"type": "unknown", "amount": 5000.0}], TS)
    assert mystery.peak_adjusted_equity == 10000.0  # quarantined: no new peak
    assert mystery.quarantined_flow == 5000.0


def test_july18_fixture_deposit_creates_no_high_and_drawdown_is_positive_magnitude():
    s = RiskState.initial(2000.0, datetime(2026, 6, 4, tzinfo=timezone.utc))
    s = update_risk_state(
        s, 6000.0, [{"type": "deposit", "amount": 4000.0}],
        datetime(2026, 6, 8, tzinfo=timezone.utc))
    assert s.drawdown_magnitude == 0.0
    s = update_risk_state(s, 6243.15, [], datetime(2026, 6, 15, tzinfo=timezone.utc))
    assert s.peak_adjusted_equity == 6243.15
    s = update_risk_state(s, 5879.43, [], datetime(2026, 7, 13, tzinfo=timezone.utc))
    assert s.drawdown_magnitude == pytest.approx(0.058259, abs=1e-6)
    assert s.drawdown_magnitude > 0
    assert s.peak_adjusted_equity == 6243.15


def test_peak_lowering_requires_explicit_operator_reset_record():
    s = RiskState.initial(10000.0, TS)
    new_state, event = reset_peak(s, 9000.0, reason="account restructure",
                                  actor="operator@example", observed_at=TS)
    assert new_state.peak_adjusted_equity == 9000.0
    assert event["old_peak"] == 10000.0
    assert event["new_peak"] == 9000.0
    assert event["reason"] == "account restructure"
    assert event["actor"] == "operator@example"


def test_legacy_live_order_block_blocks_every_side_when_contained():
    containment = {"legacy_order_authority_disabled": True}
    for side in ("buy", "sell"):
        reason = legacy_live_order_block("alpaca-main", side, containment)
        assert reason is not None and "legacy" in reason.lower()
    assert legacy_live_order_block("alpaca-main", "buy", {}) is None
    assert legacy_live_order_block(
        "alpaca-main", "buy", {"legacy_order_authority_disabled": False}) is None
    # Malformed containment state fails closed on a live instance.
    assert legacy_live_order_block("alpaca-main", "buy", None) is not None


def test_evaluate_mark_health_classifies_fresh_fallback_stale_missing():
    from market_marks import MarkQuality, MarkSource, MarketMark
    now = TS

    def broker_mark(sym, age_s):
        t = now - timedelta(seconds=age_s)
        return MarketMark(symbol=sym, price=10.0, bid=None, ask=None,
                          bid_size=None, ask_size=None, observed_at=t,
                          received_at=t, source=MarkSource.BROKER_POSITION,
                          feed="broker", quality=MarkQuality.BROKER_DERIVED,
                          session="regular")

    marks = {"FRESH": broker_mark("FRESH", 30),
             "FALLBACK": broker_mark("FALLBACK", 90),
             "STALE": broker_mark("STALE", 500)}
    health = evaluate_mark_health(
        ["FRESH", "FALLBACK", "STALE", "MISSING"], marks, now)
    assert health.ok is False
    by_symbol = {e["symbol"]: e for e in health.entries}
    assert by_symbol["FRESH"]["status"] == "fresh"
    assert by_symbol["FALLBACK"]["status"] == "fallback"
    assert by_symbol["STALE"]["status"] == "stale"
    assert by_symbol["MISSING"]["status"] == "missing"
    assert by_symbol["STALE"]["age_seconds"] == 500.0
    all_fresh = evaluate_mark_health(["FRESH"], marks, now)
    assert all_fresh.ok is True
