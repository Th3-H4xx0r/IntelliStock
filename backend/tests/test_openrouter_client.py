import openrouter_client


def test_list_models_normalizes(monkeypatch):
    payload = {"data": [
        {"id": "openai/gpt-4o-mini", "name": "GPT-4o mini", "context_length": 128000,
         "pricing": {"prompt": "0.00000015", "completion": "0.0000006"}},
        {"id": "anthropic/claude-3.5-sonnet", "name": "Claude 3.5 Sonnet",
         "context_length": 200000,
         "pricing": {"prompt": "0.000003", "completion": "0.000015",
                     "input_cache_read": "0.0000003", "input_cache_write": "0.00000375"}},
    ]}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return payload

    import requests
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: _Resp())
    rows = openrouter_client.list_models()
    # Sorted by id → anthropic first.
    assert rows[0]["id"] == "anthropic/claude-3.5-sonnet"
    assert rows[0]["pricing"]["prompt"] == "0.000003"
    assert rows[0]["context_length"] == 200000
    assert rows[1]["id"] == "openai/gpt-4o-mini"


def test_list_models_skips_malformed(monkeypatch):
    payload = {"data": [
        {"name": "no id here"},
        "not a dict",
        {"id": "x/y", "name": "Y"},
    ]}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    import requests
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: _Resp())
    rows = openrouter_client.list_models()
    assert [r["id"] for r in rows] == ["x/y"]
    assert rows[0]["pricing"] == {}


def test_list_models_error_returns_empty(monkeypatch):
    import requests

    def _boom(url, timeout=None):
        raise requests.RequestException("down")

    monkeypatch.setattr(requests, "get", _boom)
    assert openrouter_client.list_models() == []


def test_list_models_custom_base_url(monkeypatch):
    seen = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": []}

    def _get(url, timeout=None):
        seen["url"] = url
        return _Resp()

    import requests
    monkeypatch.setattr(requests, "get", _get)
    openrouter_client.list_models("https://proxy.example/api/v1/")
    assert seen["url"] == "https://proxy.example/api/v1/models"
