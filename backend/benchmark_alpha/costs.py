"""Versioned execution-cost and implementation-shortfall model (Task 9A).

Every component is reported separately: directional half-spread, decision
latency drift, market impact, regulatory fees, missed-trade opportunity, and
fixed operating cost (reported both raw and amortized at current capital).
Missing NBBO inputs use a conservative floor and are flagged degraded — they
never become free trades. Shortfall computed without contemporaneous NBBO is
labeled ``reference_proxy`` and is never promotion evidence.
"""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CostModelConfig:
    version: str
    impact_coeff_bps: float
    regulatory_fee_bps: float
    fixed_monthly_cost: float
    missed_trade_bps: float
    half_spread_floor_bps: float = 5.0


@dataclass(frozen=True)
class CostEstimate:
    half_spread_cost: float
    latency_drift_cost: float
    impact_cost: float
    regulatory_fees: float
    missed_trade_cost: float
    total_cost: float
    total_bps: float
    degraded_inputs: bool


def _mid(quote):
    if not isinstance(quote, dict):
        return None
    bid, ask = quote.get("bid"), quote.get("ask")
    if not bid or not ask or bid <= 0 or ask <= 0:
        return None
    # A crossed/locked quote is garbled market data, not a rebate (audit
    # 2026-07-18: bid>ask produced a NEGATIVE half-spread "cost").
    if float(bid) >= float(ask):
        return None
    return (float(bid) + float(ask)) / 2.0


class EquityCostModel:
    def __init__(self, config):
        if not isinstance(config, CostModelConfig):
            raise ValueError("config must be a CostModelConfig")
        self.config = config

    def estimate(self, *, side, notional, decision_quote, submit_quote,
                 adv_notional=None, unfilled_notional=0.0):
        side = str(side).lower()
        notional = float(notional)
        degraded = False

        decision_mid = _mid(decision_quote)
        if decision_mid is not None:
            half_spread = ((float(decision_quote["ask"]) - float(decision_quote["bid"]))
                           / 2.0 / decision_mid) * notional
        else:
            degraded = True
            half_spread = self.config.half_spread_floor_bps / 1e4 * notional

        submit_mid = _mid(submit_quote)
        latency_drift = 0.0
        if decision_mid is not None and submit_mid is not None:
            move = (submit_mid - decision_mid) / decision_mid
            adverse = move if side == "buy" else -move
            latency_drift = max(0.0, adverse) * notional
        else:
            # Missing OR malformed either quote: latency unpriceable —
            # flag degraded, never silently free (audit 2026-07-18).
            degraded = True

        if adv_notional and adv_notional > 0:
            participation = math.sqrt(min(1.0, notional / float(adv_notional)))
        else:
            participation = 1.0  # unknown liquidity: conservative full impact
            degraded = True
        impact = self.config.impact_coeff_bps / 1e4 * participation * notional

        regulatory = (self.config.regulatory_fee_bps / 1e4 * notional
                      if side == "sell" else 0.0)
        missed = self.config.missed_trade_bps / 1e4 * float(unfilled_notional or 0.0)

        total = half_spread + latency_drift + impact + regulatory + missed
        return CostEstimate(
            half_spread_cost=half_spread,
            latency_drift_cost=latency_drift,
            impact_cost=impact,
            regulatory_fees=regulatory,
            missed_trade_cost=missed,
            total_cost=total,
            total_bps=(total / notional * 1e4) if notional else 0.0,
            degraded_inputs=degraded,
        )

    def amortized_fixed_bps(self, nav):
        nav = float(nav)
        if nav <= 0:
            raise ValueError("nav must be positive")
        return self.config.fixed_monthly_cost / nav * 1e4


@dataclass(frozen=True)
class ShortfallRecord:
    shortfall_dollars: float
    shortfall_bps: float
    basis: str                      # measured_nbbo | reference_proxy
    decision_to_fill_seconds: float | None
    promotion_eligible: bool


class ImplementationShortfall:
    def observe(self, *, side, decision_reference_price, fills,
                nbbo_at_decision=None, decision_to_fill_seconds=None):
        side = str(side).lower()
        for fill in fills or ():
            if fill.get("qty") is None or fill.get("price") is None:
                raise ValueError(f"malformed fill (qty/price required): {fill}")
        qty = sum(float(f["qty"]) for f in fills or ())
        if qty <= 0:
            raise ValueError("shortfall requires at least one fill")
        vwap = sum(float(f["qty"]) * float(f["price"]) for f in fills) / qty

        nbbo_ref = None
        if isinstance(nbbo_at_decision, dict):
            nbbo_ref = (nbbo_at_decision.get("ask") if side == "buy"
                        else nbbo_at_decision.get("bid"))
        if nbbo_ref:
            reference = float(nbbo_ref)
            basis = "measured_nbbo"
        else:
            # Fill-versus-stale-reference drift is NOT broker slippage; it is
            # a diagnostic proxy only (July 18 ~32.1 bps study).
            reference = float(decision_reference_price)
            basis = "reference_proxy"
        if reference <= 0:
            raise ValueError("shortfall reference price must be positive")

        signed = (vwap - reference) if side == "buy" else (reference - vwap)
        return ShortfallRecord(
            shortfall_dollars=signed * qty,
            shortfall_bps=signed / reference * 1e4,
            basis=basis,
            decision_to_fill_seconds=decision_to_fill_seconds,
            promotion_eligible=(basis == "measured_nbbo"),
        )
