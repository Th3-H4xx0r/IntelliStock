"""AI conviction score vs outcome, ported from ST calibration.py (R4-04).

    calibration.py:28-47   _bucket_label, _score_of           verbatim
    calibration.py:50-82   swing_calibration over SwingSignals swing rows with
                           a closed outcome (ST: paper_trades.csv round trips)
    calibration.py:85-124  wheel_calibration over wheel rows with a premium
                           outcome (ST: wheel_trades.csv)
    calibration.py:127-137 calibration_report
Honesty rule (ST): a bucket with count < MIN_BUCKET_N is insufficient_n=True.
Outcomes are written by the lanes (record_outcomes) from the broker's closed
orders, so the record carries the FILL price (spec §9 fix 7).

G8a ruling 4 (G5 minor 2): a swing entry with no filled buy once
UNFILLED_AFTER_SESSIONS NY sessions have passed since its own is closed with
outcome {"unfilled": True, ...}. Left open it would claim a later trade's
round trip, keep its symbol swing-owned, and pin record_outcomes' window.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd

from swing_trader import clock, signals_store
from swing_trader.constants import BUCKETS, GATE_TRADES, MIN_BUCKET_N

try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="SwingCalibration")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[SwingCalibration] {msg}")

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

#: G8a ruling 4: an entry gets its own session and the next to fill (a day
#: order expires with its session); on the second session after its own it
#: never filled.
UNFILLED_AFTER_SESSIONS = 2


def _bucket_label(lo: int, hi: int) -> str:
    return f"{lo}-{hi}"


def _score_of(value) -> int | None:
    """Parse an ai_score/conviction_score cell. Blank/NaN/garbage → None."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def swing_calibration(rows) -> dict:
    """Bucket closed swing round trips by the signal's score."""
    matched = [r for r in (rows or []) if r.get("lane") == "swing"
               and isinstance(r.get("outcome"), dict) and r["outcome"].get("pnl") is not None]

    scored = []
    for t in matched:
        score = _score_of(t.get("score"))
        if score is not None:
            scored.append({**t["outcome"], "score": score})

    buckets = {}
    for lo, hi in BUCKETS:
        in_bucket = [t for t in scored if lo <= t["score"] <= hi]
        wins = [t for t in in_bucket if t["pnl"] > 0]
        buckets[_bucket_label(lo, hi)] = {
            "count":          len(in_bucket),
            "insufficient_n": len(in_bucket) < MIN_BUCKET_N,
            "win_rate":       round(len(wins) / len(in_bucket) * 100, 1) if in_bucket else None,
            "avg_pnl_pct":    round(sum(t["pnl_pct"] for t in in_bucket) / len(in_bucket), 2) if in_bucket else None,
            "total_pnl":      round(sum(t["pnl"] for t in in_bucket), 2),
        }

    return {
        "closed_total":  len(matched),
        "closed_scored": len(scored),
        "excluded_unscored": len(matched) - len(scored),
        "buckets":       buckets,
    }


def wheel_calibration(rows) -> dict:
    """Bucket wheel candidates by score; outcome = realized premium on placed
    orders. Assignment losses are out of scope until assignment history exists."""
    wheel_rows = [r for r in (rows or []) if r.get("lane") == "wheel"]
    if not wheel_rows:
        return {"placed_total": 0, "buckets": {}}

    placed = []
    for r in wheel_rows:
        score = _score_of(r.get("score"))
        out = r.get("outcome") or {}
        prem = out.get("premium_received")
        if score is None or prem in (None, ""):
            continue
        try:
            prem_per_share = float(prem)
        except (TypeError, ValueError):
            continue
        try:
            contracts = int(float(out.get("contracts") or 1))
        except (TypeError, ValueError):
            contracts = 1
        placed.append({"score": score,
                       "premium_dollars": prem_per_share * 100 * max(contracts, 1)})

    buckets = {}
    for lo, hi in BUCKETS:
        in_bucket = [t for t in placed if lo <= t["score"] <= hi]
        buckets[_bucket_label(lo, hi)] = {
            "count":              len(in_bucket),
            "insufficient_n":     len(in_bucket) < MIN_BUCKET_N,
            "total_premium":      round(sum(t["premium_dollars"] for t in in_bucket), 2),
            "avg_premium":        round(sum(t["premium_dollars"] for t in in_bucket) / len(in_bucket), 2) if in_bucket else None,
        }

    return {"placed_total": len(placed), "buckets": buckets}


def calibration_report(instance_id=None, *, rows=None) -> dict:
    if rows is None:
        rows = signals_store.all_signals(instance_id)
    swing = swing_calibration(rows)
    return {
        "swing": swing,
        "wheel": wheel_calibration(rows),
        "gate": {
            "closed_scored_trades": swing["closed_scored"],
            "required": GATE_TRADES,
            "met": swing["closed_scored"] >= GATE_TRADES,
        },
    }


# -- outcomes (spec §8 "Calibration reads SwingSignals joined to outcomes") ---

def _flatten(orders):
    for o in orders or []:
        yield o
        for leg in (getattr(o, "legs", ()) or ()):
            yield leg


def _at(o):
    return clock.as_utc(getattr(o, "submitted_at_utc", None)) or _EPOCH


