"""Pure row -> card-payload shapers for the Kalshi UI (web tab, dashboard card,
mobile). Mirrors nexus_telemetry.py: no DB, no clock, no network — just shape
data so the endpoints stay thin and this stays unit-testable.
"""
from __future__ import annotations

from kalshi.models import EdgeFlag, KalshiContractPosition


def edge_radar_payload(flags: list[EdgeFlag], limit: int = 10) -> list[dict]:
    """Top mispriced contracts, strongest edge first."""
    ordered = sorted(flags, key=lambda f: f.edge, reverse=True)[:limit]
    return [
        {
            "market_ticker": f.market_ticker,
            "fixture_id": f.fixture_id,
            "side": f.side,
            "edge": round(f.edge, 4),
            "fair_prob": round(f.fair_prob, 4),
            "kalshi_implied": round(f.kalshi_implied, 4),
        }
        for f in ordered
    ]


def portfolio_series(snapshots: list[dict], max_points: int = 120) -> list[dict]:
    """Equity curve for PortfolioChart. snapshots: [{'ts', 'value_cents'}] in
    chronological order. Downsamples to <= max_points (keeps first+last) and
    converts cents to dollars."""
    pts = [
        {"ts": s["ts"], "value": round(s["value_cents"] / 100.0, 2)}
        for s in snapshots
    ]
    n = len(pts)
    if n <= max_points or max_points <= 1:
        return pts
    step = n / float(max_points)
    idxs = sorted({int(i * step) for i in range(max_points)} | {0, n - 1})
    return [pts[i] for i in idxs if 0 <= i < n]


def _unrealized_cents(p: KalshiContractPosition) -> float | None:
    if p.current_price_cents is None:
        return None
    return (p.current_price_cents - p.avg_price_cents) * p.contracts


def settlement_items(positions: list[KalshiContractPosition]) -> list[dict]:
    """Open positions awaiting settlement, with unrealized P&L (when a current
    price is known)."""
    out = []
    for p in positions:
        u = _unrealized_cents(p)
        out.append(
            {
                "market_ticker": p.market_ticker,
                "side": p.side,
                "contracts": p.contracts,
                "avg_price_cents": p.avg_price_cents,
                "current_price_cents": p.current_price_cents,
                "unrealized_cents": None if u is None else round(u, 2),
            }
        )
    return out
