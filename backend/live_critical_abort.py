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
# Separate one-shot latch for the DEGRADE (article-enrichment) path. It must be
# distinct from _already_alerted so a degrade never claims the halt latch — else
# a later decision-role failure would short-circuit at the halt latch and NEVER
# halt (the inverse safety bug; review finding, Task 4).
_already_degraded_alerted = False


def _halt_live_trading(*, reason: str, instance_id: str | None = None) -> dict:
    """Isolated for test mocking."""
    from live_kill_switch import halt_live_trading
    return halt_live_trading(reason=reason, instance_id=instance_id)


def _alert_strategy_error(*, instance_id: str, tag: str, message: str) -> None:
    """Isolated for test mocking."""
    from live_alerts import alert_strategy_error
    alert_strategy_error(instance_id=instance_id, tag=tag, message=message)


def handle(*, instance_id: str, failure) -> None:
    """Top-level entry. Idempotent per outcome: a degrade and a halt each have
    their own one-shot latch. Crucially the HALT latch is claimed ONLY by a
    halt-worthy failure, so a prior article-role degrade can never disarm a
    later decision-role halt."""
    global _already_alerted, _already_degraded_alerted

    sample = (failure.attempts[-1].get("body_sample") or "") if failure.attempts else ""

    # 0. Role-scoped degrade (Task 4, incident 2026-06-22/23). A critical LLM
    # failure in an article-ENRICHMENT role (macro_article / company_article /
    # lookback_*) must NOT flip the whole-instance kill switch — those signals
    # are optional and the run should degrade (empty signal), not halt. Only
    # decision roles (None / unknown / everything else, fail-safe) halt.
    #
    # Evaluate role-worthiness FIRST, BEFORE claiming any latch, so the halt
    # latch is never claimed by a degrade (review finding, Task 4). The degrade
    # path gets its OWN one-shot latch (_already_degraded_alerted).
    role = getattr(failure, "role", None)
    try:
        from llm_critical_guard import role_is_halt_worthy, failure_is_role_independent
        # Role-INDEPENDENT fatal classes (e.g. insufficient_credits / HTTP 402)
        # OVERRIDE the article-role degrade — nothing can run without credits, so
        # even an enrichment role must halt. Checked BEFORE the role check.
        _halt_worthy = failure_is_role_independent(failure) or role_is_halt_worthy(role)
    except Exception:
        _halt_worthy = True  # fail-safe: if the guard import breaks, halt.

    if not _halt_worthy:
        with _state_lock:
            if _already_degraded_alerted:
                return
            _already_degraded_alerted = True
        try:
            from intellistock_logger import intellistock_logger
            intellistock_logger.log(
                f"LLM critical in non-decision role {role}: degrading (no halt) "
                f"[{failure.class_tag} on {failure.model} ({failure.provider})]",
                "red", service="LIVE_CRITICAL_ABORT",
            )
        except Exception:
            pass
        try:
            _alert_strategy_error(
                instance_id=instance_id,
                tag="llm_critical_degraded",
                message=(
                    f"{failure.class_tag} in enrichment role '{role}' on "
                    f"{failure.model} ({failure.provider}) — DEGRADING (no halt). "
                    f"Run continues without this signal. Sample: {sample[:200]}"
                ),
            )
        except Exception as e:
            try:
                from intellistock_logger import intellistock_logger
                intellistock_logger.log(
                    f"live_critical_abort degrade alert failed: {e}",
                    "red", service="LIVE_CRITICAL_ABORT",
                )
            except Exception:
                pass
        return

    # Halt-worthy: claim the halt latch now (idempotent — a second halt-worthy
    # failure is a no-op). This latch is deliberately claimed ONLY here, never
    # by the degrade branch above.
    with _state_lock:
        if _already_alerted:
            return
        _already_alerted = True

    # 1. Halt (existing infra: flip runCommand + cancel orders + alert_halt).
    # 1-B (bug-sweep 2026-05-28): scope the AUTOMATIC abort to the FAILING
    # instance only — a paper instance's LLM failure must not halt the
    # real-money 'main' instance or cancel its Robinhood orders. The global
    # blast radius is reserved for the manual `python -m backend.live_kill_switch`.
    halt_summary: dict[str, Any] = {}
    try:
        halt_summary = _halt_live_trading(
            reason=f"LLM critical: {failure.class_tag}",
            instance_id=instance_id,
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
        if failure.class_tag == "insufficient_credits":
            msg += (
                " OpenRouter credits exhausted — top up at "
                "openrouter.ai/settings/credits."
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
    global _already_alerted, _already_degraded_alerted
    with _state_lock:
        _already_alerted = False
        _already_degraded_alerted = False
