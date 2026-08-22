"""Backtest-mode critical-LLM-failure PAUSE handler (renamed conceptually
from "abort"; module filename kept to minimize churn).

When LLMCriticalFailure fires:
  1. Restore the in-memory bar snapshot (from backtest_bar_snapshot)
  2. Set BacktestInstances.paused=True (the existing pause field)
  3. Write BacktestResults.status='paused_llm_critical' + diagnostic fields
  4. Fire yellow Discord embed
  5. Return (NO sys.exit — the broker's main loop hits the existing wait block)

On resume (handled by broker.py's changefeed thread), llm_critical_guard
and this module's _already_alerted flag both reset.
"""
from __future__ import annotations

import os
import threading
from typing import Any


_state_lock = threading.RLock()
_already_alerted = False
# Retained for backwards-compat with strategy_cache_persistence guard, but
# pause flow NEVER sets it to True. Stays False unless future code paths
# need it.
_skip_snapshot_persist = False


def _get_store():
    """R26: the store takes its own pooled connection per operation, so there
    is nothing to open or close here. Kept as a seam so tests can cage it.
    """
    from db import store as db_store
    return db_store


def _write_backtest_pause_status(backtest_id, payload: dict) -> None:
    """DB seam (tests cage this). ``status`` goes to the hot BacktestProgress
    row and the diagnostic pause_* fields deep-merge into BacktestResults.doc,
    so every partial-result field already on the row survives -- the same
    merge semantics the legacy ``.update(payload)`` had.

    BacktestResults.id is written as an int by the engine (and by every other
    writer). store.coerce_id accepts an int or a string and RAISES on garbage,
    so the old failure mode -- a string id silently no-opping against
    RethinkDB's type-strict get() with {skipped: 1} -- is now impossible
    rather than merely avoided.
    """
    import backtest_result_store as _brs
    _brs.set_status(backtest_id,
                    payload.get("status") or "paused_llm_critical",
                    extra_metadata={k: v for k, v in payload.items()
                                    if k != "status"})


def _enqueue_discord(channel: str, content: str, embed: dict) -> None:
    from discord_sender import enqueue_discord_message
    from interactive_utils import get_conn
    conn = get_conn()
    try:
        enqueue_discord_message(conn, channel, content, embed=embed)
    finally:
        try: conn.close()
        except Exception: pass


def _bs_restore():
    """Indirection for test mocking."""
    from backtest_bar_snapshot import restore
    return restore()


def _apply_restore(*, strategy_caches: dict, portfolio_emulator: Any, current_time) -> None:
    """Indirection for test mocking — broker_snapshot_helpers does the real work."""
    from broker_snapshot_helpers import _apply_in_process_snapshot_restore
    _apply_in_process_snapshot_restore(
        strategy_caches=strategy_caches,
        portfolio_emulator=portfolio_emulator,
        current_time=current_time,
    )


def _build_pause_embed(*, backtest_id, instance_id, failure, restored_time) -> dict:
    first = failure.attempts[0] if failure.attempts else {}
    last = failure.attempts[-1] if failure.attempts else {}
    sample = (last.get("body_sample") or "")[:500]
    if len(last.get("body_sample") or "") > 500:
        sample += "...[truncated]"
    bar_str = _fmt_ts(restored_time) if restored_time else "unknown"
    embed = {
        "title": "Backtest paused: LLM critical failure",
        "color": 0xF1C40F,
        "fields": [
            {"name": "backtest_id", "value": str(backtest_id), "inline": True},
            {"name": "instance_id", "value": str(instance_id), "inline": True},
            {"name": "class", "value": failure.class_tag, "inline": True},
            {"name": "provider", "value": failure.provider, "inline": True},
            {"name": "model", "value": failure.model, "inline": True},
            {"name": "call_site",
             "value": str(failure.attribution.get("call_site") or "unknown"),
             "inline": True},
            {"name": "bar_time", "value": bar_str, "inline": True},
            {"name": "attempts",
             "value": f"{len(failure.attempts)} (1 + {max(0, len(failure.attempts)-1)} retries)",
             "inline": True},
            {"name": "sample", "value": f"```{sample}```", "inline": False},
            {"name": "resume_via",
             "value": "Click Resume in the backtest detail UI, or POST /backtests/<id>/resume",
             "inline": False},
        ],
        "footer": {"text": "IntelliStock backtest critical-guard - resume from UI when provider recovers"},
    }
    try:
        from live_alerts import _scrub_embed
        embed = _scrub_embed(embed) or embed
    except Exception:
        pass
    return embed


