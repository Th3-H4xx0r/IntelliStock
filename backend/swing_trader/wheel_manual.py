"""Operator-triggered wheel work: regrade one pending card, run a scan now.

The weekly scan (strategies/strategy_wheel.py) scores each candidate once, on
Monday, and never again. An operator who approves on Friday is looking at
Monday's price, strike and score. Two actions close that gap:

  regrade   re-screen and re-score ONE pending wheel signal at today's bars,
            and rewrite its score, strike, premium and reasoning in place. The
            card stays pending whatever the new score is; a contract that no
            longer qualifies is flagged, not removed (operator ruling
            2026-11-18).
  scan      run the whole screen + scorer now, for today's session. REVIEW
            ONLY: nothing is ever sent to the broker from here. A score at or
            above the auto threshold becomes a pending card like any other
            (operator ruling 2026-11-18). Symbols that already have an open
            card or today's row are skipped, not re-scored.

Both run inside the instance's broker process, on their own daemon thread, so
the LiveCommands worker (which also carries `halt`) is never held for the
minutes a scan takes. They write nothing to the strategy cache. A manual card
dated on the weekly scan's day would take the weekly scan's own signal id, and
the weekly scan reads an existing row as "already decided" and orders nothing
on it; so a manual run is refused on the weekly scan's day and its fallback day
until that week's scan has completed (bug sweep 2026-11-19, ruling 1).

The resolved lane config (data credentials, the linked conviction model) only
exists inside a strategy tick, so `StrategyWheel.run_once` hands it to
`remember_context` on every live tick and the command handler reads it back.
"""
from __future__ import annotations

import functools
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from db import store  # noqa: F401  (tests monkeypatch this name)
from db.store import P

from swing_trader import (
    account,
    ai_analyst,
    clock,
    market_data,
    notify,
    signals_store,
    universe,
    wheel_rules,
)
from swing_trader.constants import WHEEL_WARMUP_DAYS
from swing_trader.refdata import RUNS_TABLE, SCANS_TABLE

REGRADE_COMMAND = "swing_regrade"
SCAN_COMMAND = "wheel_manual_scan"
COMMAND_TYPES = (REGRADE_COMMAND, SCAN_COMMAND)

#: A regrade or a run that has not finished after this long is treated as
#: dead (the process restarted under it), so the operator can ask again.
REGRADE_STALE_S = 300
RUN_STALE_S = 600
#: Scoring is one news search plus one model call per name (20-60 s). Three at
#: a time keeps a 40-name run near five minutes without stampeding a provider.
SCAN_WORKERS = 3
#: Regrades get their own two scoring slots, so a burst of regrades stays
#: bounded and a regrade never queues behind a manual run's scoring (the scan
#: is bounded by its own pool of SCAN_WORKERS and never takes these). At most
#: SCAN_WORKERS + REGRADE_WORKERS AI calls run at once per instance.
REGRADE_WORKERS = 2
_REGRADE_SLOTS = threading.BoundedSemaphore(REGRADE_WORKERS)
#: Signals an operator can still act on, or that already became an order.
_OPEN_CARD_STATUSES = ("pending", "approved", "approved_half", "submitted",
                       "auto_approved")
_SCORING_FAILED = "AI scoring failed"

_CONTEXT: dict = {}
_CONTEXT_LOCK = threading.Lock()
#: "regrade:<signal id>" / "scan:<instance id>" -> the token of the thread that
#: holds it (the run id for a scan), so a finished thread frees only its own key.
_ACTIVE: dict = {}
_ACTIVE_LOCK = threading.Lock()
_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
                  "Sunday")


