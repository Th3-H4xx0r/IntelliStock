"""Settings read from the environment, validated in one pass, and the service's fixed limits.

load_settings() checks every variable and reports every problem at once, so the
operator fixes the whole .env in one go. Messages name the variable and the rule
it broke; they never repeat a value, because several of these values are secrets
and a URL can carry credentials.
"""
import base64
import binascii
import ipaddress
import math
import os
import re
from dataclasses import dataclass, field
from typing import Mapping, Optional
from urllib.parse import urlsplit

ACCESS_TOKEN_TTL_S = 900
REFRESH_ABS_LIFETIME_S = 2_592_000
REFRESH_REUSE_GRACE_S = 120
AUTH_CODE_TTL_S = 120
LOGIN_TXN_TTL_S = 600
ENROLL_TTL_S = 600
RESULT_CAP_BYTES = 102_400
ERROR_DETAIL_MAX_CHARS = 500
SCOPE = "mcp"
CLIENT_HEADER_VALUE = "jarvis"
API_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_DIRECT_TOOLS = 40
PORT = 8090

DB_FILENAME = "mcp_auth.sqlite3"
MASTER_KEY_BYTES = 32
SERVICE_TOKEN_MIN_BYTES = 32
LOG_LEVELS = ("critical", "error", "warning", "info", "debug", "trace")  # uvicorn's names

_DEFAULTS = {
    "MCP_API_BASE": "http://api:8012",
    "MCP_DATA_DIR": "/data",
    "MCP_JARVIS_CLIENT_ID": "jarvis",
    "MCP_LOG_LEVEL": "info",
    "MCP_API_TIMEOUT_S": "60",
    "MCP_SCHEMA_POLL_S": "60",
}

# Jarvis's loopback callback (RFC 8252 section 7.3): the IPv4 literal, a fixed port, one path.
_REDIRECT_URI = re.compile(r"http://127\.0\.0\.1:([1-9]\d{1,4})/callback")
_LABEL = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
_HOSTNAME = re.compile(_LABEL + r"(?:\." + _LABEL + r")*")
# A Host header value as the server compares it: lowercase host or [IPv6], optional port.
_HOST_PORT = re.compile(r"(?P<host>" + _HOSTNAME.pattern + r"|\[[0-9a-f:.]+\])(?::(?P<port>[1-9][0-9]{0,4}))?")
_CLIENT_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_VISIBLE_ASCII = re.compile(r"[\x21-\x7e]+")


@dataclass(frozen=True)
class Settings:
    public_base: str
    rp_id: str
    api_base: str
    service_token: str = field(repr=False)
    data_dir: str
    jarvis_client_id: str
    jarvis_redirect_uri: str
    master_key: bytes = field(repr=False)
    extra_allowed_hosts: tuple[str, ...]
    log_level: str
    api_timeout_s: float
    schema_poll_s: float

    @property
    def issuer(self) -> str:
        """Byte-identical to MCP_PUBLIC_BASE: the SDK client compares the issuer exactly."""
        return self.public_base

    @property
    def resource_url(self) -> str:
        """The canonical /mcp URL: the access token audience and the required RFC 8707 resource."""
        return self.public_base + "/mcp"

    @property
    def public_origin(self) -> str:
        parts = urlsplit(self.public_base)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        """Host header values the server answers: the public base's netloc, then the extras."""
        return tuple(dict.fromkeys((urlsplit(self.public_base).netloc,) + self.extra_allowed_hosts))

    @property
    def redirect_origin(self) -> str:
        parts = urlsplit(self.jarvis_redirect_uri)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, DB_FILENAME)


