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


_operator_cache = {"id": None, "ts": 0.0}


def _reset_operator_cache():
    _operator_cache.update(id=None, ts=0.0)


def _resolve_operator() -> Optional[str]:
    """Resolve whose prefs/devices the live-alert path targets.

    The canonical signal is **the user who registered a push device** — that's
    whoever set up push, on their own account. There is no special admin user.
    Falls back to the sole user if exactly one exists and none have devices yet.
    """
    try:
        from interactive_utils import get_conn, action_push_device_user_ids
        conn = get_conn()
        try:
            device_users = action_push_device_user_ids(conn)
            if len(device_users) == 1:
                return device_users[0]
            if len(device_users) > 1:
                intellistock_logger.log(
                    "notify: multiple users have push devices; set "
                    "NOTIFY_OPERATOR_USER_ID to disambiguate",
                    "yellow", service="NOTIFY",
                )
                return None
            # No devices registered yet — use the sole user if there's just one.
            try:
                from auth_utils import list_users
                users = list_users(conn) or []
                if len(users) == 1:
                    return users[0].get("id")
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as e:
        intellistock_logger.log(
            f"notify: operator resolve failed: {type(e).__name__}: {e}",
            "yellow", service="NOTIFY",
        )
    return None


def _operator_user_id() -> str:
    """Whose preferences/devices to use when a caller doesn't specify one.

    Env override wins; otherwise resolve from the DB (cached ~2 min so an alert
    burst doesn't re-query every time, while a newly-registered device is picked
    up quickly).
    """
    env = os.environ.get("NOTIFY_OPERATOR_USER_ID", "").strip()
    if env:
        return env
    import time
    now = time.time()
    if _operator_cache["id"] and (now - _operator_cache["ts"]) < 120:
        return _operator_cache["id"]
    resolved = _resolve_operator()
    if resolved:
        _operator_cache.update(id=resolved, ts=now)
        return resolved
    return "operator"


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
    # Push title/body are plaintext on Apple's servers + the lock screen — run
    # them through the same redact filter the Discord path uses so an error
    # message / reason text can never leak a key or token.
    try:
        from intellistock_logger import _redact
        title = _redact(title) if isinstance(title, str) else title
        body = _redact(body) if isinstance(body, str) else body
    except Exception:
        pass
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
    # Resolve the target user ONCE so prefs and devices use the same identity
    # (the live-alert path passes no user_id; it must resolve to whoever set up
    # push, not the literal default "operator").
    target_user = user_id or _operator_user_id()
    try:
        prefs = _load_prefs(target_user).get("categories", {})
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
                target_user,
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
