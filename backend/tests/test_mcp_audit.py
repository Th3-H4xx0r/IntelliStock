"""McpAudit writer and secret redaction (plan Task 8, contract §6).

One McpAudit row per MCP call. The service token, Authorization headers and
secret-named request fields must never reach an audit row or a log line, so the
argument summary is redacted before it is cut, and a failed write logs only the
exception's type.
"""
from __future__ import annotations

import logging
import re
from unittest.mock import Mock

import pytest

import mcp_audit
from mcp_audit import (
    OUTCOMES, is_secret_key, redact_secrets, summarize_args, write_audit,
)
from mcp_helpers import use_store


def test_redact_secrets_is_deep_and_case_insensitive():
    got = redact_secrets({"Key": "AK1", "nested": [{"odds_api_key": "x", "symbol": "TQQQ"}], "my_token": "t"})
    assert got == {"Key": "[redacted]", "nested": [{"odds_api_key": "[redacted]", "symbol": "TQQQ"}], "my_token": "[redacted]"}


def test_redact_secrets_leaves_the_input_untouched():
    body = {"password": "hunter2", "nested": {"secret": "s", "keep": [1, 2]}}
    got = redact_secrets(body)
    assert body == {"password": "hunter2", "nested": {"secret": "s", "keep": [1, 2]}}
    assert got["nested"]["keep"] == [1, 2] and got["nested"]["keep"] is not body["nested"]["keep"]


def test_redact_secrets_replaces_a_whole_subtree_under_a_secret_key():
    assert redact_secrets({"oauth_token": {"access": "a", "refresh": "r"}}) == {"oauth_token": "[redacted]"}


def test_is_secret_key_exact_names_and_suffixes():
    for name in ("api_key", "API_KEY", "Password", "dsn", "passphrase", "alpaca_secret",
                 "discord_bot_token", "openrouter_api_key"):
        assert is_secret_key(name), name
    for name in ("symbol", "idempotency_key", "keys", "token_count", "monkey", "secretary",
                 "idempotencyKey", "Idempotency-Key", "maxTokens", "public_key"):
        assert not is_secret_key(name), name


@pytest.mark.parametrize("name", [
    "kalshi_private_key", "kalshiPrivateKey", "KalshiPrivateKey", "kalshi-private-key",
    "KALSHI_PRIVATE_KEY", "private_key", "privateKey", "PrivateKey", "privatekey",
    "apiKey", "APIKey", "X-Api-Key", "x-api-key", "accessToken", "clientSecret",
    "secret_auth_key", "secretAuthKey",
])
def test_camel_case_and_hyphen_variants_are_secret(name):
    assert is_secret_key(name)
    assert redact_secrets({name: "s3cr3t"}) == {name: "[redacted]"}


FAKE_PEM = ("-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEAfakefakefakefakefakefakefakefakefakefakefake\n"
            "-----END RSA PRIVATE KEY-----\n")


@pytest.mark.parametrize("body,secrets", [
    ({"brokerage_type": "kalshi", "account_name": "Kalshi", "kalshi_key_id": "kid-1",
      "kalshi_private_key": FAKE_PEM, "kalshi_environment": "live"},
     ("PRIVATE KEY", "MIIEpAIBAAKCAQEAfake")),
    ({"brokerage_type": "alpaca", "account_name": "Alpaca", "key": "AKFAKEKEYID",
      "secret": "SKFAKESECRET", "paper": False}, ("AKFAKEKEYID", "SKFAKESECRET")),
])
def test_a_link_brokerage_body_keeps_its_credentials_out_of_the_summary(body, secrets):
    # POST /brokerages (link_brokerage, irreversible): its args_summary lives 365 days.
    s = summarize_args({}, body)
    assert "[redacted]" in s and not [x for x in secrets if x in s]
    assert redact_secrets(body)["account_name"] == body["account_name"]


# Request fields the app really takes that look secret but are not: an identifier
# sent beside the secret it names, and an approval device's public key.
NOT_SECRET = frozenset({"kalshi_key_id", "public_key"})
_SECRETISH = re.compile(r"key|secret|token|passw|passphrase|private|credential|dsn|pem", re.I)


