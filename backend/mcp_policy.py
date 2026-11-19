"""MCP route policy, gate configuration and the canonical hash.

Every route carries ``openapi_extra=mcp_policy(...)``: an x-mcp object saying what
an MCP caller may do with it (contract §1). The gate reads it back through
``validate_policy``, so a malformed annotation fails the same way at decoration
time (ValueError) and at request time (None, treated as "not available").

``McpConfig`` is the MCP_* environment, parsed once per read. An unset variable
takes its contract default (§7); a set one is taken as written, so an empty
MCP_PROTECTED_INSTANCES means "protect nothing" and an unparseable
MCP_ALLOWED_CIDR disables the listener rather than widening it.

Every hash the gate compares (body_sha256, argument digests, the schema hash) is
the sha256 hex of ``canonical_json``, the one formula the contract pins.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone

POLICY_CLASSES = ("read", "write", "money", "irreversible", "never")
NON_READ_CLASSES = frozenset({"write", "money", "irreversible"})
TARGET_KINDS = ("instance", "brokerage", "strategy")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_POLICY_KEYS = frozenset({"class", "tool", "target", "cost"})
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_MIN_TOKEN_BYTES = 32
_TRUE_WORDS = frozenset({"1", "true", "yes"})


def _is_name(value: object) -> bool:
    # fullmatch: NAME_RE's `$` would also accept a trailing newline.
    return isinstance(value, str) and NAME_RE.fullmatch(value) is not None


def validate_policy(raw: object) -> dict | None:
    """Return a normalized copy of an x-mcp object, or None when it breaks a rule."""
    if not isinstance(raw, dict) or not set(raw) <= _POLICY_KEYS:
        return None
    cls = raw.get("class")
    if not isinstance(cls, str) or cls not in POLICY_CLASSES:
        return None
    out: dict = {"class": cls}
    if "tool" in raw:
        if not _is_name(raw["tool"]):
            return None
        out["tool"] = raw["tool"]
    if ("target" in raw) != (cls == "money"):
        return None
    if "target" in raw:
        target = raw["target"]
        if not isinstance(target, dict) or len(target) != 1:
            return None
        (kind, field), = target.items()
        if kind not in TARGET_KINDS or not _is_name(field):
            return None
        out["target"] = {kind: field}
    if "cost" in raw:
        if raw["cost"] is not True or cls not in NON_READ_CLASSES:
            return None
        out["cost"] = True
    return out


def mcp_policy(cls: str, *, tool: str | None = None, target: dict | None = None,
               cost: bool = False) -> dict:
    """Build ``{"x-mcp": {...}}`` for a route's openapi_extra; ValueError if invalid."""
    raw: dict = {"class": cls}
    if tool is not None:
        raw["tool"] = tool
    if target is not None:
        raw["target"] = target
    if cost:
        raw["cost"] = cost
    policy = validate_policy(raw)
    if policy is None:
        raise ValueError(f"invalid x-mcp: {raw!r}")
    return {"x-mcp": policy}


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    """An integer >= 0 from the environment; anything else is the default."""
    raw = env.get(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value >= 0 else default


def _env_set(env: Mapping[str, str], name: str, default: str, *, upper: bool = False) -> frozenset[str]:
    raw = env.get(name)
    if raw is None:
        raw = default
    items = (part.strip() for part in raw.split(","))
    return frozenset(item.upper() if upper else item for item in items if item)


def _env_cidr(env: Mapping[str, str], name: str,
              default: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    raw = env.get(name)
    try:
        return ipaddress.ip_network((default if raw is None else raw).strip(), strict=False)
    except ValueError:
        return None


@dataclass(frozen=True)
class McpConfig:
    enabled: bool
    service_token: str | None = field(repr=False)  # a secret: never in a repr or a log line
    port: int
    allowed_cidr: ipaddress.IPv4Network | ipaddress.IPv6Network | None
    protected_instances: frozenset[str]
    protected_accounts: frozenset[str]
    approval_ttl_s: int
    cap_cost_per_day: int
    cap_approvals_per_day: int
    cap_calls_per_min: int
    cap_writes_per_min: int
    cap_concurrent_backtests: int
    max_body_bytes: int
    approval_exec_stale_s: int

    @property
    def usable(self) -> bool:
        """True only when the switch is on and a token and a CIDR both parsed."""
        return self.enabled and self.service_token is not None and self.allowed_cidr is not None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> McpConfig:
        env = os.environ if environ is None else environ
        token = (env.get("MCP_SERVICE_TOKEN") or "").strip()
        if len(token.encode("utf-8")) < _MIN_TOKEN_BYTES:
            token = ""
        return cls(
            enabled=(env.get("MCP_ENABLED") or "").strip().lower() in _TRUE_WORDS,
            service_token=token or None,
            port=_env_int(env, "MCP_API_PORT", 8012),
            allowed_cidr=_env_cidr(env, "MCP_ALLOWED_CIDR", "172.31.250.0/24"),
            protected_instances=_env_set(env, "MCP_PROTECTED_INSTANCES", "f14-paper,swing-paper"),
            protected_accounts=_env_set(env, "MCP_PROTECTED_ACCOUNTS", "PA37JPXGU196", upper=True),
            approval_ttl_s=_env_int(env, "MCP_APPROVAL_TTL_S", 900),
            cap_cost_per_day=_env_int(env, "MCP_CAP_COST_PER_DAY", 20),
            cap_approvals_per_day=_env_int(env, "MCP_CAP_APPROVALS_PER_DAY", 50),
            cap_calls_per_min=_env_int(env, "MCP_CAP_CALLS_PER_MIN", 60),
            cap_writes_per_min=_env_int(env, "MCP_CAP_WRITES_PER_MIN", 10),
            cap_concurrent_backtests=_env_int(env, "MCP_CAP_CONCURRENT_BACKTESTS", 1),
            max_body_bytes=_env_int(env, "MCP_MAX_BODY_BYTES", 1048576),
            approval_exec_stale_s=_env_int(env, "MCP_APPROVAL_EXEC_STALE_S", 600),
        )


def canonical_request(method: str, path: str, query: Mapping[str, object], body: object) -> dict:
    return {"method": method.upper(), "path": path, "query": dict(query), "body": body}


def canonical_json(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_request_sha256(method: str, path: str, query: Mapping[str, object], body: object) -> str:
    return hashlib.sha256(canonical_json(canonical_request(method, path, query, body))).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime) -> str:
    """Fixed-width UTC stamp, so bytewise (COLLATE "C") order is chronological."""
    if dt.tzinfo is None:
        raise ValueError("iso_utc needs a timezone-aware datetime")
    return dt.astimezone(timezone.utc).strftime(_ISO_FORMAT)


def parse_iso_utc(s: str) -> datetime:
    """Inverse of iso_utc; ValueError on any other shape."""
    return datetime.strptime(s, _ISO_FORMAT).replace(tzinfo=timezone.utc)
