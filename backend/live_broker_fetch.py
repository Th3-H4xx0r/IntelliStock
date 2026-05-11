"""Direct broker fetch for the Live Trading API endpoint.

Bypasses the LiveState row by querying Alpaca / Robinhood directly so the UI
keeps showing fresh equity / positions / portfolio_history when the instance
container is stalled. Public API: ``fetch_broker_live_state(conn, instance_id)``.
TradingClient/RobinhoodClient cached per-instance and recycled on cred-hash
change; portfolio_history cached for ``_PORTFOLIO_HISTORY_TTL_SEC`` seconds.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from rethinkdb import RethinkDB
from secret_store import decrypt


_DB_NAME = "IntelliStock"
_PORTFOLIO_HISTORY_TTL_SEC = float(os.environ.get("LIVE_BROKER_PH_TTL_SEC", "60"))
_MAX_RECENT_TRADES = int(os.environ.get("LIVE_STATE_MAX_RECENT_TRADES", "100"))
_MAX_PORTFOLIO_HISTORY = int(os.environ.get("LIVE_STATE_MAX_PORTFOLIO_HISTORY", "500"))
_HTTP_TIMEOUT_SEC = float(os.environ.get("LIVE_BROKER_HTTP_TIMEOUT_SEC", "10"))

_instance_locks: dict[str, threading.RLock] = {}
_instance_locks_guard = threading.Lock()
_client_cache: dict[tuple[str, str], dict] = {}
_ph_cache: dict[str, dict] = {}

_ALPACA_LIVE_BASE = "https://api.alpaca.markets"
_ALPACA_PAPER_BASE = "https://paper-api.alpaca.markets"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _instance_lock(instance_id: str) -> threading.RLock:
    with _instance_locks_guard:
        lk = _instance_locks.get(instance_id)
        if lk is None:
            lk = threading.RLock()
            _instance_locks[instance_id] = lk
        return lk


def _hash_creds(*parts: Optional[str]) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _open_db_conn():
    rdb = RethinkDB()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    return rdb, rdb.connect(host=host, port=port)


def _load_credentials(instance_id: str) -> dict:
    """Load broker credentials for this instance from RethinkDB.

    Returns a dict with ``error`` (None on success) plus broker-specific fields.
    Mirrors broker._load_live_credentials_from_db without importing broker.py.
    """
    out: dict[str, Any] = {"error": None, "broker_type": "alpaca", "paper": True, "brokerage_id": None}
    try:
        rdb, conn = _open_db_conn()
    except Exception as e:
        out["error"] = f"db_connect_failed: {type(e).__name__}: {e}"
        return out
    try:
        try:
            inst = rdb.db(_DB_NAME).table("Instances").get(str(instance_id)).run(conn)
        except Exception as e:
            out["error"] = f"instance_lookup_failed: {type(e).__name__}: {e}"
            return out
        if not inst:
            out["error"] = "instance_not_found"
            return out
        brokerage_id = inst.get("brokerage_id") or None
        broker_type = (inst.get("broker_type") or "alpaca").strip().lower()
        paper_field = inst.get("alpaca_paper")
        out["brokerage_id"] = brokerage_id
        out["broker_type"] = broker_type
        if broker_type == "robinhood":
            out["paper"] = False
        else:
            out["paper"] = True if paper_field is None else bool(paper_field)
        if not brokerage_id:
            # Legacy instance-level credentials fallback (mirrors broker.py:348-356).
            try:
                k = decrypt(inst.get("key")) or None
                s = decrypt(inst.get("secret")) or None
            except Exception as e:
                out["error"] = f"legacy_creds_decrypt_failed: {type(e).__name__}: {e}"
                return out
            if k and s:
                out["key"] = k
                out["secret"] = s
                return out
            out["error"] = "no_brokerage_linked_and_no_legacy_creds"
            return out
        try:
            b = rdb.db(_DB_NAME).table("BrokerageAccounts").get(brokerage_id).run(conn)
        except Exception as e:
            out["error"] = f"brokerage_lookup_failed: {type(e).__name__}: {e}"
            return out
        if not b:
            out["error"] = "brokerage_account_missing"
            return out
        broker_type = (b.get("brokerage_type") or broker_type or "alpaca").strip().lower()
        out["broker_type"] = broker_type
        if broker_type == "robinhood":
            out["paper"] = False
        else:
            b_paper = b.get("alpaca_paper")
            if b_paper is not None:
                out["paper"] = bool(b_paper)
        if broker_type == "alpaca":
            try:
                k = decrypt(b.get("alpaca_key")) or None
                s = decrypt(b.get("alpaca_secret")) or None
            except Exception as e:
                out["error"] = f"creds_decrypt_failed: {type(e).__name__}: {e}"
                return out
            if not k or not s:
                out["error"] = "alpaca_creds_missing"
                return out
            out["key"] = k
            out["secret"] = s
            return out
        if broker_type == "robinhood":
            try:
                acc_tok = decrypt(b.get("robinhood_access_token")) or None
                ref_tok = decrypt(b.get("robinhood_refresh_token")) or None
            except Exception as e:
                out["error"] = f"creds_decrypt_failed: {type(e).__name__}: {e}"
                return out
            try:
                dev_tok = decrypt(b.get("robinhood_device_token")) or None
            except Exception:
                dev_tok = b.get("robinhood_device_token") or None
            try:
                obtained_at = int(b.get("robinhood_obtained_at_epoch")) if b.get("robinhood_obtained_at_epoch") is not None else None
            except Exception:
                obtained_at = None
            try:
                expires_in = int(b.get("robinhood_expires_in")) if b.get("robinhood_expires_in") is not None else None
            except Exception:
                expires_in = None
            if not acc_tok:
                out["error"] = "robinhood_access_token_missing"
                return out
            out["access_token"] = acc_tok
            out["refresh_token"] = ref_tok
            out["device_token"] = dev_tok
            out["account_number"] = (b.get("robinhood_account_number") or "").strip() or None
            out["account_url"] = (b.get("robinhood_account_url") or "").strip() or None
            out["obtained_at_epoch"] = obtained_at
            out["expires_in"] = expires_in
            # Required by the Bonfire portfolio-history helper (see Bug B).
            out["token_type"] = b.get("robinhood_token_type") or "Bearer"
            return out
        out["error"] = f"unsupported_broker_type: {broker_type}"
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Alpaca ──────────────────────────────────────────────────────────────────

def _get_alpaca_client(instance_id: str, creds: dict):
    """Return cached TradingClient, rebuilding on credential rotation."""
    from alpaca.trading.client import TradingClient
    cache_key = (instance_id, "alpaca")
    cred_hash = _hash_creds(creds.get("key"), creds.get("secret"), str(creds.get("paper")))
    entry = _client_cache.get(cache_key)
    if entry and entry.get("cred_hash") == cred_hash:
        return entry["client"]
    client = TradingClient(api_key=creds["key"], secret_key=creds["secret"], paper=bool(creds.get("paper", True)))
    _client_cache[cache_key] = {"client": client, "cred_hash": cred_hash}
    return client


def _alpaca_portfolio_history(creds: dict) -> list[dict]:
    """Fetch /v2/account/portfolio/history (1M / 15Min) and map to the
    {ts, value} shape the UI consumes."""
    base = _ALPACA_PAPER_BASE if creds.get("paper", True) else _ALPACA_LIVE_BASE
    headers = {"APCA-API-KEY-ID": creds["key"], "APCA-API-SECRET-KEY": creds["secret"]}
    params = {"period": "1M", "timeframe": "15Min", "extended_hours": "true"}
    resp = requests.get(f"{base}/v2/account/portfolio/history", headers=headers, params=params, timeout=_HTTP_TIMEOUT_SEC)
    resp.raise_for_status()
    body = resp.json() or {}
    out: list[dict] = []
    for ts, eq in zip(body.get("timestamp") or [], body.get("equity") or []):
        if eq is None:
            continue
        try:
            iso = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            out.append({"ts": iso, "value": float(eq)})
        except Exception:
            continue
    return out[-_MAX_PORTFOLIO_HISTORY:]


def _alpaca_recent_trades(client, limit: int = 50) -> list[dict]:
    """Fetch most recent closed orders, map filled ones to LiveState shape."""
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=int(limit))
        orders = client.get_orders(filter=req) or []
    except Exception:
        return []
    out: list[dict] = []
    for o in orders:
        try:
            status = str(getattr(o.status, "value", o.status) or "").lower()
            if status != "filled":
                continue
            side = str(getattr(o.side, "value", o.side) or "").lower()
            ts = getattr(o, "filled_at", None) or getattr(o, "submitted_at", None)
            if hasattr(ts, "isoformat"):
                ts = ts.isoformat()
            out.append({
                "ts": ts,
                "symbol": str(getattr(o, "symbol", "") or "").upper() or None,
                "side": "buy" if side == "buy" else "sell",
                "qty": float(getattr(o, "filled_qty", 0.0) or 0.0),
                "price": float(getattr(o, "filled_avg_price", 0.0) or 0.0),
                "order_id": str(getattr(o, "id", "") or "") or None,
            })
        except Exception:
            continue
    return out[:_MAX_RECENT_TRADES]


def _fetch_alpaca(instance_id: str, creds: dict) -> dict:
    """Build the broker-state dict from Alpaca."""
    client = _get_alpaca_client(instance_id, creds)
    acct = client.get_account()
    cash = float(getattr(acct, "cash", 0.0) or 0.0)
    equity = float(getattr(acct, "equity", 0.0) or 0.0)
    buying_power = float(getattr(acct, "buying_power", 0.0) or 0.0)
    last_equity = float(getattr(acct, "last_equity", 0.0) or 0.0)
    account_id = str(getattr(acct, "account_number", "") or getattr(acct, "id", "") or "") or None

    positions_payload: list[dict] = []
    try:
        positions = client.get_all_positions() or []
    except Exception:
        positions = []
    for p in positions:
        try:
            qty_f = float(getattr(p, "qty", 0.0) or 0.0)
            avg_entry = float(getattr(p, "avg_entry_price", 0.0) or 0.0)
            market_value = float(getattr(p, "market_value", 0.0) or 0.0)
            price = market_value / qty_f if qty_f else 0.0
            unrealized = (price - avg_entry) * qty_f if avg_entry and price else 0.0
            unrealized_pct = ((price / avg_entry) - 1.0) * 100.0 if avg_entry else 0.0
            positions_payload.append({
                "symbol": str(getattr(p, "symbol", "") or "").upper(),
                "qty": qty_f,
                "avg_entry_price": avg_entry if avg_entry else None,
                "last_price": price,
                "market_value": market_value,
                "unrealized_pnl": unrealized,
                "unrealized_pnl_pct": unrealized_pct,
            })
        except Exception:
            continue

    recent_trades = _alpaca_recent_trades(client)
    ph = _portfolio_history_cached(instance_id, lambda: _alpaca_portfolio_history(creds))
    initial_value = ph[0]["value"] if ph else equity
    total_pnl = equity - initial_value if initial_value else 0.0
    total_pnl_pct = ((equity / initial_value) - 1.0) * 100.0 if initial_value > 0 else 0.0
    if last_equity > 0 and equity > 0:
        day_pnl = equity - last_equity
        day_pnl_pct = ((equity / last_equity) - 1.0) * 100.0
    else:
        day_pnl = 0.0
        day_pnl_pct = 0.0

    return {
        "cash": cash, "equity": equity, "buying_power": buying_power, "last_equity": last_equity,
        "initial_value": initial_value, "day_pnl": day_pnl, "day_pnl_pct": day_pnl_pct,
        "total_pnl": total_pnl, "total_pnl_pct": total_pnl_pct,
        "positions": positions_payload, "recent_trades": recent_trades, "portfolio_history": ph,
        "broker": {"name": "AlpacaAdapter", "account_id": account_id,
                   "paper": bool(creds.get("paper", True)),
                   "health": {"auth_fresh": True, "errors": []}},
    }


# ── Robinhood ───────────────────────────────────────────────────────────────

def _get_robinhood_client(instance_id: str, creds: dict):
    """Return cached RobinhoodClient, rebuilding on credential rotation."""
    from robinhood_engine import RobinhoodClient, RobinhoodSessionState
    cache_key = (instance_id, "robinhood")
    cred_hash = _hash_creds(
        creds.get("access_token"), creds.get("refresh_token"),
        creds.get("account_number"), creds.get("device_token"),
    )
    entry = _client_cache.get(cache_key)
    if entry and entry.get("cred_hash") == cred_hash:
        return entry["client"]
    state = RobinhoodSessionState(
        access_token=creds.get("access_token"), refresh_token=creds.get("refresh_token"),
        device_token=creds.get("device_token"), account_number=creds.get("account_number"),
        account_url=creds.get("account_url"), obtained_at_epoch=creds.get("obtained_at_epoch"),
        expires_in=creds.get("expires_in"),
    )
    client = RobinhoodClient(state=state)
    _client_cache[cache_key] = {"client": client, "cred_hash": cred_hash}
    return client


def _robinhood_portfolio_history(creds: dict) -> list[dict]:
    """Bonfire endpoint — legacy /portfolios/historicals/ returns empty for new accounts."""
    acc_num = (creds.get("account_number") or "").strip()
    if not acc_num:
        return []
    try:
        from interactive_utils import _fetch_robinhood_portfolio_history
        raw = _fetch_robinhood_portfolio_history(
            access_token=creds.get("access_token") or "",
            token_type=creds.get("token_type") or "Bearer",
            account_number=acc_num,
            range_str="1M",
        )
    except Exception:
        return []
    timestamps = raw.get("timestamps") or []
    values = raw.get("values") or []
    out: list[dict] = []
    for t_ms, v in zip(timestamps, values):
        try:
            dt = datetime.fromtimestamp(int(t_ms) / 1000, tz=timezone.utc)
            out.append({"ts": dt.isoformat(), "value": float(v)})
        except Exception:
            continue
    return out[-_MAX_PORTFOLIO_HISTORY:]


def _robinhood_recent_trades(client, limit: int = 50) -> list[dict]:
    """Pull recent filled RH orders; map to LiveState shape."""
    try:
        orders = client.list_orders(limit=int(limit), state="filled") or []
    except Exception:
        return []
    out: list[dict] = []
    instr_to_sym: dict[str, str] = {}
    for o in orders:
        try:
            if (o.get("state") or "").strip().lower() != "filled":
                continue
            instr = (o.get("instrument") or "").strip()
            sym = instr_to_sym.get(instr)
            if not sym and instr:
                try:
                    inst_doc = client.get_instrument(instr) or {}
                    sym = (inst_doc.get("symbol") or "").upper() or None
                    if sym:
                        instr_to_sym[instr] = sym
                except Exception:
                    sym = None
            side = (o.get("side") or "").strip().lower()
            out.append({
                "ts": o.get("last_transaction_at") or o.get("updated_at") or o.get("created_at"),
                "symbol": sym,
                "side": "buy" if side == "buy" else "sell",
                "qty": float(o.get("cumulative_quantity") or o.get("quantity") or 0.0),
                "price": float(o.get("average_price") or o.get("price") or 0.0),
                "order_id": o.get("id"),
            })
        except Exception:
            continue
    return out[:_MAX_RECENT_TRADES]


def _fetch_robinhood(instance_id: str, creds: dict) -> dict:
    """Build broker-state dict from Robinhood."""
    client = _get_robinhood_client(instance_id, creds)
    summary = client.get_account_summary(account_number=creds.get("account_number"))
    cash = float(summary.get("cash") or 0.0)
    equity = float(summary.get("equity") or 0.0)
    buying_power = float(summary.get("buying_power") or 0.0)
    account_id = (summary.get("account_number") or creds.get("account_number") or "") or None

    raw_positions: list[dict] = []
    try:
        positions = client.get_positions(account_number=creds.get("account_number"), nonzero=True) or []
    except Exception:
        positions = []
    for p in positions:
        try:
            qty_f = float(p.get("quantity") or 0.0)
            if qty_f <= 0:
                continue
            avg_entry = float(p.get("average_buy_price") or 0.0)
            instr = (p.get("instrument") or "").strip()
            sym = ""
            if instr:
                try:
                    inst_doc = client.get_instrument(instr) or {}
                    sym = (inst_doc.get("symbol") or "").upper()
                except Exception:
                    sym = ""
            raw_positions.append({"symbol": sym or None, "qty": qty_f, "avg_entry": avg_entry})
        except Exception:
            continue

    # Batch one quote call — single broken quote must not poison the snapshot.
    symbols_for_quote = [r["symbol"] for r in raw_positions if r.get("symbol")]
    live_prices: dict[str, float] = {}
    if symbols_for_quote:
        try:
            from robinhood_engine import get_live_prices
            live_prices = get_live_prices(symbols_for_quote) or {}
        except Exception:
            live_prices = {}

    positions_payload: list[dict] = []
    for r in raw_positions:
        sym = r["symbol"]
        qty_f = r["qty"]
        avg_entry = r["avg_entry"]
        last_price_raw = live_prices.get(sym) if sym else None
        try:
            last_price = float(last_price_raw) if last_price_raw is not None else None
        except Exception:
            last_price = None
        if last_price is not None and last_price > 0:
            market_value = qty_f * last_price
            unrealized = (last_price - avg_entry) * qty_f if avg_entry > 0 else 0.0
            unrealized_pct = ((last_price / avg_entry) - 1.0) * 100.0 if avg_entry > 0 else 0.0
        else:
            last_price = None
            market_value = None
            unrealized = None
            unrealized_pct = None
        positions_payload.append({
            "symbol": sym,
            "qty": qty_f,
            "avg_entry_price": avg_entry if avg_entry else None,
            "last_price": last_price,
            "market_value": market_value,
            "unrealized_pnl": unrealized,
            "unrealized_pnl_pct": unrealized_pct,
        })

    recent_trades = _robinhood_recent_trades(client)
    ph = _portfolio_history_cached(instance_id, lambda: _robinhood_portfolio_history(creds))

    # initial_value: 1-month-ago equity from Bonfire history. None when unavailable
    # so the UI shows "—" rather than a misleading 0% Total P&L.
    initial_value: Optional[float]
    if ph:
        try:
            initial_value = float(ph[0]["value"])
        except Exception:
            initial_value = None
    else:
        initial_value = None

    # last_equity: prefer RH-reported previous close; fall back to today's first
    # portfolio_history sample. Field name varies across RH responses.
    last_equity_val: Optional[float] = None
    try:
        portfolio = client.get_portfolio(account_number=creds.get("account_number")) or {}
        epc = portfolio.get("equity_previous_close") or portfolio.get("adjusted_equity_previous_close")
        if epc is not None:
            last_equity_val = float(epc)
    except Exception:
        last_equity_val = None
    if not last_equity_val and ph:
        today_iso = datetime.now(timezone.utc).date().isoformat()
        for sample in ph:
            ts = str(sample.get("ts") or "")
            if ts.startswith(today_iso):
                try:
                    last_equity_val = float(sample.get("value") or 0.0) or None
                except Exception:
                    last_equity_val = None
                break

    if initial_value and initial_value > 0:
        total_pnl = equity - initial_value
        total_pnl_pct = ((equity / initial_value) - 1.0) * 100.0
    else:
        total_pnl = None
        total_pnl_pct = None

    if last_equity_val and last_equity_val > 0 and equity > 0:
        day_pnl = equity - last_equity_val
        day_pnl_pct = ((equity / last_equity_val) - 1.0) * 100.0
    else:
        day_pnl = None
        day_pnl_pct = None

    return {
        "cash": cash, "equity": equity, "buying_power": buying_power,
        "last_equity": last_equity_val if last_equity_val is not None else 0.0,
        "initial_value": initial_value, "day_pnl": day_pnl, "day_pnl_pct": day_pnl_pct,
        "total_pnl": total_pnl, "total_pnl_pct": total_pnl_pct,
        "positions": positions_payload, "recent_trades": recent_trades, "portfolio_history": ph,
        "broker": {"name": "RobinhoodAdapter", "account_id": account_id, "paper": False,
                   "health": {"auth_fresh": True, "errors": []}},
    }


def _portfolio_history_cached(instance_id: str, fetch_fn) -> list[dict]:
    """Return cached portfolio_history if within TTL, else refetch.

    On fetch failure, falls back to the previous cached value (if any) so a
    transient outage doesn't wipe the UI graph.
    """
    now = time.time()
    entry = _ph_cache.get(instance_id)
    if entry and (now - float(entry.get("ts") or 0)) < _PORTFOLIO_HISTORY_TTL_SEC:
        return list(entry.get("data") or [])
    try:
        data = fetch_fn() or []
    except Exception:
        return list((entry or {}).get("data") or [])
    _ph_cache[instance_id] = {"ts": now, "data": data}
    return data


def fetch_broker_live_state(conn, instance_id: str) -> dict:
    """Fetch live state directly from the broker (no container dependency).

    Returns broker fields matching ``broker._compute_live_state_snapshot``
    (cash, equity, buying_power, last_equity, day_pnl[_pct], total_pnl[_pct],
    initial_value, positions[], recent_trades[], portfolio_history[], broker)
    plus metadata: ``broker_fetched_at_iso``, ``broker_fetch_error`` (None on
    success), ``broker_fetch_age_seconds`` (0 on success). Never raises.
    """
    instance_id = str(instance_id)
    base_meta = {
        "broker_fetched_at_iso": _now_iso(),
        "broker_fetch_error": None,
        "broker_fetch_age_seconds": 0,
    }
    lock = _instance_lock(instance_id)
    with lock:
        creds = _load_credentials(instance_id)
        if creds.get("error"):
            base_meta["broker_fetch_error"] = creds["error"]
            return base_meta
        broker_type = creds.get("broker_type") or "alpaca"
        try:
            if broker_type == "alpaca":
                state = _fetch_alpaca(instance_id, creds)
            elif broker_type == "robinhood":
                state = _fetch_robinhood(instance_id, creds)
            else:
                base_meta["broker_fetch_error"] = f"unsupported_broker_type: {broker_type}"
                return base_meta
        except Exception as e:
            base_meta["broker_fetch_error"] = f"broker_api_error: {type(e).__name__}: {e}"
            return base_meta
        state.update(base_meta)
        return state
