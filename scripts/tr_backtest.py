#!/usr/bin/env python3
"""Run ONE engine backtest of the TR lab and print its scorecard.

    python3 scripts/tr_backtest.py                    # the ledger's own window
    python3 scripts/tr_backtest.py --start 2025-02-24 --end 2026-11-01
    python3 scripts/tr_backtest.py --cost-tiers etf-liquid
    python3 scripts/tr_backtest.py --scorecard <backtest id>     # print only

The window defaults to the lab's ledger: from one week before the chain's
first ``ok`` week (a warm-up week) through the end of its last ``ok`` week.
The chain, model and prompt version are read from the lab instance's linked
document; the ledger rows through a read-only Postgres session (as
tr_journal.py does). Before the first ledger week the lane holds no book --
the account sits in uninvested cash -- so a ``--start`` more than two weeks
before the first ``ok`` week is REFUSED unless ``--allow-cash-lead`` is given:
it dilutes every figure with cash and fetches years of bars for nothing.
Build the ledger BEFORE the backtest: the broker fetches the ledger's symbols
once, when the backtest starts.

Posts ``{instance_id: "strategy-tr-lab", stocks: <the lane's fixed ETF set>,
start_date, end_date, granularity: "900", initial_cash: 5500}`` (granularity
is SECONDS: "60" would step every minute), then polls until the run reaches a
terminal status and prints ``tr_scorecard`` from
``GET /backtests/{id}/summary``.

One at a time (operator rule): the script refuses to post while any backtest
-- strategy-tr-lab's or another instance's -- is pending, queued, running or
paused (``paused_llm_critical`` included: the lab's own rows are read with the
instance filter, because the global listing pins only plain ``paused`` rows to
page 1). A cost flag is sent
only when given. A status field can lie about a dead container, so the poll
gives up after ``--max-wait-hours`` and says so; the run is NOT stopped then
(stop it with ``POST /backtests/{id}/stop``; a stuck container needs its
``BacktestInstances`` row set to ``run=false``).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from trend_research import book  # noqa: E402

INSTANCE_ID = "strategy-tr-lab"
STOCKS = sorted(book.ALL_ALLOWED_ETFS)
GRANULARITY = "900"
INITIAL_CASH = 5500
TERMINAL = frozenset({"finished", "completed", "failed", "error", "stopped", "cancelled"})
#: Uninvested cash allowed before the first ledger week without the flag.
MAX_CASH_LEAD_DAYS = 14
WARMUP_DAYS = 7
_SEGMENTS = ("honest", "optimistic", "overall")


def _rows(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("backtests"), list):
        return payload["backtests"]
    return []


def _is_active(status) -> bool:
    s = str(status or "").strip().lower()
    return s in ("running", "queued", "pending") or s.startswith("paused")


def active_runs(call) -> list:
    """Backtests not yet finished, on ANY instance. Page 1 of the global
    listing pins the running/queued/pending/paused rows first; a
    ``paused_llm_critical`` row is not pinned, so the lab's own rows are also
    read with the instance filter (its history is short)."""
    seen, out = set(), []
    for path in ("/backtests?per_page=100",
                 f"/backtests?instance_id={INSTANCE_ID}&per_page=100"):
        _, payload = call("GET", path)
        for r in _rows(payload):
            key = str(r.get("id"))
            if key not in seen and _is_active(r.get("status")):
                seen.add(key)
                out.append(r)
    return out


def assert_idle(call) -> None:
    """Refuse while a backtest is in flight: the lab's own (the brief's rule)
    or another instance's (the operator's: never two engine runs at once)."""
    busy = active_runs(call)
    if busy:
        listing = ", ".join(f"{r.get('id')} on {r.get('instance')} ({r.get('status')})"
                            for r in busy)
        mine = any(str(r.get("instance")) == INSTANCE_ID for r in busy)
        raise SystemExit(
            f"REFUSING: {INSTANCE_ID if mine else 'the engine'} already has a backtest in "
            f"flight: {listing}. Backtests run one at a time; wait for it to finish or "
            "stop it first.")


def lab_selector(call) -> dict:
    """``{tr_chain_id, tr_model, tr_prompt_version}`` of the lab instance's
    linked document's enabled strategy_trend_research lane."""
    _, inst = call("GET", f"/instances/{INSTANCE_ID}")
    doc_id = (inst or {}).get("strategy_id")
    if doc_id in (None, ""):
        raise SystemExit(f"REFUSING: {INSTANCE_ID} has no linked document; run "
                         "scripts/tr_lab_setup.py first")
    _, doc = call("GET", f"/strategies/{doc_id}")
    for spec in (doc or {}).get("strategies") or []:
        if str((spec or {}).get("strategy") or "").strip().lower() != "strategy_trend_research":
            continue
        cfg = spec.get("config") or {}
        chain = str(cfg.get("tr_chain_id") or "").strip()
        model = str(cfg.get("tr_model") or "").strip()
        if chain and model:
            return {"tr_chain_id": chain, "tr_model": model,
                    "tr_prompt_version": str(cfg.get("tr_prompt_version") or "tr-v1").strip()}
    raise SystemExit(f"REFUSING: doc {doc_id} has no strategy_trend_research lane with "
                     "tr_chain_id and tr_model set; run scripts/tr_lab_setup.py first")


