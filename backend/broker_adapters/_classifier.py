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

from ._symbols import normalize_broker_symbol

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
    """Best-effort ISO timestamp parse. Returns None on failure.

    Normalizes any tz-naive datetime to UTC so callers can safely compare
    against a tz-aware cutoff (bug-sweep 2026-05-28: prior version crashed
    with "can't compare offset-naive and offset-aware datetimes" if any
    WAL row was ever written without a tz suffix).
    """
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, str):
        try:
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
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
    #
    # Exclude synthetic dry-run fills. A dry-run submit writes a terminal
    # 'filled' WAL row with broker_order_id 'dry-<uuid>' that was never sent to
    # the broker. Feeding it into classification or the _trades rebuild
    # would fabricate phantom positions, bogus recent_sell_block entries, and bad V32
    # entry anchors on a clean-room boot taken AFTER a dry-run cycle. Real fills carry
    # a genuine broker_order_id and are kept.
    #
    # 2-C: track tickers whose only prefix-matching BUY provenance is BEYOND the
    # retention cutoff so a position quarantined below can be labeled 'aged-out'
    # (had WAL provenance) vs 'no WAL trace' (truly unknown). We deliberately do NOT
    # auto-adopt an aged-out position: a holding older than retention is fail-safe
    # quarantined (the strategy's max_hold_days would have exited a real position long
    # ago), and auto-adopting could resurrect a manual long-term hold and force-sell it.
    filtered: list[dict] = []
    aged_out_buy_tickers: set[str] = set()
    for row in wal_rows or []:
        cid = row.get("client_order_id") or ""
        if not cid.startswith(prefix):
            continue
        if str(row.get("broker_order_id") or "").startswith("dry-"):
            continue
        ts = _parse_iso(row.get("updated_at_utc") or row.get("created_at_utc"))
        if ts is not None and ts < cutoff:
            if (row.get("side") or "").upper() == "BUY" and row.get("symbol"):
                try:
                    if float(row.get("filled_qty") or 0.0) > 0:
                        aged_out_buy_tickers.add(normalize_broker_symbol(row.get("symbol")))
                except (TypeError, ValueError):
                    pass
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
        # Normalize dot->dash so WAL and broker share-class symbols match.
        symbol = normalize_broker_symbol(row.get("symbol"))
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
            # Lowercase to match the convention every other _trades writer
            # uses throughout the strategy runtime.
            # Strategy consumers do bare-equality `action == "buy"`
            # (graph_nexus_analysis.py:16365 and others); uppercase would
            # silently disable V32 risk pipeline for WAL-seeded positions.
            "action": side.lower(),
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
        # 2-D / Scope D C3: normalize the broker symbol to the same dash form the
        # WAL net is keyed by, so dot/dash share-class tickers reconcile.
        ticker = normalize_broker_symbol(ticker)
        if not ticker:
            continue

        # Defense-in-depth (bug-sweep 2026-05-28):
        # - Zero-qty positions are not worth recording (broker occasionally
        #   leaves a zero-qty row in the response window after liquidation).
        # - Negative broker_qty (short position) is quarantined regardless of
        #   WAL state. The graph_nexus strategy is long-only and has no
        #   notion of managing a short; letting one through as strategy-owned
        #   would corrupt sell-fraction math and get_positions_value.
        if broker_qty == 0.0:
            continue
        if broker_qty < 0.0:
            external[ticker] = {
                "qty": broker_qty,
                "market_value": mv,
                "note": "short position quarantined (strategy is long-only)",
                "first_seen_utc": now.isoformat(),
            }
            continue

        wal_qty = max(0.0, net_qty_by_ticker.get(ticker, 0.0))
        # Tolerance: ABS_TOL covers floating-point rounding; REL_TOL covers
        # fractional-share precision but is CAPPED at 0.5 share absolute so a
        # 1% gap on a $1M position (potentially $10K of unaccounted external)
        # does not get silently swallowed as "owned" (bug-sweep 2026-05-28).
        tol = max(_QTY_ABS_TOL, min(0.5, _QTY_REL_TOL * abs(broker_qty)))

        if wal_qty <= _QTY_ABS_TOL:
            # No WAL trace within retention -> external (quarantined). Distinguish
            # 'aged-out' (had a prefix-matching BUY beyond the retention window) from
            # 'no WAL trace' (truly unknown / manual) for operator visibility (2-C).
            if ticker in aged_out_buy_tickers:
                note = (
                    f"aged-out: WAL BUY provenance exists but is beyond the "
                    f"{retention_days}d retention window. Quarantined fail-safe "
                    f"(not auto-adopted); adopt manually if this is a strategy position."
                )
            else:
                note = "no WAL trace within retention window"
            external[ticker] = {
                "qty": broker_qty,
                "market_value": mv,
                "note": note,
                "first_seen_utc": now.isoformat(),
            }
        elif abs(wal_qty - broker_qty) <= tol:
            # Match within tolerance -> fully strategy-owned
            owned[ticker] = broker_qty
        elif wal_qty < broker_qty:
            # WAL implies less than broker shows -> partial-external split.
            # broker_qty > wal_qty > _QTY_ABS_TOL > 0 by this branch's guards,
            # so the division is safe.
            owned[ticker] = wal_qty
            excess = broker_qty - wal_qty
            external[ticker] = {
                "qty": excess,
                "market_value": mv * (excess / broker_qty),
                "note": f"partial external: WAL implies {wal_qty:.4f}sh, broker shows {broker_qty:.4f}sh",
                "first_seen_utc": now.isoformat(),
            }
        else:
            # WAL implies MORE than broker (e.g. manual sell during downtime).
            # Broker is authoritative; trim our view to broker reality.
            owned[ticker] = broker_qty

    return owned, external, trades
