"""Backtest-mode critical-LLM-failure handler.

Called from broker.py's outer except block when LLMCriticalFailure bubbles
up. Responsibilities:
  1. Idempotently mark BacktestResults.status='aborted_llm_failure'
  2. Set _skip_snapshot_persist module flag (read by strategy_cache_persistence)
  3. Enqueue rich Discord embed to #backtests
"""
from __future__ import annotations

import os
import threading
from typing import Any


_state_lock = threading.RLock()
_already_alerted = False
_skip_snapshot_persist = False


def _get_conn_and_r():
    """Return (conn, r_module). Isolated for test mocking."""
    from rethinkdb import RethinkDB
    r = RethinkDB()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    conn = r.connect(host=host, port=port, timeout=10)
    return conn, r


def _enqueue_discord(channel: str, content: str, embed: dict) -> None:
    """Isolated for test mocking."""
    from discord_sender import enqueue_discord_message
    from interactive_utils import get_conn
    conn = get_conn()
    try:
        enqueue_discord_message(conn, channel, content, embed=embed)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _build_embed(*, backtest_id: str, instance_id: str, failure) -> dict:
    """Build the Discord embed payload. See design Section 5."""
    first_attempt = failure.attempts[0] if failure.attempts else {}
    last_attempt = failure.attempts[-1] if failure.attempts else {}
    sample_raw = (last_attempt.get("body_sample") or "")[:500]
    if len(last_attempt.get("body_sample") or "") > 500:
        sample_raw += "...[truncated]"

    embed = {
        "title": "Backtest aborted: LLM critical failure",
        "color": 0xE74C3C,
        "fields": [
            {"name": "backtest_id", "value": str(backtest_id), "inline": True},
            {"name": "instance_id", "value": str(instance_id), "inline": True},
            {"name": "class", "value": failure.class_tag, "inline": True},
            {"name": "provider", "value": failure.provider, "inline": True},
            {"name": "model", "value": failure.model, "inline": True},
            {"name": "call_site",
             "value": str(failure.attribution.get("call_site") or "unknown"),
             "inline": True},
            {"name": "attempts",
             "value": f"{len(failure.attempts)} (1 + {max(0, len(failure.attempts)-1)} retries)",
             "inline": False},
            {"name": "first_attempt_ts",
             "value": _fmt_ts(first_attempt.get("ts")),
             "inline": True},
            {"name": "last_attempt_ts",
             "value": _fmt_ts(last_attempt.get("ts")),
             "inline": True},
            {"name": "sample", "value": f"```{sample_raw}```", "inline": False},
            {"name": "next steps",
             "value": (
                 f"Verify {failure.provider} resource status before rerun. "
                 f"Failed run cached as misses - "
                 f"`python scripts/clear_backtest_state.py {backtest_id} --apply` to wipe."
             ),
             "inline": False},
        ],
        "footer": {"text": "IntelliStock backtest critical-guard"},
    }
    # Pipe through live_alerts._scrub_embed for redaction.
    try:
        from live_alerts import _scrub_embed
        embed = _scrub_embed(embed) or embed
    except Exception:
        pass
    return embed


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "unknown"
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def handle(*, backtest_id: str, instance_id: str, failure) -> None:
    """Top-level entry. Idempotent: second call is a no-op."""
    global _already_alerted, _skip_snapshot_persist
    with _state_lock:
        if _already_alerted:
            return
        _already_alerted = True
        _skip_snapshot_persist = True

    # 1. BacktestResults update
    try:
        conn, r = _get_conn_and_r()
        try:
            r.db("IntelliStock").table("BacktestResults").get(str(backtest_id)).update(
                lambda row: r.branch(
                    row["status"].default("").ne("aborted_llm_failure"),
                    {
                        "status": "aborted_llm_failure",
                        "aborted_at": r.now(),
                        "abort_reason": failure.class_tag,
                        "abort_sample": (failure.attempts[-1].get("body_sample") or "")[:500] if failure.attempts else "",
                        "abort_attempts": failure.attempts,
                        "abort_provider": failure.provider,
                        "abort_model": failure.model,
                    },
                    {},
                )
            ).run(conn)
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as e:
        # Logging only - never let the abort handler itself crash the worker.
        try:
            from intellistock_logger import intellistock_logger
            intellistock_logger.log(
                f"backtest_critical_abort BacktestResults update failed: {e}",
                "red", service="BACKTEST_CRITICAL_ABORT",
            )
        except Exception:
            pass

    # 2. Discord enqueue
    try:
        content = (
            f"BACKTEST ABORT [{backtest_id}] {failure.class_tag} after "
            f"{len(failure.attempts)} attempts on {failure.model} ({failure.provider})"
        )
        embed = _build_embed(backtest_id=backtest_id, instance_id=instance_id, failure=failure)
        _enqueue_discord("backtests", content, embed)
    except Exception as e:
        try:
            from intellistock_logger import intellistock_logger
            intellistock_logger.log(
                f"backtest_critical_abort Discord enqueue failed: {e}",
                "red", service="BACKTEST_CRITICAL_ABORT",
            )
        except Exception:
            pass


def reset_state() -> None:
    """For tests only. Clears the idempotency flags."""
    global _already_alerted, _skip_snapshot_persist
    with _state_lock:
        _already_alerted = False
        _skip_snapshot_persist = False
