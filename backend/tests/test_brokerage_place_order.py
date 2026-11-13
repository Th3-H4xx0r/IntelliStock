"""POST /brokerages/{id}/orders and DELETE /brokerages/{id}/orders/{order_id} —
the operator's own buy/sell and cancel from the web and iOS stock pages.
Alpaca HTTP is mocked; nothing here reaches a broker."""
from types import SimpleNamespace

import pytest


PAPER_ROW = {
    "id": "b1",
    "brokerage_type": "alpaca",
    "alpaca_paper": True,
    "alpaca_base_url": "https://paper-api.alpaca.markets",
}
LIVE_ROW = {
    "id": "b2",
    "brokerage_type": "alpaca",
    "alpaca_paper": False,
    "alpaca_base_url": "https://api.alpaca.markets",
}


def _alpaca_order(**kw):
    base = {
        "id": "61e69015-8549-4bfd-b9c3-01e75843f47d", "client_order_id": "web-abc",
        "symbol": "AAPL", "side": "buy", "type": "market", "status": "accepted",
        "qty": "3", "notional": None, "filled_qty": "0", "filled_avg_price": None,
        "limit_price": None, "stop_price": None, "time_in_force": "day",
        "submitted_at": "2026-11-12T15:01:02.123456Z", "created_at": "2026-11-12T15:01:02.120000Z",
        "filled_at": None, "canceled_at": None, "asset_class": "us_equity",
    }
    base.update(kw)
    return base


class _Resp:
    def __init__(self, payload=None, status=200, text=""):
        self._payload, self.status_code, self.text = payload, status, text

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON")
        return self._payload


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
    rows = {"b1": PAPER_ROW, "b2": LIVE_ROW, "k1": {"id": "k1", "brokerage_type": "kalshi"}}
    monkeypatch.setattr(api_main.db_store, "get",
                        lambda table, key: dict(rows[key]) if key in rows else None)
    api_main.app.dependency_overrides[api_main.get_current_user] = lambda: {
        "id": "test-user", "username": "test"}
    yield TestClient(api_main.app), api_main
    api_main.app.dependency_overrides.clear()
    api_main._ORDER_BOOK_CACHE.clear()


def _stub_post(monkeypatch, calls, response=None, exc=None):
    import requests

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, dict(json or {}), dict(headers or {})))
        if exc is not None:
            raise exc
        return response

    monkeypatch.setattr(requests, "post", fake_post)


def _stub_delete(monkeypatch, calls, response=None, exc=None):
    import requests

    def fake_delete(url, headers=None, timeout=None):
        calls.append((url, dict(headers or {})))
        if exc is not None:
            raise exc
        return response

    monkeypatch.setattr(requests, "delete", fake_delete)


def _no_network(monkeypatch):
    import requests

    def boom(*a, **k):
        raise AssertionError("must not reach Alpaca")

    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(requests, "delete", boom)


# ── Validation (pure) ─────────────────────────────────────────────────────────

def test_build_manual_order_market_shares():
    from api.main import build_manual_order

    order = build_manual_order({"symbol": " aapl ", "side": "buy", "qty": 3, "client_order_id": "web-1"})
    assert order == {
        "symbol": "AAPL", "side": "buy", "type": "market", "time_in_force": "day",
        "client_order_id": "web-1", "qty": "3",
    }


def test_build_manual_order_limit_gtc_and_extended_hours():
    from api.main import build_manual_order

    gtc = build_manual_order({"symbol": "MSFT", "side": "sell", "qty": 2, "type": "limit",
                              "limit_price": 412.5, "time_in_force": "gtc"})
    assert gtc["type"] == "limit" and gtc["limit_price"] == "412.5" and gtc["time_in_force"] == "gtc"
    assert "extended_hours" not in gtc

    ext = build_manual_order({"symbol": "BRK.B", "side": "buy", "qty": 1, "type": "limit",
                              "limit_price": 0.5123, "extended_hours": True})
    assert ext["symbol"] == "BRK.B" and ext["limit_price"] == "0.5123" and ext["extended_hours"] is True


def test_build_manual_order_notional_and_exact_fractional_qty():
    from api.main import build_manual_order

    dollars = build_manual_order({"symbol": "NVDA", "side": "buy", "notional": 250})
    assert dollars["notional"] == "250" and "qty" not in dollars

    # "Sell all" sends the held quantity exactly; a dollar order fills to 9 decimals.
    held = build_manual_order({"symbol": "NVDA", "side": "sell", "qty": 1.358695652})
    assert held["qty"] == "1.358695652"


