"""Kalshi soccer backtest replay engine — the pure per-fixture core.

`run_backtest` (built separately) loops historical fixtures and calls the
primitives in this module: `evaluate()` turns one fixture's model/sharp probs
and Kalshi asks into sized bets using the SAME pricing/candidate/sizing code
path the live bot uses (`intelligence.fusion.fuse`/`renormalize_group` ->
`strategy.candidates.generate_candidates` -> `capital.planner.allocate`);
`settle()`/`aggregate()` grade those bets against the final result and roll
them up into a `BacktestResult`.

No network, no DB — every function here takes plain data in and returns plain
data out, so it is unit-testable with fabricated inputs.

CAVEAT: when a single fixture yields multiple candidates under a binding
capital cap, `evaluate()` orders/allocates them by raw `edge` (candidate
`score` fed to `capital.planner.allocate`), not live's `opportunity_score`
(which also weighs recency/liquidity across fixtures). This is an accepted,
documented divergence from the live path for this per-fixture primitive.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from kalshi import instance_config
from kalshi.client import yes_ask_close_at
from kalshi.data.sources.clubelo import elo_for
from kalshi.devig import power_devig, shin_devig, proportional_devig
from kalshi.fees import DEFAULT_FEE_RATE
from kalshi.intelligence.fusion import fuse, renormalize_group
from kalshi.intelligence.pricing import model_market_probs
from kalshi.quant.elo import elo_to_expected_goals
from kalshi.quant.national_elo import is_national_team, national_elo_from
from kalshi.strategy.candidates import generate_candidates
from kalshi.capital.planner import allocate
from kalshi.reconcile import reconcile_position
from kalshi.risk import RiskCaps

log = logging.getLogger(__name__)

_DEVIG_METHODS = ("power", "shin", "proportional")
_DEVIG_FUNCS = {"power": power_devig, "shin": shin_devig, "proportional": proportional_devig}

# Candlestick fetch window around a fixture's kickoff. Kalshi's candlesticks
# endpoint 400s on an unbounded range; a pre-match lookback of 14 days (hourly
# candles -> ~336 rows) covers every decision snapshot under the request cap.
CANDLE_LOOKBACK_SEC = 14 * 24 * 3600
CANDLE_LOOKAHEAD_SEC = 6 * 3600


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
    model: str = ""              # analyst LLM model id (Models table); "" = none
    analyst_max_calls: int = 10  # cap on LLM analyst calls (cost control)
    use_llm: bool = False        # whether the replay runs the LLM analyst


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
        model=str(body.get("model") or ""),
        analyst_max_calls=int(body.get("analyst_max_calls", 10) or 10),
        use_llm=bool(body.get("use_llm", False)),
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
    league: str = ""
    kickoff: str | int = ""


def evaluate(cfg: BacktestConfig, model_probs: dict, sharp_probs: dict,
             kalshi_asks: dict, fixture: dict, llm_adjustments: dict = None) -> list["SizedBet"]:
    """Replicate the live pre-match decision path for ONE fixture:
    fuse model+sharp probs -> generate edge-gated candidates -> size via the
    quarter-Kelly/order-size-range planner. `model_probs`/`sharp_probs` are
    `{side: prob}` for the fixture's `winner` market (the pre-match slice this
    engine targets); `kalshi_asks` is `{side: cents}`.

    This mirrors `orchestrator.plan_and_allocate`'s single-fixture call
    sequence (fuse per side -> generate_candidates -> allocate) with the
    live/in-play parts (LLM adjustments, player props, opportunity scoring
    across multiple fixtures) omitted — a backtest fixture has no LLM
    rationale and is evaluated one match at a time.

    ADAPTATION from the brief: the brief named `intelligence.fusion.build_market_probs`
    as the fuser, but that function actually lives in `intelligence.pricing`
    and derives its OWN model probs from `expected_goals` (a Dixon-Coles
    scoreline matrix) — it has no way to accept an externally-computed
    `model_probs` dict, which is exactly what `evaluate()`'s signature takes.
    Reusing it here would silently ignore the caller's `model_probs` whenever
    `expected_goals` is absent from the fixture. Instead this fuses each side
    directly with `intelligence.fusion.fuse` (the same per-side blend
    `pricing.build_market_probs` calls internally) and renormalizes the
    winner group with `fusion.renormalize_group` — the same two primitives,
    without the Dixon-Coles detour."""
    fixture_id = fixture.get("fixture_id", "")
    league = fixture.get("league", "")
    # BacktestDataProvider.fixtures() dicts key kickoff as `kickoff_ts`; accept
    # a plain `kickoff` too in case the caller passes an already-normalized dict.
    kickoff = fixture.get("kickoff_ts", fixture.get("kickoff", ""))
    tier = fixture.get("tier") or _TIER_FOR_MARKET_TYPES

    sharp_probs = sharp_probs or {}
    model_probs = model_probs or {}
    kalshi_asks = kalshi_asks or {}
    llm_adjustments = llm_adjustments or {}

    base = {}
    for side in set(model_probs) | set(sharp_probs):
        sharp_v = sharp_probs.get(side)
        model_v = model_probs.get(side, sharp_v if sharp_v is not None else 0.0)
        base[side] = fuse(sharp=sharp_v, model=model_v,
                          llm_adjustment=float(llm_adjustments.get(side, 0.0) or 0.0),
                          w_sharp=cfg.sharp_weight, llm_cap=0.05)
    fused = {"winner": renormalize_group(base) if base else {}}
    sharp_by_type = {"winner": dict(sharp_probs)} if sharp_probs else {}

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
            league=league,
            kickoff=kickoff,
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
    kickoff: str | int = ""


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
    # Populated by run_backtest() with data-layer telemetry (api_calls/cache_hits
    # from the BacktestDataProvider that fed it); aggregate() itself has no data
    # provider to report on, so this defaults empty for direct aggregate() callers.
    summary: dict = field(default_factory=dict)


def settle(bet: SizedBet, result: str, fee_rate: float = DEFAULT_FEE_RATE) -> Trade:
    """Grade one `SizedBet` against the fixture result. `result` is the winning
    side ('home'/'away'/'draw', matching `bet.side`) — win when `result ==
    bet.side`. Settlement math is delegated to `reconcile.reconcile_position`
    (the live settlement path) rather than reimplemented here: a `SizedBet` is
    a single-fill position, so its cost-weighted-average path collapses to the
    plain formula, and `bet.side` winning is exactly the position's YES side
    winning. Kalshi charges the trading fee AT EXECUTION regardless of outcome,
    so `reconcile_position` (and therefore this) charges it on wins AND losses:
    win -> (100-entry)*size - fee; loss -> -entry*size - fee. CLV is the fused
    fair (the closest thing to a pre-match "sharp close" this per-fixture
    primitive has) vs the entry price — `reconcile_position`'s own CLV (which
    grades vs a sharp/mid close, not the fused fair) is not used here."""
    won = (result == bet.side)
    position = {
        "market_ticker": bet.market_ticker,
        "contracts": bet.size,
        "avg_entry_cents": bet.entry_cents,
    }
    settled = reconcile_position(position, result=("yes" if won else "no"), fee_rate=fee_rate)
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
        outcome=settled["outcome"],
        realized_pnl_cents=int(settled["realized_pnl_cents"]),
        clv=round(clv, 4),
        market_type=bet.market_type,
        fixture_id=bet.fixture_id,
        league=bet.league,
        kickoff=bet.kickoff,
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


# --------------------------------------------------------------------------
# Task 8: run_backtest() — orchestration over cached historical data
# --------------------------------------------------------------------------


def _sharp_probs_at(oddseries: list[dict], snap_ts: int, devig_method: str) -> dict:
    """Devigged {home,draw,away} from the odds snapshot at/just-before
    `snap_ts` (the latest snapshot with ts <= snap_ts; the earliest snapshot if
    all of them are AFTER snap_ts — better than nothing pre-match; {} if there
    are no snapshots at all). Same devig methods `evaluate`'s caller already
    uses via `fair_value.fair_from_odds` (power/shin/proportional), applied
    directly here since `fair_value.fair_from_odds` wants an `OddsQuote`, not a
    raw historical-odds dict."""
    if not oddseries:
        return {}
    before = [s for s in oddseries if int(s.get("ts", 0)) <= snap_ts]
    snap = max(before, key=lambda s: int(s.get("ts", 0))) if before else min(
        oddseries, key=lambda s: int(s.get("ts", 0)))
    fn = _DEVIG_FUNCS.get(devig_method, power_devig)
    raw = [1.0 / snap["home"], 1.0 / snap["draw"], 1.0 / snap["away"]]
    p = fn(raw)
    return {"home": p[0], "draw": p[1], "away": p[2]}


def run_backtest(cfg: BacktestConfig, data, model_fn, progress_cb=None, analyst_fn=None) -> BacktestResult:
    """Replay `data`'s fixture history through `evaluate`/`settle`/`aggregate`,
    in kickoff order, one decision snapshot per fixture.

    For each fixture: `data.final_score(fx)` gates it in (None -> SKIP,
    reason "unsettled"); `data.kalshi_tickers(fx)` resolves the Kalshi side
    tickers (empty -> SKIP, reason "unmatched"). For each offset in
    `cfg.decision_offsets_sec` (in order), `snap_ts = kickoff_ts + offset`;
    `client.yes_ask_close_at` reads each side's Kalshi ask at-or-before
    `snap_ts` from that side's candles, and `_sharp_probs_at` devigs the sharp
    odds snapshot at-or-before `snap_ts`. The FIRST offset that produces at
    least one Kalshi ask is used (one snapshot per fixture) — if none do,
    SKIP with reason "no_candle_data". `model_fn(fx)` supplies model probs
    (injected so tests don't need a live Elo table; `build_model_fn` wires the
    production closure). `evaluate()` is called with the resolved tickers
    threaded through `fixture["market_tickers"]` so it prices the REAL Kalshi
    tickers instead of synthesizing placeholders; each returned `SizedBet` is
    `settle()`d against the fixture result. `progress_cb(frac)` (if given) is
    called once per fixture, reaching 1.0 on the last one (a no-op, frac=1.0,
    single call when there are zero fixtures). Returns `aggregate()`'s result
    with `data.api_calls`/`data.cache_hits` surfaced into `result.summary`.
    """
    fixtures = list(data.fixtures(cfg.leagues, cfg.start_date, cfg.end_date))
    fixtures.sort(key=lambda fx: fx.get("kickoff_ts", fx.get("kickoff", 0)) or 0)

    trades: list[Trade] = []
    n = len(fixtures)
    for i, fx in enumerate(fixtures):
        fixture_id = fx.get("fixture_id", "")
        result = data.final_score(fx)
        if result is None:
            log.info("run_backtest: skipping fixture_id=%s reason=unsettled", fixture_id)
        else:
            tickers = data.kalshi_tickers(fx)
            if not tickers:
                log.info("run_backtest: skipping fixture_id=%s reason=unmatched", fixture_id)
            else:
                kickoff_ts = int(fx.get("kickoff_ts", fx.get("kickoff", 0)) or 0)
                # Bound the candlestick window around kickoff. Kalshi's endpoint
                # rejects (400) an unbounded range — an hourly interval over
                # 1970..2100 would be millions of candles. A pre-match window of
                # ~14 days back to a few hours after kickoff covers every decision
                # snapshot while staying well under the per-request candle cap.
                if kickoff_ts > 0:
                    _c_start = kickoff_ts - CANDLE_LOOKBACK_SEC
                    _c_end = kickoff_ts + CANDLE_LOOKAHEAD_SEC
                    candles_by_side = {side: data.candles(ticker, _c_start, _c_end)
                                       for side, ticker in tickers.items()}
                else:
                    candles_by_side = {side: data.candles(ticker)
                                       for side, ticker in tickers.items()}
                oddseries = data.sharp_odds(fx)

                kalshi_asks: dict = {}
                sharp_probs: dict = {}
                for offset in cfg.decision_offsets_sec:
                    snap_ts = kickoff_ts + int(offset)
                    asks = {}
                    for side, candles in candles_by_side.items():
                        ask = yes_ask_close_at(candles, snap_ts)
                        if ask is not None:
                            asks[side] = ask
                    if asks:
                        kalshi_asks = asks
                        sharp_probs = _sharp_probs_at(oddseries, snap_ts, cfg.devig_method)
                        break

                if not kalshi_asks:
                    log.info("run_backtest: skipping fixture_id=%s reason=no_candle_data", fixture_id)
                else:
                    model_probs = model_fn(fx) or {}
                    llm_adj = {}
                    if analyst_fn is not None:
                        try:
                            llm_adj = analyst_fn(fx, model_probs, sharp_probs) or {}
                        except Exception:
                            llm_adj = {}   # analyst failure -> no-op (statistical model only)
                    fx_for_eval = dict(fx)
                    fx_for_eval["market_tickers"] = tickers
                    bets = evaluate(cfg, model_probs, sharp_probs, kalshi_asks, fx_for_eval, llm_adj)
                    for bet in bets:
                        trades.append(settle(bet, result, cfg.fee_rate))

        if progress_cb is not None:
            progress_cb((i + 1) / n if n else 1.0)

    out = aggregate(trades, cfg.bankroll_cents)
    out.summary = {
        "api_calls": getattr(data, "api_calls", 0),
        "cache_hits": getattr(data, "cache_hits", 0),
    }
    return out


# --------------------------------------------------------------------------
# Task 9: build_model_fn() — production model_fn via the live pricing chain
# --------------------------------------------------------------------------


def build_model_fn(nat_elo_table: dict | None, elo_table: dict | None):
    """Production `model_fn(fx) -> {home, draw, away}` for `run_backtest`,
    reusing the SAME pricing chain the live engine (`engine.py`'s tick loop)
    uses: `quant.elo.elo_to_expected_goals` -> `intelligence.pricing
    .model_market_probs`'s `winner` group (itself `quant.dixon_coles
    .scoreline_matrix` + `quant.derive_markets.one_x_two`).

    Per-team Elo lookup mirrors `engine.py`'s `_team_elo` closure: a team
    recognized as a national side (`quant.national_elo.is_national_team`) is
    priced off `nat_elo_table` (`national_elo_from` — falls back to the
    built-in static table, then `DEFAULT_NATIONAL_ELO`, when the team is
    unlisted); anything else is priced off the club `elo_table`
    (`data.sources.clubelo.elo_for` — falls back to its own 1500.0 default).

    CAVEAT — look-ahead: `nat_elo_table`/`elo_table` are single point-in-time
    snapshots the caller injects (e.g. today's live ClubElo/eloratings.net
    fetch), not a historical Elo as of each fixture's kickoff. A backtest run
    over past fixtures is therefore priced with CURRENT team strength, not the
    strength at the time of that match — this makes `build_model_fn`'s model
    signal indicative-only (a rough form check proxy) until per-date historical
    Elo snapshots are wired into the data layer; the sharp (Pinnacle) line
    devigged in `run_backtest` remains the properly-dated pre-match probability
    when it is present.
    """
    nat_elo_table = nat_elo_table or {}
    elo_table = elo_table or {}

    def _team_elo(name: str) -> float:
        if is_national_team(name):
            return national_elo_from(nat_elo_table, name)
        return elo_for(elo_table, name)

    def model_fn(fx: dict) -> dict:
        home = fx.get("home", "")
        away = fx.get("away", "")
        home_elo, away_elo = _team_elo(home), _team_elo(away)
        expected_goals = elo_to_expected_goals(home_elo, away_elo)
        probs = model_market_probs(expected_goals)
        return probs.get("winner", {})

    return model_fn
