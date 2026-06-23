"""Kalshi prediction-markets feature package.

Import convention matches the rest of backend/: no `backend.` prefix; relies on
`backend/` being on sys.path (set by broker.py startup and backend/tests/conftest.py).

Only the dependency-light pure modules are re-exported here. The client,
ingestion, fees, engine, and db modules pull in cryptography / requests /
rethinkdb and are imported directly by their consumers, so importing this
package stays cheap and side-effect-free for unit tests.
"""
from kalshi.models import (
    OddsQuote,
    Fixture,
    KalshiMarket,
    EdgeFlag,
    KalshiContractPosition,
    KalshiOrderRef,
    KalshiFill,
    KalshiBalance,
)
from kalshi.devig import proportional_devig, power_devig, shin_devig
from kalshi.fair_value import fair_from_odds, blend_fair
from kalshi.edge import implied_from_cents, compute_edge, flag_edges
from kalshi.risk import quarter_kelly_fraction, RiskCaps, size_order, check_caps
from kalshi.clv import compute_clv, summarize_clv
from kalshi.normalize import normalize_team, TeamCrosswalk
from kalshi.telemetry import edge_radar_payload, portfolio_series, settlement_items

__all__ = [
    "OddsQuote", "Fixture", "KalshiMarket", "EdgeFlag", "KalshiContractPosition",
    "KalshiOrderRef", "KalshiFill", "KalshiBalance",
    "proportional_devig", "power_devig", "shin_devig",
    "fair_from_odds", "blend_fair",
    "implied_from_cents", "compute_edge", "flag_edges",
    "quarter_kelly_fraction", "RiskCaps", "size_order", "check_caps",
    "compute_clv", "summarize_clv",
    "normalize_team", "TeamCrosswalk",
    "edge_radar_payload", "portfolio_series", "settlement_items",
]
