"""E6 + E4: the two places a live-order failure disappeared without a word.

E6  `reconcile_wal_with_broker` walked every open WAL row in ONE try-less loop,
    and `get_order_by_client_id` RAISES on a transient broker failure (by
    design — so a 5xx is not mistaken for "not at broker"). One raising row
    therefore aborted the whole pass and threw away the resolutions already
    computed for the rows before it. And an `intent` row the broker has never
    heard of — the process died between the WAL write and the API call — stayed
    open forever, reported as missing on every later pass.

E4  `LiveOrderService.submit` turns five different exceptions into reason codes
    (`dependency.snapshot.unavailable`, `persistence.intent.failed`, …) and
    discards the exception itself. The code says which STAGE failed; nothing
    anywhere said why.
"""
import datetime
import os
import sys
from decimal import Decimal

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import pytest  # noqa: E402

import broker_adapters.alpaca as alpaca_mod  # noqa: E402
from broker_adapters._wal import LiveOrderWAL  # noqa: E402
from broker_adapters.base import OrderRef  # noqa: E402


@pytest.fixture
def alog(monkeypatch):
    """Capture the module-level `_alog` the reconciler reports through."""
    lines = []
    monkeypatch.setattr(
        alpaca_mod, "_alog",
        lambda service, msg, color="white": lines.append((str(msg), color)))
    return lines


# --- E6: the WAL reconciler -------------------------------------------------

class MemoryStore:
    def __init__(self):
        self.rows = {}

    def insert(self, row):
        self.rows[row["client_order_id"]] = dict(row)

    def update(self, cid, patch):
        self.rows.setdefault(cid, {"client_order_id": cid}).update(patch)

    def get(self, cid):
        return self.rows.get(cid)

    def list_open(self):
        return [dict(r) for r in self.rows.values()]


class Reconciler:
    """The adapter surface `reconcile_wal_with_broker` actually touches, with
    the real method bound onto it."""

    from broker_adapters.alpaca import AlpacaAdapter
    reconcile_wal_with_broker = AlpacaAdapter.reconcile_wal_with_broker
    _intent_age_hours = AlpacaAdapter._intent_age_hours
    STALE_INTENT_RETIREMENT_HOURS = AlpacaAdapter.STALE_INTENT_RETIREMENT_HOURS

    def __init__(self, wal, answers):
        self._wal = wal
        self._answers = answers
        self.discarded = []

    def get_order_by_client_id(self, cid):
        answer = self._answers[cid]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def _discard_orders_today(self, symbol, side):
        self.discarded.append((symbol, side))


def wal_with(*intents, age_hours=0.0):
    store = MemoryStore()
    wal = LiveOrderWAL(store)
    stamp = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(hours=age_hours)).isoformat()
    for cid, symbol, side in intents:
        wal.record_intent(cid, symbol, side, 1.0, None)
        store.rows[cid]["created_at_utc"] = stamp
        store.rows[cid]["updated_at_utc"] = stamp
    return wal, store


def ref(cid, status="filled"):
    return OrderRef(broker_order_id=f"b-{cid}", client_order_id=cid,
                    symbol="TQQQ", side="sell", qty=1.0, status=status,
                    filled_qty=1.0, filled_avg_price=80.0)


def test_one_transient_row_no_longer_costs_the_whole_pass(alog):
    """The row that raises is the one that stays unresolved. Everything after
    it used to be skipped, and everything BEFORE it was thrown away with the
    exception — on the boot path that decides whether the account can sell."""
    wal, _store = wal_with(("a", "TQQQ", "sell"), ("b", "GLD", "sell"),
                           ("c", "SPY", "buy"))
    rec = Reconciler(wal, {"a": ref("a"), "b": RuntimeError("alpaca 502"),
                           "c": ref("c", "canceled")})
    got = rec.reconcile_wal_with_broker()
    assert got["a"] == "already_filled"
    assert got["c"] == "already_canceled"
    assert got["b"].startswith("unresolved"), got["b"]
    assert "RuntimeError" in got["b"]
    assert [m for m, c in alog if c == "red"]


def test_an_unresolved_row_is_left_open_for_the_next_pass():
    wal, store = wal_with(("a", "TQQQ", "sell"))
    rec = Reconciler(wal, {"a": RuntimeError("alpaca 502")})
    rec.reconcile_wal_with_broker()
    assert store.rows["a"]["state"] == "intent", "a transient failure retired it"


