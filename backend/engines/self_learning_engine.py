"""Self-Learning Engine (Phase 1: OBSERVE).

Watches backtest runs for completions, normalizes their decisions into
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
kept the 5-13MB document off the wire.

After the BacktestResults split the projection stops being an optimisation at
all: the watched table is the hot `BacktestProgress` row, which IS `id` +
`status`. The whole document is read once, on a completion, through
`backtest_result_store.assemble`.
"""
from __future__ import annotations

import hashlib
import os
import sys
import threading
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

import backtest_result_store as brs
import self_learning_progress as slp
from rethink_changefeed import (is_transient_db_error,
                                run_reconnecting_changefeed)
from self_learning import approvals as learning_approvals
from self_learning import hypotheses as learning_hypotheses
from self_learning import llm as learning_llm
from self_learning import prompts as learning_prompts
from self_learning import budget as learning_budget
from self_learning import lease as learning_lease
from self_learning import loop as learning_loop
from self_learning import outcomes as learning_outcomes
from self_learning import permissions as learning_perms
from self_learning import retention, store
from self_learning.watch import is_watched
from self_learning.pipeline import process_backtest_document

_TERMINAL = frozenset({"completed", "complete", "finished", "done"})
_CRYPTO_HINTS = ("crypto", "coin")
_SWEEP_INTERVAL_SECONDS = 6 * 3600

_last_sweep = 0.0

# The changefeed fires for existing rows once and then only on change, so with
# every completed run already processed the loop would never turn again — which
# is exactly what an operator saw: findings recorded, then nothing, forever.
# The design always called for "event-driven PLUS a slow heartbeat"; this is
# the heartbeat.
_TURN_INTERVAL_SECONDS = 120


