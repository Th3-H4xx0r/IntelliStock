"""Wiring tests for /push/devices and /notifications/test (called directly;
httpx/TestClient absent from venv, mirroring the other api tests)."""
from __future__ import annotations

import os
import sys

import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def test_register_push_device(monkeypatch):
    from api import main
    saved = {}

    def _fake_reg(conn, uid, token, platform, env, app_version):
        saved.update(uid=uid, token=token, platform=platform, env=env, app_version=app_version)
        return {"id": token, "user_id": uid}

    monkeypatch.setattr(main, "action_register_push_device", _fake_reg)
    body = main.PushDeviceBody(device_token="TOK1", platform="ios", env="sandbox", app_version="1.0")
    res = main.api_register_push_device(body=body, conn=None, current_user={"id": "u1"})
    assert saved == {"uid": "u1", "token": "TOK1", "platform": "ios", "env": "sandbox", "app_version": "1.0"}
    assert res["id"] == "TOK1"


def test_delete_push_device(monkeypatch):
    from api import main
    deleted = {}
    monkeypatch.setattr(main, "action_delete_push_device",
                        lambda conn, token, user_id: deleted.update(token=token, user_id=user_id) or {"deleted": token})
    res = main.api_delete_push_device(token="TOK1", conn=None, current_user={"id": "u1"})
    assert deleted["token"] == "TOK1"
    assert deleted["user_id"] == "u1"  # delete scoped to caller
    assert res["deleted"] == "TOK1"


def test_test_notification_discord(monkeypatch):
    from api import main
    import notifications
    calls = []
    monkeypatch.setattr(notifications, "_discord_sink",
                        lambda ch, content, embed: calls.append((ch, content, embed)))
    body = main.TestNotificationBody(channel="discord")
    res = main.api_test_notification(body=body, conn=None, current_user={"id": "u1"})
    assert res["ok"] is True
    assert len(calls) == 1


def test_test_notification_push(monkeypatch):
    from api import main
    import apns_sender
    seen = {}
    def _fake(uid, **kw):
        seen.update(uid=uid, **kw)
        return {"sent": 1, "devices": 1}
    monkeypatch.setattr(apns_sender, "send_to_user", _fake)
    body = main.TestNotificationBody(channel="push")
    res = main.api_test_notification(body=body, conn=None, current_user={"id": "u1"})
    assert res["ok"] is True and res["sent"] == 1
    assert seen["uid"] == "u1" and seen["category"] == "test"


def test_test_notification_push_reports_no_devices(monkeypatch):
    from api import main
    import apns_sender
    monkeypatch.setattr(apns_sender, "send_to_user", lambda uid, **kw: {"sent": 0, "devices": 0})
    body = main.TestNotificationBody(channel="push")
    res = main.api_test_notification(body=body, conn=None, current_user={"id": "u1"})
    assert res["ok"] is False and res["sent"] == 0


def test_test_notification_unknown_channel():
    from api import main
    from fastapi import HTTPException
    body = main.TestNotificationBody(channel="carrier_pigeon")
    with pytest.raises(HTTPException) as ei:
        main.api_test_notification(body=body, conn=None, current_user={"id": "u1"})
    assert ei.value.status_code == 400
