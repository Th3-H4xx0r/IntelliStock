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
    min_price_cents: int = 15,
    max_price_cents: int = 90,
    draw_min_edge: float = 0.10,
    collect_skips: bool = False,
):
    """Returns the candidate list. When collect_skips=True, returns
    (candidates, skips) where skips records every CONSIDERED-but-rejected market with
    a reason — so the decision log can show what the bot evaluated and why it passed."""
    allowed = allowed_markets(tier)
    cands: list[Candidate] = []
    skips: list[dict] = []

    def _skip(m, side, fair, price, edge, reason):
        if collect_skips:
            skips.append({"market_ticker": m.get("market_ticker", ""), "side": side,
                          "market_type": m.get("market_type"), "fair": fair,
                          "price_cents": price, "edge": edge, "reason": reason})

    for m in kalshi_markets:
        mt = m.get("market_type")
        if mt not in allowed:
            continue
        side = m.get("side")
        fair = (market_probs.get(mt) or {}).get(side)
        if fair is None:
            continue
        price = int(m.get("yes_ask_cents", 0))
        # Price-band gate (favorite-longshot tax): skip cheap longshots — in paper
        # EVERY sub-15c bet lost (0/9, -$33) — and near-certain favorites where
        # fees eat the thin upside. The losses were 100% concentrated below this band.
        if price < min_price_cents or price > max_price_cents:
            _skip(m, side, fair, price, None, f"price {price}c outside band [{min_price_cents},{max_price_cents}]")
            continue
        fee = fee_as_prob(price, fee_rate)
        e = compute_edge(fair_prob=fair, yes_ask_cents=price, fee=fee)
        # Draws were model-overconfident (0/12, -$58). Don't hard-ban (research:
        # that's model miscalibration, not market overpricing) but demand a much
        # larger edge so we only take a draw when the disagreement is big.
        gate = max(edge_threshold, draw_min_edge) if side == "draw" else edge_threshold
        if e <= gate:
            _skip(m, side, fair, price, e, f"edge {e * 100:.1f}% <= bar {gate * 100:.1f}%")
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

    result = filtered[: max_bets_per_game(tier)]
    return (result, skips) if collect_skips else result
