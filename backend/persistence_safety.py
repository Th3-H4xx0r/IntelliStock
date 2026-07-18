"""Secret-safe persistence boundary for BacktestResults and alpha ledgers.

``sanitize_snapshot`` strips credential material from a strategy-schema
snapshot (or any JSON-ish structure) before persistence. ``assert_secret_free``
is the write-side guard: it raises ``SecretMaterialError`` when a payload still
carries secret material, so callers fail the write closed instead of persisting
plaintext credentials (the 2026-07 BacktestResults incident).

Secret-key matching is segment-based and case-insensitive (``alpaca_key``,
``openrouter_api_key``, ``neo4j_password``, ...). Safe names are explicitly
allowlisted; ``secret_ref`` values must additionally match an approved
reference scheme (e.g. ``env:OPENROUTER_API_KEY``) so a literal secret cannot
hide under an allowlisted name. All string values — regardless of key — are
scanned with logger-style value patterns (bearer headers, AWS/OpenRouter-style
keys, URL userinfo, private-key blocks, key=value pairs).
"""
import copy
import re


class SecretMaterialError(ValueError):
    """Raised when a payload destined for persistence contains secret material."""


REDACTION_MARKER = {"redacted": True, "source": "runtime_secret"}

# Key segments (split on non-alphanumerics) that mark a field as secret-bearing.
_SECRET_KEY_SEGMENTS = frozenset({
    "key", "secret", "token", "password", "passwd", "pwd", "credential",
    "credentials", "auth", "authorization", "bearer", "apikey", "dsn",
    "userinfo", "passphrase",
})

# Exact key names (lowercased) that are safe despite containing a secret segment.
_ALLOWLISTED_KEYS = frozenset({"strategy_config_hash", "secret_ref"})

# Approved secret-reference schemes: a reference points at a secret, it is not one.
_SECRET_REF_RE = re.compile(r"^(env|vault|aws-sm|gcp-sm|keychain):[A-Za-z0-9_./-]+$")

# Logger-compatible value patterns applied to every string value.
_VALUE_PATTERNS = tuple(re.compile(p) for p in (
    r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{12,}",          # Authorization: Bearer <token>
    r"\bAKIA[0-9A-Z]{16,}\b",                             # AWS access key id
    r"\bsk-[A-Za-z0-9_\-]{16,}\b",                        # OpenAI/OpenRouter-style keys
    r"\bghp_[A-Za-z0-9]{20,}\b",                          # GitHub PAT
    r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b",                 # Slack tokens
    r"\bAIza[0-9A-Za-z_\-]{30,}\b",                       # Google API key
    r"://[^/\s:@]+:[^/\s@]+@",                            # URL userinfo (user:pass@host)
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",                # PEM private key block
    r"(?i)\b(password|passwd|pwd|secret|token|api_key|apikey)\b\s*[=:]\s*\S{6,}",
))


def _is_secret_key(key):
    # camelCase splits BEFORE lowering (audit 2026-07-18: fused names like
    # clientSecret/alpacaKey — exactly what frontend JSON produces —
    # bypassed the segment check entirely).
    decamel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key))
    lowered = decamel.lower()
    if lowered in _ALLOWLISTED_KEYS or str(key).lower() in _ALLOWLISTED_KEYS:
        return False
    segments = re.split(r"[^a-z0-9]+", lowered)
    return any(seg in _SECRET_KEY_SEGMENTS for seg in segments if seg)


def _is_approved_secret_ref(value):
    return isinstance(value, str) and bool(_SECRET_REF_RE.match(value))


def _string_has_secret_material(value):
    return any(p.search(value) for p in _VALUE_PATTERNS)


def _redact_string(value):
    for pattern in _VALUE_PATTERNS:
        value = pattern.sub("<redacted>", value)
    return value


def _is_redaction_marker(value):
    # Exact-match only: a deep-merged dict that RETAINS secret fields
    # alongside the marker keys must not pass (audit 2026-07-18).
    return value == REDACTION_MARKER


def sanitize_snapshot(value):
    """Return a deep-copied, secret-free version of ``value``.

    Secret-key values become ``REDACTION_MARKER``; every string is scrubbed
    with the value patterns; non-secret structure is deep-copied so callers
    cannot mutate the original through the sanitized result.
    """
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered == "secret_ref" and not _is_approved_secret_ref(item):
                clean[key] = dict(REDACTION_MARKER)
            elif _is_secret_key(key):
                clean[key] = dict(REDACTION_MARKER)
            else:
                clean[key] = sanitize_snapshot(item)
        return clean
    if isinstance(value, (list, tuple)):
        cleaned = [sanitize_snapshot(item) for item in value]
        return type(value)(cleaned) if isinstance(value, tuple) else cleaned
    if isinstance(value, set) or isinstance(value, frozenset):
        return [sanitize_snapshot(item) for item in sorted(value, key=repr)]
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, (bytes, bytearray)):
        return dict(REDACTION_MARKER)
    return copy.deepcopy(value)


def assert_secret_free(value, _path="$"):
    """Raise ``SecretMaterialError`` if ``value`` contains secret material.

    Accepts redaction markers under secret keys and approved ``secret_ref``
    values; anything else under a secret key, or any string matching a value
    pattern anywhere, fails closed. The offending path — never the offending
    value — is included in the error message.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{_path}.{key}"
            lowered = str(key).lower()
            if lowered == "secret_ref":
                if not _is_approved_secret_ref(item):
                    raise SecretMaterialError(
                        f"unapproved secret_ref value at {child_path}")
                continue
            if _is_secret_key(key):
                if not _is_redaction_marker(item):
                    raise SecretMaterialError(
                        f"unredacted secret-bearing field at {child_path}")
                continue
            assert_secret_free(item, child_path)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for idx, item in enumerate(value):
            assert_secret_free(item, f"{_path}[{idx}]")
        return
    if isinstance(value, str):
        if _string_has_secret_material(value):
            raise SecretMaterialError(f"secret-pattern string value at {_path}")
        return
    if isinstance(value, (bytes, bytearray)):
        raise SecretMaterialError(f"raw bytes value at {_path}")
