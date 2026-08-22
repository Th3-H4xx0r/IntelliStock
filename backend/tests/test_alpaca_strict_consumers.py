"""Strict encrypted-credential behavior at every Alpaca stock consumer.

The canary rows model legacy plaintext database records.  Stock-facing
consumers must reject them before constructing a broker client or issuing an
HTTP request.  Tests use only in-memory database doubles.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest


class _FakeQuery:
    def __init__(self, value):
        self._value = value

    def run(self, _conn):
        return self._value


class _FakeTable:
    def __init__(self, name, rows):
        self._name = name
        self._rows = rows

    def get(self, row_id):
        return _FakeQuery(self._rows.get((self._name, str(row_id))))


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    def table(self, name):
        return _FakeTable(name, self._rows)


class _FakeR:
    """ReQL-shaped double, still needed for interactive_utils (group G3's
    file, not yet ported)."""

    def __init__(self, rows):
        self._rows = rows

    def db(self, _name):
        return _FakeDb(self._rows)


class _FakeStore:
    """db.store stand-in keyed by (table, id). Postgres port (G11)."""

    def __init__(self, rows):
        self._rows = rows

    def get(self, table, row_id):
        return self._rows.get((table, str(row_id)))

    def run(self, selection):
        table = getattr(selection, "table", selection)
        return [row for (t, _id), row in self._rows.items() if t == table]

    def filter(self, table, _predicate):
        return table


class _FakeConn:
    def close(self):
        return None


def _plaintext_alpaca_row():
    return {
        "id": "alpaca-linked",
        "brokerage_type": "alpaca",
        "alpaca_key": "CANARY_PLAINTEXT_KEY",
        "alpaca_secret": "CANARY_PLAINTEXT_SECRET",
        "alpaca_paper": True,
        "alpaca_data_feed": "iex",
        "alpaca_base_url": "https://paper-api.alpaca.markets",
    }


def _install_fake_alpaca(monkeypatch, constructions):
    alpaca = ModuleType("alpaca")
    trading = ModuleType("alpaca.trading")
    client_module = ModuleType("alpaca.trading.client")
    requests_module = ModuleType("alpaca.trading.requests")
    enums_module = ModuleType("alpaca.trading.enums")

    class _TradingClient:
        def __init__(self, **kwargs):
            constructions.append(kwargs)

        def cancel_orders(self):
            return [{"id": "should-not-be-reached"}]

        def get_all_positions(self):
            return []

        def get_account(self):
            return SimpleNamespace(cash="0", equity="0")

    client_module.TradingClient = _TradingClient
    requests_module.GetOrdersRequest = lambda **kwargs: kwargs
    enums_module.QueryOrderStatus = SimpleNamespace(CLOSED="closed")
    alpaca.trading = trading
    trading.client = client_module
    trading.requests = requests_module
    trading.enums = enums_module
    monkeypatch.setitem(sys.modules, "alpaca", alpaca)
    monkeypatch.setitem(sys.modules, "alpaca.trading", trading)
    monkeypatch.setitem(sys.modules, "alpaca.trading.client", client_module)
    monkeypatch.setitem(sys.modules, "alpaca.trading.requests", requests_module)
    monkeypatch.setitem(sys.modules, "alpaca.trading.enums", enums_module)


def test_portfolio_history_rejects_plaintext_alpaca_row_before_fetch(monkeypatch):
    """Removing strict decryption would pass the canary into the fetch helper."""
    import interactive_utils as iu

    row = _plaintext_alpaca_row()
    monkeypatch.setattr(iu, "_ensure_brokerage_accounts_table", lambda _conn: None)
    monkeypatch.setattr(
        iu,
        "r",
        _FakeR({("BrokerageAccounts", row["id"]): row}),
    )
    called = []
    monkeypatch.setattr(
        iu,
        "_fetch_alpaca_portfolio_history",
        lambda **kwargs: called.append(kwargs) or {"timestamps": [], "values": []},
    )

    with pytest.raises(Exception) as exc:
        iu.action_get_portfolio_history(_FakeConn(), row["id"])

    assert called == []
    assert "CANARY" not in str(exc.value)


def test_live_state_loader_rejects_plaintext_and_instance_fallback(monkeypatch):
    """A stock instance must use one exact encrypted BrokerageAccounts link."""
    import live_broker_fetch as live_fetch

    row = _plaintext_alpaca_row()
    instance = {
        "id": "stock-main",
        "kind": "stocks",
        "brokerage_id": row["id"],
        "key": "CANARY_INSTANCE_KEY",
        "secret": "CANARY_INSTANCE_SECRET",
    }
    rows = {
        ("Instances", instance["id"]): instance,
        ("BrokerageAccounts", row["id"]): row,
    }
    monkeypatch.setattr(
        live_fetch,
        "_open_db_conn",
        lambda: (_FakeStore(rows), _FakeConn()),
    )

    result = live_fetch._load_credentials(instance["id"])

    assert result["error"]
    assert "key" not in result
    assert "secret" not in result
    assert "CANARY" not in repr(result)


def test_kill_switch_rejects_plaintext_before_alpaca_client(monkeypatch):
    """Removing strict decryption would let the kill switch authenticate."""
    import live_kill_switch as kill_switch

    row = _plaintext_alpaca_row()

    class _KillStore:
        """The store handle live_kill_switch unpacks from _get_conn()."""

        def Selection(self, table):
            return table

        def filter(self, table, _predicate):
            return table

        def update(self, _table, _selector, _patch):
            return {"replaced": 1, "unchanged": 0}

        def run(self, selection):
            return [row] if selection == "BrokerageAccounts" else []

        def get(self, _table, _row_id):
            return None

    monkeypatch.setattr(kill_switch, "_get_conn",
                        lambda: (_KillStore(), _FakeConn()))

    constructions = []
    _install_fake_alpaca(monkeypatch, constructions)
    alerts = ModuleType("live_alerts")
    alerts.alert_halt = lambda **_kwargs: None
    monkeypatch.setitem(sys.modules, "live_alerts", alerts)

    result = kill_switch.halt_live_trading(reason="test")

    assert constructions == []
    assert result["orders_canceled"] == 0
    assert result["errors"]
    assert "CANARY" not in repr(result)


def test_stored_alpaca_diagnostic_rejects_plaintext_before_probe(monkeypatch):
    """The edit-mode diagnostic may read stored credentials only if encrypted."""
    from fastapi import HTTPException
    from api import main

    row = _plaintext_alpaca_row()
    monkeypatch.setattr(
        main,
        "db_store",
        _FakeStore({("BrokerageAccounts", row["id"]): row}),
    )
    called = []
    monkeypatch.setattr(
        main,
        "alpaca_run_diagnostic_suite",
        lambda **kwargs: called.append(kwargs) or {"ok": True},
    )

    with pytest.raises(HTTPException) as exc:
        main.api_test_alpaca_brokerage(
            main.TestAlpacaBody(brokerage_id=row["id"]),
            conn=_FakeConn(),
            current_user={"id": "test"},
        )

    assert called == []
    assert exc.value.status_code == 500
    assert "CANARY" not in str(exc.value.detail)


def test_holding_opens_rejects_plaintext_before_client(monkeypatch):
    """A plaintext row must not reach the Alpaca SDK."""
    from api import main

    row = _plaintext_alpaca_row()
    monkeypatch.setattr(
        main,
        "db_store",
        _FakeStore({("BrokerageAccounts", row["id"]): row}),
    )
    main._HOLDING_OPENS_CACHE.clear()
    constructions = []
    _install_fake_alpaca(monkeypatch, constructions)

    result = main.api_brokerage_holding_opens(
        row["id"],
        conn=_FakeConn(),
        current_user={"id": "test"},
    )

    assert result["opens"] == {}
    assert constructions == []


def test_movers_rejects_plaintext_before_http_request(monkeypatch):
    """A plaintext row must not reach Alpaca's data endpoint."""
    import requests
    from api import main

    row = _plaintext_alpaca_row()
    monkeypatch.setattr(
        main,
        "db_store",
        _FakeStore({("BrokerageAccounts", row["id"]): row}),
    )
    calls = []
    monkeypatch.setattr(
        requests,
        "get",
        lambda *args, **kwargs: calls.append((args, kwargs))
        or SimpleNamespace(ok=True, json=lambda: {}),
    )

    result = main.api_brokerage_movers(
        row["id"],
        conn=_FakeConn(),
        current_user={"id": "test"},
    )

    assert result == {"gainers": [], "losers": []}
    assert calls == []


def test_inspector_rejects_plaintext_before_client(monkeypatch):
    """The read-only inspector must obey the same encrypted stock boundary."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "inspect_broker_state.py"
    spec = importlib.util.spec_from_file_location("inspect_broker_state_strict", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    constructions = []
    _install_fake_alpaca(monkeypatch, constructions)

    with pytest.raises(Exception) as exc:
        module._fetch_broker_positions_and_cash(_plaintext_alpaca_row())

    assert constructions == []
    assert "CANARY" not in str(exc.value)
