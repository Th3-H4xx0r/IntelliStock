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

    _run_with_crash_alert(
        EngineConfig(
            instance_id=instance_id,
            brokerage_id=brokerage_id,
            environment=environment,
            live_enabled=bool(cfg.get("live_enabled", False)),
            caps=risk_caps_from_config(cfg),
            poll_seconds=int(cfg.get("poll_seconds", 60)),
            tier=str(cfg.get("tier", "medium")),
            reserve_frac=float(cfg.get("reserve_frac", 0.3)),
            live_monitoring=bool(cfg.get("live_monitoring", True)),
            live_poll_seconds=int(cfg.get("live_poll_seconds", 30)),
            inplay_caps=inplay_caps_from_config(cfg),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
