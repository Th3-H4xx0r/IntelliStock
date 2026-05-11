"""Thread-shared diagnostic state for the live broker's strategy worker.

When the strategy worker thread wedges silently (e.g. on a TLS recv with
no timeout, or a re-entered lock), the only artifacts left are a stale
"strategy-tick #N STARTED" log line and silence. The snapshot daemon
(separate thread) keeps writing LiveState rows but had no visibility
into the strategy thread's progress.

This module provides:
- ``STATE`` — a single module-global dict that the strategy thread
  stamps with its current phase. Single-key dict assignments are
  GIL-atomic in CPython, so concurrent reads from the snapshot daemon
  thread are tear-free without a lock. We deliberately avoid a lock
  here because the whole point is to surface state from a thread that
  may be wedged on its own lock — adding more locking would defeat
  that.
- ``set_phase()`` — defensive helper called from broker.py's outer
  loop on each phase transition. Never raises (a bug here would
  itself wedge the broker).

The snapshot daemon does ``dict(STATE)`` to copy the current values
into its outgoing payload.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Optional


STATE: dict[str, Any] = {
    "last_tick_index": 0,
    "last_tick_phase": "init",
    "last_tick_phase_ts": None,
    "last_tick_mode": None,
    "last_tick_started_ts": None,
    "last_tick_completed_ts": None,
    "next_wake_ts": None,
    "next_wake_mode": None,
}


def set_phase(
    phase: str,
    *,
    tick_index: Optional[int] = None,
    mode: Optional[str] = None,
    next_wake_ts: Optional[str] = None,
    next_wake_mode: Optional[str] = None,
    started: bool = False,
    completed: bool = False,
) -> None:
    """Stamp the strategy thread's current phase. Caller-defensive — any
    exception is swallowed to keep the strategy hot path safe.

    Phases used by the broker outer loop: ``wake``, ``warm_boot_eppi``,
    ``scheduling_check``, ``rh_refresh``, ``strategy_run``,
    ``post_tick_snapshot``, ``sleep``. Plus free-form values for ad-hoc
    instrumentation.

    All updates land via a single ``STATE.update(...)`` call so a
    reader doing ``dict(STATE)`` between writes either sees the full
    prior snapshot or the full new one — never a torn mix.
    ``dict.update`` from a Python dict is a single C-level operation
    that holds the GIL across all key copies.
    """
    try:
        now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
        delta: dict[str, Any] = {
            "last_tick_phase": phase,
            "last_tick_phase_ts": now_iso,
        }
        if tick_index is not None:
            try:
                delta["last_tick_index"] = int(tick_index)
            except Exception:
                # int() on a hostile object raised — skip the field but
                # still apply the rest of the phase update.
                pass
        if mode is not None:
            delta["last_tick_mode"] = mode
        if next_wake_ts is not None:
            delta["next_wake_ts"] = next_wake_ts
        if next_wake_mode is not None:
            delta["next_wake_mode"] = next_wake_mode
        if started:
            delta["last_tick_started_ts"] = now_iso
        if completed:
            delta["last_tick_completed_ts"] = now_iso
        STATE.update(delta)
    except Exception:
        pass


def snapshot() -> dict[str, Any]:
    """Return a shallow copy of STATE for inclusion in payloads."""
    try:
        return dict(STATE)
    except Exception:
        return {}
