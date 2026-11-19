"""McpAudit writer and secret redaction (plan Task 8, contract §6).

One McpAudit row per MCP call. The service token, Authorization headers and
secret-named request fields must never reach an audit row or a log line, so the
argument summary is redacted before it is cut, and a failed write logs only the
exception's type.
"""
from __future__ import annotations

import logging
from unittest.mock import Mock

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
    for name in ("symbol", "idempotency_key", "keys", "token_count", "monkey", "secretary"):
        assert not is_secret_key(name), name


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
            "verify_password_limited"} <= OUTCOMES and len(OUTCOMES) == 17
