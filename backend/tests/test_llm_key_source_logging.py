"""Credential-source diagnostics must never disclose credential fragments."""

from __future__ import annotations

import os
import sys


_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import strategies.graph_nexus_analysis as g  # noqa: E402


def test_llm_key_source_log_reports_presence_without_key_fragments(monkeypatch):
    secret = "sk-live-sensitive-tail"
    messages: list[str] = []
    monkeypatch.setattr(g, "_log", lambda message, _color="white": messages.append(message))
    g._LLM_KEY_SOURCE_LOG_SEEN.clear()

    provider, resolved, _model, _prompt_version = g._resolve_role_llm_config(
        {
            "company_article_llm_provider": "openai",
            "company_article_llm_api_key": secret,
            "company_article_llm_model": "gpt-test",
        },
        "company_article",
    )

    assert provider == "openai"
    assert resolved == secret
    assert len(messages) == 1
    diagnostic = messages[0]
    assert "config.company_article_llm_api_key" in diagnostic
    assert "present=True" in diagnostic
    assert f"len={len(secret)}" in diagnostic
    assert secret not in diagnostic
    assert secret[:4] not in diagnostic
    assert secret[-4:] not in diagnostic
