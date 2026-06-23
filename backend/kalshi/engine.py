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
) -> list[dict]:
    """One scan tick: for each fixture with a known fair value, fetch its
    markets, plan orders, and execute them (gated by should_execute). Takes the
    client + precomputed fair values so it's integration-testable with a fake
    client and no DB. Returns per-fixture results."""
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

    tick = 0
    while True:
        inst = kdb._r.db(kdb.DB_NAME).table("Instances").get(config.instance_id).run(conn) or {}
        if not inst.get("runCommand", False):
            log("runCommand=False — stopping engine.", "yellow")
            break

        tick += 1
        try:
            fixtures, fair_by_fixture = [], {}
            for row in kdb._r.db(kdb.DB_NAME).table("kalshi_odds_snapshots").filter(
                {"brokerage_id": config.brokerage_id}
            ).run(conn):
                fid = row.get("fixture_id")
                q = parse_three_way(row, book="pinnacle")
                if q is None or not fid:
                    continue
                fair_by_fixture[fid] = fair_from_odds(q, method="power")
                fixtures.append(Fixture(
                    fixture_id=fid, sport="soccer", league=row.get("league", ""),
                    home=row.get("home", ""), away=row.get("away", ""),
                    kickoff_utc=row.get("kickoff_utc", ""),
                ))

            if not fixtures:
                log(f"tick {tick}: no odds snapshots to scan yet (waiting on ingest).", "white")
            else:
                results = run_once(
                    client=client, fixtures=fixtures, fair_by_fixture=fair_by_fixture,
                    caps=config.caps, fee_rate=config.fee_rate, environment=config.environment,
                    live_enabled=config.live_enabled, cid_prefix=config.instance_id,
                )
                flagged = sum(len(r0.get("results", [])) for r0 in results)
                placed = sum(1 for r0 in results for x in r0.get("results", []) if x.get("submitted"))
                blocked = [r0["fixture_id"] for r0 in results if r0.get("blocked")]
                dry = " (dry-run, live gate off)" if flagged and not placed else ""
                log(f"tick {tick}: scanned {len(fixtures)} fixtures → {flagged} edge order(s), {placed} placed{dry}."
                    + (f" Risk-blocked: {', '.join(blocked)}" if blocked else ""), "cyan")
                bal = client.get_balance()
                kdb.save_portfolio_snapshot(
                    conn, brokerage_id=config.brokerage_id, ts=_iso_now(),
                    value_cents=bal.portfolio_value_cents, cash_cents=bal.cash_cents,
                )
                log(f"tick {tick}: portfolio ${bal.portfolio_value_cents / 100:.2f} "
                    f"(cash ${bal.cash_cents / 100:.2f})", "white")
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
