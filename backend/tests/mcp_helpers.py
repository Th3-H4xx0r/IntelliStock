"""Shared harness for the MCP gate tests (backend/tests/test_mcp_*.py).

A plain module the tests import by name, like ``live_order_task8_helpers``.
"""
from __future__ import annotations

import base64
import importlib
import os
import uuid
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from mcp_policy import McpConfig

MCP_TOKEN = "t" * 40

# Socket peers inside and outside the default MCP_ALLOWED_CIDR (172.31.250.10/32,
# the mcp container's pinned address on mcp_net). PEER_GATEWAY is mcp_net's host
# gateway: on the subnet, but not the mcp container, so outside the default.
PEER_IN = "172.31.250.10"
PEER_GATEWAY = "172.31.250.1"
PEER_OUT = "10.9.9.9"

# Shared constants. Time-dependent tests monkeypatch mcp_policy.utc_now (and the
# modules that import it) to return T0 + delta.
T0 = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
CFG = McpConfig.from_env({})
PRINCIPAL = {"id": "u-op", "username": "operator", "via": "mcp", "client": "jarvis"}

# Every module that binds `store` at module level and that an MCP test reads
# through. use_store skips the ones a later task has not written yet.
MCP_STORE_MODULES = ("mcp_policy", "mcp_audit", "mcp_approvals", "mcp_idempotency",
                     "mcp_caps", "auth_utils", "interactive_utils")


def mcp_env(monkeypatch, **overrides) -> None:
    """Turn the MCP listener on for one test: switch, token and port, plus overrides.

    Any MCP_* variable already in the process environment is cleared first, so a
    developer's shell can't leak into a test. An override of None unsets the
    variable instead (``mcp_env(monkeypatch, MCP_SERVICE_TOKEN=None)``).
    """
    for name in [n for n in os.environ if n.startswith("MCP_")]:
        monkeypatch.delenv(name, raising=False)
    values = {"MCP_ENABLED": "true", "MCP_SERVICE_TOKEN": MCP_TOKEN, "MCP_API_PORT": "8012", **overrides}
    for name, value in values.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, str(value))


def use_store(monkeypatch, store) -> None:
    """Point every importable module in MCP_STORE_MODULES at the test's `store` fixture.

    Only a module that does not exist is skipped: one that exists but fails to
    import (a missing dependency of its own) raises.
    """
    for name in MCP_STORE_MODULES:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:
                raise
            continue
        monkeypatch.setattr(module, "store", store)


def seed_money_fixture(store) -> None:
    """Brokerages, instances and strategies covering every money state (test_mcp_money_state).

    b-swing carries PA37JPXGU196 untrimmed and lower-case, so a match has to normalize it.
    """
    store.insert("BrokerageAccounts", [
        {"id": "b-live", "alpaca_paper": False, "alpaca_account_number": "LIVE123"},
        {"id": "b-paper", "alpaca_paper": True, "alpaca_account_number": "PA3IBY5S84PG"},
        {"id": "b-f14", "alpaca_paper": True, "alpaca_account_number": "PA3TRLBV6ZNZ"},
        {"id": "b-swing", "alpaca_paper": True, "alpaca_account_number": " pa37jpxgu196 "},
        {"id": "b-kalshi-live", "kalshi_environment": "live"},
        {"id": "b-kalshi-demo", "kalshi_environment": "demo"},
        {"id": "b-odd"},
    ])
    store.insert("Instances", [
        {"id": "alpaca-main", "brokerage_id": "b-live", "strategy_id": 200},
        {"id": "paper-1", "brokerage_id": "b-paper", "strategy_id": 201},
        {"id": "f14-paper", "brokerage_id": "b-f14", "strategy_id": 207},
        {"id": "swing-paper", "brokerage_id": "b-swing", "strategy_id": 202},
        {"id": "other-on-pa37", "brokerage_id": "b-swing", "strategy_id": 204},
        {"id": "no-broker", "strategy_id": 205},
        {"id": "kalshi-real", "brokerage_id": "b-kalshi-live", "kind": "kalshi",
         "kalshi_config": {"live_enabled": True, "paper_mode": False}},
        {"id": "kalshi-dry", "brokerage_id": "b-kalshi-live", "kind": "kalshi",
         "kalshi_config": {"live_enabled": False, "paper_mode": True}},
        {"id": "kalshi-demo", "brokerage_id": "b-kalshi-demo", "kind": "kalshi"},
        {"id": "odd-1", "brokerage_id": "b-odd"},
    ])
    store.insert("Strategies", [{"id": sid, "name": f"strategy {sid}"}
                                for sid in (200, 201, 202, 204, 205, 207, 299)])


