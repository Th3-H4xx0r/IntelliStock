"""Live-broker strategy schedule.

Single source of truth for "when should the broker fire `run_once` next, and
in what mode?". Replaces the dual-cadence gate previously embedded in
`graph_nexus_analysis.run_once` (lines 16097-16313 — deleted in commit 5
of the 2026-05-07 scheduler refactor).

The function is PURE: same `(now, marker, config)` always returns the same
`(next_wake_utc, mode)`. No I/O, no globals, no clock reads. The broker is
the only caller; the strategy receives `mode` as a parameter and obeys.

Schedule (default config, configurable per-instance):
- Session: 5:00 AM PT — 5:00 PM PT (`open_pt_min=300`, `close_pt_min=1020`)
- FULL cycle anchor: 6:30 AM PT (`full_anchor_pt_min=390`)
- MONITOR cadence: every 20 min from open through close
- Weekdays only by default (`weekdays_only=True`)
- After session close: sleep until next session day's open
- DST-correct via `zoneinfo.ZoneInfo("America/Los_Angeles")`

Marker semantics (`_nexus_full_cycle_completed_date` in strategy_cache):
- NY-date string ("YYYY-MM-DD") set when a FULL cycle completes successfully
- Read by `get_next_wake` to decide FULL vs MONITOR at the anchor slot
- Restart-resilient: a mid-day reboot sees marker=today and demotes the
  6:30 AM slot to MONITOR (skipping the FULL re-run)
- Marker write LIVES IN THE BROKER (commit 4) so a strategy crash mid-FULL
  doesn't silently set the marker and lose buys
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Mapping, Optional, Sequence

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover — Python <3.9 not supported
    raise

_log = logging.getLogger(__name__)

# Module-level dedup set — defensive warnings for invalid config / naive
# datetimes fire only once per process per key, not on every call (the
# scheduler is invoked every broker tick, so without dedup a misconfigured
# instance would emit ~1440 warnings/day per bad key).
_warned_keys: set[str] = set()

Mode = Literal["FULL", "MONITOR", "IDLE"]

DEFAULT_CONFIG: dict[str, Any] = {
    "session_tz": "America/Los_Angeles",
    "open_pt_min": 300,            # 5:00 AM PT
    "close_pt_min": 1020,          # 5:00 PM PT
    "full_anchor_pt_min": 390,     # 6:30 AM PT
    "monitor_interval_min": 20,
    "weekdays_only": True,
    "alignment_tolerance_sec": 60, # how late can we wake post-slot and still fire
}


def _warn_once(key: str, msg: str, *args) -> None:
    """Emit a warning at most once per process per key. Used so a
    misconfigured prod instance doesn't generate 1440 warnings/day."""
    if key in _warned_keys:
        return
    _warned_keys.add(key)
    _log.warning(msg, *args)


