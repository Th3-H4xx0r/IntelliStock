"""Single fan-out point for live-trading notifications.

``live_alerts.alert_*`` build their human/embed content and hand it here;
``notify`` reads the per-category preference matrix and routes to Discord
and/or iOS push. Each sink is isolated so one failing never blocks trading or
the other sink — same graceful-degrade contract as the old direct
``_safe_enqueue`` call. With default preferences (Discord on, push off) this is
behavior-identical to the pre-routing code.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from intellistock_logger import intellistock_logger


def _operator_user_id() -> str:
    """Whose preferences/devices to use when a caller doesn't specify one.

    The system is effectively single-operator (``main`` = real money); the
    operator's user id is configurable so multi-user can slot in later.
    """
    return os.environ.get("NOTIFY_OPERATOR_USER_ID", "operator")


def _load_prefs(user_id: Optional[str]) -> dict:
    from interactive_utils import get_conn, action_get_notification_preferences
    conn = get_conn()
    try:
        return action_get_notification_preferences(conn, user_id or _operator_user_id())
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _discord_sink(channel: str, content: str, embed: Optional[dict]) -> None:
    # Reuse the existing redact/scrub + graceful-degrade + log-line path.
    from live_alerts import _safe_enqueue
    _safe_enqueue(channel, content, embed=embed)


def _push_sink(user_id: str, *, title: str, body: str, category: str, data: dict) -> None:
    try:
        from apns_sender import send_to_user
    except Exception:
        return  # APNs module/deps absent → push simply doesn't deliver
    send_to_user(user_id, title=title, body=body, category=category, data=data)


def notify(
    *,
    category: str,
    instance_id: str,
    title: str,
    body: str,
    discord_channel: str,
    discord_embed: Optional[dict] = None,
    push_title: Optional[str] = None,
    push_body: Optional[str] = None,
    user_id: Optional[str] = None,
    data: Optional[dict] = None,
) -> None:
    """Route one notification to the sinks enabled for ``category``."""
    try:
        prefs = _load_prefs(user_id).get("categories", {})
    except Exception as e:
        # Fail OPEN to Discord so a prefs-store outage never silences alerts
        # (preserves today's behavior).
        intellistock_logger.log(
            f"notify: prefs load failed ({type(e).__name__}: {e}); discord fallback",
            "yellow", service="NOTIFY",
        )
        prefs = {category: {"discord": True, "push": False}}

    route = prefs.get(category) or {"discord": True, "push": False}

    if route.get("discord", True):
        try:
            _discord_sink(discord_channel, body, discord_embed)
        except Exception as e:
            intellistock_logger.log(
                f"notify discord sink failed [{category}]: {type(e).__name__}: {e}",
                "yellow", service="NOTIFY",
            )

    if route.get("push", False):
        try:
            _push_sink(
                user_id or _operator_user_id(),
                title=push_title or title,
                body=push_body or body,
                category=category,
                data=data or {"category": category, "instance_id": instance_id},
            )
        except Exception as e:
            intellistock_logger.log(
                f"notify push sink failed [{category}]: {type(e).__name__}: {e}",
                "yellow", service="NOTIFY",
            )
