"""Shared harness for the MCP gate tests (backend/tests/test_mcp_*.py).

A plain module the tests import by name, like ``live_order_task8_helpers``.
"""
from __future__ import annotations

import os

MCP_TOKEN = "t" * 40


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