def _log(msg, color="white"):
    try:
        from intellistock_logger import intellistock_logger as _ilog  # type: ignore
        _ilog.log(str(msg), color, service="WheelManual")
    except Exception:  # pragma: no cover - standalone/test import
        print(f"[WheelManual] {msg}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def is_stale(stamp, max_age_s, now=None) -> bool:
    """True when `stamp` is unreadable or older than `max_age_s`."""
    at = _parse_iso(stamp)
    if at is None:
        return True
    now = now or datetime.now(timezone.utc)
    return (now - at) > timedelta(seconds=max_age_s)


# -- the tick's resolved config ------------------------------------------------

def remember_context(instance_id, cfg, adapter, weekly_marker=None) -> None:
    """Called by StrategyWheel.run_once on every live tick. `weekly_marker` is
    a zero-argument callable that reads the weekly scan's completion marker
    from the live strategy cache at the moment it is called."""
    with _CONTEXT_LOCK:
        _CONTEXT[str(instance_id)] = {"cfg": dict(cfg or {}), "adapter": adapter,
                                      "weekly_marker": weekly_marker}


def forget_context(instance_id) -> None:
    """Called by StrategyWheel.run_once when the lane is disabled or has no
    adapter: regrades and manual runs stop with it, not on the last config."""
    with _CONTEXT_LOCK:
        _CONTEXT.pop(str(instance_id), None)


def context_for(instance_id):
    with _CONTEXT_LOCK:
        return _CONTEXT.get(str(instance_id))


def weekly_scan_pending(cfg, marker, today):
    """The refusal text when `today` is the weekly scan's day or its fallback
    day and this week's scan has not completed (`marker` is its completion
    marker), else None. The schedule is the lane's own (strategy_wheel._weekly):
    `scan_weekday`, then the next day if that scan did not finish."""
    scan_time = str(cfg.get("scan_time_et") or "").strip()
    if not scan_time:
        return None                       # no weekly scan is scheduled at all
    scan_wd = int(cfg.get("scan_weekday", 0)) % 7
    fallback_wd = (scan_wd + 1) % 7
    if today.weekday() not in (scan_wd, fallback_wd):
        return None
    if wheel_rules.scan_already_ran_this_week(marker, today):
        return None
    return (f"The weekly wheel scan has not run yet this week (it runs on "
            f"{_WEEKDAY_NAMES[scan_wd]} at {scan_time} ET, and on "
            f"{_WEEKDAY_NAMES[fallback_wd]} if it does not finish); run this after it "
            "finishes.")


# -- regrade: the pure part ----------------------------------------------------

def regrade_in_progress(signal, now=None) -> bool:
    block = (signal or {}).get("regrade") or {}
    if block.get("status") not in ("queued", "running"):
        return False
    return not is_stale(block.get("requested_at"), REGRADE_STALE_S, now)


def request_block(signal, requested_by, now_iso) -> dict:
    """The `regrade` object the API writes when the operator asks."""
    prior = dict((signal or {}).get("regrade") or {})
    return {**prior, "status": "queued", "requested_at": now_iso,
            "requested_by": requested_by, "error": None}


def _snapshot(signal) -> dict:
    """What the card said before this regrade."""
    proposal = signal.get("proposal") or {}
    context = signal.get("context") or {}
    prior = signal.get("regrade") or {}
    return {"score": signal.get("score"),
            "recommendation": signal.get("recommendation"),
            "stock_price": context.get("stock_price"),
            "strike": proposal.get("strike"),
            "premium_est": proposal.get("premium_est"),
            "expiry": proposal.get("expiry"),
            # When these numbers were scored: the last regrade that re-scored,
            # else the card's creation. A regrade that did not re-score (or
            # failed) leaves them as they were, so it never re-dates them.
            "at": prior.get("scored_at") or signal.get("created_at")}


def build_regrade(signal, *, candidate, scored, reason, now_iso) -> dict:
    """The signal row after a regrade. Pure.

    `candidate` is today's screened candidate, or None when the name no longer
    passes (then `reason` says why and the score, strike and reasoning are
    left exactly as they were). `scored` is ai_analyst.score_candidate's
    answer for `candidate`. The status is never changed here.
    """
    doc = dict(signal)
    prior = dict(signal.get("regrade") or {})
    block = {"status": "done", "requested_at": prior.get("requested_at"),
             "requested_by": prior.get("requested_by"), "finished_at": now_iso,
             "count": int(prior.get("count") or 0) + 1, "error": None,
             "scored_at": prior.get("scored_at"), "previous": _snapshot(signal)}
    if candidate is None:
        block.update({"qualifies": False, "rescored": False,
                      "reason": str(reason or "no longer passes the wheel screen")})
        doc["regrade"] = block
        return doc
    rec = str(scored.get("recommendation") or "").lower()
    contracts = int(scored.get("position_size_contracts") or 1)
    doc["score"] = scored.get("conviction_score")
    doc["recommendation"] = rec.upper()
    doc["reasoning"] = str(scored.get("reasoning") or "")
    doc["key_risks"] = [str(r) for r in (scored.get("key_risks") or [])]
    doc["proposal"] = {**(signal.get("proposal") or {}), "contract": None,
                       "strike": candidate["strike_price"],
                       "expiry": candidate["expiry"], "qty": contracts,
                       "limit_price": None, "premium_est": candidate["est_premium"],
                       "delta": None}
    doc["context"] = {**(signal.get("context") or {}),
                      "stock_price": candidate["stock_price"],
                      "otm_pct": candidate["otm_pct"], "rsi": candidate["rsi"],
                      "atr_pct": candidate["atr_pct"],
                      "earnings_days": candidate.get("earnings_days")}
    qualifies = rec != "reject"
    block.update({"qualifies": qualifies, "rescored": True, "scored_at": now_iso,
                  "reason": None if qualifies else
                  f"score {doc['score']} is below the review threshold"})
    doc["regrade"] = block
    return doc


def failed_block(signal, error, now_iso) -> dict:
    prior = dict((signal or {}).get("regrade") or {})
    return {**prior, "status": "failed", "finished_at": now_iso,
            "error": str(error)[:300]}


# -- one name at today's bars --------------------------------------------------

def fresh_candidate(symbol, cfg, today, *, bars_fn=None, earnings_fn=None):
    """(candidate, reason): today's wheel candidate for `symbol`, or
    (None, why it is not one). The same screen, earnings rule and expiry the
    weekly scan applies; the sector cap is a whole-scan rule and is not."""
    symbol = universe.norm_symbol(symbol)
    if bars_fn is None:
        client = market_data.data_client(cfg.get("alpaca_key"), cfg.get("alpaca_secret"))

        def bars_fn(symbols):
            return market_data.get_daily_bars(symbols, days=int(WHEEL_WARMUP_DAYS * 1.5),
                                              client=client)
    raw = bars_fn([symbol]) or {}
    if symbol not in raw:
        raise RuntimeError(f"no market data returned for {symbol}")
    lines, stats = [], {}
    pre = wheel_rules.screen_technicals(raw, [symbol], cfg=cfg, stats=stats,
                                        log=lambda msg, color="white": lines.append(str(msg)))
    if not pre:
        if stats.get("errors") or stats.get("nan") or stats.get("short_history"):
            raise RuntimeError(f"{symbol}: the screen could not be computed "
                               f"({'; '.join(lines)[-200:] or 'no detail'})")
        why = next((ln.split("FILTERED — ", 1)[1] for ln in lines if "FILTERED — " in ln),
                   "no longer passes the wheel screen")
        return None, why
    expiry = wheel_rules.next_friday(today, clock.trading_days)
    days = (earnings_fn or wheel_rules.wheel_earnings_days)(symbol)
    candidate = wheel_rules.apply_earnings(
        pre[0], days, expiry=expiry, earnings_block_days=int(cfg["earnings_block_days"]))
    if candidate is None:
        return None, f"earnings in {days} days, inside the {cfg['earnings_block_days']}-day block"
    return candidate, None


def _score(candidate, cfg, role, score_fn=None):
    return (score_fn or ai_analyst.score_candidate)(
        candidate, role=role, approve_threshold=int(cfg["approve_threshold"]),
        review_threshold=int(cfg["review_threshold"]), rsi_min=cfg["rsi_min"],
        rsi_max=cfg["rsi_max"], days_to_expiry=int(cfg["days_to_expiry"]))


def _scoring_failed(scored) -> bool:
    """score_candidate never raises: a failed call comes back as score 0 with
    this reasoning. Recording that would overwrite a real score with a zero."""
    return str((scored or {}).get("reasoning") or "").startswith(_SCORING_FAILED)


def _mirror_scan_row(signal, doc) -> None:
    """Both UIs link a scan row to its signal by strike; keep the row in step."""
    try:
        rid = signals_store.signal_id_for(signal.get("instance_id"), "wheelscan",
                                          signal.get("session"), signal.get("symbol"))
        row = store.get(SCANS_TABLE, rid)
        if not row:
            return
        store.update(SCANS_TABLE, rid, {
            "stock_price": doc["context"].get("stock_price"),
            "strike": doc["proposal"].get("strike"),
            "expiry": doc["proposal"].get("expiry"),
            "premium_est": doc["proposal"].get("premium_est"),
            "score": doc.get("score"), "recommendation": doc.get("recommendation"),
            "reasoning": doc.get("reasoning")})
    except Exception as exc:  # the scan table is a view of the run, never the truth
        _log(f"regrade: scan row not updated ({type(exc).__name__}: {exc})", "yellow")


def run_regrade(signal_id, ctx, *, today=None, bars_fn=None, earnings_fn=None,
                score_fn=None, now_iso=None) -> dict:
    """Regrade one pending wheel signal and write it back. Returns the
    `regrade` block that was written. Never raises."""
    signal = signals_store.get_signal(signal_id)
    if not signal:
        return {"status": "failed", "error": "signal not found"}

    def fail(error):
        block = failed_block(signals_store.get_signal(signal_id) or signal, error,
                             now_iso or _now_iso())
        try:
            signals_store.update_signal(signal_id, {"regrade": block})
        except Exception as exc:  # pragma: no cover - the store is down
            _log(f"regrade {signal_id}: could not record the failure ({exc})", "red")
        _log(f"regrade {signal.get('symbol')}: FAILED — {error}", "yellow")
        return block

    try:
        if signal.get("lane") != "wheel":
            return fail("only wheel signals can be regraded")
        if signal.get("status") != "pending":
            return fail(f"the signal is {signal.get('status')}, not pending")
        cfg = dict(ctx.get("cfg") or {})
        role = ai_analyst.llm_role_from_config(cfg)
        if role is None and score_fn is None:
            return fail("no conviction model is linked to the wheel lane")
        signals_store.update_signal(signal_id, {"regrade": {
            **(signal.get("regrade") or {}), "status": "running"}})
        today = today or date.fromisoformat(clock.ny_date(datetime.now(timezone.utc)))
        candidate, reason = fresh_candidate(signal["symbol"], cfg, today,
                                            bars_fn=bars_fn, earnings_fn=earnings_fn)
        scored = None
        if candidate is not None:
            with _REGRADE_SLOTS:
                scored = _score(candidate, cfg, role, score_fn)
            if _scoring_failed(scored):
                return fail(scored.get("reasoning"))
        latest = signals_store.get_signal(signal_id) or signal
        if latest.get("status") != "pending":
            # build_regrade copies the status it is given. A row read while
            # approved/submitted can be back to pending by the CAS below (a
            # broker back_to_pending), and that status must never be written.
            return fail("the signal was decided while it was being regraded")
        doc = build_regrade(latest, candidate=candidate, scored=scored, reason=reason,
                            now_iso=now_iso or _now_iso())
        # Compare-and-swap on `pending`: a decision made while the model was
        # thinking wins, and its order is rebuilt from the row it approved.
        if not signals_store.cas_signal(signal_id, expect_status="pending", doc=doc):
            return fail("the signal was decided while it was being regraded")
        if candidate is not None:
            _mirror_scan_row(latest, doc)
        before = doc["regrade"]["previous"]
        _log(f"regrade {signal['symbol']}: score {before.get('score')} -> {doc.get('score')}, "
             f"price ${before.get('stock_price')} -> ${(doc.get('context') or {}).get('stock_price')}, "
             f"strike ${before.get('strike')} -> ${(doc.get('proposal') or {}).get('strike')}"
             + ("" if doc["regrade"]["qualifies"] else
                f" | NO LONGER QUALIFIES: {doc['regrade']['reason']}"), "cyan")
        return doc["regrade"]
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}")