def ok_weeks(selector: dict, store) -> list:
    """The Mondays of the chain's ``ok`` ledger rows, oldest first."""
    import tr_journal
    rows = tr_journal.chain_rows(selector["tr_chain_id"], model=selector["tr_model"],
                                 prompt_version=selector["tr_prompt_version"], store=store)
    out = set()
    for row in rows:
        if row.get("status") == "ok":
            try:
                out.add(date.fromisoformat(str(row.get("week_start"))[:10]))
            except ValueError:
                continue
    return sorted(out)


def resolve_window(args, weeks) -> tuple:
    """(start, end) ISO dates: the flags, defaulted to the ledger's window;
    refuses a long cash lead-in unless ``--allow-cash-lead``."""
    if not weeks:
        if args.allow_cash_lead and args.start and args.end:
            return args.start, args.end
        raise SystemExit("REFUSING: the chain has no ok ledger weeks yet -- build the "
                         "ledger first (runbook step 2); a backtest before it is all cash")
    first, last = weeks[0], weeks[-1]
    start = args.start or (first - timedelta(days=WARMUP_DAYS)).isoformat()
    end = args.end or (last + timedelta(days=6)).isoformat()
    lead = (first - date.fromisoformat(start)).days
    if lead > MAX_CASH_LEAD_DAYS and not args.allow_cash_lead:
        raise SystemExit(
            f"REFUSING: --start {start} is {lead} days before the chain's first ok week "
            f"({first}); the lane holds no book before it, so that stretch is uninvested "
            "cash that dilutes the scorecard. Start on or after "
            f"{(first - timedelta(days=MAX_CASH_LEAD_DAYS)).isoformat()}, or pass "
            "--allow-cash-lead.")
    if date.fromisoformat(end) > last + timedelta(days=7):
        print(f"note: --end {end} runs past the last ok ledger week ({last}); the lane "
              "holds its last book after it", flush=True)
    if date.fromisoformat(end) < date.fromisoformat(start):
        raise SystemExit(f"REFUSING: end {end} is before start {start}")
    return start, end


def post_body(start: str, end: str, *, cost_tiers=None, cost_bps=None) -> dict:
    body = {"instance_id": INSTANCE_ID, "stocks": list(STOCKS), "start_date": start,
            "end_date": end, "granularity": GRANULARITY, "initial_cash": INITIAL_CASH}
    if cost_tiers is not None:
        body["equity_cost_tiers"] = cost_tiers
    if cost_bps is not None:
        body["equity_total_cost_bps"] = cost_bps
    return body


def wait_for(call, backtest_id, *, max_wait_sec: float, poll_sec: float,
             sleep=time.sleep, clock=time.monotonic) -> str | None:
    """The terminal status, or None when `max_wait_sec` ran out first."""
    deadline = clock() + max_wait_sec
    last = None
    while True:
        try:
            _, st = call("GET", f"/backtests/{backtest_id}/status")
        except (SystemExit, Exception) as error:   # HTTP error after _api's retries, or network
            st = {"status": None}
            print(f"poll failed: {str(error).splitlines()[0]}", flush=True)
        status = str((st or {}).get("status") or "").strip().lower()
        if status in TERMINAL:
            return status
        line = (status, (st or {}).get("progress"))
        if line != last:
            print(f"backtest {backtest_id}: {status or '?'} progress={line[1]} "
                  f"last_active={(st or {}).get('_last_active')}", flush=True)
            last = line
        if clock() >= deadline:
            return None
        sleep(poll_sec)


def _fmt(value) -> str:
    return "-" if value is None else (f"{value:.2f}" if isinstance(value, float) else str(value))


