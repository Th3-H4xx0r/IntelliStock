#!/usr/bin/env python3
"""Pull ONE BacktestResults document per lifecycle stage, read-only, and write
gzipped test fixtures with secrets stripped.

Developer tool. Nothing imports it, and it is the only file in the Postgres
port allowed to touch the rethinkdb driver -- lazily, inside the fetch
function. Run it once; the fixtures it writes are committed.

    python3 scripts/dev_fetch_backtest_fixture.py
    python3 scripts/dev_fetch_backtest_fixture.py --synthetic   # no live DB

Live host comes from .env RETHINKDB_HOST. The connection is read-only: no
insert, update, delete, or index_create is issued.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_DIR = os.path.join(REPO, "backend", "tests", "fixtures")
DB_NAME = "IntelliStock"

# Stage -> (status, a known live id to read directly). There is no "completed"
# status live. Reading by id keeps this off a full-table scan of multi-MB
# documents; if the id has aged out, the status filter is the fallback.
STAGES = {
    "running": ("running", 138148),
    "finished": ("finished", 102463),
    "stopped": ("stopped", 108477),
    "error": ("error", 101666),
}

# Hard denylist applied after the driver read, at every depth. Substring
# matching, not equality: `overlay_llm_max_output_tokens` carries "token" and
# a fixture that keeps it would trip the secret-free test for no reason, so
# any key CONTAINING a marker is dropped and any string value containing one
# is masked. Nothing in the base key set matches, so the shape survives.
_SECRET_MARKERS = ("api_key", "apikey", "secret", "password", "token",
                   "pk_live", "sk_live", "bearer ", "credential", "private_key",
                   "authorization", "passwd")

_TRUNCATE = {"backtest_decisions": 200, "backtest_prices": 200,
             "portfolio_value_history": 200, "logs": 100,
             "backtest_trades": 50, "backtest_refusals": 50}

_MASK_RE = re.compile("|".join(re.escape(m) for m in _SECRET_MARKERS),
                      re.IGNORECASE)


def _has_marker(text: str) -> bool:
    return _MASK_RE.search(text) is not None


def _scrub(value):
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if not _has_marker(str(k))}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str) and _has_marker(value):
        return _MASK_RE.sub("REDACTED", value)
    return value


def _shrink(doc: dict) -> dict:
    out = _scrub(copy.deepcopy(doc))
    for key, cap in _TRUNCATE.items():
        if isinstance(out.get(key), list):
            out[key] = out[key][:cap]
    out["instance_id"] = "fixture-instance"
    return out


def _jsonable(value):
    """RethinkDB hands back datetimes for TIME pseudotype fields; the fixture
    is JSON, and the port stores those as ISO-8601 strings anyway."""
    import datetime as dt
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value


def fetch_live() -> dict:
    """Returns {stage: doc}. Raises if the live DB is unreachable."""
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO, ".env"))
    from rethinkdb import RethinkDB           # noqa: PLC0415 - lazy on purpose
    r = RethinkDB()
    conn = r.connect(host=os.environ["RETHINKDB_HOST"],
                     port=int(os.environ.get("RETHINKDB_PORT", "28015")),
                     timeout=20)
    try:
        out = {}
        table = r.db(DB_NAME).table("BacktestResults")
        for stage, (status, sample_id) in STAGES.items():
            row = table.get(sample_id).run(conn)
            if not row or row.get("status") != status:
                rows = list(table.filter({"status": status}).limit(1).run(conn))
                if not rows:
                    raise RuntimeError("no live document with status=%r" % status)
                row = rows[0]
            out[stage] = _shrink(_jsonable(row))
        return out
    finally:
        conn.close()


def synthesize() -> dict:
    """Same top-level keys and types as the live documents, invented values."""
    base = {
        "_last_active": "2026-08-22T03:37:00.123456+00:00",
        "backtest_decisions": [{"date": "2026-03-02", "ticker": "AACI",
                                "action": "BUY", "reason": "signal"}
                               for _ in range(40)],
        "backtest_id": 460555,
        "backtest_prices": [],
        "backtest_refusals": [{"date": "2026-03-02", "ticker": "AACI",
                               "reason": "gate"}],
        "backtest_trades": [{"symbol": "AACI", "qty": 3, "price": 12.5,
                             "side": "buy", "ts": "2026-03-02T14:30:00"}],
        "difficulty": 3,
        "end_date": "2026-04-01 00:00:00",
        "granularity_sec": 900,
        "id": 460555,
        "initial_cash": 10000,
        "instance_id": "fixture-instance",
        "logs": ["[14:30:00] tick 1"] * 60,
        "pnl": 123.45,
        "pnl_percent": 1.2345,
        "portfolio_value_history": [{"t": "2026-03-02T14:30:00", "v": 10000.0}
                                    for _ in range(30)],
        "progress": 42.5,
        "start_date": "2026-03-01 00:00:00",
        "status": "running",
        "strategy_id": 179,
        "strategy_schema": {"name": "graph_nexus", "version": 9,
                            "config": {"k%d" % i: i for i in range(200)}},
        "tickers": ["AACI", "AA", "ZZ", "AAPL", "MSFT", "NVDA"],
        "time_elapsed_seconds": 812,
        "timestamp": "2026-08-22T03:37:00.123456",
        "experiment_fingerprint": None,
        "experiment_id": None,
        "experiment_search_scope": None,
    }
    finished = dict(base)
    finished.update({
        "status": "finished", "progress": 100, "backtest_prices":
            [{"date": "2026-03-02", "ticker": "AACI", "close": 12.5}
             for _ in range(50)],
        "time_elapsed_seconds": 812.5,
        "cadence_mode": "daily_bars_intraday_marks",
        "code_version": "f1d9fde-2026-08-21T00:00Z",
        "dividend_summary": {"total": 0.0, "events": 0, "accrued": 0.0,
                             "paid": 0.0, "per_symbol": {}, "currency": "USD"},
        "dual_cadence_backtest_simulation": True,
        "execution_cost_model": {"spread_bps": 5.0, "slippage_bps": 2.0,
                                 "fee_bps": 0.0, "impact_bps": 0.0,
                                 "version": "2026-07-01"},
        "execution_cost_model_version": "2026-07-01T00:00:00Z",
        "execution_promotion_eligible": True,
        "execution_promotion_error": None,
        "execution_provenance_complete": True,
        "fees": None,
        "fill_provenance": [{"symbol": "AACI", "source": "bar_close"}],
        "pnl_per_stock": {"AACI": 12.5},
        "pnl_percent_per_stock": {"AACI": 1.1},
        "rejected_order_count": 0,
        "slippage_cost": 1.25,
        "spread_cost": 2.5,
        "stock_price_change": {"AACI": 0.03},
        "total_fees": 0.0,
        "unfilled_order_count": 0,
    })
    stopped = dict(base)
    stopped.update({"status": "stopped", "progress": 0, "pnl": None,
                    "pnl_percent": None, "backtest_decisions": [],
                    "backtest_refusals": [], "backtest_trades": [],
                    "backtest_prices": [], "portfolio_value_history": [],
                    "tickers": [],
                    "nexus_lookback": {"current": 5, "total": 10,
                                       "current_date": "2026-03-02",
                                       "start_date": "2026-03-01",
                                       "end_date": "2026-04-01"}})
    errored = dict(base)
    errored.update({"status": "error", "progress": 100.0,
                    "error": "strategy raised: no strategy linked to instance"})
    return {"running": base, "finished": finished,
            "stopped": stopped, "error": errored}


def derive_stub(running: dict) -> dict:
    """backend/engines/backtest_engine.py:938-955's literal payload."""
    return {
        "id": running["id"],
        "backtest_id": running["id"],
        "status": "running",
        "progress": 0,
        "timestamp": running["timestamp"],
        "instance_id": None,
        "strategy_id": None,
        "pnl": None,
        "pnl_percent": None,
        "start_date": running["start_date"],
        "end_date": running["end_date"],
        "tickers": list(running["tickers"]),
        "time_elapsed_seconds": None,
        "portfolio_value_history": [],
        "backtest_trades": [],
        "backtest_prices": [],
        "logs": [],
    }


