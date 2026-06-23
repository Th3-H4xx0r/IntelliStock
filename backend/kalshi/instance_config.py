"""Kalshi instance config <-> Instances-row shaping (pure, unit-tested).

A Kalshi bot is an Instances row tagged kind='kalshi' with a kalshi_config blob,
bound to a Kalshi brokerage. It carries NO equities fields (no strategy_id /
stocks). server.py's supervisor launches instance.py for it like any instance;
instance.py dispatches kind='kalshi' rows to the lean engine (kalshi/runner.py).
"""
from __future__ import annotations

from kalshi.risk import RiskCaps

DEFAULT_LEAGUES = ["EPL", "Serie B", "Ligue 2"]


def _dollars_to_cents(v, default=0) -> int:
    try:
        return int(round(float(v) * 100))
    except (TypeError, ValueError):
        return default


def normalize_config(raw: dict, *, live_enabled: bool) -> dict:
    """Validate/normalize a creation-form config into the stored kalshi_config.
    Dollar inputs (bankroll, daily-loss cap) are converted to cents."""
    raw = raw or {}
    leagues = raw.get("leagues") or DEFAULT_LEAGUES
    tier = str(raw.get("tier") or "medium").lower()
    if tier not in ("low", "medium", "high", "max"):
        tier = "medium"
    return {
        "leagues": [str(x) for x in leagues if str(x).strip()] or DEFAULT_LEAGUES,
        "edge_threshold": float(raw.get("edge_threshold", 0.03)),
        "kelly_fraction": float(raw.get("kelly_fraction", 0.25)),
        "max_contracts_per_market": int(raw.get("max_contracts_per_market", 50)),
        "max_open_exposure_frac": float(raw.get("max_open_exposure_frac", 0.60)),
        "per_league_cap_frac": float(raw.get("per_league_cap_frac", 0.25)),
        "daily_loss_cap_cents": _dollars_to_cents(raw.get("daily_loss_cap_dollars"), 0),
        "bankroll_cents": _dollars_to_cents(raw.get("bankroll_dollars"), 0),
        "poll_seconds": max(15, int(raw.get("poll_seconds", 60))),
        "tier": tier,
        "model": (str(raw.get("model")).strip() or None) if raw.get("model") else None,
        "live_enabled": bool(live_enabled),
    }


def build_kalshi_instance_doc(instance_id: str, *, brokerage_id: str, name: str, config: dict) -> dict:
    """The Instances row for a Kalshi bot. runCommand starts False (created
    stopped); start/stop flips it like any instance."""
    return {
        "id": str(instance_id),
        "name": name or "Kalshi instance",
        "kind": "kalshi",
        "brokerage_id": str(brokerage_id),
        "runCommand": False,
        "created_by": "user",
        "kalshi_config": config,
    }


def risk_caps_from_config(config: dict) -> RiskCaps:
    """Build the engine's RiskCaps from a stored kalshi_config."""
    c = config or {}
    return RiskCaps(
        edge_threshold=float(c.get("edge_threshold", 0.03)),
        kelly_fraction=float(c.get("kelly_fraction", 0.25)),
        max_contracts_per_market=int(c.get("max_contracts_per_market", 50)),
        max_open_exposure_frac=float(c.get("max_open_exposure_frac", 0.60)),
        per_league_cap_frac=float(c.get("per_league_cap_frac", 0.25)),
        daily_loss_cap_cents=int(c.get("daily_loss_cap_cents", 0)),
        bankroll_cents=int(c.get("bankroll_cents", 0)),
    )
