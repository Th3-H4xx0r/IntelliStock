"""Task 1: secret-safe persistence boundary (persistence_safety.py).

The recursive sanitizer must strip credential material from strategy-schema
snapshots before they reach BacktestResults, while the assertion guard fails
any write whose payload still carries secret material.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from persistence_safety import (  # noqa: E402
    REDACTION_MARKER,
    SecretMaterialError,
    assert_secret_free,
    sanitize_snapshot,
)


def test_sanitize_snapshot_removes_nested_secret_values():
    raw = {
        "name": "Nexus Only",
        "strategies": [{"config": {
            "alpaca_key": "CANARY_ALPACA_VALUE",
            "openrouter_api_key": "CANARY_OPENROUTER_VALUE",
            "strategy_config_hash": "abc123",
            "secret_ref": "env:OPENROUTER_API_KEY",
        }}],
    }
    clean = sanitize_snapshot(raw)
    encoded = json.dumps(clean, sort_keys=True)
    assert "CANARY_ALPACA_VALUE" not in encoded
    assert "CANARY_OPENROUTER_VALUE" not in encoded
    assert clean["strategies"][0]["config"]["strategy_config_hash"] == "abc123"
    assert clean["strategies"][0]["config"]["secret_ref"] == "env:OPENROUTER_API_KEY"


def test_assert_secret_free_rejects_secret_hidden_in_safe_field():
    with pytest.raises(SecretMaterialError):
        assert_secret_free({"notes": "Authorization: Bearer CANARY_TOKEN_MATERIAL_123456789"})


def test_sanitize_replaces_secret_keys_with_redaction_marker():
    clean = sanitize_snapshot({"alpaca_secret": "CANARY", "password": "CANARY2"})
    assert clean["alpaca_secret"] == {"redacted": True, "source": "runtime_secret"}
    assert clean["password"] == {"redacted": True, "source": "runtime_secret"}


def test_sanitize_is_case_insensitive_and_covers_provider_aliases():
    raw = {
        "ALPACA_KEY": "CANARY_A",
        "OpenRouter_Api_Key": "CANARY_B",
        "benzinga_api_key": "CANARY_C",
        "neo4j_password": "CANARY_D",
        "aws_secret_access_key": "CANARY_E",
        "azure_api_key": "CANARY_F",
        "authorization": "Bearer CANARY_G_0123456789",
    }
    encoded = json.dumps(sanitize_snapshot(raw), sort_keys=True)
    for canary in ("CANARY_A", "CANARY_B", "CANARY_C", "CANARY_D", "CANARY_E",
                   "CANARY_F", "CANARY_G"):
        assert canary not in encoded


def test_sanitize_scans_string_values_under_safe_keys():
    raw = {"notes": "conn is rethinkdb://svc_user:CANARY_DB_PASS@100.95.106.23:28015/app"}
    encoded = json.dumps(sanitize_snapshot(raw), sort_keys=True)
    assert "CANARY_DB_PASS" not in encoded


def test_sanitize_deep_copies_non_secret_structures():
    inner = {"max_positions": 8, "symbols": ["AAPL", "SPY"]}
    raw = {"strategies": [{"config": inner}]}
    clean = sanitize_snapshot(raw)
    clean["strategies"][0]["config"]["symbols"].append("MUTATED")
    assert inner["symbols"] == ["AAPL", "SPY"]


def test_sanitize_handles_tuples_and_scalars():
    clean = sanitize_snapshot({"pair": ({"api_token": "CANARY_T"}, 3)})
    assert "CANARY_T" not in repr(clean)
    assert clean["pair"][1] == 3
    assert sanitize_snapshot(7) == 7
    assert sanitize_snapshot(None) is None


def test_secret_ref_with_unapproved_scheme_is_redacted():
    # H14: an allowlisted name cannot smuggle a literal secret value.
    clean = sanitize_snapshot({"secret_ref": "sk-or-v1-CANARYLITERALSECRETVALUE12345"})
    assert "CANARYLITERALSECRETVALUE" not in json.dumps(clean)


def test_assert_secret_free_accepts_sanitized_payload():
    raw = {
        "name": "Nexus Only",
        "strategies": [{"config": {
            "alpaca_key": "CANARY_ALPACA_VALUE",
            "strategy_config_hash": "abc123",
            "secret_ref": "env:OPENROUTER_API_KEY",
        }}],
        "logs": ["backtest started", "progress 10%"],
    }
    assert_secret_free(sanitize_snapshot(raw))  # must not raise


def test_assert_secret_free_rejects_plaintext_under_secret_key():
    with pytest.raises(SecretMaterialError):
        assert_secret_free({"strategy_schema": {"strategies": [{"config": {
            "alpaca_secret": "CANARY_PLAINTEXT"}}]}})


def test_assert_secret_free_rejects_aws_and_openrouter_value_patterns():
    with pytest.raises(SecretMaterialError):
        assert_secret_free({"note": "creds AKIAIOSFODNN7CANARY99 in text"})
    with pytest.raises(SecretMaterialError):
        assert_secret_free({"note": "sk-or-v1-abcdef0123456789abcdef0123456789"})


def test_camel_case_secret_names_are_caught():
    """Audit: fused camelCase (clientSecret, alpacaKey) bypassed the
    segment split — exactly the shape frontend JSON produces."""
    for key in ("clientSecret", "alpacaKey", "openRouterApiKey", "dbPassword"):
        clean = sanitize_snapshot({key: "CANARY_CAMEL_VALUE"})
        assert "CANARY_CAMEL_VALUE" not in json.dumps(clean), key
        with pytest.raises(SecretMaterialError):
            assert_secret_free({key: "CANARY_CAMEL_VALUE"})


# ── ticker symbols that collide with credential key segments ───────────────
#
# bt 599773 completed its whole window, printed its summary, and then died at
# 97% on:
#     unredacted secret-bearing field at $.portfolio_value_history[381].prices.KEY
# `KEY` is KeyCorp. `assert_secret_free` runs over the entire BacktestResults
# row (broker.py:12755), which carries a symbol->price map, and `_is_secret_key`
# lowercases the ticker to the segment `key`. The separating rule is the VALUE,
# not the name: a credential here is always a string, never a bare number.

def test_a_ticker_named_KEY_does_not_fail_the_write():
    row = {"portfolio_value_history": [
        {"timestamp": "2026-01-02T15:00:00Z",
         "value": 6123.45,
         "prices": {"SPY": 681.82, "KEY": 18.42, "SNDK": 237.33}}]}
    assert_secret_free(row)   # must not raise


def test_a_ticker_named_KEY_keeps_its_price_through_sanitize():
    """Redacting it would be worse than the failed write: the equity curve
    would persist as {"redacted": True} and look like a value."""
    out = sanitize_snapshot({"prices": {"KEY": 18.42, "SPY": 681.82}})
    assert out["prices"]["KEY"] == 18.42
    assert out["prices"]["SPY"] == 681.82


def test_every_credential_segment_still_fails_closed_on_a_string():
    for name in ("key", "api_key", "secret", "token", "password", "passwd",
                 "pwd", "credential", "auth", "authorization", "bearer",
                 "apikey", "dsn", "userinfo", "passphrase", "alpacaKey",
                 "clientSecret"):
        with pytest.raises(SecretMaterialError):
            assert_secret_free({name: "hunter2-hunter2-hunter2"})


def test_a_string_secret_under_a_ticker_shaped_key_still_fails():
    """The relaxation is scoped to NUMBERS. A string under `KEY` is exactly the
    shape the guard exists for and must still be refused."""
    with pytest.raises(SecretMaterialError):
        assert_secret_free({"prices": {"KEY": "sk-abcdefghijklmnopqrstuvwx"}})
    with pytest.raises(SecretMaterialError):
        assert_secret_free({"KEY": "AKIAIOSFODNN7EXAMPLE"})


def test_a_bool_under_a_secret_key_is_not_treated_as_a_number():
    """`isinstance(True, int)` is True in Python; a flag must keep going through
    the marker path rather than silently persisting."""
    with pytest.raises(SecretMaterialError):
        assert_secret_free({"api_key": True})
    assert sanitize_snapshot({"api_key": True})["api_key"] == REDACTION_MARKER


def test_numbers_under_secret_keys_survive_a_round_trip():
    row = {"prices": {"KEY": 18.42}, "count": 3, "api_key": "sk-" + "a" * 24}
    cleaned = sanitize_snapshot(row)
    assert_secret_free(cleaned)          # must not raise
    assert cleaned["prices"]["KEY"] == 18.42
    assert cleaned["api_key"] == REDACTION_MARKER
