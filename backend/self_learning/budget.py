"""The budget accountant: reserve before you spend.

An autonomous loop with a credit card needs a ceiling that binds BEFORE the
money is committed, not a tally that notices afterwards. So an experiment
reserves its estimated cost up front and cannot launch if the reserve exceeds
what remains; the reserve is released or converted to actual spend when the run
finishes.

The ceiling is a HARD stop, and it fails CLOSED: an unreadable ledger means no
spending, not unlimited spending.
"""
from __future__ import annotations

from dataclasses import dataclass

from self_learning.timeline import to_naive_utc

RESERVED = "reserved"
SPENT = "spent"
RELEASED = "released"


@dataclass(frozen=True)
class BudgetState:
    daily_limit_usd: float
    monthly_limit_usd: float
    spent_today_usd: float
    spent_month_usd: float
    reserved_usd: float

    @property
    def daily_remaining(self) -> float:
        return max(0.0, self.daily_limit_usd - self.spent_today_usd - self.reserved_usd)

    @property
    def monthly_remaining(self) -> float:
        return max(0.0, self.monthly_limit_usd - self.spent_month_usd - self.reserved_usd)

    @property
    def remaining(self) -> float:
        return min(self.daily_remaining, self.monthly_remaining)

    def to_doc(self) -> dict:
        return {
            "daily_limit_usd": self.daily_limit_usd,
            "monthly_limit_usd": self.monthly_limit_usd,
            "spent_today_usd": round(self.spent_today_usd, 4),
            "spent_month_usd": round(self.spent_month_usd, 4),
            "reserved_usd": round(self.reserved_usd, 4),
            "daily_remaining_usd": round(self.daily_remaining, 4),
            "monthly_remaining_usd": round(self.monthly_remaining, 4),
            "remaining_usd": round(self.remaining, 4),
        }


def state_from_ledger(ledger, *, now_iso, daily_limit_usd, monthly_limit_usd
                      ) -> BudgetState:
    """Roll a ledger of debits into today's and this month's spend.

    A row whose timestamp cannot be parsed counts toward BOTH periods. Silently
    dropping it would let a clock bug become free money.
    """
    now = to_naive_utc(now_iso)
    today = now.date() if now else None
    spent_today = spent_month = reserved = 0.0
    for row in (ledger or []):
        if not isinstance(row, dict):
            continue
        try:
            amount = float(row.get("amount_usd") or 0.0)
        except (TypeError, ValueError):
            continue
        status = str(row.get("status") or SPENT)
        if status == RELEASED:
            continue
        if status == RESERVED:
            reserved += amount
            continue
        stamp = to_naive_utc(row.get("at"))
        if stamp is None or today is None:
            spent_today += amount
            spent_month += amount
            continue
        if stamp.date() == today:
            spent_today += amount
        if (stamp.year, stamp.month) == (today.year, today.month):
            spent_month += amount
    return BudgetState(
        daily_limit_usd=float(daily_limit_usd or 0.0),
        monthly_limit_usd=float(monthly_limit_usd or 0.0),
        spent_today_usd=spent_today, spent_month_usd=spent_month,
        reserved_usd=reserved)


def can_afford(state: BudgetState, amount_usd) -> dict:
    """Whether a reservation of `amount_usd` fits under both ceilings."""
    if state is None:
        return {"allowed": False, "reason": "no budget state — failing closed",
                "remaining_usd": 0.0}
    try:
        amount = float(amount_usd or 0.0)
    except (TypeError, ValueError):
        return {"allowed": False, "reason": f"cost {amount_usd!r} is not numeric",
                "remaining_usd": state.remaining}
    if amount < 0:
        return {"allowed": False, "reason": "a negative cost is not a cost",
                "remaining_usd": state.remaining}
    if state.daily_limit_usd <= 0 or state.monthly_limit_usd <= 0:
        return {"allowed": False,
                "reason": ("no spend limit is configured — the loop will not "
                           "spend money until a ceiling exists"),
                "remaining_usd": 0.0}
    if amount > state.daily_remaining:
        return {"allowed": False,
                "reason": (f"${amount:.2f} exceeds the ${state.daily_remaining:.2f} "
                           f"remaining today"),
                "remaining_usd": state.remaining}
    if amount > state.monthly_remaining:
        return {"allowed": False,
                "reason": (f"${amount:.2f} exceeds the ${state.monthly_remaining:.2f} "
                           f"remaining this month"),
                "remaining_usd": state.remaining}
    return {"allowed": True, "reason": "within budget",
            "remaining_usd": state.remaining}


def reservation(*, experiment_id, amount_usd, now_iso, kind="backtest") -> dict:
    return {"id": f"reserve|{experiment_id}",
            "experiment_id": str(experiment_id), "kind": kind,
            "amount_usd": round(float(amount_usd or 0.0), 6),
            "status": RESERVED, "at": str(now_iso)}


def settle(reservation_doc, *, actual_usd, now_iso) -> dict:
    """Convert a reservation into actual spend."""
    return {**(reservation_doc or {}),
            "amount_usd": round(float(actual_usd or 0.0), 6),
            "status": SPENT, "settled_at": str(now_iso)}


def release(reservation_doc, *, now_iso, reason="") -> dict:
    """Give a reservation back — the experiment never ran."""
    return {**(reservation_doc or {}), "status": RELEASED,
            "released_at": str(now_iso), "release_reason": str(reason)}
