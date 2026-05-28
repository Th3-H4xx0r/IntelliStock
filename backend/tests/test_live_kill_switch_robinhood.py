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


def _fake_r_scoped(brokerages: list[dict], inst_doc: dict):
    """Mock where Instances supports .filter().update().run(), .get().run(),
    and BrokerageAccounts.run() yields the given rows."""
    from unittest.mock import MagicMock
    r = MagicMock()
    db = MagicMock()
    r.db.return_value = db
    instances_tbl = MagicMock()
    bro_tbl = MagicMock()
    db.table.side_effect = lambda name: instances_tbl if name == "Instances" else bro_tbl
    upd_res = {"replaced": 1, "unchanged": 0, "errors": 0}
    instances_tbl.update.return_value.run.return_value = upd_res
    instances_tbl.filter.return_value.update.return_value.run.return_value = upd_res
    instances_tbl.get.return_value.run.return_value = inst_doc
    bro_tbl.run.return_value = iter(brokerages)
    return r, MagicMock(), instances_tbl


def _patch_rh_engine(monkeypatch, listed_accounts: list):
    """Patch secret_store + robinhood_engine; record which account_numbers get
    list_orders so a test can assert blast radius."""
    monkeypatch.setitem(sys.modules, "secret_store", type(sys)("secret_store"))
    sys.modules["secret_store"].decrypt = lambda v: v
    fake_engine = type(sys)("robinhood_engine")

    class _FakeState:
        def __init__(self, **kw):
            self.kw = kw

    class _FakeClient:
        def __init__(self, *, state, timeout_sec=20, **_):
            self.state = state

        def list_orders(self, *, state, limit, account_number):
            listed_accounts.append(account_number)
            return [{"id": "ord-1", "cancel": "https://api.robinhood.com/orders/ord-1/cancel/"}]

        def cancel_order(self, *, cancel_url=None, order_id=None):
            return True

    fake_engine.RobinhoodSessionState = _FakeState
    fake_engine.RobinhoodClient = _FakeClient
    monkeypatch.setitem(sys.modules, "robinhood_engine", fake_engine)
    fake_alerts = type(sys)("live_alerts")
    fake_alerts.alert_halt = lambda **_: None
    monkeypatch.setitem(sys.modules, "live_alerts", fake_alerts)


def _rh(id_, acct):
    return {
        "id": id_, "brokerage_type": "robinhood",
        "robinhood_access_token": "a", "robinhood_refresh_token": "rf",
        "robinhood_device_token": "d", "robinhood_account_number": acct,
        "robinhood_account_url": f"https://api.robinhood.com/accounts/{acct}/",
        "robinhood_obtained_at_epoch": 1779000000, "robinhood_expires_in": 2400000,
    }


def test_scoped_halt_only_touches_linked_brokerage(monkeypatch):
    """1-B: halt_live_trading(instance_id=...) must flip runCommand on ONLY that
    instance and cancel orders on ONLY its linked brokerage — not the real-money
    'main' brokerage."""
    import live_kill_switch as lks

    rh_main = _rh("rh-main", "REDACTED-ACCT")   # real money — MUST be untouched
    rh_paper = _rh("rh-paper", "999999999")  # the failing (paper) instance's brokerage
    inst_doc = {"id": "nexus-live", "brokerage_id": "rh-paper"}

    fake_r, fake_conn, instances_tbl = _fake_r_scoped([rh_main, rh_paper], inst_doc)
    monkeypatch.setattr(lks, "_get_conn", lambda: (fake_r, fake_conn))
    listed: list = []
    _patch_rh_engine(monkeypatch, listed)

    summary = lks.halt_live_trading(reason="LLM critical", instance_id="nexus-live")

    assert summary["scope"] == "nexus-live"
    # Step 1 used a FILTER (scoped), not a bulk update.
    assert instances_tbl.filter.called, "scoped halt must filter Instances, not bulk-update"
    # Only the paper brokerage's account was touched; the real-money acct was NOT.
    assert listed == ["999999999"], f"blast radius leaked to other accounts: {listed}"
    assert summary["orders_canceled"] == 1
    assert summary["errors"] == [], summary["errors"]


def test_scoped_halt_with_no_linked_brokerage_cancels_nothing(monkeypatch):
    """1-B fail-safe: an instance with no brokerage_id cancels NOTHING (never
    falls back to touching every account)."""
    import live_kill_switch as lks

    rh_main = _rh("rh-main", "REDACTED-ACCT")
    inst_doc = {"id": "ghost", "brokerage_id": None}
    fake_r, fake_conn, _ = _fake_r_scoped([rh_main], inst_doc)
    monkeypatch.setattr(lks, "_get_conn", lambda: (fake_r, fake_conn))
    listed: list = []
    _patch_rh_engine(monkeypatch, listed)

    summary = lks.halt_live_trading(reason="LLM critical", instance_id="ghost")

    assert listed == [], "must not touch any brokerage when no link resolves"
    assert summary["orders_canceled"] == 0
    assert any("no linked brokerage_id" in e for e in summary["errors"]), summary["errors"]


def test_critical_abort_threads_instance_id_to_halt(monkeypatch):
    """live_critical_abort.handle must scope the automatic halt to the failing instance."""
    import live_critical_abort as lca
    lca.reset_state()
    captured = {}

    def _fake_halt(*, reason, instance_id=None):
        captured["reason"] = reason
        captured["instance_id"] = instance_id
        return {"instances_halted": 1, "orders_canceled": 0}

    monkeypatch.setattr(lca, "_halt_live_trading", _fake_halt)
    monkeypatch.setattr(lca, "_alert_strategy_error", lambda **_: None)

    class _F:
        class_tag = "empty_body"
        attempts = [{"body_sample": "x"}]
        model = "kimi-k2.5"
        provider = "moonshot"

    lca.handle(instance_id="nexus-live", failure=_F())
    assert captured["instance_id"] == "nexus-live", \
        f"automatic abort must pass instance_id, got {captured}"
    lca.reset_state()


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