def seed_operator(store) -> None:
    """The one human account the MCP tests act as."""
    store.insert("Users", {"id": "u-op", "username": "operator", "password_hash": "x", "token_version": 0})


def human_token(monkeypatch, user_id: str = "u-op", username: str = "operator") -> str:
    """A human JWT for user_id; JWT_SECRET is set for the test."""
    import auth_utils

    monkeypatch.setenv("JWT_SECRET", "j" * 40)
    return auth_utils.create_access_token(user_id, username, 0)


def human_headers(monkeypatch) -> dict:
    """Authorization for the operator's human session; tests bind it to HUMAN."""
    return {"Authorization": f"Bearer {human_token(monkeypatch)}"}


def p256_keypair() -> tuple[ec.EllipticCurvePrivateKey, str]:
    """A fresh approval-device key: the private key, and the public key as the iOS
    app enrols it (base64 of the 65-byte X9.62 uncompressed point)."""
    key = ec.generate_private_key(ec.SECP256R1())
    raw = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return key, base64.b64encode(raw).decode("ascii")


def sign_decision(private_key: ec.EllipticCurvePrivateKey, approval_id: str, body_sha256: str,
                  decision: str, ts: int) -> str:
    """The decision signature the iOS app sends: base64 DER ECDSA P-256 SHA-256 over
    UTF-8 f"{approval_id}|{body_sha256}|{decision}|{ts}" (contract §5). Built here
    from the contract, not from mcp_approvals, so it checks the API independently."""
    message = f"{approval_id}|{body_sha256}|{decision}|{ts}".encode("utf-8")
    return base64.b64encode(private_key.sign(message, ec.ECDSA(hashes.SHA256()))).decode("ascii")


def IDEM() -> dict:  # noqa: N802 - reads as a constant at the call site: headers=IDEM()
    """A fresh Idempotency-Key header."""
    return {"Idempotency-Key": uuid.uuid4().hex}


def mcp_client(app, *, peer: str = PEER_IN, acting_user: str | None = "u-op",
               client: str | None = "jarvis", token: str | None = MCP_TOKEN,
               raise_server_exceptions: bool = True):
    """A TestClient on the MCP listener (:8012) from socket peer 'peer'.

    Sends what the MCP server sends on every call; a header whose argument is
    None is omitted. raise_server_exceptions=False answers a route's exception
    with the 500 a real client would see.
    """
    from fastapi.testclient import TestClient

    headers = {
        "Authorization": None if token is None else f"Bearer {token}",
        "X-MCP-Acting-User": acting_user,
        "X-MCP-Client": client,
        "X-MCP-Correlation-Id": str(uuid.uuid4()),
    }
    return TestClient(app, base_url="http://api:8012", client=(peer, 40000),
                      raise_server_exceptions=raise_server_exceptions,
                      headers={k: v for k, v in headers.items() if v is not None})


def public_client(app):
    """A TestClient on the public listener (:8011)."""
    from fastapi.testclient import TestClient

    return TestClient(app, base_url="http://api:8011")