def derive_paused(running: dict) -> dict:
    """backend/backtest_critical_abort.py:160-174's payload."""
    out = dict(running)
    out.update({
        "status": "paused_llm_critical",
        "pause_reason": "llm_critical_failure",
        "pause_call_site": "graph_nexus_analysis.overlay",
        "pause_attempts": 3,
        "pause_bar_time": "2026-03-02T14:30:00+00:00",
        "paused_at": "2026-08-22T03:40:00+00:00",
        "pause_sample": "provider returned 429 rate limit",
    })
    return out


def write(stage: str, doc: dict) -> None:
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    path = os.path.join(FIXTURE_DIR, "backtest_result_%s.json.gz" % stage)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(doc, fh, sort_keys=True, indent=1)
    size = os.path.getsize(path)
    print("wrote %s (%d bytes)" % (path, size))
    if size >= 512 * 1024:
        print("  WARNING: over the 512 KB commit ceiling", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--synthetic", action="store_true",
                    help="skip the live DB and emit synthetic documents")
    args = ap.parse_args(argv)
    if args.synthetic:
        docs = synthesize()
        print("SYNTHETIC fixtures (no live DB read)")
    else:
        try:
            docs = fetch_live()
            print("LIVE fixtures read read-only from RETHINKDB_HOST")
        except Exception as exc:
            print("live fetch failed (%s: %s); falling back to synthetic"
                  % (type(exc).__name__, exc), file=sys.stderr)
            docs = synthesize()
    docs["stub"] = derive_stub(docs["running"])
    docs["paused"] = derive_paused(docs["running"])
    for stage in ("stub", "running", "paused", "stopped", "error", "finished"):
        write(stage, docs[stage])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
