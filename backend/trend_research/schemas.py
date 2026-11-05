"""Structured outputs of the four TR research stages (spec §7.2, plan Task 6).

Each model is the ``output_type`` of one ``StageCaller.call``; the ledger
stores ``model_dump()`` of each. ``book.target_book`` reads the dumped
``ResearchNote`` and ``BookProposal`` field names directly, so renaming a
field here breaks the lane.

Ranges are enforced by validation: a reply outside them fails the stage
(``StageFailed`` after retries) rather than being clipped silently.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = ["Trend", "TrendScan", "Candidate", "CandidateList", "ResearchNote", "Pick",
           "BookProposal", "TREND_CATEGORIES", "TREND_STATES", "REGIMES"]

TrendCategory = Literal["tech", "geopolitics", "economy_rates", "consumer_health"]
TrendState = Literal["emerging", "strengthening", "mature", "fading", "broken"]
Regime = Literal["risk_on", "neutral", "risk_off", "crisis"]
PricedIn = Literal["little", "partly", "mostly", "fully"]
ThesisStatus = Literal["new", "intact", "weakened", "broken"]
Role = Literal["core", "defensive", "leveraged", "inverse"]

TREND_CATEGORIES = TrendCategory.__args__
TREND_STATES = TrendState.__args__
REGIMES = Regime.__args__


class Trend(BaseModel):
    name: str
    category: TrendCategory
    state: TrendState
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(description="ids of the supplied items that support it")
    beneficiaries: list[str] = Field(description="tickers that gain from the trend")
    losers: list[str] = Field(description="tickers hurt by the trend")
    summary: str


class TrendScan(BaseModel):
    trends: list[Trend]
    regime: Regime
    regime_reasons: str


class Candidate(BaseModel):
    symbol: str
    trend_names: list[str]
    why: str


class CandidateList(BaseModel):
    candidates: list[Candidate]


class ResearchNote(BaseModel):
    symbol: str
    conviction: float = Field(ge=0.0, le=10.0)
    thesis: str
    breaks_if: list[str] = Field(description="observable conditions that would end the thesis")
    priced_in: PricedIn
    thesis_status: ThesisStatus
    risks: str


class Pick(BaseModel):
    symbol: str
    conviction: float = Field(ge=0.0, le=10.0)
    target_weight: float = Field(ge=0.0, le=0.25)
    role: Role


class BookProposal(BaseModel):
    picks: list[Pick]
    defensive_share: float = Field(ge=0.0, le=1.0)
    notes: str
