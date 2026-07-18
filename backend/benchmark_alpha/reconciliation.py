"""Order-ownership reconciliation (Task 8 Step 9).

Classifies every broker fill as strategy-owned (WAL-linked by client-order
prefix), dashboard/manual, or UNRESOLVED. Any unresolved owner is a
readiness failure — promotion is blocked until ownership is exact. Also
surfaces decision/WAL lineage gaps: WAL intents without terminal state and
fills without WAL lineage.
"""
from broker_adapters._wal import TERMINAL_STATES


def reconcile_order_ownership(*, broker_fills, wal_rows, strategy_cid_prefix):
    if not strategy_cid_prefix or not str(strategy_cid_prefix).strip():
        # startswith("") is True for EVERY cid — an empty prefix would
        # silently classify all fills as strategy-owned (bug sweep 2026-07-18).
        raise ValueError("strategy_cid_prefix must be a non-empty prefix")
    strategy_cid_prefix = str(strategy_cid_prefix)
    wal_by_cid = {str(row.get("client_order_id") or ""): row
                  for row in (wal_rows or [])}
    strategy_owned = 0
    dashboard = 0
    unresolved = []
    fills_without_wal = []
    for fill in broker_fills or ():
        cid = str(fill.get("client_order_id") or "")
        broker_order_id = str(fill.get("broker_order_id") or "")
        if cid.startswith(strategy_cid_prefix) or cid.startswith("alpha-") \
                or cid.startswith("emg-"):
            strategy_owned += 1
            if cid not in wal_by_cid:
                fills_without_wal.append(broker_order_id or cid)
        elif cid:
            # A client id we did not issue: dashboard/manual ownership.
            dashboard += 1
        else:
            unresolved.append(broker_order_id or "<no-id>")
    non_terminal = sorted(
        cid for cid, row in wal_by_cid.items()
        if str(row.get("state") or "") not in TERMINAL_STATES)
    return {
        "strategy_owned": strategy_owned,
        "dashboard_or_manual": dashboard,
        "unresolved": unresolved,
        "fills_without_wal_lineage": fills_without_wal,
        "non_terminal_intents": non_terminal,
        "readiness_ok": not unresolved and not fills_without_wal,
    }
