"""Money-state resolution for the MCP gate (plan Task 2, Decision 2).

A money route's x-mcp target names the instance, brokerage or strategy it acts
on. resolve_money_state reads that target's rows straight from the store and
answers "live", "unknown", "protected", "paper" or "none"; anything it cannot
establish is "unknown", so the gate holds the call for approval.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest

import mcp_policy
from mcp_helpers import seed_money_fixture, use_store
from mcp_policy import (
    McpConfig, approval_reason, brokerage_flag_money, instance_money, resolve_money_state,
)

P_INST = {"class": "money", "target": {"instance": "instance_id"}}
P_BROK = {"class": "money", "target": {"brokerage": "brokerage_id"}}
P_STRAT = {"class": "money", "target": {"strategy": "strategy_id"}}
CFG = McpConfig.from_env({})


@pytest.fixture
def seeded(store, monkeypatch):
    use_store(monkeypatch, store)
    seed_money_fixture(store)
    return store


@pytest.mark.parametrize("iid,expected", [
    ("alpaca-main", "live"), ("paper-1", "paper"), ("no-broker", "none"), ("missing", "unknown"),
    ("f14-paper", "protected"), ("swing-paper", "protected"), ("other-on-pa37", "protected"),
    ("kalshi-real", "live"), ("kalshi-dry", "paper"), ("kalshi-demo", "paper"), ("odd-1", "unknown")])
def test_instance_target(seeded, iid, expected):
    assert resolve_money_state(P_INST, {"instance_id": iid}, None, CFG) == expected


@pytest.mark.parametrize("bid,expected", [
    ("b-live", "live"), ("b-paper", "paper"), ("b-f14", "protected"), ("b-swing", "protected"),
    ("b-kalshi-live", "live"), ("b-kalshi-demo", "paper"), ("b-odd", "unknown"), ("missing", "unknown")])
def test_brokerage_target(seeded, bid, expected):
    assert resolve_money_state(P_BROK, {"brokerage_id": bid}, None, CFG) == expected


@pytest.mark.parametrize("sid,expected", [
    ("200", "live"), ("201", "paper"), ("207", "protected"), ("205", "none"), ("299", "none"),
    ("999", "unknown"),
    # swing-paper's document, and one used only by another instance on its account.
    ("202", "protected"), ("204", "protected"),
    # FastAPI parses the path int, so "0200" edits strategy 200: it must read as 200 does.
    ("0200", "live"), ("abc", "unknown")])
def test_strategy_target(seeded, sid, expected):
    assert resolve_money_state(P_STRAT, {"strategy_id": sid}, None, CFG) == expected


def test_body_field_target(seeded):     # POST /instances shape
    p = {"class": "money", "target": {"brokerage": "brokerage_id"}}
    assert resolve_money_state(p, {}, {}, CFG) == "none"
    assert resolve_money_state(p, {}, {"brokerage_id": "b-live"}, CFG) == "live"
    assert resolve_money_state(p, {}, {"brokerage_id": "nope"}, CFG) == "unknown"


def test_missing_instance_field_fails_closed(seeded):
    assert resolve_money_state(P_INST, {}, {}, CFG) == "unknown"
    assert resolve_money_state(P_INST, {}, None, CFG) == "unknown"


def test_target_id_of_an_odd_type_fails_closed(seeded):
    assert resolve_money_state(P_BROK, {}, {"brokerage_id": ["b-paper"]}, CFG) == "unknown"
    assert resolve_money_state(P_INST, {}, {"instance_id": True}, CFG) == "unknown"


def test_escalators(seeded):
    assert resolve_money_state(P_INST, {"instance_id": "paper-1"}, {"brokerage_id": "b-live"}, CFG) == "live"
    assert resolve_money_state(P_INST, {"instance_id": "kalshi-dry"}, {"paper_mode": False}, CFG) == "live"
    assert resolve_money_state(P_INST, {"instance_id": "kalshi-dry"}, {"paper_mode": True}, CFG) == "paper"


def test_paper_mode_that_pydantic_reads_as_false_escalates(seeded):
    # A Optional[bool] field takes "false" and 0 as False, so only True and None stay paper.
    for value in ("false", 0, "no"):
        body = {"paper_mode": value}
        assert resolve_money_state(P_INST, {"instance_id": "kalshi-dry"}, body, CFG) == "live"
    assert resolve_money_state(P_INST, {"instance_id": "kalshi-dry"}, {"paper_mode": None}, CFG) == "paper"


@pytest.mark.parametrize("config", [
    {"paper_mode": False, "live_enabled": False},   # final review I1
    {"live_enabled": False},                        # no stored paper_mode
    {"paper_mode": "yes", "live_enabled": False},   # truthy is not True
])
def test_kalshi_on_a_live_brokerage_is_paper_only_when_paper_mode_is_true(seeded, config):
    # PATCH .../kalshi/config re-derives live_enabled = live and not paper on every
    # call, from the stored paper_mode when the body omits it. A rename sent to a
    # bot stored paper_mode false + live_enabled false writes live_enabled true:
    # real orders. So only a stored paper_mode of exactly True reads paper.
    seeded.insert("Instances", {"id": "kalshi-off", "brokerage_id": "b-kalshi-live",
                                "kind": "kalshi", "kalshi_config": config})
    assert resolve_money_state(P_INST, {"instance_id": "kalshi-off"}, {"name": "renamed"}, CFG) == "live"
    assert resolve_money_state(P_INST, {"instance_id": "kalshi-off"}, None, CFG) == "live"


def test_escalating_brokerage_is_touched(seeded):
    # Repointing a paper instance at f14-paper's account touches f14-paper.
    assert resolve_money_state(P_INST, {"instance_id": "paper-1"}, {"brokerage_id": "b-f14"}, CFG) == "protected"
    assert resolve_money_state(P_INST, {"instance_id": "paper-1"}, {"brokerage_id": "b-swing"}, CFG) == "protected"
    assert resolve_money_state(P_INST, {"instance_id": "paper-1"}, {"brokerage_id": "nope"}, CFG) == "unknown"
    assert resolve_money_state(P_INST, {"instance_id": "paper-1"}, {"brokerage_id": ""}, CFG) == "paper"


def test_live_and_unknown_outrank_protected(seeded):
    assert resolve_money_state(P_INST, {"instance_id": "f14-paper"}, {"brokerage_id": "b-live"}, CFG) == "live"
    assert resolve_money_state(P_INST, {"instance_id": "swing-paper"}, {"brokerage_id": "b-odd"}, CFG) == "unknown"


def test_protection_follows_config(seeded):
    cfg = McpConfig.from_env({"MCP_PROTECTED_INSTANCES": "", "MCP_PROTECTED_ACCOUNTS": ""})
    assert resolve_money_state(P_INST, {"instance_id": "f14-paper"}, None, cfg) == "paper"
    assert resolve_money_state(P_INST, {"instance_id": "other-on-pa37"}, None, cfg) == "paper"


def test_store_failure_is_unknown(seeded, monkeypatch):
    monkeypatch.setattr(mcp_policy.store, "run", Mock(side_effect=RuntimeError("db down")))
    assert resolve_money_state(P_INST, {"instance_id": "paper-1"}, None, CFG) == "unknown"


def test_brokerage_read_failure_is_unknown(seeded, monkeypatch):
    monkeypatch.setattr(mcp_policy.store, "get", Mock(side_effect=RuntimeError("db down")))
    assert resolve_money_state(P_INST, {"instance_id": "paper-1"}, None, CFG) == "unknown"
    assert resolve_money_state(P_INST, {"instance_id": "no-broker"}, None, CFG) == "none"


def test_instances_are_read_once(seeded, monkeypatch):
    run = Mock(wraps=seeded.run)
    monkeypatch.setattr(mcp_policy.store, "run", run)
    assert resolve_money_state(P_STRAT, {"strategy_id": "204"}, {"brokerage_id": "b-live"}, CFG) == "live"
    assert run.call_count == 1


@pytest.mark.parametrize("row,expected", [
    ({"alpaca_paper": True}, "paper"), ({"alpaca_paper": False}, "live"),
    ({"alpaca_paper": "false"}, "unknown"), ({"kalshi_environment": "demo"}, "paper"),
    ({"kalshi_environment": "live"}, "live"), ({"kalshi_environment": "prod"}, "live"),
    ({"kalshi_environment": "staging"}, "unknown"), ({}, "unknown"), (None, "unknown")])
def test_brokerage_flag_money(row, expected):
    assert brokerage_flag_money(row) == expected


def test_instance_money_kalshi_ignores_alpaca_flag():
    brokerages = {"b": {"alpaca_paper": True, "kalshi_environment": "live"}}
    inst = {"kind": "kalshi", "brokerage_id": "b", "kalshi_config": {"live_enabled": True}}
    assert instance_money(inst, brokerages) == "live"
    assert instance_money({"kind": "kalshi", "brokerage_id": "b-gone"}, {}) == "unknown"
    off = {"kind": "kalshi", "brokerage_id": "b", "kalshi_config": {"live_enabled": False, "paper_mode": False}}
    assert instance_money(off, brokerages) == "live"
    assert instance_money({**off, "kalshi_config": None}, brokerages) == "live"
    assert instance_money({"brokerage_id": "b-gone"}, {}) == "unknown"
    assert instance_money({"brokerage_id": ""}, {}) == "none"


def test_use_store_patches_the_store_readers(store, monkeypatch):
    import auth_utils
    import interactive_utils
    use_store(monkeypatch, store)
    assert mcp_policy.store is store and auth_utils.store is store and interactive_utils.store is store


@pytest.mark.parametrize("policy,state,expected", [
    ({"class": "irreversible"}, None, "irreversible"), (P_INST, "live", "live"),
    (P_INST, "unknown", "unknown"), (P_INST, "protected", "protected"),
    (P_INST, "paper", None), (P_INST, "none", None), ({"class": "write"}, None, None),
    ({"class": "read"}, None, None),
    # A money call with no resolved state never runs unapproved.
    (P_INST, None, "unknown"), (P_INST, "bogus", "unknown")])
def test_approval_reason(policy, state, expected):
    assert approval_reason(policy, state) == expected
