"""Typed, content-addressed records for the self-learning subsystem.

Identity is deliberately narrow: an Observation is identified by WHERE and WHEN
a decision happened, never by what we later learned about it. Resolving an
outcome must UPDATE that row rather than create a second one, or backfill would
duplicate every record it re-reads.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

SCHEMA_VERSION = 1


def content_id(kind: str, identity: dict) -> str:
    """A stable 64-hex identity over `kind` + the natural key. Key order and
    dict nesting never change the result."""
    canonical = json.dumps(
        {"schema_version": SCHEMA_VERSION, "kind": kind, "identity": identity},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Observation:
    run_id: str
    origin: str            # "backtest" | "live"
    venue: str             # "equity" | "crypto" | "kalshi"
    strategy_id: str
    as_of: str             # ISO timestamp of the bar
    symbol: str
    action: str
    decision: int          # 1 buy | 0 hold | -1 sell
    normalized_score: float | None
    executed: bool
    refusal_reason: str | None
    votes: tuple           # ((strategy, decision, weight), ...)
    config_hash: str | None

    @property
    def id(self) -> str:
        return content_id("observation", {
            "run_id": self.run_id, "origin": self.origin,
            "symbol": self.symbol, "as_of": self.as_of,
        })

    def to_doc(self) -> dict:
        return {
            "id": self.id, "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id, "origin": self.origin, "venue": self.venue,
            "strategy_id": self.strategy_id, "as_of": self.as_of,
            "symbol": self.symbol, "action": self.action,
            "decision": self.decision, "normalized_score": self.normalized_score,
            "executed": self.executed, "refusal_reason": self.refusal_reason,
            "votes": [list(v) for v in self.votes],
            "config_hash": self.config_hash,
        }


@dataclass(frozen=True)
class VarianceReport:
    field_name: str
    n: int
    distinct: int
    top_value: object
    top_share: float
    saturated: bool

    def to_doc(self) -> dict:
        return {
            "field_name": self.field_name, "n": self.n,
            "distinct": self.distinct, "top_value": self.top_value,
            "top_share": round(self.top_share, 6), "saturated": self.saturated,
        }


@dataclass(frozen=True)
class Finding:
    kind: str
    target: str            # "<venue>/<strategy_id>"
    severity: str          # "low" | "medium" | "high"
    title: str
    detail: str
    evidence: dict = field(default_factory=dict)
    detected_at: str = ""
    run_id: str = ""
    status: str = "open"

    @property
    def id(self) -> str:
        return content_id("finding", {
            "kind": self.kind, "target": self.target, "title": self.title,
        })

    def to_doc(self) -> dict:
        return {
            "id": self.id, "schema_version": SCHEMA_VERSION, "kind": self.kind,
            "target": self.target, "severity": self.severity,
            "title": self.title, "detail": self.detail,
            "evidence": self.evidence, "detected_at": self.detected_at,
            "run_id": self.run_id, "status": self.status,
        }


@dataclass(frozen=True)
class Lever:
    strategy_id: str
    kind: str              # "config" | "weight" | "execution_position" | "membership"
    key: str
    value_type: str        # "bool" | "number" | "string" | "list" | "dict" | "null"
    default: object

    def to_doc(self) -> dict:
        return {
            "strategy_id": self.strategy_id, "kind": self.kind, "key": self.key,
            "value_type": self.value_type, "default": self.default,
        }