def format_scorecard(card) -> str:
    if not isinstance(card, dict):
        return ("no tr_scorecard on this backtest (the summary endpoint may predate "
                "the TR deploy, or the lane was not enabled)")
    if card.get("error"):
        return f"tr_scorecard error: {card['error']}"
    lines = [f"{'segment':<11} {'start':<10} {'end':<10} {'ret%':>8} {'spy%':>8} "
             f"{'cagr%':>7} {'spy':>7} {'xs pp':>7} {'dd%':>7} {'rnd-c':>6} {'rnd-u':>6} pass"]
    for seg in _SEGMENTS:
        s = card.get(seg) or {}
        if s.get("untested"):
            lines.append(f"{seg:<11} untested")
            continue
        lines.append(
            f"{seg:<11} {_fmt(s.get('start')):<10} {_fmt(s.get('end')):<10} "
            f"{_fmt(s.get('ret')):>8} {_fmt(s.get('spy_ret')):>8} {_fmt(s.get('cagr')):>7} "
            f"{_fmt(s.get('spy_cagr')):>7} {_fmt(s.get('excess_pp')):>7} "
            f"{_fmt(s.get('max_dd')):>7} {_fmt(s.get('random_pctile_candidates')):>6} "
            f"{_fmt(s.get('random_pctile_universe')):>6} {s.get('pass')} "
            f"(regimes={s.get('pass_all_regimes')}, +5pp={s.get('pass_plus5')})")
    honest = card.get("honest") or {}
    for w in honest.get("windows") or []:
        lines.append(f"  honest {w.get('kind'):<5} {w.get('tag')}: {_fmt(w.get('ret'))}% vs "
                     f"SPY {_fmt(w.get('spy_ret'))}% beat={w.get('beat')}")
    meta = card.get("meta") or {}
    for note in meta.get("notes") or []:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def print_scorecard(call, backtest_id) -> dict | None:
    _, summary = call("GET", f"/backtests/{backtest_id}/summary")
    card = (summary or {}).get("tr_scorecard")
    print(format_scorecard(card))
    if isinstance(card, dict):
        print(json.dumps(card, indent=2, default=str))
    return card


def _iso_day(text: str) -> str:
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {text!r}")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=_iso_day,
                    help="first day YYYY-MM-DD (default: a week before the first ok "
                         "ledger week)")
    ap.add_argument("--end", type=_iso_day,
                    help="last day YYYY-MM-DD (default: the end of the last ok ledger week)")
    ap.add_argument("--allow-cash-lead", action="store_true",
                    help="allow a --start more than two weeks before the ledger")
    ap.add_argument("--cost-tiers", choices=("etf-liquid",),
                    help="send equity_cost_tiers (default: the engine's flat cost)")
    ap.add_argument("--cost-bps", type=float, choices=(25.0, 50.0),
                    help="send equity_total_cost_bps (default: absent)")
    ap.add_argument("--max-wait-hours", type=float, default=24.0,
                    help="give up polling after this long (default 24)")
    ap.add_argument("--poll-seconds", type=float, default=60.0)
    ap.add_argument("--scorecard", metavar="BACKTEST_ID",
                    help="only print the scorecard of an existing backtest")
    args = ap.parse_args(argv)
    if args.scorecard is None and args.start and args.end and args.end < args.start:
        ap.error("--end is before --start")
    return args


def main(argv=None, *, call=None, sleep=time.sleep, clock=time.monotonic,
         store=None) -> int:
    args = parse_args(argv)
    if call is None:
        from _api import call as call  # noqa: PLW0127
    if args.scorecard is not None:
        print_scorecard(call, args.scorecard)
        return 0

    assert_idle(call)
    if args.allow_cash_lead and args.start and args.end:
        weeks = []
    else:
        if store is None:
            import tr_journal
            store = tr_journal.read_only_store()
        weeks = ok_weeks(lab_selector(call), store)
    start, end = resolve_window(args, weeks)
    if weeks:
        print(f"ledger: {len(weeks)} ok week(s), {weeks[0]}..{weeks[-1]}", flush=True)
    body = post_body(start, end, cost_tiers=args.cost_tiers, cost_bps=args.cost_bps)
    _, created = call("POST", "/backtests", body)
    backtest_id = (created or {}).get("id")
    if backtest_id is None:
        raise SystemExit(f"POST /backtests returned no id: {created!r}")
    print(f"posted backtest {backtest_id}: {start}..{end} granularity "
          f"{GRANULARITY}s cash {INITIAL_CASH}", flush=True)

    status = wait_for(call, backtest_id, max_wait_sec=args.max_wait_hours * 3600,
                      poll_sec=args.poll_seconds, sleep=sleep, clock=clock)
    if status is None:
        print(f"TIMEOUT: backtest {backtest_id} not terminal after {args.max_wait_hours} h. "
              "Its status may be stale (a dead container keeps its last status). It was "
              "NOT stopped; check its logs, stop it if it is stuck, and rerun "
              f"`tr_backtest.py --scorecard {backtest_id}` once it finishes.", flush=True)
        return 2
    print(f"backtest {backtest_id}: {status}", flush=True)
    print_scorecard(call, backtest_id)
    return 0 if status in ("finished", "completed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
