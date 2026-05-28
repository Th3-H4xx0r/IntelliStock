"""WAL-based broker-position classifier.

Used at adapter ``__init__`` when ``clean_room_mode=True`` to partition the
positions reported by the brokerage account into:

- ``strategy_owned``: positions whose most recent FILLED BUYs in this
  instance's LiveOrderWAL net to the current broker qty
- ``external``: positions with no WAL provenance (manually placed, or
  pre-dating the WAL retention window)

Also reconstructs the strategy's ``_trades`` list from the same WAL rows so
that V32 risk-exit logic (trailing stop, fast-loser, days-held) sees the
correct entry timestamps on restart.

The function is pure (no I/O, no mutation of inputs). Tests call it with a
list of pre-fetched WAL rows; production calls it with rows pulled via
``LiveOrderWAL.list_filled_for_prefix``.

WAL row schema (per backend/broker_adapters/_wal.py + nexus_runtime_state.py):
    client_order_id, symbol, side, state, filled_qty, filled_avg_price,
    updated_at_utc (ISO string), broker_order_id, ...

CID prefix convention (per broker_adapters/_client_order_id.py):
    First 8 alphanumeric chars of instance_id + "-"
    e.g. instance "main"         -> prefix "main-"
         instance "nexus-live"   -> prefix "nexuslive-"
         instance "nexus-testing"-> prefix "nexustesti-"
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# Tolerance for qty matching when classifying. We use the LARGER of an
# absolute float-epsilon and 1% of broker qty so fractional-share rounding
# does not tip a fully-owned position into "partial external".
_QTY_ABS_TOL = 1e-6
_QTY_REL_TOL = 0.01


_CID_SAFE = re.compile(r"[^A-Za-z0-9]")


def derive_cid_prefix(instance_id: str) -> str:
    """Compute the client_order_id prefix this instance writes.

    Mirrors backend/broker_adapters/_client_order_id.py::_safe(instance_id, 8).
    Returns ``"{first 8 alphanumeric chars of instance_id}-"`` or ``"x-"``
    if instance_id has no alphanumerics.
    """
    safe = _CID_SAFE.sub("", instance_id or "")[:8] or "x"
    return f"{safe}-"


def _parse_iso(ts: Any) -> Optional[datetime]:
    """Best-effort ISO timestamp parse. Returns None on failure."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    return None


def classify_broker_positions(
    positions: list[Any],
    wal_rows: list[dict],
    instance_id: str,
    cid_prefix: Optional[str] = None,
    retention_days: int = 180,
    now_utc: Optional[datetime] = None,
) -> tuple[dict[str, float], dict[str, dict], list[dict]]:
    """Partition broker positions into (strategy_owned, external) + reconstruct _trades.

    Args:
        positions: list of PositionDTO-shaped objects (must expose .symbol,
                   .qty, .market_value) OR dicts with those keys.
        wal_rows:  list of WAL row dicts (e.g. from
                   ``LiveOrderWAL.list_filled_for_prefix(cid_prefix, since)``);
                   we filter further here as defense-in-depth.
        instance_id: this instance's id (used for audit notes; cid_prefix
                     derives from it unless one is passed explicitly).
        cid_prefix: explicit override; if None, derived from instance_id.
        retention_days: how far back to walk the rows.
        now_utc: optional injection for tests; defaults to UTC now.

    Returns:
        owned:    {ticker: qty} for strategy-owned positions.
        external: {ticker: {qty, market_value, note, first_seen_utc}}.
        trades:   list of dicts shaped like ``_trades`` entries
                  (ticker, action, shares, price, timestamp, ...).

    Edge cases:
        - WAL implies LESS than broker qty: partial-external split.
        - WAL implies MORE than broker qty (e.g. manual sell during downtime):
          we trust broker (authoritative); owned = broker qty.
        - Sell-to-zero followed by re-buy: net qty tracks correctly.
        - Malformed WAL row (missing filled_qty or side): skipped.
    """
    now = now_utc or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    prefix = cid_prefix or derive_cid_prefix(instance_id)

    # Defensive re-filter on prefix + retention so the classifier is safe
    # even if the caller didn't filter.
    filtered: list[dict] = []
    for row in wal_rows or []:
        cid = row.get("client_order_id") or ""
        if not cid.startswith(prefix):
            continue
        ts = _parse_iso(row.get("updated_at_utc") or row.get("created_at_utc"))
        if ts is not None and ts < cutoff:
            continue
        filtered.append(row)

    # Sort by timestamp (oldest first) so net-qty tracking is correct.
    filtered.sort(key=lambda r: _parse_iso(r.get("updated_at_utc") or r.get("created_at_utc")) or now)

    net_qty_by_ticker: dict[str, float] = {}
    trades: list[dict] = []

    for row in filtered:
        side = (row.get("side") or "").upper()
        if side not in ("BUY", "SELL"):
            continue
        try:
            qty = float(row.get("filled_qty") or 0.0)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        symbol = row.get("symbol")
        if not symbol:
            continue

        delta = qty if side == "BUY" else -qty
        net_qty_by_ticker[symbol] = net_qty_by_ticker.get(symbol, 0.0) + delta

        try:
            price = float(row.get("filled_avg_price") or 0.0)
        except (TypeError, ValueError):
            price = 0.0
        trades.append({
            "ticker": symbol,
            "action": side,
            "shares": qty,
            "price": price,
            "timestamp": _parse_iso(row.get("updated_at_utc") or row.get("created_at_utc")) or now,
            "client_order_id": row.get("client_order_id"),
            "broker_order_id": row.get("broker_order_id"),
            "source": "wal",
        })

    # Classify each broker position
    owned: dict[str, float] = {}
    external: dict[str, dict] = {}

    for pos in positions or []:
        # Support both PositionDTO and dict shapes
        if isinstance(pos, dict):
            ticker = pos.get("symbol")
            broker_qty = float(pos.get("qty") or 0.0)
            mv = float(pos.get("market_value") or 0.0)
        else:
            ticker = getattr(pos, "symbol", None)
            broker_qty = float(getattr(pos, "qty", 0.0) or 0.0)
            mv = float(getattr(pos, "market_value", 0.0) or 0.0)
        if not ticker:
            continue

        wal_qty = max(0.0, net_qty_by_ticker.get(ticker, 0.0))
        tol = max(_QTY_ABS_TOL, _QTY_REL_TOL * broker_qty)

        if wal_qty <= _QTY_ABS_TOL:
            # No WAL trace -> fully external
            external[ticker] = {
                "qty": broker_qty,
                "market_value": mv,
                "note": "no WAL trace within retention window",
                "first_seen_utc": now.isoformat(),
            }
        elif abs(wal_qty - broker_qty) <= tol:
            # Match within tolerance -> fully strategy-owned
            owned[ticker] = broker_qty
        elif wal_qty < broker_qty:
            # WAL implies less than broker shows -> partial-external split
            owned[ticker] = wal_qty
            excess = broker_qty - wal_qty
            external[ticker] = {
                "qty": excess,
                "market_value": mv * (excess / broker_qty) if broker_qty > 0 else 0.0,
                "note": f"partial external: WAL implies {wal_qty:.4f}sh, broker shows {broker_qty:.4f}sh",
                "first_seen_utc": now.isoformat(),
            }
        else:
            # WAL implies MORE than broker (e.g. manual sell during downtime).
            # Broker is authoritative; trim our view to broker reality.
            owned[ticker] = broker_qty

    return owned, external, trades
