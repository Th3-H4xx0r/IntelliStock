"""Settings: every variable validated in one pass, secrets never echoed."""
import base64
import os

import pytest

from mcp_server.config import (
    ACCESS_TOKEN_TTL_S,
    API_MAX_RESPONSE_BYTES,
    AUTH_CODE_TTL_S,
    CLIENT_HEADER_VALUE,
    ENROLL_TTL_S,
    ERROR_DETAIL_MAX_CHARS,
    LOGIN_TXN_TTL_S,
    MAX_DIRECT_TOOLS,
    PORT,
    REFRESH_ABS_LIFETIME_S,
    REFRESH_REUSE_GRACE_S,
    RESULT_CAP_BYTES,
    SCOPE,
    SettingsError,
    load_settings,
)


def _errors(env):
    with pytest.raises(SettingsError) as info:
        load_settings(env)
    assert isinstance(info.value, ValueError)
    return info.value.errors


def _names(errors, name):
    return [message for message in errors if name in message]


def test_constants_match_the_contract():
    assert ACCESS_TOKEN_TTL_S == 900
    assert REFRESH_ABS_LIFETIME_S == 2_592_000
    assert REFRESH_REUSE_GRACE_S == 120
    assert AUTH_CODE_TTL_S == 120
    assert LOGIN_TXN_TTL_S == 600
    assert ENROLL_TTL_S == 600
    assert RESULT_CAP_BYTES == 102_400
    assert ERROR_DETAIL_MAX_CHARS == 500
    assert SCOPE == "mcp"
    assert CLIENT_HEADER_VALUE == "jarvis"
    assert API_MAX_RESPONSE_BYTES == 10 * 1024 * 1024
    assert MAX_DIRECT_TOOLS == 40
    assert PORT == 8090


def test_load_settings_valid(env):
    s = load_settings(env)
    assert s.resource_url == env["MCP_PUBLIC_BASE"] + "/mcp"
    assert s.issuer == env["MCP_PUBLIC_BASE"] == "https://s7.tail.ts.net:8443"
    assert s.public_origin == "https://s7.tail.ts.net:8443"
    assert s.rp_id == "s7.tail.ts.net"
    assert s.api_base == "http://api:8012"
    assert s.service_token == env["MCP_SERVICE_TOKEN"]
    assert s.jarvis_client_id == "jarvis"
    assert s.jarvis_redirect_uri == "http://127.0.0.1:33418/callback"
    assert s.redirect_origin == "http://127.0.0.1:33418"
    assert s.master_key == bytes(range(32))
    assert s.data_dir == env["MCP_DATA_DIR"]
    assert s.db_path == os.path.join(env["MCP_DATA_DIR"], "mcp_auth.sqlite3")
    assert s.extra_allowed_hosts == ()
    assert s.allowed_hosts == ("s7.tail.ts.net:8443",)
    assert s.log_level == "info"
    assert s.api_timeout_s == 60.0
    assert s.schema_poll_s == 60.0


def test_settings_fixture_is_loaded_from_env(settings, env):
    assert settings == load_settings(env)


def test_defaults_apply_when_unset_or_blank(env):
    for name in ("MCP_API_BASE", "MCP_DATA_DIR", "MCP_JARVIS_CLIENT_ID", "MCP_LOG_LEVEL"):
        env.pop(name, None)
    # compose passes ${VAR:-} through as an empty string: blank means unset
    env["MCP_EXTRA_ALLOWED_HOSTS"] = ""
    env["MCP_API_TIMEOUT_S"] = "  "
    s = load_settings(env)
    assert s.api_base == "http://api:8012"
    assert s.data_dir == "/data"
    assert s.db_path == "/data/mcp_auth.sqlite3"
    assert s.jarvis_client_id == "jarvis"
    assert s.log_level == "info"
    assert s.extra_allowed_hosts == ()
    assert s.api_timeout_s == 60.0
    assert s.schema_poll_s == 60.0


def test_collects_every_error():
    errors = _errors({})
    for name in ("MCP_PUBLIC_BASE", "MCP_RP_ID", "MCP_SERVICE_TOKEN", "MCP_JARVIS_REDIRECT_URI",
                 "MCP_TOTP_ENC_KEY"):
        assert _names(errors, name), name
    # the variables with defaults are not reported
    for name in ("MCP_API_BASE", "MCP_DATA_DIR", "MCP_JARVIS_CLIENT_ID", "MCP_LOG_LEVEL"):
        assert not _names(errors, name), name


def test_collects_errors_across_variables(env):
    env["MCP_PUBLIC_BASE"] = "https://h/x"
    env["MCP_SERVICE_TOKEN"] = "short"
    env["MCP_LOG_LEVEL"] = "verbose"
    errors = _errors(env)
    assert _names(errors, "MCP_PUBLIC_BASE")
    assert _names(errors, "MCP_SERVICE_TOKEN")
    assert _names(errors, "MCP_LOG_LEVEL")


