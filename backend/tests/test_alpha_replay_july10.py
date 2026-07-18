"""Task 6 / Phase 1 verification gate: July 10 replay.

With Alpaca stubbed, prove: every position receives CHANGING marks across
refreshes, drawdown becomes nonzero on the real equity path, contained legacy
orders are blocked while the typed reduce-only path stays available, and a
restart does not reset the high-water mark.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark_alpha.emergency import ReduceOnlyEmergencyExecutor
from benchmark_alpha.rethink_store import AlphaRethinkStore
from benchmark_alpha.risk import (
    RiskLevel, RiskState, evaluate_mark_health, legacy_live_order_block,
    update_risk_state,
)
from market_marks import MarkQuality, MarkSource, MarketMark, MarketMarkBook

T0 = datetime(2026, 7, 10, 14, 0, tzinfo=timezone.utc)

# July 10 morning marks vs afternoon broker closes (8 held positions).
MORNING = {"MRNA": 76.51, "OKTA": 92.10, "S": 17.80, "QLYS": 141.20,
           "KNX": 44.90, "CNC": 61.20, "BX": 131.40, "EWTX": 22.10}
AFTERNOON = {"MRNA": 68.26, "OKTA": 90.05, "S": 17.10, "QLYS": 138.75,
             "KNX": 43.80, "CNC": 59.90, "BX": 129.10, "EWTX": 21.55}


def _broker_mark(sym, price, ts):
    return MarketMark(symbol=sym, price=price, bid=None, ask=None,
                      bid_size=None, ask_size=None, observed_at=ts,
                      received_at=ts, source=MarkSource.BROKER_POSITION,
                      feed="broker", quality=MarkQuality.BROKER_DERIVED,
                      session="regular")


def test_all_eight_positions_receive_changing_marks_and_fill_never_survives():
    book = MarketMarkBook()
    # Morning fills seed the cache (the legacy failure mode).
    for sym, price in MORNING.items():
        fill_ts = T0 - timedelta(hours=3)
        book.update(MarketMark(symbol=sym, price=price * 1.06, bid=None,
                               ask=None, bid_size=None, ask_size=None,
                               observed_at=fill_ts, received_at=fill_ts,
                               source=MarkSource.FILL, feed="execution",
                               quality=MarkQuality.EXECUTION_ONLY,
                               session="regular"))
    for sym, price in MORNING.items():
        assert book.update(_broker_mark(sym, price, T0)) is True
    for sym, price in AFTERNOON.items():
        assert book.update(_broker_mark(sym, price, T0 + timedelta(hours=5))) is True
    for sym, price in AFTERNOON.items():
        mark = book.get(sym)
        assert mark.price == price
        assert mark.source is MarkSource.BROKER_POSITION
    assert book.get("MRNA").price == 68.26


def test_mark_health_and_drawdown_follow_the_broker_equity_path():
    book = MarketMarkBook()
    now = T0 + timedelta(hours=5)
    for sym, price in AFTERNOON.items():
        book.update(_broker_mark(sym, price, now))
    health = evaluate_mark_health(list(AFTERNOON), book.snapshot(), now)
    assert health.ok is True

    s = RiskState.initial(6113.98, T0)
    end = update_risk_state(s, 5949.05, [], now)
    assert end.drawdown_magnitude > 0
    assert end.drawdown_magnitude == pytest.approx(1 - 5949.05 / 6113.98)
    assert end.level is RiskLevel.NORMAL  # 2.7% — below SOFT


def test_contained_legacy_orders_blocked_but_reduce_only_path_available():
    containment = {"legacy_order_authority_disabled": True}
    assert legacy_live_order_block("alpaca-main", "buy", containment)
    assert legacy_live_order_block("alpaca-main", "sell", containment)

    submitted = []
    executor = ReduceOnlyEmergencyExecutor(
        read_positions=lambda: {"MRNA": 4.0},
        submit_reduce=lambda symbol, qty, client_order_id: submitted.append(
            (symbol, qty, client_order_id)) or "broker-1",
        instance_id="alpaca-main",
    )
    actions = executor.reduce_to_targets("july10-kill", {"MRNA": 0.0})
    assert len(actions) == 1
    assert submitted[0][:2] == ("MRNA", 4.0)


def test_restart_does_not_reset_the_high_water_mark():
    class Backend:
        def __init__(self):
            self.states = {}

        def compare_and_swap_state(self, key, expected_version, doc, *, durability):
            prior = self.states.get(key)
            version = 0 if prior is None else prior["version"]
            if version != expected_version:
                return None
            self.states[key] = doc
            return doc

        def get_state_row(self, key):
            return self.states.get(key)

    backend = Backend()
    store = AlphaRethinkStore.for_backend(backend)
    peak_state = update_risk_state(RiskState.initial(6000.0, T0), 6243.15, [], T0)
    store.put_state("risk:alpaca-main", {
        "peak_adjusted_equity": peak_state.peak_adjusted_equity,
        "cumulative_external_flow": peak_state.cumulative_external_flow,
    }, expected_version=0)

    # Restart: reload and reconcile against a LOWER current broker equity.
    restored = AlphaRethinkStore.for_backend(backend).get_state("risk:alpaca-main")
    resumed = RiskState(
        peak_adjusted_equity=restored.payload["peak_adjusted_equity"],
        last_raw_equity=5949.05,
        cumulative_external_flow=restored.payload["cumulative_external_flow"],
        drawdown_magnitude=0.0, level=RiskLevel.NORMAL, updated_at=T0,
    )
    after = update_risk_state(resumed, 5949.05, [], T0 + timedelta(days=1))
    assert after.peak_adjusted_equity == 6243.15  # not reset to restart equity
    assert after.drawdown_magnitude == pytest.approx(1 - 5949.05 / 6243.15)
