"""Per-bar in-memory snapshot/restore primitive for backtest LLM-critical pauses.

Reuses strategy_cache_persistence._serialize_cache_for_blob for the
strategy_cache portion (handles unpicklable lambdas via __skipped_fields__).
portfolio_emulator and current_time are simple python values; deepcopy.

Module-level single-slot storage. Only the latest snapshot is kept.
"""
from __future__ import annotations

import copy
import datetime as _dt
import threading
import time
from typing import Any


_state_lock = threading.RLock()
_last_good_bar: dict[str, Any] | None = None


def capture(
    *,
    strategy_caches: dict,
    portfolio_emulator: Any,
    current_time: _dt.datetime,
) -> None:
    """Capture a snapshot at the start of the next bar's strategy execution.
    Overwrites any prior snapshot (single-slot)."""
    global _last_good_bar
    from strategy_cache_persistence import _serialize_cache_for_blob

    blob = _serialize_cache_for_blob(strategy_caches or {})
    portfolio_copy = copy.deepcopy(portfolio_emulator)
    time_copy = current_time  # datetime is immutable; no copy needed
    with _state_lock:
        _last_good_bar = {
            "cache_blob": blob,
            "portfolio": portfolio_copy,
            "current_time": time_copy,
            "captured_at": time.time(),
        }


def restore() -> tuple[dict, Any, _dt.datetime] | None:
    """Return the (strategy_caches, portfolio_emulator, current_time) tuple from
    the last capture, or None if no snapshot exists. Deserializes the cache blob."""
    from strategy_cache_persistence import _deserialize_cache_from_blob
    with _state_lock:
        snap = _last_good_bar
    if snap is None:
        return None
    caches = _deserialize_cache_from_blob(snap["cache_blob"] or "")
    portfolio = copy.deepcopy(snap["portfolio"])  # deep-copy on read too — caller may mutate
    return caches, portfolio, snap["current_time"]


def discard() -> None:
    """Clear the snapshot slot. Called at backtest completion for memory hygiene."""
    global _last_good_bar
    with _state_lock:
        _last_good_bar = None


def has_snapshot() -> bool:
    with _state_lock:
        return _last_good_bar is not None
