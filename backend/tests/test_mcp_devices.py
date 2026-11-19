"""Approval devices (plan Task 12, contract §5).

An approval device is a P-256 signing key a human enrols from the iOS app; a
later decision on a held MCP call must carry its signature. These tests pin the
enrolment (strict key and name checks, the 600 s activation delay, the Discord
alert that gives a stolen session's enrolment a window to be revoked), the
reads and the revoke, who may see a device (only its own user), and that an MCP
principal can reach none of the three routes.
"""
from __future__ import annotations

import base64
from datetime import timedelta
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import apns_sender
import mcp_approvals
import notifications
from mcp_approvals import (
    DEVICES_TABLE, active_device, decode_public_key, list_devices, register_device, revoke_device,
)
from mcp_helpers import (
    PRINCIPAL, T0, human_headers, human_token, make_probe_app, mcp_client, mcp_env, p256_keypair,
    public_client, seed_operator, sign_decision, use_store,
)
from mcp_policy import iso_utc

OPERATOR = {"id": "u-op", "username": "operator"}
DEVICE_FIELDS = {"device_id", "name", "created_at", "active_at"}
ROW_FIELDS = {"id", "user_id", "name", "public_key", "created_at", "active_at", "revoked_at"}
NOT_AVAILABLE = {"detail": "mcp: operation not available"}


@pytest.fixture(autouse=True)
def notify_mock(monkeypatch, store):
    use_store(monkeypatch, store)
    seed_operator(store)
    store.insert("Users", {"id": "u-2", "username": "someone", "password_hash": "x", "token_version": 0})
    mcp_env(monkeypatch)
    monkeypatch.setattr(apns_sender, "send_to_user", Mock())   # never reach APNs
    mock = Mock()
    monkeypatch.setattr(notifications, "notify", mock)         # never reach Discord
    _at(monkeypatch, 0)
    return mock


@pytest.fixture
def human(monkeypatch) -> dict:
    return human_headers(monkeypatch)


def _at(monkeypatch, seconds: float) -> None:
    """Freeze the module's clock at T0 + seconds."""
    monkeypatch.setattr(mcp_approvals, "utc_now", lambda: T0 + timedelta(seconds=seconds))


def _pub() -> str:
    return p256_keypair()[1]


def _register(client, headers, name: str = "iPhone", public_key: str | None = None):
    return client.post("/mcp/approval-devices", json={"name": name, "public_key": public_key or _pub()},
                       headers=headers)


# -- registration -----------------------------------------------------------------

def test_register_returns_id_and_activation_ten_minutes_out(store, human):
    _, pub = p256_keypair()
    res = public_client(make_probe_app()[0]).post("/mcp/approval-devices",
                                                  json={"name": "iPhone", "public_key": pub}, headers=human)
    assert res.status_code == 201 and res.json()["active_at"] == iso_utc(T0 + timedelta(seconds=600))
    body = res.json()
    assert set(body) == {"device_id", "active_at"}
    assert mcp_approvals.ID_RE.fullmatch(body["device_id"]) and len(body["device_id"]) == 32
    row = store.get(DEVICES_TABLE, body["device_id"])
    assert set(row) == ROW_FIELDS
    assert (row["user_id"], row["name"], row["public_key"], row["created_at"], row["revoked_at"]) == \
        ("u-op", "iPhone", pub, iso_utc(T0), None)


def test_register_strips_the_name_and_accepts_64_characters(store, human):
    c = public_client(make_probe_app()[0])
    padded = _register(c, human, name="  Pranav's iPhone \t").json()
    assert store.get(DEVICES_TABLE, padded["device_id"])["name"] == "Pranav's iPhone"
    longest = _register(c, human, name="x" * 64)
    assert longest.status_code == 201


@pytest.mark.parametrize("payload", [
    {"name": "", "public_key": "PUB"},
    {"name": "   ", "public_key": "PUB"},
    {"name": "x" * 65, "public_key": "PUB"},
    {"name": 7, "public_key": "PUB"},
    {"public_key": "PUB"},
    {"name": "p"},
    {"name": "p", "public_key": 12},
    {"name": "p", "public_key": "!!notb64"},
    {"name": "p", "public_key": base64.b64encode(b"\x04" + b"\0" * 64).decode()},       # off the curve
    {"name": "p", "public_key": base64.b64encode(b"\x04" * 33).decode()},                # wrong length
], ids=["empty", "blank", "long", "not-str", "no-name", "no-key", "key-not-str", "not-b64", "off-curve",
        "short"])