def test_a_stale_intent_the_broker_never_saw_is_retired(alog):
    """E4. The process died between the WAL write and the API call, so there is
    no order and never will be. Left open it is reported missing on every boot
    and the cycle reads unhealthy forever."""
    wal, store = wal_with(("a", "TQQQ", "sell"), age_hours=40)
    rec = Reconciler(wal, {"a": None})
    got = rec.reconcile_wal_with_broker()
    assert got["a"] == "retired_stale_intent"
    assert store.rows["a"]["state"] == "expired"
    assert [m for m, c in alog if c == "red"]


def test_a_fresh_intent_is_not_retired():
    """Minutes old, the submit may still be in flight in another thread."""
    wal, store = wal_with(("a", "TQQQ", "sell"), age_hours=1)
    rec = Reconciler(wal, {"a": None})
    assert rec.reconcile_wal_with_broker()["a"] == "not_found_at_broker"
    assert store.rows["a"]["state"] == "intent"


def test_a_submitted_row_is_never_retired_by_age():
    """It carries a broker_order_id, so "the broker has not heard of it" is a
    contradiction to investigate, not a row to tidy away."""
    wal, store = wal_with(("a", "TQQQ", "sell"), age_hours=400)
    wal.mark_submitted("a", "b-1")
    rec = Reconciler(wal, {"a": None})
    assert rec.reconcile_wal_with_broker()["a"] == "not_found_at_broker"
    assert store.rows["a"]["state"] == "submitted"


def test_an_unparseable_timestamp_does_not_retire_a_row():
    wal, store = wal_with(("a", "TQQQ", "sell"))
    store.rows["a"]["created_at_utc"] = "not a date"
    rec = Reconciler(wal, {"a": None})
    assert rec.reconcile_wal_with_broker()["a"] == "not_found_at_broker"
    assert store.rows["a"]["state"] == "intent"


# --- E4: the service's swallowed exceptions ---------------------------------

from live_order_task8_helpers import intent as _base_intent  # noqa: E402
from live_orders.service import LiveOrderService  # noqa: E402


def intent(symbol="AAPL"):
    """The symbol is part of the idempotency identity; the reason is not."""
    return _base_intent(symbol=symbol)


def service(**kwargs):
    lines = []

    def log(message, color="white"):
        lines.append((str(message), color))

    kwargs.setdefault("transport", lambda **_k: None)
    svc = LiveOrderService(account_id="acct-1", instance_id="instance-1",
                           log=log, **kwargs)
    return svc, lines


def boom(_i):
    raise RuntimeError("risk row unreadable")


def test_a_snapshot_provider_that_raises_reports_the_exception():
    svc, lines = service(snapshot_provider=boom)
    out = svc.submit(intent())
    assert out.decision.reason_codes == ("dependency.snapshot.unavailable",)
    assert lines, "the reason code named the STAGE and nothing named the cause"
    assert "RuntimeError" in lines[0][0]
    assert "risk row unreadable" in lines[0][0]
    assert "dependency.snapshot.unavailable" in lines[0][0]


def test_an_invalid_snapshot_is_reported_too():
    svc, lines = service(snapshot_provider=lambda _i: "not a snapshot")
    out = svc.submit(intent())
    assert out.decision.reason_codes == ("dependency.snapshot.invalid",)
    assert lines and "dependency.snapshot.invalid" in lines[0][0]


def test_the_same_identity_is_reported_once():
    """A retried exit must not fill the log with the same line."""
    svc, lines = service(snapshot_provider=boom)
    for _ in range(5):
        svc.submit(intent())
    assert len(lines) == 1


def test_a_different_identity_is_reported_again():
    svc, lines = service(snapshot_provider=boom)
    svc.submit(intent("AAPL"))
    svc.submit(intent("TQQQ"))
    assert len(lines) == 2


def test_a_broken_log_never_reaches_the_order_path():
    def bad_log(*_a, **_k):
        raise RuntimeError("logger is down")

    svc = LiveOrderService(account_id="acct-1", instance_id="instance-1",
                           snapshot_provider=boom, transport=lambda **_k: None,
                           log=bad_log)
    assert svc.submit(intent()).decision.reason_codes == (
        "dependency.snapshot.unavailable",)


def test_a_service_with_no_logger_is_unchanged():
    svc = LiveOrderService(account_id="acct-1", instance_id="instance-1",
                           snapshot_provider=boom, transport=lambda **_k: None)
    assert svc.submit(intent()).decision.reason_codes == (
        "dependency.snapshot.unavailable",)
