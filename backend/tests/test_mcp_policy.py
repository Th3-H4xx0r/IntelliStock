"""x-mcp route policy helper, MCP config parsing and canonical hashing (mcp_policy).

The contract (docs/superpowers/specs/2026-11-19-intellistock-mcp-contract.md) pins
the x-mcp object, the MCP_* defaults and the one hash formula every MCP check uses.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from mcp_policy import (  # noqa: E402
    McpConfig,
    canonical_request_sha256,
    iso_utc,
    mcp_policy,
    parse_iso_utc,
    validate_policy,
)


def test_mcp_policy_builds_openapi_extra():
    assert mcp_policy("money", tool="live_command", target={"instance": "instance_id"}) == {
        "x-mcp": {"class": "money", "tool": "live_command", "target": {"instance": "instance_id"}}}
    assert mcp_policy("read") == {"x-mcp": {"class": "read"}}
    assert mcp_policy("write", cost=True) == {"x-mcp": {"class": "write", "cost": True}}


@pytest.mark.parametrize("args,kwargs", [
    (("delete",), {}),
    (("money",), {}),                                              # money needs a target
    (("money",), {"target": {"instance": "a", "brokerage": "b"}}),
    (("money",), {"target": {"model": "model_id"}}),
    (("read",), {"target": {"instance": "instance_id"}}),          # target only on money
    (("read",), {"tool": "Bad-Name"}),
    (("read",), {"tool": "x" * 65}),
    (("read",), {"cost": True}),
    (("never",), {"cost": True}),
])
def test_mcp_policy_rejects_invalid(args, kwargs):
    with pytest.raises(ValueError):
        mcp_policy(*args, **kwargs)


def test_validate_policy():
    assert validate_policy(None) is None
    assert validate_policy({"class": "nope"}) is None
    assert validate_policy({"class": "read", "extra": 1}) is None
    assert validate_policy({"class": "write", "cost": False}) is None
    assert validate_policy({"class": "read"}) == {"class": "read"}


def test_config_defaults():
    cfg = McpConfig.from_env({})
    assert cfg.enabled is False and cfg.service_token is None and cfg.port == 8012
    assert str(cfg.allowed_cidr) == "172.31.250.0/24"
    assert cfg.protected_instances == frozenset({"f14-paper", "swing-paper"})
    assert cfg.protected_accounts == frozenset({"PA37JPXGU196"})
    assert (cfg.approval_ttl_s, cfg.cap_cost_per_day, cfg.cap_approvals_per_day, cfg.cap_calls_per_min,
            cfg.cap_writes_per_min, cfg.cap_concurrent_backtests) == (900, 20, 50, 60, 10, 1)
    assert (cfg.max_body_bytes, cfg.approval_exec_stale_s) == (1048576, 600)
    assert cfg.usable is False


@pytest.mark.parametrize("raw,expected", [("true", True), ("1", True), ("YES", True),
                                          ("false", False), ("", False), ("on", False)])
def test_config_enabled_parsing(raw, expected):
    assert McpConfig.from_env({"MCP_ENABLED": raw}).enabled is expected


def test_short_token_is_unset():
    assert McpConfig.from_env({"MCP_ENABLED": "true", "MCP_SERVICE_TOKEN": "x" * 31}).usable is False
    assert McpConfig.from_env({"MCP_ENABLED": "true", "MCP_SERVICE_TOKEN": "x" * 32}).usable is True


def test_bad_cidr_disables():
    env = {"MCP_ENABLED": "true", "MCP_SERVICE_TOKEN": "x" * 32, "MCP_ALLOWED_CIDR": "nonsense"}
    assert McpConfig.from_env(env).usable is False


def test_bad_ints_fall_back_and_zero_is_allowed():
    assert McpConfig.from_env({"MCP_CAP_COST_PER_DAY": "abc"}).cap_cost_per_day == 20
    assert McpConfig.from_env({"MCP_CAP_COST_PER_DAY": "-1"}).cap_cost_per_day == 20
    assert McpConfig.from_env({"MCP_CAP_COST_PER_DAY": "0"}).cap_cost_per_day == 0


def test_protected_accounts_are_normalised():
    assert McpConfig.from_env({"MCP_PROTECTED_ACCOUNTS": " pa37jpxgu196 , PA3X ,"}).protected_accounts == \
        frozenset({"PA37JPXGU196", "PA3X"})


def test_canonical_request_sha256_follows_the_contract():
    body = {"b": "é", "a": 1}
    expected = hashlib.sha256(json.dumps(
        {"method": "POST", "path": "/x", "query": {"q": "1"}, "body": body},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    assert canonical_request_sha256("post", "/x", {"q": "1"}, body) == expected


def test_iso_utc_round_trip():
    dt = datetime(2026, 11, 19, 7, 0, tzinfo=timezone.utc)
    assert iso_utc(dt) == "2026-11-19T07:00:00.000000Z"
    assert parse_iso_utc(iso_utc(dt)) == dt
