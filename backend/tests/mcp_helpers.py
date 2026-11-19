"""Shared harness for the MCP gate tests (backend/tests/test_mcp_*.py).

A plain module the tests import by name, like ``live_order_task8_helpers``.
"""
from __future__ import annotations

import importlib
import os

MCP_TOKEN = "t" * 40

# Every module that binds `store` at module level and that an MCP test reads
# through. use_store skips the ones a later task has not written yet.
MCP_STORE_MODULES = ("mcp_policy", "mcp_audit", "mcp_approvals", "mcp_idempotency",
                     "mcp_caps", "auth_utils", "interactive_utils")


def mcp_env(monkeypatch, **overrides) -> None:
    """Turn the MCP listener on for one test: switch, token and port, plus overrides.

    Any MCP_* variable already in the process environment is cleared first, so a
    developer's shell can't leak into a test. An override of None unsets the
    variable instead (``mcp_env(monkeypatch, MCP_SERVICE_TOKEN=None)``).
    """
    for name in [n for n in os.environ if n.startswith("MCP_")]:
        monkeypatch.delenv(name, raising=False)
    values = {"MCP_ENABLED": "true", "MCP_SERVICE_TOKEN": MCP_TOKEN, "MCP_API_PORT": "8012", **overrides}
    for name, value in values.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, str(value))


def use_store(monkeypatch, store) -> None:
    """Point every importable module in MCP_STORE_MODULES at the test's `store` fixture.

    Only a module that does not exist is skipped: one that exists but fails to
    import (a missing dependency of its own) raises.
    """
    for name in MCP_STORE_MODULES:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:
                raise
            continue
        monkeypatch.setattr(module, "store", store)


def seed_money_fixture(store) -> None:
    """Brokerages, instances and strategies covering every money state (test_mcp_money_state).

    b-swing carries PA37JPXGU196 untrimmed and lower-case, so a match has to normalize it.
    """
    store.insert("BrokerageAccounts", [
        {"id": "b-live", "alpaca_paper": False, "alpaca_account_number": "LIVE123"},
        {"id": "b-paper", "alpaca_paper": True, "alpaca_account_number": "PA3IBY5S84PG"},
        {"id": "b-f14", "alpaca_paper": True, "alpaca_account_number": "PA3TRLBV6ZNZ"},
        {"id": "b-swing", "alpaca_paper": True, "alpaca_account_number": " pa37jpxgu196 "},
        {"id": "b-kalshi-live", "kalshi_environment": "live"},
        {"id": "b-kalshi-demo", "kalshi_environment": "demo"},
        {"id": "b-odd"},
    ])
    store.insert("Instances", [
        {"id": "alpaca-main", "brokerage_id": "b-live", "strategy_id": 200},
        {"id": "paper-1", "brokerage_id": "b-paper", "strategy_id": 201},
        {"id": "f14-paper", "brokerage_id": "b-f14", "strategy_id": 207},
        {"id": "swing-paper", "brokerage_id": "b-swing", "strategy_id": 202},
        {"id": "other-on-pa37", "brokerage_id": "b-swing", "strategy_id": 204},
        {"id": "no-broker", "strategy_id": 205},
        {"id": "kalshi-real", "brokerage_id": "b-kalshi-live", "kind": "kalshi",
         "kalshi_config": {"live_enabled": True, "paper_mode": False}},
        {"id": "kalshi-dry", "brokerage_id": "b-kalshi-live", "kind": "kalshi",
         "kalshi_config": {"live_enabled": False, "paper_mode": True}},
        {"id": "kalshi-demo", "brokerage_id": "b-kalshi-demo", "kind": "kalshi"},
        {"id": "odd-1", "brokerage_id": "b-odd"},
    ])
    store.insert("Strategies", [{"id": sid, "name": f"strategy {sid}"}
                                for sid in (200, 201, 202, 204, 205, 207, 299)])
