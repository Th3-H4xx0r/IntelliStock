"""Pure DTOs for the Kalshi prediction-markets feature.

These are intentionally dependency-light (stdlib only) so the engine's pure
logic — de-vig, edge, sizing, CLV, telemetry — can be unit-tested with no
network, no DB, and no equities-trade-path imports. The Kalshi trade path is
isolated from the equities BrokerAdapter ABC by design (see the design doc).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class OddsQuote:
    """Decimal odds for a 3-way (1X2) soccer market from one book."""
    book: str
    home: float
    draw: float
    away: float


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    sport: str
    league: str
    home: str
    away: str
    kickoff_utc: str  # ISO8601


@dataclass(frozen=True)
class KalshiMarket:
    """A single YES-side contract on a fixture outcome."""
    market_ticker: str
    fixture_id: str
    side: str          # 'home' | 'draw' | 'away'
    yes_ask_cents: int  # 0..100


@dataclass(frozen=True)
class EdgeFlag:
    fixture_id: str
    market_ticker: str
    side: str
    fair_prob: float
    kalshi_implied: float
    fee: float
    edge: float


@dataclass(frozen=True)
class KalshiContractPosition:
    market_ticker: str
    side: str          # 'YES' | 'NO'
    contracts: int
    avg_price_cents: float
    current_price_cents: Optional[float] = None


@dataclass(frozen=True)
class KalshiOrderRef:
    client_order_id: str
    broker_order_id: Optional[str]
    market_ticker: str
    side: str          # 'YES' | 'NO'
    action: str        # 'buy' | 'sell'
    contracts: int
    limit_cents: int
    status: str        # 'resting' | 'filled' | 'canceled' | 'pending'


@dataclass(frozen=True)
class KalshiFill:
    market_ticker: str
    side: str
    action: str
    contracts: int
    price_cents: int
    ts: str


@dataclass(frozen=True)
class KalshiBalance:
    cash_cents: int
    portfolio_value_cents: int
