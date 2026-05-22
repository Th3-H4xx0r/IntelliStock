"""Live-mode critical-LLM-failure handler.

Called from broker.py's outer except block when LLMCriticalFailure bubbles
up in live mode. Wraps existing infra:
  - live_kill_switch.halt_live_trading() - flips runCommand=False + cancels orders
  - live_alerts.alert_strategy_error(tag="llm_critical") - adds diagnostic context
    on top of the terse alert_halt that halt_live_trading already fires.
"""
from __future__ import annotations

import threading
from typing import Any


_state_lock = threading.RLock()
_already_alerted = False


def _halt_live_trading(*, reason: str) -> dict:
    """Isolated for test mocking."""
    from live_kill_switch import halt_live_trading
    return halt_live_trading(reason=reason)


def _alert_strategy_error(*, instance_id: str, tag: str, message: str) -> None:
    """Isolated for test mocking."""
    from live_alerts import alert_strategy_error
    alert_strategy_error(instance_id=instance_id, tag=tag, message=message)


def handle(*, instance_id: str, failure) -> None:
    """Top-level entry. Idempotent: second call is a no-op for both halt
    and alert."""
    global _already_alerted
    with _state_lock:
        if _already_alerted:
            return
        _already_alerted = True

    sample = (failure.attempts[-1].get("body_sample") or "") if failure.attempts else ""

    # 1. Halt (existing infra: flip runCommand + cancel orders + alert_halt)
    halt_summary: dict[str, Any] = {}
    try:
        halt_summary = _halt_live_trading(
            reason=f"LLM critical: {failure.class_tag}"
        ) or {}
    except Exception as e:
        try:
            from intellistock_logger import intellistock_logger
            intellistock_logger.log(
                f"live_critical_abort halt_live_trading failed: {e}",
                "red", service="LIVE_CRITICAL_ABORT",
            )
        except Exception:
            pass

    # 2. Strategy-error alert with diagnostic detail
    try:
        msg = (
            f"{failure.class_tag} after {len(failure.attempts)} attempts on "
            f"{failure.model} ({failure.provider}). Sample: {sample[:200]}. "
            f"Halt summary: {halt_summary.get('instances_halted', 0)} instances, "
            f"{halt_summary.get('orders_canceled', 0)} orders canceled."
        )
        _alert_strategy_error(
            instance_id=instance_id, tag="llm_critical", message=msg,
        )
    except Exception as e:
        try:
            from intellistock_logger import intellistock_logger
            intellistock_logger.log(
                f"live_critical_abort alert_strategy_error failed: {e}",
                "red", service="LIVE_CRITICAL_ABORT",
            )
        except Exception:
            pass


def reset_state() -> None:
    """For tests only."""
    global _already_alerted
    with _state_lock:
        _already_alerted = False