@pytest.mark.parametrize("value", [
    "https://h/",
    "https://h/x",
    "http://h.ts.net",
    "https://h?x=1",
    "https://h?",
    "https://h#frag",
    "https://user:pw@h.ts.net",
    "https://h.ts.net:",
    "https://h.ts.net:0",
    "https://h.ts.net:70000",
    "https://H.ts.net",
    "HTTPS://h.ts.net",
    "ftp://h.ts.net",
    "h.ts.net",
    "https://",
])
def test_public_base_rejected(env, value):
    env["MCP_PUBLIC_BASE"] = value
    env["MCP_RP_ID"] = "h.ts.net"
    assert _names(_errors(env), "MCP_PUBLIC_BASE")


@pytest.mark.parametrize("value, rp_id", [
    ("https://h.ts.net:8443", "h.ts.net"),
    ("https://h.ts.net", "h.ts.net"),
    ("http://127.0.0.1:8090", "127.0.0.1"),
])
def test_public_base_accepted(env, value, rp_id):
    env["MCP_PUBLIC_BASE"] = value
    env["MCP_RP_ID"] = rp_id
    s = load_settings(env)
    assert s.public_base == value
    assert s.issuer == value
    assert s.resource_url == value + "/mcp"


def test_public_base_value_never_echoed(env):
    env["MCP_PUBLIC_BASE"] = "https://user:hunter2-secret@h.ts.net"
    assert "hunter2-secret" not in " ".join(_errors(env))


@pytest.mark.parametrize("rp_id, ok", [
    ("s7.tail.ts.net", True),
    ("tail.ts.net", True),
    ("evil.net", False),
    ("il.ts.net", False),
    ("s7.tail.ts.net.evil", False),
    ("S7.tail.ts.net", False),
    ("https://s7.tail.ts.net", False),
    ("s7.tail.ts.net:8443", False),
])
def test_rp_id_must_match_host(env, rp_id, ok):
    env["MCP_RP_ID"] = rp_id
    if ok:
        assert load_settings(env).rp_id == rp_id
    else:
        assert _names(_errors(env), "MCP_RP_ID")


def test_rp_id_on_ip_host_must_be_exact(env):
    env["MCP_PUBLIC_BASE"] = "http://127.0.0.1:8090"
    env["MCP_RP_ID"] = "0.0.1"
    assert _names(_errors(env), "MCP_RP_ID")


@pytest.mark.parametrize("uri", [
    "http://127.0.0.1:33418/callback",
    "http://127.0.0.1:10/callback",
    "http://127.0.0.1:65535/callback",
])
def test_redirect_uri_loopback_accepted(env, uri):
    env["MCP_JARVIS_REDIRECT_URI"] = uri
    s = load_settings(env)
    assert s.jarvis_redirect_uri == uri
    assert s.redirect_origin == uri[: -len("/callback")]


@pytest.mark.parametrize("uri", [
    "http://localhost:33418/callback",
    "https://127.0.0.1:33418/callback",
    "http://127.0.0.1:33418/cb",
    "http://127.0.0.1:0/callback",
    "http://127.0.0.1:70000/callback",
    "http://127.0.0.1:65536/callback",
    "http://127.0.0.1:7/callback",
    "http://127.0.0.1/callback",
    "http://127.0.0.1:33418/callback/",
    "http://127.0.0.1:33418/callback?x=1",
    "http://127.0.0.1:033418/callback",
    "http://127.0.0.2:33418/callback",
])
def test_redirect_uri_exact_loopback(env, uri):
    env["MCP_JARVIS_REDIRECT_URI"] = uri
    assert _names(_errors(env), "MCP_JARVIS_REDIRECT_URI")


def test_service_token_min_32_bytes(env):
    env["MCP_SERVICE_TOKEN"] = "t" * 31
    errors = _errors(env)
    assert _names(errors, "MCP_SERVICE_TOKEN")
    assert "t" * 31 not in " ".join(errors)
    env["MCP_SERVICE_TOKEN"] = "t" * 32
    assert load_settings(env).service_token == "t" * 32


@pytest.mark.parametrize("token", [
    "has whitespace " + "x" * 32,
    "x" * 32 + "\n",
    "\u00e9" * 32,
])
def test_service_token_must_be_header_safe(env, token):
    env["MCP_SERVICE_TOKEN"] = token
    errors = _errors(env)
    assert _names(errors, "MCP_SERVICE_TOKEN")
    assert token.strip() not in " ".join(errors)


def test_master_key_exactly_32_bytes(env):
    for size in (31, 33, 16, 0):
        raw = os.urandom(size)
        for encoded in (base64.b64encode(raw).decode(), base64.urlsafe_b64encode(raw).decode()):
            env["MCP_TOTP_ENC_KEY"] = encoded or "="
            errors = _errors(env)
            assert _names(errors, "MCP_TOTP_ENC_KEY"), (size, encoded)
            if encoded:
                assert encoded not in " ".join(errors)


