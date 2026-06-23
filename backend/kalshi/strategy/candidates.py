"""Candidate bet generation (pure). Given fused per-market probabilities and the
Kalshi markets available for a fixture, emit candidate bets filtered by the
instance's risk tier, the fee-net edge gate, mutual-exclusion (one side per
ME market), and the per-game bet cap. Multiple bets per game are allowed."""
from __future__ import annotations

from dataclasses import dataclass

from kalshi.strategy.risk_tiers import allowed_markets, max_bets_per_game
from kalshi.edge import compute_edge
from kalshi.fees import fee_as_prob

# Markets where the sides are one mutually-exclusive group — keep only the
# top-edge side (you can't hold contradictory sides of the same group). NOT
# exact_score: distinct scorelines (1-0, 2-1, ...) are independent bets, so
# several can be held (subject to the per-game cap).
_ME_TYPES = {"winner", "double_chance"}


@dataclass(frozen=True)
class Candidate:
    fixture_id: str
    market_ticker: str
    market_type: str
    side: str
    fair: float
    price_cents: int
    edge: float


def generate_candidates(
    fixture_id: str,
    tier: str,
    market_probs: dict,        # {market_type: {side: fair_prob}}
    kalshi_markets: list[dict],  # [{market_ticker, market_type, side, yes_ask_cents}]
    *,
    fee_rate: float,
    edge_threshold: float,
) -> list[Candidate]:
    allowed = allowed_markets(tier)
    cands: list[Candidate] = []
    for m in kalshi_markets:
        mt = m.get("market_type")
        if mt not in allowed:
            continue
        side = m.get("side")
        fair = (market_probs.get(mt) or {}).get(side)
        if fair is None:
            continue
        price = int(m.get("yes_ask_cents", 0))
        fee = fee_as_prob(price, fee_rate)
        e = compute_edge(fair_prob=fair, yes_ask_cents=price, fee=fee)
        if e <= edge_threshold:
            continue
        cands.append(Candidate(fixture_id, m.get("market_ticker", ""), mt, side, fair, price, e))

    cands.sort(key=lambda c: c.edge, reverse=True)

    # Mutual-exclusion: keep only the highest-edge side of each ME market type.
    seen_me: set[str] = set()
    filtered: list[Candidate] = []
    for c in cands:
        if c.market_type in _ME_TYPES:
            if c.market_type in seen_me:
                continue
            seen_me.add(c.market_type)
        filtered.append(c)

    return filtered[: max_bets_per_game(tier)]
