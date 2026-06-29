"""Kalshi v2 REST client with RSA-PSS auth. WebSocket prices/fills are exposed
via `ws_url()` + `ws_headers()` for a consumer to connect (websocket-client is
available in the env).

Environments: 'demo' -> demo-api.kalshi.co (paper), 'live'/'prod' -> production.
The exact production host is configurable and must be confirmed in Phase 0; the
signed path is always the full `/trade-api/v2/...` path WITHOUT query string.

The transport (a `requests`-like session with `.request(method, url, headers,
params, json)`) is injectable so request construction is unit-tested without
network or credentials.
"""
from __future__ import annotations

import time
import uuid as _uuid
from typing import Any, Optional

from kalshi.signing import access_headers
from kalshi.models import (
    KalshiBalance,
    KalshiContractPosition,
    KalshiFill,
    KalshiOrderRef,
    KalshiMarket,
)

_API_PREFIX = "/trade-api/v2"
_HOSTS = {
    # demo-api.kalshi.co is deprecated — it still serves reads but returns 410 Gone
    # on order placement. The current demo Trade API root is external-api.demo.kalshi.co.
    "demo": "https://external-api.demo.kalshi.co",
    "live": "https://api.elections.kalshi.com",
    "prod": "https://api.elections.kalshi.com",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _fp(v) -> float:
    """Parse a Kalshi fixed-point string/number (e.g. '3.00') to float; 0 on junk."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _dollars_cents(v) -> float:
    """Dollar amount (string/number, e.g. '1.56') -> cents (156.0)."""
    return _fp(v) * 100.0


class KalshiHTTPError(RuntimeError):
    """Typed Kalshi API error. Subclasses RuntimeError so existing callers that
    `except RuntimeError` / `except Exception` keep working. `status` lets callers
    branch (e.g. 422 insufficient_balance is terminal, 503 is retryable)."""
    RETRYABLE = {429, 500, 502, 503, 504}

    def __init__(self, status: int, detail: str = "", message: str = ""):
        super().__init__(message or f"Kalshi HTTP {status}: {detail}")
        self.status = int(status)
        self.detail = detail


class KalshiClient:
    def __init__(
        self,
        *,
        key_id: str,
        private_key_pem: str,
        environment: str = "demo",
        session: Any = None,
        host: Optional[str] = None,
        timeout: float = 15.0,
    ):
        self.key_id = key_id
        self.private_key_pem = private_key_pem
        self.environment = environment
        self.host = host or _HOSTS.get(environment, _HOSTS["demo"])
        self.timeout = timeout
        if session is None:
            import requests
            session = requests.Session()
        self._session = session

    # --- low level ---
    def _request(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> Any:
        full_path = _API_PREFIX + path
        ts = _now_ms()
        headers = access_headers(
            key_id=self.key_id,
            method=method,
            path=full_path,  # sign the bare path; access_headers strips any query
            ts_ms=ts,
            private_key_pem=self.private_key_pem,
        )
        headers["Content-Type"] = "application/json"
        url = self.host + full_path
        # Retry transient failures (429/5xx, exchange paused) with exponential
        # backoff; terminal errors (400/401/403/404/410/422 incl. insufficient_balance)
        # raise immediately — retrying them just wastes time and hammers the API.
        import time as _time
        for attempt in range(3):
            resp = self._session.request(
                method, url, headers=headers, params=params, json=body, timeout=self.timeout
            )
            status = getattr(resp, "status_code", 200)
            if status < 400:
                if getattr(resp, "content", None):
                    return resp.json()
                return {}
            detail = ""
            try:
                detail = resp.text[:400]
            except Exception:
                detail = ""
            retryable = (status in KalshiHTTPError.RETRYABLE
                         or "exchange_is_paused" in detail
                         or "temporarily" in detail.lower())
            if retryable and attempt < 2:
                _time.sleep(0.5 * (2 ** attempt))
                continue
            raise KalshiHTTPError(status, detail, f"Kalshi {method} {path} -> HTTP {status}: {detail}")
        raise KalshiHTTPError(0, "", f"Kalshi {method} {path} -> retries exhausted")

    # --- portfolio ---
    def get_balance(self) -> KalshiBalance:
        d = self._request("GET", "/portfolio/balance")
        cash = int(d.get("balance", 0))
        return KalshiBalance(
            cash_cents=cash,
            portfolio_value_cents=int(d.get("portfolio_value", cash)),
        )

    def market_price_cents(self, ticker: str):  # pragma: no cover - integration
        """Current YES price for a market in cents (mid of bid/ask, else last
        trade). None on failure. Used to mark positions to market."""
        try:
            d = self._request("GET", f"/markets/{ticker}")
            m = d.get("market", d) or {}
            bid = _dollars_cents(m.get("yes_bid_dollars", m.get("yes_bid", 0)))
            ask = _dollars_cents(m.get("yes_ask_dollars", m.get("yes_ask", 0)))
            if bid > 0 and ask > 0:
                return (bid + ask) / 2.0
            last = _dollars_cents(m.get("last_price_dollars", m.get("last_price", 0)))
            return last or (bid or ask) or None
        except Exception:
            return None

    def get_positions(self, *, with_price: bool = True) -> list[KalshiContractPosition]:
        d = self._request("GET", "/portfolio/positions")
        out = []
        for p in d.get("market_positions", []) or []:
            # New API: position_fp (fixed-point string) + market_exposure_dollars.
            # The legacy integer `position`/`market_exposure` fields are gone.
            qty = int(round(_fp(p.get("position_fp", p.get("position", 0)))))
            if qty == 0:
                continue
            exposure_cents = _dollars_cents(p.get("market_exposure_dollars", p.get("market_exposure", 0)))
            ticker = p.get("ticker", "")
            cur = self.market_price_cents(ticker) if with_price else None
            # market_price_cents is the YES mark; a NO contract marks at (100 - YES).
            # The bot trades YES-only, but a manual NO holding must price against its
            # own side or its value/odds/P&L would invert.
            if cur is not None and qty < 0:
                cur = 100.0 - cur
            out.append(
                KalshiContractPosition(
                    market_ticker=ticker,
                    side="YES" if qty > 0 else "NO",
                    contracts=abs(qty),
                    avg_price_cents=exposure_cents / max(abs(qty), 1),
                    current_price_cents=cur,
                )
            )
        return out

    def get_fills(self, limit: int = 100) -> list[KalshiFill]:
        d = self._request("GET", "/portfolio/fills", params={"limit": limit})
        out = []
        for f in (d.get("fills", []) or []):
            # New API: count_fp + yes_price_dollars/no_price_dollars (legacy
            # count/yes_price are gone -> they read as 0, hence "0x @ 0c").
            count = int(round(_fp(f.get("count_fp", f.get("count", 0)))))
            px = _dollars_cents(f.get("yes_price_dollars", f.get("yes_price", f.get("price", 0))))
            out.append(KalshiFill(
                market_ticker=f.get("ticker", ""),
                side=f.get("side", ""),
                action=f.get("action", ""),
                contracts=count,
                price_cents=int(round(px)),
                ts=f.get("created_time", ""),
            ))
        return out

    # --- markets ---
    def get_markets(self, event_ticker: str) -> list[KalshiMarket]:
        d = self._request("GET", "/markets", params={"event_ticker": event_ticker})
        out = []
        for m in d.get("markets", []) or []:
            out.append(
                KalshiMarket(
                    market_ticker=m.get("ticker", ""),
                    fixture_id=event_ticker,
                    side=str(m.get("yes_sub_title", "")).lower() or "yes",
                    yes_ask_cents=int(m.get("yes_ask", 0)),
                )
            )
        return out

    def get_orderbook(self, ticker: str) -> dict:
        return self._request("GET", f"/markets/{ticker}/orderbook")

    def list_markets(self, *, status: str = "open", series_ticker: str | None = None,
                     limit: int = 200, cursor: str | None = None) -> dict:
        """Discover markets. Returns the raw {markets:[...], cursor} so the
        caller can classify + paginate. Used by discovery to find soccer markets."""
        params = {"status": status, "limit": max(1, min(int(limit), 1000))}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/markets", params=params)

    def get_events(self, *, series_ticker: str | None = None, status: str = "open", limit: int = 200) -> dict:
        params = {"status": status, "limit": max(1, min(int(limit), 200))}
        if series_ticker:
            params["series_ticker"] = series_ticker
        return self._request("GET", "/events", params=params)

    # --- orders ---
    def submit_order(
        self,
        *,
        market_ticker: str,
        side: str,           # 'yes' | 'no'
        action: str,         # 'buy' | 'sell'
        contracts: int,
        limit_cents: int,
        client_order_id: str,
        post_only: bool = False,
    ) -> KalshiOrderRef:
        # V2 create-order (POST /portfolio/events/orders): the legacy
        # /portfolio/orders is deprecated and 410s on this API. V2 uses a single
        # YES book with fixed-point DOLLAR strings — `bid` = buy YES, `ask` = sell
        # YES (we only ever trade the YES leg). count/price are string fixed-point.
        book_side = "ask" if action == "sell" else "bid"
        # V2 validates client_order_id as a UUID (the legacy endpoint accepted any
        # string). Map our logical id to a deterministic UUID so retries of the same
        # order stay idempotent.
        try:
            cid = str(_uuid.UUID(client_order_id))
        except (ValueError, AttributeError, TypeError):
            cid = str(_uuid.uuid5(_uuid.NAMESPACE_OID, str(client_order_id)))
        body = {
            "ticker": market_ticker,
            "side": book_side,
            "count": f"{int(contracts)}.00",          # FixedPointCount (2dp per the spec example)
            "price": f"{int(limit_cents) / 100:.4f}",  # FixedPointDollars
            "time_in_force": "good_till_canceled",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": bool(post_only),   # maker-only: exchange cancels instead of crossing the spread
            "client_order_id": cid,
        }
        d = self._request("POST", "/portfolio/events/orders", body=body)
        o = d.get("order", d)
        return KalshiOrderRef(
            client_order_id=o.get("client_order_id", cid),
            broker_order_id=o.get("order_id"),
            market_ticker=market_ticker,
            side=side.upper(),
            action=action,
            contracts=contracts,
            limit_cents=limit_cents,
            status=o.get("status", "resting" if o.get("order_id") else "pending"),
        )

    def cancel_order(self, order_id: str) -> bool:
        # V2 family: the legacy /portfolio/orders 410s (it silently broke the
        # kill-switch). Use the same /portfolio/events/orders root as create.
        self._request("DELETE", f"/portfolio/events/orders/{order_id}")
        return True

    def get_settlements(self, limit: int = 200) -> list[dict]:
        """Settled markets (ground truth for reconcile): ticker, market_result,
        revenue, yes/no cost, settled_time. Empty list on any error."""
        try:
            d = self._request("GET", "/portfolio/settlements",
                              params={"limit": max(1, min(int(limit), 1000))})
        except Exception:
            return []
        return d.get("settlements", []) or []

    def list_open_orders(self) -> list[str]:
        d = self._request("GET", "/portfolio/events/orders", params={"status": "resting"})
        return [o.get("order_id") for o in (d.get("orders", []) or []) if o.get("order_id")]

    def get_resting_orders(self) -> list[dict]:
        """Resting (still-open, not-yet-filled) orders as dicts — the TRUE pending
        set. New-API fields are *_dollars / *_fp (with legacy fallbacks). Empty when
        nothing is resting."""
        try:
            d = self._request("GET", "/portfolio/events/orders", params={"status": "resting"})
        except Exception:
            return []
        out = []
        for o in (d.get("orders", []) or []):
            # Defense in depth: the ?status=resting param isn't always honored on
            # V2, so a FILLED order can come back in this list and wrongly show as
            # "pending" while it's actually an open position. Drop anything the
            # order itself doesn't call resting.
            st = str(o.get("status", "")).lower()
            if st and st != "resting":
                continue
            # Prefer the live remaining count; only fall back to the original size
            # when the API omits remaining entirely (a present-but-0 remaining means
            # the order filled — never resurrect it from `count`).
            rem = o.get("remaining_count_fp", o.get("remaining_count"))
            remaining = _fp(rem) if rem is not None else _fp(o.get("count", 0))
            if remaining <= 0:
                continue
            out.append({
                "market_ticker": o.get("ticker", ""),
                "side": "yes" if (o.get("side") in ("yes", "bid")) else o.get("side", ""),
                "contracts": int(round(remaining)),
                "price_cents": int(round(_dollars_cents(
                    o.get("yes_price_dollars", o.get("price_dollars", o.get("yes_price", 0)))))),
                "ts": o.get("created_time", ""),
            })
        return out

    def cancel_all_open_orders(self) -> int:
        """Kill-switch primitive: cancel every resting order. Returns the count
        canceled."""
        n = 0
        for oid in self.list_open_orders():
            try:
                if self.cancel_order(oid):
                    n += 1
            except Exception:
                continue
        return n

    # --- websocket ---
    def ws_url(self) -> str:
        scheme = "wss://" + self.host.split("://", 1)[-1]
        return scheme + _API_PREFIX + "/ws"

    def ws_headers(self) -> dict:
        ts = _now_ms()
        return access_headers(
            key_id=self.key_id,
            method="GET",
            path=_API_PREFIX + "/ws",
            ts_ms=ts,
            private_key_pem=self.private_key_pem,
        )
