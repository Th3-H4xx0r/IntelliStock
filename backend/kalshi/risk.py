"""Risk manager + sizing. Quarter-Kelly off the MEASURED edge, then clamped by
hard caps enforced pre-trade. Across every published prediction-market project
risk control mattered more than the signal; over-sizing and over-allocating to
correlated events is the most expensive mistake. Defaults are conservative.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def quarter_kelly_fraction(*, edge: float, price_cents: float, kelly: float = 0.25) -> float:
    """Fractional-Kelly stake (of bankroll) for a binary contract: kelly * edge /
    (1 - price). `kelly` is the Kelly MULTIPLE (the risk tier's setting — 0.25
    quarter-Kelly default, up to 0.60 for max). Named for the historical default.

    For a YES contract bought at price p (settling to $1), Kelly is (fair - p) /
    (1 - p) — the denominator is 1 - PRICE, not 1 - fair_prob. `edge` is the
    fee-net edge, so the numerator is already conservative. Non-positive edge ->
    no stake."""
    if edge <= 0.0:
        return 0.0
    price = price_cents / 100.0
    denom = 1.0 - price
    if denom <= 0.0:
        return 0.0
    return max(0.0, kelly) * edge / denom


@dataclass
class RiskCaps:
    edge_threshold: float = 0.03            # min net edge to trade
    kelly_fraction: float = 0.25            # Kelly MULTIPLE (tier aggression)
    max_contracts_per_market: int = 50
    max_open_exposure_frac: float = 0.60    # fraction of bankroll
    per_league_cap_frac: float = 0.25       # per correlated category
    daily_loss_cap_cents: int = 0           # 0 = disabled
    bankroll_cents: int = 0
    min_stake_frac: float = 0.0             # floor: any +EV bet stakes >= this frac of bankroll


def size_order(*, edge: float, yes_ask_cents: float, caps: RiskCaps) -> int:
    """Number of YES contracts to buy: Kelly stake (at the tier's kelly_fraction,
    floored at min_stake_frac of bankroll) / price, clamped to the per-market cap.
    Returns 0 when there's no edge or no bankroll/price."""
    frac = quarter_kelly_fraction(edge=edge, price_cents=yes_ask_cents, kelly=caps.kelly_fraction)
    if frac <= 0.0 or caps.bankroll_cents <= 0 or yes_ask_cents <= 0:
        return 0
    stake_cents = max(frac, max(0.0, caps.min_stake_frac)) * caps.bankroll_cents
    contracts = int(math.floor(stake_cents / yes_ask_cents))
    return max(0, min(contracts, caps.max_contracts_per_market))


def check_caps(
    caps: RiskCaps,
    *,
    day_pnl_cents: int,
    open_exposure_frac: float,
    league: str,
    league_exposure_frac: float,
) -> tuple[bool, str]:
    """Pre-trade gate. Returns (ok, reason). reason is '' when ok."""
    if caps.daily_loss_cap_cents and day_pnl_cents <= -abs(caps.daily_loss_cap_cents):
        return False, "daily loss cap hit"
    if open_exposure_frac > caps.max_open_exposure_frac:
        return False, "max open exposure exceeded"
    if league_exposure_frac > caps.per_league_cap_frac:
        return False, f"per-league cap exceeded for {league}"
    return True, ""
