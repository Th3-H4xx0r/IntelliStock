"""
Future Trades Queue — helpers for strategies to schedule trades at a future date/time.

Usage in a strategy's run() or run_once():
    from strategies.future_trades import schedule_trade, get_pending_trades, expire_old_trades

    # Schedule a trade for a future date
    schedule_trade(strategy_cache, symbol="AAPL", target_date="2026-02-15",
                   signal=1, reason="Earnings expected positive", source_date="2026-02-11")

    # At the start of each run, check for pending trades for this symbol on current_time
    pending = get_pending_trades(strategy_cache, symbol="AAPL", current_time=current_time)
    # pending is a list of trade dicts; strategy should use these as hints, not blindly execute

    # Periodically clean up expired entries
    expire_old_trades(strategy_cache, current_time=current_time, max_age_days=30)

Trade dict schema:
    {
        "symbol": str,           # ticker
        "target_date": str,      # ISO date (YYYY-MM-DD) when trade should trigger
        "signal": int,           # 1=buy, -1=sell
        "reason": str,           # why this was scheduled
        "source_date": str,      # ISO date when the trade was scheduled
        "confidence": float,     # 0.0-1.0, how confident the strategy was
        "allocation_pct": float, # optional; 0-100, share of reserved capital to use for this buy
    }
"""

from __future__ import annotations

from datetime import datetime, timedelta


_QUEUE_KEY = "_future_trades_queue"


def schedule_trade(
    strategy_cache: dict,
    symbol: str,
    target_date: str | datetime,
    signal: int,
    reason: str = "",
    source_date: str | datetime | None = None,
    confidence: float = 0.5,
    allocation_pct: float | None = None,
) -> None:
    """Add a trade to the future trades queue in strategy_cache. allocation_pct (0-100) is optional for buys."""
    if strategy_cache is None:
        return
    queue: list = strategy_cache.setdefault(_QUEUE_KEY, [])
    td = target_date if isinstance(target_date, str) else target_date.strftime("%Y-%m-%d")
    sd = ""
    if source_date is not None:
        sd = source_date if isinstance(source_date, str) else source_date.strftime("%Y-%m-%d")
    entry = {
        "symbol": symbol.upper(),
        "target_date": td,
        "signal": int(signal),
        "reason": str(reason)[:500],
        "source_date": sd,
        "confidence": max(0.0, min(1.0, float(confidence))),
    }
    if allocation_pct is not None:
        entry["allocation_pct"] = max(0.0, min(100.0, float(allocation_pct)))
    queue.append(entry)


def get_pending_trades(
    strategy_cache: dict,
    symbol: str,
    current_time,
) -> list[dict]:
    """Return list of scheduled trades for symbol on the current date. Does NOT remove them."""
    if strategy_cache is None:
        return []
    queue: list = strategy_cache.get(_QUEUE_KEY, [])
    if not queue:
        return []
    today = _to_date_str(current_time)
    sym = symbol.upper()
    return [t for t in queue if t.get("symbol") == sym and t.get("target_date") == today]


def pop_pending_trades(
    strategy_cache: dict,
    symbol: str,
    current_time,
) -> list[dict]:
    """Return and remove scheduled trades for symbol on the current date."""
    if strategy_cache is None:
        return []
    queue: list = strategy_cache.get(_QUEUE_KEY, [])
    if not queue:
        return []
    today = _to_date_str(current_time)
    sym = symbol.upper()
    matched = [t for t in queue if t.get("symbol") == sym and t.get("target_date") == today]
    if matched:
        strategy_cache[_QUEUE_KEY] = [t for t in queue if not (t.get("symbol") == sym and t.get("target_date") == today)]
    return matched


def get_all_pending_trades_for_date(
    strategy_cache: dict,
    current_time,
) -> list[dict]:
    """Return all scheduled trades for the current date (any symbol). Does NOT remove them."""
    if strategy_cache is None:
        return []
    queue: list = strategy_cache.get(_QUEUE_KEY, [])
    if not queue:
        return []
    today = _to_date_str(current_time)
    return [t for t in queue if t.get("target_date") == today]


def pop_all_pending_trades_for_date(
    strategy_cache: dict,
    current_time,
) -> list[dict]:
    """Return and remove all scheduled trades for the current date (any symbol)."""
    if strategy_cache is None:
        return []
    queue: list = strategy_cache.get(_QUEUE_KEY, [])
    if not queue:
        return []
    today = _to_date_str(current_time)
    matched = [t for t in queue if t.get("target_date") == today]
    if matched:
        strategy_cache[_QUEUE_KEY] = [t for t in queue if t.get("target_date") != today]
    return matched


def get_all_future_trades(
    strategy_cache: dict,
) -> list[dict]:
    """Return all scheduled trades in the queue (any symbol, any date). Does NOT remove them."""
    if strategy_cache is None:
        return []
    return list(strategy_cache.get(_QUEUE_KEY, []))


def cancel_trades(
    strategy_cache: dict,
    symbol: str,
    target_date: str | None = None,
) -> list[dict]:
    """
    Cancel (remove) all pending trades for a symbol.
    If target_date is specified, only cancel trades for that specific date.
    Returns the cancelled trades.
    """
    if strategy_cache is None:
        return []
    queue: list = strategy_cache.get(_QUEUE_KEY, [])
    if not queue:
        return []
    sym = symbol.upper()
    if target_date:
        cancelled = [t for t in queue if t.get("symbol") == sym and t.get("target_date") == target_date]
        if cancelled:
            strategy_cache[_QUEUE_KEY] = [t for t in queue if not (t.get("symbol") == sym and t.get("target_date") == target_date)]
    else:
        cancelled = [t for t in queue if t.get("symbol") == sym]
        if cancelled:
            strategy_cache[_QUEUE_KEY] = [t for t in queue if t.get("symbol") != sym]
    return cancelled


def expire_old_trades(
    strategy_cache: dict,
    current_time,
    max_age_days: int = 30,
) -> int:
    """Remove trades whose target_date is more than max_age_days before current_time. Returns count removed."""
    if strategy_cache is None:
        return 0
    queue: list = strategy_cache.get(_QUEUE_KEY, [])
    if not queue:
        return 0
    today = _to_date(current_time)
    cutoff = (today - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    before = len(queue)
    strategy_cache[_QUEUE_KEY] = [t for t in queue if t.get("target_date", "9999") >= cutoff]
    return before - len(strategy_cache[_QUEUE_KEY])


def _to_date_str(current_time) -> str:
    """Convert current_time to YYYY-MM-DD string."""
    if isinstance(current_time, str):
        return current_time[:10]
    if isinstance(current_time, datetime):
        return current_time.strftime("%Y-%m-%d")
    if hasattr(current_time, "strftime"):
        return current_time.strftime("%Y-%m-%d")
    return str(current_time)[:10]


def _to_date(current_time):
    """Convert current_time to a date object."""
    if isinstance(current_time, datetime):
        return current_time.date()
    if hasattr(current_time, "year"):
        return current_time
    try:
        return datetime.fromisoformat(str(current_time)[:10]).date()
    except Exception:
        return datetime.utcnow().date()
