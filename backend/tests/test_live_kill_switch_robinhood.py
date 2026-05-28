"""Tests for the Robinhood branch of live_kill_switch.halt_live_trading.

The smoke test in test_live_kill_switch_smoke.py covers Alpaca + module-load
paths; this file adds coverage for the new Robinhood cancel path so the
operator can rely on `python -m backend.live_kill_switch` to cancel both
Alpaca and Robinhood open orders.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)


def _fake_r_with_brokerages(brokerages: list[dict]) -> tuple[MagicMock, MagicMock]:
    """Build a rethinkdb-mock that yields the given BrokerageAccounts rows
    and an Instances .update() that succeeds."""
    r = MagicMock()
    db = MagicMock()
    r.db.return_value = db
    instances_tbl = MagicMock()
    bro_tbl = MagicMock()
    db.table.side_effect = lambda name: instances_tbl if name == "Instances" else bro_tbl
    instances_tbl.update.return_value.run.return_value = {
        "replaced": 1,
        "unchanged": 0,
        "errors": 0,
    }
    bro_tbl.run.return_value = iter(brokerages)
    return r, MagicMock()  # (r, conn)


def test_robinhood_cancel_path_invokes_cancel_for_each_open_order(monkeypatch):
    """Given an RH brokerage with 2 open orders, both should be canceled."""
    import live_kill_switch as lks

    rh_brokerage = {
        "id": "rh-1",
        "brokerage_type": "robinhood",
        "robinhood_access_token": "<encrypted access>",
        "robinhood_refresh_token": "<encrypted refresh>",
        "robinhood_device_token": "<encrypted device>",
        "robinhood_account_number": "REDACTED-ACCT",
        "robinhood_account_url": "https://api.robinhood.com/accounts/REDACTED-ACCT/",
        "robinhood_obtained_at_epoch": 1779000000,
        "robinhood_expires_in": 2400000,
    }

    fake_r, fake_conn = _fake_r_with_brokerages([rh_brokerage])
    monkeypatch.setattr(lks, "_get_conn", lambda: (fake_r, fake_conn))

    # Plaintext passthrough for decrypt (secret_store's contract — see
    # secret_store.py:7).
    monkeypatch.setitem(sys.modules, "secret_store", type(sys)("secret_store"))
    sys.modules["secret_store"].decrypt = lambda v: v

    # Fake RobinhoodSessionState that accepts any kwargs.
    fake_engine = type(sys)("robinhood_engine")

    class _FakeState:
        def __init__(self, **kw):
            self.kw = kw

    class _FakeClient:
        def __init__(self, *, state, timeout_sec=20, **_):
            self.state = state
            self.cancel_calls = []

        def list_orders(self, *, state, limit, account_number):
            assert state == "open"
            return [
                {"id": "ord-1", "cancel": "https://api.robinhood.com/orders/ord-1/cancel/"},
                {"id": "ord-2", "cancel": None},
            ]

        def cancel_order(self, *, cancel_url=None, order_id=None):
            self.cancel_calls.append((cancel_url, order_id))
            return True

    fake_engine.RobinhoodSessionState = _FakeState
    fake_engine.RobinhoodClient = _FakeClient
    monkeypatch.setitem(sys.modules, "robinhood_engine", fake_engine)

    # Don't actually try to send Discord alerts.
    fake_alerts = type(sys)("live_alerts")
    fake_alerts.alert_halt = lambda **_: None
    monkeypatch.setitem(sys.modules, "live_alerts", fake_alerts)

    summary = lks.halt_live_trading(reason="test")

    assert summary["instances_halted"] >= 1
    assert summary["orders_canceled"] == 2, (
        f"expected 2 RH cancels, got {summary}"
    )
    assert summary["errors"] == [], summary["errors"]


def test_robinhood_cancel_path_skips_when_creds_missing(monkeypatch):
    """A brokerage row with no decryptable RH tokens should be skipped
    with an error message, not crash."""
    import live_kill_switch as lks

    rh_brokerage = {
        "id": "rh-broken",
        "brokerage_type": "robinhood",
        "robinhood_access_token": None,
        "robinhood_refresh_token": None,
    }
    fake_r, fake_conn = _fake_r_with_brokerages([rh_brokerage])
    monkeypatch.setattr(lks, "_get_conn", lambda: (fake_r, fake_conn))
    monkeypatch.setitem(sys.modules, "secret_store", type(sys)("secret_store"))
    sys.modules["secret_store"].decrypt = lambda v: v

    fake_alerts = type(sys)("live_alerts")
    fake_alerts.alert_halt = lambda **_: None
    monkeypatch.setitem(sys.modules, "live_alerts", fake_alerts)

    summary = lks.halt_live_trading(reason="test")

    assert summary["orders_canceled"] == 0
    assert any("creds missing" in e for e in summary["errors"]), summary["errors"]


def test_unknown_brokerage_type_recorded_as_error(monkeypatch):
    """A row with an unsupported brokerage_type should produce a clear error
    line rather than silently skipping."""
    import live_kill_switch as lks

    bro = {
        "id": "weird-1",
        "brokerage_type": "interactive_brokers",
    }
    fake_r, fake_conn = _fake_r_with_brokerages([bro])
    monkeypatch.setattr(lks, "_get_conn", lambda: (fake_r, fake_conn))
    fake_alerts = type(sys)("live_alerts")
    fake_alerts.alert_halt = lambda **_: None
    monkeypatch.setitem(sys.modules, "live_alerts", fake_alerts)

    summary = lks.halt_live_trading(reason="test")
    assert any("unsupported brokerage_type" in e for e in summary["errors"]), \
        summary["errors"]
