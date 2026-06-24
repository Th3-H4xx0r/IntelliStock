"""Kalshi fee handling. Fees are pulled from live market metadata and baked
into every edge calc and backtest — NEVER hardcoded as a constant, because the
schedule changes and some sports markets run promotional 0% periods. An edge
that ignores fees is fiction.

Kalshi's general trading fee is `ceil(fee_rate * C * P * (1-P))` cents for C
contracts at price P (dollars). Per contract, expressed as a probability (since
one contract settles to $1), that is `fee_rate * P * (1-P)`.
"""
from __future__ import annotations

import math

# Fallback coefficient ONLY for when live metadata is unavailable. resolve_fee_rate
# overrides this from the market's live schedule / promo flags.
DEFAULT_FEE_RATE = 0.07


def resolve_fee_rate(market_meta: dict | None, default: float = DEFAULT_FEE_RATE) -> float:
    """Read the live per-market fee coefficient. Honors explicit 0% promos and
    an explicit `fee_rate` field; otherwise falls back to `default`."""
    if not market_meta:
        return default
    if market_meta.get("fee_waived") or market_meta.get("maker_fee_promo"):
        return 0.0
    rate = market_meta.get("fee_rate")
    if rate is None:
        return default
    return float(rate)


def fee_as_prob(price_cents: float, fee_rate: float = DEFAULT_FEE_RATE) -> float:
    """Per-contract fee as a probability (fraction of $1). Pass the live
    `fee_rate` from `resolve_fee_rate`."""
    p = price_cents / 100.0
    return fee_rate * p * (1.0 - p)


def fee_cents_for_order(price_cents: float, contracts: int, fee_rate: float = DEFAULT_FEE_RATE) -> int:
    """Total order fee in cents, rounded up to the next cent like Kalshi does."""
    p = price_cents / 100.0
    raw_cents = fee_rate * contracts * p * (1.0 - p) * 100.0
    # Round away float noise (175.00000000000003) before ceiling to the cent.
    return int(math.ceil(round(raw_cents, 6)))
