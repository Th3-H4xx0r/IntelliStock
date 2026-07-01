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

# Per-tier RISK defaults (low -> max = increasing aggression). Picking a tier gives a
# coherent profile so a UI edit can't reset a knob to a mismatched flat default. NOTE:
# bankroll is deliberately NOT here — it is the user's real capital, never a hardcoded
# default. Higher tiers: bigger Kelly + per-bet cap + exposure, lower edge bar, and a
# lower no-sharp bar (take more model-only) with a lighter size haircut.
_TIER_DEFAULTS = {
    "low":    {"edge_threshold": 0.04,  "kelly_fraction": 0.125, "per_bet_cap_frac": 0.03,
               "max_open_exposure_frac": 0.10, "no_sharp_edge_threshold": 0.10,
               "model_only_size_mult": 0.40, "max_concurrent_positions": 4,
               "max_contracts_per_market": 50,  "inplay_exposure_frac": 0.10, "max_adds_per_match": 1,
               "order_size_min_cents": 100, "order_size_max_cents": 300},
    "medium": {"edge_threshold": 0.035, "kelly_fraction": 0.15,  "per_bet_cap_frac": 0.05,
               "max_open_exposure_frac": 0.15, "no_sharp_edge_threshold": 0.08,
               "model_only_size_mult": 0.50, "max_concurrent_positions": 6,
               "max_contracts_per_market": 50,  "inplay_exposure_frac": 0.12, "max_adds_per_match": 2,
               "order_size_min_cents": 200, "order_size_max_cents": 500},
    "high":   {"edge_threshold": 0.03,  "kelly_fraction": 0.20,  "per_bet_cap_frac": 0.07,
               "max_open_exposure_frac": 0.25, "no_sharp_edge_threshold": 0.05,
               "model_only_size_mult": 0.50, "max_concurrent_positions": 8,
               "max_contracts_per_market": 75,  "inplay_exposure_frac": 0.15, "max_adds_per_match": 2,
               "order_size_min_cents": 500, "order_size_max_cents": 1000},
    "max":    {"edge_threshold": 0.025, "kelly_fraction": 0.25,  "per_bet_cap_frac": 0.10,
               "max_open_exposure_frac": 0.40, "no_sharp_edge_threshold": 0.03,
               "model_only_size_mult": 0.60, "max_concurrent_positions": 12,
               "max_contracts_per_market": 100, "inplay_exposure_frac": 0.20, "max_adds_per_match": 3,
               "order_size_min_cents": 800, "order_size_max_cents": 1500},
}