def _fmt_ts(ts) -> str:
    if ts is None:
        return "unknown"
    if hasattr(ts, "strftime"):
        return ts.strftime("%Y-%m-%d %H:%M:%SZ")
    try:
        import datetime as _dt
        return _dt.datetime.fromtimestamp(float(ts), tz=_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    except Exception:
        return str(ts)


def handle(*, backtest_id: str, instance_id: str, failure) -> None:
    """PAUSE on critical failure. Idempotent: second call is a no-op."""
    global _already_alerted
    with _state_lock:
        if _already_alerted:
            return
        _already_alerted = True

    # 1. Restore in-memory snapshot (if one exists)
    restored_time = None
    try:
        restored = _bs_restore()
        if restored is not None:
            r_caches, r_portfolio, r_time = restored
            restored_time = r_time
            try:
                _apply_restore(
                    strategy_caches=r_caches,
                    portfolio_emulator=r_portfolio,
                    current_time=r_time,
                )
            except Exception as e:
                _log_red(f"snapshot apply failed: {e}")
    except Exception as e:
        _log_red(f"snapshot restore failed: {e}")

    # 2 + 3. DB writes
    try:
        db_store = _get_store()
        # BacktestInstances.paused=True
        try:
            db_store.update("BacktestInstances", int(backtest_id),
                            {"paused": True})
        except Exception as e:
            _log_red(f"BacktestInstances pause flag write failed: {e}")

        # BacktestResults.status + diagnostic fields
        try:
            payload = {
                "status": "paused_llm_critical",
                "pause_reason_tag": failure.class_tag,
                "pause_reason_text": _format_reason_text(failure),
                "pause_provider": failure.provider,
                "pause_model": failure.model,
                "pause_call_site": failure.attribution.get("call_site") or "unknown",
                "pause_attempts": len(failure.attempts),
                "pause_bar_time": _fmt_ts(restored_time),
                "paused_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc).isoformat(),
                "pause_sample": (failure.attempts[-1].get("body_sample") or "")[:500] if failure.attempts else "",
            }
            _write_backtest_pause_status(backtest_id, payload)
        except Exception as e:
            _log_red(f"BacktestResults pause status update failed: {e}")
    except Exception as e:
        _log_red(f"DB write phase failed: {e}")

    # 4. Discord
    try:
        content = (
            f"BACKTEST PAUSED [{backtest_id}] {failure.class_tag} after "
            f"{len(failure.attempts)} attempts on {failure.model} ({failure.provider}); "
            f"resume from UI when provider recovers"
        )
        embed = _build_pause_embed(
            backtest_id=backtest_id,
            instance_id=instance_id,
            failure=failure,
            restored_time=restored_time,
        )
        _enqueue_discord("backtests", content, embed)
    except Exception as e:
        _log_red(f"Discord enqueue failed: {e}")


def _format_reason_text(failure) -> str:
    sample = (failure.attempts[-1].get("body_sample") or "") if failure.attempts else ""
    return f"{failure.provider.title()} {failure.class_tag}: {sample[:200]}"


def _log_red(msg: str) -> None:
    try:
        from intellistock_logger import intellistock_logger
        intellistock_logger.log(msg, "red", service="BACKTEST_CRITICAL_PAUSE")
    except Exception:
        pass


def reset_state() -> None:
    """Called by broker.py's changefeed thread on paused: True->False transition."""
    global _already_alerted, _skip_snapshot_persist
    with _state_lock:
        _already_alerted = False
        _skip_snapshot_persist = False


# Pause-metadata fields handle() writes to BacktestResults (the keys in its
# `payload` minus `status`). Cleared on resume so a run that finishes after
# resuming doesn't carry stale pause_* metadata. Keep in sync with handle().
_PAUSE_RESULT_FIELDS = (
    "pause_reason_tag",
    "pause_reason_text",
    "pause_provider",
    "pause_model",
    "pause_call_site",
    "pause_attempts",
    "pause_bar_time",
    "pause_sample",
    "paused_at",
)


def cleared_pause_fields() -> dict:
    """Return ``{field: None}`` for every pause_* field, for the broker's
    resume update so finished-after-resume runs don't carry stale pause data."""
    return {k: None for k in _PAUSE_RESULT_FIELDS}


# ---------------------------------------------------------------------------
# Round-2 Task 3 (2026-07): credit-exhaustion CLEAN STOP — distinct from the
# paused_llm_critical idle-wait flow above.
#
# Incident: backtest 586767 simulated an ENTIRE month of trading days after
# OpenRouter credits died (195 calls HTTP 402) — no abort, no alert,
# misleading blind results. Task 2 made 402 classify as `insufficient_credits`
# (role-INDEPENDENT fatal). When that LLMCriticalFailure escapes a sim day,
# broker.py's outer-except routes it HERE instead of handle(): the run stops
# cleanly (process exits; container is reaped and the queue row deleted by the
# engine's normal finally path) rather than idling a container for the days a
# credit top-up can take. Resume = operator tops up credits and re-queues; the
# existing resume-date query skips already-processed days.
#
# Partial results are preserved because the row update() MERGES and touches
# ONLY {status, error, paused_at_date}.
# ---------------------------------------------------------------------------


def _human_message_from_failure(failure) -> str:
    """Operator-facing one-liner: the provider's own words when available
    (e.g. 'This request requires more credits ...'), else a self-explanatory
    fallback with the top-up instruction."""
    sample = ""
    try:
        if failure.attempts:
            sample = (failure.attempts[-1].get("body_sample") or "").strip()
    except Exception:
        sample = ""
    if sample:
        return sample[:300]
    return (
        f"{failure.provider} {failure.class_tag} on {failure.model} after "
        f"{len(failure.attempts)} attempts; top up provider credits and "
        "re-queue the backtest"
    )


def _build_credit_pause_payload(failure, sim_date) -> dict:
    """The exact BacktestResults update for a credit-exhaustion pause.

    ONLY three keys — update() merges, so every partial-result field already
    on the row (pnl, trades, progress, logs, ...) survives untouched."""
    if hasattr(sim_date, "strftime"):
        date_str = sim_date.strftime("%Y-%m-%d")
    else:
        date_str = str(sim_date)
    return {
        "status": "paused_credits",
        "error": f"{failure.class_tag}: {_human_message_from_failure(failure)}",
        "paused_at_date": date_str,
    }


def _write_backtest_credit_pause(conn, rrow_id, payload) -> None:
    """DB seam (tests cage this). The retry-on-a-fresh-connection dance is
    gone: the pool owns connections now, so there is no long-lived broker conn
    to go stale mid-run. ``conn`` stays in the signature because the caller
    and ~10 tests pass it; it is unused.

    ``status`` lands on the hot BacktestProgress row and the other two keys
    deep-merge into doc, so partial results are preserved exactly as the
    legacy merge-only update() preserved them.

    The int() coercion mirrors handle(): BacktestResults.id is written as an
    int by the engine, and store.coerce_id accepts an int or a string and
    raises on garbage -- so the old failure mode, a string id silently
    no-opping against RethinkDB's type-strict get() with {skipped: 1}, cannot
    happen rather than merely being avoided."""
    def _do():
        import backtest_result_store as _brs
        _brs.set_status(rrow_id, payload.get("status") or "paused_credits",
                        extra_metadata={k: v for k, v in payload.items()
                                        if k != "status"})

    _do()


def _pause_backtest_on_credit_exhaustion(rrow_id, failure, sim_date, conn) -> dict:
    """Clean-stop pause for credit exhaustion. Called ONCE by broker.py's
    outer-except immediately before the sim-day loop stops, so the single
    call here IS the once-only alert guarantee.

    1. BacktestResults.update({status: 'paused_credits', error, paused_at_date})
       — merge-only, partial results preserved.
    2. ONE operator alert through the `alert_strategy_error` seam (call-time
       lazy import = the where-it's-looked-up seam; tests cage
       live_alerts.alert_strategy_error).

    Each step is independently fault-isolated: a dead DB must not swallow the
    operator page, and a broken alert stack must not lose the row update.
    Returns the payload written (for logging/tests)."""
    payload = _build_credit_pause_payload(failure, sim_date)
    try:
        _write_backtest_credit_pause(conn, rrow_id, payload)
    except Exception as e:
        _log_red(f"credit-pause BacktestResults update failed: {e}")
    try:
        from live_alerts import alert_strategy_error
        instance_id = str((failure.attribution or {}).get("instance_id") or rrow_id)
        alert_strategy_error(
            instance_id=instance_id,
            tag="backtest_credit_exhaustion",
            message=payload["error"],
        )
    except Exception as e:
        _log_red(f"credit-pause alert failed: {e}")
    return payload
