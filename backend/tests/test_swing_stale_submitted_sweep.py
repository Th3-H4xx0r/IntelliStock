"""Fix wave FW1 item 9 (plan B final review M-2): a stale "submitted" row.

The approval handler claims a signal approved -> submitted with a
compare-and-swap BEFORE it sends anything, then writes the order's key back.
A process that died between the two leaves a row that reads "submitted"
forever with no key and no lifecycle intent: the operator believes the trade
is on, and nothing will ever resolve it. The lane tick now sweeps such a row
once it is 10 minutes past its claim: it is marked failed with a notice that
the order may not have been placed. Swing and wheel documents only."""
import datetime as dtm
from decimal import Decimal
from types import SimpleNamespace

import pytest

from live_orders import (
    InMemoryLifecycleBackend,
    OrderLifecycleStore,
    OrderSource,
)
from swing_broker_harness import extract, function_source, source
from swing_live_fixtures import bracket_intent, option_intent
from swing_trader import notify, signals_store

UTC = dtm.timezone.utc
NOW = dtm.datetime(2026, 10, 5, 15, 0, tzinfo=UTC)
IID = "instance-1"


@pytest.fixture
def world(store, monkeypatch):
    monkeypatch.setattr(signals_store, "store", store)
    sent = []
    monkeypatch.setattr(notify, "_sink", lambda **kwargs: sent.append(kwargs))
    service = SimpleNamespace(
        instance_id=IID,
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()))
    lines = []
    sweep = extract(("_sweep_stale_submitted_signals",),
                    namespace={"datetime": dtm})["_sweep_stale_submitted_signals"]

    def run(**kwargs):
        return sweep(service, now_utc=NOW,
                     log=lambda message, color="white": lines.append((color, message)),
                     **kwargs)

    return SimpleNamespace(service=service, sent=sent, lines=lines, run=run)


def _row(lane="swing", symbol="AAPL", *, claimed_minutes_ago=11, key=None,
         status="submitted", instance_id=IID, claimed=True, session="2026-10-05"):
    doc = signals_store.new_signal(
        instance_id=instance_id, lane=lane, symbol=symbol, session=session,
        score=62, recommendation="review", reasoning="r", key_risks=[],
        size_adjustment=1.0, proposal={"entry": 98.0}, status=status)
    at = (NOW - dtm.timedelta(minutes=claimed_minutes_ago)).isoformat()
    doc.update(decided_by="pranav", decided_at=at, order_client_id=key)
    if claimed:
        doc["claimed_at"] = at
    signals_store.insert_signal(doc)
    return doc["id"]


def test_a_claimed_row_with_no_key_and_no_intent_is_marked_failed(world):
    sid = _row()
    assert world.run() == [sid]
    row = signals_store.get_signal(sid)
    assert row["status"] == "failed" and row["order_client_id"] is None
    (notice,) = world.sent
    assert notice["category"] == "swing_approval_failed"
    assert "may not have been placed — check open orders" in notice["body"]
    assert "AAPL" in notice["body"]
    assert any(color == "red" and sid in message for color, message in world.lines)
    assert world.run() == []          # once only


def test_a_row_younger_than_ten_minutes_is_left(world):
    sid = _row(claimed_minutes_ago=9)
    assert world.run() == []
    assert signals_store.get_signal(sid)["status"] == "submitted"
    assert world.sent == []


def test_the_age_counts_from_the_claim_not_the_decision(world):
    """An approval that waited in the queue is claimed late: a crash just
    after that claim is only minutes old however old the decision is."""
    sid = _row(claimed_minutes_ago=2)
    row = signals_store.get_signal(sid)
    row["decided_at"] = (NOW - dtm.timedelta(hours=3)).isoformat()
    signals_store.update_signal(sid, {"decided_at": row["decided_at"]})
    assert world.run() == []


def test_a_row_without_a_claim_stamp_ages_from_its_decision(world):
    sid = _row(claimed=False, claimed_minutes_ago=30)
    assert world.run() == [sid]


