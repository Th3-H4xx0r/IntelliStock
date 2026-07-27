"""Alpaca-equity credential boundaries.

These tests deliberately use canary values and assert that they never reach a
database document, Docker argv/environment, API-shaped strategy response, or
error representation.  No test opens RethinkDB, Docker, or a broker connection.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _known_strategy_meta(name: str = "graph_nexus_analysis") -> dict:
    return {
        "id": name,
        "name": name,
        "schema": {
            "strategy": name,
            "decision_phase": "pre",
            "execution_scope": "run_once",
        },
    }


def test_recursive_strategy_scrubber_rejects_material_without_echoing_it():
    from strategy_secret_boundary import InlineStrategySecretError, scrub_inline_strategy_secrets

    canary = "CANARY_STRATEGY_SECRET"
    with pytest.raises(InlineStrategySecretError) as exc:
        scrub_inline_strategy_secrets(
            {
                "regime_profiles": {
                    "bear": {
                        "company_article_llm_api_key": canary,
                    },
                },
            },
            reject_material=True,
        )

    assert canary not in str(exc.value)
    assert "company_article_llm_api_key" in str(exc.value)


def test_recursive_strategy_scrubber_drops_placeholders_and_preserves_safe_token_fields():
    from persistence_safety import REDACTION_MARKER
    from strategy_secret_boundary import scrub_inline_strategy_secrets

    result = scrub_inline_strategy_secrets(
        {
            "llm_api_key": "<provider_api_key>",
            "alpaca_secret": dict(REDACTION_MARKER),
            "max_output_tokens": 1024,
            "strategy_config_hash": "hash",
            "secret_ref": "env:OPENROUTER_API_KEY",
            "nested": [{"neo4j_password": ""}, {"tokenizer": "bert"}],
        },
        reject_material=True,
    )

    assert "llm_api_key" not in result
    assert "alpaca_secret" not in result
    assert result["max_output_tokens"] == 1024
    assert result["strategy_config_hash"] == "hash"
    assert result["secret_ref"] == "env:OPENROUTER_API_KEY"
    assert result["nested"] == [{}, {"tokenizer": "bert"}]


def test_strategy_normalization_rejects_inline_material_on_write(monkeypatch):
    import interactive_utils as iu
    from strategy_secret_boundary import InlineStrategySecretError

    monkeypatch.setattr(iu, "_resolve_strategy_meta", lambda _name: _known_strategy_meta())

    with pytest.raises(InlineStrategySecretError):
        iu._normalize_strategy_payload_item(
            {
                "strategy": "graph_nexus_analysis",
                "weight": 1,
                "config": {"benzinga_api_key": "CANARY_BENZINGA"},
            },
            strict=True,
        )


def test_strategy_normalization_removes_legacy_inline_material_on_read(monkeypatch):
    import interactive_utils as iu

    monkeypatch.setattr(iu, "_resolve_strategy_meta", lambda _name: _known_strategy_meta())
    normalized = iu._normalize_strategy_payload_item(
        {
            "strategy": "graph_nexus_analysis",
            "weight": 1,
            "config": {
                "llm_api_key": "CANARY_LLM",
                "neo4j_password": "CANARY_NEO4J",
                "max_positions": 8,
            },
        },
        strict=False,
    )

    assert normalized["config"] == {"max_positions": 8}
    assert "CANARY" not in repr(normalized)


def test_stock_instance_creation_rejects_direct_credentials_before_write(monkeypatch):
    import interactive_utils as iu

    monkeypatch.setattr(iu, "ensure_instances_table", lambda _conn: None)
    insert = MagicMock(side_effect=AssertionError("stock instance must not be written"))
    monkeypatch.setattr(iu, "r", SimpleNamespace(db=lambda _name: insert))

    with pytest.raises(ValueError, match="linked Alpaca brokerage"):
        iu.action_create_instance(
            object(),
            "stock-instance",
            key="CANARY_KEY",
            secret="CANARY_SECRET",
        )


def test_stock_instance_creation_does_not_copy_environment_credentials(monkeypatch):
    import interactive_utils as iu

    monkeypatch.setattr(iu, "ensure_instances_table", lambda _conn: None)
    monkeypatch.setenv("APCA_API_KEY_ID", "CANARY_ENV_KEY")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "CANARY_ENV_SECRET")

    captured = {}

    class _Insert:
        def run(self, _conn):
            return {"inserted": 1}

    class _Table:
        def insert(self, doc, conflict=None):
            captured.update(doc)
            assert conflict == "replace"
            return _Insert()

    class _Db:
        def table(self, name):
            assert name == "Instances"
            return _Table()

    monkeypatch.setattr(iu, "r", SimpleNamespace(db=lambda _name: _Db()))

    iu.action_create_instance(object(), "stock-instance")

    assert "key" not in captured
    assert "secret" not in captured
    assert "CANARY" not in repr(captured)


def test_stock_backtest_creation_rejects_direct_credentials(monkeypatch):
    import interactive_utils as iu

    monkeypatch.setattr(iu, "ensure_backtest_instances_table", lambda _conn: None)
    monkeypatch.setattr(iu, "_resolve_instance_doc", lambda _conn, _iid: {"id": "stock", "kind": None})
    monkeypatch.setattr(
        iu,
        "insert_backtest_with_unique_id",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not enqueue")),
    )

    with pytest.raises(ValueError, match="linked Alpaca brokerage"):
        iu.action_create_backtest(
            object(),
            "stock",
            ["SPY"],
            "2026-01-01",
            "2026-02-01",
            key="CANARY_KEY",
            secret="CANARY_SECRET",
        )


def test_stock_backtest_queue_row_contains_no_credentials(monkeypatch):
    import interactive_utils as iu

    monkeypatch.setattr(iu, "ensure_backtest_instances_table", lambda _conn: None)
    monkeypatch.setattr(
        iu,
        "_resolve_instance_doc",
        lambda _conn, _iid: {
            "id": "stock",
            "kind": None,
            "brokerage_id": "linked-alpaca",
        },
    )
    monkeypatch.setenv("KEY", "CANARY_ENV_KEY")
    monkeypatch.setenv("SECRET", "CANARY_ENV_SECRET")
    monkeypatch.setenv("APCA_API_KEY_ID", "CANARY_APCA_KEY")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "CANARY_APCA_SECRET")
    captured = {}

    def _insert(_conn, doc):
        captured.update(doc)
        return 123456

    monkeypatch.setattr(iu, "insert_backtest_with_unique_id", _insert)
    monkeypatch.setattr(iu, "action_enqueue_discord_message", lambda *_a, **_k: None)
    monkeypatch.setattr(iu, "get_instance_avg_difficulty", lambda *_a, **_k: 1.0)
    monkeypatch.setattr(iu, "get_instance_high_usage", lambda *_a, **_k: False)

    result = iu.action_create_backtest(
        object(),
        "stock",
        ["SPY"],
        "2026-01-01",
        "2026-02-01",
    )

    assert result["id"] == 123456
    assert "key" not in captured
    assert "secret" not in captured
    assert "CANARY" not in repr(captured)


def test_stock_backtest_container_receives_no_alpaca_secret_in_argv_or_env(monkeypatch):
    """The engine passes NULL placeholders; broker.py resolves the exact DB link."""
    original_cwd = os.getcwd()
    try:
        from engines import backtest_engine as engine
    finally:
        os.chdir(original_cwd)

    monkeypatch.setenv("KEY", "CANARY_ENGINE_KEY")
    monkeypatch.setenv("SECRET", "CANARY_ENGINE_SECRET")
    monkeypatch.setenv("APCA_API_KEY_ID", "CANARY_APCA_KEY")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "CANARY_APCA_SECRET")
    monkeypatch.setenv("INTELLISTOCK_CRED_KEY", "CANARY_FERNET_KEY")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(engine, "_remove_row_and_mark_done", lambda _row_id: None)
    monkeypatch.setattr(engine, "_get_network", lambda _client: None)
    monkeypatch.setattr(
        engine,
        "_get_instance_doc",
        lambda _conn, _iid: {"id": "stock", "kind": None, "brokerage_id": "linked-alpaca"},
    )
    monkeypatch.setattr(engine, "get_conn", lambda: SimpleNamespace(close=lambda: None))

    captured = {}

    class _Images:
        def get(self, _image):
            return object()

    class _Containers:
        def get(self, _name):
            raise LookupError("absent")

        def run(self, image, **kwargs):
            captured["image"] = image
            captured.update(kwargs)
            return object()

    fake_client = SimpleNamespace(images=_Images(), containers=_Containers())
    monkeypatch.setattr(engine, "_get_docker_client", lambda: fake_client)

    engine.run_one_backtest(
        {
            "id": 123456,
            "instance": "stock",
            "stocks": ["SPY"],
            "start-date": "2026-01-01",
            "end-date": "2026-02-01",
            "granularity_sec": 3600,
            "initial_cash": 100000,
        }
    )

    command = captured["command"]
    environment = captured["environment"]
    assert command[7:9] == ["NULL", "NULL"]
    assert "KEY" not in environment
    assert "SECRET" not in environment
    encoded = repr((command, environment))
    for canary in (
        "CANARY_ENGINE_KEY",
        "CANARY_ENGINE_SECRET",
        "CANARY_APCA_KEY",
        "CANARY_APCA_SECRET",
    ):
        assert canary not in encoded


def test_forced_model_delete_cannot_restore_inline_credentials(monkeypatch):
    import interactive_utils as iu

    referenced = [{"id": 7, "name": "Equity Strategy"}]
    monkeypatch.setattr(iu, "_ensure_models_table", lambda _conn: None)
    monkeypatch.setattr(iu, "_find_strategies_referencing_model", lambda *_a: referenced)

    delete = MagicMock(side_effect=AssertionError("referenced model must not be deleted"))

    class _Get:
        def run(self, _conn):
            return {"id": "model-1", "provider": "openrouter", "api_key": "fernet:ciphertext"}

        def delete(self):
            return delete()

    class _Table:
        def get(self, _model_id):
            return _Get()

    class _Db:
        def table(self, _name):
            return _Table()

    monkeypatch.setattr(iu, "r", SimpleNamespace(db=lambda _name: _Db()))

    with pytest.raises(ValueError, match="reassign"):
        iu.action_delete_model(object(), "model-1", force=True)

    delete.assert_not_called()


def test_broker_stock_credential_boundary_is_wired_to_strict_decryption():
    broker_source = (
        Path(__file__).resolve().parents[1] / "broker.py"
    ).read_text(encoding="utf-8")

    assert "resolve_linked_alpaca_credentials" in broker_source
    assert "if is_equity_stock_instance(inst_doc):" in broker_source


def test_stock_credential_boundary_rejects_plaintext_and_link_mismatch():
    from stock_credential_boundary import (
        StockCredentialError,
        resolve_linked_alpaca_credentials,
    )

    instance = {"brokerage_id": "linked", "kind": None}
    with pytest.raises(StockCredentialError, match="strict decryption"):
        resolve_linked_alpaca_credentials(
            instance,
            {
                "id": "linked",
                "brokerage_type": "alpaca",
                "alpaca_paper": True,
                "alpaca_key": "plaintext",
                "alpaca_secret": "plaintext",
            },
            data=False,
        )

    with pytest.raises(StockCredentialError, match="exact instance link"):
        resolve_linked_alpaca_credentials(
            instance,
            {
                "id": "different",
                "brokerage_type": "alpaca",
                "alpaca_paper": True,
                "alpaca_key": "fernet:not-used",
                "alpaca_secret": "fernet:not-used",
            },
            data=False,
        )