def _source_fingerprint() -> str:
    """A hash of this file, so the tab can prove which code the engine runs."""
    try:
        with open(os.path.abspath(__file__), "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()[:12]
    except Exception:
        return "unknown"


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


def _plan_and_log_turn(conn, config) -> list:
    """Run one decision turn and RECORD it.

    This is the wiring the sweep found missing: every guard in this subsystem —
    the judge's floor, the ladder, the permission matrix, the budget, the lease
    — existed with passing tests and NO production caller. That is precisely the
    failure this project already has thirteen instances of: a lever that ships,
    never executes, and is scored as if it had. The safety layer does not get an
    exemption from its own rule, so the loop runs, and it logs every intent with
    its reason.
    """
    now = _now_iso()
    try:
        floors = {}
        for row in store.list_noise_floors(conn, limit=500):
            from self_learning.noise import NoiseFloor
            floors[str(row.get("target") or "")] = NoiseFloor(
                target=str(row.get("target") or ""),
                window_class=str(row.get("window_class") or ""),
                n=int(row.get("n") or 0),
                floor_pp=float(row.get("floor_pp") or 0.0),
                mean_pp=float(row.get("mean_pp") or 0.0),
                measured=bool(row.get("measured")),
                reason=str(row.get("reason") or ""))

        ledger = store.list_budget_ledger(conn, limit=1000)
        budget_state = learning_budget.state_from_ledger(
            ledger, now_iso=now,
            daily_limit_usd=config.get("daily_budget_usd", 0),
            monthly_limit_usd=config.get("monthly_budget_usd", 0))

        running = store.running_backtests(conn)
        lease_decision = learning_lease.acquire(
            current_lease=store.get_lease(conn), running_backtests=running,
            now_iso=now, experiment_id="turn")

        active = store.list_active_changes(conn)
        targets = sorted({str(f.get("target") or "")
                          for f in store.list_funnels(conn, limit=200)
                          if f.get("target")})

        intents = learning_loop.plan_turn(
            config=config,
            matrix=learning_perms.merge_matrix(config.get("permission_matrix")),
            floors=floors, active_changes=active,
            hypotheses=store.list_hypotheses(conn, limit=100),
            budget_state=budget_state.to_doc(), lease_decision=lease_decision,
            drawdown_pct=float(config.get("attributable_drawdown_pct") or 0.0),
            breaker_limit_pct=float(config.get("breaker_limit_pct") or 0.0),
            targets_seen=targets)
    except Exception as exc:
        _log(f"turn planning failed: {type(exc).__name__}: {exc}", "red")
        return []

    summary = learning_loop.summarise(intents)
    _log(f"TURN {summary['by_kind']} (breaking={summary['breaking']})", "cyan")
    store.put_engine_status(
        conn, source_fingerprint=_source_fingerprint(), last_turn_at=now,
        last_turn_kinds=summary["by_kind"],
        has_propose_executor=True)
    for intent in intents:
        # Every intent announces itself. An unlogged decision is the same
        # unprovable state as an unlogged lever.
        _log(f"INTENT {intent.kind}"
             + (f" target={intent.target}" if intent.target else "")
             + (f" rung={intent.rung}" if intent.rung else "")
             + f" — {intent.reason}",
             "yellow" if intent.kind != learning_loop.IDLE else "white")
        try:
            store.put_intent(conn, intent.to_doc(), at=now)
        except Exception:
            pass
        if intent.kind == learning_loop.REQUEST_APPROVAL:
            _request_approval(conn, intent, now)
        elif intent.kind == learning_loop.PROPOSE:
            try:
                _execute_propose(conn, config, intent, now)
            except Exception as exc:
                _skip(conn, now,
                      f"the proposal path raised {type(exc).__name__}: {exc}")
    return intents


def _skip(conn, now, reason, **extra) -> None:
    """Record WHY a proposal did not happen, where the operator can see it.

    A skip that only writes to a container log is indistinguishable, from the
    tab, from the loop not running at all — which is precisely the "it just
    stopped" state this subsystem exists to make impossible. So the reason is a
    row like any other decision.
    """
    _log(f"PROPOSE skipped — {reason}", "yellow")
    try:
        store.put_intent(conn, {"kind": "propose_skipped", "reason": reason,
                                **extra}, at=now)
    except Exception:
        pass


def _execute_propose(conn, config, intent, now) -> None:
    """Actually ask the generator for a hypothesis.

    Until this existed the loop PLANNED a proposal every turn and nothing acted
    on it, so an operator with all four roles configured watched the tab do
    nothing forever. Planning without executing is the same unprovable state as
    a lever that never runs — the failure this subsystem exists to detect.
    """
    resolved = store.resolved_config(conn)
    from self_learning.roles import GENERATOR, role_config

    generator = role_config(GENERATOR, resolved)
    if not generator.configured:
        _skip(conn, now,
              f"no generator model resolved — learning_generator_llm_model_id="
              f"{config.get('learning_generator_llm_model_id') or 'unset'!r}, "
              f"resolved provider={generator.provider!r} model={generator.model!r}"
              + (f"; resolver error: {resolved['_resolution_error']}"
                 if resolved.get("_resolution_error") else
                 "; the Models row resolved to no provider — is "
                 "INTELLISTOCK_CRED_KEY present in the engine container?"))
        return

    findings = store.list_findings(conn, limit=10)
    open_findings = [f for f in findings
                     if str(f.get("status") or "open") == "open"]
    if not open_findings:
        _skip(conn, now,
              f"no open finding to base a hypothesis on "
              f"({len(findings)} finding(s) total)")
        return
    finding = open_findings[0]
    target = str(finding.get("target") or "")

    funnels = [f for f in store.list_funnels(conn, limit=50)
               if str(f.get("target") or "") == target]
    summary = funnels[0] if funnels else {}
    floor = store.get_noise_floor(
        conn, target=target,
        window_class=str(summary.get("window_class") or "")) or {}

    try:
        from self_learning.levers import lever_surface
        from strategies_meta import get_available_strategies
        strategy_id = target.split("/")[-1]
        levers = [l.to_doc() for l in lever_surface(get_available_strategies())
                  if l.strategy_id == strategy_id]
    except Exception:
        levers = []

    ledger = store.list_hypotheses(conn, limit=100, target=target)
    system, user = learning_prompts.generator_prompt(
        target=target, levers=levers, noise_floor=floor,
        summary=summary, refusal_cost=(summary.get("refusal_cost") or {}),
        findings=[finding],
        rejected=learning_hypotheses.prior_rejections(ledger, target=target))

    # Beacon: the tab polls this to show a live spinner on the finding whose
    # hypothesis is being generated, rather than leaving the operator to guess
    # whether a model call is in flight.
    store.begin_activity(conn, role=GENERATOR, target=target,
                         finding_id=str(finding.get("id") or ""),
                         step="PROPOSED", at=now)
    try:
        result = learning_llm.call_role(GENERATOR, resolved, system, user)
    finally:
        store.end_activity(conn, role=GENERATOR)
    if not result.ok:
        _log(f"PROPOSE failed — {result.error}", "red")
        return

    try:
        hypothesis = learning_hypotheses.build(
            result.payload, finding_id=str(finding.get("id") or ""),
            target=target, author_model=result.model,
            prompt_hash=result.prompt_hash, created_at=now,
            known_levers=[l["key"] for l in levers] or None)
    except learning_hypotheses.HypothesisError as exc:
        # A refused proposal is RECORDED, not swallowed: "the generator keeps
        # proposing undetectable effects" is itself a finding.
        _skip(conn, now, f"the generator's proposal was refused: {exc}",
              target=target, model=result.model)
        return

    skip = learning_hypotheses.already_proposed(hypothesis, ledger)
    if skip:
        _skip(conn, now, skip, target=target)
        return

    store.put_hypothesis(conn, hypothesis)
    _log(f"LEARNING PROPOSAL [{target}] {hypothesis.claim} "
         f"(predicts {hypothesis.predicted_direction} "
         f"{hypothesis.predicted_min_pp}-{hypothesis.predicted_max_pp}pp via "
         f"{', '.join(hypothesis.lever_keys)}; model {result.model})", "yellow")


def _request_approval(conn, intent, now) -> None:
    """Enqueue the proposal the operator has to answer.

    Nothing constructed an Approval before this, so `action_learning_approvals`
    could only ever return an empty queue and `decide_approval` could only
    decide rows that were never written.
    """
    try:
        approval = learning_approvals.Approval(
            hypothesis_id=intent.hypothesis_id, experiment_id=intent.experiment_id,
            target=intent.target, rung=intent.rung,
            action_class=learning_perms.CONFIG_LEVERS,
            summary=intent.reason, document_id=intent.document_id,
            requested_at=now)
        store.put_approval(conn, approval)
        _log(f"LEARNING PROPOSAL [{intent.rung}] {intent.target}: "
             f"{intent.reason}", "yellow")
    except Exception as exc:
        _log(f"could not enqueue approval: {type(exc).__name__}: {exc}", "red")


class ControlPlaneUnreadable(RuntimeError):
    """The control plane could not be READ — which is not the same answer as
    "the operator turned it off", and must not be handled the same way.

    Before the Postgres port the changefeed cursor was bound to the same
    connection this reads, so a dropped connection raised out of the feed loop
    and `run_reconnecting_changefeed` healed it. The feed now lives on Postgres
    and cannot see a RethinkDB blip, so the liveness coupling has to be
    restored here: swallowing the error would leave completion processing
    permanently inert while the process still looked healthy.
    """


def _refuse(what: str, exc: Exception) -> None:
    """Log an unreadable control read, and re-raise the transient ones.

    Fail CLOSED either way — every caller treats a return as "do not run".
    What changes is that a connection-level failure is now LOUD and reaches a
    reconnect, instead of being indistinguishable from `running: False`.
    """
    _log(f"cannot read {what}: {type(exc).__name__}: {exc} — refusing to run",
         "red")
    if is_transient_db_error(exc):
        raise ControlPlaneUnreadable(f"{what}: {exc}") from exc


def _should_run(conn) -> bool:
    """Fail CLOSED. A missing control document or an unreadable config means
    stop, not go — the engine ships `running: False` and a database that cannot
    answer is not permission to start scanning it.

    A TRANSIENT read failure additionally raises `ControlPlaneUnreadable`, so
    the caller can reconnect rather than sit inert forever. Fail-closed is
    unaffected: nothing downstream of a raise gets to run either.
    """
    try:
        doc = r.db(DB_NAME).table("EngineControl").get(
            "self_learning_engine").run(conn)
    except Exception as exc:
        _refuse("EngineControl", exc)
        return False
    if not doc or not doc.get("running", False):
        return False
    try:
        return bool(store.get_config(conn).get("enabled", True))
    except Exception as exc:
        _refuse("LearningConfig", exc)
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
        # The whole document, reassembled from the split tables. is_watched()
        # and _process() both read the legacy shape, so nothing narrower will
        # do -- and this is the ONE read per completion, not per progress tick.
        doc = brs.assemble(run_id)
        if doc:
            config = store.get_config(conn)
            watched = config.get("watched_instances") or []
            if not is_watched(doc, watched):
                _log(f"run {run_id} skipped — its instance is not in the "
                     f"watch list ({len(watched)} watched)", "white")
                return
            _process(conn, doc, config)
            _maybe_sweep(conn, config)
            # A finished run is an event worth thinking about, which is what
            # "event-driven" was supposed to mean.
            _plan_and_log_turn(conn, config)
    except Exception as exc:
        _log(f"run {run_id} failed: {type(exc).__name__}: {exc}", "red")


def _make_handler(processed):
    """The per-change handler, as a closure over the processed-run watermark.

    Module level, not nested in main(), so the property that matters can be
    tested: a dead control-plane connection must reach
    `run_reconnecting_changefeed` and be reconnected, not be swallowed into
    permanent inertness.
    """
    def _handle(change, c):
        try:
            if not _should_run(c):
                return
            new_val = (change or {}).get("new_val") or {}
            run_id = new_val.get("id")
            if run_id is None:
                return
            _handle_run(c, run_id, new_val.get("status"), processed)
        except ControlPlaneUnreadable:
            # Deliberately ESCAPES. The runner treats an escaping handler
            # exception as a feed error and reconnects with a fresh connection
            # (rethink_changefeed.py:112-116) — which is what the ReQL cursor
            # bound to this same connection used to do for us.
            raise
        except Exception as exc:
            _log(f"change handler error: {type(exc).__name__}: {exc}", "red")
    return _handle


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

    # Telemetry is opt-in per process: `record_llm_call` returns immediately
    # unless `configure` ran. Without this the engine made real LLM calls and
    # recorded none of them, so the tab's token and cost figures sat at zero
    # while money was being spent — the worst possible direction for a
    # subsystem whose spending is supposed to be capped.
    try:
        import llm_telemetry
        from llm_telemetry import ensure_llm_usage_tables
        llm_telemetry.configure(
            db_conn_factory=store.get_conn,
            enabled=True,
            flush_interval_s=2.0,
            max_buffer=25,
            pricing_yaml_path=os.path.join(_backend_dir, "llm_pricing.yaml"),
            r_module=r,
            db_name=DB_NAME,
        )
        setup_conn = store.get_conn()
        try:
            ensure_llm_usage_tables(conn=setup_conn, r=r, db_name=DB_NAME)
        finally:
            try:
                setup_conn.close()
            except Exception:
                pass
        _log("LLM telemetry configured — token and cost recording is on", "green")
    except Exception as exc:
        _log(f"LLM telemetry NOT configured ({type(exc).__name__}: {exc}) — "
             f"calls will still work but their cost will not be recorded", "red")

    _log(f"Self-learning engine started (source {_source_fingerprint()})", "green")
    store.put_engine_status(conn, source_fingerprint=_source_fingerprint(),
                            started_at=_now_iso(), has_propose_executor=True)
    processed = set(store.get_config(conn).get("processed_run_ids") or [])

    def _open_feed(c):
        # The old feed needed a server-side pluck because `new_val` was
        # otherwise the whole 5-13MB document on every progress tick of a
        # running backtest. After the split the hot BacktestProgress row IS
        # that projection, so this watches it directly. include_initial
        # replays every row on each reconnect and the persisted
        # processed_run_ids watermark dedupes, exactly as before.
        #
        # `c` is the self-learning store's connection: the LearningConfig /
        # LearningObservations tables the handler reads and writes have not
        # been ported, so the handler still needs it and the feed ignores it.
        return slp.progress_feed()

    _handle = _make_handler(processed)

    def _heartbeat():
        """Turn on a timer as well as on an event."""
        beat_conn = None
        while True:
            time.sleep(_TURN_INTERVAL_SECONDS)
            try:
                if beat_conn is None:
                    beat_conn = store.get_conn()
                if not _should_run(beat_conn):
                    continue
                config = store.get_config(beat_conn)
                _plan_and_log_turn(beat_conn, config)
                _maybe_sweep(beat_conn, config)
            except Exception as exc:
                _log(f"heartbeat error: {type(exc).__name__}: {exc}", "yellow")
                try:
                    beat_conn.close()
                except Exception:
                    pass
                beat_conn = None

    threading.Thread(target=_heartbeat, daemon=True).start()
    _log(f"Heartbeat started — a turn every {_TURN_INTERVAL_SECONDS}s", "green")

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
