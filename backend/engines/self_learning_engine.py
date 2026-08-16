"""Self-Learning Engine (Phase 1: OBSERVE).

Watches BacktestResults for completed runs, normalizes their decisions into
LearningObservations, and raises findings when a guard trips. It writes NO
strategy config and takes NO autonomous action — later phases add that behind
the permission matrix in LearningConfig.

Controlled via EngineControl.self_learning_engine (running true/false), exactly
like daily_digest_engine and discover_engine.

WHY A CHANGEFEED AND NOT A POLL. The first version scanned
``BacktestResults.pluck("id","status")`` every 30 seconds. RethinkDB has no
columnar projection, so `pluck` is a post-read transform: the server must
deserialize every document to emit two fields, and a single row here carries
5-13MB (`interactive_utils.py:5220`) across ~1000 rows. That is gigabytes of
deserialization 2,880 times a day against a 5GB cache on a VM that already
suffered 17 restarts in 12 days. `run_reconnecting_changefeed` is the house
idiom for exactly this (six uses in `server.py`), and a server-side projection
keeps the 5-13MB document off the wire.
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

from rethink_changefeed import run_reconnecting_changefeed
from self_learning import outcomes as learning_outcomes
from self_learning import retention, store
from self_learning.pipeline import process_backtest_document

_TERMINAL = frozenset({"completed", "complete", "finished", "done"})
_CRYPTO_HINTS = ("crypto", "coin")
_SWEEP_INTERVAL_SECONDS = 6 * 3600

_last_sweep = 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _venue_for(doc) -> str:
    """Crypto and equity are different targets and must not share a thread.

    `Finding.id` hashes its target, so labelling a crypto run "equity" collapses
    two venues' findings into one row and `conflict="update"` overwrites one
    with the other.
    """
    kind = str((doc or {}).get("kind") or (doc or {}).get("instance_kind") or "")
    if any(hint in kind.lower() for hint in _CRYPTO_HINTS):
        return "crypto"
    tickers = (doc or {}).get("tickers") or []
    if tickers and all("/" in str(t) for t in tickers):
        return "crypto"          # crypto pairs are SYM/QUOTE; equities are not
    return "equity"


def _run_time(doc) -> str:
    """The run's own time, for ordering. NOT the processing time — stamping
    "now" makes a restart rewrite every funnel's position in the feed."""
    for key in ("completed_at", "end_date", "timestamp", "_last_active"):
        value = (doc or {}).get(key)
        if value:
            return str(value)
    return _now_iso()


def _process(conn, doc, config) -> None:
    venue = _venue_for(doc)
    result = process_backtest_document(
        doc, detected_at=_now_iso(), venue=venue,
        variance_threshold=float(config.get("variance_threshold", 0.95) or 0.95),
        variance_min_n=int(config.get("variance_min_n", 30) or 30),
    )
    if not result["observations"]:
        return
    written = store.put_observations(conn, result["observations"])

    # Phase 2: price the decisions, INCLUDING the ones a gate refused. Without
    # this "it refused 134 names" stays a fact; with it, it becomes "those names
    # went on to beat the benchmark by X", which is a finding.
    persisted = store.persistable(result["observations"])
    resolved = learning_outcomes.resolve(persisted, doc)
    cost = learning_outcomes.refusal_cost(resolved, persisted)
    summary = dict(result["summary"])
    summary["refusal_cost"] = cost
    # Unresolved outcomes are not persisted, so their COUNTS ride here — they
    # are the denominator, and they are not randomly distributed.
    summary["unresolved_outcomes"] = learning_outcomes.unresolved_reasons(resolved)

    # The cheap, high-value writes go FIRST. `_handle_run` marks a run
    # processed BEFORE calling this (deliberately, so one bad document cannot
    # wedge the loop), which means an exception here is never retried. If the
    # bulk outcome write went first, its failure would permanently destroy the
    # funnel row and findings for this run — and `store.counts()` derives every
    # operator-facing counter from the funnel table.
    store.put_funnel(conn, result["run_id"], summary,
                     target=result["target"], observed_at=_run_time(doc))
    store.put_findings(conn, result["findings"])

    # Only the scoring horizon is persisted. Three horizons x thousands of
    # decisions x ~1000 historical runs is millions of rows, and PriceHistory
    # at 2.3M rows already drove 17 restarts in 12 days on this host.
    try:
        scoring = [o for o in resolved
                   if o.horizon_bars == learning_outcomes.SCORING_HORIZON_BARS]
        store.put_outcomes(conn, scoring)
    except Exception as exc:
        _log(f"outcome write failed for run {result['run_id']}: "
             f"{type(exc).__name__}: {exc}", "yellow")
    # Every lever in this project that shipped without its own log line became
    # unprovable. This one announces itself.
    _log(
        f"OBSERVED run {result['run_id']} target={result['target']} "
        f"rows={written} decided={summary['decided']} "
        f"executed={summary['executed']} refused={summary['refused']} "
        f"join={summary['trades_matched']}/{summary['trades_available']} "
        f"gate_refused={summary.get('gate_refused', 0)} "
        f"outcomes={len(resolved)} findings={len(result['findings'])}",
        "cyan",
    )
    if cost.get("refusals_resolvable") and cost.get("refused_median_excess_pct") is not None:
        _bought = cost.get("executed_median_excess_pct")
        _bought_txt = "n/a" if _bought is None else f"{_bought:+.2f}pp"
        _log(
            f"REFUSAL COST run {result['run_id']}: {cost['refused_n']} refused "
            f"BUY(s) had a median {cost['horizon_bars']}-bar excess of "
            f"{cost['refused_median_excess_pct']:+.2f}pp vs {_bought_txt} for "
            f"the ones it bought; by gate: {cost.get('by_gate') or {}}",
            "yellow",
        )
    elif summary.get("unresolved_outcomes"):
        # Silence here used to be ambiguous between "no refusals" and "the
        # benchmark was missing so nothing could be scored". Say which.
        _log(f"REFUSAL COST run {result['run_id']}: not scoreable — "
             f"{summary['unresolved_outcomes']}", "yellow")
    for finding in result["findings"]:
        _log(f"FINDING [{finding.severity}] {finding.title}", "yellow")