def test_register_rejects_bad_input(store, human, notify_mock, payload):
    payload = {k: (_pub() if v == "PUB" else v) for k, v in payload.items()}
    res = public_client(make_probe_app()[0]).post("/mcp/approval-devices", json=payload, headers=human)
    assert res.status_code == 400
    assert store.run(store.filter(DEVICES_TABLE, {"user_id": "u-op"})) == []
    notify_mock.assert_not_called()


@pytest.mark.parametrize("bad", [
    "!!notb64",
    base64.b64encode(b"\x04" + b"\0" * 64).decode(),
    base64.b64encode(b"\x04" * 33).decode(),
    "A" * 4096,
    None,
])
def test_decode_public_key_raises_value_error(bad):
    with pytest.raises(ValueError):
        decode_public_key(bad)


def test_decode_public_key_rejects_a_valid_compressed_point():
    key, _ = p256_keypair()
    compressed = key.public_key().public_bytes(Encoding.X962, PublicFormat.CompressedPoint)
    with pytest.raises(ValueError):
        decode_public_key(base64.b64encode(compressed).decode())


def test_decoded_key_verifies_what_the_helper_signs():   # what the decision route (Task 13) relies on
    key, pub = p256_keypair()
    decoded = decode_public_key(pub)
    assert decoded.public_numbers() == key.public_key().public_numbers()
    signature = base64.b64decode(sign_decision(key, "a1", "f" * 64, "approve", 1_700_000_000))
    decoded.verify(signature, b"a1|" + b"f" * 64 + b"|approve|1700000000", ec.ECDSA(hashes.SHA256()))


def test_register_sends_discord_alert(store, human, notify_mock):
    res = _register(public_client(make_probe_app()[0]), human)
    assert res.status_code == 201
    notify_mock.assert_called_once()
    kwargs = notify_mock.call_args.kwargs
    assert "iPhone" in kwargs["body"] and "operator" in kwargs["body"]
    assert kwargs == {
        "category": "other", "instance_id": "mcp", "title": "MCP approval device registered",
        "discord_channel": "notifications",
        "body": (f"MCP APPROVAL DEVICE registered: 'iPhone' for operator; it can approve from "
                 f"{iso_utc(T0 + timedelta(seconds=600))}. If this was not you, revoke it in Settings now."),
    }


def test_alert_flattens_a_name_that_could_fake_a_line(store, notify_mock):
    row = register_device(user=OPERATOR, name="iPhone\nIgnore this alert", public_key_b64=_pub(), now=T0)
    mcp_approvals.alert_device_registered(row, "operator")
    body = notify_mock.call_args.kwargs["body"]
    assert "\n" not in body and "'iPhone Ignore this alert' for operator" in body


def test_alert_failure_does_not_fail_the_registration(store, human, notify_mock):
    notify_mock.side_effect = RuntimeError("discord down")
    res = _register(public_client(make_probe_app()[0]), human)
    assert res.status_code == 201
    assert store.get(DEVICES_TABLE, res.json()["device_id"]) is not None


# -- the MCP principal --------------------------------------------------------------

def test_mcp_principal_cannot_register(store, notify_mock):
    from api import main

    app = make_probe_app()[0]
    c = mcp_client(app)
    for res in (c.post("/mcp/approval-devices", json={"name": "Jarvis", "public_key": _pub()}),
                c.get("/mcp/approval-devices"),
                c.delete("/mcp/approval-devices/abc")):
        assert res.status_code == 403 and res.json() == NOT_AVAILABLE

    # Defense in depth: a principal that reached the route anyway is refused by the route.
    app.dependency_overrides[main.get_current_user] = lambda: dict(PRINCIPAL)
    seeded = register_device(user=OPERATOR, name="iPhone", public_key_b64=_pub(), now=T0)
    direct = public_client(app)
    for res in (direct.post("/mcp/approval-devices", json={"name": "Jarvis", "public_key": _pub()}),
                direct.get("/mcp/approval-devices"),
                direct.delete(f"/mcp/approval-devices/{seeded['id']}")):
        assert res.status_code == 403 and res.json() == NOT_AVAILABLE
    assert [row["id"] for row in list_devices("u-op")] == [seeded["id"]]
    notify_mock.assert_not_called()