def _filled(o) -> bool:
    return (str(getattr(o, "status", "")).lower() == "filled"
            and getattr(o, "filled_avg_price", None) not in (None, 0, ""))


def _filled_of(signal: dict, orders) -> list:
    sym = str(signal.get("symbol") or "").upper()
    return [o for o in _flatten(orders)
            if str(getattr(o, "symbol", "")).upper() == sym and _filled(o)]


def _entry_buys(signal: dict, mine) -> list:
    """Filled BUYs of the signal's symbol from a day before the signal on."""
    since = (clock.as_utc(signal.get("created_at")) or _EPOCH) - timedelta(days=1)
    return sorted((o for o in mine if str(o.side).lower() == "buy" and _at(o) >= since),
                  key=_at)


def resolve_swing_outcome(signal: dict, orders):
    """The round trip behind a swing signal: the first filled BUY of the symbol
    from a day before the signal on, then the first filled SELL after it
    (bracket legs included)."""
    mine = _filled_of(signal, orders)
    buys = _entry_buys(signal, mine)
    if not buys:
        return None
    entry = buys[0]
    sells = sorted((o for o in mine if str(o.side).lower() == "sell" and _at(o) >= _at(entry)),
                   key=_at)
    if not sells:
        return None
    exit_ = sells[0]
    buy_px, sell_px = float(entry.filled_avg_price), float(exit_.filled_avg_price)
    shares = float(getattr(entry, "filled_qty", 0) or getattr(entry, "qty", 0) or 0)
    if buy_px <= 0 or shares <= 0:
        return None
    return {"entry_price": round(buy_px, 4), "exit_price": round(sell_px, 4),
            "shares": shares, "pnl": round((sell_px - buy_px) * shares, 2),
            "pnl_pct": round((sell_px - buy_px) / buy_px * 100, 4),
            "exit_order_id": getattr(exit_, "broker_order_id", None),
            "exit_date": _at(exit_).date().isoformat()}


def _sessions_after(session: date, today: date) -> int:
    """NYSE sessions strictly after `session`, up to and including `today`."""
    if today <= session:
        return 0
    return len(clock.trading_days(session + timedelta(days=1), today))


def unfilled_swing_outcome(signal: dict, orders, today: date):
    """G8a ruling 4: {"unfilled": True, ...} when the entry shows no filled
    buy and UNFILLED_AFTER_SESSIONS sessions have passed since its own; else
    None."""
    try:
        session = date.fromisoformat(str(signal.get("session") or "")[:10])
    except ValueError:
        return None
    waited = _sessions_after(session, today)
    if waited < UNFILLED_AFTER_SESSIONS or _entry_buys(signal, _filled_of(signal, orders)):
        return None
    return {"unfilled": True, "as_of": today.isoformat(), "sessions_waited": waited}


def _contract_of(signal: dict):
    return ((signal.get("submitted_order") or {}).get("contract")
            or (signal.get("proposal") or {}).get("contract"))


def resolve_wheel_outcome(signal: dict, orders):
    """The premium a wheel signal's put actually sold for."""
    contract = _contract_of(signal)
    if not contract:
        return None
    for o in _flatten(orders):
        if (str(getattr(o, "symbol", "")).upper() == str(contract).upper()
                and str(getattr(o, "side", "")).lower() == "sell" and _filled(o)):
            return {"premium_received": float(o.filled_avg_price),
                    "contracts": int(float(getattr(o, "filled_qty", 0) or getattr(o, "qty", 1) or 1)),
                    "order_id": getattr(o, "broker_order_id", None)}
    return None


def record_outcomes(instance_id, adapter, lane: str, *, held=None, today=None) -> int:
    """Write the outcome of every open swing or wheel signal the broker's
    closed orders can resolve. A swing symbol still held is still open. With
    a positions read (`held`), a swing entry that never filled is closed as
    unfilled (G8a ruling 4). Never raises; returns the number of rows updated."""
    try:
        today = today or date.fromisoformat(clock.ny_date(datetime.now(timezone.utc)))
        rows = [r for r in signals_store.all_signals(instance_id)
                if r.get("lane") == lane and r.get("status") in signals_store.OPEN_STATUSES
                and not r.get("outcome")]
        if lane == "swing" and held is not None:
            still_held = {str(s).upper() for s in held}
            rows = [r for r in rows if str(r.get("symbol")).upper() not in still_held]
        if not rows:
            return 0
        symbols = sorted({(r["symbol"] if lane == "swing" else _contract_of(r))
                          for r in rows} - {None})
        if not symbols:
            return 0
        after = min(str(r.get("created_at") or "") for r in rows)[:10]
        orders = list(adapter.list_closed_orders(symbols, after) or [])
        n = 0
        for r in rows:
            out = (resolve_swing_outcome(r, orders) if lane == "swing"
                   else resolve_wheel_outcome(r, orders))
            if out is None and lane == "swing" and held is not None:
                out = unfilled_swing_outcome(r, orders, today)
                if out:
                    _log(f"{r.get('symbol')}: swing entry of {r.get('session')} never "
                         f"filled in {out['sessions_waited']} sessions — closed as unfilled")
            if out:
                signals_store.update_signal(r["id"], {"outcome": out})
                n += 1
        return n
    except Exception as exc:
        _log(f"outcome resolution skipped ({type(exc).__name__}: {exc})", "yellow")
        return 0