def test_master_key_std_and_urlsafe_accepted(env):
    raw = bytes([0xFB, 0xFF, 0xBF]) * 10 + b"\xfe\xff"  # forces + / and - _ in the encodings
    assert len(raw) == 32
    std = base64.b64encode(raw).decode()
    urlsafe = base64.urlsafe_b64encode(raw).decode()
    assert "+" in std or "/" in std
    assert "-" in urlsafe or "_" in urlsafe
    for encoded in (std, urlsafe, urlsafe.rstrip("="), " " + std + "\n"):
        env["MCP_TOTP_ENC_KEY"] = encoded
        assert load_settings(env).master_key == raw, encoded


@pytest.mark.parametrize("value", [
    "not base64 at all!!",
    "AAAA" * 10 + "A",
    base64.b64encode(bytes(range(32))).decode()[:-4] + "-+==",
])
def test_master_key_must_be_base64(env, value):
    env["MCP_TOTP_ENC_KEY"] = value
    errors = _errors(env)
    assert _names(errors, "MCP_TOTP_ENC_KEY")
    assert value not in " ".join(errors)


def test_allowed_hosts(env):
    env["MCP_EXTRA_ALLOWED_HOSTS"] = " s7:8443 , 127.0.0.1:8090,, localhost "
    s = load_settings(env)
    assert s.extra_allowed_hosts == ("s7:8443", "127.0.0.1:8090", "localhost")
    assert s.allowed_hosts == ("s7.tail.ts.net:8443", "s7:8443", "127.0.0.1:8090", "localhost")


def test_allowed_hosts_without_port(env):
    env["MCP_PUBLIC_BASE"] = "https://s7.tail.ts.net"
    assert load_settings(env).allowed_hosts == ("s7.tail.ts.net",)


@pytest.mark.parametrize("entry", ["https://s7:8443", "s7/x", "user@s7", "s7:*", "s7:99999", "S7"])
def test_extra_allowed_hosts_must_be_host_port(env, entry):
    env["MCP_EXTRA_ALLOWED_HOSTS"] = "ok.example," + entry
    assert _names(_errors(env), "MCP_EXTRA_ALLOWED_HOSTS")


def test_extra_allowed_hosts_errors_give_positions_not_values(env):
    env["MCP_EXTRA_ALLOWED_HOSTS"] = "ok.example, https://u:pw-secret@h, fine:443, bad host"
    (message,) = _names(_errors(env), "MCP_EXTRA_ALLOWED_HOSTS")
    assert "2, 4" in message
    assert "pw-secret" not in message


@pytest.mark.parametrize("value", ["ftp://api:8012", "http://api:8012/x", "http://u:p@api:8012",
                                   "http://api:8012?x=1", "api:8012", "http://"])
def test_api_base_rejected(env, value):
    env["MCP_API_BASE"] = value
    assert _names(_errors(env), "MCP_API_BASE")


def test_api_base_trailing_slash_dropped(env):
    env["MCP_API_BASE"] = "http://api:8012/"
    assert load_settings(env).api_base == "http://api:8012"


def test_data_dir_must_be_absolute(env):
    env["MCP_DATA_DIR"] = "relative/data"
    assert _names(_errors(env), "MCP_DATA_DIR")


@pytest.mark.parametrize("value", ["", "has space", "x" * 65, "jar/vis"])
def test_client_id_rules(env, value):
    env["MCP_JARVIS_CLIENT_ID"] = value
    if value == "":
        assert load_settings(env).jarvis_client_id == "jarvis"
    else:
        assert _names(_errors(env), "MCP_JARVIS_CLIENT_ID")


def test_log_level(env):
    env["MCP_LOG_LEVEL"] = "DEBUG"
    assert load_settings(env).log_level == "debug"
    env["MCP_LOG_LEVEL"] = "verbose"
    assert _names(_errors(env), "MCP_LOG_LEVEL")


@pytest.mark.parametrize("name", ["MCP_API_TIMEOUT_S", "MCP_SCHEMA_POLL_S"])
def test_seconds_must_be_positive_finite(env, name):
    env[name] = "12.5"
    s = load_settings(env)
    assert (s.api_timeout_s if name == "MCP_API_TIMEOUT_S" else s.schema_poll_s) == 12.5
    for bad in ("0", "-1", "nan", "inf", "abc"):
        env[name] = bad
        assert _names(_errors(env), name), bad


def test_repr_hides_secrets(env):
    s = load_settings(env)
    text = repr(s)
    assert env["MCP_SERVICE_TOKEN"] not in text
    assert repr(s.master_key) not in text
    assert "s7.tail.ts.net" in text
