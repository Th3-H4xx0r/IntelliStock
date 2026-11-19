# MCP prerequisites: API hardening (Phase 0 items 7.1, 7.3, 7.4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close three live holes in the public API that the MCP layer would inherit: saved or server LLM keys sent to caller-chosen URLs, a login limiter keyed on Traefik's address, and chatbot-internal routes reachable through Traefik.

**Architecture:** A pure policy module (`backend/llm_url_policy.py`) decides which hosts a saved or server key may travel to. `/llm/test` and the three list-models routes consult it and return fixed error text for hosts outside the allowlist. Uvicorn startup moves into `backend/api/server_config.py`, which trusts `X-Forwarded-For` only from `FORWARDED_ALLOW_IPS`. The in-process limiter gains a per-IP bucket and a signup bucket. `_require_loopback` also refuses any request that carries a forwarding header, and it now guards the two `/chatbot/internal/*` routes.

**Tech Stack:** Python 3.11, FastAPI/Starlette, uvicorn (`ProxyHeadersMiddleware`), httpx, ollama SDK (`>=0.4,<1`), pytest with FakeStore.

**Spec:** `docs/superpowers/specs/2026-11-19-intellistock-mcp-server-design.md` (§7 items 1, 3, 4), bound by `docs/superpowers/specs/2026-11-19-intellistock-mcp-contract.md` (§2 listeners, §4 verify-password limiter key).

