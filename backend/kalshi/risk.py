"""Risk manager + sizing. Quarter-Kelly off the MEASURED edge, then clamped by
hard caps enforced pre-trade. Across every published prediction-market project
risk control mattered more than the signal; over-sizing and over-allocating to
correlated events is the most expensive mistake. Defaults are conservative.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def quarter_kelly_fraction(*, edge: float, fair_prob: float) -> float:
    """Binary-contract quarter-Kelly: 0.25 * edge / (1 - fair_prob).

    Full Kelly on a mis-estimated edge is the fastest path to ruin, and the
    edge here IS mis-estimated. Non-positive edge -> no stake.
    """
    if edge <= 0.0:
        return 0.0
    denom = 1.0 - fair_prob
    if denom <= 0.0:
        return 0.0
    return 0.25 * edge / denom


@dataclass
class RiskCaps:
    edge_threshold: float = 0.03            # min net edge to trade
    kelly_fraction: float = 0.25            # quarter-Kelly
    max_contracts_per_market: int = 50
    max_open_exposure_frac: float = 0.60    # fraction of bankroll
    per_league_cap_frac: float = 0.25       # per correlated category
    daily_loss_cap_cents: int = 0           # 0 = disabled
    bankroll_cents: int = 0


def size_order(*, edge: float, fair_prob: float, yes_ask_cents: float, caps: RiskCaps) -> int:
    """Number of YES contracts to buy: quarter-Kelly stake / price, floored,
    clamped to the per-market cap. Returns 0 when there's no stake or no
    bankroll/price."""
    frac = quarter_kelly_fraction(edge=edge, fair_prob=fair_prob)
    if frac <= 0.0 or caps.bankroll_cents <= 0 or yes_ask_cents <= 0:
        return 0
    stake_cents = frac * caps.bankroll_cents
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