# -- the manual scan -----------------------------------------------------------

def latest_run(instance_id):
    """The newest manual run row for the instance, or None."""
    rows = store.run(store.limit(store.order_by(
        store.filter(RUNS_TABLE, P.field("instance_id").eq(str(instance_id))),
        fields=(store.desc("started_at"),)), 1))
    rows = list(rows)
    return rows[0] if rows else None


def run_in_progress(run, now=None) -> bool:
    if not run or run.get("status") != "running":
        return False
    return not is_stale(run.get("heartbeat_at") or run.get("started_at"), RUN_STALE_S, now)


def new_run(instance_id, requested_by, now_iso, run_id=None) -> dict:
    return {"id": run_id or uuid.uuid4().hex, "instance_id": str(instance_id),
            "status": "running", "phase": "screening", "requested_by": requested_by,
            "started_at": now_iso, "heartbeat_at": now_iso, "finished_at": None,
            "session": None, "scanned": 0, "passed_screen": 0, "to_score": 0,
            "scored": 0, "new_pending": 0, "rejected": 0, "skipped_existing": 0,
            "errors": 0, "error": None}


def open_card_symbols(instance_id) -> set:
    """Wheel names that already have a card the operator can act on, or an
    order that came from one. A manual run does not score those again: the
    card is regraded instead."""
    return {str(r.get("symbol") or "").upper()
            for r in signals_store.all_signals(instance_id)
            if r.get("lane") == "wheel" and r.get("status") in _OPEN_CARD_STATUSES
            and not r.get("outcome")}


