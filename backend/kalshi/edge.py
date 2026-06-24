"""Edge detection. edge = fair_prob - (kalshi_implied + fee). Fees are ALWAYS
passed in as a probability pulled live from Kalshi's schedule (see fees.py);
an edge that ignores fees is fiction. Only contracts whose edge clears the
threshold are flagged, sorted strongest-first.
"""
from __future__ import annotations

from kalshi.models import KalshiMarket, EdgeFlag


def implied_from_cents(cents: float) -> float:
    """Kalshi YES price in cents (0..100) -> implied probability (0..1)."""
    return cents / 100.0


def compute_edge(*, fair_prob: float, yes_ask_cents: float, fee: float) -> float:
    """Net +EV edge for buying YES at the ask. fee is a probability."""
    return fair_prob - (implied_from_cents(yes_ask_cents) + fee)


def flag_edges(
    fixture_id: str,
    fair: dict,
    markets: list[KalshiMarket],
    *,
    fee: float,
    threshold: float,
) -> list[EdgeFlag]:
    """Return EdgeFlags for markets whose net edge exceeds the threshold,
    sorted by edge descending."""
    out: list[EdgeFlag] = []
    for m in markets:
        if m.side not in fair:
            continue
        fp = fair[m.side]
        e = compute_edge(fair_prob=fp, yes_ask_cents=m.yes_ask_cents, fee=fee)
        if e > threshold:
            out.append(
                EdgeFlag(
                    fixture_id=fixture_id,
                    market_ticker=m.market_ticker,
                    side=m.side,
                    fair_prob=fp,
                    kalshi_implied=implied_from_cents(m.yes_ask_cents),
                    fee=fee,
                    edge=e,
                )
            )
    return sorted(out, key=lambda f: f.edge, reverse=True)
