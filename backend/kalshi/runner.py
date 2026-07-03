"""Entry point for a Kalshi instance process: `python -m kalshi.runner <instance_id>`.

instance.py launches this (instead of broker.py) for kind='kalshi' Instances rows.
It loads the instance's kalshi_config + linked brokerage, builds the EngineConfig,
and hands off to the lean engine loop (which polls runCommand + the kill switch).
"""
from __future__ import annotations

import sys

from kalshi import db as kdb
from kalshi.engine import EngineConfig, run_instance
from kalshi.instance_config import inplay_caps_from_config, risk_caps_from_config


def _configure_telemetry() -> None:  # pragma: no cover - integration
    """Enable LLM token-usage telemetry in THIS process. record_llm_call is gated on
    llm_telemetry.configure() having run; the API + equities broker call it at their
    startup, but the Kalshi runner is a separate process, so without this the analyst's
    tokens are silently dropped (never reach the Token Usage page)."""
    try:
        import os
        import llm_telemetry
        from rethinkdb import RethinkDB
        r = RethinkDB()
        pricing_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "llm_pricing.yaml")
        llm_telemetry.configure(
            db_conn_factory=kdb.get_conn, enabled=True, flush_interval_s=2.0,
            max_buffer=50, pricing_yaml_path=pricing_path if os.path.exists(pricing_path) else None,
            r_module=r, db_name=kdb.DB_NAME,
        )
        try:
            from llm_telemetry import ensure_llm_usage_tables
            c = kdb.get_conn()
            try:
                ensure_llm_usage_tables(conn=c, r=r, db_name=kdb.DB_NAME)
            finally:
                try:
                    c.close()
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass


def _start_training_worker(instance_id, cfg) -> None:  # pragma: no cover - integration
    """Start the self-improving calibration worker as a daemon alongside the engine.
    Best-effort: any failure here must NOT block trading (the engine simply runs
    with the last champion, or identity)."""
    try:
        import datetime as _dt
        from kalshi import training_worker
        from kalshi.backtest_data import BacktestDataProvider
        from kalshi.backtest import build_model_fn
        from kalshi.client import KalshiClient
        leagues = list(cfg.get("leagues") or [])
        if not leagues:
            return
        # Static national Elo + empty club table: national-team (WC) fixtures are priced
        # off the built-in national table, so we skip the synchronous ClubElo fetch that
        # would otherwise delay engine start (the engine fetches club Elo itself). The
        # calibrator maps prob->prob and is robust to small train/live model diffs.
        model_fn = build_model_fn({}, {})

        def provider_factory(conn):
            return BacktestDataProvider(
                conn=conn,
                kalshi_client=KalshiClient(key_id="training", private_key_pem="", environment="prod"))

        # Per-instance champion (engine reads config.instance_id, falling back to the
        # seeded "__default__" bootstrap). start_date is intentionally recent — the model
        # prices with a single point-in-time Elo snapshot, so training only on the
        # current window keeps that a minor approximation, not a deep-history look-ahead.
        from kalshi.model_ensemble import build_feature_fn
        refit = training_worker.build_refit_fn(
            provider_factory=provider_factory, model_fn=model_fn, leagues=leagues,
            start_date="2026-01-01",
            end_date_fn=lambda: _dt.datetime.utcnow().strftime("%Y-%m-%d"),
            instance_id=instance_id, min_total=100,
            feat_fn=build_feature_fn({}, {}))   # SP2: also refit the model champion
        try:
            from notifications import notify as _notify
        except Exception:
            _notify = None
        training_worker.start_worker(kdb.get_conn, refit, refresh_secs=3600, notify=_notify)
    except Exception:
        pass


def _run_with_crash_alert(config, *, run=run_instance, notify=None) -> None:
    """Run the engine; on an uncaught crash, alert the operator (Discord + iOS push,
    category 'kalshi_runtime') then re-raise so the supervisor still sees the failure.
    The alert is best-effort and never masks the original exception."""
    try:
        run(config)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        import traceback
        try:
            _notify = notify
            if _notify is None:
                from notifications import notify as _notify
            tb = traceback.format_exc()[-600:]
            _notify(
                category="kalshi_runtime",
                instance_id=config.instance_id,
                title=f"KALSHI RUNTIME [{config.instance_id}] crashed",
                body=f"{type(e).__name__}: {e}\n\n{tb}",
                discord_channel="notifications",
                push_title="Kalshi runtime crashed",
                push_body=f"{type(e).__name__}: {str(e)[:120]}",
            )
        except Exception:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: python -m kalshi.runner <instance_id>", file=sys.stderr)
        return 2
    instance_id = argv[0]

    conn = kdb.get_conn()
    try:
        inst = kdb._r.db(kdb.DB_NAME).table("Instances").get(instance_id).run(conn) or {}
        if inst.get("kind") != "kalshi":
            print(f"instance {instance_id} is not a kalshi instance", file=sys.stderr)
            return 1
        cfg = inst.get("kalshi_config") or {}
        brokerage_id = inst.get("brokerage_id")
        brokerage = kdb._r.db(kdb.DB_NAME).table("BrokerageAccounts").get(brokerage_id).run(conn) or {}
        environment = brokerage.get("kalshi_environment") or "demo"
    finally:
        try:
            conn.close()
        except Exception:
            pass

    _configure_telemetry()
    _start_training_worker(instance_id, cfg)
    _run_with_crash_alert(
        EngineConfig(
            instance_id=instance_id,
            brokerage_id=brokerage_id,
            environment=environment,
            live_enabled=bool(cfg.get("live_enabled", False)),
            paper_mode=bool(cfg.get("paper_mode", False)),
            caps=risk_caps_from_config(cfg),
            poll_seconds=int(cfg.get("poll_seconds", 60)),
            tier=str(cfg.get("tier", "medium")),
            reserve_frac=float(cfg.get("reserve_frac", 0.3)),
            live_monitoring=bool(cfg.get("live_monitoring", True)),
            live_poll_seconds=int(cfg.get("live_poll_seconds", 30)),
            inplay_caps=inplay_caps_from_config(cfg),
            analyst_max_calls=int(cfg.get("analyst_max_calls", 10)),
            odds_api_key=(str(cfg.get("odds_api_key") or "").strip()
                          or __import__("os").environ.get("ODDS_API_KEY", "").strip()),
            oddspapi_api_key=(str(cfg.get("oddspapi_api_key") or "").strip()
                              or __import__("os").environ.get("ODDSPAPI_API_KEY", "").strip()),
            sharp_weight=float(cfg.get("sharp_weight", 0.7)),
            devig_method=str(cfg.get("devig_method", "power")),
            market_shrink=float(cfg.get("market_shrink", 0.4)),
            odds_refresh_secs=int(cfg.get("odds_refresh_secs", 3600)),
            odds_regions=str(cfg.get("odds_regions", "eu,uk,us")),
            soccer_series=list(cfg.get("soccer_series") or []),
            scoreboard_leagues=list(cfg.get("scoreboard_leagues") or []),
            national_elo_live=bool(cfg.get("national_elo_live", True)),
            national_elo_url=(str(cfg.get("national_elo_url") or "").strip()
                              or __import__("os").environ.get("NATIONAL_ELO_URL", "").strip()
                              or "https://www.eloratings.net"),
            national_elo_refresh_ticks=int(cfg.get("national_elo_refresh_ticks", 60)),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