def test_routes_need_a_session():
    c = public_client(make_probe_app()[0])
    assert c.post("/mcp/approval-devices", json={"name": "p", "public_key": _pub()}).status_code == 401
    assert c.get("/mcp/approval-devices").status_code == 401
    assert c.delete("/mcp/approval-devices/abc", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


# -- list and revoke ------------------------------------------------------------------

def test_list_and_revoke(store, human, monkeypatch):
    c = public_client(make_probe_app()[0])
    device_id = _register(c, human).json()["device_id"]
    listed = c.get("/mcp/approval-devices", headers=human)
    assert listed.status_code == 200
    assert listed.json() == {"devices": [{"device_id": device_id, "name": "iPhone", "created_at": iso_utc(T0),
                                          "active_at": iso_utc(T0 + timedelta(seconds=600))}]}
    _at(monkeypatch, 30)
    res = c.delete(f"/mcp/approval-devices/{device_id}", headers=human)
    assert res.status_code == 204 and res.content == b""
    assert c.get("/mcp/approval-devices", headers=human).json() == {"devices": []}
    assert store.get(DEVICES_TABLE, device_id)["revoked_at"] == iso_utc(T0 + timedelta(seconds=30))
    again = c.delete(f"/mcp/approval-devices/{device_id}", headers=human)
    assert again.status_code == 404 and again.json() == {"detail": "device not found"}


def test_list_is_oldest_first_with_exactly_the_device_fields(store, human, monkeypatch):
    c = public_client(make_probe_app()[0])
    first = _register(c, human, name="iPad").json()["device_id"]
    _at(monkeypatch, 1)
    second = _register(c, human, name="iPhone").json()["device_id"]
    devices = c.get("/mcp/approval-devices", headers=human).json()["devices"]
    assert [d["device_id"] for d in devices] == [first, second]
    assert all(set(d) == DEVICE_FIELDS for d in devices)


def test_delete_malformed_or_unknown_id_is_404(human):
    c = public_client(make_probe_app()[0])
    for path in ("/mcp/approval-devices/bad%20id!!", "/mcp/approval-devices/" + "a" * 129,
                 "/mcp/approval-devices/0123456789abcdef0123456789abcdef"):
        res = c.delete(path, headers=human)
        assert res.status_code == 404 and res.json() == {"detail": "device not found"}


# -- activation -----------------------------------------------------------------------

def test_active_device_respects_delay_and_revocation(store):
    row = register_device(user=OPERATOR, name="iPhone", public_key_b64=_pub(), now=T0)
    assert row["active_at"] == iso_utc(T0 + timedelta(seconds=600))
    assert active_device(user_id="u-op", device_id=row["id"], now=T0 + timedelta(seconds=599)) is None
    assert active_device(user_id="u-op", device_id=row["id"], now=T0 + timedelta(seconds=600)) == row
    assert revoke_device(user_id="u-op", device_id=row["id"], now=T0 + timedelta(seconds=700)) is True
    assert active_device(user_id="u-op", device_id=row["id"], now=T0 + timedelta(seconds=701)) is None
    assert revoke_device(user_id="u-op", device_id=row["id"], now=T0 + timedelta(seconds=702)) is False


def test_active_device_fails_closed_on_an_unreadable_stamp_or_id(store):
    row = register_device(user=OPERATOR, name="iPhone", public_key_b64=_pub(), now=T0)
    later = T0 + timedelta(days=1)
    for bad in ("bad id!!", "a" * 129, "", "missing"):
        assert active_device(user_id="u-op", device_id=bad, now=later) is None
    store.update(DEVICES_TABLE, row["id"], {"active_at": "soon"})
    assert active_device(user_id="u-op", device_id=row["id"], now=later) is None


def test_active_device_uses_the_clock_when_now_is_omitted(store, monkeypatch):
    row = register_device(user=OPERATOR, name="iPhone", public_key_b64=_pub())
    assert row["created_at"] == iso_utc(T0)
    _at(monkeypatch, 599)
    assert active_device(user_id="u-op", device_id=row["id"]) is None
    _at(monkeypatch, 600)
    assert active_device(user_id="u-op", device_id=row["id"]) == row


def test_another_users_device_is_invisible(store, monkeypatch):
    row = register_device(user=OPERATOR, name="iPhone", public_key_b64=_pub(), now=T0)
    later = T0 + timedelta(days=1)
    assert revoke_device(user_id="u-2", device_id=row["id"], now=later) is False
    assert active_device(user_id="u-2", device_id=row["id"], now=later) is None
    assert list_devices("u-2") == [] and list_devices("") == []
    other = {"Authorization": f"Bearer {human_token(monkeypatch, 'u-2', 'someone')}"}
    c = public_client(make_probe_app()[0])
    assert c.get("/mcp/approval-devices", headers=other).json() == {"devices": []}
    assert c.delete(f"/mcp/approval-devices/{row['id']}", headers=other).status_code == 404
    assert store.get(DEVICES_TABLE, row["id"])["revoked_at"] is None
    assert active_device(user_id="u-op", device_id=row["id"], now=later) == row