class SettingsError(ValueError):
    """Every problem load_settings found, one message per entry in errors."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("invalid MCP settings: " + "; ".join(self.errors))


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _is_loopback_ip(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _valid_host_port(value: str) -> bool:
    match = _HOST_PORT.fullmatch(value)
    return match is not None and (match.group("port") is None or int(match.group("port")) <= 65535)


def _public_base(value: str) -> Optional[str]:
    """The hostname of a valid public base URL, or None."""
    parts = urlsplit(value)
    if not _valid_host_port(parts.netloc) or value != f"{parts.scheme}://{parts.netloc}":
        return None  # catches path, query, fragment, userinfo, uppercase scheme or host, empty port
    host = parts.hostname or ""
    if parts.scheme == "https" or (parts.scheme == "http" and _is_loopback_ip(host)):
        return host
    return None


def _api_base(value: str) -> Optional[str]:
    """The normalised API base (no trailing slash), or None."""
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or "@" in parts.netloc:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment or value.endswith(("?", "#")):
        return None
    try:
        parts.port
    except ValueError:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _decode_key(value: str) -> Optional[bytes]:
    """Standard or URL-safe base64, padding optional; None when it is neither."""
    text = value
    if "-" in text or "_" in text:
        if "+" in text or "/" in text:
            return None
        text = text.translate(str.maketrans("-_", "+/"))
    text += "=" * (-len(text) % 4)
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None


def _positive_seconds(raw: str) -> Optional[float]:
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None


def load_settings(env: Mapping[str, str]) -> Settings:
    """Validate every MCP_* variable; raise SettingsError listing all problems."""
    errors: list[str] = []

    def get(name: str) -> Optional[str]:
        raw = env.get(name)
        if raw is None or not raw.strip():
            raw = _DEFAULTS.get(name)
        return None if raw is None else raw.strip()

    def require(name: str) -> Optional[str]:
        value = get(name)
        if value is None:
            errors.append(f"{name} is required")
        return value

    public_base = require("MCP_PUBLIC_BASE")
    host = None
    if public_base is not None:
        host = _public_base(public_base)
        if host is None:
            errors.append("MCP_PUBLIC_BASE must be https://host[:port] in lowercase, with no path, "
                          "trailing slash, query, fragment or credentials (http only for a loopback IP)")

    rp_id = require("MCP_RP_ID")
    if rp_id is not None:
        if not (_HOSTNAME.fullmatch(rp_id) or _is_ip(rp_id)):
            errors.append("MCP_RP_ID must be a bare lowercase host name (no scheme, port or path)")
        elif host is not None and rp_id != host and (_is_ip(host) or not host.endswith("." + rp_id)):
            errors.append("MCP_RP_ID must equal the host of MCP_PUBLIC_BASE or a parent domain of it")

    api_base = None
    raw_api_base = get("MCP_API_BASE")
    if raw_api_base is not None:
        api_base = _api_base(raw_api_base)
        if api_base is None:
            errors.append("MCP_API_BASE must be http(s)://host[:port] with no path, query or credentials")

    # Not stripped: the API compares this value byte for byte.
    service_token = env.get("MCP_SERVICE_TOKEN")
    if service_token is None or not service_token.strip():
        errors.append("MCP_SERVICE_TOKEN is required")
        service_token = None
    elif not _VISIBLE_ASCII.fullmatch(service_token):
        errors.append("MCP_SERVICE_TOKEN must be visible ASCII with no whitespace (it is sent in a header)")
    elif len(service_token.encode("ascii")) < SERVICE_TOKEN_MIN_BYTES:
        errors.append(f"MCP_SERVICE_TOKEN must be at least {SERVICE_TOKEN_MIN_BYTES} bytes")

    data_dir = get("MCP_DATA_DIR")
    if data_dir is not None and not os.path.isabs(data_dir):
        errors.append("MCP_DATA_DIR must be an absolute path")

    client_id = get("MCP_JARVIS_CLIENT_ID")
    if client_id is not None and not _CLIENT_ID.fullmatch(client_id):
        errors.append("MCP_JARVIS_CLIENT_ID must be 1-64 characters from A-Z a-z 0-9 . _ -")

    redirect_uri = require("MCP_JARVIS_REDIRECT_URI")
    if redirect_uri is not None:
        match = _REDIRECT_URI.fullmatch(redirect_uri)
        if match is None or int(match.group(1)) > 65535:
            errors.append("MCP_JARVIS_REDIRECT_URI must be exactly http://127.0.0.1:<port>/callback")

    master_key = None
    raw_key = require("MCP_TOTP_ENC_KEY")
    if raw_key is not None:
        master_key = _decode_key(raw_key)
        if master_key is None:
            errors.append("MCP_TOTP_ENC_KEY must be base64 (standard or URL-safe)")
        elif len(master_key) != MASTER_KEY_BYTES:
            errors.append(f"MCP_TOTP_ENC_KEY must decode to exactly {MASTER_KEY_BYTES} bytes "
                          "(generate with: openssl rand -base64 32)")

    extra_hosts = tuple(item.strip() for item in (get("MCP_EXTRA_ALLOWED_HOSTS") or "").split(",")
                        if item.strip())
    bad_hosts = [str(index) for index, item in enumerate(extra_hosts, 1) if not _valid_host_port(item)]
    if bad_hosts:
        errors.append("MCP_EXTRA_ALLOWED_HOSTS entries must be lowercase host[:port] values; "
                      f"rejected entry number {', '.join(bad_hosts)}")

    log_level = (get("MCP_LOG_LEVEL") or "").lower()
    if log_level not in LOG_LEVELS:
        errors.append(f"MCP_LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}")

    seconds = {}
    for name in ("MCP_API_TIMEOUT_S", "MCP_SCHEMA_POLL_S"):
        seconds[name] = _positive_seconds(get(name) or "")
        if seconds[name] is None:
            errors.append(f"{name} must be a positive number of seconds")

    if errors:
        raise SettingsError(errors)
    return Settings(
        public_base=public_base,
        rp_id=rp_id,
        api_base=api_base,
        service_token=service_token,
        data_dir=data_dir,
        jarvis_client_id=client_id,
        jarvis_redirect_uri=redirect_uri,
        master_key=master_key,
        extra_allowed_hosts=extra_hosts,
        log_level=log_level,
        api_timeout_s=seconds["MCP_API_TIMEOUT_S"],
        schema_poll_s=seconds["MCP_SCHEMA_POLL_S"],
    )
