"""Plan B Task 20: an operator decision, then plan A-live's approval handler,
with the REAL swing_trader.approvals and signals_store over the FakeStore.
One approval places exactly one order (spec §7; interfaces §7; Review Focus 4).
Runs after plan A-live Task 15, whose handler and harness it exercises."""
import datetime as datetime_module
import threading
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from broker_adapters.base import OptionContractDTO, OptionSnapshotDTO
from live_orders import GateDecision, OrderSide, OrderSource
from live_orders.service import OrderSubmission
from swing_broker_harness import extract
from swing_live_fixtures import RTH
from swing_trader import approvals, notify, signals_store

IID = "instance-1"
LANES = [{"strategy": "strategy_swing", "config": {"strategy_swing_enabled": True}},
         {"strategy": "strategy_wheel", "config": {"strategy_wheel_enabled": True}}]
NOW_ISO = RTH.isoformat()
LIVE = 100.5
EXPIRY = "2026-10-16"      # next_friday(2026-10-05): the 10-09 Friday is under 7 days out


def put(strike, expiry=EXPIRY):
    return f"APH{expiry[2:4]}{expiry[5:7]}{expiry[8:10]}P{int(strike * 1000):08d}"


class Service:
    account_id = "acct-1"
    instance_id = IID

    def __init__(self):
        self.intents = []

    def enqueue(self, intent, *, snapshot_overlay=None):
        self.intents.append(intent)
        decision = GateDecision(allowed=True, approved_quantity=intent.quantity,
                                reason_codes=(), idempotency_key=intent.idempotency_key)
        return OrderSubmission(decision=decision, reference=NS(broker_order_id="b-1"))


class Adapter:
    """What the handler (A-live) and build_approved_order (plan B) read."""
    _account_equity = 60_000.0

    def __init__(self, option_positions=(), open_orders=()):
        self._market_marks = NS(get=lambda symbol: NS(price=LIVE, observed_at=RTH))
        self.option_positions, self.open_orders = list(option_positions), list(open_orders)

    def fetch_rest_quote_marks(self, symbols):
        return tuple(symbols)

    def get_latest_trades(self, symbols):
        return {}

    def list_option_positions(self):
        return list(self.option_positions)

    def list_open_orders(self, limit=200):
        return list(self.open_orders)

    def get_account_options(self):
        return {"cash": 60_000.0, "equity": 60_000.0}

    def get_option_contracts(self, underlying, *, option_type=None, expiration_gte=None,
                             expiration_lte=None, strike_gte=None, strike_lte=None):
        return [OptionContractDTO(put(k), "APH", "put", k, EXPIRY, 100, 1.0)
                for k in (95.0, 96.0, 97.0)]

    def get_option_snapshots(self, contracts):
        return {c: OptionSnapshotDTO(c, 2.0, 2.2, 2.1, 0.3,
                                     -0.25 if c == put(96.0) else -0.10,
                                     None, None, None, RTH.isoformat())
                for c in contracts}


def handler():
    return extract(
        ("_execute_swing_approval", "_lane_config", "_approval_live_price",
         "_build_bracket_intent", "_build_option_intent",
         "_refresh_option_quote", "_truthy", "_merged_strategy_settings"),
        assigns=("_live_option_quotes", "_LANE_ENABLE_FLAGS"),
        namespace={"datetime": datetime_module,
                   "_live_order_dependency_lock": threading.Lock(),
                   "_live_order_dependency_state": {"risk_snapshot_id": "risk-9"},
                   # FW-lo-I1's control re-read, stubbed; its own tests are in
                   # test_swing_approval_controls.py.
                   "_approval_control_overlay":
                       lambda adapter, *, instance_key, now_utc=None: {}},
        check=("_execute_swing_approval", "_lane_config",
               "_approval_live_price"))["_execute_swing_approval"]


@pytest.fixture
def notices(monkeypatch):
    """What the handler's REAL swing_approval_failed sender hands the outbox,
    recorded at notify._sink (the real sink reaches for Discord and push,
    about 2 s a call). Stubbing the sink, not the sender, runs the sender's
    real signature: a handler call that drifts from it raises inside the
    handler's notice helper, no notice reaches the sink, and the test fails
    (G8b minor 3)."""
    sent = []
    monkeypatch.setattr(notify, "_sink", lambda **kwargs: sent.append(kwargs))
    return sent


@pytest.fixture
def signals(store, monkeypatch, notices):
    monkeypatch.setattr(signals_store, "store", store)
    return store


def pending(lane="swing", symbol="AAPL", **proposal):
    doc = signals_store.new_signal(
        instance_id=IID, lane=lane, symbol=symbol, session="2026-09-28", score=62,
        recommendation="review", reasoning="r", key_risks=[], size_adjustment=1.0,
        proposal=proposal or {"entry": 98.0, "stop": 92.12, "target": 106.82, "shares": 76},
        status="pending")
    signals_store.insert_signal(doc)
    return doc["id"]