def test_build_manual_order_generates_client_order_id():
    from api.main import build_manual_order

    a = build_manual_order({"symbol": "SPY", "side": "buy", "qty": 1})
    b = build_manual_order({"symbol": "SPY", "side": "buy", "qty": 1})
    assert a["client_order_id"].startswith("app-") and len(a["client_order_id"]) == 36
    assert a["client_order_id"] != b["client_order_id"]


@pytest.mark.parametrize("payload, message", [
    ("nope", "Send the order as a JSON object"),
    ({"side": "buy", "qty": 1}, "Enter a stock symbol"),
    ({"symbol": "AAPL240119C00150000", "side": "buy", "qty": 1}, "Enter a stock symbol like AAPL"),
    ({"symbol": "BTC/USD", "side": "buy", "qty": 1}, "Enter a stock symbol like AAPL"),
    ({"symbol": "AAPL", "side": "short", "qty": 1}, "Choose buy or sell"),
    ({"symbol": "AAPL", "side": "buy"}, "Enter a number of shares or a dollar amount"),
    ({"symbol": "AAPL", "side": "buy", "qty": 1, "notional": 100}, "Enter shares or a dollar amount, not both"),
    ({"symbol": "AAPL", "side": "buy", "qty": 0}, "Shares must be more than zero"),
    ({"symbol": "AAPL", "side": "buy", "qty": -2}, "Shares must be more than zero"),
    ({"symbol": "AAPL", "side": "buy", "qty": True}, "Shares must be a number"),
    ({"symbol": "AAPL", "side": "buy", "qty": "abc"}, "Shares must be a number"),
    ({"symbol": "AAPL", "side": "buy", "qty": float("nan")}, "Shares must be a number"),
    ({"symbol": "AAPL", "side": "buy", "qty": 0.0000000001}, "Shares can have at most 9 decimal places"),
    ({"symbol": "AAPL", "side": "buy", "notional": 0}, "The dollar amount must be more than zero"),
    ({"symbol": "AAPL", "side": "buy", "notional": 10.005}, "The dollar amount can't go past cents"),
    ({"symbol": "AAPL", "side": "buy", "notional": 100, "type": "limit", "limit_price": 10},
     "Dollar amounts work with market orders only"),
    ({"symbol": "AAPL", "side": "buy", "notional": 100, "time_in_force": "gtc"}, "Dollar amounts are day orders only"),
    ({"symbol": "AAPL", "side": "buy", "qty": 1.5, "time_in_force": "gtc"}, "Fractional shares are day orders only"),
    ({"symbol": "AAPL", "side": "buy", "qty": 1, "type": "stop"}, "Order type must be market or limit"),
    ({"symbol": "AAPL", "side": "buy", "qty": 1, "time_in_force": "ioc"}, "Time in force must be day or gtc"),
    ({"symbol": "AAPL", "side": "buy", "qty": 1, "type": "limit"}, "Enter a limit price above zero"),
    ({"symbol": "AAPL", "side": "buy", "qty": 1, "type": "limit", "limit_price": 0}, "Enter a limit price above zero"),
    ({"symbol": "AAPL", "side": "buy", "qty": 1, "type": "limit", "limit_price": 190.123},
     "Limit prices of $1 or more go to the cent"),
    ({"symbol": "AAPL", "side": "buy", "qty": 1, "limit_price": 190}, "A market order doesn't take a limit price"),
    ({"symbol": "AAPL", "side": "buy", "qty": 1, "extended_hours": True}, "Extended hours needs a limit order for the day"),
    ({"symbol": "AAPL", "side": "buy", "qty": 1, "type": "limit", "limit_price": 190, "time_in_force": "gtc",
      "extended_hours": True}, "Extended hours needs a limit order for the day"),
    ({"symbol": "AAPL", "side": "buy", "qty": 1, "extended_hours": "yes"}, "extended_hours must be true or false"),
    ({"symbol": "AAPL", "side": "buy", "qty": 1, "client_order_id": "has space"}, "client_order_id must be"),
    ({"symbol": "AAPL", "side": "buy", "qty": 1, "client_order_id": "x" * 49}, "client_order_id must be"),
])
def test_build_manual_order_rejects(payload, message):
    from api.main import ManualOrderError, build_manual_order

    with pytest.raises(ManualOrderError) as err:
        build_manual_order(payload)
    assert str(err.value).startswith(message)