def run_manual_scan(run, ctx, *, today=None, bars_fn=None, earnings_fn=None,
                    score_fn=None, workers=SCAN_WORKERS, release=None) -> dict:
    """Screen the wheel universe and score what passes, for today's session.
    Writes pending / ai_rejected signals and scan rows; places nothing.
    Returns the finished run row. Never raises. `release` (the command's
    one-run-at-a-time guard) is called once the run row reads done, before the
    summary notification, so a click in that window starts a new run."""
    iid = run["instance_id"]
    run = dict(run)

    def save(**patch):
        run.update(patch, heartbeat_at=_now_iso())
        try:
            store.insert(RUNS_TABLE, dict(run), conflict="replace")
        except Exception as exc:  # pragma: no cover - the store is down
            _log(f"manual scan: run row not saved ({exc})", "yellow")

    try:
        signals_store.ensure_tables()
        cfg = dict(ctx.get("cfg") or {})
        role = ai_analyst.llm_role_from_config(cfg)
        if role is None and score_fn is None:
            raise RuntimeError("no conviction model is linked to the wheel lane")
        today = today or date.fromisoformat(clock.ny_date(datetime.now(timezone.utc)))
        session = today.isoformat()
        save(session=session, phase="screening")
        symbols = [universe.norm_symbol(s) for s in universe.get_wheel_universe()]
        if bars_fn is None:
            client = market_data.data_client(cfg.get("alpaca_key"), cfg.get("alpaca_secret"))

            def bars_fn(names):
                return market_data.get_daily_bars(
                    names, days=int(WHEEL_WARMUP_DAYS * 1.5), client=client)
        raw = bars_fn(symbols)
        if not raw:
            raise RuntimeError("no market data returned")
        pre = wheel_rules.screen_technicals(raw, symbols, cfg=cfg,
                                            log=lambda msg, color="white": None)
        expiry = wheel_rules.next_friday(today, clock.trading_days)
        save(scanned=len(symbols), passed_screen=len(pre), phase="earnings")
        _log(f"manual scan {session} | {len(pre)} of {len(symbols)} passed the screen; "
             f"expiry {expiry}", "cyan")

        earnings = earnings_fn or wheel_rules.wheel_earnings_days
        candidates = []
        for p in pre:
            c = wheel_rules.apply_earnings(
                p, earnings(p["symbol"]), expiry=expiry,
                earnings_block_days=int(cfg["earnings_block_days"]))
            if c is not None:
                candidates.append(c)
            # One yfinance call per name, sequential: keep the heartbeat live
            # or a slow phase reads as a dead run (RUN_STALE_S) mid-way.
            save()
        queue = wheel_rules.sector_filter(candidates,
                                          max_per_sector=int(cfg["max_per_sector"]))

        # Names with a card, an open short put or a working sell order are
        # left alone. An unreadable book skips nothing: a card is not an
        # order, and the approval path reads the book strictly itself.
        taken = open_card_symbols(iid)
        adapter = ctx.get("adapter")
        positions = account.option_positions(adapter) if adapter is not None else None
        book = account.open_orders(adapter) if adapter is not None else None
        todo, skipped = [], 0
        for c in queue:
            sid = signals_store.signal_id_for(iid, "wheel", session, c["symbol"])
            held = (positions is not None and book is not None
                    and wheel_rules.duplicate_put_reason(c["symbol"], positions, book, {}))
            if c["symbol"].upper() in taken or held or signals_store.get_signal(sid):
                skipped += 1
                continue
            todo.append(c)
        save(phase="scoring", to_score=len(todo), skipped_existing=skipped)

        lock = threading.Lock()
        found = []

        def consider(c):
            scored = _score(c, cfg, role, score_fn)
            if _scoring_failed(scored):
                with lock:
                    save(scored=run["scored"] + 1, errors=run["errors"] + 1)
                _log(f"manual scan {session} | {c['symbol']}: {scored.get('reasoning')}",
                     "yellow")
                return
            rec = str(scored.get("recommendation") or "").lower()
            score = scored.get("conviction_score")
            contracts = int(scored.get("position_size_contracts") or 1)
            status = "ai_rejected" if rec == "reject" else "pending"
            doc = signals_store.new_signal(
                instance_id=iid, lane="wheel", symbol=c["symbol"], session=session,
                score=score, recommendation=rec, reasoning=scored.get("reasoning"),
                key_risks=scored.get("key_risks"), size_adjustment=None,
                proposal={"contract": None, "strike": c["strike_price"],
                          "expiry": c["expiry"], "qty": contracts, "limit_price": None,
                          "premium_est": c["est_premium"], "delta": None},
                status=status,
                context={"stock_price": c["stock_price"], "otm_pct": c["otm_pct"],
                         "rsi": c["rsi"], "atr_pct": c["atr_pct"],
                         "earnings_days": c.get("earnings_days")})
            doc["source"] = "manual_scan"
            doc["run_id"] = run["id"]
            if not signals_store.insert_signal_once(doc):
                # The weekly scan wrote this row while the model was thinking.
                # Its row (and its scan row) are the truth: leave both alone.
                with lock:
                    save(scored=run["scored"] + 1,
                         skipped_existing=run["skipped_existing"] + 1)
                _log(f"manual scan {session} | {c['symbol']}: a row for today already "
                     "exists (the weekly scan) — left as it is", "cyan")
                return
            signals_store.insert_wheel_scan({
                "instance_id": iid, "session": session, "symbol": c["symbol"],
                "stock_price": c["stock_price"], "strike": c["strike_price"],
                "expiry": c["expiry"], "premium_est": c["est_premium"], "score": score,
                "recommendation": rec.upper(), "reasoning": scored.get("reasoning") or "",
                "status": "rejected" if rec == "reject" else "pending",
                "skip_reason": None})
            with lock:
                if rec == "reject":
                    save(scored=run["scored"] + 1, rejected=run["rejected"] + 1)
                else:
                    found.append((c["symbol"], score, c["strike_price"]))
                    save(scored=run["scored"] + 1, new_pending=run["new_pending"] + 1)
            _log(f"manual scan {session} | {c['symbol']} ${c['stock_price']:.2f}, strike "
                 f"${c['strike_price']}: score {score} -> "
                 f"{'REJECTED' if rec == 'reject' else 'CARD'}",
                 "yellow" if rec == "reject" else "green")

        def guarded(c):
            try:
                consider(c)
            except Exception as exc:
                with lock:
                    save(scored=run["scored"] + 1, errors=run["errors"] + 1)
                _log(f"manual scan {session} | {c['symbol']}: skipped after "
                     f"{type(exc).__name__}: {exc}", "yellow")

        if todo:
            with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
                list(pool.map(guarded, todo))
        save(status="done", phase="done", finished_at=_now_iso())
        if release is not None:
            try:
                release()
            except Exception:  # pragma: no cover - the guard also clears on exit
                pass
        lines = [f"{session} (manual run, nothing was ordered)",
                 f"Scanned {run['scanned']} | passed screen {run['passed_screen']} | "
                 f"scored {run['scored']}",
                 f"New cards: {run['new_pending']} | rejected: {run['rejected']} | "
                 f"already had a card: {run['skipped_existing']}"]
        lines += [f"{sym} ${strike} put (score {score})"
                  for sym, score, strike in sorted(found, key=lambda f: -(f[1] or 0))[:10]]
        notify.send("swing_run_summary", iid, "Wheel manual run complete", "\n".join(lines))
    except Exception as exc:
        save(status="failed", phase="failed", finished_at=_now_iso(),
             error=f"{type(exc).__name__}: {exc}"[:300])
        _log(f"manual scan FAILED — {type(exc).__name__}: {exc}", "red")
    return run


