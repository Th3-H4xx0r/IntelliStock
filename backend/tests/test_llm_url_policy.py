import pytest
from llm_url_policy import (BaseUrlInvalid, BaseUrlNotAllowed, guard_key_for_base_url,
                            is_allowed_base_url, parse_base_url)

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


NOENV: dict = {}
EVIL = "https://evil.example/v1"
WITHHELD = ("This base URL is not on the allowlist for openai, so a saved or server API key "
            "will not be sent to it. Enter the API key to test this URL, or add its host to "
            "LLM_BASE_URL_ALLOWLIST.")

def test_parse_normalises_scheme_and_host():
    assert parse_base_url("https://API.OpenAI.com:443/v1/") == ("https", "api.openai.com")
    assert parse_base_url("http://[::1]:11434") == ("http", "::1")
    assert parse_base_url("192.168.1.50:11434") == ("http", "192.168.1.50")  # scheme-less reads as http, like the Ollama SDK

@pytest.mark.parametrize("url", ["", "   ", "ftp://h/", "file:///etc/passwd", "javascript:alert(1)",
    "https://user:pw@api.openai.com/v1", "https://:pw@api.openai.com", "http://h:99999/",
    "http://169.254.169.254/latest", "http://[fe80::1]:11434"])
def test_parse_rejects(url):
    with pytest.raises(BaseUrlInvalid) as e:
        parse_base_url(url)
    assert str(e.value) == "Invalid base URL: use http:// or https:// with a host and no credentials."

def test_official_hosts_need_https_and_the_exact_host():
    assert is_allowed_base_url("openai", "https://api.openai.com/v1", env=NOENV)
    assert is_allowed_base_url("openrouter", "https://openrouter.ai/api/v1", env=NOENV)
    assert is_allowed_base_url("ollama", "https://ollama.com", env=NOENV)
    assert is_allowed_base_url("openai", "https://API.OpenAI.com:443/v1/", env=NOENV)
    assert not is_allowed_base_url("openai", "http://api.openai.com/v1", env=NOENV)
    assert not is_allowed_base_url("openai", "https://api.openai.com.evil.example/v1", env=NOENV)
    assert not is_allowed_base_url("openai", EVIL, env=NOENV)
    assert not is_allowed_base_url("openai", "https://u:p@api.openai.com/v1", env=NOENV)  # invalid -> False, no raise

def test_azure_has_no_wildcard_official_host():
    assert not is_allowed_base_url("azure", "https://someone-else.openai.azure.com", env=NOENV)

def test_env_configured_base_url_host_is_allowed_for_its_provider_only():
    env = {"AZURE_OPENAI_ENDPOINT": "https://mine.openai.azure.com"}
    assert is_allowed_base_url("azure", "https://mine.openai.azure.com/openai", env=env)
    assert not is_allowed_base_url("openai", "https://mine.openai.azure.com/v1", env=env)

def test_allowlist_env_scoped_and_global_entries():
    env = {"LLM_BASE_URL_ALLOWLIST": " openai=Proxy.Corp , gateway.internal,"}
    assert is_allowed_base_url("openai", "http://proxy.corp:8080/v1", env=env)
    assert not is_allowed_base_url("openrouter", "https://proxy.corp/api/v1", env=env)
    assert is_allowed_base_url("openrouter", "https://gateway.internal/api/v1", env=env)

def test_env_defaults_to_os_environ(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL_ALLOWLIST", "openai=proxy.corp")
    assert is_allowed_base_url("openai", "https://proxy.corp/v1")

def test_ollama_builtin_default_host_is_allowed():
    assert is_allowed_base_url("ollama", "http://localhost:11434", env=NOENV)
    assert is_allowed_base_url("ollama", "http://127.0.0.1:11434", env=NOENV)

def test_saved_row_host_counts_only_for_that_row_and_its_provider():
    row = {"provider": "ollama", "ollama_base_url": "http://192.168.1.50:11434"}
    assert is_allowed_base_url("ollama", "http://192.168.1.50:11434", saved_row=row, env=NOENV)
    assert not is_allowed_base_url("ollama", "http://192.168.1.50:11434", env=NOENV)
    other = {"provider": "openai", "ollama_base_url": "http://192.168.1.50:11434"}
    assert not is_allowed_base_url("ollama", "http://192.168.1.50:11434", saved_row=other, env=NOENV)

def test_guard_caller_key_goes_anywhere():
    assert guard_key_for_base_url("openai", key="mine", source="caller", caller_base_url=EVIL,
                                  saved_row=None, env=NOENV) == "mine"

def test_guard_without_a_caller_url_keeps_todays_behaviour():
    assert guard_key_for_base_url("openai", key="srv", source="env", caller_base_url="",
                                  saved_row=None, env=NOENV) == "srv"

@pytest.mark.parametrize("source", ["saved", "env"])
def test_guard_refuses_saved_or_env_key_to_an_unlisted_url(source):
    with pytest.raises(BaseUrlNotAllowed) as e:
        guard_key_for_base_url("openai", key="k", source=source, caller_base_url=EVIL,
                               saved_row={"provider": "openai", "api_key": "k"}, env=NOENV)
    assert str(e.value) == WITHHELD and "evil.example" not in str(e.value)

def test_guard_saved_key_may_go_to_its_own_row_host():
    row = {"provider": "openai", "openai_base_url": "https://llm.lan/v1"}
    assert guard_key_for_base_url("openai", key="k", source="saved", caller_base_url="https://llm.lan/v1",
                                  saved_row=row, env=NOENV) == "k"

def test_guard_row_host_does_not_authorise_the_env_key():
    row = {"provider": "openai", "openai_base_url": "https://llm.lan/v1"}
    with pytest.raises(BaseUrlNotAllowed):
        guard_key_for_base_url("openai", key="srv", source="env", caller_base_url="https://llm.lan/v1",
                               saved_row=row, env=NOENV)

def test_guard_withholds_rather_than_refuses_for_ollama():
    assert guard_key_for_base_url("ollama", key="srv", source="env", caller_base_url="http://10.9.9.9:11434",
                                  saved_row=None, env=NOENV) == ""

def test_guard_empty_key_needs_no_check():
    assert guard_key_for_base_url("openai", key="", source="env", caller_base_url=EVIL,
                                  saved_row=None, env=NOENV) == ""
