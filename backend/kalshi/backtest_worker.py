"""In-process background worker for Kalshi backtests.

A queued `KalshiBacktests` row (status 'pending') is picked up — on boot (drain)
and via a RethinkDB changefeed — and run to completion by `run_job`, which
writes throttled progress back to the row and the result into
`KalshiBacktestResults`. Unlike the heavy Docker-per-backtest stock engine, the
Kalshi pre-match replay is cheap, so it runs in a small thread pool in-process.

`run_job` is pure orchestration over injected collaborators (store / provider /
model_fn / run function) so it unit-tests without a DB, network, or the real
replay. Production wiring lives in `_build_job_context` + `start_worker`.
"""
from __future__ import annotations

import datetime
import logging
import threading

from kalshi import db as _db
from kalshi.backtest import config_from_body, run_backtest as _run_backtest

log = logging.getLogger("kalshi.backtest_worker")


def _iso_now() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


class _Stopped(Exception):
    """Raised internally when the job's run flag is flipped off mid-replay."""


def run_job(job, *, conn, provider, model_fn, store=_db, stop_check=None,
            run_fn=_run_backtest, analyst_fn=None) -> str:
    """Run one backtest job to completion, writing status/progress/result via
    `store`. Returns the terminal status ('finished' | 'stopped' | 'error').

    Injectable collaborators keep this testable: `store` is the DB writer module
    (`kalshi.db` by default), `provider`/`model_fn` feed `run_fn` (the replay,
    `run_backtest` by default), and `stop_check() -> bool` reports whether the
    job's run flag has been cleared (default: read it from `store`).
    """
    jid = job["id"]
    if stop_check is None:
        def stop_check():
            row = store.get_backtest(conn, jid) or {}
            return row.get("run") is False

    try:
        store.update_backtest_progress(conn, jid, status="running",
                                       started_at=_iso_now(), progress=0.0)
        cfg = config_from_body(job.get("config") or {})

        last = [0.0]

        def progress_cb(frac):
            if stop_check():
                raise _Stopped()
            # throttle DB writes to every 2% (and always the final 100%).
            if frac - last[0] >= 0.02 or frac >= 1.0:
                last[0] = frac
                store.update_backtest_progress(conn, jid, progress=round(frac * 100, 2))

        result = run_fn(cfg, provider, model_fn, progress_cb=progress_cb, analyst_fn=analyst_fn)

        store.save_backtest_result(conn, jid, result)
        summary = dict(getattr(result, "summary", {}) or {})
        summary.update({
            "pnl_cents": getattr(result, "pnl_cents", 0),
            "roi": getattr(result, "roi", 0.0),
            "n_bets": getattr(result, "n_bets", 0),
            "win_rate": getattr(result, "win_rate", 0.0),
            "clv_avg": getattr(result, "clv_avg", 0.0),
        })
        store.update_backtest_progress(conn, jid, status="finished", progress=100.0,
                                       finished_at=_iso_now(), summary=summary)
        return "finished"
    except _Stopped:
        log.info("backtest %s stopped by run flag", jid)
        store.update_backtest_progress(conn, jid, status="stopped",
                                       finished_at=_iso_now())
        return "stopped"
    except Exception as e:  # pragma: no cover - defensive; unit-tested via run_fn raising
        log.exception("backtest %s failed", jid)
        # Save the error as a viewable log so the results screen shows WHAT failed
        # instead of an empty "No logs recorded."
        try:
            store.save_backtest_result(conn, jid, {"logs": [f"ERROR: {e}"], "summary": {}})
        except Exception:
            pass
        store.update_backtest_progress(conn, jid, status="error", error=str(e),
                                       finished_at=_iso_now())
        return "error"


# --- production wiring (integration; not unit-tested) --------------------

def _used_this_month(conn) -> int:  # pragma: no cover - integration
    try:
        window = _db.scan_budget_window(_iso_now())
        row = _db._r.db(_db.DB_NAME).table("kalshi_scan_budget").get(window).run(conn)
        return int((row or {}).get("used", 0))
    except Exception:
        return 0


