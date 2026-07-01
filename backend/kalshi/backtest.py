"""Kalshi soccer backtest replay engine — the pure per-fixture core.

`run_backtest` (built separately) loops historical fixtures and calls the
primitives in this module: `evaluate()` turns one fixture's model/sharp probs
and Kalshi asks into sized bets using the SAME pricing/candidate/sizing code
path the live bot uses (`intelligence.pricing.build_market_probs` ->
`strategy.candidates.generate_candidates` -> `capital.planner.allocate`);
`settle()`/`aggregate()` grade those bets against the final result and roll
them up into a `BacktestResult`.

No network, no DB — every function here takes plain data in and returns plain
data out, so it is unit-testable with fabricated inputs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from kalshi import instance_config
from kalshi.fees import DEFAULT_FEE_RATE, fee_as_prob, fee_cents_for_order
from kalshi.intelligence.pricing import build_market_probs
from kalshi.strategy.candidates import generate_candidates
from kalshi.capital.planner import allocate
from kalshi.risk import RiskCaps

_DEVIG_METHODS = ("power", "shin", "proportional")


# --------------------------------------------------------------------------
# Task 5: BacktestConfig + config_from_body
# --------------------------------------------------------------------------


@dataclass
class BacktestConfig:
    leagues: list
    start_date: str
    end_date: str
    bankroll_cents: int
    caps: RiskCaps
    sharp_weight: float
    devig_method: str
    decision_offsets_sec: tuple = (-3 * 3600,)
    fee_rate: float = DEFAULT_FEE_RATE


def config_from_body(body: dict) -> BacktestConfig:
    """Build a `BacktestConfig` from an API request body, mapping the SAME
    knobs the live instance uses (edge_threshold, no_sharp_edge_threshold,
    kelly_fraction, order_size_min/max_cents, max_open_exposure_frac,
    per_bet_cap_frac, min/max_price_cents, draw_min_edge, sharp_weight,
    devig_method, bankroll_cents) into a `RiskCaps` via
    `instance_config.risk_caps_from_config` — no reimplementation of the
    live caps-building logic."""
    body = dict(body or {})

    # Dollar-denominated UI fields -> cents (mirrors instance_config.normalize_config).
    bankroll_cents = instance_config._dollars_to_cents(
        body.get("bankroll_dollars"), int(body.get("bankroll_cents", 0) or 0)
    )
    order_min_cents = instance_config._dollars_to_cents(
        body.get("order_size_min_dollars"), int(body.get("order_size_min_cents", 0) or 0)
    )
    order_max_cents = instance_config._dollars_to_cents(
        body.get("order_size_max_dollars"), int(body.get("order_size_max_cents", 0) or 0)
    )

    caps_config = dict(body)
    caps_config["bankroll_cents"] = bankroll_cents
    caps_config["order_size_min_cents"] = order_min_cents
    caps_config["order_size_max_cents"] = order_max_cents
    caps = instance_config.risk_caps_from_config(caps_config)

    offsets = body.get("decision_offsets_sec")
    decision_offsets_sec = tuple(offsets) if offsets is not None else (-3 * 3600,)

    devig_method = str(body.get("devig_method") or "power").lower()
    if devig_method not in _DEVIG_METHODS:
        devig_method = "power"

    return BacktestConfig(
        leagues=list(body.get("leagues") or []),
        start_date=str(body.get("start_date", "") or ""),
        end_date=str(body.get("end_date", "") or ""),
        bankroll_cents=bankroll_cents,
        caps=caps,
        sharp_weight=min(1.0, max(0.0, float(body.get("sharp_weight", 0.85)))),
        devig_method=devig_method,
        decision_offsets_sec=decision_offsets_sec,
        fee_rate=float(body.get("fee_rate", DEFAULT_FEE_RATE)),
    )


# --------------------------------------------------------------------------
# Task 6: evaluate() — per-fixture candidate generation + sizing
# --------------------------------------------------------------------------

_TIER_FOR_MARKET_TYPES = "max"  # widest allowed-markets set; caps still gate volume/size


@dataclass(frozen=True)
class SizedBet:
    side: str
    market_ticker: str
    entry_cents: int
    size: int
    model_prob: float | None
    sharp_prob: float | None
    fused_fair: float
    edge: float
    market_type: str = ""
    fixture_id: str = ""


def evaluate(cfg: BacktestConfig, model_probs: dict, sharp_probs: dict,
             kalshi_asks: dict, fixture: dict) -> list["SizedBet"]:
    """Replicate the live pre-match decision path for ONE fixture:
    fuse model+sharp probs -> generate edge-gated candidates -> size via the
    quarter-Kelly/order-size-range planner. `model_probs`/`sharp_probs` are
    `{side: prob}` for the fixture's `winner` market (the pre-match slice this
    engine targets); `kalshi_asks` is `{side: cents}`.

    This mirrors `orchestrator.plan_and_allocate`'s single-fixture call
    sequence (build_market_probs -> generate_candidates -> allocate) with the
    live/in-play parts (LLM adjustments, player props, opportunity scoring
    across multiple fixtures) omitted — a backtest fixture has no LLM
    rationale and is evaluated one match at a time."""
    fixture_id = fixture.get("fixture_id", "")
    tier = fixture.get("tier") or _TIER_FOR_MARKET_TYPES
    league = fixture.get("league", "")

    expected_goals = fixture.get("expected_goals")
    sharp_probs = sharp_probs or {}
    model_probs = model_probs or {}
    kalshi_asks = kalshi_asks or {}

    # build_market_probs expects {market_type: {side: prob}}; evaluate()'s
    # model_probs/sharp_probs are already scoped to one market ({side: prob}),
    # so wrap them under "winner" before fusing.
    sharp_by_type = {"winner": dict(sharp_probs)} if sharp_probs else {}
    fused = build_market_probs(expected_goals, sharp_by_type, {}, w_sharp=cfg.sharp_weight)
    # If there's no expected_goals model signal, fall back to the caller-supplied
    # model_probs directly (fused would otherwise degrade to sharp-only).
    if "winner" not in fused or not fused.get("winner"):
        fused = dict(fused)
        base = {}
        for side in set(model_probs) | set(sharp_probs):
            base[side] = dict(sharp_by_type.get("winner", {})).get(side) if side in sharp_probs else None
        fused["winner"] = {
            side: (
                (cfg.sharp_weight * sharp_probs[side] + (1 - cfg.sharp_weight) * model_probs.get(side, sharp_probs[side]))
                if side in sharp_probs else model_probs.get(side)
            )
            for side in (set(model_probs) | set(sharp_probs))
        }

    kalshi_markets = [
        {
            "market_ticker": fixture.get("market_tickers", {}).get(side, f"{fixture_id}-{side.upper()}")
                if isinstance(fixture.get("market_tickers"), dict) else f"{fixture_id}-{side.upper()}",
            "market_type": "winner",
            "side": side,
            "yes_ask_cents": ask,
        }
        for side, ask in kalshi_asks.items()
    ]

    candidates = generate_candidates(
        fixture_id, tier, fused, kalshi_markets,
        fee_rate=cfg.fee_rate,
        edge_threshold=cfg.caps.edge_threshold,
        min_price_cents=cfg.caps.min_price_cents,
        max_price_cents=cfg.caps.max_price_cents,
        draw_min_edge=cfg.caps.draw_min_edge,
        sharp_probs=sharp_by_type,
        no_sharp_edge_threshold=cfg.caps.no_sharp_edge_threshold,
    )
    if not candidates:
        return []

    scored = [
        {"id": c.market_ticker, "score": c.edge, "edge": c.edge,
         "price_cents": c.price_cents, "has_sharp": c.has_sharp}
        for c in candidates
    ]
    allocations = allocate(
        scored, bankroll_cents=cfg.bankroll_cents, caps=cfg.caps,
        reserve_frac=0.0, expected_better_soon=False,
    )
    alloc_by_id = {a["id"]: a for a in allocations}

    out: list[SizedBet] = []
    for c in candidates:
        a = alloc_by_id.get(c.market_ticker)
        if not a or a["contracts"] <= 0:
            continue
        out.append(SizedBet(
            side=c.side,
            market_ticker=c.market_ticker,
            entry_cents=c.price_cents,
            size=a["contracts"],
            model_prob=model_probs.get(c.side),
            sharp_prob=sharp_probs.get(c.side),
            fused_fair=c.fair,
            edge=c.edge,
            market_type=c.market_type,
            fixture_id=fixture_id,
        ))
    return out


# --------------------------------------------------------------------------
# Task 7: settle() + aggregate()
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Trade:
    side: str
    market_ticker: str
    entry_cents: int
    size: int
    model_prob: float | None
    sharp_prob: float | None
    fused_fair: float
    edge: float
    outcome: str
    realized_pnl_cents: int
    clv: float
    market_type: str = ""
    fixture_id: str = ""
    league: str = ""
    kickoff: str = ""


@dataclass
class BacktestResult:
    pnl_cents: int
    roi: float
    n_bets: int
    win_rate: float
    clv_avg: float
    equity_curve: list
    per_league: dict
    calibration: list
    trades: list


def settle(bet: SizedBet, result: str, fee_rate: float = DEFAULT_FEE_RATE) -> Trade:
    """Grade one `SizedBet` against the fixture result. `result` is the winning
    side ('home'/'away'/'draw', matching `bet.side`) — win when `result ==
    bet.side`. Same settlement math as `reconcile.reconcile_position` (a
    single-fill position, so its cost-weighted-average path collapses to the
    plain formula): win -> (100-entry)*size - fee; loss -> -entry*size. CLV
    is the fused fair (the closest thing to a pre-match "sharp close" this
    per-fixture primitive has) vs the entry price."""
    won = (result == bet.side)
    fee = fee_cents_for_order(bet.entry_cents, bet.size, fee_rate)
    if won:
        realized = (100 - bet.entry_cents) * bet.size - fee
    else:
        realized = -bet.entry_cents * bet.size
    clv = (bet.fused_fair * 100.0 - bet.entry_cents) / 100.0
    return Trade(
        side=bet.side,
        market_ticker=bet.market_ticker,
        entry_cents=bet.entry_cents,
        size=bet.size,
        model_prob=bet.model_prob,
        sharp_prob=bet.sharp_prob,
        fused_fair=bet.fused_fair,
        edge=bet.edge,
        outcome="win" if won else "loss",
        realized_pnl_cents=int(realized),
        clv=round(clv, 4),
        market_type=bet.market_type,
        fixture_id=bet.fixture_id,
    )


def _calibration_buckets(trades: list[Trade], n_buckets: int = 10) -> list[dict]:
    """Predicted-probability vs actual-win-rate buckets (reliability curve)."""
    buckets = [[] for _ in range(n_buckets)]
    for t in trades:
        p = t.fused_fair if t.fused_fair is not None else 0.0
        idx = min(n_buckets - 1, max(0, int(p * n_buckets)))
        buckets[idx].append(t)
    out = []
    for i, bucket in enumerate(buckets):
        if not bucket:
            continue
        lo, hi = i / n_buckets, (i + 1) / n_buckets
        n = len(bucket)
        wins = sum(1 for t in bucket if t.outcome == "win")
        out.append({
            "bucket": [lo, hi],
            "predicted_avg": sum(t.fused_fair for t in bucket) / n,
            "actual_rate": wins / n,
            "n": n,
        })
    return out


def aggregate(trades: list[Trade], bankroll_cents: int) -> BacktestResult:
    """Roll up settled trades into a `BacktestResult`. `trades` should already
    be ordered by fixture kickoff (the caller — `run_backtest` — settles
    fixtures in kickoff order); the equity curve is the running cumulative
    P&L in that order."""
    n_bets = len(trades)
    pnl_cents = sum(t.realized_pnl_cents for t in trades)
    roi = (pnl_cents / bankroll_cents) if bankroll_cents else 0.0
    wins = sum(1 for t in trades if t.outcome == "win")
    win_rate = (wins / n_bets) if n_bets else 0.0
    clv_avg = (sum(t.clv for t in trades) / n_bets) if n_bets else 0.0

    equity_curve = []
    running = 0
    for t in trades:
        running += t.realized_pnl_cents
        equity_curve.append(running)

    per_league: dict = {}
    for t in trades:
        lg = t.league or "unknown"
        row = per_league.setdefault(lg, {"pnl_cents": 0, "n_bets": 0, "wins": 0})
        row["pnl_cents"] += t.realized_pnl_cents
        row["n_bets"] += 1
        row["wins"] += 1 if t.outcome == "win" else 0
    for row in per_league.values():
        row["win_rate"] = row["wins"] / row["n_bets"] if row["n_bets"] else 0.0

    calibration = _calibration_buckets(trades)

    return BacktestResult(
        pnl_cents=int(pnl_cents),
        roi=roi,
        n_bets=n_bets,
        win_rate=win_rate,
        clv_avg=round(clv_avg, 4),
        equity_curve=equity_curve,
        per_league=per_league,
        calibration=calibration,
        trades=list(trades),
    )
