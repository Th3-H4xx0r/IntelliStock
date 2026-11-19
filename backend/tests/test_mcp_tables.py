"""The MCP gate's four tables (contract §6): registered, retained, indexed.

McpApprovals, McpApprovalDevices, McpIdempotency and McpAudit are reached only
through the gate's own modules. The registry must declare them so a bare
``ensure_schema()`` creates them, and the two append-only tables carry the
retention windows the contract pins.
"""
from __future__ import annotations

import os
import sys
import threading

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from db import schema  # noqa: E402
from db.store import P  # noqa: E402

requires_pg = pytest.mark.skipif(
    not os.environ.get("PG_TEST_DSN"),
    reason="PG_TEST_DSN not set (run: ./scripts/dev_pg.sh up)")

NAMES = ("McpApprovals", "McpApprovalDevices", "McpIdempotency", "McpAudit")


def run_in_two_threads(fn):
    """Run ``fn`` in two threads released together; return both results."""
    barrier = threading.Barrier(2)
    results = [None, None]
    errors = []

    def worker(i):
        try:
            barrier.wait()
            results[i] = fn()
        except BaseException as exc:  # surfaced below, never swallowed
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]
    return results


def test_mcp_tables_are_registered():
    for n in NAMES:
        assert n in schema.ALL_TABLES and n in schema.TABLES


def test_mcp_retention_and_indexes():
    a, i = schema.spec("McpAudit"), schema.spec("McpIdempotency")
    assert (a.retention.days_env, a.retention.default_days, a.retention.field) == ("RETAIN_MCP_AUDIT_DAYS", 365, "ts")
    assert (i.retention.days_env, i.retention.default_days, i.retention.field) == ("RETAIN_MCP_IDEMPOTENCY_DAYS", 1, "created_at")
    assert a.notify is False and i.notify is False
    assert schema.spec("McpApprovals").indexed_fields == ("status", "user_id", "created_at")
    assert "ts" in a.indexed_fields and "created_at" in i.indexed_fields


@requires_pg
def test_approval_status_cas_has_one_winner(store):
    store.insert("McpApprovals", {"id": "a1", "status": "pending"})
    when = P.field("status").eq("pending")
    results = run_in_two_threads(lambda: store.replace_if("McpApprovals", "a1", when=when,
                                                          doc={"id": "a1", "status": "approved"}))
    assert sum(r is not None for r in results) == 1
