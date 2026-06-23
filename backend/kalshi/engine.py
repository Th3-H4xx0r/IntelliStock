"""Lean Kalshi instance engine.

The decision is split out as pure functions (`plan_orders`, `should_execute`,
`execute_plan`) so it's unit-testable with a fake client and no network/DB. The
loop (`run_instance`) only adds DB I/O, the runCommand/kill-switch poll, and
notifications around that decision — it never owns the trading math.

Per-market fees: the fee on a YES contract depends on its price, so the engine
computes `fee_as_prob(price, live_rate)` per market rather than a single blanket
fee. An edge that ignores fees is fiction.

Execution gate (safety): demo/paper always executes; LIVE executes only when the
instance is explicitly `live_enabled`. This keeps real money behind a hard gate
until CLV is validated.
"""
from __future__ import annotations

from dataclasses import dataclass

from kalshi.models import KalshiMarket
from kalshi.edge import compute_edge
from kalshi.fees import fee_as_prob
from kalshi.risk import RiskCaps, size_order, check_caps


@dataclass(frozen=True)
class OrderIntent:
    market_ticker: str
    side: str          # 'yes'
    action: str        # 'buy'
    contracts: int
    limit_cents: int
    edge: float
    reason: str


def should_execute(environment: str, live_enabled: bool) -> bool:
    """Demo/paper executes freely; live requires the explicit gate."""
    env = (environment or "").lower()
    if env == "demo":
        return True
    if env in ("live", "prod"):
        return bool(live_enabled)
    return False


def plan_orders(
    *,
    fixture_id: str,
    league: str,
    fair: dict,
    markets: list[KalshiMarket],
    caps: RiskCaps,
    fee_rate: float,
    day_pnl_cents: int = 0,
    open_exposure_frac: float = 0.0,
    league_exposure_frac: float = 0.0,
) -> tuple[list[OrderIntent], str]:
    """Decide which YES contracts to buy for one fixture. Returns
    (intents, blocked_reason). When a cap blocks, returns ([], reason)."""
    ok, reason = check_caps(
        caps,
        day_pnl_cents=day_pnl_cents,
        open_exposure_frac=open_exposure_frac,
        league=league,
        league_exposure_frac=league_exposure_frac,
    )
    if not ok:
        return [], reason

    intents: list[OrderIntent] = []
    for m in markets:
        if m.side not in fair:
            continue
        fp = fair[m.side]
        fee = fee_as_prob(m.yes_ask_cents, fee_rate)
        e = compute_edge(fair_prob=fp, yes_ask_cents=m.yes_ask_cents, fee=fee)
        if e <= caps.edge_threshold:
            continue
        n = size_order(edge=e, yes_ask_cents=m.yes_ask_cents, caps=caps)
        if n <= 0:
            continue
        intents.append(
            OrderIntent(
                market_ticker=m.market_ticker,
                side="yes",
                action="buy",
                contracts=n,
                limit_cents=int(m.yes_ask_cents),
                edge=e,
                reason=f"fair {fp:.3f} vs {m.yes_ask_cents}c (fee {fee:.3f}) -> edge {e:.3f}",
            )
        )
    intents.sort(key=lambda i: i.edge, reverse=True)
    return intents, ""


@dataclass
class EngineConfig:
    instance_id: str
    brokerage_id: str
    environment: str          # 'demo' | 'live'
    live_enabled: bool
    caps: RiskCaps
    fee_rate: float = 0.07
    poll_seconds: int = 60
    tier: str = "medium"      # risk tier -> allowed market types
    reserve_frac: float = 0.3


