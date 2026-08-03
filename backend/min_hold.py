"""Minimum holding period — the most direct lever on turnover.

Measured position (2026-08-03): the live book trades ~290% of NAV per month at a
notional-weighted spread of 45.6 bps, which is 23.2 bps one-way and **8.07%/yr
of drag** before any signal has to prove itself. Novy-Marx & Velikov (RFS 2016)
find anomalies under ~50% monthly turnover generate significant net spreads and
few above it do; this book is ~6x over that line. Nothing about the graph, the
LLM overlay or the scoring changes that arithmetic — only trading less does.

A holding floor attacks it directly: a position bought today cannot be sold on a
discretionary signal for `min_days`, so the churn that produced 290%/month
cannot recur regardless of how noisy the signal is.

Two supporting results say this costs less alpha than it looks:

  * Cohen & Frazzini (2008) — skipping a full week before trading retains
    **93%** of the economic-links alpha (1.578%/mo -> 1.464%/mo).
  * Ali & Hirshleifer (2020) — at 30.3 bps the 12-month version of the signal
    nets **53.9 bp/mo** against the 1-month version's 38.2, i.e. 41% MORE net
    alpha for a third of the trading.

It also unlocks the other lever. Limit orders only work when you are not in a
hurry; on a 30-day floor, waiting hours for a fill costs ~nothing, and that is
what turns 22.8 bps of crossed spread into ~0.

DEFAULT OFF. `min_hold_enabled` is False unless a config sets it, so an
untouched doc-179 parses to a disabled gate and every call is a no-op.

WHAT THIS MUST NEVER DO
-----------------------
Block a risk exit. A holding floor that traps you in a loser costs far more than
the commissions it saves, and this codebase already treats `decision == -1` as
reduce-only by construction. Stops, trailing stops, circuit breakers, forced
exits, deep-loser protection and nexus sell-enforcement are all exempt, and
`sell_is_blocked` takes `is_risk_exit` as a REQUIRED argument so a caller cannot
forget to classify.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

__all__ = [
    "MinHoldConfig",
    "min_hold_config",
    "sell_is_blocked",
    "holding_days",
]


@dataclass(frozen=True)
class MinHoldConfig:
    """Parsed holding-floor levers. Never raises out of the parser — a raise
    would silently disable the gate at every call site at once."""

    enabled: bool = False
    #: Calendar days, not trading days. 30 calendar ~ 21 trading sessions.
    #: Calendar is deliberate: it needs no market calendar, cannot drift out of
    #: sync with one, and errs toward holding slightly longer.
    min_days: int = 30


def _i(cfg, key, default):
    try:
        value = cfg.get(key, default)
        if value is None or value == "":
            return int(default)
        return int(value)
    except (TypeError, ValueError, AttributeError):
        return int(default)


def min_hold_config(config) -> MinHoldConfig:
    """Parse the holding floor off a strategy config. Absent keys give the
    documented defaults and ``enabled`` is False unless explicitly set."""
    cfg = config or {}
    raw = cfg.get("min_hold_enabled", False)
    enabled = str(raw).strip().lower() in {"1", "true", "yes", "on"} \
        if not isinstance(raw, bool) else raw
    days = _i(cfg, "min_hold_days", 30)
    return MinHoldConfig(enabled=bool(enabled), min_days=max(0, days))


def _as_utc(value):
    """Coerce a timestamp to an aware UTC datetime, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def holding_days(entry_ts, now) -> float | None:
    """Calendar days a position has been held, or None if undatable."""
    start, end = _as_utc(entry_ts), _as_utc(now)
    if start is None or end is None:
        return None
    return (end - start).total_seconds() / 86400.0


def sell_is_blocked(cfg: MinHoldConfig, *, entry_ts, now,
                    is_risk_exit: bool) -> tuple[bool, str]:
    """Should this SELL be suppressed by the holding floor?

    Returns ``(blocked, reason)``; the reason is logged either way so an
    operator can see the gate working rather than inferring it from absent
    trades.

    ``is_risk_exit`` is required and not defaulted: every caller must classify
    the sell, because the one behaviour this must never exhibit is trapping a
    position that a stop wants out of.
    """
    if not cfg.enabled or cfg.min_days <= 0:
        return False, "disabled"
    if is_risk_exit:
        # The whole point. A budget that traps you in a loser costs more than
        # the spread it saves.
        return False, "risk_exit_exempt"

    held = holding_days(entry_ts, now)
    if held is None:
        # FAIL OPEN, deliberately. An undatable position is one we did not
        # record an entry for -- a pre-existing holding, a restart that lost
        # state, an externally-opened position. Blocking its exit would trap
        # capital indefinitely with no way for the operator to know why, which
        # is strictly worse than letting one extra sell through. The gate is a
        # cost optimisation; it must never become a liquidity trap.
        return False, "no_entry_timestamp"
    if held < 0:
        # Clock skew or a bad record; treat as undatable rather than trusting it.
        return False, "negative_age"
    if held >= cfg.min_days:
        return False, f"held_{held:.1f}d"
    return True, f"min_hold_{held:.1f}d_of_{cfg.min_days}d"
