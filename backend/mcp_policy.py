"""MCP route policy, gate configuration and the canonical hash.

Every route carries ``openapi_extra=mcp_policy(...)``: an x-mcp object saying what
an MCP caller may do with it (contract §1). The gate reads it back through
``validate_policy``, so a malformed annotation fails the same way at decoration
time (ValueError) and at request time (None, treated as "not available").

``McpConfig`` is the MCP_* environment, parsed once per read. An unset variable
takes its contract default (§7); a set one is taken as written, so an empty
MCP_PROTECTED_INSTANCES means "protect nothing" and an unparseable
MCP_ALLOWED_CIDR, or one of prefix length 0, disables the listener rather than
widening it.

Every hash the gate compares (body_sha256, argument digests, the schema hash) is
the sha256 hex of ``canonical_json``, the one formula the contract pins.

``resolve_money_state`` says how much real money a money route's target puts at
risk, read straight from the store with the rules of
``interactive_utils.action_learning_targets`` (plan Decision 2). It fails closed:
whatever it cannot establish, a store error included, is "unknown", and
``approval_reason`` holds every live, unknown or protected call for approval.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone

from db import store

_log = logging.getLogger(__name__)

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
    """The network named, or None (the listener stays off) when it does not parse
    or admits every address: prefix length 0 is refused, as FORWARDED_ALLOW_IPS is."""
    raw = env.get(name)
    try:
        network = ipaddress.ip_network((default if raw is None else raw).strip(), strict=False)
    except ValueError:
        return None
    return None if network.prefixlen == 0 else network


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
            allowed_cidr=_env_cidr(env, "MCP_ALLOWED_CIDR", "172.31.250.10/32"),
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


# -- money state --------------------------------------------------------------

_APPROVAL_MONEY_STATES = frozenset({"live", "unknown", "protected"})

# Combination order for the monetary part; "protected" is applied afterwards.
_MONEY_RANK = {"none": 0, "paper": 1, "unknown": 2, "live": 3}
_INSTANCES = "Instances"
_BROKERAGES = "BrokerageAccounts"
_STRATEGIES = "Strategies"


def _worst(*states: str) -> str:
    return max(states, key=_MONEY_RANK.__getitem__)


def _kalshi_env(row: object) -> str:
    env = row.get("kalshi_environment") if isinstance(row, dict) else None
    return env.strip().lower() if isinstance(env, str) else ""


def brokerage_flag_money(row: dict | None) -> str:
    """What a BrokerageAccounts row says on its own: "paper", "live" or "unknown"."""
    if not isinstance(row, dict):
        return "unknown"
    paper = row.get("alpaca_paper")
    if paper is True:
        return "paper"
    if paper is False:
        return "live"
    env = _kalshi_env(row)
    if env == "demo":
        return "paper"
    if env in ("live", "prod"):
        return "live"
    return "unknown"


def instance_money(inst: dict, brokerages: Mapping[str, dict | None]) -> str:
    """An Instances row's money: "none" when unlinked, else "paper", "live" or "unknown".

    A Kalshi account is paper or real per instance, so a Kalshi instance reads
    its brokerage's environment and its own kalshi_config, never alpaca_paper.
    Demo counts as paper: its orders execute, on fake money (plan Decision 3).
    On a live account only a stored paper_mode of exactly True is paper:
    PATCH .../kalshi/config re-derives live_enabled = live and not paper on every
    call, so a body without paper_mode sent to a bot stored paper_mode false
    turns real orders on, whatever its stored live_enabled says.
    """
    brokerage_id = inst.get("brokerage_id")
    if not brokerage_id:
        return "none"
    row = brokerages.get(str(brokerage_id))
    if str(inst.get("kind") or "") != "kalshi":
        return brokerage_flag_money(row)
    env = _kalshi_env(row)
    if env == "demo":
        return "paper"
    if env in ("live", "prod"):
        cfg = inst.get("kalshi_config") or {}
        return "paper" if cfg.get("paper_mode") is True else "live"
    return "unknown"


def _target_id(value: object) -> str | None:
    """A target id as text, or None when it is absent; ValueError on any other type."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("a target id must be a string or an integer")
    return str(value)


