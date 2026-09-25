"""FW-lo-I5, snapshot part (swing-port final review, live-orders slice
Important 5).

After an assignment on the margin paper account Alpaca's cash goes negative.
DependencySnapshot refuses available_cash < 0, so every order's snapshot
raised and the service answered dependency.snapshot.unavailable: the Friday
monitor's buy_to_close of another in-the-money put became "AUTO-CLOSE FAILED"
while the put rolled into a second assignment, and swing exits on the same
account failed the same way. With cash above zero, cash.insufficient still
refused a buy_to_close whose premium exceeded it.

Now a reduce-only order on a document with an enabled swing or wheel lane
builds its snapshot with negative cash read as zero, and a buy_to_close skips
cash.insufficient (Alpaca enforces buying power). Opening orders still fail
closed. EB's document enables neither lane, so its snapshot still raises on
negative cash exactly as before."""
import datetime as dtm
import json
import threading
from decimal import Decimal
from types import SimpleNamespace

import pytest

from broker_adapters.base import OptionPositionDTO
from live_orders import (
    InMemoryLifecycleBackend,
    LiveOrderService,
    OrderIntent,
    OrderLifecycleStore,
    OrderSide,
    OrderSource,
    UnifiedOrderGate,
)
from market_marks import MarketMark, MarkQuality, MarkSource
from swing_broker_harness import extract
from swing_live_fixtures import buy_to_close, option_intent, option_snapshot

RTH = dtm.datetime(2026, 10, 9, 19, 40, tzinfo=dtm.timezone.utc)   # Fri 15:40 ET
OCC = "QRS261009P00050000"
SWING = [{"strategy": "strategy_swing", "config": {"strategy_swing_enabled": True}}]
WHEEL = [{"strategy": "strategy_wheel", "config": {"strategy_wheel_enabled": True}}]
DOC_200 = [{"strategy": "strategy_eb", "weight": 1.0,
            "config": {"strategy_eb_enabled": True}},
           {"strategy": "graph_nexus_analysis", "weight": 0.0, "config": {}}]


class _Frozen(dtm.datetime):
    @classmethod
    def now(cls, tz=None):
        return RTH if tz is not None else RTH.replace(tzinfo=None)


CLOCK = SimpleNamespace(datetime=_Frozen, timezone=dtm.timezone,
                        timedelta=dtm.timedelta, date=dtm.date)
AT = _Frozen(2026, 10, 9, 19, 40, tzinfo=dtm.timezone.utc)


def _state():
    state = {n: "healthy" for n in ("kill_switch", "cash", "positions",
                                    "persistence", "risk_state", "watchdog",
                                    "calendar")}
    state.update({f"{n}_at": AT for n in ("kill_switch", "cash", "positions",
                                          "persistence", "risk_state",
                                          "watchdog", "calendar")})
    state.update({"risk_snapshot_id": "risk-1", "market_open": True})
    return state


def _ns(strategies):
    helpers = extract(("_lane_enabled", "_truthy", "_merged_strategy_settings"),
                      assigns=("_LANE_ENABLE_FLAGS",))
    namespace = {
        "datetime": CLOCK, "_live_order_dependency_lock": threading.Lock(),
        "_live_order_dependency_state": _state(),
        "_live_stock_order_service": None, "_live_risk_state": None,
        "instance_id": "swing-paper", "MODE_LIVE": "live", "mode": "live",
        "live_broker_type": "alpaca", "live_brokerage_id": "acct-1",
        "_cached_strategies": strategies,
        "_lane_enabled": helpers["_lane_enabled"],
    }
    ns = extract(("_live_option_dependency_snapshot",
                  "_pending_sell_to_open_collateral",
                  "_open_order_idempotency_keys",
                  "_live_order_dependency_snapshot"),
                 assigns=("_live_option_quotes",), namespace=namespace,
                 check=("_live_option_dependency_snapshot",
                        "_live_order_dependency_snapshot"))
    return ns


# --- options: the Friday monitor's buy_to_close -------------------------------

def _option_world(cash, strategies=WHEEL):
    ns = _ns(strategies)
    adapter = SimpleNamespace(
        _option_positions={OCC: OptionPositionDTO(
            OCC, "QRS", "put", 50.0, "2026-10-09", -1, 1.0, 4.0, -400.0, -300.0)},
        _option_positions_complete=True, _cash=cash, _account_equity=20000.0,
        _instance_id="swing-paper", _positions_stale_since=None)
    sent = []
    service = LiveOrderService(
        account_id="acct-1", instance_id="swing-paper",
        snapshot_provider=lambda i: ns["_live_option_dependency_snapshot"](
            adapter, i, now_utc=RTH),
        transport=lambda **kw: sent.append(kw) or SimpleNamespace(
            status="accepted", broker_order_id="b-1", id="b-1", filled_qty=0,
            filled_avg_price=None),
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()))
    ns["_live_stock_order_service"] = service
    ns["_live_option_quotes"][OCC] = {"price": Decimal("4.00"), "quote_at": RTH,
                                      "fetched_at": RTH}
    return service, sent


def _btc():
    return OrderIntent(
        account_id="acct-1", instance_id="swing-paper",
        source=OrderSource.RISK_EXIT, reason="wheel_btc_itm", symbol=OCC,
        side=OrderSide.BUY, quantity=Decimal(1), reduce_only=True,
        decision_at=RTH, quote_at=RTH, risk_snapshot_id="risk-1",
        order_type="market", tif="day", asset_class="us_option",
        position_intent="buy_to_close", contract_multiplier=100,
        underlying="QRS", option_type="put", strike=Decimal("50"),
        expiry="2026-10-09")


