"""Kalshi instance config <-> Instances-row shaping (pure, unit-tested).

A Kalshi bot is an Instances row tagged kind='kalshi' with a kalshi_config blob,
bound to a Kalshi brokerage. It carries NO equities fields (no strategy_id /
stocks). server.py's supervisor launches instance.py for it like any instance;
instance.py dispatches kind='kalshi' rows to the lean engine (kalshi/runner.py).
"""
from __future__ import annotations

from kalshi.live.live_decision import InPlayCaps
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
    # Legacy min-stake FLOOR disabled (it forced thin edges into huge bets — a top
    # loss driver). A per-bet CEILING (by tier) caps single-bet exposure instead.
    _per_bet_cap_by_tier = {"low": 0.03, "medium": 0.05, "high": 0.07, "max": 0.10}
    _bankroll = _dollars_to_cents(raw.get("bankroll_dollars"), 0)
    # Daily loss cap auto-derives to daily_loss_cap_frac of bankroll when not set explicitly.
    _daily_frac = float(raw.get("daily_loss_cap_frac", 0.08))
    _daily_loss = _dollars_to_cents(raw.get("daily_loss_cap_dollars"), 0) or (round(_daily_frac * _bankroll) if _bankroll else 0)
    return {
        "leagues": [str(x) for x in leagues if str(x).strip()] or DEFAULT_LEAGUES,
        "edge_threshold": float(raw.get("edge_threshold", 0.04)),
        "kelly_fraction": float(raw.get("kelly_fraction", 0.125)),
        "min_stake_frac": float(raw.get("min_stake_frac", 0.0)),
        "per_bet_cap_frac": float(raw.get("per_bet_cap_frac", _per_bet_cap_by_tier.get(tier, 0.05))),
        "max_contracts_per_market": int(raw.get("max_contracts_per_market", 50)),
        "max_open_exposure_frac": float(raw.get("max_open_exposure_frac", 0.15)),
        "per_league_cap_frac": float(raw.get("per_league_cap_frac", 0.25)),
        # Price band + draw gate (favorite-longshot tax): no cheap longshots, no
        # near-certs, draws need a big edge. This is where 100% of the losses were.
        "min_price_cents": int(raw.get("min_price_cents", 15)),
        "max_price_cents": int(raw.get("max_price_cents", 90)),
        "draw_min_edge": float(raw.get("draw_min_edge", 0.10)),
        "maker_first": bool(raw.get("maker_first", True)),
        "maker_min_spread_cents": int(raw.get("maker_min_spread_cents", 3)),
        "maker_min_book_depth": int(raw.get("maker_min_book_depth", 5)),
        "maker_max_adverse_imbalance": float(raw.get("maker_max_adverse_imbalance", -0.5)),
        "cash_buffer_frac": float(raw.get("cash_buffer_frac", 0.03)),
        "daily_loss_cap_frac": _daily_frac,
        "daily_loss_cap_cents": _daily_loss,
        "bankroll_cents": _bankroll,
        "poll_seconds": max(15, int(raw.get("poll_seconds", 60))),
        "bankroll_usage_pct": min(100, max(0, int(raw.get("bankroll_usage_pct", 50)))),
        "tier": tier,
        "model": (str(raw.get("model")).strip() or None) if raw.get("model") else None,
        "live_enabled": bool(live_enabled),
        # Live in-match monitoring (Kalshi-price-only, two-way in-play).
        "live_monitoring": bool(raw.get("live_monitoring", True)),
        "live_poll_seconds": max(10, int(raw.get("live_poll_seconds", 30))),
        "analyst_max_calls": max(0, int(raw.get("analyst_max_calls", 10))),
        "inplay_exposure_frac": float(raw.get("inplay_exposure_frac", 0.15)),
        "max_adds_per_match": int(raw.get("max_adds_per_match", 2)),
        "no_add_after_min": float(raw.get("no_add_after_min", 75.0)),
        "stop_loss_frac": float(raw.get("stop_loss_frac", 0.35)),
        # Sharp-odds anchor (de-vig'd bookmaker odds -> fair value -> edge vs Kalshi).
        "odds_api_key": str(raw.get("odds_api_key") or "").strip(),
        "sharp_weight": min(1.0, max(0.0, float(raw.get("sharp_weight", 0.85)))),
        "devig_method": (str(raw.get("devig_method") or "power").lower()
                         if str(raw.get("devig_method") or "power").lower()
                         in ("power", "shin", "proportional") else "power"),
        "odds_refresh_secs": max(300, int(raw.get("odds_refresh_secs", 3600))),
        "odds_regions": str(raw.get("odds_regions") or "eu,uk,us").strip() or "eu,uk,us",
        # Kalshi series tickers to scan ([] = engine uses DEFAULT_SOCCER_SERIES).
        "soccer_series": [str(s).strip() for s in (raw.get("soccer_series") or []) if str(s).strip()],
        # ESPN league slugs for live-score detection ([] = engine uses DEFAULT_SCOREBOARD_LEAGUES).
        "scoreboard_leagues": [str(s).strip() for s in (raw.get("scoreboard_leagues") or []) if str(s).strip()],
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
        edge_threshold=float(c.get("edge_threshold", 0.04)),
        kelly_fraction=float(c.get("kelly_fraction", 0.125)),
        max_contracts_per_market=int(c.get("max_contracts_per_market", 50)),
        max_open_exposure_frac=float(c.get("max_open_exposure_frac", 0.15)),
        per_league_cap_frac=float(c.get("per_league_cap_frac", 0.25)),
        daily_loss_cap_cents=int(c.get("daily_loss_cap_cents", 0)),
        bankroll_cents=int(c.get("bankroll_cents", 0)),
        min_stake_frac=float(c.get("min_stake_frac", 0.0)),
        per_bet_cap_frac=float(c.get("per_bet_cap_frac", 0.05)),
        min_price_cents=int(c.get("min_price_cents", 15)),
        max_price_cents=int(c.get("max_price_cents", 90)),
        draw_min_edge=float(c.get("draw_min_edge", 0.10)),
        maker_first=bool(c.get("maker_first", True)),
        maker_min_spread_cents=int(c.get("maker_min_spread_cents", 3)),
        maker_min_book_depth=int(c.get("maker_min_book_depth", 5)),
        maker_max_adverse_imbalance=float(c.get("maker_max_adverse_imbalance", -0.5)),
        cash_buffer_frac=float(c.get("cash_buffer_frac", 0.03)),
    )


def inplay_caps_from_config(config: dict) -> InPlayCaps:
    """Build the live monitor's InPlayCaps from a stored kalshi_config (in-play
    caps are intentionally tighter than the pre-match RiskCaps)."""
    c = config or {}
    return InPlayCaps(
        edge_threshold=float(c.get("edge_threshold", 0.04)),
        kelly_fraction=float(c.get("kelly_fraction", 0.125)),
        bankroll_cents=int(c.get("bankroll_cents", 0)),
        max_contracts_per_market=int(c.get("max_contracts_per_market", 50)),
        inplay_exposure_frac=float(c.get("inplay_exposure_frac", 0.15)),
        max_adds_per_match=int(c.get("max_adds_per_match", 2)),
        no_add_after_min=float(c.get("no_add_after_min", 75.0)),
        stop_loss_frac=float(c.get("stop_loss_frac", 0.35)),
        take_profit_mult=float(c.get("take_profit_mult", 1.6)),
        catastrophe_stop_frac=float(c.get("catastrophe_stop_frac", 0.6)),
    )
