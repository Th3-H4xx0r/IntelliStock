"""Binance.US REST client (spot).

Thin, dependency-light wrapper over the Binance.US spot REST API
(https://docs.binance.us/). Public market-data calls need no auth; account /
order calls are HMAC-SHA256 signed. Kept free of broker.py imports so it stays
import-safe and unit-testable.

Why Binance.US: spot fees are 0.00% maker / 0.02% taker (2026), vs Alpaca's
0.15%/0.25% — the only US venue where higher-frequency crypto trading survives
fees (see docs/superpowers/specs). The BrokerAdapter using this client applies
that fee model in paper mode and reads real fills in live mode.

Symbol mapping: the platform uses slashed pairs ("BTC/USD"); Binance uses
concatenated symbols ("BTCUSD", "BTCUSDT"). ``to_binance``/``from_binance``
convert, defaulting the "/USD" quote to Binance's USD market.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
from typing import Any, Optional

import requests

BINANCE_US_BASE = "https://api.binance.us"

# Fees (spot): 0% maker / 0.02% taker, all pairs, all users (2026).
BINANCE_US_FEES = {"maker": 0.0000, "taker": 0.0002}

# Quote currencies Binance.US lists, longest-first so "USDT" matches before "USD".
_QUOTES = ("USDT", "USDC", "USD", "BTC", "ETH")


def to_binance(symbol: str) -> str:
    """"BTC/USD" -> "BTCUSD" (Binance concatenated symbol). Idempotent."""
    return str(symbol or "").replace("/", "").upper()


def from_binance(symbol: str) -> str:
    """"BTCUSD" -> "BTC/USD". Splits on the known quote suffix (longest first)."""
    s = str(symbol or "").upper()
    if "/" in s:
        return s
    for q in _QUOTES:
        if s.endswith(q) and len(s) > len(q):
            return s[:-len(q)] + "/" + q
    return s


class BinanceUSError(Exception):
    """Binance.US API error (carries the HTTP status + Binance error code)."""

    def __init__(self, message: str, status: int = 0, code: Optional[int] = None):
        super().__init__(message)
        self.status = status
        self.code = code


class BinanceUSClient:
    def __init__(self, api_key: str = "", api_secret: str = "",
                 base_url: str = BINANCE_US_BASE, recv_window: int = 5000,
                 timeout: float = 15.0):
        self.api_key = api_key or ""
        self.api_secret = api_secret or ""
        self.base_url = base_url.rstrip("/")
        self.recv_window = int(recv_window)
        self.timeout = float(timeout)
        self._session = requests.Session()

    # ---- signing ---------------------------------------------------------
    def _sign(self, query: str) -> str:
        return hmac.new(self.api_secret.encode(), query.encode(),
                        hashlib.sha256).hexdigest()

    def _signed_query(self, params: dict, now_ms: Optional[int] = None) -> str:
        """Build the signed query string: params + timestamp/recvWindow + signature.
        ``now_ms`` is injectable for deterministic tests."""
        p = {k: v for k, v in (params or {}).items() if v is not None}
        p["timestamp"] = int(now_ms if now_ms is not None else time.time() * 1000)
        p["recvWindow"] = self.recv_window
        q = urllib.parse.urlencode(p)
        return q + "&signature=" + self._sign(q)

    # ---- HTTP ------------------------------------------------------------
    def _request(self, method: str, path: str, params: dict, signed: bool):
        headers = {"X-MBX-APIKEY": self.api_key} if (signed or self.api_key) else {}
        url = self.base_url + path
        if signed:
            query = self._signed_query(params)
            if method == "GET" or method == "DELETE":
                url = url + "?" + query
                r = self._session.request(method, url, headers=headers, timeout=self.timeout)
            else:  # POST: signed params go in the body
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                r = self._session.request(method, url, headers=headers, data=query, timeout=self.timeout)
        else:
            r = self._session.request(method, url, headers=headers,
                                      params={k: v for k, v in (params or {}).items() if v is not None},
                                      timeout=self.timeout)
        if r.status_code >= 400:
            code = None
            try:
                body = r.json()
                code = body.get("code")
                msg = body.get("msg") or r.text
            except Exception:
                msg = r.text
            raise BinanceUSError(f"{method} {path} -> {r.status_code}: {msg}", r.status_code, code)
        return r.json()

    # ---- public market data ---------------------------------------------
    def klines(self, symbol: str, interval: str = "1h",
               start_ms: Optional[int] = None, end_ms: Optional[int] = None,
               limit: int = 1000) -> list[dict]:
        """OHLCV bars as platform-shaped dicts {t,o,h,l,c,v}. ``interval`` is a
        Binance interval string (1m,5m,15m,1h,1d...)."""
        params = {"symbol": to_binance(symbol), "interval": interval, "limit": min(int(limit), 1000)}
        if start_ms is not None:
            params["startTime"] = int(start_ms)
        if end_ms is not None:
            params["endTime"] = int(end_ms)
        raw = self._request("GET", "/api/v3/klines", params, signed=False)
        out = []
        for k in raw:
            # [openTime, open, high, low, close, volume, closeTime, ...]
            out.append({
                "t": int(k[0]), "o": float(k[1]), "h": float(k[2]),
                "l": float(k[3]), "c": float(k[4]), "v": float(k[5]),
            })
        return out

    def price(self, symbol: str) -> float:
        d = self._request("GET", "/api/v3/ticker/price", {"symbol": to_binance(symbol)}, signed=False)
        return float(d["price"])

    def ping(self) -> bool:
        self._request("GET", "/api/v3/ping", {}, signed=False)
        return True

    # ---- signed account / orders ----------------------------------------
    def account(self) -> dict:
        return self._request("GET", "/api/v3/account", {}, signed=True)

    def balances(self) -> dict[str, float]:
        """Free balances by asset (e.g. {"BTC": 0.1, "USD": 500.0})."""
        acct = self.account()
        out = {}
        for b in acct.get("balances", []):
            free = float(b.get("free") or 0.0)
            if free != 0.0:
                out[b.get("asset")] = free
        return out

    def new_order(self, symbol: str, side: str, order_type: str = "MARKET",
                  quantity: Optional[float] = None, quote_qty: Optional[float] = None,
                  price: Optional[float] = None, tif: Optional[str] = None,
                  client_order_id: Optional[str] = None) -> dict:
        """Place a spot order. side='BUY'/'SELL'. For a MARKET buy use
        ``quote_qty`` (spend N quote), for a MARKET sell use ``quantity`` (sell N
        base). LIMIT requires ``quantity``+``price``+``tif``."""
        params: dict[str, Any] = {"symbol": to_binance(symbol), "side": side.upper(),
                                  "type": order_type.upper(), "newOrderRespType": "FULL"}
        if quantity is not None:
            params["quantity"] = quantity
        if quote_qty is not None:
            params["quoteOrderQty"] = quote_qty
        if price is not None:
            params["price"] = price
        if tif is not None:
            params["timeInForce"] = tif
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        return self._request("POST", "/api/v3/order", params, signed=True)

    def get_order(self, symbol: str, order_id: Optional[int] = None,
                  orig_client_order_id: Optional[str] = None) -> dict:
        params = {"symbol": to_binance(symbol)}
        if order_id is not None:
            params["orderId"] = order_id
        if orig_client_order_id is not None:
            params["origClientOrderId"] = orig_client_order_id
        return self._request("GET", "/api/v3/order", params, signed=True)

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        return self._request("DELETE", "/api/v3/order",
                             {"symbol": to_binance(symbol), "orderId": order_id}, signed=True)

    def open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        params = {"symbol": to_binance(symbol)} if symbol else {}
        return self._request("GET", "/api/v3/openOrders", params, signed=True)
