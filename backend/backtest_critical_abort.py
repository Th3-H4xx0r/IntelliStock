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


def _get_conn_and_r():
    from rethinkdb import RethinkDB
    r = RethinkDB()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    return r.connect(host=host, port=port, timeout=10), r


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
        conn, r = _get_conn_and_r()
        try:
            # BacktestInstances.paused=True
            try:
                r.db("IntelliStock").table("BacktestInstances").get(int(backtest_id)).update({
                    "paused": True,
                }).run(conn)
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
                    "paused_at": r.now(),
                    "pause_sample": (failure.attempts[-1].get("body_sample") or "")[:500] if failure.attempts else "",
                }
                # RethinkDB primary keys are type-strict; BacktestResults.id
                # is written as int by the engine (broker.py:5842 and every
                # other writer), so a get("357345") string silently misses
                # (returns {skipped: 1} instead of raising). Coerce to int.
                r.db("IntelliStock").table("BacktestResults").get(int(backtest_id)).update(payload).run(conn)
            except Exception as e:
                _log_red(f"BacktestResults pause status update failed: {e}")
        finally:
            try: conn.close()
            except Exception: pass
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