def _resolve_config(config: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Merge user config over defaults, clamping invalid values defensively.
    Warnings are logged at most once per process per invalid key (see
    `_warn_once`)."""
    out = dict(DEFAULT_CONFIG)
    if config:
        for k, v in config.items():
            if k in DEFAULT_CONFIG:
                out[k] = v
    # Validation — clamp to sane ranges, warn once if invalid.
    if not isinstance(out["open_pt_min"], int) or not (0 <= out["open_pt_min"] < 1440):
        _warn_once("open_pt_min", "Invalid open_pt_min=%r; using default 300", out["open_pt_min"])
        out["open_pt_min"] = DEFAULT_CONFIG["open_pt_min"]
    if not isinstance(out["close_pt_min"], int) or not (0 < out["close_pt_min"] <= 1440):
        _warn_once("close_pt_min", "Invalid close_pt_min=%r; using default 1020", out["close_pt_min"])
        out["close_pt_min"] = DEFAULT_CONFIG["close_pt_min"]
    if out["open_pt_min"] >= out["close_pt_min"]:
        _warn_once(
            "open_close_inverted",
            "open_pt_min(%d) >= close_pt_min(%d); using defaults",
            out["open_pt_min"], out["close_pt_min"],
        )
        out["open_pt_min"] = DEFAULT_CONFIG["open_pt_min"]
        out["close_pt_min"] = DEFAULT_CONFIG["close_pt_min"]
    if not isinstance(out["full_anchor_pt_min"], int) or not (
        out["open_pt_min"] <= out["full_anchor_pt_min"] < out["close_pt_min"]
    ):
        _warn_once(
            "full_anchor_outside_session",
            "full_anchor_pt_min=%r outside session [%d, %d); using default 390",
            out["full_anchor_pt_min"], out["open_pt_min"], out["close_pt_min"],
        )
        out["full_anchor_pt_min"] = DEFAULT_CONFIG["full_anchor_pt_min"]
    if not isinstance(out["monitor_interval_min"], int) or out["monitor_interval_min"] < 1:
        _warn_once("monitor_interval", "Invalid monitor_interval_min=%r; using default 20", out["monitor_interval_min"])
        out["monitor_interval_min"] = DEFAULT_CONFIG["monitor_interval_min"]
    if not isinstance(out["alignment_tolerance_sec"], (int, float)) or out["alignment_tolerance_sec"] < 0:
        out["alignment_tolerance_sec"] = DEFAULT_CONFIG["alignment_tolerance_sec"]
    return out


def _is_session_day(d, weekdays_only: bool) -> bool:
    if not weekdays_only:
        return True
    # Monday=0 ... Friday=4
    return d.weekday() < 5


def _next_session_day(d, weekdays_only: bool):
    """Return the next date >= d+1 that's a session day."""
    nxt = d + timedelta(days=1)
    if not weekdays_only:
        return nxt
    # Skip Sat (5) and Sun (6).
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def _build_slots(open_min: int, close_min: int, full_min: int, step: int) -> list[int]:
    """Slot grid for a session day, in minutes-of-day PT.

    Monitors are aligned to `open_min` and step every `step` minutes through
    the session window. The full-anchor `full_min` is added as an extra slot
    (it may or may not coincide with a monitor slot — at the default config
    it doesn't, since 390 isn't a multiple of 20 from 300).

    Example with defaults (open=300, full=390, close=1020, step=20):
      monitors: 300, 320, 340, 360, 380, 400, 420, 440, ..., 1000
      anchor:   390  (inserted between 380 and 400)
      Combined: 300, 320, 340, 360, 380, 390, 400, 420, ..., 1000
    """
    slots = set()
    m = open_min
    while m < close_min:
        slots.add(m)
        m += step
    slots.add(full_min)
    # Drop full_min if it equals close_min — defensive, shouldn't happen.
    slots.discard(close_min)
    return sorted(slots)


def _pt_at(date_, minute_of_day: int, pt: ZoneInfo) -> datetime:
    """Construct a tz-aware PT datetime at the given date+minute. Uses fold=0
    (first occurrence) to disambiguate fall-back DST repeats deterministically.
    """
    h, m = divmod(minute_of_day, 60)
    return datetime(date_.year, date_.month, date_.day, h, m, 0, 0, tzinfo=pt, fold=0)


def get_next_wake(
    now: datetime,
    marker: Optional[str],
    config: Optional[Mapping[str, Any]] = None,
) -> tuple[datetime, Mode]:
    """Pure scheduling decision.

    Args:
        now: Current time. tz-aware preferred; naive treated as UTC with a
            once-per-process warning (defensive — production callers always
            pass UTC).
        marker: Most recent successful FULL date as "YYYY-MM-DD" in PT, or
            None if the strategy hasn't run a FULL today.
        config: Optional override; missing keys fall back to DEFAULT_CONFIG.

    Returns:
        (next_wake_utc, mode_for_THIS_tick)
        - next_wake_utc: when the broker should sleep until for the *next* tick
        - mode: what the strategy should do AT now (or shortly after)
    """
    cfg = _resolve_config(config)
    pt = ZoneInfo(cfg["session_tz"])

    # Defensive: handle naive `now`.
    if now.tzinfo is None:
        _warn_once("naive_now", "get_next_wake received naive `now`; assuming UTC")
        now = now.replace(tzinfo=timezone.utc)

    now_pt = now.astimezone(pt)
    today_pt = now_pt.date()
    open_min = cfg["open_pt_min"]
    close_min = cfg["close_pt_min"]
    full_min = cfg["full_anchor_pt_min"]
    step = cfg["monitor_interval_min"]
    tolerance = float(cfg["alignment_tolerance_sec"])
    weekdays_only = bool(cfg["weekdays_only"])

    slots = _build_slots(open_min, close_min, full_min, step)

    # Second-resolution alignment — adversarial-review C3.
    # We're "in" a slot if `now_sec - slot_sec` is in [0, tolerance].
    now_sec = now_pt.hour * 3600 + now_pt.minute * 60 + now_pt.second
    fired_slot_min: Optional[int] = None
    if _is_session_day(today_pt, weekdays_only):
        for s in slots:
            slot_sec = s * 60
            if 0 <= (now_sec - slot_sec) <= tolerance:
                fired_slot_min = s  # keep the largest slot we're aligned with
                # (don't break — slots are sorted; later ones with same-second-mod won't match)

    # Decide MODE for this tick.
    today_str = today_pt.isoformat()
    is_today_session = _is_session_day(today_pt, weekdays_only)
    in_window = is_today_session and (open_min * 60) <= now_sec < (close_min * 60)

    mode: Mode
    if not in_window:
        mode = "IDLE"
    elif fired_slot_min is None:
        mode = "IDLE"          # mid-tick wake-up between slots
    elif marker != today_str and fired_slot_min >= full_min:
        # catch-up: if the anchor was missed (crash/restart churn), the next slot re-fires FULL
        mode = "FULL"
    else:
        mode = "MONITOR"

    # --- Compute next_wake_utc ---
    next_wake_pt: datetime

    # Find the next slot strictly AFTER the alignment of `now`.
    # If we just fired slot S, next is the first slot > S; otherwise next is
    # the first slot > now_sec/60 (with sub-minute precision via tolerance).
    next_slot_min: Optional[int] = None
    for s in slots:
        slot_sec = s * 60
        # If we ALREADY fired this slot (now_sec is in [slot_sec, slot_sec+tol]),
        # the next wake is strictly after it.
        if fired_slot_min is not None and s <= fired_slot_min:
            continue
        # Otherwise we want the first slot whose start is strictly later than now.
        if slot_sec > now_sec:
            next_slot_min = s
            break

    if not is_today_session or next_slot_min is None or now_sec >= close_min * 60:
        # Past last slot today (or weekend) — wake at next session day's open.
        next_day = today_pt
        if (
            not is_today_session
            or now_sec >= close_min * 60
            or next_slot_min is None
        ):
            next_day = _next_session_day(today_pt, weekdays_only)
        next_wake_pt = _pt_at(next_day, open_min, pt)
    else:
        next_wake_pt = _pt_at(today_pt, next_slot_min, pt)

    next_wake_utc = next_wake_pt.astimezone(timezone.utc)

    # Defensive: clock-skew safety. If we computed a wake time at or before
    # `now`, bump forward by 60s so the loop guarantees forward progress.
    if next_wake_utc <= now:
        next_wake_utc = now + timedelta(seconds=60)

    return next_wake_utc, mode


__all__ = ["get_next_wake", "Mode", "DEFAULT_CONFIG"]