def make_probe_app():
    """A small app behind McpGateMiddleware whose routes depend on main.get_current_user,
    plus the real /mcp/approvals routes (api.mcp_routes).

    Returns (app, calls). Each route counts its calls in calls["count"][operation_id]
    and records the body it received (calls["body"]) and its principal
    (calls["principal"]). Later tasks add routes here.

    Money routes target the instance or brokerage in their path; the boom
    routes raise RuntimeError and the refuse routes HTTPException(400, "nope").
    """
    from fastapi import Body, Depends, FastAPI, HTTPException

    from api import main, mcp_routes
    from api.mcp_gate import McpGateMiddleware
    from mcp_policy import mcp_policy

    app = FastAPI()
    app.add_middleware(McpGateMiddleware)
    app.include_router(mcp_routes.build_router(main.get_current_user))
    calls: dict = {"count": {}, "body": None, "principal": None}

    def hit(operation_id: str, user: dict, body: object = None) -> None:
        calls["count"][operation_id] = calls["count"].get(operation_id, 0) + 1
        calls["principal"] = user
        calls["body"] = body

    @app.get("/whoami", operation_id="whoami", openapi_extra=mcp_policy("read"))
    def whoami(user: dict = Depends(main.get_current_user)):
        hit("whoami", user)
        return user

    @app.get("/probe/read", operation_id="probe_read", openapi_extra=mcp_policy("read"))
    def probe_read(user: dict = Depends(main.get_current_user)):
        hit("probe_read", user)
        return {"ok": True}

    @app.post("/probe/never", operation_id="probe_never", openapi_extra=mcp_policy("never"))
    def probe_never(body: dict = Body(default=None), user: dict = Depends(main.get_current_user)):
        hit("probe_never", user, body)
        return {"ok": True}

    @app.get("/probe/unannotated", operation_id="probe_unannotated")
    def probe_unannotated(user: dict = Depends(main.get_current_user)):
        hit("probe_unannotated", user)
        return {"ok": True}

    instance_target = {"instance": "instance_id"}

    @app.post("/probe/write", operation_id="probe_write", openapi_extra=mcp_policy("write"))
    def probe_write(body: dict = Body(default=None), user: dict = Depends(main.get_current_user)):
        hit("probe_write", user, body)
        return body

    @app.post("/probe/instances/{instance_id}/act", operation_id="probe_act",
              openapi_extra=mcp_policy("money", target=instance_target))
    def probe_act(instance_id: str, body: dict = Body(default=None),
                  user: dict = Depends(main.get_current_user)):
        hit("probe_act", user, body)
        return {"instance_id": instance_id, "body": body}

    @app.post("/probe/brokerages/{brokerage_id}/order", operation_id="probe_order",
              openapi_extra=mcp_policy("money", target={"brokerage": "brokerage_id"}))
    def probe_order(brokerage_id: str, body: dict = Body(default=None),
                    user: dict = Depends(main.get_current_user)):
        hit("probe_order", user, body)
        return {"brokerage_id": brokerage_id, "body": body}

    @app.post("/probe/cost", operation_id="probe_cost", openapi_extra=mcp_policy("write", cost=True))
    def probe_cost(body: dict = Body(default=None), user: dict = Depends(main.get_current_user)):
        hit("probe_cost", user, body)
        return {"ok": True}

    @app.delete("/probe/things/{thing_id}", operation_id="probe_delete",
                openapi_extra=mcp_policy("irreversible"))
    def probe_delete(thing_id: str, user: dict = Depends(main.get_current_user)):
        hit("probe_delete", user)
        return {"deleted": thing_id}

    @app.post("/probe/boom", operation_id="probe_boom", openapi_extra=mcp_policy("write"))
    def probe_boom(body: dict = Body(default=None), user: dict = Depends(main.get_current_user)):
        hit("probe_boom", user, body)
        raise RuntimeError("boom")

    @app.post("/probe/refuse", operation_id="probe_refuse", openapi_extra=mcp_policy("write"))
    def probe_refuse(body: dict = Body(default=None), user: dict = Depends(main.get_current_user)):
        hit("probe_refuse", user, body)
        raise HTTPException(status_code=400, detail="nope")

    @app.post("/probe/backtests", operation_id="create_backtest",
              openapi_extra=mcp_policy("write", cost=True))
    def probe_create_backtest(body: dict = Body(default=None),
                              user: dict = Depends(main.get_current_user)):
        hit("create_backtest", user, body)
        return {"ok": True}

    @app.post("/probe/instances/{instance_id}/boom", operation_id="probe_act_boom",
              openapi_extra=mcp_policy("money", target=instance_target))
    def probe_act_boom(instance_id: str, body: dict = Body(default=None),
                       user: dict = Depends(main.get_current_user)):
        hit("probe_act_boom", user, body)
        raise RuntimeError("boom")

    @app.post("/probe/instances/{instance_id}/refuse", operation_id="probe_act_refuse",
              openapi_extra=mcp_policy("money", target=instance_target))
    def probe_act_refuse(instance_id: str, body: dict = Body(default=None),
                         user: dict = Depends(main.get_current_user)):
        hit("probe_act_refuse", user, body)
        raise HTTPException(status_code=400, detail="nope")

    return app, calls