def _request_field_names(spec: dict) -> set[str]:
    """Every query/path parameter and request-body property name, nested models included."""
    schemas = spec.get("components", {}).get("schemas", {})
    names: set[str] = set()
    seen: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.rsplit("/", 1)[-1] not in seen:
                seen.add(ref.rsplit("/", 1)[-1])
                walk(schemas.get(ref.rsplit("/", 1)[-1], {}))
            names.update((node.get("properties") or {}).keys())
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    for ops in spec["paths"].values():
        for op in ops.values():
            walk(op.get("requestBody") or {})
            names.update(p["name"] for p in op.get("parameters") or [])
    return names


def test_every_secret_looking_request_field_of_the_app_is_redacted():
    """Ties the list to the real API: a new request field that looks secret fails
    here until is_secret_key catches it or NOT_SECRET names it."""
    from api import main

    names = _request_field_names(main.app.openapi())
    assert {"kalshi_private_key", "secret", "password"} <= names       # the walk reaches bodies
    missed = sorted(n for n in names
                    if _SECRETISH.search(n) and n not in NOT_SECRET and not is_secret_key(n))
    assert missed == []


def test_summarize_args_redacts_and_truncates():
    # Canonical JSON sorts keys: the filler must sort after "password", or the
    # cut at 500 would drop the "[redacted]" marker along with the filler.
    s = summarize_args({"q": "1"}, {"password": "hunter2", "text": "x" * 900})
    assert "hunter2" not in s and "[redacted]" in s and len(s) == 501 and s.endswith("…")


def test_summarize_args_redacts_query_secrets_and_keeps_short_summaries_whole():
    s = summarize_args({"api_key": "AKSECRET", "symbol": "TQQQ"}, None)
    assert s == '{"body":null,"query":{"api_key":"[redacted]","symbol":"TQQQ"}}'


def test_write_audit_fills_id_and_ts(store, monkeypatch):
    use_store(monkeypatch, store)
    write_audit({"outcome": "ok", "status": 200, "operation_id": "list_instances"})
    (row,) = store.run("McpAudit")
    assert len(row["id"]) == 32 and row["ts"].endswith("Z") and row["operation_id"] == "list_instances"


def test_write_audit_keeps_a_given_id_and_ts(store, monkeypatch):
    use_store(monkeypatch, store)
    write_audit({"id": "a" * 32, "ts": "2026-11-19T00:00:00.000000Z", "outcome": "capped"})
    (row,) = store.run("McpAudit")
    assert (row["id"], row["ts"]) == ("a" * 32, "2026-11-19T00:00:00.000000Z")


def test_write_audit_drops_authorization_in_any_case(store, monkeypatch):
    use_store(monkeypatch, store)
    row = {"outcome": "unauthenticated", "authorization": "Bearer one", "Authorization": "Bearer two"}
    write_audit(row)
    (stored,) = store.run("McpAudit")
    assert not {k.lower() for k in stored} & {"authorization"}
    assert "Bearer" not in repr(stored)
    assert row == {"outcome": "unauthenticated", "authorization": "Bearer one", "Authorization": "Bearer two"}


def test_write_audit_refuses_an_unknown_outcome(store, monkeypatch, caplog):
    use_store(monkeypatch, store)
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        write_audit({"outcome": "allowed"})
    assert store.run("McpAudit") == []
    assert "mcp audit write failed" in caplog.text


def test_write_audit_never_raises(monkeypatch):
    monkeypatch.setattr(mcp_audit.store, "insert", Mock(side_effect=RuntimeError("down")))
    write_audit({"outcome": "ok"})          # no exception


def test_write_audit_failure_logs_the_type_only(monkeypatch, caplog):
    monkeypatch.setattr(mcp_audit.store, "insert", Mock(side_effect=RuntimeError("tok=" + "t" * 40)))
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        write_audit({"outcome": "ok"})
    assert "mcp audit write failed: RuntimeError" in caplog.text
    assert "t" * 40 not in caplog.text


def test_write_audit_logs_a_row_the_store_rejected(monkeypatch, caplog):
    rejected = Mock(errors=1, first_error="Key (id)=(secret-ish) already exists")
    monkeypatch.setattr(mcp_audit.store, "insert", Mock(return_value=rejected))
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        write_audit({"outcome": "ok"})
    assert "mcp audit write failed" in caplog.text and "secret-ish" not in caplog.text


def test_outcomes_cover_the_gate_vocabulary():
    assert {"ok", "approval_required", "executed_approved", "auth_event",
            "verify_password_limited", "rejected"} <= OUTCOMES and len(OUTCOMES) == 18
