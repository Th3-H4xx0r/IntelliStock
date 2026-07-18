"""Non-trading research portfolio policy (Task 9A).

Supplies the same cash/SPY-residual/name/sector/turnover/wash-sale
constraints the production allocator will later enforce, so Task 16 can
evaluate costed 40/60/80 portfolios BEFORE production Tasks 12-15 exist.
It has no broker, WAL, runtime, or order-intent dependency and produces
target weights only. Initially only fresh current-cycle DIRECT forecasts
are tradable; everything else is rejected with an auditable reason.
"""
from dataclasses import dataclass, field
from datetime import timedelta

from benchmark_alpha.types import EvidenceClass

CASH_WEIGHT = 0.02
MAX_NAMES = 10
MAX_NAME_WEIGHT = 0.08
MAX_SECTOR_WEIGHT = 0.20
WASH_SALE_BLOCK_DAYS = 31


@dataclass(frozen=True)
class ResearchTaxState:
    """Conservative research tax proxy: loss-sale dates per symbol."""
    loss_sales: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ResearchTarget:
    weights: dict
    cash_weight: float
    rejections: tuple
    turnover: float
    turnover_capped: bool
    tax_opportunity_cost_bps: float
    # Weight not reachable this session (e.g. turnover-capped transitions);
    # it sits in cash until a later session — explicit, never implicit.
    unallocated_weight: float = 0.0


class ResearchPortfolioPolicy:
    def build(self, forecasts, *, active_cap, as_of, sectors=None,
              tax_state=None, current_weights=None, turnover_cap=None,
              cost_model=None):
        sectors = sectors or {}
        rejections = []
        tax_cost_bps = 0.0

        eligible = []
        for forecast in forecasts or ():
            if not forecast.eligibility:
                rejections.append({"symbol": forecast.symbol,
                                   "reason": "ineligible_forecast"})
                continue
            if forecast.evidence_class is not EvidenceClass.DIRECT:
                rejections.append({
                    "symbol": forecast.symbol,
                    "reason": f"evidence_class_{forecast.evidence_class.value.lower()}_research_only"})
                continue
            if forecast.as_of != as_of:
                rejections.append({"symbol": forecast.symbol,
                                   "reason": "not_current_cycle"})
                continue
            loss_sale = (tax_state.loss_sales.get(forecast.symbol)
                         if tax_state else None)
            if loss_sale is not None and \
                    as_of < loss_sale + timedelta(days=WASH_SALE_BLOCK_DAYS):
                rejections.append({"symbol": forecast.symbol,
                                   "reason": "wash_sale_window_block"})
                tax_cost_bps += max(0.0, forecast.expected_excess_return) \
                    * MAX_NAME_WEIGHT * 1e4
                continue
            eligible.append(forecast)

        # Conservative ranking with deterministic symbol tie-breaking.
        eligible.sort(key=lambda f: (-f.expected_excess_return, f.symbol))

        weights = {}
        sector_used = {}
        active_used = 0.0
        for forecast in eligible:
            if len(weights) >= MAX_NAMES or active_used >= active_cap - 1e-12:
                rejections.append({"symbol": forecast.symbol,
                                   "reason": "active_capacity_full"})
                continue
            sector = sectors.get(forecast.symbol)
            sector_room = (MAX_SECTOR_WEIGHT - sector_used.get(sector, 0.0)
                           if sector is not None else MAX_NAME_WEIGHT)
            weight = min(MAX_NAME_WEIGHT, active_cap - active_used, sector_room)
            if weight <= 1e-9:
                rejections.append({"symbol": forecast.symbol,
                                   "reason": "sector_cap"})
                continue
            weights[forecast.symbol] = weight
            active_used += weight
            if sector is not None:
                sector_used[sector] = sector_used.get(sector, 0.0) + weight

        weights["SPY"] = max(0.0, 1.0 - CASH_WEIGHT - active_used)

        turnover = 0.0
        turnover_capped = False
        if current_weights:
            all_syms = set(weights) | set(current_weights)
            gross = sum(abs(weights.get(s, 0.0) - float(current_weights.get(s, 0.0)))
                        for s in all_syms)
            turnover = gross
            if turnover_cap is not None and gross > turnover_cap:
                scale = turnover_cap / gross
                weights = {
                    s: float(current_weights.get(s, 0.0))
                    + (weights.get(s, 0.0) - float(current_weights.get(s, 0.0))) * scale
                    for s in all_syms}
                weights = {s: w for s, w in weights.items() if w > 1e-12}
                turnover = turnover_cap
                turnover_capped = True

        unallocated = max(0.0, 1.0 - CASH_WEIGHT - sum(weights.values()))
        return ResearchTarget(
            weights=weights, cash_weight=CASH_WEIGHT,
            rejections=tuple(rejections), turnover=turnover,
            turnover_capped=turnover_capped,
            tax_opportunity_cost_bps=tax_cost_bps,
            unallocated_weight=unallocated)
