"""Swing & wheel notifications (replaces ST notify.py's Pushover sender).

ST sent Pushover messages at priority 0, 1 or 2. IntelliStock routes by
category (backend/notification_types.py) to Discord and iOS push (spec §9
item 13), so the category decides the routing and a priority-2 message is
marked URGENT in its text.
"""
from __future__ import annotations

PREFIXES = {
    "swing_entry": "SWING ENTRY",
    "swing_pending_review": "SWING REVIEW",
    "swing_exit": "SWING EXIT",
    "swing_run_summary": "SWING RUN",
    "wheel_put_placed": "WHEEL PUT",
    "wheel_pending_review": "WHEEL REVIEW",
    "wheel_position_alert": "WHEEL ALERT",
    "wheel_assignment": "WHEEL ASSIGNMENT",
    "swing_approval_failed": "SWING APPROVAL FAILED",
    "strategy_error": "STRATEGY ERROR",
}

try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="SwingNotify")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[SwingNotify] {msg}")


def _sink(**kwargs):
    from notifications import notify as _notify
    _notify(**kwargs)


def send(category, instance_id, title, message, *, priority=0) -> None:
    """Enqueue one notification. Never raises: a notification failure must
    never cost a scan its orders."""
    try:
        from notification_types import type_for_key
        meta = type_for_key(category) or {}
        prefix = PREFIXES.get(category, str(category).upper())
        urgent = " (URGENT)" if int(priority or 0) >= 2 else ""
        body = f"{prefix} [{instance_id}] {title}{urgent}\n{message}"
        _sink(category=category, instance_id=str(instance_id), title=str(title),
              body=body, discord_channel=meta.get("channel") or "notifications",
              push_title=f"{title}{urgent}"[:120], push_body=str(message)[:220])
    except Exception as exc:
        _log(f"notify failed [{category}]: {type(exc).__name__}: {exc}", "yellow")


def notify_wheel_assignment(instance_id, *, symbol, qty, price=None, date=None) -> None:
    """Plan A-live's activities poller calls this on an OPASN activity.
    Never raises, like send(): a bad field costs the text, not the poller."""
    try:
        detail = f"{symbol}: assigned {float(qty):g} shares"
        if price:
            detail += f" @ ${float(price):.2f}"
        if date:
            detail += f" on {date}"
    except Exception as exc:
        _log(f"notify failed [wheel_assignment]: {type(exc).__name__}: {exc}", "yellow")
        detail = f"{symbol}: assigned {qty} shares"
    send("wheel_assignment", instance_id, f"Wheel assignment: {symbol}", detail, priority=1)


def notify_swing_approval_failed(instance_id, *, symbol, lane, reason) -> None:
    """Plan A-live's approval handler calls this when an order the operator
    approved could not be rebuilt or the broker refused it: the operator
    believes that trade is on, so this category pushes by default."""
    lane_name = str(lane or "swing")
    why = str(reason or "no reason given")[:300]
    send("swing_approval_failed", instance_id,
         f"Approved {lane_name} order refused: {symbol}",
         f"{symbol}: the {lane_name} order you approved was not sent — {why}",
         priority=1)
