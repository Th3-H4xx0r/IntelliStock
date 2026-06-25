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

from dataclasses import dataclass, field

from kalshi.models import KalshiMarket
from kalshi.edge import compute_edge
from kalshi.fees import fee_as_prob
from kalshi.risk import RiskCaps, size_order, check_caps
from kalshi.live.live_decision import InPlayCaps


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
    live_monitoring: bool = True
    live_poll_seconds: int = 30
    inplay_caps: InPlayCaps = field(default_factory=InPlayCaps)
    analyst_max_calls: int = 10   # cap on LLM analyst calls per tick (cost control)
    # Sharp-odds anchor: fair value from de-vig'd bookmaker odds -> edge vs Kalshi.
    odds_api_key: str = ""
    sharp_weight: float = 0.7
    devig_method: str = "power"
    odds_refresh_secs: int = 3600
    odds_regions: str = "eu,uk,us"
    # Kalshi series to scan (empty = use discovery.DEFAULT_SOCCER_SERIES).
    soccer_series: list = field(default_factory=list)
    # ESPN league slugs for live-score lookups (empty = use scoreboard.DEFAULT_SCOREBOARD_LEAGUES).
    scoreboard_leagues: list = field(default_factory=list)


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
    from kalshi.intelligence.pricing import build_market_probs, model_market_probs
    from kalshi.live import match_clock, monitor as live_monitor, scoreboard as espn
    from kalshi.data.sources import odds_api
    from kalshi.fair_value import sharp_probs_from_quote
    from kalshi.orchestrator import plan_and_allocate

    # Resolve the configured analyst LLM model (reads news + adjusts probabilities).
    # Optional: None -> the analyst is a no-op and the engine trades model-only.
    llm_call = None
    _model_name = ""
    try:
        _cfg0 = inst.get("kalshi_config") or {} if (inst := kdb._r.db(kdb.DB_NAME).table("Instances").get(config.instance_id).run(conn)) else {}
        _model_id = _cfg0.get("model")
        if _model_id:
            _mdoc = kdb._r.db(kdb.DB_NAME).table("Models").get(_model_id).run(conn) or {}
            _model_name = _mdoc.get("name") or _model_id
            log(f"Resolving analyst LLM '{_model_name}' (provider={_mdoc.get('provider')!r})…", "white")
            llm_call = make_llm_call(_mdoc, log=log, instance_id=config.instance_id)   # logs provider/model + every call; tags token usage
            log(f"Analyst LLM ready: {_model_name} — reads injuries/lineups/news per match."
                if llm_call else f"Analyst LLM '{_model_name}' could not init — trading model-only.",
                "green" if llm_call else "yellow")
        else:
            log("No analyst LLM configured — trading on the statistical model only.", "white")
    except Exception as e:
        log(f"Analyst LLM resolution failed ({type(e).__name__}: {e}); trading model-only.", "yellow")

    elo_table: dict = {}
    analyst_cache: dict = {}    # event_ticker -> {"ts": wall, "out": dict}; news+LLM TTL (wall-clock)
    price_history: dict = {}    # market_ticker -> [mid_cents...]; live event detection
    adds_by_match: dict = {}    # fixture_id -> in-play add count
    live_llm_cd: dict = {}      # fixture_id -> wall ts of last in-play LLM read (cooldown)
    live_news_cache: dict = {}  # fixture_id -> {"ts": wall, "snip": str}; live-card news (5 min)
    odds_cache: dict = {}       # sport_key -> {"ts": wall, "events": [...], "quota": int|None}
    espn_cache: dict = {"ts": 0.0, "board": []}   # ESPN live scores+clock (timing only, ~30s)
    raw_dumped = False           # one-time raw-market field dump (price diagnostics)
    placed_markets: set = set()  # market_tickers already held/ordered — don't re-order
    _soccer_series = config.soccer_series or discovery.DEFAULT_SOCCER_SERIES
    _scoreboard_leagues = config.scoreboard_leagues or espn.DEFAULT_SCOREBOARD_LEAGUES
    odds_sport_keys = [k for k in (odds_api.sport_key_for_series(s)
                                   for s in _soccer_series) if k]
    if not config.odds_api_key:
        log("No odds API key — pricing model-only. Set odds_api_key (or ODDS_API_KEY env) "
            "to anchor fair value to sharp bookmaker odds and trade where Kalshi disagrees.", "yellow")
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
            now_wall = time.time()
            # 1) Team strength (free ClubElo; refresh ~hourly worth of ticks).
            if not elo_table or tick % 60 == 1:
                elo_table = fetch_elo_table() or {}
                log(f"tick {tick}: loaded {len(elo_table)} club Elo ratings.",
                    "white" if elo_table else "yellow")

            # 2) Discover open Kalshi soccer markets by series (World Cup = KXWCGAME).
            soccer, total_raw = [], 0
            for series in _soccer_series:
                try:
                    rs = (client.list_markets(status="open", series_ticker=series, limit=500) or {}).get("markets", []) or []
                except Exception as e:
                    log(f"tick {tick}: list_markets({series}) failed: {type(e).__name__}: {e}", "red")
                    rs = []
                total_raw += len(rs)
                if tick == 1 and rs and not raw_dumped:
                    _s = rs[0]
                    log(f"raw prices — yes_ask_dollars={_s.get('yes_ask_dollars')} "
                        f"yes_bid_dollars={_s.get('yes_bid_dollars')} "
                        f"last_price_dollars={_s.get('last_price_dollars')} "
                        f"ask_size={_s.get('yes_ask_size_fp')} status={_s.get('status')}", "cyan")
                    raw_dumped = True
                for m in rs:
                    p = discovery.parse_kalshi_market(m)
                    if p["market_type"] != "other" and p["home"] and p["away"]:
                        p["series"] = series
                        # Kalshi returns prices under *_dollars fields (0.40 = 40c);
                        # fall back to the bare/cents names + last trade for the ask.
                        p["yes_ask_cents"] = price_cents(m, "yes_ask_dollars", "yes_ask", "last_price_dollars", "last_price")
                        p["yes_bid_cents"] = price_cents(m, "yes_bid_dollars", "yes_bid")
                        _a, _b = p["yes_ask_cents"], p["yes_bid_cents"]
                        p["mid_cents"] = (_a + _b) / 2.0 if (_a and _b) else (_a or _b or None)
                        p["kickoff_ts"] = match_clock.kickoff_from_market(m)
                        soccer.append(p)
            by_event = discovery.group_by_event(soccer)
            log(f"tick {tick}: scanned series {_soccer_series} → {total_raw} markets, "
                f"{len(soccer)} priceable, {len(by_event)} matches.", "cyan")
            if not by_event:
                log(f"tick {tick}: no priceable soccer matches in {_soccer_series}. "
                    f"Add your leagues' series tickers to kalshi_config.soccer_series or "
                    f"discovery.DEFAULT_SOCCER_SERIES.", "yellow")
                time.sleep(config.poll_seconds)
                continue

            # 2b) Sharp-odds anchor: refresh bookmaker odds (cached + budget-aware) so
            # fair value is anchored to the de-vig'd sharp consensus, not the model
            # alone. Odds are environment-agnostic (same on demo + live) — only the
            # Kalshi price we compare against differs.
            all_events = []
            if config.odds_api_key:
                for sk in odds_sport_keys:
                    c = odds_cache.get(sk)
                    if c and (now_wall - c["ts"]) < config.odds_refresh_secs:
                        all_events.extend(c["events"])
                        continue
                    if c and c.get("quota") is not None and c["quota"] <= 1:
                        all_events.extend(c["events"])   # out of monthly quota — reuse cache
                        if tick % 30 == 1:
                            log(f"tick {tick}: odds quota exhausted for {sk}; reusing cached odds.", "yellow")
                        continue
                    evs, quota = odds_api.fetch_events(sk, config.odds_api_key, regions=config.odds_regions)
                    odds_cache[sk] = {"ts": now_wall, "events": evs, "quota": quota}
                    all_events.extend(evs)
                    try:
                        kdb.bump_scan_budget(conn, _iso_now())
                    except Exception:
                        pass
                    log(f"tick {tick}: sharp odds — fetched {len(evs)} events for {sk}"
                        + (f" (quota left {quota})" if quota is not None else "") + ".", "white")

            # 2c) Live scores/clock (ESPN) — TIMING ONLY (not pricing): gives the true
            # in-play minute so the live monitor knows a match is actually live and can
            # act, instead of waiting on a price move. Cached ~30s.
            if now_wall - espn_cache["ts"] > 30:
                try:
                    espn_cache["board"] = espn.fetch_scoreboard(_scoreboard_leagues)
                except Exception:
                    espn_cache["board"] = espn_cache.get("board") or []
                espn_cache["ts"] = now_wall
            board = espn_cache.get("board") or []

            # 3) Per-match: phase split + model pricing. Pregame -> pre-match planner,
            # LIVE -> in-play monitor. To control latency + LLM cost, the analyst runs
            # ONLY on the most promising pregame matches: those whose best model edge is
            # within the analyst's ±cap of the edge bar (so the LLM could plausibly tip
            # them), capped at analyst_max_calls. Matches nowhere near tradeable are
            # priced model-only — no tokens spent. Results cached per match ~30 min.
            news_refresh_secs = 1800

            # 3a) Pre-pass: classify phase + model-price every match (no LLM yet).
            metas = []
            for event_ticker, mkts in by_event.items():
                home = next((m["home"] for m in mkts if m["home"]), None)
                away = next((m["away"] for m in mkts if m["away"]), None)
                if not home or not away:
                    continue
                series = next((m.get("series") for m in mkts if m.get("series")), "")
                national_series = series in discovery.NATIONAL_TEAM_SERIES
                kickoff_ts = next((m.get("kickoff_ts") for m in mkts if m.get("kickoff_ts")), None)
                phase, elapsed = match_clock.phase_for_market(
                    kickoff_ts=kickoff_ts, ticker=event_ticker, now_ts=now_wall)
                # ESPN's real clock (when matched) overrides the date heuristic: it
                # confirms a match is actually in-play and gives the true minute, so
                # the live monitor can open instead of waiting on a price move.
                if board:
                    _ep, _emin = espn.live_status(espn.match_score(board, home, away))
                    if _ep:
                        phase = _ep
                        if _emin is not None:
                            elapsed = _emin
                if phase == match_clock.ENDED:
                    continue  # settled / over — nothing to price

                def _team_elo(name, _ns=national_series):
                    if _ns or is_national_team(name):
                        return national_elo(name)
                    return elo_for(elo_table, name)

                he, ae = _team_elo(home), _team_elo(away)
                eg = elo_to_expected_goals(he, ae)
                # Sharp anchor: de-vig the matched bookmaker odds, then fuse sharp+model.
                # No match -> sharp_probs={} -> fused == model (degrade-safe).
                sharp = {}
                if all_events:
                    ev = odds_api.match_event(all_events, home, away)
                    if ev:
                        sharp = sharp_probs_from_quote(odds_api.quote_for_event(ev), config.devig_method)
                fused = build_market_probs(eg, sharp, {}, w_sharp=config.sharp_weight)
                best_edge = -1.0
                for m in mkts:
                    prob = (fused.get(m["market_type"], {}) or {}).get(m["side"])
                    ask = m.get("yes_ask_cents") or 0
                    if prob is not None and ask > 0:
                        best_edge = max(best_edge, prob - ask / 100.0)
                # Crests (any league): ESPN provides national flags AND club badges.
                _sc = espn.match_score(board, home, away) if board else None
                home_logo = (_sc or {}).get("home_logo", "")
                away_logo = (_sc or {}).get("away_logo", "")
                metas.append({
                    "id": event_ticker, "home": home, "away": away, "mkts": mkts, "eg": eg,
                    "fused": fused, "sharp_probs": sharp, "sharp_matched": bool(sharp),
                    "he": he, "ae": ae, "phase": phase, "elapsed": elapsed,
                    "home_logo": home_logo, "away_logo": away_logo,
                    "mtypes": sorted({m["market_type"] for m in mkts}), "best_edge": best_edge,
                })

            # Sharp-odds coverage this tick.
            if config.odds_api_key:
                _matched = sum(1 for m in metas if m["sharp_matched"])
                log(f"tick {tick}: sharp anchor — matched {_matched}/{len(metas)} match(es) to "
                    f"bookmaker odds (w_sharp {config.sharp_weight:.0%}); edge = sharp vs Kalshi price."
                    + ("" if _matched else " No matches found in the odds feed — priced model-only."),
                    "cyan" if _matched else "yellow")

            # 3b) Pick the analyst targets (cost cap) among pregame matches.
            pregame_metas = [m for m in metas if m["phase"] != match_clock.LIVE]
            targets = (select_analyst_targets(
                pregame_metas, edge_threshold=config.caps.edge_threshold,
                max_calls=config.analyst_max_calls) if llm_call is not None else set())
            if llm_call is not None:
                log(f"tick {tick}: analyst targets — {len(targets)}/{len(pregame_metas)} pregame "
                    f"match(es) within reach of the {config.caps.edge_threshold:.1%} bar "
                    f"(cap {config.analyst_max_calls}); the rest priced model-only to save tokens.", "cyan")
            # Diagnostics: liquidity (are there asks to buy?) + the closest match's
            # per-side fair vs ask. This pins down WHY nothing trades — no ask
            # (illiquid), a real negative edge (price agrees with sharp), or a hit.
            _asks = sum(1 for mt in metas for m in mt["mkts"] if (m.get("yes_ask_cents") or 0) > 0)
            _tot = sum(len(mt["mkts"]) for mt in metas) or 1
            log(f"tick {tick}: liquidity — {_asks}/{_tot} contracts have a YES ask to buy"
                + ("" if _asks else " (nothing to lift — no offers on these markets right now)") + ".",
                "white" if _asks else "yellow")
            if pregame_metas:
                m = max(pregame_metas, key=lambda x: x["best_edge"])
                fused = m["fused"]
                sides = []
                for mk in m["mkts"]:
                    prob = (fused.get(mk["market_type"], {}) or {}).get(mk["side"])
                    ask = mk.get("yes_ask_cents") or 0
                    if prob is not None:
                        sides.append(f"{mk['side']} fair {prob:.0%} @ {ask}c"
                                     + (f" → edge {prob - ask / 100.0:+.1%}" if ask > 0 else " (no ask)"))
                log(f"tick {tick}: closest — {m['home']} v {m['away']}: " + "; ".join(sides)
                    + f" (need > {config.caps.edge_threshold:.1%}).", "white")

            # 3c) Build the planner + monitor inputs (analyst only for the targets).
            fixtures_in, live_matches = [], []
            for meta in metas:
                home, away, mkts, eg, phase = meta["home"], meta["away"], meta["mkts"], meta["eg"], meta["phase"]
                he, ae, elapsed, mtypes = meta["he"], meta["ae"], meta["elapsed"], meta["mtypes"]
                log(f"tick {tick}: {home} vs {away} [{phase}] — Elo {he:.0f}/{ae:.0f}, model xG "
                    f"{eg[0]:.2f}/{eg[1]:.2f}; markets: {', '.join(mtypes)}.", "white")

                if phase == match_clock.LIVE:
                    fused = meta["fused"]   # sharp-anchored when odds matched, else model
                    model_probs, mk_list = {}, []
                    for m in mkts:
                        model_probs[m["market_ticker"]] = (fused.get(m["market_type"], {}) or {}).get(m["side"])
                        mk_list.append({
                            "market_ticker": m["market_ticker"], "side": m["side"],
                            "market_type": m["market_type"], "yes_ask_cents": m["yes_ask_cents"],
                            "yes_bid_cents": m.get("yes_bid_cents", 0), "mid_cents": m.get("mid_cents"),
                        })
                    live_matches.append({
                        "fixture_id": meta["id"], "home": home, "away": away,
                        "phase": phase, "elapsed_min": elapsed,
                        "model_probs": model_probs, "markets": mk_list,
                    })
                    continue

                # Pregame: analyst only for selected targets (news-reading, cached).
                analyst_out = {"adjustments": {}, "rationales": {}, "shortlist": []}
                if llm_call is not None and meta["id"] in targets:
                    cached = analyst_cache.get(meta["id"])
                    if cached and (now_wall - cached["ts"]) < news_refresh_secs:
                        analyst_out = cached["out"]
                        if analyst_out.get("adjustments"):
                            age_m = int((now_wall - cached["ts"]) // 60)
                            log(f"tick {tick}:   ↳ analyst (cached {age_m}m): "
                                f"{_fmt_adj(analyst_out['adjustments'])}.", "white")
                    else:
                        try:
                            articles = fetch_match_news(home, away)
                            news = summarize_news_items(articles)
                            log(f"tick {tick}:   ↳ analyst (edge {meta['best_edge']:+.1%}): read "
                                f"{len(articles)} articles for {home} vs {away} → calling {_model_name}…", "white")
                            feats = MatchFeatures(fixture_id=meta["id"], home=home, away=away,
                                                  home_form=TeamForm(elo=he), away_form=TeamForm(elo=ae))
                            analyst_out = analyst_analyze(feats, mtypes, news=news, llm_call=llm_call)
                            analyst_cache[meta["id"]] = {"ts": now_wall, "out": analyst_out}
                            adj = analyst_out.get("adjustments") or {}
                            rats = analyst_out.get("rationales") or {}
                            if adj or rats:
                                first_rat = next(iter(rats.values()), "")
                                log(f"tick {tick}:   ↳ analyst {home} vs {away}: {_fmt_adj(adj)}"
                                    + (f" — “{first_rat}”" if first_rat else "."), "cyan")
                            else:
                                log(f"tick {tick}:   ↳ analyst {home} vs {away}: no usable LLM output "
                                    f"— priced on model+Elo only.", "yellow")
                        except Exception as e:
                            log(f"tick {tick}:   ↳ analyst failed for {home} vs {away}: "
                                f"{type(e).__name__}: {e}", "yellow")

                fixtures_in.append({
                    "fixture_id": meta["id"], "expected_goals": eg,
                    "sharp_probs": meta["sharp_probs"],
                    "analyst": analyst_out,
                    "kalshi_markets": [
                        {"market_ticker": m["market_ticker"], "market_type": m["market_type"],
                         "side": m["side"], "yes_ask_cents": m["yes_ask_cents"]}
                        for m in mkts
                    ],
                    "liquidity": 500, "hours_to_kickoff": 24, "model_confidence": 0.6,
                })

            # 4) Plan + allocate across the forward book.
            ts = _iso_now()
            plan = plan_and_allocate(
                fixtures_in, instance_id=config.instance_id, brokerage_id=config.brokerage_id, ts=ts,
                tier=config.tier, caps=config.caps, fee_rate=config.fee_rate,
                edge_threshold=config.caps.edge_threshold, reserve_frac=config.reserve_frac,
                expected_better_soon=False, w_sharp=config.sharp_weight,
            )
            allocations, decisions = plan["allocations"], plan["decisions"]
            log(f"tick {tick}: {len(decisions)} candidate(s) evaluated → {len(allocations)} to place "
                f"(tier {config.tier}, edge > {config.caps.edge_threshold:.1%}).", "cyan")

            # Readable, LEAGUE-AGNOSTIC team names + crests for the UI: names from
            # discovery (any league/club), logos from ESPN when matched. Stamped on
            # every decision row so orders/positions never show a raw ticker.
            fixture_info = {m["id"]: {"home": m["home"], "away": m["away"],
                                      "home_logo": m.get("home_logo", ""),
                                      "away_logo": m.get("away_logo", "")} for m in metas}
            for d in decisions:
                fi = fixture_info.get(d.get("fixture_id"))
                if fi:
                    d["home"], d["away"] = fi["home"], fi["away"]
                    d["home_logo"], d["away_logo"] = fi["home_logo"], fi["away_logo"]

            # 5) Execute (gated) + 6) write decision rows. Fetch current positions
            # first and skip markets we already hold or already ordered this run —
            # so we don't re-submit the same order every tick.
            dry = not should_execute(config.environment, config.live_enabled)
            try:
                positions = client.get_positions()
            except Exception as e:
                log(f"tick {tick}: get_positions failed: {type(e).__name__}: {e}", "yellow")
                positions = []
            positions_by_ticker = {p.market_ticker: p for p in positions}
            try:
                resting_tickers = {o["market_ticker"] for o in client.get_resting_orders()}
            except Exception:
                resting_tickers = set()
            # Re-sync from the BROKER's actual state each tick (positions + resting
            # orders) — NOT a forever-accumulating set. If you manually sell on Kalshi,
            # the position vanishes here, so the bot can re-buy it; if you still hold
            # it, it won't double-order. This is the source of truth.
            placed_markets = set(positions_by_ticker) | resting_tickers

            alloc_by_id = {a["id"]: a for a in allocations}
            for d in decisions:
                a = alloc_by_id.get(f"{d['fixture_id']}|{d['market_ticker']}")
                if a and d["market_ticker"] in placed_markets:
                    log(f"tick {tick}: already positioned/ordered {d['market_ticker']} — skipping.", "white")
                    d["decision"], d["block_reason"] = "skipped", "already positioned"
                elif a and not dry:
                    try:
                        client.submit_order(
                            market_ticker=d["market_ticker"], side="yes", action="buy",
                            contracts=a["contracts"], limit_cents=a["price_cents"],
                            client_order_id=f"{config.instance_id}-{d['market_ticker']}-{ts}",
                        )
                        placed_markets.add(d["market_ticker"])
                        log(f"tick {tick}: PLACED {a['contracts']}x {d['market_ticker']} @ {a['price_cents']}c "
                            f"(edge {(d['edge'] or 0):.1%}).", "green")
                    except Exception as e:
                        log(f"tick {tick}: order {d['market_ticker']} failed: {e}", "red")
                        d["decision"], d["block_reason"] = "blocked", str(e)
                elif a and dry:
                    placed_markets.add(d["market_ticker"])   # don't spam dry-run logs either
                    log(f"tick {tick}: DRY-RUN would place {a['contracts']}x {d['market_ticker']} @ "
                        f"{a['price_cents']}c (edge {(d['edge'] or 0):.1%}).", "cyan")
                try:
                    kdb._r.db(kdb.DB_NAME).table("kalshi_decisions").insert(d, conflict="replace").run(conn)
                except Exception:
                    pass

            # 8) Live in-match monitoring (two-way) for in-play matches.
            if config.live_monitoring and live_matches:
                def _live_llm_tilt(match, mk, move):
                    """On a material move, re-read fresh news + ask the analyst for a
                    bounded tilt on the moved market's side (per-fixture 5-min cooldown)."""
                    if llm_call is None:
                        return 0.0
                    fid = match.get("fixture_id", "")
                    if (now_wall - live_llm_cd.get(fid, 0)) < 300:
                        return 0.0
                    live_llm_cd[fid] = now_wall
                    try:
                        news = summarize_news_items(fetch_match_news(match["home"], match["away"]))
                        feats = MatchFeatures(fixture_id=fid, home=match["home"], away=match["away"])
                        out = analyst_analyze(feats, [mk.get("market_type", "winner")], news=news, llm_call=llm_call)
                        tilt = float((out.get("adjustments") or {}).get(mk.get("market_type"), 0.0) or 0.0)
                        if tilt:
                            log(f"tick {tick}: live analyst tilt {tilt:+.3f} on {mk.get('market_ticker')} "
                                f"after {move.direction} move ({move.delta_cents:+.0f}c).", "cyan")
                        return tilt
                    except Exception as e:
                        log(f"tick {tick}: live analyst failed: {type(e).__name__}: {e}", "yellow")
                        return 0.0

                live_dry = not should_execute(config.environment, config.live_enabled)
                live_rows = live_monitor.run_live_step(
                    client=client, live_matches=live_matches, price_history=price_history,
                    adds_by_match=adds_by_match, positions_by_ticker=positions_by_ticker,
                    caps=config.inplay_caps, dry_run=live_dry, instance_id=config.instance_id,
                    brokerage_id=config.brokerage_id, ts=ts, llm=_live_llm_tilt, log=log,
                    already_placed=placed_markets,
                )
                acted = sum(1 for r in live_rows if r.get("decision") == "placed")
                log(f"tick {tick}: live monitor — {len(live_matches)} in-play match(es), "
                    f"{len(live_rows)} markets watched, {acted} order(s).", "magenta")
                for r in live_rows:
                    try:
                        kdb._r.db(kdb.DB_NAME).table("kalshi_decisions").insert(r, conflict="replace").run(conn)
                    except Exception:
                        pass

                # 8b) Persist live-match cards (score + market probs + event + news +
                # decisions) for the web/mobile live view.
                from kalshi.live import cards as _cards
                _sb = espn   # reuse the board fetched in 2c
                rows_by_fixture = {}
                for r in live_rows:
                    rows_by_fixture.setdefault(r["fixture_id"], []).append(r)
                for match in live_matches:
                    fid = match["fixture_id"]
                    frows = rows_by_fixture.get(fid, [])
                    sc = _sb.match_score(board, match["home"], match["away"])
                    score = {"home": sc.get("home_score"), "away": sc.get("away_score"),
                             "clock": sc.get("clock"), "detail": sc.get("detail"),
                             "state": sc.get("state")} if sc else None
                    nc = live_news_cache.get(fid)
                    if nc and (now_wall - nc["ts"]) < 300:
                        snip = nc["snip"]
                    else:
                        try:
                            snip = summarize_news_items(fetch_match_news(match["home"], match["away"]), limit=3)
                        except Exception:
                            snip = nc["snip"] if nc else ""
                        live_news_cache[fid] = {"ts": now_wall, "snip": snip}
                    decs = [{"market_ticker": d["market_ticker"], "side": d.get("side"),
                             "action": d.get("live_action"), "decision": d.get("decision"),
                             "reason": d.get("block_reason") or d.get("llm_rationale") or "",
                             "size": d.get("size", 0)} for d in frows]
                    last_event = next((d.get("event") for d in frows if d.get("event")), "")
                    card = _cards.build_live_card(
                        instance_id=config.instance_id, fixture_id=fid,
                        home=match["home"], away=match["away"],
                        market_probs=_cards.market_probs_from_markets(match["markets"]),
                        score=score, elapsed_min=match.get("elapsed_min"),
                        event=last_event, news=snip, decisions=decs, ts=ts,
                        home_logo=(sc or {}).get("home_logo", ""),
                        away_logo=(sc or {}).get("away_logo", ""))
                    try:
                        kdb._r.db(kdb.DB_NAME).table("kalshi_live").insert(card, conflict="replace").run(conn)
                    except Exception:
                        pass

            # 8c) Prune live cards for matches no longer in play this tick.
            if config.live_monitoring:
                try:
                    keep = {f"{config.instance_id}|{m['fixture_id']}" for m in live_matches}
                    for row in kdb._r.db(kdb.DB_NAME).table("kalshi_live").filter(
                            {"instance_id": config.instance_id}).pluck("id").run(conn):
                        if row["id"] not in keep:
                            kdb._r.db(kdb.DB_NAME).table("kalshi_live").get(row["id"]).delete().run(conn)
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
            any_live = bool(live_matches)
        except Exception as e:
            log(f"tick {tick} error: {type(e).__name__}: {e}", "red")
            any_live = False

        # Adaptive cadence: poll faster while matches are live.
        time.sleep(config.live_poll_seconds if (config.live_monitoring and any_live)
                   else config.poll_seconds)

    try:
        conn.close()
    except Exception:
        pass
    log("Kalshi engine stopped.", "yellow")


def _iso_now() -> str:  # pragma: no cover - thin clock wrapper
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _fmt_adj(adj: dict) -> str:  # pragma: no cover - logging helper
    """Compact analyst-adjustment string for the live log, e.g. 'winner +0.030'."""
    if not adj:
        return "no adjustment"
    return ", ".join(f"{k} {v:+.3f}" for k, v in adj.items())


def price_cents(raw: dict, *keys) -> int:
    """Read a Kalshi price field as integer cents, robust to scale. Kalshi prices
    are 1..99 (cents) — but a feed that returns dollars (0.40) or a different key
    would otherwise read as 0 and block all trading. >1 -> already cents; (0,1] ->
    dollars*100; <=0 / unparseable -> try the next key, else 0."""
    for k in keys:
        v = (raw or {}).get(k)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f <= 0:
            continue
        return int(round(f * 100)) if f <= 1.0 else int(round(f))
    return 0


ANALYST_ADJ_CAP = 0.05  # the analyst's bounded ±adjustment (matches analyst_panel)


def select_analyst_targets(metas, *, edge_threshold: float, max_calls: int,
                           llm_cap: float = ANALYST_ADJ_CAP) -> set:
    """Choose which pregame matches get the costly LLM analyst this tick (latency +
    cost control). A match is worth analyzing only if its best model edge is within
    the analyst's ±cap of the edge bar — i.e. the LLM could plausibly tip it over.
    Highest-edge first, capped at max_calls. metas: iterable of {"id", "best_edge"}.
    Returns the set of ids to analyze."""
    contestable = [m for m in metas if m.get("best_edge", -1.0) >= edge_threshold - llm_cap]
    contestable.sort(key=lambda m: m.get("best_edge", -1.0), reverse=True)
    return {m["id"] for m in contestable[:max(0, int(max_calls))]}


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