# -- the LiveCommands entry point ----------------------------------------------

def _spawn(target, *args) -> None:
    threading.Thread(target=target, args=args, daemon=True,
                     name="wheel-manual").start()


def _release(key, token) -> None:
    """Free `key` only while `token` still holds it: a run that released
    early (before its summary) must not free a newer run's key on exit."""
    with _ACTIVE_LOCK:
        if _ACTIVE.get(key) == token:
            del _ACTIVE[key]


def _guarded(key, token, target, *args) -> None:
    try:
        target(*args)
    finally:
        _release(key, token)


def _weekly_refusal(ctx, today):
    """weekly_scan_pending for the remembered context on `today`, the NY date
    execute_command read once and also hands to the run."""
    marker = None
    reader = ctx.get("weekly_marker")
    if callable(reader):
        try:
            marker = reader()
        except Exception as exc:  # unreadable marker: treat as "not completed"
            _log(f"manual scan: weekly-scan marker unreadable ({type(exc).__name__}: {exc})",
                 "yellow")
    try:
        return weekly_scan_pending(ctx.get("cfg") or {}, marker, today)
    except Exception as exc:
        # An unparseable schedule refuses the weekly scan itself
        # (strategy_wheel._weekly), so there is nothing to collide with.
        _log(f"manual scan: weekly-scan schedule unreadable ({type(exc).__name__}: {exc}); "
             "not held for it", "yellow")
        return None