def tier_defaults(tier: str) -> dict:
    return _TIER_DEFAULTS.get((tier or "medium").lower(), _TIER_DEFAULTS["medium"])


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
    # Per-tier risk defaults (low -> max). Legacy min-stake FLOOR disabled (it forced
    # thin edges into huge bets); a per-bet CEILING (by tier) caps single-bet exposure.
    td = tier_defaults(tier)
    _bankroll = _dollars_to_cents(raw.get("bankroll_dollars"), 0)
    # Daily loss cap auto-derives to daily_loss_cap_frac of bankroll when not set explicitly.
    _daily_frac = float(raw.get("daily_loss_cap_frac", 0.08))
    _daily_loss = _dollars_to_cents(raw.get("daily_loss_cap_dollars"), 0) or (round(_daily_frac * _bankroll) if _bankroll else 0)
    return {
        "leagues": [str(x) for x in leagues if str(x).strip()] or DEFAULT_LEAGUES,
        "edge_threshold": float(raw.get("edge_threshold", td["edge_threshold"])),
        "kelly_fraction": float(raw.get("kelly_fraction", td["kelly_fraction"])),
        "min_stake_frac": float(raw.get("min_stake_frac", 0.0)),
        "per_bet_cap_frac": float(raw.get("per_bet_cap_frac", td["per_bet_cap_frac"])),
        "max_contracts_per_market": int(raw.get("max_contracts_per_market", td["max_contracts_per_market"])),
        "max_open_exposure_frac": float(raw.get("max_open_exposure_frac", td["max_open_exposure_frac"])),
        "per_league_cap_frac": float(raw.get("per_league_cap_frac", 0.25)),
        # Price band + draw gate (favorite-longshot tax): no cheap longshots, no
        # near-certs, draws need a big edge. This is where 100% of the losses were.
        "min_price_cents": int(raw.get("min_price_cents", 15)),
        "max_price_cents": int(raw.get("max_price_cents", 90)),
        "draw_min_edge": float(raw.get("draw_min_edge", 0.10)),
        # No-sharp-line gate: model-only fairs overrate favorites, so demand a bigger
        # edge before betting a market the sharp book doesn't price (keeps volume on
        # genuinely large disagreements rather than hard-skipping).
        "no_sharp_edge_threshold": float(raw.get("no_sharp_edge_threshold", td["no_sharp_edge_threshold"])),
        # Size haircut for model-only (no-sharp) bets — they're unanchored favorite
        # risk, so bet them smaller than sharp-anchored ones.
        "model_only_size_mult": float(raw.get("model_only_size_mult", td["model_only_size_mult"])),
        # Order-size RANGE ($/trade): when set, the bot sizes each trade within [min,max]
        # by edge conviction (decoupled from Kelly) — meaningful trades even on a tiny
        # account. 0/0 = auto (Kelly-sized).
        # order-size range: explicit dollars if provided, else the tier's suggested range.
        "order_size_min_cents": (_dollars_to_cents(raw["order_size_min_dollars"])
                                 if raw.get("order_size_min_dollars") is not None else td["order_size_min_cents"]),
        "order_size_max_cents": (_dollars_to_cents(raw["order_size_max_dollars"])
                                 if raw.get("order_size_max_dollars") is not None else td["order_size_max_cents"]),
        "maker_first": bool(raw.get("maker_first", True)),
        "maker_min_spread_cents": int(raw.get("maker_min_spread_cents", 3)),
        "maker_min_book_depth": int(raw.get("maker_min_book_depth", 5)),
        "maker_max_adverse_imbalance": float(raw.get("maker_max_adverse_imbalance", -0.5)),
        "cash_buffer_frac": float(raw.get("cash_buffer_frac", 0.03)),
        "max_concurrent_positions": int(raw.get("max_concurrent_positions", td["max_concurrent_positions"])),
        "daily_loss_cap_frac": _daily_frac,
        "daily_loss_cap_cents": _daily_loss,
        "bankroll_cents": _bankroll,
        "poll_seconds": max(15, int(raw.get("poll_seconds", 60))),
        "bankroll_usage_pct": min(100, max(0, int(raw.get("bankroll_usage_pct", 50)))),
        "tier": tier,
        "model": (str(raw.get("model")).strip() or None) if raw.get("model") else None,
        "live_enabled": bool(live_enabled),
        # HARD dry-run gate: when True the engine reads real prices but places NO real
        # orders (paper-fill grading). Lets a FUNDED live account be tested safely.
        "paper_mode": bool(raw.get("paper_mode", False)),
        # Live in-match monitoring (Kalshi-price-only, two-way in-play).
        "live_monitoring": bool(raw.get("live_monitoring", True)),
        "live_poll_seconds": max(10, int(raw.get("live_poll_seconds", 30))),
        "analyst_max_calls": max(0, int(raw.get("analyst_max_calls", 10))),
        "inplay_exposure_frac": float(raw.get("inplay_exposure_frac", td["inplay_exposure_frac"])),
        "max_adds_per_match": int(raw.get("max_adds_per_match", td["max_adds_per_match"])),
        "no_add_after_min": float(raw.get("no_add_after_min", 75.0)),
        "stop_loss_frac": float(raw.get("stop_loss_frac", 0.35)),
        # Sharp-odds anchor (de-vig'd bookmaker odds -> fair value -> edge vs Kalshi).
        "odds_api_key": str(raw.get("odds_api_key") or "").strip(),
        # OddsPapi key (historical sharp line for backtests). Persisted so it's
        # entered once and reused across backtests.
        "oddspapi_api_key": str(raw.get("oddspapi_api_key") or "").strip(),
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
        no_sharp_edge_threshold=float(c.get("no_sharp_edge_threshold", 0.0)),
        model_only_size_mult=float(c.get("model_only_size_mult", 1.0)),
        order_size_min_cents=int(c.get("order_size_min_cents", 0)),
        order_size_max_cents=int(c.get("order_size_max_cents", 0)),
        maker_first=bool(c.get("maker_first", True)),
        maker_min_spread_cents=int(c.get("maker_min_spread_cents", 3)),
        maker_min_book_depth=int(c.get("maker_min_book_depth", 5)),
        maker_max_adverse_imbalance=float(c.get("maker_max_adverse_imbalance", -0.5)),
        cash_buffer_frac=float(c.get("cash_buffer_frac", 0.03)),
        max_concurrent_positions=int(c.get("max_concurrent_positions", 8)),
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
        tp_overshoot_cents=float(c.get("tp_overshoot_cents", 8.0)),
        maker_min_spread_cents=int(c.get("maker_min_spread_cents", 3)),
        maker_min_book_depth=int(c.get("maker_min_book_depth", 5)),
        maker_max_adverse_imbalance=float(c.get("maker_max_adverse_imbalance", -0.5)),
        order_size_min_cents=int(c.get("order_size_min_cents", 0)),
        order_size_max_cents=int(c.get("order_size_max_cents", 0)),
    )