def _int_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _account_number(row: object) -> str:
    number = row.get("alpaca_account_number") if isinstance(row, dict) else None
    return str(number or "").strip().upper()


def _resolve_money_state(policy: dict, path_params: Mapping[str, str], body: object,
                         cfg: McpConfig) -> str:
    (kind, field_name), = policy["target"].items()
    body_map = body if isinstance(body, dict) else {}
    value = _target_id(path_params.get(field_name) or body_map.get(field_name))
    instances = store.run(_INSTANCES)
    brokerages: dict[str, dict | None] = {}
    touched_instances: set[str] = set()
    touched_brokerages: set[str] = set()

    def brokerage_row(brokerage_id: str) -> dict | None:
        if brokerage_id not in brokerages:
            brokerages[brokerage_id] = store.get(_BROKERAGES, brokerage_id)
        touched_brokerages.add(brokerage_id)
        return brokerages[brokerage_id]

    def money_of(inst: dict) -> str:
        touched_instances.add(str(inst.get("id")))
        if inst.get("brokerage_id"):
            brokerage_row(str(inst["brokerage_id"]))
        return instance_money(inst, brokerages)

    def brokerage_money(brokerage_id: str) -> str:
        # An account is as risky as its own flag and every instance trading on it.
        flag = brokerage_flag_money(brokerage_row(brokerage_id))
        users = [i for i in instances if str(i.get("brokerage_id") or "") == brokerage_id]
        return _worst(flag, *(money_of(i) for i in users))

    if value is None:
        base = "none" if kind == "brokerage" else "unknown"
    elif kind == "instance":
        row = next((i for i in instances if str(i.get("id")) == value), None)
        base = "unknown" if row is None else money_of(row)
    elif kind == "brokerage":
        base = brokerage_money(value)
    elif kind == "strategy":
        # Strategies is int-keyed, and the route parses the path as an int: "0200" is 200.
        strategy_id = _int_id(value)
        if strategy_id is None or store.get(_STRATEGIES, strategy_id) is None:
            base = "unknown"
        else:
            users = [i for i in instances if _int_id(i.get("strategy_id")) == strategy_id]
            base = _worst("none", *(money_of(i) for i in users))
    else:
        raise ValueError(f"unknown target kind {kind!r}")

    # Escalators (plan Decision 4): repointing at another account, or a Kalshi
    # paper_mode a pydantic Optional[bool] reads as False ("false" and 0 included).
    escalating = _target_id(body_map.get("brokerage_id"))
    if escalating is not None and not (kind == "brokerage" and escalating == value):
        base = _worst(base, brokerage_money(escalating))
    paper_mode = body_map.get("paper_mode")
    if paper_mode is not None and paper_mode is not True:
        base = _worst(base, "live")

    if base in ("live", "unknown"):
        return base
    protected = (not touched_instances.isdisjoint(cfg.protected_instances)
                 or any(_account_number(brokerages.get(b)) in cfg.protected_accounts
                        for b in touched_brokerages))
    return "protected" if protected else base


def resolve_money_state(policy: dict, path_params: Mapping[str, str], body: object,
                        cfg: McpConfig) -> str:
    """The money state of a money route's target: "live", "unknown", "protected",
    "paper" or "none".

    The target is the policy's one target field, from the path or else the body.
    live > unknown > paper > none; "protected" (a touched instance or account the
    operator listed) replaces paper or none only. Any failure is "unknown".
    """
    try:
        return _resolve_money_state(policy, path_params, body, cfg)
    except Exception as exc:
        # The type only: a message can quote request values.
        _log.warning("mcp: money-state resolution failed (%s); treating it as unknown",
                     type(exc).__name__)
        return "unknown"


def approval_reason(policy: dict, money_state: str | None) -> str | None:
    """Why a call needs approval ("irreversible", "live", "unknown" or "protected"), or None.

    A money call whose state is neither paper nor none (a missing or unrecognized
    one included) is held as "unknown", never let through.
    """
    cls = policy.get("class")
    if cls == "irreversible":
        return "irreversible"
    if cls != "money" or money_state in ("paper", "none"):
        return None
    return money_state if money_state in _APPROVAL_MONEY_STATES else "unknown"