def execute_command(ctype, payload, *, instance_id, spawn=None, today=None):
    """(ok, error, result) for a `swing_regrade` or `wheel_manual_scan`
    LiveCommand. Returns as soon as the work is handed to its own thread; the
    outcome is written to the signal row or the run row, where the UIs read it.
    `today` (the NY date) is for tests."""
    payload = payload or {}
    iid = str(payload.get("instance_id") or instance_id or "")
    ctx = context_for(iid)
    spawn = spawn or _spawn

    def refuse(error):
        # The API marked the row queued / running before the command was
        # sent; a refusal has to clear that or the button spins until stale.
        try:
            if ctype == REGRADE_COMMAND and payload.get("signal_id"):
                signal = signals_store.get_signal(payload["signal_id"])
                if signal:
                    signals_store.update_signal(payload["signal_id"], {
                        "regrade": failed_block(signal, error, _now_iso())})
            elif ctype == SCAN_COMMAND and payload.get("run_id"):
                row = store.get(RUNS_TABLE, str(payload["run_id"]))
                if row and row.get("status") == "running":
                    store.update(RUNS_TABLE, row["id"], {
                        "status": "failed", "phase": "failed", "error": error,
                        "finished_at": _now_iso()})
        except Exception:
            pass
        return (False, error, {})

    if ctx is None:
        return refuse("the wheel lane has not completed a tick since this instance "
                      "started; try again after its next tick (up to 20 minutes)")
    if ctype == REGRADE_COMMAND:
        sid = str(payload.get("signal_id") or "")
        if not sid:
            return (False, "swing_regrade: signal_id is required", {})
        key, token = f"regrade:{sid}", uuid.uuid4().hex
        with _ACTIVE_LOCK:
            busy = key in _ACTIVE
            if not busy:
                _ACTIVE[key] = token
        if busy:
            return refuse("a regrade of this card is already running")
        spawn(_guarded, key, token, run_regrade, sid, ctx)
        return (True, "", {"signal_id": sid, "started": True})
    if ctype == SCAN_COMMAND:
        key = f"scan:{iid}"
        run_id = str(payload.get("run_id") or "") or uuid.uuid4().hex
        with _ACTIVE_LOCK:
            active_id = _ACTIVE.get(key)
            if active_id is None:
                _ACTIVE[key] = run_id
        if active_id is not None:
            # The API saw no live heartbeat (or two clicks raced) and wrote a
            # second run row. That row would become latest_run and hide the
            # live run, and nothing would ever finish it: remove it.
            removed = None
            if run_id != active_id and payload.get("run_id"):
                try:
                    store.delete(RUNS_TABLE, run_id)
                    removed = run_id
                except Exception as exc:
                    _log(f"manual scan: duplicate run row {run_id} not removed ({exc})",
                         "yellow")
            return (True, "", {"already_running": True, "active_run_id": active_id,
                               "removed_run_id": removed})
        try:
            try:
                run = store.get(RUNS_TABLE, run_id)
            except Exception:
                run = None
            if run is not None and run.get("status") != "running":
                # e.g. the API's queue write raised after it landed, and the
                # API already marked the run failed: never run it behind that.
                _release(key, run_id)
                return refuse(f"run {run_id} is {run.get('status')}, not running; "
                              "start a new run")
            # ONE read of the New York date for both the hold check and the
            # run: read twice, a click just before midnight on the night before
            # scan day could pass the hold on Sunday and take Monday's session.
            day = today or date.fromisoformat(clock.ny_date(datetime.now(timezone.utc)))
            waiting = _weekly_refusal(ctx, day)
            if waiting:
                _release(key, run_id)
                return refuse(waiting)
            run = run or new_run(iid, payload.get("requested_by"), _now_iso(), run_id)
            spawn(_guarded, key, run_id,
                  functools.partial(run_manual_scan, today=day,
                                    release=functools.partial(_release, key, run_id)),
                  run, ctx)
        except BaseException:
            _release(key, run_id)
            raise
        return (True, "", {"run_id": run["id"], "started": True})
    return (False, f"unknown wheel command: {ctype}", {})
