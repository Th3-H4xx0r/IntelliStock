"""Self-Learning Engine (Phase 1: OBSERVE).

Watches BacktestResults for completed runs, normalizes their decisions into
LearningObservations, and raises findings when a guard trips. It writes NO
strategy config and takes NO autonomous action — later phases add that behind
the permission matrix in LearningConfig.

Controlled via EngineControl.self_learning_engine (running true/false), exactly
like daily_digest_engine and discover_engine.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

# Run from backend/engines/; backend is parent for imports and cwd (must be
# before any backend imports).
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
os.chdir(_backend_dir)

from rethinkdb import RethinkDB

r = RethinkDB()
DB_NAME = "IntelliStock"

try:
    from intellistock_logger import intellistock_logger

    def _log(msg, color="white"):
        intellistock_logger.log(msg, color, service="SELF_LEARNING")
except Exception:                                    # pragma: no cover
    def _log(msg, color="white"):
        print(f"[SELF_LEARNING] {msg}")

from self_learning import store
from self_learning.pipeline import process_backtest_document

_TERMINAL = frozenset({"completed", "complete", "finished", "done"})
_IDLE_SECONDS = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _process(conn, doc, config) -> None:
    result = process_backtest_document(
        doc, detected_at=_now_iso(),
        variance_threshold=float(config.get("variance_threshold", 0.95)),
        variance_min_n=int(config.get("variance_min_n", 30)),
    )
    if not result["observations"]:
        return
    written = store.put_observations(conn, result["observations"])
    store.put_funnel(conn, result["run_id"], result["summary"],
                     target=result["target"], observed_at=_now_iso())
    store.put_findings(conn, result["findings"])
    summary = result["summary"]
    # Every lever in this project that shipped without its own log line became
    # unprovable. This one announces itself.
    _log(
        f"OBSERVED run {result['run_id']} target={result['target']} "
        f"observations={written} decided={summary['decided']} "
        f"executed={summary['executed']} refused={summary['refused']} "
        f"findings={len(result['findings'])}",
        "cyan",
    )
    for finding in result["findings"]:
        _log(f"FINDING [{finding.severity}] {finding.title}", "yellow")


def _is_running(conn) -> bool:
    try:
        doc = r.db(DB_NAME).table("EngineControl").get(
            "self_learning_engine").run(conn)
    except Exception:
        return True
    return bool((doc or {}).get("running", True))


def main() -> None:
    conn = store.get_conn()
    store.ensure_tables(conn)
    _log("Self-learning engine started (Phase 1: observe-only)", "green")
    seen = set()
    while True:
        try:
            if not _is_running(conn):
                time.sleep(10)
                continue
            config = store.get_config(conn)
            if not config.get("enabled", True):
                time.sleep(_IDLE_SECONDS)
                continue
            rows = list(r.db(DB_NAME).table("BacktestResults")
                        .pluck("id", "status").run(conn))
            for row in rows:
                rid = row.get("id")
                if rid in seen:
                    continue
                if str(row.get("status") or "").strip().lower() not in _TERMINAL:
                    continue
                doc = r.db(DB_NAME).table("BacktestResults").get(rid).run(conn)
                if doc:
                    _process(conn, doc, config)
                seen.add(rid)
            time.sleep(_IDLE_SECONDS)
        except Exception as exc:                      # pragma: no cover
            _log(f"loop error: {type(exc).__name__}: {exc}", "red")
            time.sleep(_IDLE_SECONDS)
            try:
                conn = store.get_conn()
            except Exception:
                pass


if __name__ == "__main__":
    main()
