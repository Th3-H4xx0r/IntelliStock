"""Pure helpers for the per-symbol GATE REFUSAL register.

`broker.py` records a decision into `backtest_decisions` only when
`not _trade_skipped_no_price`, and roughly two dozen gate paths either set that
flag or `continue` past the record site. The comment at the min-position floor
states the effect outright::

    _trade_skipped_no_price = True  # reuse flag to prevent recording

So `backtest_decisions` holds what SURVIVED the gates, and the names a gate
refused — the population worth studying — were simply absent. That is why "0 of
134 grants cleared the min-position floor" had to be found by a human reading
logs.

Rather than instrument every refusing path (a large diff in the code that places
real orders), the register is OPTIMISTIC: arm a refusal as soon as a non-hold
decision exists, clear it if the decision is actually recorded, and whatever
remains was refused — by whichever gate fired, with no change to control flow.

Kept free of any DB / threading so it unit-tests in isolation; `broker.py`
itself is a CLI script and is not importable. Same reasoning as
``bot_decision_log``.
"""
from __future__ import annotations

DEFAULT_REASON = "gate"


def primary_strategy_of(strategy_summary):
    """The heaviest-weighted contributing strategy, or None.

    Mirrors how `broker.py` picks the primary driver for the decision log, so a
    refusal is attributed the same way a fill would have been.
    """
    entries = [s for s in (strategy_summary or []) if isinstance(s, dict)]
    if not entries:
        return None
    best = max(entries, key=lambda item: float(item.get("weight") or 0.0))
    return best.get("strategy")


def build_refusal(*, timestamp, symbol, action, decision, normalized,
                  strategy_summary=None):
    """The armed refusal record, or None when there is nothing to refuse.

    A HOLD is not a refusal — it is the system doing exactly what it decided.
    """
    try:
        decision = int(decision or 0)
    except (TypeError, ValueError):
        decision = 0
    if decision == 0:
        return None
    try:
        score = round(float(normalized), 4) if normalized is not None else None
    except (TypeError, ValueError):
        score = None
    return {
        "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat")
        else (str(timestamp) if timestamp else None),
        "symbol": str(symbol or "").upper(),
        "action": str(action or "").strip().lower(),
        "decision": decision,
        "normalized_score": score,
        "primary_strategy": primary_strategy_of(strategy_summary),
    }


def finalize_refusal(pending, reason=None):
    """Stamp the gate that fired. An unlabelled path still records the refusal —
    losing the fact because the reason is unknown would be the worse error."""
    if not pending:
        return None
    stamped = dict(pending)
    stamped["reason"] = reason or DEFAULT_REASON
    return stamped
