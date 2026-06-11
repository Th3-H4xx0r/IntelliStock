"""Direct APNs sender — token-based, no Firebase. httpx + Apple are mocked via
module seams so nothing leaves the machine.
"""
from __future__ import annotations

import pytest


def _ec_pem():
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _creds():
    return {
        "key": _ec_pem(),
        "key_id": "KID123",
        "team_id": "TEAM123",
        "bundle_id": "dev.pkrishna.intellistockMobile",
        "env": "prod",
    }


def test_apns_host_selects_env():
    import apns_sender
    assert apns_sender._apns_host("prod") == "api.push.apple.com"
    assert apns_sender._apns_host("sandbox") == "api.sandbox.push.apple.com"


def test_build_payload_shape():
    import apns_sender
    p = apns_sender._build_payload("Filled AAPL", "BUY 1 AAPL", "order_fill", {"instance_id": "i1"})
    assert p["aps"]["alert"] == {"title": "Filled AAPL", "body": "BUY 1 AAPL"}
    assert p["aps"]["sound"] == "default"
    assert p["category"] == "order_fill"
    assert p["instance_id"] == "i1"


def test_build_jwt_header_and_claims_and_cache():
    import jwt as pyjwt
    import apns_sender
    apns_sender._reset_jwt_cache()
    creds = _creds()
    tok = apns_sender._build_jwt(creds, now_ts=1000.0)
    hdr = pyjwt.get_unverified_header(tok)
    assert hdr["alg"] == "ES256" and hdr["kid"] == "KID123"
    claims = pyjwt.decode(tok, options={"verify_signature": False})
    assert claims["iss"] == "TEAM123" and claims["iat"] == 1000
    # cached within the refresh window -> identical token
    tok2 = apns_sender._build_jwt(creds, now_ts=1200.0)
    assert tok2 == tok


def test_send_to_user_noop_without_creds(monkeypatch):
    import apns_sender
    monkeypatch.setattr(apns_sender, "_load_creds", lambda: None)
    sent_flag = {"called": False}
    monkeypatch.setattr(apns_sender, "_send_one", lambda *a, **k: (sent_flag.__setitem__("called", True), (200, ""))[1])
    res = apns_sender.send_to_user("u1", title="t", body="b", category="order_fill", data={})
    assert res["sent"] == 0
    assert sent_flag["called"] is False


def test_send_to_user_delivers_and_counts(monkeypatch):
    import apns_sender
    monkeypatch.setattr(apns_sender, "_load_creds", _creds)
    monkeypatch.setattr(apns_sender, "_list_devices",
                        lambda uid: [{"device_token": "TOK1", "env": "prod"}])
    posted = {}
    def _fake_send_one(token, payload, creds, host):
        posted["token"] = token
        posted["host"] = host
        posted["topic"] = creds["bundle_id"]
        return 200, ""
    monkeypatch.setattr(apns_sender, "_send_one", _fake_send_one)
    res = apns_sender.send_to_user("u1", title="t", body="b", category="order_fill", data={})
    assert res["sent"] == 1
    assert posted["token"] == "TOK1"
    assert posted["host"] == "api.push.apple.com"
    assert posted["topic"] == "dev.pkrishna.intellistockMobile"


def test_send_to_user_picks_host_per_device_env(monkeypatch):
    """A single server must serve BOTH sandbox (debug) and prod (release)
    tokens — each routed to its own APNs host — instead of dropping one env."""
    import apns_sender
    monkeypatch.setattr(apns_sender, "_load_creds", _creds)
    monkeypatch.setattr(apns_sender, "_list_devices", lambda uid: [
        {"device_token": "PROD_TOK", "env": "prod"},
        {"device_token": "SBOX_TOK", "env": "sandbox"},
    ])
    hosts = {}
    def _fake(token, payload, creds, host):
        hosts[token] = host
        return 200, ""
    monkeypatch.setattr(apns_sender, "_send_one", _fake)
    res = apns_sender.send_to_user("u1", title="t", body="b", category="order_fill", data={})
    assert res["sent"] == 2
    assert hosts["PROD_TOK"] == "api.push.apple.com"
    assert hosts["SBOX_TOK"] == "api.sandbox.push.apple.com"


def test_send_to_user_retries_other_host_on_bad_token(monkeypatch):
    """A token registered as 'prod' but actually a sandbox token (the
    aps-environment=development + release-build mismatch) returns 400
    BadDeviceToken on the prod host; we retry the sandbox host, succeed, and
    persist the corrected env."""
    import apns_sender
    monkeypatch.setattr(apns_sender, "_load_creds", _creds)
    monkeypatch.setattr(apns_sender, "_list_devices",
                        lambda uid: [{"device_token": "TOK", "env": "prod"}])
    seen = []
    def _fake(token, payload, creds, host):
        seen.append(host)
        if host == "api.push.apple.com":
            return 400, "BadDeviceToken"
        return 200, ""
    monkeypatch.setattr(apns_sender, "_send_one", _fake)
    corrected = {}
    monkeypatch.setattr(apns_sender, "_update_device_env",
                        lambda tok, env: corrected.update(token=tok, env=env))
    res = apns_sender.send_to_user("u1", title="t", body="b", category="order_fill", data={})
    assert res["sent"] == 1
    assert seen == ["api.push.apple.com", "api.sandbox.push.apple.com"]
    assert corrected == {"token": "TOK", "env": "sandbox"}
    assert "errors" not in res


def test_send_to_user_surfaces_reason_when_both_hosts_fail(monkeypatch):
    import apns_sender
    monkeypatch.setattr(apns_sender, "_load_creds", _creds)
    monkeypatch.setattr(apns_sender, "_list_devices",
                        lambda uid: [{"device_token": "TOK", "env": "prod"}])
    monkeypatch.setattr(apns_sender, "_send_one", lambda *a, **k: (400, "BadDeviceToken"))
    monkeypatch.setattr(apns_sender, "_update_device_env", lambda tok, env: None)
    res = apns_sender.send_to_user("u1", title="t", body="b", category="order_fill", data={})
    assert res["sent"] == 0
    assert res["errors"][0]["reason"] == "BadDeviceToken"
    assert res["errors"][0]["status"] == 400


def test_send_to_user_prunes_410(monkeypatch):
    import apns_sender
    monkeypatch.setattr(apns_sender, "_load_creds", _creds)
    monkeypatch.setattr(apns_sender, "_list_devices",
                        lambda uid: [{"device_token": "DEADTOK", "env": "prod"}])
    monkeypatch.setattr(apns_sender, "_send_one", lambda *a, **k: (410, "Unregistered"))
    pruned = []
    monkeypatch.setattr(apns_sender, "_delete_device", lambda tok: pruned.append(tok))
    res = apns_sender.send_to_user("u1", title="t", body="b", category="order_fill", data={})
    assert res["sent"] == 0
    assert pruned == ["DEADTOK"]


def test_send_to_user_never_raises(monkeypatch):
    import apns_sender
    monkeypatch.setattr(apns_sender, "_load_creds", _creds)
    def _boom(uid):
        raise RuntimeError("rethink down")
    monkeypatch.setattr(apns_sender, "_list_devices", _boom)
    # must swallow and return a result
    res = apns_sender.send_to_user("u1", title="t", body="b", category="order_fill", data={})
    assert res["sent"] == 0