**Branch:** `fix/api-hardening-mcp-prereqs`, cut from `origin/main` at or after `4420fea6` (wheel-regrade PR #188 merged; Task 1, Step 0). Worktree: `/Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock/.worktrees/mcp-api-gate` (written `<WT>` below).

**Scope:** in: §7.1, §7.3, §7.4. Out: §7.2 (chatbot lockdown, its own PR, owns `backend/chatbot/claude_cli_provider.py`), §7.5 (approval compare-and-swap), and the §7 "noted, not in scope" items. This PR adds no routes, no `x-mcp` annotations (build C classifies routes, including #188's regrade and wheel-scan routes) and no tables, so the schema table-count test stays at 159. It leaves #188's three swing/wheel routes untouched.

## Decisions made without the operator (review these)

1. **Unlisted caller base URL: caller key only.** When the caller supplies a base URL that is not allowlisted, only a key typed into the request may be sent there. A test that would need the saved or server key gets a 400 naming `LLM_BASE_URL_ALLOWLIST`. Ollama is the exception: it runs keyless instead, because local Ollama needs no key. Rejecting every unlisted URL would be stricter, but it would break "test my new self-hosted endpoint" until a redeploy.
2. **"Hosts stored on saved Models rows" is narrowed.** A row's stored host authorises only that row's own saved key. It does not authorise the server env key or another row's key. Any signed-in caller can create a row in one request, so "some row has this host" is no evidence that the operator trusts it.
3. **Azure has no wildcard official host.** Every `*.openai.azure.com` name is somebody's own resource. Azure endpoints are allowed only from env, the row's own endpoint, or the allowlist.
4. **Found a live leak: the installed ollama-python (0.6.2; requirements allow anything below 1) attaches `OLLAMA_API_KEY` from the environment to every request that has no Authorization header.** Today `/ollama/list-models` therefore sends the server's Ollama key to any URL a caller names. `/llm/test` sends it too, explicitly, through `resolve_api_key_for_provider`. After this PR, discovery sends only explicit keys, and `/llm/test` opts out of the ambient key. The runtime strategy path keeps today's behaviour (default `allow_ambient_key=True`), because blank-key Ollama Cloud rows may rely on it.
5. **`/llm/test` hides upstream error text only for non-allowlisted URLs**, which is where the port-scan channel is. Official, env-configured and own-row hosts still show "invalid key", "model not found" and similar errors.
6. **List-models routes stay open to any http(s) host** so LAN Ollama discovery keeps working. After this PR they carry no saved or server key to unlisted hosts, return fixed error strings, and refuse credential-bearing URLs and link-local literals (cloud metadata). A timing side channel remains, reachable only by signed-in callers.
7. **Limits:** 30 failed logins per IP per 15 min, on top of the existing 10 per (username, IP). A success does not clear the per-IP bucket. Signup allows 5 attempts per IP per hour, counting every attempt. **Lead ruling:** the per-IP limit must never become a global lockout. While `FORWARDED_ALLOW_IPS` names no proxy (unset or loopback only), it is skipped for non-loopback peers, because that peer is Traefik's shared address. The per-(username, IP) and signup limits still apply, and startup logs a warning. Once the variable names the proxy, the per-IP limit keys on the real client.
8. **`FORWARDED_ALLOW_IPS` is parsed defensively.** `*`, `/0` networks and hostnames are dropped, so the API never trusts everyone. A CIDR with host bits set is normalised (`10.0.1.5/24` becomes `10.0.1.0/24`). If nothing valid is left, the default is `127.0.0.1`.
9. **`_require_loopback` refuses `X-Forwarded-For`, `Forwarded` and `X-Real-IP`** whatever the peer address.
10. **Residual risk, not fixed here:** `PUT /models/{id}` keeps the saved key when only the base URL changes (`action_edit_model` skips masked keys). A signed-in caller can still repoint a saved key at runtime. Changing that would alter the human edit flow, which the spec does not authorise. This is the next follow-up.

## Global Constraints

- Backend code runs on Python 3.11 (CI): no PEP 695 `type`/generic syntax, no nested same-quote f-strings, no `typing.override`, no 3.12+ stdlib.
- Test command, from the repo root: `python -m pytest -q -p no:cacheprovider backend/tests`. Tests never touch the network, production, brokers or LLMs. Use FakeStore (the default when `PG_TEST_DSN` is unset) and mock HTTP with `httpx.MockTransport`.
- `api/main.py` runs `load_dotenv` at import, so a developer's `.env` leaks into tests. Every test file that depends on LLM env vars deletes them in an autouse fixture: `OPENAI_API_KEY OPENAI_BASE_URL AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT NVIDIA_BASE_URL OLLAMA_API_KEY OLLAMA_BASE_URL OPENROUTER_API_KEY OPENROUTER_BASE_URL LLM_BASE_URL_ALLOWLIST`.
- Behaviour for human (JWT) callers changes only as listed under "Behaviour changes" below. Real money trades through this API (instance `alpaca-main`), and merging redeploys it, so the operator merges outside market hours.
- GitNexus index: if any GitNexus tool warns the index is stale (likely after the move to 4420fea6), run `GITNEXUS_MAX_FILE_SIZE=2048 npx gitnexus analyze` in `<WT>` first. The env var is required; without it the indexer silently skips files over 512 KB.
- CLAUDE.md: before editing any existing function, run `mcp__gitnexus__impact({target, direction: "upstream", repo: "IntelliStock"})` and put the blast radius (direct callers, processes, risk) in the task report. GitNexus returns UNKNOWN for FastAPI-wired code, so re-run the grep given in the task and report what it finds. If the result is HIGH or CRITICAL, write the warning into the task report for the controller to surface, and do not widen the change. Before every commit, run `mcp__gitnexus__detect_changes({scope: "staged", worktree: "<WT>", repo: "IntelliStock"})` and confirm only the task's symbols changed.
- Datastore: no tables, no DDL, no DB access outside `backend/db`. The limiter stays in-process.
- No new dependencies.
- Do not edit `backend/chatbot/claude_cli_provider.py`; the §7.2 PR owns it.
- Refer to existing code by function name. `main.py` line numbers shift when the wheel-regrade PR merges.
- Stage only the files the task lists, never the untracked `docs/superpowers/` spec or plan files.
- Commit messages are conventional (`fix(api): …`) and end with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Env knobs and defaults (exact): `FORWARDED_ALLOW_IPS=127.0.0.1`; `LLM_BASE_URL_ALLOWLIST=` (empty); `LOGIN_RATE_LIMIT_MAX_ATTEMPTS=10` and `LOGIN_RATE_LIMIT_WINDOW_SECONDS=900` (existing); `LOGIN_RATE_LIMIT_PER_IP_MAX_ATTEMPTS=30`; `SIGNUP_RATE_LIMIT_MAX_ATTEMPTS=5`; `SIGNUP_RATE_LIMIT_WINDOW_SECONDS=3600`.

### Behaviour changes for human callers (complete list)

1. `POST /llm/test` refuses (400) to pair a saved or server key with a non-allowlisted base URL, and hides upstream error text for such URLs.
2. `POST /ollama|bedrock|openrouter/list-models` return fixed error strings, with no URL and no exception text. Malformed, credential-bearing and link-local base URLs get a 400. `/ollama/list-models` sends the server's `OLLAMA_API_KEY` only to allowlisted hosts.
3. `POST /auth/login` returns 429 after 30 failures from one client IP in 15 min, but only once `FORWARDED_ALLOW_IPS` names the proxy (or for a loopback peer). This is on top of the existing 10 per (username, IP), which applies in every mode.
4. `POST /auth/signup` allows 5 attempts per client IP per hour.
5. `POST /chatbot/internal/mcp-tools-list` and `/mcp-tool-call` return 403 unless the request comes from loopback with no forwarding header.
6. Once `FORWARDED_ALLOW_IPS` names the proxy, `request.client.host` is the real client (limiter key, logs), and `request.url.scheme` follows `X-Forwarded-Proto`.

## Review Focus

1. **Saved-model edit flows where the client pre-fills a default base URL the row never stored.** iOS and web send `https://openrouter.ai/api/v1` and `http://localhost:11434` for rows saved with an empty base URL, and the saved key must still be used. Pinned in Task 3 (`test_edit_flow_with_client_default_base_url_still_uses_saved_key`).
2. **Base URL spellings.** An upper-case host, an explicit `:443`, a trailing slash or path, an IPv6 literal and a scheme-less `host:port` still match official, env and row hosts. Credentials-in-URL and link-local literals are refused. Pinned in Task 1.
3. **`FORWARDED_ALLOW_IPS` typed loosely:** spaces, a trailing comma, a hostname, a CIDR with host bits, `*`, `0.0.0.0/0`. These parse to the valid subset and never to trust-all. Pinned in Task 5.
4. **A multi-hop `X-Forwarded-For` (`client, edge`) from the trusted proxy, and `X-Forwarded-For` sent straight to the host-published :8011.** The limiter keys on the real client or the real peer, never on a spoofed value. Pinned in Task 5.
5. **Port already bound at startup.** The process exits non-zero, as `uvicorn.run` did, so Docker's restart policy sees the failure. Pinned in Task 5.

## Coordination

- **§7.2 chatbot-lockdown PR (owns `backend/chatbot/claude_cli_provider.py`).** The stdio MCP server reaches `/chatbot/internal/*` at `INTELLISTOCK_MCP_URL = _mcp_callback_url()`. At 26cd7546 that defaults to `http://127.0.0.1:${API_PORT:-8011}`, which is loopback, so Task 7 keeps working without touching that file. The override `CLAUDE_CLI_MCP_CALLBACK_URL` must stay unset or loopback in production (deploy check below). Task 7 pins the default with a test: if §7.2 renames or changes `_mcp_callback_url`, update that pin test, not the provider. Note that `main.py` defaults `API_PORT` to 8000 while the callback defaults to 8011. Production sets `API_PORT=8011`, so they agree, but §7.2 should derive both from one place.
- **MCP gate PR (build C).** It reuses `build_uvicorn_config(app, host=..., port=MCP_API_PORT, proxy_headers=False)` for :8012, and the limiter key `(username, "mcp")` for `/internal/mcp/verify-password` (contract §4). With Task 6, all `"mcp"` failures also share one per-IP bucket (direct calls keep the default `per_ip=True`), which acts as a global cap of 30 per 15 min.

## File map

| File | Change |
|---|---|
| `backend/llm_url_policy.py` | **Create.** Pure host-allowlist policy for LLM base URLs. |
| `backend/api/server_config.py` | **Create.** Trusted-proxy parsing, `trusted_proxy_configured`, `uvicorn.Config` builder, runner. |
| `backend/ollama_client.py` | `NoAmbientAuthorization`; `list_models` and `show_model` send explicit keys only. |
| `backend/llm_utils.py` | `_make_ollama_sync_client`, `_call_ollama`, and the Ollama branch of `call_llm_by_provider` gain an opt-out of the ambient key. |
| `backend/auth_utils.py` | Per-IP login bucket (applied only behind a configured proxy) and signup bucket. |
| `backend/api/main.py` | `api_test_llm_config` (+ `_llm_test_key`), `api_ollama_list_models`, `api_bedrock_list_models`, `api_openrouter_list_models`, `api_login`, `api_signup` (+ `_client_host`), `_require_loopback`, `api_mcp_tools_list`, `api_mcp_tool_call`, the `__main__` block. |
| `docker-compose.yml` | `FORWARDED_ALLOW_IPS` on the `api` service. |
| `.env.example`, `docs/runbooks/users-and-login.md` | The new knobs. |
| `backend/tests/test_api_authorization.py` | `PUBLIC_ROUTES` reason strings only. |
| New tests | `test_llm_url_policy.py`, `test_ollama_ambient_key.py`, `test_llm_test_base_url_policy.py`, `test_list_models_hardening.py`, `test_login_signup_limits.py`, `test_server_config.py`, `test_loopback_internal_routes.py` (all under `backend/tests/`). |

---

### Task 1: LLM base-URL policy module

**Files:**
- Create: `backend/llm_url_policy.py`
- Modify: `.env.example` (LLM section, next to `OLLAMA_API_KEY`)
- Test: `backend/tests/test_llm_url_policy.py`

**Interfaces:**
- Consumes: nothing.
- Produces (`backend/llm_url_policy.py`, imported as `llm_url_policy`):
  - `ROW_BASE_URL_FIELD: dict[str, str] = {"openai": "openai_base_url", "azure": "azure_openai_endpoint", "ollama": "ollama_base_url", "openrouter": "openrouter_base_url"}`. These are both the Models-row field and the `LlmConfigTestBody` field. nvidia is absent because `_build_llm_test_provider_config` hard-codes its URL; bedrock uses a region, not a URL.
  - `OFFICIAL_HTTPS_HOSTS = {"openai": {"api.openai.com"}, "openrouter": {"openrouter.ai"}, "ollama": {"ollama.com"}, "nvidia": {"integrate.api.nvidia.com"}}` (https only; azure has none).
  - `BUILTIN_DEFAULT_HOSTS = {"ollama": {"localhost", "127.0.0.1"}}` (the code's own default, `http://localhost:11434`).
  - `ENV_BASE_URL_VARS = {"openai": ("OPENAI_BASE_URL",), "azure": ("AZURE_OPENAI_ENDPOINT",), "nvidia": ("NVIDIA_BASE_URL",), "openrouter": ("OPENROUTER_BASE_URL",), "ollama": ("OLLAMA_BASE_URL",)}`
  - `ALLOWLIST_ENV = "LLM_BASE_URL_ALLOWLIST"`
  - `class BaseUrlInvalid(ValueError)`, whose `str()` is exactly `"Invalid base URL: use http:// or https:// with a host and no credentials."` (never echoes input).
  - `class BaseUrlNotAllowed(ValueError)` with `.provider: str`; its `str()` is the withheld-key copy in the tests below.
  - `parse_base_url(url: str) -> tuple[str, str]` returns `(scheme, host)` lower-cased, or raises `BaseUrlInvalid`.
  - `is_allowed_base_url(provider: str, url: str, *, saved_row: Mapping[str, Any] | None = None, env: Mapping[str, str] | None = None) -> bool`. An invalid URL gives False and never raises. `env=None` means `os.environ`.
  - `guard_key_for_base_url(provider: str, *, key: str, source: str, caller_base_url: str, saved_row: Mapping[str, Any] | None, env: Mapping[str, str] | None = None) -> str`, with `source` one of `"caller" | "saved" | "env"`.

- [ ] **Step 0: Cut the branch**

```bash
cd <WT>
git fetch origin
git merge-base --is-ancestor 4420fea6 origin/main && echo base-ok
```
Expected: `base-ok` (PR #188 is in main). **If it is missing, stop and tell the controller. Do not branch from an older main.**
```bash
git status --short          # only untracked docs/superpowers/* may appear
git switch -c fix/api-hardening-mcp-prereqs origin/main
python -m pytest -q -p no:cacheprovider backend/tests 2>&1 | tail -5   # baseline: record pre-existing failures in the task report
```

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from llm_url_policy import (BaseUrlInvalid, BaseUrlNotAllowed, guard_key_for_base_url,
                            is_allowed_base_url, parse_base_url)

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
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `python -m pytest -q -p no:cacheprovider backend/tests/test_llm_url_policy.py`
Expected: collection error, `ModuleNotFoundError: No module named 'llm_url_policy'`.

- [ ] **Step 3: Implement `backend/llm_url_policy.py`**

- `parse_base_url`:
  1. Strip the input; empty is invalid.
  2. With no `"://"`, prefix `"http://"`.
  3. `urllib.parse.urlsplit`; the scheme must be `http` or `https`.
  4. A username or password is invalid; a hostname is required.
  5. Reading `.port` must not raise.
  6. An IP-literal host with `is_link_local` is invalid.
  7. Return `(scheme, hostname)`. `urlsplit` already lower-cases and strips IPv6 brackets.
- `is_allowed_base_url`: normalise the provider with `strip().lower()`; a parse failure gives False. The URL is allowed when any of these holds:
  - the scheme is `https` and the host is in `OFFICIAL_HTTPS_HOSTS[p]`;
  - the host is in `BUILTIN_DEFAULT_HOSTS[p]`;
  - the host equals the host of a set, parseable `env[var]` for a var in `ENV_BASE_URL_VARS[p]`;
  - the host is listed in `env[ALLOWLIST_ENV]` for `p` or for all providers;
  - `saved_row` is given, its `provider` (lower-cased) equals `p`, and the host equals the host of `saved_row[ROW_BASE_URL_FIELD[p]]`.

  Host comparison ignores port. Only the official set constrains the scheme.
- Allowlist grammar: comma-separated. Each entry is `provider=host` or a bare `host` (all providers). Ignore whitespace and empty entries, compare case-insensitively, and read an entry containing `://` through `parse_base_url`.
- `guard_key_for_base_url`:
  - Return `key` when `source == "caller"`, the stripped `caller_base_url` is empty, or `key` is empty.
  - For `source == "saved"`, check `is_allowed_base_url(..., saved_row=saved_row)`. For `source == "env"`, check `is_allowed_base_url(..., saved_row=None)`.
  - If allowed, return `key`. Otherwise return `""` for `ollama`, else raise `BaseUrlNotAllowed(provider)`.
  - Any other `source` raises `ValueError` (programming error).

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `python -m pytest -q -p no:cacheprovider backend/tests/test_llm_url_policy.py`
Expected: all pass.

- [ ] **Step 5: Document the knob in `.env.example`** (commented out, after `OLLAMA_API_KEY=`):

```
# LLM_BASE_URL_ALLOWLIST: extra hosts a SAVED or SERVER LLM key may be sent to
# when a caller supplies a base URL (POST /llm/test, /ollama/list-models).
# Comma-separated "provider=host" or bare "host" (all providers). Official hosts,
# the *_BASE_URL/AZURE_OPENAI_ENDPOINT hosts above, and a saved model's own host
# are always allowed. A key typed into the request may go anywhere.
# LLM_BASE_URL_ALLOWLIST=
```

- [ ] **Step 6: Commit** (run `detect_changes` first; expect only new symbols)

```bash
git add backend/llm_url_policy.py backend/tests/test_llm_url_policy.py .env.example
git commit -m "feat(llm): host allowlist policy for saved and server LLM keys" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Ollama runtime client can refuse the SDK's ambient key

**Files:**
- Modify: `backend/ollama_client.py` (add one class; no behaviour change in this task)
- Modify: `backend/llm_utils.py`: `_make_ollama_sync_client`, `_call_ollama`, and the `ollama` branch of `call_llm_by_provider`
- Test: `backend/tests/test_ollama_ambient_key.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ollama_client.NoAmbientAuthorization(httpx.Auth)`, whose `auth_flow` removes any `Authorization` header from each request before it is sent. It works for sync and async httpx clients.
  - `llm_utils._make_ollama_sync_client(base_url: str, api_key: str | None, timeout: float, *, allow_ambient_key: bool = True)`. When `allow_ambient_key` is False and `api_key` is empty, it passes `auth=NoAmbientAuthorization()` to `ollama.Client` (import it lazily inside the function).
  - `llm_utils._call_ollama(..., allow_ambient_key: bool = True)`, forwarded to `_make_ollama_sync_client`.
  - provider_config key `"ollama_allow_ambient_key"` (bool, default True). `call_llm_by_provider`'s ollama branch passes `allow_ambient_key=bool(resolved.get("ollama_allow_ambient_key", True))`. `_resolve_provider_config` already preserves unknown keys, so leave it unchanged.

- [ ] **Step 1: Impact analysis.** Run `mcp__gitnexus__impact` (upstream) on `_make_ollama_sync_client`, `_call_ollama` and `call_llm_by_provider`, then confirm with `grep -rn "_make_ollama_sync_client\|_call_ollama(\|call_llm_by_provider(" backend --include=*.py | grep -v /tests/`. Callers at plan time:
  - `_make_ollama_sync_client` is called by `_call_ollama` and `call_ollama_with_tools`;
  - `_call_ollama` is called only by the ollama branch of `call_llm_by_provider`;
  - `call_llm_by_provider` is a hub with many callers, so expect HIGH.

  Record in the report that the change is a default-preserving keyword read in the ollama branch. Leave `call_ollama_with_tools` untouched.

- [ ] **Step 2: Write the failing tests**

```python
import httpx, ollama, pytest

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

def test_dispatcher_forwards_the_flag(monkeypatch):
    import llm_utils
    calls: list = []
    monkeypatch.setattr(llm_utils, "_call_ollama", lambda *a, **kw: calls.append(kw) or "ok")
    # distinct prompts so the prompt cache can never short-circuit the second call
    llm_utils.call_llm_by_provider("ollama", "", "m", "p-off", provider_config={"ollama_allow_ambient_key": False})
    llm_utils.call_llm_by_provider("ollama", "", "m", "p-on", provider_config={})
    assert [c["allow_ambient_key"] for c in calls] == [False, True]
```

- [ ] **Step 3: Run the tests to confirm they fail**

Run: `python -m pytest -q -p no:cacheprovider backend/tests/test_ollama_ambient_key.py`
Expected: FAIL with `ImportError` (`NoAmbientAuthorization`) and `TypeError: unexpected keyword argument 'allow_ambient_key'`.

- [ ] **Step 4: Implement the three signatures above.**

- [ ] **Step 5: Run the tests to confirm they pass, with no regression**

Run: `python -m pytest -q -p no:cacheprovider backend/tests/test_ollama_ambient_key.py backend/tests/test_llm_utils_ollama.py backend/tests/test_llm_utils_ollama_tools.py backend/tests/test_ollama_client.py`
Expected: all pass.

- [ ] **Step 6: Commit** (`detect_changes`: only the three llm_utils functions and the new class)

```bash
git add backend/ollama_client.py backend/llm_utils.py backend/tests/test_ollama_ambient_key.py
git commit -m "fix(llm): let callers stop the ollama SDK attaching OLLAMA_API_KEY" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `/llm/test` never pairs a saved or server key with an unlisted base URL

**Files:**
- Modify: `backend/api/main.py`: `api_test_llm_config`, plus a new helper `_llm_test_key` beside it
- Test: `backend/tests/test_llm_test_base_url_policy.py`

**Interfaces:**
- Consumes: Task 1 (`ROW_BASE_URL_FIELD`, `parse_base_url`, `is_allowed_base_url`, `guard_key_for_base_url`, `BaseUrlInvalid`, `BaseUrlNotAllowed`) and Task 2 (provider_config key `"ollama_allow_ambient_key"`).
- Produces: `_llm_test_key(conn, body: LlmConfigTestBody, provider: str) -> tuple[str, str, dict | None]`, returning `(key, source, saved_row)`. Its precedence matches today's exactly:
  - A non-blank, non-masked submitted key gives `(submitted, "caller", None)`.
  - A masked key without `model_id` gives `(masked, "caller", None)`. A masked string is not a secret.
  - A blank or masked key with `model_id` reads the row through `action_get_model_raw` (row None on failure) and gives `(row key, "saved", row)`.
  - If that leaves an empty key, it falls through to `(resolve_api_key_for_provider(provider, ""), "env", row)`.

- [ ] **Step 1: Impact analysis** on `api_test_llm_config` and `_build_llm_test_provider_config` (the latter only to confirm it is NOT edited). Callers: FastAPI only. The clients are `frontend/src/views/ModelsView.vue` (`_runLlmTest`), `frontend/src/components/onboarding/StepAddModel.vue` and `ios/.../ModelRepository.swift`; all of them display `detail`.

- [ ] **Step 2: Write the failing tests.** An autouse fixture deletes the LLM env vars (Global Constraints). Fixture `llm_test`:
  - Override `get_current_user` and `conn_dependency` on `api.main.app`.
  - Patch `api.main.action_get_model_raw` to return `llm_test.saved_row` (default `{"id": "m1", "provider": "openai", "api_key": "saved-secret", "openai_base_url": ""}`).
  - Patch `api.main.call_structured_llm_by_provider` to record `(args[1], kwargs["provider_config"])` into `llm_test.calls` and return an object with `ok=True` (or `llm_test.structured_result` when set to None).
  - Patch `api.main.call_llm_by_provider` to return `"PONG"` (or raise `llm_test.smoke_exc`), and record its provider_config into `llm_test.smoke_configs`.
  - Patch `api.main.get_last_structured_llm_call_metadata` to return `llm_test.meta` (default `{"effective_model": "m", "provider_meta": {}}`).
  - Expose `llm_test.post(json) -> Response`. Pop the overrides on teardown.

```python
HIDDEN = ("LLM connectivity test failed. Details are hidden because the base URL is not on the "
          "allowlist; the API log has them.")
HIDDEN_SMOKE = "Smoke generation failed. Details are hidden because the base URL is not on the allowlist."
EDIT = {"provider": "openai", "model": "gpt-x", "api_key": "sk-****abcd", "model_id": "m1"}

def test_saved_key_is_not_sent_to_an_unlisted_base_url(llm_test):
    r = llm_test.post({**EDIT, "openai_base_url": "https://evil.example/v1"})
    assert r.status_code == 400 and "LLM_BASE_URL_ALLOWLIST" in r.json()["detail"]
    assert llm_test.calls == []

def test_server_key_is_not_sent_to_an_unlisted_base_url(llm_test, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-secret")
    r = llm_test.post({"provider": "openai", "model": "gpt-x", "openai_base_url": "https://evil.example/v1"})
    assert r.status_code == 400 and llm_test.calls == []

def test_server_key_still_used_without_a_caller_url(llm_test, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-secret")
    assert llm_test.post({"provider": "openai", "model": "gpt-x"}).status_code == 200
    assert llm_test.calls[0][0] == "env-secret"

def test_caller_key_may_go_to_any_url(llm_test):
    r = llm_test.post({"provider": "openai", "model": "gpt-x", "api_key": "sk-mine",
                       "openai_base_url": "https://evil.example/v1"})
    assert r.status_code == 200 and llm_test.calls[0][0] == "sk-mine"

def test_saved_key_is_used_for_its_own_row_host(llm_test):
    llm_test.saved_row = {**llm_test.saved_row, "openai_base_url": "https://llm.lan/v1"}
    assert llm_test.post({**EDIT, "openai_base_url": "https://llm.lan/v1"}).status_code == 200
    assert llm_test.calls[0][0] == "saved-secret"

@pytest.mark.parametrize("provider,row_url,form_url", [
    ("openrouter", {"openrouter_base_url": ""}, {"openrouter_base_url": "https://openrouter.ai/api/v1"}),
    ("ollama", {"ollama_base_url": ""}, {"ollama_base_url": "http://localhost:11434"}),
])
def test_edit_flow_with_client_default_base_url_still_uses_saved_key(llm_test, provider, row_url, form_url):
    llm_test.saved_row = {"id": "m1", "provider": provider, "api_key": "saved-secret", **row_url}
    r = llm_test.post({"provider": provider, "model": "m", "api_key": "sk-****abcd", "model_id": "m1", **form_url})
    assert r.status_code == 200 and llm_test.calls[0][0] == "saved-secret"

def test_unlisted_ollama_url_runs_keyless_without_the_ambient_key(llm_test, monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "env-secret")
    r = llm_test.post({"provider": "ollama", "model": "llama3.2", "ollama_base_url": "http://10.9.9.9:11434"})
    assert r.status_code == 200
    key, cfg = llm_test.calls[0]
    assert key == "" and cfg["ollama_allow_ambient_key"] is False
    assert llm_test.smoke_configs[0]["ollama_allow_ambient_key"] is False

def test_failure_detail_is_hidden_for_an_unlisted_url(llm_test):
    llm_test.structured_result = None
    llm_test.meta = {"error": "Connection refused: 10.9.9.9:8080"}
    r = llm_test.post({"provider": "openai", "model": "gpt-x", "api_key": "sk-mine",
                       "openai_base_url": "http://10.9.9.9:8080/v1"})
    assert r.status_code == 400 and r.json()["detail"] == HIDDEN
    assert "10.9.9.9" not in r.text and "refused" not in r.text.lower()

def test_failure_detail_passes_through_for_official_hosts(llm_test):
    llm_test.structured_result = None
    llm_test.meta = {"error": "Incorrect API key provided"}
    r = llm_test.post({"provider": "openai", "model": "gpt-x", "api_key": "sk-bad"})
    assert r.status_code == 400 and r.json()["detail"] == "Incorrect API key provided"

def test_smoke_error_is_hidden_for_an_unlisted_url(llm_test):
    llm_test.smoke_exc = RuntimeError("connect ECONNREFUSED 10.9.9.9:8080")
    r = llm_test.post({"provider": "openai", "model": "gpt-x", "api_key": "sk-mine",
                       "openai_base_url": "http://10.9.9.9:8080/v1"})
    assert r.status_code == 200 and r.json()["smoke_error"] == HIDDEN_SMOKE

def test_malformed_base_url_is_refused_before_any_call(llm_test):
    r = llm_test.post({"provider": "openai", "model": "gpt-x", "api_key": "sk-mine",
                       "openai_base_url": "https://u:p@api.openai.com/v1"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Invalid base URL: use http:// or https:// with a host and no credentials."
    assert llm_test.calls == []
```

- [ ] **Step 3: Run the tests to confirm they fail**

Run: `python -m pytest -q -p no:cacheprovider backend/tests/test_llm_test_base_url_policy.py`
Expected: the first two tests FAIL (200 instead of 400) and the hidden-detail tests FAIL.

- [ ] **Step 4: Implement in `api_test_llm_config`**, keeping the existing provider and model checks first:
  1. `caller_url` is the stripped value of the body field `ROW_BASE_URL_FIELD[provider]`, or `""` when the provider is not in the map.
  2. If `caller_url` is set, call `parse_base_url` and turn `BaseUrlInvalid` into a 400 with `detail=str(e)`.
  3. Get `key, source, row` from `_llm_test_key(...)`, then `api_key = guard_key_for_base_url(provider, key=key, source=source, caller_base_url=caller_url, saved_row=row)`, turning `BaseUrlNotAllowed` into a 400 with `detail=str(e)`. This guard runs before any LLM call.
  4. `verbose = not caller_url or is_allowed_base_url(provider, caller_url, saved_row=row)`.
  5. Keep the existing "No API key provided…" check unchanged.
  6. After `_build_llm_test_provider_config(body)`, set `provider_config["ollama_allow_ambient_key"] = False` when `provider == "ollama"`. Do this outside the builder, because existing tests assert the builder's output.
  7. When `not verbose`, replace the stage-1 failure detail with `HIDDEN` and a non-empty `_smoke_error` with `HIDDEN_SMOKE` (module constants `_HIDDEN_LLM_TEST_ERROR` and `_HIDDEN_SMOKE_ERROR`). Log the original text via `logging.getLogger("uvicorn.error").warning(...)` with the provider and the host.

- [ ] **Step 5: Run the tests to confirm they pass, with no regression**

Run: `python -m pytest -q -p no:cacheprovider backend/tests/test_llm_test_base_url_policy.py backend/tests/test_models_api_ollama.py backend/tests/test_models_api_openrouter.py backend/tests/test_models_api_bedrock.py backend/tests/test_strategy_codex_cli_dispatch.py backend/tests/test_llm_timeout_sweep.py`
Expected: all pass. `test_llm_test_endpoint_accepts_local_ollama_without_api_key` still passes because keyless Ollama is allowed.

- [ ] **Step 6: Commit** (`detect_changes`: `api_test_llm_config`, `_llm_test_key`)

```bash
git add backend/api/main.py backend/tests/test_llm_test_base_url_policy.py
git commit -m "fix(api): /llm/test never sends a saved or server key to an unlisted base URL" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: List-models routes send explicit keys only and return fixed error text

**Files:**
- Modify: `backend/ollama_client.py`: `list_models` and `show_model` (via a new `_client_kwargs`)
- Modify: `backend/api/main.py`: `api_ollama_list_models`, `api_bedrock_list_models`, `api_openrouter_list_models`
- Test: `backend/tests/test_list_models_hardening.py`

**Interfaces:**
- Consumes: Task 1 (`parse_base_url`, `is_allowed_base_url`, `BaseUrlInvalid`) and Task 2 (`NoAmbientAuthorization`).
- Produces:
  - `ollama_client._client_kwargs(api_key: str | None) -> dict` returns `{"headers": _auth_headers(api_key)}`, plus `"auth": NoAmbientAuthorization()` when `api_key` is empty.
  - `list_models` and `show_model` build `AsyncClient(host=..., timeout=..., **_client_kwargs(api_key))`. Their public signatures are unchanged, and they never send the ambient key.

- [ ] **Step 1: Impact analysis** on `list_models` (use `target_uid: "Function:backend/ollama_client.py:list_models"`), `show_model`, `api_ollama_list_models`, `api_bedrock_list_models` and `api_openrouter_list_models`. Callers at plan time:
  - `ollama_client.list_models` is called by `api_ollama_list_models` and `ollama_client.health_check`;
  - `show_model` has no non-test caller;
  - the routes are FastAPI-only. Their clients are `frontend/src/composables/use{Ollama,Bedrock,OpenRouter}Models.js`, which show `body.error || body.detail`, and the iOS model pickers.

- [ ] **Step 2: Write the failing tests** (an autouse fixture deletes the LLM env vars; the `app_client` fixture overrides `get_current_user`, as `test_ollama_list_models_endpoint.py` does)

```python
def _capture_ollama(monkeypatch):
    seen = {}
    async def fake(base_url, api_key=None, **_):
        seen.update(base_url=base_url, api_key=api_key); return []
    monkeypatch.setattr("api.main.ollama_client.list_models", fake)
    return seen

def test_ollama_env_key_goes_only_to_allowlisted_hosts(app_client, monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "env-secret")
    seen = _capture_ollama(monkeypatch)
    assert app_client.post("/ollama/list-models", json={"base_url": "https://ollama.com"}).status_code == 200
    assert seen["api_key"] == "env-secret"
    assert app_client.post("/ollama/list-models", json={"base_url": "http://10.9.9.9:11434"}).status_code == 200
    assert not seen["api_key"]

def test_ollama_caller_key_goes_anywhere(app_client, monkeypatch):
    seen = _capture_ollama(monkeypatch)
    app_client.post("/ollama/list-models", json={"base_url": "http://10.9.9.9:11434", "api_key": "mine"})
    assert seen["api_key"] == "mine"

@pytest.mark.parametrize("exc,status,body", [
    (OllamaConnectionError("Could not reach Ollama at http://10.9.9.9:22 [Errno 111] Connection refused"),
     502, {"error": "Could not reach the Ollama host"}),
    (OllamaProviderError("Ollama returned 404 listing models: <html>internal page</html>"),
     502, {"error": "The Ollama host returned an error"}),
    (OllamaAuthError("Unauthorized at http://10.9.9.9:11434"), 401, {"error": "Authentication failed"}),
])
def test_ollama_errors_are_fixed_strings(app_client, monkeypatch, exc, status, body):
    monkeypatch.setattr("api.main.ollama_client.list_models", AsyncMock(side_effect=exc))
    r = app_client.post("/ollama/list-models", json={"base_url": "http://10.9.9.9:11434"})
    assert (r.status_code, r.json()) == (status, body)

@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://user:pw@h:11434", "http://169.254.169.254/"])
def test_ollama_rejects_bad_urls_before_any_call(app_client, monkeypatch, url):
    seen = _capture_ollama(monkeypatch)
    assert app_client.post("/ollama/list-models", json={"base_url": url}).status_code == 400
    assert seen == {}

@pytest.mark.parametrize("exc,status,body", [
    (BedrockConnectionError("Could not reach Bedrock in us-east-1: Connect timeout on endpoint URL"),
     502, {"error": "Could not reach Bedrock in us-east-1"}),
    (BedrockAuthError("AccessDeniedException: User arn:aws:iam::123:user/x is not authorized"),
     401, {"error": "Authentication failed (the key may lack bedrock:ListFoundationModels)"}),
    (BedrockProviderError("ValidationException: bad"), 502, {"error": "Bedrock returned an error"}),
])
def test_bedrock_errors_are_fixed_strings(app_client, monkeypatch, exc, status, body):
    monkeypatch.setattr("api.main.bedrock_client.list_models", Mock(side_effect=exc))
    r = app_client.post("/bedrock/list-models", json={"api_key": "k", "region": "us-east-1"})
    assert (r.status_code, r.json()) == (status, body)

def test_openrouter_rejects_bad_urls(app_client, monkeypatch):
    called = Mock(return_value=[])
    monkeypatch.setattr("api.main.openrouter_client.list_models", called)
    assert app_client.post("/openrouter/list-models", json={"base_url": "ftp://x/"}).status_code == 400
    called.assert_not_called()

def test_ollama_discovery_client_never_sends_the_ambient_key(monkeypatch):
    import asyncio, httpx, ollama, ollama_client
    monkeypatch.setenv("OLLAMA_API_KEY", "env-secret")
    seen: list = []
    def handler(r):
        seen.append({k.lower(): v for k, v in r.headers.items()}); return httpx.Response(200, json={"models": []})
    real = ollama.AsyncClient
    monkeypatch.setattr(ollama_client, "AsyncClient",
                        lambda *a, **kw: real(*a, transport=httpx.MockTransport(handler), **kw))
    asyncio.run(ollama_client.list_models("http://h:11434"))
    assert "authorization" not in seen[-1]
    asyncio.run(ollama_client.list_models("http://h:11434", "mine"))
    assert seen[-1]["authorization"] == "Bearer mine"
```

- [ ] **Step 3: Run the tests to confirm they fail**

Run: `python -m pytest -q -p no:cacheprovider backend/tests/test_list_models_hardening.py`
Expected: FAIL. Today the errors echo the URL and detail, bad URLs are not refused, and the discovery client sends the env key.

- [ ] **Step 4: Implement**
  - `ollama_client` uses `_client_kwargs`.
  - `api_ollama_list_models`:
    1. `parse_base_url(body.base_url)`; `BaseUrlInvalid` becomes `HTTPException(400, str(e))`.
    2. The key is `body.api_key` if set, otherwise `os.environ.get("OLLAMA_API_KEY")` when `is_allowed_base_url("ollama", body.base_url)`, otherwise None.
    3. Map errors to the three fixed bodies above, logging the exception with the host on `uvicorn.error` at WARNING.
  - `api_bedrock_list_models`: the three fixed bodies; drop every `detail` key.
  - `api_openrouter_list_models`: when `body.base_url` is set, run `parse_base_url` (400 on failure); the success and empty response is unchanged.

- [ ] **Step 5: Run the tests to confirm they pass, with no regression**

Run: `python -m pytest -q -p no:cacheprovider backend/tests/test_list_models_hardening.py backend/tests/test_ollama_list_models_endpoint.py backend/tests/test_ollama_client.py backend/tests/test_models_api_bedrock.py backend/tests/test_models_api_openrouter.py`
Expected: all pass. The existing `"Could not reach" in error` assertion still holds, and `https://proxy/api/v1` is still accepted.

- [ ] **Step 6: Commit** (`detect_changes`: the three routes, `list_models`, `show_model`, `_client_kwargs`)

```bash
git add backend/ollama_client.py backend/api/main.py backend/tests/test_list_models_hardening.py
git commit -m "fix(api): list-models routes send explicit keys only and return fixed errors" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Uvicorn startup trusts forwarded headers only from configured proxies

**Files:**
- Create: `backend/api/server_config.py`
- Modify: `backend/api/main.py`, the `if __name__ == "__main__":` block only
- Modify: `docker-compose.yml` (`api` service `environment`), `.env.example`, `docs/runbooks/users-and-login.md`
- Test: `backend/tests/test_server_config.py`

**Interfaces:**
- Consumes: nothing. The integration tests use today's per-(username, IP) limiter; Task 6 adds the per-IP bucket on top.
- Produces (`api.server_config`; the MCP gate PR reuses these for :8012 with `proxy_headers=False`):
  - `trusted_proxy_entries(raw: str | None) -> list[str]`: split on commas and strip. Keep IPs, and CIDRs normalised with `ipaddress.ip_network(e, strict=False)`. Drop `*`, any network with `prefixlen == 0`, hostnames and invalid entries, each with a warning. An empty result becomes `["127.0.0.1"]`.
  - `trusted_proxy_configured(raw: str | None = None) -> bool`: `raw=None` reads `os.environ.get("FORWARDED_ALLOW_IPS")`. True when `trusted_proxy_entries(raw)` holds at least one entry whose network is not loopback (`ip_network(e, strict=False).is_loopback` is False). Unset, `127.0.0.1`, `::1`, `*` and garbage give False. Task 6 uses it to decide whether the per-IP login limit applies.
  - `build_uvicorn_config(app, *, host: str, port: int, proxy_headers: bool) -> uvicorn.Config`.
    - With `proxy_headers=True`: `uvicorn.Config(app, host=host, port=port, proxy_headers=True, forwarded_allow_ips=",".join(trusted_proxy_entries(os.environ.get("FORWARDED_ALLOW_IPS"))))`. Log at INFO on `_log = logging.getLogger("uvicorn.error")`: `"Proxy headers: trusting X-Forwarded-For from %s"`. When the variable is unset or blank, also log a WARNING: `"FORWARDED_ALLOW_IPS is not set: X-Forwarded-For is trusted only from 127.0.0.1, so behind a reverse proxy every client shares the proxy's address; the per-IP login limit stays off until it is set."`
    - With `proxy_headers=False`: `uvicorn.Config(app, host=host, port=port, proxy_headers=False)`.
  - `run_server(config: uvicorn.Config) -> None`: `server = uvicorn.Server(config); server.run()`, then `raise SystemExit(3)` if `not server.started`. This keeps `uvicorn.run`'s STARTUP_FAILURE exit.
  - `main.py` tail: `from api.server_config import build_uvicorn_config, run_server`, then `run_server(build_uvicorn_config(app, host=API_HOST, port=API_PORT, proxy_headers=True))`.

- [ ] **Step 1: Impact analysis.** No existing function is edited. The `__main__` block is module code; its only caller is `docker-compose.yml` `api.command: ["python", "api/main.py"]`. Confirm with `grep -rn "api/main.py\|api.main:app" docker-compose.yml backend/Dockerfile scripts`.

- [ ] **Step 2: Write the failing tests.** Unit tests use a three-line ASGI app. The `served` fixture mirrors `users_app` in `test_users_api.py`:
  - `store` fixture, `JWT_SECRET` set, `auth_utils.store` patched, and `ensure_users_table` no-ops on `auth_utils` and `main`;
  - limiter reset and a `conn_dependency` override;
  - `FORWARDED_ALLOW_IPS=10.0.0.0/8`, then `cfg = build_uvicorn_config(main.app, host="127.0.0.1", port=8011, proxy_headers=True); cfg.load()`;
  - `auth_utils.create_user(None, "root", "the-right-password")`, and yield `(cfg.loaded_app, auth_utils)`.

  `_via(app, peer)` is `TestClient(app, client=(peer, 50000))`.

```python
@pytest.mark.parametrize("raw,expected", [
    (None, ["127.0.0.1"]), ("", ["127.0.0.1"]), ("  ", ["127.0.0.1"]),
    ("10.0.1.0/24, 172.18.0.5 ,,", ["10.0.1.0/24", "172.18.0.5"]),
    ("10.0.1.5/24", ["10.0.1.0/24"]), ("fd00::/64", ["fd00::/64"]),
    ("*", ["127.0.0.1"]), ("0.0.0.0/0,10.0.1.0/24", ["10.0.1.0/24"]), ("::/0", ["127.0.0.1"]),
    ("traefik,10.0.1.300", ["127.0.0.1"]),
])
def test_trusted_proxy_entries(raw, expected):
    assert trusted_proxy_entries(raw) == expected

@pytest.mark.parametrize("raw,expected", [
    (None, False), ("", False), ("127.0.0.1", False), ("::1,127.0.0.1", False), ("*", False), ("traefik", False),
    ("10.0.1.0/24", True), ("127.0.0.1,10.0.1.5", True), ("fd00::/64", True),
])
def test_trusted_proxy_configured(raw, expected):
    assert trusted_proxy_configured(raw) is expected

def test_trusted_proxy_configured_reads_the_environment(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.1.0/24")
    assert trusted_proxy_configured() is True
    monkeypatch.delenv("FORWARDED_ALLOW_IPS")
    assert trusted_proxy_configured() is False

def test_proxy_listener_trusts_only_the_configured_proxies(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.1.0/24")
    cfg = build_uvicorn_config(tiny_app, host="0.0.0.0", port=8011, proxy_headers=True)
    assert (cfg.host, cfg.port, cfg.proxy_headers, cfg.forwarded_allow_ips) == ("0.0.0.0", 8011, True, "10.0.1.0/24")
    cfg.load(); assert isinstance(cfg.loaded_app, ProxyHeadersMiddleware)

def test_listener_without_proxy_headers_never_rewrites(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.1.0/24")
    cfg = build_uvicorn_config(tiny_app, host="0.0.0.0", port=8012, proxy_headers=False)
    cfg.load(); assert cfg.proxy_headers is False and not isinstance(cfg.loaded_app, ProxyHeadersMiddleware)

def test_unset_forwarded_allow_ips_warns(monkeypatch):
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    log = Mock(); monkeypatch.setattr(server_config, "_log", log)
    build_uvicorn_config(tiny_app, host="0.0.0.0", port=8011, proxy_headers=True)
    assert "FORWARDED_ALLOW_IPS" in log.warning.call_args.args[0]

def test_run_server_exits_3_when_startup_fails(monkeypatch):
    monkeypatch.setattr(uvicorn.Server, "run", lambda self, sockets=None: None)   # never sets started
    with pytest.raises(SystemExit) as e:
        run_server(build_uvicorn_config(tiny_app, host="127.0.0.1", port=8011, proxy_headers=False))
    assert e.value.code == 3

def test_run_server_returns_after_a_clean_run(monkeypatch):
    def ran(self, sockets=None): self.started = True
    monkeypatch.setattr(uvicorn.Server, "run", ran)
    assert run_server(build_uvicorn_config(tiny_app, host="127.0.0.1", port=8011, proxy_headers=False)) is None

def test_main_entrypoint_uses_the_builder():
    src = open(os.path.join(BACKEND, "api", "main.py"), encoding="utf-8").read()
    tail = src[src.index('if __name__ == "__main__":'):]
    assert "build_uvicorn_config(" in tail and "proxy_headers=True" in tail and "uvicorn.run(" not in tail

BAD = {"username": "root", "password": "wrong-password"}

def test_one_clients_failures_do_not_lock_out_the_operator(served):
    app, _ = served
    via_traefik = _via(app, "10.0.1.2")
    for _ in range(10):
        assert via_traefik.post("/auth/login", json=BAD, headers={"X-Forwarded-For": "203.0.113.5"}).status_code == 401
    assert via_traefik.post("/auth/login", json=BAD, headers={"X-Forwarded-For": "203.0.113.5"}).status_code == 429
    ok = via_traefik.post("/auth/login", json={"username": "root", "password": "the-right-password"},
                          headers={"X-Forwarded-For": "198.51.100.7"})
    assert ok.status_code == 200                                   # human JWT login still works
    me = via_traefik.get("/auth/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}",
                                              "X-Forwarded-For": "198.51.100.7"})
    assert me.status_code == 200 and me.json()["username"] == "root"

def test_multi_hop_forwarded_for_keys_on_the_rightmost_untrusted_hop(served):
    app, auth_utils = served
    for _ in range(10):
        _via(app, "10.0.1.2").post("/auth/login", json=BAD, headers={"X-Forwarded-For": "203.0.113.5, 10.0.1.9"})
    assert auth_utils.login_is_rate_limited("root", "203.0.113.5")
    assert not auth_utils.login_is_rate_limited("root", "10.0.1.9")

def test_forwarded_for_from_an_untrusted_peer_is_ignored(served):   # straight to the host-published :8011
    app, auth_utils = served
    direct = _via(app, "198.51.100.9")
    for i in range(10):
        direct.post("/auth/login", json=BAD, headers={"X-Forwarded-For": f"192.0.2.{i}"})
    assert auth_utils.login_is_rate_limited("root", "198.51.100.9")
```

- [ ] **Step 3: Run the tests to confirm they fail**

Run: `python -m pytest -q -p no:cacheprovider backend/tests/test_server_config.py`
Expected: collection error, `ModuleNotFoundError: No module named 'api.server_config'`.

- [ ] **Step 4: Implement** `server_config.py` and the `main.py` tail (Interfaces).
  - `docker-compose.yml`, `api.environment`: add `- FORWARDED_ALLOW_IPS=${FORWARDED_ALLOW_IPS:-127.0.0.1}` with a two-line comment ("reverse proxies whose X-Forwarded-For the API trusts; set to Traefik's address or the dokploy-network subnet").
  - `.env.example`: a commented `# FORWARDED_ALLOW_IPS=127.0.0.1` with the same explanation.
  - `docs/runbooks/users-and-login.md`: a short section, "Login limits behind the proxy". It covers the limits (10 per user+IP per 15 min; 30 per IP per 15 min, but only once `FORWARDED_ALLOW_IPS` names the proxy; signup 5 per IP per hour), why `FORWARDED_ALLOW_IPS` must name Traefik, the two read-only `docker` commands from the deploy notes, and the unlock: restarting the api container clears the in-memory buckets.

- [ ] **Step 5: Run the tests to confirm they pass, with no regression**

Run: `python -m pytest -q -p no:cacheprovider backend/tests/test_server_config.py backend/tests/test_auth_hardening.py backend/tests/test_users_api.py backend/tests/test_api_authorization.py backend/tests/test_app_updates.py`
Expected: all pass. `test_app_updates.py` reads `X-Forwarded-*` itself and must be unaffected.

- [ ] **Step 6: Commit** (`detect_changes`: new module plus the `main.py` tail)

```bash
git add backend/api/server_config.py backend/api/main.py backend/tests/test_server_config.py docker-compose.yml .env.example docs/runbooks/users-and-login.md
git commit -m "fix(api): trust X-Forwarded-For only from FORWARDED_ALLOW_IPS" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Per-IP login limit (only behind a configured proxy) and a signup limiter

**Files:**
- Modify: `backend/auth_utils.py` (login rate-limiting section)
- Modify: `backend/api/main.py`: `api_login`, `api_signup`, plus new `_client_host` and `_per_ip_limit_applies`
- Modify: `.env.example` (after `# LOGIN_RATE_LIMIT_WINDOW_SECONDS=900`)
- Test: `backend/tests/test_login_signup_limits.py`

**Interfaces:**
- Consumes: Task 5 (`api.server_config.trusted_proxy_configured`).
- Produces (`auth_utils`). Public names and signatures stay; the MCP gate PR calls them with `client_host="mcp"`.
  - `login_is_rate_limited(username, client_host, now=None, *, per_ip: bool = True) -> bool`: True when the (username, host) bucket has at least `LOGIN_RATE_LIMIT_MAX_ATTEMPTS` entries, or (only when `per_ip`) the host bucket has at least `login_ip_rate_limit_max()` entries, both within `LOGIN_RATE_LIMIT_WINDOW_SECONDS`.
  - `record_failed_login(username, client_host, now=None, *, per_ip: bool = True) -> int`: records into the (username, host) bucket, and into the host bucket only when `per_ip`. It returns the (username, host) count, as today. The default `per_ip=True` is what the MCP gate's direct `(username, "mcp")` calls get, so they share one global bucket.
  - `clear_login_attempts(username, client_host)`: clears the (username, host) bucket only.
  - `login_ip_rate_limit_max() -> int`: env `LOGIN_RATE_LIMIT_PER_IP_MAX_ATTEMPTS`, default 30, read through the existing `_env_int`.
  - `signup_rate_limit_config() -> tuple[int, int]`: `(SIGNUP_RATE_LIMIT_MAX_ATTEMPTS, SIGNUP_RATE_LIMIT_WINDOW_SECONDS)`, default `(5, 3600)`.
  - `signup_is_rate_limited(client_host, now=None) -> bool` and `record_signup_attempt(client_host, now=None) -> int`.
  - `reset_login_rate_limit()` clears all three buckets.
  - Factor the dict + lock + 10,000-bucket ceiling into one private helper reused by the three buckets. Existing bucket behaviour is unchanged.
- Produces (`main`):
  - `_client_host(request: Request | None) -> str` returns `request.client.host`, or `"unknown"` when the request or client is missing or raises. It replaces the inline block in `api_login`.
  - `_per_ip_limit_applies(client_host: str) -> bool` returns `trusted_proxy_configured() or client_host in ("127.0.0.1", "::1", "localhost")`. **Lead ruling:** with no proxy configured, a non-loopback peer is Traefik (every client shares its address), so the per-IP limit is skipped. Otherwise it would lock everyone out at once, including the operator, between the redeploy and someone setting the env var. The per-(username, IP) limit and the signup limit apply in both modes.

- [ ] **Step 1: Impact analysis** on `login_is_rate_limited`, `record_failed_login`, `clear_login_attempts`, `reset_login_rate_limit`, `api_login` and `api_signup`. Callers at plan time: the limiter functions are used only by `api_login`, plus tests (`test_auth_hardening.py`, `test_users_api.py` and `test_api_authorization.py` reset them). The routes are FastAPI-only; `/auth/signup` has no frontend caller (`install.sh` documents it).

- [ ] **Step 2: Write the failing tests** (autouse: `auth_utils.reset_login_rate_limit()` before and after; `JWT_SECRET` set; `_FakeRequest(host)` as in `test_auth_hardening.py`)

```python
T0 = 1_000_000.0

def test_per_ip_default_is_thirty(monkeypatch):
    monkeypatch.delenv("LOGIN_RATE_LIMIT_PER_IP_MAX_ATTEMPTS", raising=False)
    assert auth_utils.login_ip_rate_limit_max() == 30

def test_thirty_failures_across_usernames_from_one_ip_are_limited():
    for i in range(30):
        auth_utils.record_failed_login(f"user{i}", "1.2.3.4", now=T0 + i)
    assert auth_utils.login_is_rate_limited("someone-else", "1.2.3.4", now=T0 + 30)
    assert not auth_utils.login_is_rate_limited("someone-else", "5.6.7.8", now=T0 + 30)
    assert not auth_utils.login_is_rate_limited("someone-else", "1.2.3.4", now=T0 + 29 + 901)

def test_a_success_clears_only_the_user_bucket():
    for i in range(29):
        auth_utils.record_failed_login(f"user{i}", "1.2.3.4", now=T0 + i)
    auth_utils.clear_login_attempts("user0", "1.2.3.4")
    auth_utils.record_failed_login("user0", "1.2.3.4", now=T0 + 29)
    assert auth_utils.login_is_rate_limited("root", "1.2.3.4", now=T0 + 30)

def test_the_mcp_source_key_is_one_shared_bucket():          # contract §4: (username, "mcp")
    for i in range(30):
        auth_utils.record_failed_login(f"u{i}", "mcp", now=T0 + i)
    assert auth_utils.login_is_rate_limited("anyone", "mcp", now=T0 + 30)

def test_signup_defaults(monkeypatch):
    monkeypatch.delenv("SIGNUP_RATE_LIMIT_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("SIGNUP_RATE_LIMIT_WINDOW_SECONDS", raising=False)
    assert auth_utils.signup_rate_limit_config() == (5, 3600)

def test_per_ip_false_neither_checks_nor_records_the_host_bucket():
    for i in range(30):
        auth_utils.record_failed_login(f"user{i}", "1.2.3.4", now=T0 + i, per_ip=False)
    assert not auth_utils.login_is_rate_limited("someone-else", "1.2.3.4", now=T0 + 30)

@pytest.fixture
def root_login(monkeypatch):
    user = {"id": "u1", "username": "root", "password_hash": "h"}
    monkeypatch.setattr(main, "get_user_by_username", lambda conn, u: user if u == "root" else None)
    monkeypatch.setattr(main, "verify_password", lambda p, h: p == "right")
    monkeypatch.setattr(main, "count_users", lambda conn: 1)
    def login(username, password, host):
        return main.api_login(body=main.LoginBody(username=username, password=password),
                              request=_FakeRequest(host), conn=None)
    return login

def _spray(login, host, n=30):
    for i in range(n):
        with pytest.raises(HTTPException):
            login(f"user{i}", "x", host)

def test_without_a_trusted_proxy_the_per_ip_limit_is_off_for_the_proxy_address(root_login, monkeypatch):
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)       # mode 1: not configured yet
    _spray(root_login, "10.0.1.2")                                  # Traefik's address, shared by everyone
    assert root_login("root", "right", "10.0.1.2")["access_token"]  # no global lockout
    for _ in range(10):
        with pytest.raises(HTTPException):
            root_login("root", "wrong", "10.0.1.2")
    with pytest.raises(HTTPException) as e:                         # the per-(username, ip) limit still holds
        root_login("root", "right", "10.0.1.2")
    assert e.value.status_code == 429

def test_without_a_trusted_proxy_a_loopback_peer_still_gets_the_per_ip_limit(root_login, monkeypatch):
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    _spray(root_login, "127.0.0.1")
    with pytest.raises(HTTPException) as e:
        root_login("root", "right", "127.0.0.1")
    assert e.value.status_code == 429

def test_with_a_trusted_proxy_the_per_ip_limit_keys_on_the_real_client(root_login, monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.0.0/8")         # mode 2: uvicorn now hands the route the real client
    _spray(root_login, "203.0.113.5")
    with pytest.raises(HTTPException) as e:
        root_login("root", "right", "203.0.113.5")
    assert e.value.status_code == 429
    assert root_login("root", "right", "198.51.100.7")["access_token"]

def test_signup_route_limits_attempts_per_ip(monkeypatch):
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)      # the signup limit applies in both modes
    monkeypatch.setenv("SECRET_AUTH_KEY", "right-key")
    monkeypatch.setattr(main, "create_user", lambda conn, u, p: {"id": "n1", "username": u})
    body = lambda key: main.SignupBody(username="new", password="long-enough", secret_auth_key=key)
    for _ in range(5):
        with pytest.raises(HTTPException) as e:
            main.api_signup(body=body("wrong"), request=_FakeRequest("1.2.3.4"), conn=None)
        assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        main.api_signup(body=body("right-key"), request=_FakeRequest("1.2.3.4"), conn=None)
    assert e.value.status_code == 429 and e.value.detail == "Too many signup attempts. Try again later."
    assert main.api_signup(body=body("right-key"), request=_FakeRequest("9.9.9.9"), conn=None)["access_token"]
```

- [ ] **Step 3: Run the tests to confirm they fail**

Run: `python -m pytest -q -p no:cacheprovider backend/tests/test_login_signup_limits.py`
Expected: FAIL with `AttributeError: login_ip_rate_limit_max` (and similar), and `api_signup() got an unexpected keyword argument 'request'`.

- [ ] **Step 4: Implement**
  - `auth_utils` as in Interfaces.
  - `api_login` uses `host = _client_host(request)` and passes `per_ip=_per_ip_limit_applies(host)` to both `login_is_rate_limited` and `record_failed_login`; otherwise unchanged. `clear_login_attempts` is unchanged.
  - `api_signup(body: SignupBody, request: Request = None, conn=Depends(conn_dependency))`:
    1. `ip = _client_host(request)`.
    2. If `signup_is_rate_limited(ip)`, return 429 with `"Too many signup attempts. Try again later."`.
    3. `record_signup_attempt(ip)`; every attempt counts.
    4. Then the existing key check and create.
  - `.env.example`: add commented `# LOGIN_RATE_LIMIT_PER_IP_MAX_ATTEMPTS=30`, `# SIGNUP_RATE_LIMIT_MAX_ATTEMPTS=5` and `# SIGNUP_RATE_LIMIT_WINDOW_SECONDS=3600`, each with a one-line comment.

- [ ] **Step 5: Run the tests to confirm they pass, with no regression**

Run: `python -m pytest -q -p no:cacheprovider backend/tests/test_login_signup_limits.py backend/tests/test_server_config.py backend/tests/test_auth_hardening.py backend/tests/test_users_api.py backend/tests/test_api_authorization.py`
Expected: all pass.

- [ ] **Step 6: Commit** (`detect_changes`: the limiter functions, `api_login`, `api_signup`, `_client_host`, `_per_ip_limit_applies`)

```bash
git add backend/auth_utils.py backend/api/main.py backend/tests/test_login_signup_limits.py .env.example
git commit -m "fix(auth): per-IP login limit behind a configured proxy, and a signup limiter" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Chatbot-internal routes are loopback-only, and forwarded requests are never loopback

**Files:**
- Modify: `backend/api/main.py`: `_require_loopback`, `api_mcp_tools_list`, `api_mcp_tool_call`
- Modify: `backend/tests/test_api_authorization.py`: the two `/chatbot/internal/*` reason strings in `PUBLIC_ROUTES` become `"loopback-only (_require_loopback) and authenticated by the MCP session token"`
- Test: `backend/tests/test_loopback_internal_routes.py`

**Interfaces:**
- Consumes: Task 5 (`build_uvicorn_config`), for the forged-header test.
- Produces:
  - `_FORWARDING_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip")`.
  - `_require_loopback(request) -> None` raises `HTTPException(403, "loopback only")` unless `request.client.host` is in `("127.0.0.1", "::1", "localhost")` (the same set as today) and none of `_FORWARDING_HEADERS` is present.
  - Both internal routes call `_require_loopback(request)` as their first statement, before `_resolve_mcp_session`. Keep that literal call text, because `test_llm_timeout_sweep.py` greps for it.

- [ ] **Step 1: Impact analysis** on `_require_loopback`, `api_mcp_tools_list` and `api_mcp_tool_call`.
  - `_require_loopback` is called by `api_llm_output_save` (`/llm/outputs`).
  - The internal routes are called only by `backend/chatbot/intellistock_mcp_server.py` `_http_post`. It uses urllib, sends no forwarding header, and targets `INTELLISTOCK_MCP_URL`, which equals `_mcp_callback_url()`, which defaults to `http://127.0.0.1:<API_PORT>`.
  - Confirm with `grep -rn "_require_loopback\|chatbot/internal" backend --include=*.py`.

- [ ] **Step 2: Write the failing tests.** Fixture `mcp_session` monkeypatches:
  - `chatbot.claude_cli_provider.get_session_manager` to return an object whose `lookup_by_token(t)` appends `t` to a list and returns `SimpleNamespace(conversation_id="c1", user_id="u1", mcp_token="tok")` for `"tok"`;
  - `chatbot.tools.openai_tool_definitions` to return `lambda: []`.

  It returns the lookup list. `_Req(host, headers)` is a stand-in with `.client.host` (or `client=None`) and a lower-cased `.headers` dict.

```python
HDR, BODY = {"X-IntelliStock-MCP-Token": "tok"}, {"conversation_id": "c1"}

@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_without_forwarding_headers_passes(host):
    main._require_loopback(_Req(host))

@pytest.mark.parametrize("header", ["X-Forwarded-For", "Forwarded", "X-Real-IP"])
def test_any_forwarding_header_is_refused_even_from_loopback(header):
    with pytest.raises(HTTPException) as e:
        main._require_loopback(_Req("127.0.0.1", {header: "127.0.0.1"}))
    assert e.value.status_code == 403

@pytest.mark.parametrize("host", ["10.0.1.7", "testclient", "", None])
def test_non_loopback_peers_are_refused(host):
    with pytest.raises(HTTPException):
        main._require_loopback(_Req(host))

def test_tools_list_still_works_for_the_in_container_mcp_server(mcp_session):
    r = TestClient(main.app, client=("127.0.0.1", 50000)).post("/chatbot/internal/mcp-tools-list", json=BODY, headers=HDR)
    assert r.status_code == 200 and r.json() == {"tools": []}

def test_tools_list_refuses_a_forwarded_request_before_reading_the_token(mcp_session):
    r = TestClient(main.app, client=("127.0.0.1", 50000)).post(
        "/chatbot/internal/mcp-tools-list", json=BODY, headers={**HDR, "X-Forwarded-For": "203.0.113.9"})
    assert r.status_code == 403 and mcp_session == []

def test_internal_routes_refuse_a_non_loopback_peer(mcp_session, monkeypatch):
    dispatched: list = []
    monkeypatch.setattr("chatbot.mcp_bridge.dispatch_mcp_tool_call", lambda **kw: dispatched.append(kw) or {})
    c = TestClient(main.app)                                   # peer "testclient"
    assert c.post("/chatbot/internal/mcp-tools-list", json=BODY, headers=HDR).status_code == 403
    assert c.post("/chatbot/internal/mcp-tool-call", json={**BODY, "tool_name": "get_status"},
                  headers=HDR).status_code == 403
    assert mcp_session == [] and dispatched == []

def test_forged_forwarded_for_through_a_trusted_proxy_cannot_reach_internal_routes(mcp_session, monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1,10.0.0.0/8")
    cfg = build_uvicorn_config(main.app, host="127.0.0.1", port=8011, proxy_headers=True); cfg.load()
    r = TestClient(cfg.loaded_app, client=("10.0.1.7", 50000)).post(
        "/chatbot/internal/mcp-tools-list", json=BODY, headers={**HDR, "X-Forwarded-For": "127.0.0.1"})
    assert r.status_code == 403 and mcp_session == []          # uvicorn rewrote client to 127.0.0.1; the header check refuses

def test_llm_outputs_still_accepts_in_container_posts(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "_LLM_OUTPUT_LOG_DIR", str(tmp_path))
    c = TestClient(main.app, client=("127.0.0.1", 50000))
    assert c.post("/llm/outputs", json={"raw_output": "x"}).status_code == 200
    assert c.post("/llm/outputs", json={"raw_output": "x"}, headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403

def test_the_chatbot_mcp_server_is_pointed_at_loopback(monkeypatch):   # pins the §7.2 PR's file
    from chatbot.claude_cli_provider import _mcp_callback_url
    monkeypatch.delenv("CLAUDE_CLI_MCP_CALLBACK_URL", raising=False)
    monkeypatch.setenv("API_PORT", "8011")
    assert _mcp_callback_url() == "http://127.0.0.1:8011"
```

- [ ] **Step 3: Run the tests to confirm they fail**

Run: `python -m pytest -q -p no:cacheprovider backend/tests/test_loopback_internal_routes.py`
Expected: the forwarding-header, non-loopback-route and forged-header tests FAIL. The loopback-pass, `/llm/outputs` and callback-URL tests already pass.

- [ ] **Step 4: Implement** as in Interfaces, then update the two `PUBLIC_ROUTES` reason strings.

- [ ] **Step 5: Run the tests to confirm they pass, with no regression**

Run: `python -m pytest -q -p no:cacheprovider backend/tests/test_loopback_internal_routes.py backend/tests/test_api_authorization.py backend/tests/test_llm_timeout_sweep.py`
Expected: all pass.

- [ ] **Step 6: Commit** (`detect_changes`: `_require_loopback`, `api_mcp_tools_list`, `api_mcp_tool_call`)

```bash
git add backend/api/main.py backend/tests/test_loopback_internal_routes.py backend/tests/test_api_authorization.py
git commit -m "fix(api): chatbot internal routes are loopback-only; forwarded requests never count as loopback" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Branch verification and PR hand-off

**Files:** none new.

- [ ] **Step 1: Full suite**

Run: `python -m pytest -q -p no:cacheprovider backend/tests`
Expected: no failure beyond the Task 1 baseline.

- [ ] **Step 2: Python 3.11 syntax check**

Run: `~/.local/bin/python3.11 -m compileall -q backend/llm_url_policy.py backend/api/server_config.py backend/api/main.py backend/auth_utils.py backend/ollama_client.py backend/llm_utils.py`
Expected: exit 0, no output.

- [ ] **Step 3: Scope check.** Run `mcp__gitnexus__detect_changes({scope: "compare", base_ref: "origin/main", worktree: "<WT>", repo: "IntelliStock"})` and `git diff --stat origin/main`. Expected: only the File map files. Changed symbols are limited to the ones named in Tasks 2–7 plus the new modules.

- [ ] **Step 4: Hand off** via superpowers:finishing-a-development-branch (push and open a PR; **never merge**, because the operator merges outside market hours). The PR body carries the "Behaviour changes" list, the "Decisions" list and the deploy notes below, and ends with the attribution line from the session's system reminder.

**Deploy notes (PR body; the operator runs these, read-only except the env edit):**
1. Find Traefik's address on server7. Run `docker inspect dokploy-traefik --format '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}'` and `docker network inspect dokploy-network --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'`. Set `FORWARDED_ALLOW_IPS` in the stack env to Traefik's IP if it is stable, otherwise to the subnet.
2. Confirm `docker exec intellistock-api printenv CLAUDE_CLI_MCP_CALLBACK_URL` is empty or loopback.
3. If a custom LLM gateway must be testable with a saved or server key, add its host to `LLM_BASE_URL_ALLOWLIST`.
4. Merge outside market hours, because the merge redeploys alpaca-main.
5. After deploy:
   - the API log shows `Proxy headers: trusting X-Forwarded-For from …` and no FORWARDED_ALLOW_IPS warning;
   - web and iOS login work;
   - `curl -s -o /dev/null -w '%{http_code}' -X POST https://<api-host>/chatbot/internal/mcp-tools-list -H 'Content-Type: application/json' -d '{"conversation_id":"x"}'` prints `403`;
   - an in-app chatbot turn on a claude-cli model still lists and calls tools.