# ── POST /brokerages/{id}/orders ──────────────────────────────────────────────

def test_place_order_sends_once_and_returns_the_order(client, monkeypatch):
    c, api_main = client
    calls = []
    _stub_post(monkeypatch, calls, _Resp(_alpaca_order()))
    r = c.post("/brokerages/b1/orders", json={"symbol": "aapl", "side": "buy", "qty": 3,
                                              "client_order_id": "web-abc"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["brokerage_id"] == "b1"
    assert body["order"]["id"] == "61e69015-8549-4bfd-b9c3-01e75843f47d"
    assert body["order"]["status"] == "accepted" and body["order"]["qty"] == 3.0
    assert body["order"]["symbol"] == "AAPL" and body["order"]["side"] == "buy"

    assert len(calls) == 1
    url, sent, headers = calls[0]
    assert url == "https://paper-api.alpaca.markets/v2/orders"
    assert sent == {"symbol": "AAPL", "side": "buy", "type": "market", "time_in_force": "day",
                    "client_order_id": "web-abc", "qty": "3"}
    assert headers == {"APCA-API-KEY-ID": "k", "APCA-API-SECRET-KEY": "s"}


def test_place_order_live_account_uses_the_live_endpoint(client, monkeypatch):
    c, _ = client
    calls = []
    _stub_post(monkeypatch, calls, _Resp(_alpaca_order(notional="250", qty=None)))
    r = c.post("/brokerages/b2/orders", json={"symbol": "NVDA", "side": "buy", "notional": 250})
    assert r.status_code == 200
    assert calls[0][0] == "https://api.alpaca.markets/v2/orders"
    assert calls[0][1]["notional"] == "250"


def test_place_order_refuses_a_url_that_contradicts_the_paper_flag(client, monkeypatch):
    c, api_main = client
    _no_network(monkeypatch)
    mismatched = dict(PAPER_ROW, alpaca_base_url="https://api.alpaca.markets")
    monkeypatch.setattr(api_main.db_store, "get", lambda table, key: dict(mismatched))
    r = c.post("/brokerages/b1/orders", json={"symbol": "AAPL", "side": "buy", "qty": 1})
    assert r.status_code == 400
    assert "doesn't match its paper or live setting" in r.json()["detail"]


def test_place_order_validation_is_400_and_never_reaches_alpaca(client, monkeypatch):
    c, _ = client
    _no_network(monkeypatch)
    r = c.post("/brokerages/b1/orders", json={"symbol": "AAPL", "side": "buy", "qty": 1, "notional": 5})
    assert r.status_code == 400
    assert r.json() == {"detail": "Enter shares or a dollar amount, not both"}


def test_place_order_unknown_and_non_alpaca_brokerage(client, monkeypatch):
    c, _ = client
    _no_network(monkeypatch)
    r = c.post("/brokerages/nope/orders", json={"symbol": "AAPL", "side": "buy", "qty": 1})
    assert r.status_code == 404 and r.json() == {"detail": "Brokerage not found"}
    r = c.post("/brokerages/k1/orders", json={"symbol": "AAPL", "side": "buy", "qty": 1})
    assert r.status_code == 400
    assert r.json() == {"detail": "Trading from the app supports Alpaca accounts only"}


@pytest.mark.parametrize("status", [403, 422])
def test_place_order_alpaca_refusal_passes_the_message_through(client, monkeypatch, status):
    c, _ = client
    calls = []
    _stub_post(monkeypatch, calls, _Resp({"code": 40310000, "message": "insufficient buying power"}, status))
    r = c.post("/brokerages/b1/orders", json={"symbol": "AAPL", "side": "buy", "qty": 1000})
    assert r.status_code == 400
    assert r.json() == {"detail": "Alpaca rejected the order: insufficient buying power"}
    assert len(calls) == 1  # never retried


def test_place_order_network_failure_is_502_and_not_retried(client, monkeypatch):
    import requests
    c, api_main = client
    calls = []
    api_main._ORDER_BOOK_CACHE[("b1", 25)] = (10**12, {"stale": True})
    _stub_post(monkeypatch, calls, exc=requests.exceptions.ConnectTimeout("slow"))
    r = c.post("/brokerages/b1/orders", json={"symbol": "AAPL", "side": "buy", "qty": 1})
    assert r.status_code == 502
    assert r.json()["detail"] == (
        "Couldn't reach Alpaca. The order was not confirmed — check Orders before trying again")
    assert len(calls) == 1
    # The order may exist, so the next order-book read goes to Alpaca.
    assert ("b1", 25) not in api_main._ORDER_BOOK_CACHE


def test_place_order_alpaca_5xx_is_502(client, monkeypatch):
    c, _ = client
    _stub_post(monkeypatch, [], _Resp(None, 503, "upstream down"))
    r = c.post("/brokerages/b1/orders", json={"symbol": "AAPL", "side": "buy", "qty": 1})
    assert r.status_code == 502


def test_place_order_clears_only_that_brokerages_order_book_cache(client, monkeypatch):
    c, api_main = client
    api_main._ORDER_BOOK_CACHE[("b1", 25)] = (10**12, {"stale": True})
    api_main._ORDER_BOOK_CACHE[("b1", 100)] = (10**12, {"stale": True})
    api_main._ORDER_BOOK_CACHE[("b2", 25)] = (10**12, {"other": True})
    _stub_post(monkeypatch, [], _Resp(_alpaca_order()))
    r = c.post("/brokerages/b1/orders", json={"symbol": "AAPL", "side": "buy", "qty": 3})
    assert r.status_code == 200
    assert list(api_main._ORDER_BOOK_CACHE) == [("b2", 25)]


def test_place_order_logs_one_line_without_keys(client, monkeypatch, caplog):
    import logging
    c, _ = client
    _stub_post(monkeypatch, [], _Resp(_alpaca_order()))
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        c.post("/brokerages/b1/orders", json={"symbol": "AAPL", "side": "buy", "qty": 3})
    lines = [rec.getMessage() for rec in caplog.records if "manual-order" in rec.getMessage()]
    assert len(lines) == 1
    assert "AAPL" in lines[0] and "61e69015-8549-4bfd-b9c3-01e75843f47d" in lines[0] and "b1" in lines[0]
    assert "APCA" not in lines[0]


# ── DELETE /brokerages/{id}/orders/{order_id} ────────────────────────────────

def test_cancel_order(client, monkeypatch):
    c, api_main = client
    calls = []
    api_main._ORDER_BOOK_CACHE[("b1", 25)] = (10**12, {"stale": True})
    _stub_delete(monkeypatch, calls, _Resp(None, 204))
    r = c.delete("/brokerages/b1/orders/61e69015-8549-4bfd-b9c3-01e75843f47d")
    assert r.status_code == 200
    assert r.json() == {"brokerage_id": "b1", "order_id": "61e69015-8549-4bfd-b9c3-01e75843f47d",
                        "canceled": True}
    assert calls == [("https://paper-api.alpaca.markets/v2/orders/61e69015-8549-4bfd-b9c3-01e75843f47d",
                      {"APCA-API-KEY-ID": "k", "APCA-API-SECRET-KEY": "s"})]
    assert api_main._ORDER_BOOK_CACHE == {}


@pytest.mark.parametrize("status", [404, 422])
def test_cancel_order_alpaca_refusal(client, monkeypatch, status):
    c, _ = client
    _stub_delete(monkeypatch, [], _Resp({"code": 42210000, "message": "order is already in \"filled\" state"}, status))
    r = c.delete("/brokerages/b1/orders/o-1")
    assert r.status_code == 400
    assert r.json() == {"detail": "Alpaca couldn't cancel this order: order is already in \"filled\" state"}


def test_cancel_order_network_failure_is_502(client, monkeypatch):
    import requests
    c, _ = client
    _stub_delete(monkeypatch, [], exc=requests.exceptions.ConnectionError("down"))
    r = c.delete("/brokerages/b1/orders/o-1")
    assert r.status_code == 502


def test_cancel_order_rejects_odd_ids_and_bad_brokerages(client, monkeypatch):
    c, _ = client
    _no_network(monkeypatch)
    assert c.delete("/brokerages/b1/orders/abc..def").status_code == 400
    assert c.delete("/brokerages/nope/orders/o-1").status_code == 404
    r = c.delete("/brokerages/k1/orders/o-1")
    assert r.status_code == 400
    assert r.json() == {"detail": "Trading from the app supports Alpaca accounts only"}
