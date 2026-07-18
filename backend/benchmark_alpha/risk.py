"""Flow-adjusted drawdown state machine, mark health, and the legacy order
block (Task 6).

Drawdown is always the positive magnitude
``max(0, 1 - adjusted_equity / peak_adjusted_equity)``. External capital
flows (deposits/withdrawals) adjust the peak in dollar terms so a deposit
never creates a performance high and a withdrawal never fakes a loss.
Dividends, interest, and fees remain economic return/cost; splits and
reorganizations preserve value; unknown flows quarantine peak raises. The
peak is never lowered except by an explicit operator reset record.
"""
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

SOFT_THRESHOLD = 0.08
HARD_THRESHOLD = 0.12
KILL_THRESHOLD = 0.15

# Flow types that adjust capital (signed effect on equity).
_CAPITAL_FLOW_SIGNS = {"deposit": 1.0, "withdrawal": -1.0, "transfer_in": 1.0,
                       "transfer_out": -1.0}
# Flow types that are economic return/cost — no capital adjustment.
_ECONOMIC_FLOWS = frozenset({"dividend", "interest", "fee"})
# Quantity/mark adjustments — value preserved, no cash effect.
_STRUCTURAL_FLOWS = frozenset({"split", "reorganization", "symbol_change"})


class RiskLevel(Enum):
    NORMAL = "NORMAL"
    SOFT = "SOFT"
    HARD = "HARD"
    KILL = "KILL"


@dataclass(frozen=True)
class RiskState:
    peak_adjusted_equity: float
    last_raw_equity: float
    cumulative_external_flow: float
    drawdown_magnitude: float
    level: RiskLevel
    updated_at: datetime
    quarantined_flow: float = 0.0

    @classmethod
    def initial(cls, equity, observed_at):
        equity = float(equity)
        if equity <= 0:
            raise ValueError("initial equity must be positive")
        return cls(peak_adjusted_equity=equity, last_raw_equity=equity,
                   cumulative_external_flow=0.0, drawdown_magnitude=0.0,
                   level=RiskLevel.NORMAL, updated_at=observed_at)


_THRESHOLD_EPSILON = 1e-12  # exact boundaries survive float round-off


def _level_for(drawdown_magnitude):
    if drawdown_magnitude >= KILL_THRESHOLD - _THRESHOLD_EPSILON:
        return RiskLevel.KILL
    if drawdown_magnitude >= HARD_THRESHOLD - _THRESHOLD_EPSILON:
        return RiskLevel.HARD
    if drawdown_magnitude >= SOFT_THRESHOLD - _THRESHOLD_EPSILON:
        return RiskLevel.SOFT
    return RiskLevel.NORMAL


def update_risk_state(previous, raw_equity, external_capital_flows, observed_at):
    """Advance the risk state with a new raw equity observation.

    ``external_capital_flows``: iterable of ``{"type": ..., "amount": ...}``
    reconciled broker cash activities effective since the previous update.
    """
    raw_equity = float(raw_equity)
    capital_delta = 0.0
    quarantined = float(previous.quarantined_flow)
    quarantine_this_update = False
    for flow in external_capital_flows or ():
        ftype = str((flow or {}).get("type", "unknown")).lower()
        amount = abs(float((flow or {}).get("amount", 0.0) or 0.0))
        if ftype in _CAPITAL_FLOW_SIGNS:
            capital_delta += _CAPITAL_FLOW_SIGNS[ftype] * amount
        elif ftype in _ECONOMIC_FLOWS or ftype in _STRUCTURAL_FLOWS:
            continue  # performance/cost or value-preserving — peak untouched
        else:
            quarantined += amount
            quarantine_this_update = True

    # Capital flows shift the peak in dollar terms: a deposit is not a
    # performance high; a withdrawal is not a loss.
    peak = float(previous.peak_adjusted_equity) + capital_delta
    if not quarantine_this_update:
        peak = max(peak, raw_equity)

    drawdown = max(0.0, 1.0 - raw_equity / peak) if peak > 0 else 0.0
    return RiskState(
        peak_adjusted_equity=peak,
        last_raw_equity=raw_equity,
        cumulative_external_flow=float(previous.cumulative_external_flow) + capital_delta,
        drawdown_magnitude=drawdown,
        level=_level_for(drawdown),
        updated_at=observed_at,
        quarantined_flow=quarantined,
    )


def reset_peak(state, new_peak, *, reason, actor, observed_at):
    """Explicit operator peak reset — the ONLY sanctioned way to lower the
    peak. Returns ``(new_state, operator_event)``; the event must be
    persisted as an append-only RethinkDB record."""
    new_peak = float(new_peak)
    if new_peak <= 0:
        raise ValueError("new peak must be positive")
    if not reason or not actor:
        raise ValueError("peak reset requires a reason and an actor")
    drawdown = max(0.0, 1.0 - state.last_raw_equity / new_peak)
    new_state = replace(state, peak_adjusted_equity=new_peak,
                        drawdown_magnitude=drawdown,
                        level=_level_for(drawdown), updated_at=observed_at)
    event = {
        "kind": "peak_reset",
        "old_peak": state.peak_adjusted_equity,
        "new_peak": new_peak,
        "reason": str(reason),
        "actor": str(actor),
        "observed_at": observed_at.isoformat(),
    }
    return new_state, event


@dataclass(frozen=True)
class MarkHealth:
    ok: bool
    entries: tuple


def evaluate_mark_health(held_symbols, marks, now, entry_max_age=60,
                         fallback_max_age=120):
    """Classify every held symbol's current mark: fresh / fallback / stale /
    missing. ``ok`` is True only when every held symbol is fresh or within
    the fallback window."""
    entries = []
    ok = True
    for symbol in held_symbols or ():
        sym = str(symbol).upper()
        mark = (marks or {}).get(sym)
        if mark is None:
            entries.append({"symbol": sym, "status": "missing", "source": None,
                            "age_seconds": None})
            ok = False
            continue
        age = mark.age_seconds(now)
        if age <= entry_max_age:
            status = "fresh"
        elif age <= fallback_max_age:
            status = "fallback"
        else:
            status = "stale"
            ok = False
        entries.append({"symbol": sym, "status": status,
                        "source": mark.source.value, "age_seconds": age})
    return MarkHealth(ok=ok, entries=tuple(entries))


def legacy_live_order_block(instance_id, side, containment_state):
    """Return a block reason when the legacy order path may not submit.

    Containment (``legacy_order_authority_disabled=true``) blocks EVERY
    legacy-generated order on the live instance, both sides — risk reduction
    is available only through the typed alpha/emergency reduce-only path or
    an explicitly external operator action. Malformed/missing containment
    state fails closed (a live instance without readable containment state
    must not trade)."""
    if not isinstance(containment_state, dict):
        return (f"legacy order authority unknown for {instance_id!r} "
                f"(containment state unreadable) — {side} blocked, fail closed")
    if containment_state.get("legacy_order_authority_disabled"):
        return (f"legacy order authority disabled for {instance_id!r} — "
                f"{side} blocked; use the typed reduce-only path")
    return None