def test_a_buy_to_close_goes_out_with_negative_cash():
    """The reviewer's probe: cash -$2,500 after an assignment."""
    service, sent = _option_world(-2500.0)
    out = service.submit(_btc())
    assert out.accepted, out.decision.reason_codes
    assert len(sent) == 1 and sent[0]["position_intent"] == "buy_to_close"


def test_a_buy_to_close_whose_premium_exceeds_cash_goes_out():
    """Cash $300 against a $400 close: the broker decides."""
    service, sent = _option_world(300.0)
    out = service.submit(_btc())
    assert out.accepted, out.decision.reason_codes


def test_a_sell_to_open_with_negative_cash_still_fails_closed():
    service, sent = _option_world(-2500.0)
    put = OrderIntent(
        account_id="acct-1", instance_id="swing-paper",
        source=OrderSource.STRATEGY, reason="wheel_sto_put",
        symbol="ABC261016P00020000", side=OrderSide.SELL, quantity=Decimal(1),
        reduce_only=False, decision_at=RTH, quote_at=RTH,
        risk_snapshot_id="risk-1", order_type="limit",
        limit_price=Decimal("0.5"), tif="day", asset_class="us_option",
        position_intent="sell_to_open", contract_multiplier=100,
        underlying="ABC", option_type="put", strike=Decimal("20"),
        expiry="2026-10-16")
    out = service.submit(put)
    assert not out.decision.allowed
    assert out.decision.reason_codes == ("dependency.snapshot.unavailable",)
    assert sent == []


def test_the_gate_skips_cash_for_a_buy_to_close_only():
    gate = UnifiedOrderGate()
    close = buy_to_close(quantity=Decimal("1"))
    snap = option_snapshot(close, quote_price=Decimal("4.00"),
                           position_quantity=Decimal("-1"),
                           available_cash=Decimal("100"))
    decision = gate.evaluate(close, snap)
    assert decision.allowed, decision.reason_codes
    opening = option_intent(side=OrderSide.BUY, position_intent="buy_to_open",
                            option_type="call", limit_price=Decimal("4.00"))
    decision = gate.evaluate(opening, option_snapshot(
        opening, quote_price=Decimal("4.00"), available_cash=Decimal("100")))
    assert "cash.insufficient" in decision.reason_codes


# --- equities: a swing exit on the same account --------------------------------

def _mark(symbol="AAPL"):
    return MarketMark(symbol=symbol, price=100.0, bid=99.99, ask=100.01,
                      bid_size=100, ask_size=100,
                      observed_at=RTH - dtm.timedelta(seconds=1), received_at=RTH,
                      source=MarkSource.REST_QUOTE, feed="sip",
                      quality=MarkQuality.CONSOLIDATED, session="regular")


def _equity_adapter(cash):
    mark = _mark()
    return SimpleNamespace(_positions={"AAPL": 12.0}, _cash=cash,
                           _instance_id="swing-paper", _account_id="acct-1",
                           _positions_stale_since=None,
                           _market_marks=SimpleNamespace(get=lambda s: mark))


def _stock(side, reduce_only):
    return OrderIntent(
        account_id="acct-1", instance_id="swing-paper",
        source=OrderSource.STRATEGY, reason="swing_stop_exit", symbol="AAPL",
        side=side, quantity=Decimal("12"), reduce_only=reduce_only,
        decision_at=RTH, quote_at=RTH - dtm.timedelta(seconds=1),
        risk_snapshot_id="risk-1", order_type="market", tif="day",
        reference_price=Decimal("100"))


@pytest.mark.parametrize("strategies", [SWING, WHEEL, SWING + WHEEL])
def test_a_swing_or_wheel_exit_builds_its_snapshot_with_negative_cash(strategies):
    ns = _ns(strategies)
    ns["_live_stock_order_service"] = SimpleNamespace()      # armed
    snap = ns["_live_order_dependency_snapshot"](
        _equity_adapter(-2500.0), _stock(OrderSide.SELL, True))
    assert snap.available_cash == Decimal("0")
    decision = UnifiedOrderGate().evaluate(_stock(OrderSide.SELL, True), snap)
    assert decision.allowed, decision.reason_codes


@pytest.mark.parametrize("strategies", [DOC_200, None, []])
def test_eb_still_refuses_to_build_a_snapshot_on_negative_cash(strategies):
    """EB pin: doc 200's lanes (strategy_eb + graph_nexus_analysis at weight
    0, read from Postgres) and a document not loaded yet raise exactly as the
    old provider did."""
    ns = _ns(strategies)
    with pytest.raises(ValueError, match="available_cash must be >= 0"):
        ns["_live_order_dependency_snapshot"](
            _equity_adapter(-2500.0), _stock(OrderSide.SELL, True))


def test_a_swing_buy_with_negative_cash_still_fails_closed():
    ns = _ns(SWING)
    with pytest.raises(ValueError, match="available_cash must be >= 0"):
        ns["_live_order_dependency_snapshot"](
            _equity_adapter(-2500.0), _stock(OrderSide.BUY, False))


def test_positive_cash_reads_through_unchanged():
    for strategies in (SWING, DOC_200):
        ns = _ns(strategies)
        snap = ns["_live_order_dependency_snapshot"](
            _equity_adapter(6041.43), _stock(OrderSide.SELL, True))
        assert snap.available_cash == Decimal("6041.43")
