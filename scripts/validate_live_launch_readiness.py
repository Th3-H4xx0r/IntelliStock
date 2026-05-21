"""Pre-launch validation for live mode.

Exit codes:
  0 - GREEN: ready to launch
  1 - YELLOW: warnings (operator should read; may proceed at their discretion)
  2 - RED: blocking issues (do not launch)

Usage:
  python scripts/validate_live_launch_readiness.py [--instance main]
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys


DB_NAME = "IntelliStock"


def _connect_rethinkdb():
    try:
        from rethinkdb import RethinkDB  # type: ignore
    except ImportError:
        return None, None
    r = RethinkDB()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    try:
        conn = r.connect(host=host, port=port)
        return r, conn
    except Exception:
        return r, None


def _check_snapshot_present(r, conn, instance_id: str):
    """Return (level, message). level in {'green', 'yellow', 'red'}."""
    try:
        rows = list(
            r.db(DB_NAME).table("NexusStrategyCache")
             .filter((r.row["instance_id"] == instance_id) & (r.row["origin"].default("") == "backtest"))
             .pluck("id", "end_date", "config_hash", "size_bytes")
             .run(conn)
        )
    except Exception as e:
        return "red", f"snapshot query failed: {e}"
    if not rows:
        return "red", f"no backtest-origin snapshot found for instance={instance_id}"
    latest = max(rows, key=lambda x: x.get("end_date") or "")
    end_date_str = latest.get("end_date", "")
    try:
        end_dt = dt.date.fromisoformat(end_date_str)
        age = (dt.date.today() - end_dt).days
    except Exception:
        return "red", f"snapshot has invalid end_date: {end_date_str!r}"
    if age > 7:
        return "yellow", f"latest snapshot is {age} days old (end_date={end_date_str}); consider rerunning backtest"
    return "green", f"snapshot found: id={latest['id']} end_date={end_date_str} bytes={latest.get('size_bytes', 0)}"


def _check_no_legacy_live_strategy_cache(r, conn, instance_id: str):
    try:
        count = int(
            r.db(DB_NAME).table("NexusStrategyCache")
             .filter((r.row["instance_id"] == instance_id) & (r.row["origin"].default("") == "live"))
             .count().run(conn) or 0
        )
    except Exception as e:
        return "yellow", f"cannot check live cache rows: {e}"
    if count > 0:
        return "yellow", f"{count} live-origin NexusStrategyCache rows still present for instance={instance_id}; run cleanup script"
    return "green", "no leftover live-origin NexusStrategyCache rows"


def _check_wal_open_count(r, conn, instance_id: str):
    try:
        existing = list(r.db(DB_NAME).table_list().run(conn))
        if "LiveOrderWAL" not in existing:
            return "green", "LiveOrderWAL table does not exist; nothing to check"
        non_terminal = ["intent", "open", "pending", "partial"]
        count = int(
            r.db(DB_NAME).table("LiveOrderWAL")
             .filter(r.row["instance_id"] == instance_id)
             .filter(lambda doc: r.expr(non_terminal).contains(doc["state"].default("")))
             .count().run(conn) or 0
        )
    except Exception as e:
        return "yellow", f"WAL check raised: {e}"
    if count > 0:
        return "yellow", f"{count} non-terminal WAL entries for instance={instance_id}; reconciliation will run on boot"
    return "green", "WAL has no non-terminal entries"


def _check_per_instance_residue(r, conn, instance_id: str):
    """Sum row counts across per-instance tables that should be empty after cleanup."""
    leftover_tables = (
        "GraphNexusDiscoveredStocks",
        "GraphNexusMarketTrends",
        "GraphNexusRotationCooldown",
        "GraphNexusTradeOutcomes",
        "GraphNexusLearningCache",
        "GraphNexusDiscoverySnapshots",
        "GraphNexusOutcomeSeries",
        "GraphNexusAnalystPanel",
    )
    try:
        existing = set(r.db(DB_NAME).table_list().run(conn))
    except Exception as e:
        return "yellow", f"table_list failed: {e}"
    by_table = {}
    for tbl in leftover_tables:
        if tbl not in existing:
            continue
        try:
            c = int(
                r.db(DB_NAME).table(tbl)
                 .filter(r.row["instance_id"] == instance_id)
                 .count().run(conn) or 0
            )
            if c > 0:
                by_table[tbl] = c
        except Exception:
            # Some tables might use composite keys; skip silently for this audit
            continue
    if by_table:
        msg = "; ".join(f"{k}={v}" for k, v in sorted(by_table.items()))
        return "yellow", f"per-instance residue: {msg} -- run cleanup script with --apply"
    return "green", "no per-instance residue in auxiliary tables"


def main(instance_id: str) -> int:
    print(f"=== Live launch readiness check: instance={instance_id} ===")
    r, conn = _connect_rethinkdb()
    if r is None:
        print("RED: rethinkdb driver not installed. `pip install rethinkdb`.")
        return 2
    if conn is None:
        print("RED: cannot connect to RethinkDB.")
        return 2

    checks = [
        ("snapshot present", _check_snapshot_present),
        ("no legacy live cache", _check_no_legacy_live_strategy_cache),
        ("WAL open count", _check_wal_open_count),
        ("per-instance residue", _check_per_instance_residue),
    ]
    results = []
    try:
        for name, fn in checks:
            level, msg = fn(r, conn, instance_id)
            tag = {"green": "[GREEN]", "yellow": "[YELLOW]", "red": "[RED]"}.get(level, "[?]")
            print(f"{tag} {name}: {msg}")
            results.append(level)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if "red" in results:
        print("\nVERDICT: RED -- do not launch.")
        return 2
    if "yellow" in results:
        print("\nVERDICT: YELLOW -- proceed at operator's discretion.")
        return 1
    print("\nVERDICT: GREEN -- ready to launch.")
    return 0


def _parse_args():
    p = argparse.ArgumentParser(description="Pre-launch validation for live mode.")
    p.add_argument("--instance", default="main")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    raise SystemExit(main(args.instance))
