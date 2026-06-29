import model_resolver


class _FakeConn:
    pass


def test_openrouter_fields_injected(monkeypatch):
    doc = {
        "id": "m1", "provider": "openrouter", "model": "anthropic/claude-3.5-sonnet",
        "api_key": "sk-or-x", "openrouter_base_url": "https://openrouter.ai/api/v1",
        "openrouter_referer": "https://intellistock.app", "openrouter_title": "IntelliStock",
    }
    monkeypatch.setattr(model_resolver, "_get_model_from_cache_or_db", lambda c, mid: doc)
    out = model_resolver.resolve_model_refs_in_config(_FakeConn(), {"llm_model_id": "m1"})
    assert out["llm_provider"] == "openrouter"
    assert out["llm_model"] == "anthropic/claude-3.5-sonnet"
    assert out["llm_api_key"] == "sk-or-x"
    assert out["openrouter_base_url"] == "https://openrouter.ai/api/v1"
    assert out["openrouter_referer"] == "https://intellistock.app"
    assert out["openrouter_title"] == "IntelliStock"


def test_openrouter_optional_fields_absent(monkeypatch):
    doc = {
        "id": "m2", "provider": "openrouter", "model": "openai/gpt-4o-mini",
        "api_key": "sk-or-y",
    }
    monkeypatch.setattr(model_resolver, "_get_model_from_cache_or_db", lambda c, mid: doc)
    out = model_resolver.resolve_model_refs_in_config(_FakeConn(), {"llm_model_id": "m2"})
    assert out["llm_provider"] == "openrouter"
    # Optional header fields not present on the row → not injected.
    assert "openrouter_referer" not in out
    assert "openrouter_title" not in out
