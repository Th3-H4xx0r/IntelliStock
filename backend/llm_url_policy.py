"""Where a saved or server LLM API key may be sent.

POST /llm/test and /ollama/list-models let a caller supply a base URL. A key
the caller typed into the same request may go anywhere: it is theirs. A key
the server supplies -- one saved on a Models row, or one read from the
environment -- may only go to a host this module allows, so a caller cannot
point the server's own credentials at a URL they control.

A host is allowed for a provider when it is that provider's official HTTPS
host, the code's own built-in default, the host of that provider's
``*_BASE_URL`` / ``AZURE_OPENAI_ENDPOINT`` environment variable, listed in
``LLM_BASE_URL_ALLOWLIST``, or (for a saved key only) the host saved on the
same Models row. Host comparison ignores the port. An official host (any
provider's) is only ever allowed over https; every other allowed host may use
http as well.
"""

import ipaddress
import os
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

# The Models-row field, and the LlmConfigTestBody field, that holds each
# provider's base URL. nvidia is absent because _build_llm_test_provider_config
# hard-codes its URL; bedrock uses a region, not a URL.
ROW_BASE_URL_FIELD: dict[str, str] = {
    "openai": "openai_base_url",
    "azure": "azure_openai_endpoint",
    "ollama": "ollama_base_url",
    "openrouter": "openrouter_base_url",
}

# HTTPS only. Azure has none: every tenant has its own resource host.
OFFICIAL_HTTPS_HOSTS: dict[str, set[str]] = {
    "openai": {"api.openai.com"},
    "openrouter": {"openrouter.ai"},
    "ollama": {"ollama.com"},
    "nvidia": {"integrate.api.nvidia.com"},
}

# The code's own default (Ollama: http://localhost:11434).
BUILTIN_DEFAULT_HOSTS: dict[str, set[str]] = {
    "ollama": {"localhost", "127.0.0.1"},
}

ENV_BASE_URL_VARS: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_BASE_URL",),
    "azure": ("AZURE_OPENAI_ENDPOINT",),
    "nvidia": ("NVIDIA_BASE_URL",),
    "openrouter": ("OPENROUTER_BASE_URL",),
    "ollama": ("OLLAMA_BASE_URL",),
}

ALLOWLIST_ENV = "LLM_BASE_URL_ALLOWLIST"

_SOURCES = ("caller", "saved", "env")


class BaseUrlInvalid(ValueError):
    """The URL is not http(s) with a host and no credentials. Never echoes it."""

    def __init__(self) -> None:
        super().__init__(
            "Invalid base URL: use http:// or https:// with a host and no credentials.")


class BaseUrlNotAllowed(ValueError):
    """A saved or server key was about to go to a host the policy does not allow."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(
            "This base URL is not on the allowlist for " + provider + ", so a saved or "
            "server API key will not be sent to it. Enter the API key to test this URL, "
            "or add its host to " + ALLOWLIST_ENV + ".")


def parse_base_url(url: str) -> tuple[str, str]:
    """Return ``(scheme, host)``, lower-cased, or raise BaseUrlInvalid.

    A URL with no scheme reads as http, as the Ollama SDK reads it.
    """
    if not isinstance(url, str):
        raise BaseUrlInvalid()
    text = url.strip()
    if not text:
        raise BaseUrlInvalid()
    if "://" not in text:
        text = "http://" + text
    try:
        parts = urlsplit(text)
        _ = parts.port  # ValueError on a non-numeric or out-of-range port
    except ValueError:
        raise BaseUrlInvalid() from None
    if parts.scheme not in ("http", "https"):
        raise BaseUrlInvalid()
    if parts.username is not None or parts.password is not None:
        raise BaseUrlInvalid()
    host = parts.hostname
    if not host:
        raise BaseUrlInvalid()
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and ip.is_link_local:
        raise BaseUrlInvalid()
    return parts.scheme, host


def _host_of(url: Any) -> Optional[str]:
    """The host of a configured URL, or None when it is unset or unparseable."""
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        return parse_base_url(url)[1]
    except BaseUrlInvalid:
        return None


def _allowlisted_hosts(raw: Any, provider: str) -> set[str]:
    """Hosts LLM_BASE_URL_ALLOWLIST grants ``provider`` (scoped or bare entries)."""
    hosts: set[str] = set()
    if not isinstance(raw, str):
        return hosts
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        scope, sep, rest = entry.partition("=")
        if sep and "://" not in scope:
            if scope.strip().lower() != provider:
                continue
            entry = rest.strip()
        if "://" in entry:
            host = _host_of(entry)
        else:
            host = entry.lower() or None
        if host:
            hosts.add(host)
    return hosts


def is_allowed_base_url(provider: str, url: str, *,
                        saved_row: Optional[Mapping[str, Any]] = None,
                        env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether a saved or server key for ``provider`` may be sent to ``url``.

    An invalid URL gives False and never raises. ``env=None`` reads
    os.environ. ``saved_row`` counts only when its provider is ``provider``.
    """
    p = (provider or "").strip().lower()
    try:
        scheme, host = parse_base_url(url)
    except BaseUrlInvalid:
        return False
    if env is None:
        env = os.environ

    # An official host (whichever provider owns it) is https-only, whatever an
    # env var, saved row or allowlist entry names.
    if scheme != "https" and any(host in hosts for hosts in OFFICIAL_HTTPS_HOSTS.values()):
        return False
    if scheme == "https" and host in OFFICIAL_HTTPS_HOSTS.get(p, ()):
        return True
    if host in BUILTIN_DEFAULT_HOSTS.get(p, ()):
        return True
    for var in ENV_BASE_URL_VARS.get(p, ()):
        if host == _host_of(env.get(var)):
            return True
    if host in _allowlisted_hosts(env.get(ALLOWLIST_ENV), p):
        return True
    if saved_row is not None:
        row_provider = str(saved_row.get("provider") or "").strip().lower()
        field = ROW_BASE_URL_FIELD.get(p)
        if field and row_provider == p and host == _host_of(saved_row.get(field)):
            return True
    return False


def guard_key_for_base_url(provider: str, *, key: str, source: str, caller_base_url: str,
                           saved_row: Optional[Mapping[str, Any]],
                           env: Optional[Mapping[str, str]] = None) -> str:
    """The key to send with a caller-supplied base URL.

    ``source`` names where ``key`` came from: "caller" (typed into this
    request; goes anywhere), "saved" (a Models row; may also go to that row's
    own host) or "env" (the server environment). With no caller URL the key
    goes to the configured URL, as before. A disallowed key is withheld (``""``)
    for ollama, whose local hosts need none, and refused with BaseUrlNotAllowed
    for every other provider.
    """
    if source not in _SOURCES:
        raise ValueError("source must be one of " + ", ".join(_SOURCES))
    if source == "caller" or not (caller_base_url or "").strip() or not key:
        return key
    row = saved_row if source == "saved" else None
    if is_allowed_base_url(provider, caller_base_url, saved_row=row, env=env):
        return key
    p = (provider or "").strip().lower()
    if p == "ollama":
        return ""
    raise BaseUrlNotAllowed(p)