def operator(signal_id, decision):
    """What the API route does (Task 21): decide, then compare-and-swap."""
    current = signals_store.get_signal(signal_id)
    decided = approvals.decide(current, decision, "pranav", None, NOW_ISO)
    if not signals_store.cas_signal(signal_id, expect_status="pending", doc=decided):
        raise AssertionError("lost the race")          # the route answers 409 (Task 21)
    return decided


def command(service, adapter, signal_id):
    return handler()(adapter, {"source": "swing_approval", "signal_id": signal_id},
                     service, cached_strategies=LANES, now_utc=RTH)


def test_a_submitted_signal_is_never_submitted_twice(signals, notices):
    sid = pending()
    operator(sid, "approve")
    with pytest.raises(approvals.SignalConflict):
        operator(sid, "approve")                      # the second click
    service = Service()
    ok, error, result = command(service, Adapter(), sid)
    assert ok is True and error == ""
    (intent,) = service.intents
    # ST api_approve: shares and legs recomputed at the live price.
    assert (intent.side, intent.quantity, intent.order_class, intent.source) == (
        OrderSide.BUY, Decimal("74"), "bracket", OrderSource.MANUAL)
    assert intent.stop_loss_price == Decimal(str(round(LIVE * 0.94, 2)))
    assert intent.take_profit_price == Decimal(str(round(LIVE * 1.09, 2)))
    row = signals_store.get_signal(sid)
    assert (row["status"], row["order_client_id"]) == ("submitted", intent.idempotency_key)
    assert row["decided_by"] == "pranav"
    # The command delivered again (at-least-once): refused, nothing new placed.
    again = command(service, Adapter(), sid)
    assert again[0] is False and "not approved" in again[1]
    assert len(service.intents) == 1
    with pytest.raises(approvals.SignalConflict):
        operator(sid, "reject")
    assert notices == []                  # a redelivery is not a failure to report


def test_approve_half_halves_the_rebuilt_share_count(signals):
    sid = pending()
    operator(sid, "approve_half")
    service = Service()
    assert command(service, Adapter(), sid)[0] is True
    assert service.intents[0].quantity == Decimal("37")


def test_a_rejected_signal_is_never_placed(signals):
    sid = pending()
    operator(sid, "reject")
    service = Service()
    ok, error, _result = command(service, Adapter(), sid)
    assert ok is False and "not approved" in error and service.intents == []
    assert signals_store.get_signal(sid)["status"] == "rejected"


def test_a_wheel_approval_sells_this_weeks_put_not_the_stale_one(signals):
    # fix 2: reviewed a week late, the stored 10-09 expiry is gone.
    sid = pending(lane="wheel", symbol="APH", contract=put(96.0, "2026-10-09"),
                  strike=96.0, expiry="2026-10-09", qty=1, limit_price=None,
                  premium_est=1.5, delta=None)
    operator(sid, "approve")
    service = Service()
    ok, error, _result = command(service, Adapter(), sid)
    assert ok is True and error == ""
    (intent,) = service.intents
    assert (intent.symbol, intent.position_intent, intent.quantity, intent.limit_price,
            intent.source, intent.contract_multiplier) == (
        put(96.0), "sell_to_open", Decimal("1"), Decimal("1.9"), OrderSource.MANUAL, 100)
    assert signals_store.get_signal(sid)["status"] == "submitted"


def test_an_approval_on_an_underlying_already_short_fails_and_places_nothing(signals,
                                                                              notices):
    sid = pending(lane="wheel", symbol="APH", strike=96.0, qty=1, premium_est=1.5)
    operator(sid, "approve")
    held = NS(symbol=put(90.0, "2026-10-09"), underlying="APH", option_type="put",
              strike=90.0, expiry="2026-10-09", qty=-1, avg_entry_price=1.0,
              market_value=-80.0)
    service = Service()
    ok, error, _result = command(service, Adapter(option_positions=[held]), sid)
    assert ok is False and "duplicate" in error and service.intents == []
    row = signals_store.get_signal(sid)
    assert (row["status"], row["order_client_id"]) == ("failed", None)
    # The operator believes that trade is on: one swing_approval_failed notice.
    (notice,) = notices
    assert notice["category"] == "swing_approval_failed"
    assert notice["instance_id"] == IID
    assert notice["push_title"] == "Approved wheel order refused: APH"
    assert notice["body"].startswith(
        f"SWING APPROVAL FAILED [{IID}] Approved wheel order refused: APH\n"
        "APH: the wheel order you approved was not sent — ")
    assert "duplicate" in notice["body"] and "duplicate" in notice["push_body"]
