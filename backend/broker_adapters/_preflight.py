"""Preflight checks: Alpaca quota budget + order-pre-submit gates."""

from __future__ import annotations

from dataclasses import dataclass

from broker_adapters.errors import (
    BrokerPreflightBlocked,
    InsufficientBuyingPower,
    PDTRestricted,
    AssetNotTradable,
)


@dataclass
class QuotaBudget:
    rpm_limit: int
    est_rpm: int
    headroom_pct: float
    ok: bool


def alpaca_quota_budget(
    n_symbols: int,
    calls_per_cycle: int,
    cycles_per_min: float,
    rpm_limit: int = 200,
) -> QuotaBudget:
    """Return quota budget. ok=True if >=10% headroom remaining."""
    est = int(n_symbols * calls_per_cycle * cycles_per_min)
    headroom = 1.0 - (est / max(1, rpm_limit))
    return QuotaBudget(
        rpm_limit=rpm_limit,
        est_rpm=est,
        headroom_pct=headroom,
        ok=headroom >= 0.10,
    )


def preflight_order(
    *,
    symbol: str,
    side: str,
    qty: float,
    est_price: float,
    account_equity: float,
    cash_available: float,
    day_trade_count: int,
    asset_tradable: bool,
    asset_fractionable: bool,
    is_fractional: bool,
) -> None:
    """Raise BrokerPreflightBlocked (or typed subclass) if order must not proceed.

    Day-of-entry checks only. Post-submit broker-side rejections use their own
    typed errors (InsufficientBuyingPower, PDTRestricted, etc).
    """
    if not asset_tradable:
        raise AssetNotTradable(f"{symbol} not tradable")
    if is_fractional and not asset_fractionable:
        raise BrokerPreflightBlocked(f"{symbol} not fractionable; got fractional qty={qty}")
    if side.lower() == "buy":
        need = qty * est_price
        if need > cash_available * 1.001:  # small float slack
            raise InsufficientBuyingPower(
                f"{symbol} buy needs ${need:.2f}, cash_available=${cash_available:.2f}"
            )
    if account_equity < 25000.0 and day_trade_count >= 3:
        # Rolling-5-day day-trade is tracked at broker side; this is a safety net.
        raise PDTRestricted(
            f"Account equity ${account_equity:.2f} < $25k and day_trade_count={day_trade_count}"
        )