def run_once(
    *,
    client,
    fixtures,
    fair_by_fixture: dict,
    caps: RiskCaps,
    fee_rate: float,
    environment: str,
    live_enabled: bool,
    cid_prefix: str,
    exposure_by_league: dict | None = None,
    open_exposure_frac: float = 0.0,
    day_pnl_cents: int = 0,
) -> list[dict]:
    """One scan tick: for each fixture with a known fair value, fetch its
    markets, plan orders, and execute them (gated by should_execute). Takes the
    client + precomputed fair values so it's integration-testable with a fake
    client and no DB. open_exposure_frac + day_pnl_cents feed the AGGREGATE risk
    caps (max-exposure, daily-loss) so they actually trip in the live loop.
    Returns per-fixture results."""
    exposure_by_league = exposure_by_league or {}
    dry = not should_execute(environment, live_enabled)
    out = []
    for fx in fixtures:
        fair = fair_by_fixture.get(fx.fixture_id)
        if not fair:
            continue
        markets = client.get_markets(fx.fixture_id)
        intents, reason = plan_orders(
            fixture_id=fx.fixture_id,
            league=fx.league,
            fair=fair,
            markets=markets,
            caps=caps,
            fee_rate=fee_rate,
            league_exposure_frac=exposure_by_league.get(fx.league, 0.0),
            open_exposure_frac=open_exposure_frac,
            day_pnl_cents=day_pnl_cents,
        )
        results = execute_plan(client, intents, dry_run=dry, cid_prefix=cid_prefix)
        out.append({"fixture_id": fx.fixture_id, "blocked": reason, "dry_run": dry, "results": results})
    return out


def _make_logger(instance_id: str):  # pragma: no cover - integration I/O
    """Attach the per-instance live-trading log file (so /instances/{id}/live-logs
    tails it, same mechanism as broker.py) and return a log(msg, color) fn that
    degrades to print() on failure."""
    try:
        import live_state as _ls
        from intellistock_logger import intellistock_logger as _logger
        f, _path = _ls.open_live_log(instance_id)
        _logger.set_context_log_file("live_trading", f)

        def _log(msg, color="white"):
            try:
                _logger.log(msg, color, service="KALSHI")
            except Exception:
                pass
        return _log
    except Exception:
        def _log(msg, color="white"):
            try:
                print(f"[KALSHI] {msg}")
            except Exception:
                pass
        return _log


