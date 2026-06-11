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


def _discord_sink(channel: str, content: str, embed: Optional[dict],
                  notif_key: Optional[str] = None) -> None:
    # Reuse the existing redact/scrub + graceful-degrade + log-line path. The
    # explicit ``notif_key`` rides through to the outbox so the router classifies
    # precisely (and the poller decides Discord-vs-push from prefs).
    from live_alerts import _safe_enqueue
    _safe_enqueue(channel, content, embed=embed, notif_key=notif_key)


# --- Outbox routing: the single point where ALL Discord-bound messages get
# classified and their Discord/push fate decided (used by
# interactive_utils.action_enqueue_discord_message). ----------------------------

_prefs_cache = {"id": None, "cats": None, "ts": 0.0}


def _reset_prefs_cache():
    _prefs_cache.update(id=None, cats=None, ts=0.0)


def _operator_prefs(conn):
    """(operator_id, categories matrix) — cached ~30s on the given conn so an
    alert burst doesn't re-read prefs per message."""
    import time
    op = _operator_user_id()
    now = time.time()
    if (_prefs_cache["id"] == op and _prefs_cache["cats"] is not None
            and (now - _prefs_cache["ts"]) < 30):
        return op, _prefs_cache["cats"]
    from interactive_utils import action_get_notification_preferences
    cats = action_get_notification_preferences(conn, op).get("categories", {})
    _prefs_cache.update(id=op, cats=cats, ts=now)
    return op, cats


def _derive_push_title(embed, content):
    if isinstance(embed, dict) and embed.get("title"):
        return str(embed["title"])[:120]
    return "IntelliStock"


def _derive_push_body(content, embed):
    if isinstance(content, str) and content.strip():
        return content.strip()[:220]
    if isinstance(embed, dict):
        d = embed.get("description")
        if d:
            return str(d)[:220]
    return "Notification"


def _redact_text(s):
    try:
        from intellistock_logger import _redact
        return _redact(s) if isinstance(s, str) else s
    except Exception:
        return s


def resolve_routing(conn, channel, content, embed=None, notif_key=None,
                    push_title=None, push_body=None) -> dict:
    """Classify a Discord-bound message and decide its Discord/push routing from
    the operator's prefs. Returns ``{notif_key, discord, push, push_*}``.

    FAIL-OPEN: on ANY error returns ``discord=True, push=False`` so the
    real-money Discord path is never silenced by a routing bug.
    """
    try:
        from notification_types import classify
        key = classify(channel, content, embed, notif_key)
        op, cats = _operator_prefs(conn)
        route = cats.get(key) or {"discord": True, "push": False}
        discord = bool(route.get("discord", True))
        push = bool(route.get("push", False))
        out = {"notif_key": key, "discord": discord, "push": push}
        if push:
            out["push_user_id"] = op
            out["push_title"] = _redact_text(push_title or _derive_push_title(embed, content))
            out["push_body"] = _redact_text(push_body or _derive_push_body(content, embed))
            out["push_data"] = {"notif_key": key}
        return out
    except Exception as e:
        intellistock_logger.log(
            f"notify: resolve_routing failed ({type(e).__name__}: {e}); discord-only",
            "yellow", service="NOTIFY",
        )
        return {"notif_key": notif_key or "other", "discord": True, "push": False}


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
    """Build + enqueue a notification tagged with its ``category``.

    Discord-vs-push routing is decided at the outbox (resolve_routing), so this
    just enqueues the Discord message with the explicit category — one unified
    path that also covers every direct ``enqueue_discord_message`` caller.
    """
    try:
        _discord_sink(discord_channel, body, discord_embed, notif_key=category)
    except Exception as e:
        intellistock_logger.log(
            f"notify enqueue failed [{category}]: {type(e).__name__}: {e}",
            "yellow", service="NOTIFY",
        )