def test_a_row_with_a_key_is_left_for_the_reconcile(world):
    sid = _row(key="swingpap-abc-0")
    assert world.run() == [] and world.sent == []
    assert signals_store.get_signal(sid)["status"] == "submitted"


def test_a_row_whose_lifecycle_intent_exists_is_left(world):
    sid = _row()
    world.service.lifecycle_store.create_intent(
        bracket_intent(source=OrderSource.MANUAL, reason=f"swing_approval:{sid}"))
    assert world.run() == [] and world.sent == []


def test_a_wheel_row_with_a_manual_option_intent_on_its_underlying_is_left(world):
    """An approval placed before its option intent carried the signal id:
    a MANUAL option intent on the underlying since the claim."""
    sid = _row(lane="wheel", symbol="APH")
    world.service.lifecycle_store.create_intent(option_intent(
        source=OrderSource.MANUAL, reason="wheel_sto_put", decision_at=NOW,
        quote_at=NOW))
    assert world.run() == []
    assert signals_store.get_signal(sid)["status"] == "submitted"


def test_other_statuses_and_instances_are_never_touched(world):
    kept = [_row(status=status, symbol=symbol) for status, symbol in (
        ("approved", "MSFT"), ("pending", "NVDA"), ("auto_approved", "AMD"),
        ("failed", "TSLA"))]
    other = _row(instance_id="alpaca-main", symbol="GLD")
    assert world.run() == []
    assert [signals_store.get_signal(s)["status"] for s in kept] == [
        "approved", "pending", "auto_approved", "failed"]
    assert signals_store.get_signal(other)["status"] == "submitted"


def test_an_unreadable_lifecycle_store_marks_nothing(world):
    sid = _row()

    def boom(_instance_id):
        raise ConnectionError("lifecycle backend down")

    world.service.lifecycle_store.list_for_instance = boom
    with pytest.raises(ConnectionError):
        world.run()
    assert signals_store.get_signal(sid)["status"] == "submitted"
    assert world.sent == []


def test_a_row_that_moved_on_meanwhile_is_not_told(world, monkeypatch):
    sid = _row()
    monkeypatch.setattr(signals_store, "cas_signal",
                        lambda *a, **k: False)
    assert world.run() == [] and world.sent == []


# --- the wiring -------------------------------------------------------------------

def test_the_sweep_runs_only_on_a_swing_or_wheel_document():
    text = source()
    call = text.index("_sweep_stale_submitted_signals(\n")
    guard = text[text.rindex("if (", 0, call):call]
    assert 'mode == MODE_LIVE' in guard
    assert '_lane_enabled(_cached_strategies, "strategy_swing")' in guard
    assert '_lane_enabled(_cached_strategies, "strategy_wheel")' in guard


def test_the_claim_is_stamped_and_the_wheel_intent_names_its_signal():
    body = function_source("_execute_swing_approval")
    # Seams m6: the day rule's own compare-and-swap precedes the claim; the
    # slice is the claim up to ITS compare-and-swap.
    start = body.index('claimed["status"] = "submitted"')
    claim = body[start:body.index("signals_store.cas_signal(", start)]
    assert 'claimed["claimed_at"]' in claim
    assert 'reason=f"swing_approval:{signal_id}"' in body
    option = body[body.index("_build_option_intent("):]
    assert 'dict(order, reason=f"swing_approval:{signal_id}")' in option[:200]


def test_a_row_whose_approval_is_in_flight_is_skipped(world):
    """Round 2, minor 3: the handler that claimed it is still placing it (a
    slow HTTP call before create_intent); the sweep must not fail it."""
    sid = _row(claimed_minutes_ago=30)
    in_flight = {sid}
    sweep = extract(("_sweep_stale_submitted_signals",),
                    namespace={"datetime": dtm,
                               "_swing_approvals_in_flight": in_flight})[
        "_sweep_stale_submitted_signals"]
    assert sweep(world.service, now_utc=NOW) == []
    assert signals_store.get_signal(sid)["status"] == "submitted"
    in_flight.clear()
    assert sweep(world.service, now_utc=NOW) == [sid]