def run_instance(config: EngineConfig) -> None:  # pragma: no cover - integration loop
    """The lean instance loop. Polls runCommand + the kill switch each tick like
    equities instances, ingests odds, computes fair value, runs run_once, and
    streams progress to the per-instance live log. DB-bound; the trading math
    lives in the tested helpers above — this only adds I/O, polling, logging.
    """
    import time
    from kalshi import db as kdb
    from kalshi.client import KalshiClient
    from kalshi.fair_value import fair_from_odds
    from kalshi.ingest_odds import parse_three_way
    from kalshi.models import Fixture

    log = _make_logger(config.instance_id)
    log(f"Kalshi engine starting — instance={config.instance_id} · env={config.environment} · "
        f"live_enabled={config.live_enabled}", "green")

    conn = kdb.get_conn()
    kdb.ensure_tables(conn)
    brokerage = kdb._r.db(kdb.DB_NAME).table("BrokerageAccounts").get(config.brokerage_id).run(conn) or {}
    from secret_store import decrypt
    client = KalshiClient(
        key_id=(brokerage.get("kalshi_key_id") or "").strip(),
        private_key_pem=decrypt(brokerage.get("kalshi_private_key")) or "",
        environment=config.environment,
    )
    log(f"Connected to Kalshi ({config.environment}). Polling every {config.poll_seconds}s · "
        f"bankroll ${config.caps.bankroll_cents / 100:.0f} · edge > {config.caps.edge_threshold:.1%} · "
        f"¼-Kelly {config.caps.kelly_fraction}", "white")

    from kalshi.quant.elo import elo_to_expected_goals
    from kalshi.quant.national_elo import is_national_team, national_elo
    from kalshi.data.sources.clubelo import fetch_elo_table, elo_for
    from kalshi.data.sources.news import fetch_match_news, summarize_news_items
    from kalshi.data import discovery
    from kalshi.feature_models import MatchFeatures, TeamForm
    from kalshi.intelligence.analyst_panel import analyze as analyst_analyze, make_llm_call
    from kalshi.orchestrator import plan_and_allocate

    # Resolve the configured analyst LLM model (reads news + adjusts probabilities).
    # Optional: None -> the analyst is a no-op and the engine trades model-only.
    llm_call = None
    try:
        _cfg0 = inst.get("kalshi_config") or {} if (inst := kdb._r.db(kdb.DB_NAME).table("Instances").get(config.instance_id).run(conn)) else {}
        _model_id = _cfg0.get("model")
        if _model_id:
            _mdoc = kdb._r.db(kdb.DB_NAME).table("Models").get(_model_id).run(conn) or {}
            llm_call = make_llm_call(_mdoc)
            log(f"Analyst LLM: {_mdoc.get('name') or _model_id} (reads injuries/lineups/news per match)."
                if llm_call else f"Analyst LLM '{_model_id}' could not init — trading model-only.", "white")
        else:
            log("No analyst LLM configured — trading on the statistical model only.", "white")
    except Exception as e:
        log(f"Analyst LLM resolution failed ({e}); trading model-only.", "yellow")

    elo_table: dict = {}
    tick = 0
    while True:
        # Control-plane poll. A transient DB/connection blip must NOT kill the
        # 24/7 engine — log and retry next tick instead of propagating.
        try:
            inst = kdb._r.db(kdb.DB_NAME).table("Instances").get(config.instance_id).run(conn) or {}
        except Exception as e:
            log(f"control-plane poll failed ({type(e).__name__}: {e}); retrying next tick.", "yellow")
            time.sleep(config.poll_seconds)
            continue
        if not inst.get("runCommand", False):
            log("runCommand=False — stopping engine.", "yellow")
            break

        tick += 1
        try:
            # 1) Team strength (free ClubElo; refresh ~hourly worth of ticks).
            if not elo_table or tick % 60 == 1:
                elo_table = fetch_elo_table() or {}
                log(f"tick {tick}: loaded {len(elo_table)} club Elo ratings.",
                    "white" if elo_table else "yellow")

            # 2) Discover open Kalshi soccer markets by series (World Cup = KXWCGAME).
            soccer, total_raw = [], 0
            for series in discovery.DEFAULT_SOCCER_SERIES:
                try:
                    rs = (client.list_markets(status="open", series_ticker=series, limit=500) or {}).get("markets", []) or []
                except Exception as e:
                    log(f"tick {tick}: list_markets({series}) failed: {type(e).__name__}: {e}", "red")
                    rs = []
                total_raw += len(rs)
                for m in rs:
                    p = discovery.parse_kalshi_market(m)
                    if p["market_type"] != "other" and p["home"] and p["away"]:
                        soccer.append(p)
            by_event = discovery.group_by_event(soccer)
            log(f"tick {tick}: scanned series {discovery.DEFAULT_SOCCER_SERIES} → {total_raw} markets, "
                f"{len(soccer)} priceable, {len(by_event)} matches.", "cyan")
            if not by_event:
                log(f"tick {tick}: no priceable soccer matches in {discovery.DEFAULT_SOCCER_SERIES}. "
                    f"Add your leagues' series tickers to discovery.DEFAULT_SOCCER_SERIES.", "yellow")
                time.sleep(config.poll_seconds)
                continue

            # 3) Per-match: model pricing (national OR club Elo) + LLM analyst reading news.
            fixtures_in = []
            for event_ticker, mkts in by_event.items():
                home = next((m["home"] for m in mkts if m["home"]), None)
                away = next((m["away"] for m in mkts if m["away"]), None)
                if not home or not away:
                    continue
                he = national_elo(home) if is_national_team(home) else elo_for(elo_table, home)
                ae = national_elo(away) if is_national_team(away) else elo_for(elo_table, away)
                eg = elo_to_expected_goals(he, ae)

                analyst_out = {"adjustments": {}, "rationales": {}}
                if llm_call is not None:
                    try:
                        news = summarize_news_items(fetch_match_news(home, away))
                        feats = MatchFeatures(fixture_id=event_ticker, home=home, away=away,
                                              home_form=TeamForm(elo=he), away_form=TeamForm(elo=ae))
                        mtypes = sorted({m["market_type"] for m in mkts})
                        analyst_out = analyst_analyze(feats, mtypes, news=news, llm_call=llm_call)
                        rats = list((analyst_out.get("rationales") or {}).values())
                        if rats:
                            log(f"tick {tick}: analyst on {home} vs {away}: {rats[0]}", "white")
                    except Exception as e:
                        log(f"tick {tick}: analyst failed for {home} vs {away}: {e}", "yellow")

                fixtures_in.append({
                    "fixture_id": event_ticker, "expected_goals": eg, "sharp_probs": {},
                    "analyst": analyst_out,
                    "kalshi_markets": [
                        {"market_ticker": m["market_ticker"], "market_type": m["market_type"],
                         "side": m["side"], "yes_ask_cents": m["yes_ask_cents"]}
                        for m in mkts
                    ],
                    "liquidity": 500, "hours_to_kickoff": 24, "model_confidence": 0.6,
                })
                log(f"tick {tick}: {home} vs {away} — Elo {he:.0f}/{ae:.0f}, model xG {eg[0]:.2f}/{eg[1]:.2f} "
                    f"({len(mkts)} markets).", "white")

            # 4) Plan + allocate across the forward book.
            ts = _iso_now()
            plan = plan_and_allocate(
                fixtures_in, instance_id=config.instance_id, brokerage_id=config.brokerage_id, ts=ts,
                tier=config.tier, caps=config.caps, fee_rate=config.fee_rate,
                edge_threshold=config.caps.edge_threshold, reserve_frac=config.reserve_frac,
                expected_better_soon=False,
            )
            allocations, decisions = plan["allocations"], plan["decisions"]
            log(f"tick {tick}: {len(decisions)} candidate(s) evaluated → {len(allocations)} to place "
                f"(tier {config.tier}, edge > {config.caps.edge_threshold:.1%}).", "cyan")

            # 5) Execute (gated) + 6) write decision rows.
            dry = not should_execute(config.environment, config.live_enabled)
            alloc_by_id = {a["id"]: a for a in allocations}
            for d in decisions:
                a = alloc_by_id.get(f"{d['fixture_id']}|{d['market_ticker']}")
                if a and not dry:
                    try:
                        client.submit_order(
                            market_ticker=d["market_ticker"], side="yes", action="buy",
                            contracts=a["contracts"], limit_cents=a["price_cents"],
                            client_order_id=f"{config.instance_id}-{d['market_ticker']}-{ts}",
                        )
                        log(f"tick {tick}: PLACED {a['contracts']}x {d['market_ticker']} @ {a['price_cents']}c "
                            f"(edge {(d['edge'] or 0):.1%}).", "green")
                    except Exception as e:
                        log(f"tick {tick}: order {d['market_ticker']} failed: {e}", "red")
                        d["decision"], d["block_reason"] = "blocked", str(e)
                elif a and dry:
                    log(f"tick {tick}: DRY-RUN would place {a['contracts']}x {d['market_ticker']} @ "
                        f"{a['price_cents']}c (edge {(d['edge'] or 0):.1%}).", "cyan")
                try:
                    kdb._r.db(kdb.DB_NAME).table("kalshi_decisions").insert(d, conflict="replace").run(conn)
                except Exception:
                    pass

            # 7) Snapshot.
            bal = client.get_balance()
            kdb.save_portfolio_snapshot(
                conn, brokerage_id=config.brokerage_id, ts=ts,
                value_cents=bal.portfolio_value_cents, cash_cents=bal.cash_cents,
            )
            log(f"tick {tick}: portfolio ${bal.portfolio_value_cents / 100:.2f} "
                f"(cash ${bal.cash_cents / 100:.2f}).", "white")
        except Exception as e:
            log(f"tick {tick} error: {type(e).__name__}: {e}", "red")

        time.sleep(config.poll_seconds)

    try:
        conn.close()
    except Exception:
        pass
    log("Kalshi engine stopped.", "yellow")


def _iso_now() -> str:  # pragma: no cover - thin clock wrapper
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def execute_plan(client, intents: list[OrderIntent], *, dry_run: bool, cid_prefix: str) -> list[dict]:
    """Submit each intent unless dry_run. Returns per-intent results. A failed
    submit is captured, not raised, so one bad order can't abort the batch."""
    results = []
    for i in intents:
        cid = f"{cid_prefix}-{i.market_ticker}"
        if dry_run:
            results.append({"intent": i, "submitted": False, "client_order_id": cid})
            continue
        try:
            ref = client.submit_order(
                market_ticker=i.market_ticker,
                side=i.side,
                action=i.action,
                contracts=i.contracts,
                limit_cents=i.limit_cents,
                client_order_id=cid,
            )
            results.append({"intent": i, "submitted": True, "order": ref})
        except Exception as e:  # pragma: no cover - exercised in integration
            results.append({"intent": i, "submitted": False, "error": str(e), "client_order_id": cid})
    return results
