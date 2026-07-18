"""Task 9: inception truth, flow-aware TWR lenses, activity ingestion."""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark_alpha.benchmark import (
    InceptionState,
    ingest_activities,
    reconcile_ledger,
    time_weighted_return,
    update_inception_state,
)

T0 = datetime(2026, 6, 4, tzinfo=timezone.utc)


def test_restart_equity_never_overwrites_inception_or_high_water():
    state = InceptionState(inception_equity=6000.0, inception_at=T0,
                           high_water_equity=6243.15, source="first_full_funding")
    after_restart = update_inception_state(state, current_equity=5949.05)
    assert after_restart.inception_equity == 6000.0
    assert after_restart.high_water_equity == 6243.15
    new_high = update_inception_state(state, current_equity=6300.0)
    assert new_high.high_water_equity == 6300.0
    assert new_high.inception_equity == 6000.0
    with pytest.raises(ValueError):
        update_inception_state(None, current_equity=0.0)


def test_first_funding_twr_removes_the_june8_deposit():
    # Jun 4: $2,000 in. Jun 8 (pre-flow value 2,010): +$4,000 deposit.
    # Jul 17 end value 6,010. Naive return would be +200.5%; TWR links
    # sub-periods around the flow instead.
    values = [("2026-06-04", 2000.0), ("2026-06-08", 6010.0),
              ("2026-07-17", 6010.0)]
    flows = [("2026-06-08", 4000.0)]
    twr = time_weighted_return(values, flows)
    expected = (2010.0 / 2000.0) * (6010.0 / 6010.0) - 1
    assert twr == pytest.approx(expected, abs=1e-9)


def test_final_funded_window_reproduces_the_july18_numbers():
    values = [("2026-06-08", 6000.0), ("2026-06-15", 6243.15),
              ("2026-07-13", 5879.43), ("2026-07-17", 5979.38)]
    twr = time_weighted_return(values, [])
    assert round(twr * 100, 4) == -0.3437
    peak, trough = 6243.15, 5879.43
    assert round(1 - trough / peak, 6) == pytest.approx(0.058259, abs=1e-6)


def test_activity_ingestion_is_idempotent_by_activity_id():
    page1 = [{"id": "a1", "activity_type": "FILL", "net_amount": -100.0},
             {"id": "a2", "activity_type": "CSD", "net_amount": 4000.0}]
    page2 = [{"id": "a2", "activity_type": "CSD", "net_amount": 4000.0},
             {"id": "a3", "activity_type": "DIV", "net_amount": 7.91}]
    ledger = ingest_activities([page1, page2])
    assert set(ledger) == {"a1", "a2", "a3"}
    again = ingest_activities([page1, page2, page1])
    assert set(again) == {"a1", "a2", "a3"}  # no double counting


def test_ledger_reconciles_the_july18_economics_within_5_cents():
    result = reconcile_ledger(
        fifo_realized_pnl=-71.3420, unrealized_pnl=43.3449,
        dividends=7.91, fees=0.52, account_pnl=-20.62)
    assert result["residual"] == pytest.approx(-0.0129, abs=1e-4)
    assert result["reconciled"] is True
    bad = reconcile_ledger(fifo_realized_pnl=-71.34, unrealized_pnl=43.34,
                           dividends=7.91, fees=0.52, account_pnl=-25.00)
    assert bad["reconciled"] is False


def test_backtest_summary_merges_benchmark_fields_without_replacing_pnl():
    from backtest_summary import compute_backtest_summary
    snapshots = [{"value": 100.0}, {"value": 102.0}, {"value": 101.0}]
    base = compute_backtest_summary(None, snapshots, 100.0)
    assert base["pnl"] == pytest.approx(1.0)
    assert "benchmark_return" not in base or base["benchmark_return"] is None
    with_benchmark = compute_backtest_summary(
        None, snapshots, 100.0, benchmark_values=[100.0, 101.0, 101.0])
    assert with_benchmark["pnl"] == pytest.approx(1.0)  # merged, not replaced
    assert with_benchmark["benchmark_return"] == pytest.approx(0.01)
    assert with_benchmark["active_return"] == pytest.approx(0.0)
    assert with_benchmark["max_drawdown_magnitude"] >= 0
    assert "information_ratio" in with_benchmark


def test_live_broker_fetch_inception_fallback_is_removed():
    from live_broker_fetch import resolve_inception
    initial, unavailable = resolve_inception(
        [{"value": 6000.0}, {"value": 5979.38}])
    assert initial == 6000.0 and unavailable is False
    initial, unavailable = resolve_inception([])
    assert initial is None
    assert unavailable is True  # NEVER current equity; P&L must not read 0