def _should_run(conn) -> bool:
    """Fail CLOSED. A missing control document or an unreadable config means
    stop, not go — the engine ships `running: False` and a database that cannot
    answer is not permission to start scanning it."""
    try:
        doc = r.db(DB_NAME).table("EngineControl").get(
            "self_learning_engine").run(conn)
    except Exception:
        return False
    if not doc or not doc.get("running", False):
        return False
    try:
        return bool(store.get_config(conn).get("enabled", True))
    except Exception:
        return False


def _maybe_sweep(conn, config) -> None:
    """Retention, as a server-side range delete on the `as_of` index."""
    global _last_sweep
    now = time.time()
    if now - _last_sweep < _SWEEP_INTERVAL_SECONDS:
        return
    _last_sweep = now
    cutoff = retention.cutoff_iso(now_iso=_now_iso(),
                                  retain_days=config.get("retain_days", 90))
    if not cutoff:
        return
    try:
        deleted = store.sweep_expired(conn, cutoff=cutoff)
        # Outcomes are keyed to observations and expire on the same clock, or
        # they become orphans that outlive the rows they describe.
        deleted_outcomes = store.sweep_expired_outcomes(conn, cutoff=cutoff)
        if deleted or deleted_outcomes:
            _log(f"RETENTION swept {deleted} observation(s) and "
                 f"{deleted_outcomes} outcome(s) older than {cutoff}", "cyan")
    except Exception as exc:
        _log(f"retention sweep failed: {type(exc).__name__}: {exc}", "yellow")


def _handle_run(conn, run_id, status, processed) -> None:
    if str(status or "").strip().lower() not in _TERMINAL:
        return
    if str(run_id) in processed:
        return
    # Mark BEFORE processing. Marking after meant one un-processable document
    # aborted the loop and was retried forever, so every run behind it was
    # never observed while the engine looked alive.
    processed.add(str(run_id))
    try:
        store.mark_processed(conn, run_id)
    except Exception:
        pass
    try:
        doc = r.db(DB_NAME).table("BacktestResults").get(run_id).run(conn)
        if doc:
            config = store.get_config(conn)
            _process(conn, doc, config)
            _maybe_sweep(conn, config)
    except Exception as exc:
        _log(f"run {run_id} failed: {type(exc).__name__}: {exc}", "red")


def main() -> None:
    conn = None
    for attempt in range(1, 31):
        try:
            conn = store.get_conn()
            store.ensure_tables(conn)
            break
        except Exception as exc:
            if attempt == 30:
                _log(f"RethinkDB not ready after 30 attempts: {exc}", "red")
                return
            _log(f"RethinkDB not ready (attempt {attempt}/30), retrying", "yellow")
            time.sleep(2)

    _log("Self-learning engine started (Phase 1: observe-only)", "green")
    processed = set(store.get_config(conn).get("processed_run_ids") or [])

    def _open_feed(c):
        # Server-side projection: without it `new_val` is the whole 5-13MB
        # document on every progress tick of a running backtest.
        return (r.db(DB_NAME).table("BacktestResults")
                .changes(squash=True, include_initial=True)
                .pluck({"new_val": ["id", "status"]}).run(c))

    def _handle(change, c):
        try:
            if not _should_run(c):
                return
            new_val = (change or {}).get("new_val") or {}
            run_id = new_val.get("id")
            if run_id is None:
                return
            _handle_run(c, run_id, new_val.get("status"), processed)
        except Exception as exc:
            _log(f"change handler error: {type(exc).__name__}: {exc}", "red")

    try:
        conn.close()
    except Exception:
        pass

    run_reconnecting_changefeed(
        _open_feed, _handle, "SelfLearning",
        get_conn=store.get_conn, log=intellistock_logger.log,
    )


if __name__ == "__main__":
    main()
