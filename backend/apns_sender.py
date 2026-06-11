"""Direct Apple Push Notification service (APNs) sender — token-based, no
Firebase.

Auth is an ES256 provider JWT signed with a ``.p8`` auth key (cached ~50 min,
within Apple's ≤60-min lifetime). Transport is HTTP/2 (required by APNs) via
httpx. iOS-only for now; Android/FCM can slot in behind ``notifications.notify``
later.

Config (env, or secret_store-backed via ``_load_creds``):
  APNS_KEY_ID, APNS_TEAM_ID, APNS_BUNDLE_ID,
  APNS_KEY_PATH (path to .p8) or APNS_KEY (PEM/p8 contents),
  APNS_ENV = sandbox | prod (default prod).

If any required credential is absent the module no-ops (logged once) so push
simply doesn't deliver — Discord and trading are unaffected.
"""
from __future__ import annotations

import os
from typing import Optional

from intellistock_logger import intellistock_logger

# JWT lifetime: Apple allows one token per 20 min and ≤60 min validity. Refresh
# at 50 min to stay comfortably inside the window.
_JWT_TTL_SEC = 50 * 60
_jwt_cache = {"token": None, "iat": 0.0, "key_id": None, "team_id": None}
_creds_missing_logged = False


def _log(msg, color="yellow"):
    try:
        intellistock_logger.log(msg, color, service="APNS")
    except Exception:
        pass


def _apns_host(env: str) -> str:
    return "api.sandbox.push.apple.com" if str(env).lower() == "sandbox" else "api.push.apple.com"


def _load_creds() -> Optional[dict]:
    """Assemble APNs credentials from env / secret_store. Returns None (and logs
    once) if any required piece is missing."""
    global _creds_missing_logged
    key_id = os.environ.get("APNS_KEY_ID", "").strip()
    team_id = os.environ.get("APNS_TEAM_ID", "").strip()
    bundle_id = os.environ.get("APNS_BUNDLE_ID", "").strip()
    env = os.environ.get("APNS_ENV", "prod").strip() or "prod"
    key = os.environ.get("APNS_KEY", "").strip()
    if not key:
        key_path = os.environ.get("APNS_KEY_PATH", "").strip()
        if key_path and os.path.exists(key_path):
            try:
                with open(key_path, "r") as f:
                    key = f.read().strip()
            except Exception as e:
                _log(f"APNs key read failed: {type(e).__name__}: {e}")
                key = ""
    if not (key_id and team_id and bundle_id and key):
        if not _creds_missing_logged:
            _log("APNs credentials incomplete — iOS push disabled "
                 "(set APNS_KEY_ID/APNS_TEAM_ID/APNS_BUNDLE_ID/APNS_KEY[_PATH])")
            _creds_missing_logged = True
        return None
    return {"key": key, "key_id": key_id, "team_id": team_id,
            "bundle_id": bundle_id, "env": env}


def _reset_jwt_cache():
    _jwt_cache.update({"token": None, "iat": 0.0, "key_id": None, "team_id": None})


def _build_jwt(creds: dict, now_ts: float) -> str:
    """ES256 provider token, cached until ~50 min old."""
    import jwt as pyjwt
    cached = _jwt_cache["token"]
    if (cached
            and _jwt_cache["key_id"] == creds["key_id"]
            and _jwt_cache["team_id"] == creds["team_id"]
            and (now_ts - _jwt_cache["iat"]) < _JWT_TTL_SEC):
        return cached
    token = pyjwt.encode(
        {"iss": creds["team_id"], "iat": int(now_ts)},
        creds["key"],
        algorithm="ES256",
        headers={"kid": creds["key_id"]},
    )
    if isinstance(token, bytes):  # PyJWT <2 returned bytes
        token = token.decode()
    _jwt_cache.update({"token": token, "iat": now_ts,
                       "key_id": creds["key_id"], "team_id": creds["team_id"]})
    return token


def _build_payload(title: str, body: str, category: str, data: Optional[dict]) -> dict:
    payload = {"aps": {"alert": {"title": title, "body": body}, "sound": "default"}}
    if data:
        for k, v in data.items():
            if k != "aps":
                payload[k] = v
    payload["category"] = category
    return payload


# --- I/O seams (monkeypatched in tests) --------------------------------------

def _list_devices(user_id: str) -> list:
    from interactive_utils import get_conn, action_list_push_devices
    env = os.environ.get("APNS_ENV", "prod").strip() or "prod"
    conn = get_conn()
    try:
        return action_list_push_devices(conn, user_id, env=env)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _delete_device(token: str) -> None:
    from interactive_utils import get_conn, action_delete_push_device
    conn = get_conn()
    try:
        action_delete_push_device(conn, token)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _send_one(token: str, payload: dict, creds: dict, host: str) -> int:
    """POST one notification over HTTP/2. Returns the APNs status code (or 0 on
    a transport error). Never raises."""
    import time
    import httpx
    jwt_token = _build_jwt(creds, time.time())
    headers = {
        "authorization": f"bearer {jwt_token}",
        "apns-topic": creds["bundle_id"],
        "apns-push-type": "alert",
        "apns-priority": "10",
    }
    url = f"https://{host}/3/device/{token}"
    try:
        with httpx.Client(http2=True, timeout=10.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            return resp.status_code
    except Exception as e:
        _log(f"APNs POST failed for token …{token[-6:]}: {type(e).__name__}: {e}")
        return 0


def send_to_user(user_id: str, *, title: str, body: str, category: str, data: dict) -> dict:
    """Deliver a push to all of a user's registered iOS devices. Best-effort:
    never raises. Prunes tokens APNs reports as gone (410)."""
    creds = _load_creds()
    if not creds:
        return {"sent": 0, "skipped": "no_creds"}
    host = _apns_host(creds["env"])
    payload = _build_payload(title, body, category, data)
    sent = 0
    pruned = 0
    try:
        devices = _list_devices(user_id)
    except Exception as e:
        _log(f"APNs device lookup failed: {type(e).__name__}: {e}")
        return {"sent": 0, "error": "device_lookup_failed"}
    for d in (devices or []):
        token = (d or {}).get("device_token") or (d or {}).get("id")
        if not token:
            continue
        try:
            status = _send_one(token, payload, creds, host)
        except Exception as e:
            _log(f"APNs send error: {type(e).__name__}: {e}")
            continue
        if status == 200:
            sent += 1
        elif status == 410:
            # Token no longer valid — prune it.
            try:
                _delete_device(token)
                pruned += 1
            except Exception:
                pass
        # other statuses (400/403/429/5xx) are logged by _send_one; left in
        # place so a transient failure can succeed on the next notification.
    return {"sent": sent, "pruned": pruned, "devices": len(devices or [])}
