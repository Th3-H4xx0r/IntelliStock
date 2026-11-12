"""GET /brokerages/{id}/order-book — open orders and recent history, read
straight from Alpaca for the dashboard (web and iOS)."""
from types import SimpleNamespace

import pytest


ALPACA_ROW = {
    "id": "b1",
    "brokerage_type": "alpaca",
    "alpaca_paper": True,
    "alpaca_base_url": "https://paper-api.alpaca.markets",
}


def _order(oid, status, **kw):
    base = {
        "id": oid, "symbol": "qqq", "side": "buy", "type": "limit", "status": status,
        "qty": "3", "notional": None, "filled_qty": "0", "filled_avg_price": None,
        "limit_price": "510.5", "stop_price": None, "time_in_force": "day",
        "submitted_at": "2026-11-12T14:31:05.123456Z", "filled_at": None,
        "canceled_at": None, "asset_class": "us_equity",
    }
    base.update(kw)
    return base


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from api import main as api_main
    import stock_credential_boundary

    api_main._ORDER_BOOK_CACHE.clear()
    monkeypatch.setattr(
        stock_credential_boundary, "resolve_alpaca_brokerage_credentials",
        lambda row, expected_brokerage_id=None: SimpleNamespace(key="k", secret="s"),
    )
    api_main.app.dependency_overrides[api_main.get_current_user] = lambda: {
        "id": "test-user", "username": "test"}
    yield TestClient(api_main.app), api_main
    api_main.app.dependency_overrides.clear()
    api_main._ORDER_BOOK_CACHE.clear()


def _stub_alpaca(monkeypatch, api_main, *, open_orders, closed_orders, calls, status=200):
    import requests

    monkeypatch.setattr(api_main.db_store, "get",
                        lambda table, key: dict(ALPACA_ROW) if key == "b1" else None)

    def fake_get(url, headers=None, timeout=None, params=None):
        calls.append((url, dict(params or {}), dict(headers or {})))
        payload = open_orders if params["status"] == "open" else closed_orders
        return _Resp(payload, status)

    monkeypatch.setattr(requests, "get", fake_get)


def test_normalize_alpaca_orders_maps_strings_and_notional():
    """A notional order has no qty; numbers arrive as strings; id-less rows are junk."""
    from api.main import normalize_alpaca_orders

    out = normalize_alpaca_orders([
        _order("o1", "partially_filled", filled_qty="1", filled_avg_price="509.9"),
        _order("o2", "filled", type="market", qty=None, notional="250", limit_price=None,
               filled_qty="0.49", filled_avg_price="510.2", side="SELL"),
        {"symbol": "AAPL"},
    ])
    assert [o["id"] for o in out] == ["o1", "o2"]
    assert out[0] == {
        "id": "o1", "symbol": "QQQ", "side": "buy", "type": "limit",
        "status": "partially_filled", "qty": 3.0, "notional": None, "filled_qty": 1.0,
        "filled_avg_price": 509.9, "limit_price": 510.5, "stop_price": None,
        "time_in_force": "day", "submitted_at": "2026-11-12T14:31:05.123456Z",
        "filled_at": None, "canceled_at": None, "asset_class": "us_equity",
    }
    assert out[1]["qty"] is None and out[1]["notional"] == 250.0
    assert out[1]["side"] == "sell" and out[1]["limit_price"] is None


def test_order_book_splits_open_and_recent_and_drops_replaced(client, monkeypatch):
    c, api_main = client
    calls = []
    _stub_alpaca(
        monkeypatch, api_main, calls=calls,
        open_orders=[_order("o1", "new")],
        closed_orders=[_order("c1", "filled"), _order("c2", "replaced"),
                       _order("c3", "canceled"), _order("c4", "expired")],
    )
    r = c.get("/brokerages/b1/order-book?recent_limit=2")
    assert r.status_code == 200
    body = r.json()
    assert body["supported"] is True
    assert [o["id"] for o in body["open"]] == ["o1"]
    assert [o["id"] for o in body["recent"]] == ["c1", "c3"]  # replaced dropped, limit 2

    # Two read-only GETs on /v2/orders against the row's base URL, keys in headers.
    assert [(u, p["status"], p["direction"]) for u, p, _ in calls] == [
        ("https://paper-api.alpaca.markets/v2/orders", "open", "desc"),
        ("https://paper-api.alpaca.markets/v2/orders", "closed", "desc"),
    ]
    assert calls[0][2] == {"APCA-API-KEY-ID": "k", "APCA-API-SECRET-KEY": "s"}


def test_order_book_caches_briefly(client, monkeypatch):
    c, api_main = client
    calls = []
    _stub_alpaca(monkeypatch, api_main, calls=calls, open_orders=[], closed_orders=[])
    assert c.get("/brokerages/b1/order-book").status_code == 200
    assert c.get("/brokerages/b1/order-book").status_code == 200
    assert len(calls) == 2  # second request served from the cache


def test_order_book_non_alpaca_is_unsupported(client, monkeypatch):
    c, api_main = client
    monkeypatch.setattr(api_main.db_store, "get",
                        lambda table, key: {"id": "k1", "brokerage_type": "kalshi"})
    r = c.get("/brokerages/k1/order-book")
    assert r.status_code == 200
    assert r.json() == {"brokerage_id": "k1", "supported": False, "open": [], "recent": []}


def test_order_book_unknown_brokerage_404(client, monkeypatch):
    c, api_main = client
    monkeypatch.setattr(api_main.db_store, "get", lambda table, key: None)
    assert c.get("/brokerages/nope/order-book").status_code == 404


def test_order_book_alpaca_failure_is_502_without_caching(client, monkeypatch):
    c, api_main = client
    calls = []
    _stub_alpaca(monkeypatch, api_main, calls=calls, open_orders=[], closed_orders=[], status=500)
    r = c.get("/brokerages/b1/order-book")
    assert r.status_code == 502
    assert r.json()["detail"] == "Couldn't load orders from Alpaca"
    assert api_main._ORDER_BOOK_CACHE == {}
