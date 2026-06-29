"""Capital allocator (pure). Greedy-by-opportunity-score fill with a ¼-Kelly
per-bet cap, the risk caps, and a forward RESERVE: when a better opportunity is
expected soon, hold back ``reserve_frac`` rather than spend it on a marginal bet
now. Bounded (not a free solver) so it can't misfire on real money. Candidates
that don't fit are effectively queued for when capital frees up.
"""
from __future__ import annotations

from kalshi.risk import quarter_kelly_fraction


def allocate(candidates, *, bankroll_cents, caps, reserve_frac, expected_better_soon):
    """candidates: [{id, score, edge, price_cents, ...}]. Returns a list of
    allocations [{id, stake_cents, contracts, price_cents}], highest-score first,
    fitting within (bankroll - reserve)."""
    reserve = int(bankroll_cents * max(0.0, min(1.0, reserve_frac))) if expected_better_soon else 0
    deployable = max(0, bankroll_cents - reserve)
    per_market_cap = int(getattr(caps, "max_contracts_per_market", 50))
    kelly = float(getattr(caps, "kelly_fraction", 0.25))           # tier aggression
    min_stake_frac = max(0.0, float(getattr(caps, "min_stake_frac", 0.0)))
    per_bet_cap_frac = max(0.0, float(getattr(caps, "per_bet_cap_frac", 0.0)))  # ceiling (0 = off)

    allocations = []
    spent = 0
    for c in sorted(candidates, key=lambda x: x.get("score", 0.0), reverse=True):
        price = int(c.get("price_cents", 0))
        if price <= 0:
            continue
        frac = quarter_kelly_fraction(edge=c.get("edge", 0.0), price_cents=price, kelly=kelly)
        if frac <= 0.0:
            continue
        # Floor any +EV bet at min_stake_frac of bankroll so small-edge bets aren't
        # dust (the risk tier controls how big). Still bounded by per-market + deployable.
        eff = max(frac, min_stake_frac)
        if per_bet_cap_frac > 0.0:
            eff = min(eff, per_bet_cap_frac)             # cap single-bet exposure
        stake = int(eff * bankroll_cents)
        contracts = min(stake // price, per_market_cap)
        if contracts <= 0:
            continue
        cost = contracts * price
        if spent + cost > deployable:
            continue  # doesn't fit — effectively queued for later capital
        spent += cost
        allocations.append({"id": c["id"], "stake_cents": cost, "contracts": contracts, "price_cents": price})
    return allocations