def _build_job_context(job, conn):  # pragma: no cover - integration
    """Build the (provider, model_fn) a job needs. Kalshi candlestick/settled
    endpoints are PUBLIC, so a credential-less client suffices; OddsPapi (sharp
    line) is optional — absent key -> model-only replay."""
    from kalshi.client import KalshiClient
    from kalshi.backtest_data import BacktestDataProvider
    from kalshi.backtest import build_model_fn

    cfg = dict(job.get("config") or {})
    kalshi_client = KalshiClient(key_id="backtest", private_key_pem="", environment="prod")

    oddspapi_client = None
    key = cfg.get("oddspapi_api_key") or ""
    if key:
        from kalshi.ingest_odds import OddsPapiClient
        oddspapi_client = OddsPapiClient(api_key=key)

    provider = BacktestDataProvider(
        kalshi_client=kalshi_client,
        oddspapi_client=oddspapi_client,
        conn=conn,
        budget_used_getter=lambda: _used_this_month(conn),
        budget_bumper=lambda: _db.bump_scan_budget(conn, _iso_now()),
    )

    # NO LOOK-AHEAD: the model's team strength must not reflect results from
    # inside the backtest window. So (1) national Elo uses the FROZEN static
    # table (pass {} -> national_elo_from falls back to the fixed ratings, which
    # never update per match) rather than the LIVE eloratings.net feed that would
    # already encode a June-30 result when replaying a June-30 game; and (2) club
    # Elo is fetched AS OF the backtest start date (ClubElo serves any past date),
    # predating every fixture in the range.
    nat_elo: dict = {}
    elo: dict = {}
    try:
        from kalshi.data.sources.clubelo import fetch_elo_table
        elo = fetch_elo_table(cfg.get("start_date")) or {}
    except Exception:
        elo = {}
    model_fn = build_model_fn(nat_elo, elo)
    analyst_fn = _build_analyst_fn(cfg, conn)
    return provider, model_fn, analyst_fn


def _build_analyst_fn(cfg, conn):  # pragma: no cover - integration
    """Build the LLM analyst closure for a backtest, or None. Uses the configured
    Models-table model; news is intentionally empty (a past fixture has no
    time-faithful news feed), so the analyst adjusts on the feature bundle only.
    Any failure degrades to None -> the replay runs on the statistical model."""
    if not cfg.get("use_llm") or not cfg.get("model"):
        return None
    try:
        model_doc = _db._r.db(_db.DB_NAME).table("Models").get(cfg["model"]).run(conn)
    except Exception:
        model_doc = None
    if not model_doc:
        return None
    try:
        from kalshi.intelligence.analyst_panel import analyze, make_llm_call
    except Exception:
        return None
    llm_call = make_llm_call(model_doc)
    if llm_call is None:
        return None

    def analyst_fn(fx, model_probs, sharp_probs):
        markets = list((model_probs or {}).keys()) or ["home", "draw", "away"]
        features = {
            "home": fx.get("home"), "away": fx.get("away"),
            "league": fx.get("league"), "model_probs": model_probs,
            "home_elo": fx.get("home_elo"), "away_elo": fx.get("away_elo"),
            "home_xg": fx.get("home_xg"), "away_xg": fx.get("away_xg"),
        }
        try:
            return analyze(features, markets, news="", llm_call=llm_call).get("adjustments", {})
        except Exception:
            return {}

    return analyst_fn


def _run_job_production(job, conn):  # pragma: no cover - integration
    provider, model_fn, analyst_fn = _build_job_context(job, conn)
    return run_job(job, conn=conn, provider=provider, model_fn=model_fn, analyst_fn=analyst_fn)


def start_worker(conn_factory, *, max_workers: int = 2):  # pragma: no cover - integration
    """Start the background worker: drain existing pending rows, then watch the
    changefeed for new ones, running each in a small thread pool. `conn_factory`
    returns a fresh RethinkDB connection (one per job thread — connections are
    not thread-safe)."""
    import concurrent.futures

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    seen: set = set()

    def _dispatch(job):
        jid = job.get("id")
        if not jid or jid in seen:
            return
        seen.add(jid)

        def _task():
            c = conn_factory()
            try:
                _run_job_production(job, c)
            finally:
                seen.discard(jid)
                try:
                    c.close()
                except Exception:
                    pass

        pool.submit(_task)

    def _loop():
        c = conn_factory()
        # Drain rows left pending/running from a prior process.
        try:
            for row in _db.pending_or_running_backtests(c):
                if row.get("status") == "running":
                    # orphaned mid-run -> re-queue as pending
                    _db.update_backtest_progress(c, row["id"], status="pending")
                    row = {**row, "status": "pending"}
                _dispatch(row)
        except Exception:
            log.exception("backtest worker drain failed")
        # Watch for new pending jobs.
        try:
            feed = (_db._r.db(_db.DB_NAME).table("KalshiBacktests")
                    .filter({"status": "pending"}).changes(include_initial=False).run(c))
            for change in feed:
                new = (change or {}).get("new_val")
                if new and new.get("status") == "pending":
                    _dispatch(new)
        except Exception:
            log.exception("backtest worker changefeed ended")

    threading.Thread(target=_loop, name="kalshi-backtest-worker", daemon=True).start()
    log.info("kalshi backtest worker started")
    return pool
