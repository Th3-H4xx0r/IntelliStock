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


def _tagged(value):
    """Type-tag a value so two different values can never serialize alike.

    A plain `json.dumps(..., default=str)` collides silently: `Decimal("1")` and
    the string `"1"` both stringify to `"1"`, and a tuple and a list both encode
    as an array. Tagging the type makes the identity injective.
    """
    if isinstance(value, dict):
        return ["dict", sorted((str(k), _tagged(v)) for k, v in value.items())]
    if isinstance(value, (list, tuple)):
        return [type(value).__name__, [_tagged(v) for v in value]]
    if value is None or isinstance(value, (str, bool, int, float)):
        return [type(value).__name__, value if value is None else str(value)]
    return [type(value).__name__, str(value)]


def content_id(kind: str, identity: dict) -> str:
    """A stable 64-hex identity over `kind` + the natural key. Key order and
    dict nesting never change the result; differing types never collide."""
    canonical = json.dumps(
        {"schema_version": SCHEMA_VERSION, "kind": kind,
         "identity": _tagged(identity or {})},
        sort_keys=True, separators=(",", ":"),
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
        # `venue` participates: the same run id under two venues is two
        # different decision points, and without it re-processing a document
        # as crypto would silently rewrite the equity row in place
        # (`store.put_observations` writes with conflict="update").
        return content_id("observation", {
            "run_id": self.run_id, "origin": self.origin, "venue": self.venue,
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
