"""The ollama SDK attaches OLLAMA_API_KEY to every client built without a key.

``allow_ambient_key=False`` lets a caller stop that, so the server's key never
goes to a host the caller chose. The default keeps today's behaviour: blank-key
Ollama Cloud rows rely on the ambient key.
"""
import asyncio

import httpx, ollama, pytest

_LLM_ENV_VARS = (
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT",
    "NVIDIA_BASE_URL", "OLLAMA_API_KEY", "OLLAMA_BASE_URL", "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL", "LLM_BASE_URL_ALLOWLIST",
)


@pytest.fixture(autouse=True)
def _clear_llm_env(monkeypatch):
    # api/main.py runs load_dotenv at import, so a developer's .env can leak in.
    for name in _LLM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def seen(monkeypatch):
    """Patch ollama.Client so every request goes to a MockTransport that records headers."""
    monkeypatch.setenv("OLLAMA_API_KEY", "env-secret")
    headers: list = []
    def handler(request):
        headers.append({k.lower(): v for k, v in request.headers.items()})
        return httpx.Response(200, json={"models": []})
    real = ollama.Client
    monkeypatch.setattr(ollama, "Client",
                        lambda *a, **kw: real(*a, transport=httpx.MockTransport(handler), **kw))
    return headers


def test_runtime_default_still_sends_the_ambient_key(seen):   # blank-key Ollama Cloud rows rely on it
    from llm_utils import _make_ollama_sync_client
    _make_ollama_sync_client("http://ollama.lan:11434", "", 5.0).list()
    assert seen[-1]["authorization"] == "Bearer env-secret"


def test_opt_out_sends_no_authorization(seen):
    from llm_utils import _make_ollama_sync_client
    _make_ollama_sync_client("http://ollama.lan:11434", "", 5.0, allow_ambient_key=False).list()
    assert "authorization" not in seen[-1]


def test_opt_out_keeps_an_explicit_key(seen):
    from llm_utils import _make_ollama_sync_client
    _make_ollama_sync_client("http://ollama.lan:11434", "mine", 5.0, allow_ambient_key=False).list()
    assert seen[-1]["authorization"] == "Bearer mine"


def test_no_ambient_authorization_strips_the_header():
    from ollama_client import NoAmbientAuthorization
    got: dict = {}
    def handler(r):
        got.update({k.lower(): v for k, v in r.headers.items()})
        return httpx.Response(204)
    httpx.Client(transport=httpx.MockTransport(handler), auth=NoAmbientAuthorization(),
                 headers={"Authorization": "Bearer x"}).get("http://h/")
    assert "authorization" not in got


def test_no_ambient_authorization_strips_the_header_on_an_async_client():
    from ollama_client import NoAmbientAuthorization
    got: dict = {}
    def handler(r):
        got.update({k.lower(): v for k, v in r.headers.items()})
        return httpx.Response(204)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                     auth=NoAmbientAuthorization(),
                                     headers={"Authorization": "Bearer x"}) as client:
            await client.get("http://h/")

    asyncio.run(go())
    assert got and "authorization" not in got


def test_dispatcher_forwards_the_flag(monkeypatch):
    import llm_utils
    monkeypatch.setenv("LLM_LOG_API_URL", "")   # keep _fire_llm_output_log off the network
    calls: list = []
    monkeypatch.setattr(llm_utils, "_call_ollama", lambda *a, **kw: calls.append(kw) or "ok")
    # distinct prompts so the prompt cache can never short-circuit the second call
    llm_utils.call_llm_by_provider("ollama", "", "m", "p-off", provider_config={"ollama_allow_ambient_key": False})
    llm_utils.call_llm_by_provider("ollama", "", "m", "p-on", provider_config={})
    assert [c["allow_ambient_key"] for c in calls] == [False, True]
