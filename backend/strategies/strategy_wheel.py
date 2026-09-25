# INTELLISTOCK_SCHEMA: {"strategy": "strategy_wheel", "weight": 1.0, "execution_position": 20, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"strategy_wheel_enabled": false, "rsi_min": 30, "rsi_max": 60, "sma_trend": 50, "atr_period": 14, "strike_atr_mult": 0.5, "min_premium_pct": 0.005, "target_delta": 0.25, "days_to_expiry": 7, "max_collateral_pct": 0.25, "max_per_sector": 2, "auto_covered_call": false, "approve_threshold": 75, "review_threshold": 50, "earnings_block_days": 7, "limit_bid_mult": 0.95, "scan_weekday": 0, "scan_time_et": "10:30", "monitor_time_et": "15:45", "conviction_llm_model_id": ""}}
# INTELLISTOCK_DESCRIPTION: ST's options wheel (github.com/tmasters2876/swing-trader), live and paper only: every Monday at 10:30 ET — Tuesday if Monday's scan did not finish — it screens the S&P 500 for RSI 30–60, price above its 50-day SMA, ATR above 1.5% and a strike at least 1.5% out of the money, skips earnings within 7 days, caps two names per sector, and has the linked conviction model score each one. Scores of 75+ sell the weekly put nearest 0.25 delta at bid × 0.95, capped at 25% of equity per name and by cash; 50–74 wait for your approval. Daily at 15:45 ET it buys back puts 10% in the money (5% within two days of expiry, any amount on expiry day), and each scan buys back a put worth twice its premium. Inert in backtests: there is no historical option data.
"""Strategy Wheel wrapper: the options lane of the swing-trader port.

ST's wheel rules live in backend/swing_trader/wheel_rules.py. This file owns
the broker contract: the resumable weekly scan, the daily monitor, the IV
snapshot, and the `_nexus_option_orders` payload (interface contract §1).

Spec: docs/superpowers/specs/2026-09-24-swing-trader-port-design.md §5.2
"""
import math
import os
import re
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import live_calendar  # noqa: E402
from db import store  # noqa: E402  (tests monkeypatch this name)
from swing_trader import (  # noqa: E402
    account,
    ai_analyst,
    calibration,
    clock,
    iv,
    market_data,
    notify,
    signals_store,
    universe,
    wheel_rules,
)
from swing_trader.constants import (  # noqa: E402
    IV_SNAPSHOT_TIME_ET,
    WHEEL_DEFAULTS as DEFAULTS,
    WHEEL_WARMUP_DAYS,
)

try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="StrategyWheel")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[StrategyWheel] {msg}")


_SCAN_KEY = "_wheel_scan"                        # the resumable weekly scan
_COMPLETE_KEY = "_wheel_scan_complete_session"   # ST: last_wheel_scan_complete.txt
_MONITOR_KEY = "_wheel_monitor_session"          # the daily monitor, once a session
_IV_KEY = "_wheel_iv_session"                    # the IV snapshot, once a session
_NO_MODEL_KEY = "_wheel_no_model_session"
_CHECKS_KEY = "_wheel_checks_session"            # the scan's position checks, once a session
_ROW_ALERTS_KEY = "_wheel_row_alerts"            # {"session", "keys"}: one alert per row
_KNOWN_PUTS_KEY = "_wheel_known_puts"            # {contract: expiry}: short puts seen lately
_MONITOR_ALERT_KEY = "_wheel_monitor_alert_session"  # "monitor incomplete", once a session
_LOGGED_KEY = "_wheel_logged"

#: One yfinance earnings lookup.
EARNINGS_RESERVE_S = 5.0

_GRID = timedelta(minutes=clock.TICK_GRID_MIN)
#: A tick belongs to the grid tick it lands within half a step of.
_GRID_SLACK = timedelta(minutes=clock.TICK_GRID_MIN / 2)
#: Regular hours, for the schedule check at config load (M5).
_RTH_OPEN, _RTH_CLOSE = "09:30", "16:00"


def _once(cache, reason, scope) -> bool:
    """True the first time `reason` is seen in `scope` (a session)."""
    seen = cache.get(_LOGGED_KEY)
    if not isinstance(seen, dict):
        seen = {}
        cache[_LOGGED_KEY] = seen
    if seen.get(reason) == scope:
        return False
    seen[reason] = scope
    return True


def _log_once(cache, reason, scope, msg, color="white"):
    if _once(cache, reason, scope):
        _log(msg, color)


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _valid_hhmm(value):
    """"HH:MM" for a real wall-clock time, else None (G2 minor): clock.parse_hhmm
    falls back to 00:00, which would run the job at midnight."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value if value is not None else ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _one_close_per_contract(orders) -> list:
    """The monitor and the scan's 2× check each skip a contract with a WORKING
    buy, but neither sees the other's order from the same tick: the first
    buy-to-close of a contract wins (the monitor runs first)."""
    out, closing = [], set()
    for o in orders:
        if o.get("position_intent") == "buy_to_close":
            key = str(o.get("contract") or "").upper()
            if key in closing:
                _log(f"StrategyWheel | {o.get('contract')}: a buy-to-close was already "
                     f"emitted this tick — dropping the second ({o.get('reason')})", "yellow")
                continue
            closing.add(key)
        out.append(o)
    return out


#: wheel_rules.size_contracts' refusals: the lane's own sizing rules.
_ROUTINE_SKIPS = ("Insufficient cash", "Collateral cap")


def _routine_sizing_skip(error) -> bool:
    return str(error or "").startswith(_ROUTINE_SKIPS)


def _emit_options(orders) -> dict:
    if not orders:
        return {}
    return {"_nexus_position_sizes": {"_cash_reserve_floor_pct": 0.0},
            "_nexus_option_orders": list(orders)}


# -- the monitor's view of the option book (I-1) and its window (I-2) ----------

def _qty(p):
    """The signed quantity, or None when it cannot be read."""
    try:
        q = float(getattr(p, "qty", 0) or 0)
    except (TypeError, ValueError):
        return None
    return q if math.isfinite(q) else None


def _is_short(p) -> bool:
    q = _qty(p)
    return q is None or q < 0


def _readable_put(p) -> bool:
    """A short put whose contract fields the monitor's rules can use."""
    try:
        strike = float(getattr(p, "strike", 0) or 0)
        date.fromisoformat(str(getattr(p, "expiry", "") or "")[:10])
    except (TypeError, ValueError):
        return False
    return (_qty(p) is not None and math.isfinite(strike) and strike > 0
            and bool(str(getattr(p, "underlying", "") or "").strip()))


def _put_identity(p):
    """(kind, expiry) from the contract fields, else from the OCC symbol."""
    kind = str(getattr(p, "option_type", "") or "").strip().lower()
    expiry = str(getattr(p, "expiry", "") or "")[:10]
    if kind in ("put", "call") and expiry:
        return kind, expiry
    parts = wheel_rules.occ_parts(getattr(p, "symbol", ""))
    return (parts[2], parts[1]) if parts else (None, None)


def _remember_puts(cache, rows, today, *, complete):
    """The short puts on record, for the "monitor incomplete" alert's
    priority when the book itself cannot be read. A complete read replaces
    the record; an incomplete one only adds to it."""
    found = {}
    for p in rows or []:
        if not _is_short(p):
            continue
        kind, expiry = _put_identity(p)
        if kind == "put" and expiry:
            found[str(p.symbol).upper()] = expiry
    prior = cache.get(_KNOWN_PUTS_KEY)
    merged = found if complete or not isinstance(prior, dict) else {**prior, **found}
    cache[_KNOWN_PUTS_KEY] = {c: e for c, e in merged.items() if e >= today.isoformat()}


def _known_puts(cache, today) -> dict:
    """{contract: DTE} for the short puts on record that have not expired."""
    out = {}
    for c, e in (cache.get(_KNOWN_PUTS_KEY) or {}).items():
        try:
            dte = (date.fromisoformat(str(e)[:10]) - today).days
        except ValueError:
            continue
        if dte >= 0:
            out[c] = dte
    return out


def _row_alert_once(cache, session, key) -> bool:
    """True the first time `key` (a contract alert) is seen this session."""
    seen = cache.get(_ROW_ALERTS_KEY)
    if not isinstance(seen, dict) or seen.get("session") != session:
        seen = {"session": session, "keys": []}
        cache[_ROW_ALERTS_KEY] = seen
    if key in seen["keys"]:
        return False
    seen["keys"].append(key)
    return True


def _session_close(today, cache, session):
    """The session's regular close in UTC from the exchange calendar — 16:00
    ET, 13:00 on a half day. When the calendar cannot say, 16:00 ET, logged
    once: a half day would then be missed, and the log says so."""
    try:
        return live_calendar.nyse_session_close_utc(today)
    except Exception as exc:
        _log_once(cache, "no-close", session,
                  f"StrategyWheel {session} | the exchange calendar has no close for today "
                  f"({type(exc).__name__}: {exc}); the monitor assumes 16:00 ET, so a half "
                  "day would be missed", "yellow")
        return datetime.combine(today, dtime(16, 0), tzinfo=clock.NY).astimezone(timezone.utc)


def _monitor_start(today, monitor_time, close):
    """I-2: the last broker grid tick at or before min(monitor_time_et, the
    close) that is still before the close — 15:40 for 15:45 on a normal day,
    12:40 on a half day (13:00 close)."""
    hh, mm = (int(x) for x in monitor_time.split(":"))
    target = min(datetime.combine(today, dtime(hh, mm), tzinfo=clock.NY).astimezone(timezone.utc),
                 close)
    t_et = target.astimezone(clock.NY)
    minutes = t_et.hour * 60 + t_et.minute
    minutes -= minutes % clock.TICK_GRID_MIN
    start = datetime.combine(today, dtime(minutes // 60, minutes % 60),
                             tzinfo=clock.NY).astimezone(timezone.utc)
    return start - _GRID if start >= close else start


class StrategyWheel:
    # broker.py resolves strategy_wheel -> StrategyWheel; the name is not free.

    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = {**DEFAULTS, **(config or {})}
        if not _truthy(cfg.get("strategy_wheel_enabled", False)):
            return {}
        cache = strategy_cache if isinstance(strategy_cache, dict) else {}
        if data is not None:
            _log_once(cache, "backtest", "any",
                      "StrategyWheel: the wheel is live-only — there is no historical "
                      "option data (spec §1) — so this lane is inert in backtests.",
                      "yellow")
            return {}
        if portfolio_emulator is None:
            _log_once(cache, "no-emulator", str(current_time)[:10],
                      "StrategyWheel: REFUSING — no broker adapter to read the book.", "red")
            return {}
        session = clock.ny_date(current_time)
        today = date.fromisoformat(session)
        if not clock.is_trading_day(today):
            return {}
        # G2 minor: each schedule is validated here, at config load. An
        # unparseable time refuses its own job (None below) and says so once a
        # session; it never falls back to clock.parse_hhmm's 00:00.
        for key, job in (("scan_time_et", "weekly put scan"),
                         ("monitor_time_et", "daily put monitor")):
            valid = _valid_hhmm(cfg.get(key))
            if valid is None:
                _log_once(cache, f"bad-{key}", session,
                          f"StrategyWheel {session} | REFUSING the {job} — {key} "
                          f"{cfg.get(key)!r} is not an HH:MM time (ET); fix it in the "
                          "strategy editor.", "red")
            elif not _RTH_OPEN <= valid < _RTH_CLOSE:
                # M5: both jobs trade, so each must fall inside regular hours.
                _log_once(cache, f"bad-{key}", session,
                          f"StrategyWheel {session} | REFUSING the {job} — {key} "
                          f"{cfg.get(key)!r} is outside regular hours ({_RTH_OPEN}–"
                          f"{_RTH_CLOSE} ET); fix it in the strategy editor.", "red")
                valid = None
            cfg[key] = valid
        iid = str(cfg.get("instance_id") or "wheel")
        signals_store.ensure_tables()
        deadline = clock.tick_deadline(current_time, mode)
        orders = []
        for step in (self._monitor, self._weekly, self._iv):
            try:
                orders.extend(step(current_time, session, today, iid, cfg,
                                   portfolio_emulator, cache, deadline) or [])
            except Exception as exc:
                _log(f"StrategyWheel {session} | {step.__name__.lstrip('_')} failed "
                     f"({type(exc).__name__}: {exc})", "red")
        return _emit_options(_one_close_per_contract(orders))

    # -- the weekly scan (wheel_trader.py:1257-1407) ---------------------------

    def _weekly(self, now, session, today, iid, cfg, emu, cache, deadline):
        if cfg.get("scan_time_et") is None:
            return []                    # refused and logged at config load
        scan_wd = int(cfg.get("scan_weekday", 0)) % 7
        if today.weekday() not in (scan_wd, (scan_wd + 1) % 7):
            return []
        if not clock.at_or_after(now, cfg["scan_time_et"]) or not clock.is_rth(now):
            return []
        # The Tuesday fallback runs only when Monday's scan did not COMPLETE
        # (wheel_trader.py:1274-1277); the marker, never partial rows or a
        # started scan's state, says so.
        if wheel_rules.scan_already_ran_this_week(cache.get(_COMPLETE_KEY), today):
            return []
        orders = []
        # ST ran its position checks at the start of every scan run
        # (wheel_trader.py:1279-1284). Here they run once a session (Task 19
        # latches _CHECKS_KEY once the book was read), ahead of the model
        # check, since a buy-back needs no model, and ahead of the bars fetch,
        # so a failing preparation does not repeat them every tick.
        if cache.get(_CHECKS_KEY) != session:
            orders += self._position_checks(session, today, iid, cfg, emu, cache) or []
            if orders:
                # M2: a buy-back goes out alone; the scan starts next tick,
                # on a book that shows it.
                return orders
        role = ai_analyst.llm_role_from_config(cfg)
        if role is None:
            if cache.get(_NO_MODEL_KEY) != session:
                cache[_NO_MODEL_KEY] = session
                msg = ("no conviction model is linked (conviction_llm_model_id), so the "
                       "weekly put scan is refused — ST scored every candidate. Link a "
                       "model in the strategy editor.")
                _log(f"StrategyWheel {session} | REFUSING the scan — {msg}", "red")
                notify.send("strategy_error", iid, "Wheel: no model linked", msg, priority=1)
            return orders

        state = cache.get(_SCAN_KEY)
        if not isinstance(state, dict) or state.get("session") != session:
            if clock.time_left(deadline) < clock.PREPARE_RESERVE_S:
                return orders
            state = self._prepare(session, today, iid, cfg, cache)
            if state is None:
                return orders
            cache[_SCAN_KEY] = state
        orders += self._advance(state, session, iid, cfg, role, emu, deadline)
        if state["phase"] == "done":
            cache[_COMPLETE_KEY] = session
            cache.pop(_SCAN_KEY, None)
            self._notify_results(state, session, iid)
        return orders

    def _prepare(self, session, today, iid, cfg, cache):
        """wheel_trader.py:1285-1300: bars and the technical screen. Returns
        the scan state, or None to retry on the next tick."""
        try:
            client = market_data.data_client(cfg.get("alpaca_key"), cfg.get("alpaca_secret"))
        except RuntimeError as exc:
            _log_once(cache, "no-creds", session,
                      f"StrategyWheel {session} | REFUSING the scan — {exc}", "red")
            return None
        live_universe = [universe.norm_symbol(s) for s in universe.get_wheel_universe()]
        raw = market_data.get_daily_bars(live_universe, days=int(WHEEL_WARMUP_DAYS * 1.5),
                                         client=client)
        if not raw:
            _log(f"StrategyWheel {session} | no market data returned — the scan retries "
                 "next tick", "yellow")
            if _once(cache, "no-bars", session):
                notify.send("swing_run_summary", iid, "Wheel Scanner ⚠️ Failed",
                            f"{session}\nNo market data returned")
            return None
        pre = wheel_rules.screen_technicals(raw, live_universe, cfg=cfg)
        expiry = wheel_rules.next_friday(today, clock.trading_days)
        _log(f"StrategyWheel {session} | {len(pre)} of {len(live_universe)} passed the "
             f"technical screen; expiry {expiry}", "cyan")
        return {"session": session, "phase": "earnings", "expiry": expiry, "pre": pre,
                "cursor": 0, "candidates": [], "queue": [],
                "scanned": len(live_universe), "approved": [], "review": [],
                "rejected": 0, "committed": {}}

    def _advance(self, state, session, iid, cfg, role, emu, deadline):
        orders = []
        if state["phase"] == "earnings":
            while state["cursor"] < len(state["pre"]):
                if clock.time_left(deadline) < EARNINGS_RESERVE_S:
                    return orders
                p = state["pre"][state["cursor"]]
                state["cursor"] += 1
                days = wheel_rules.wheel_earnings_days(p["symbol"])
                c = wheel_rules.apply_earnings(p, days, expiry=state["expiry"],
                                               earnings_block_days=int(cfg["earnings_block_days"]))
                if c is None:
                    _log(f"  [wheel] {p['symbol']}: FILTERED — earnings in {days} days")
                    continue
                state["candidates"].append(c)
            state["queue"] = wheel_rules.sector_filter(
                state["candidates"], max_per_sector=int(cfg["max_per_sector"]))
            _log(f"  {len(state['queue'])} candidate(s) after sector filter → sending to AI scorer")
            state["phase"], state["cursor"] = "scoring", 0

        if state["phase"] == "scoring":
            # fix F3 / fix 1: open puts AND working orders, the book read
            # strictly. Unreadable, no put is sold and the cursor stays put.
            positions, book = account.option_positions(emu), account.open_orders(emu)
            if positions is None or book is None:
                _log(f"StrategyWheel {session} | the option book is unreadable or not "
                     "ready — no put is sold this tick (fix 1 needs it); the scan resumes "
                     "next tick", "yellow")
                return orders
            acct = account.account_options(emu)
            if acct is None:
                # M4: cash or equity could not be read; a candidate sized on
                # an invented 0.0 would be burned for "insufficient cash".
                _log(f"StrategyWheel {session} | the account (cash, equity) is unreadable "
                     "— no put is sold this tick; the scan resumes next tick", "yellow")
                return orders
            while state["cursor"] < len(state["queue"]):
                if clock.time_left(deadline) < clock.CANDIDATE_RESERVE_S:
                    return orders
                c = state["queue"][state["cursor"]]
                state["cursor"] += 1
                try:
                    order = self._consider(c, state, session, iid, cfg, role, emu,
                                           positions, book, acct)
                except Exception as exc:
                    _log(f"StrategyWheel {session} | {c['symbol']}: skipped after "
                         f"{type(exc).__name__}: {exc} — the scan continues (fix 5)",
                         "yellow")
                    continue
                if order:
                    orders.append(order)
            state["phase"] = "done"
        return orders

    def _consider(self, c, state, session, iid, cfg, role, emu, positions, book, acct):
        """wheel_trader.py:1326-1356 for one candidate."""
        symbol = c["symbol"]
        sid = signals_store.signal_id_for(iid, "wheel", session, symbol)
        existing = signals_store.get_signal(sid)
        if existing is not None:
            # A resumed scan: the decision is recorded; never re-score.
            scored = dict(c, conviction_score=existing.get("score"),
                          recommendation=str(existing.get("recommendation") or "").lower(),
                          reasoning=existing.get("reasoning") or "",
                          position_size_contracts=int((existing.get("proposal") or {}).get("qty") or 1),
                          key_risks=existing.get("key_risks") or [])
        else:
            scored = ai_analyst.score_candidate(
                c, role=role, approve_threshold=int(cfg["approve_threshold"]),
                review_threshold=int(cfg["review_threshold"]), rsi_min=cfg["rsi_min"],
                rsi_max=cfg["rsi_max"], days_to_expiry=int(cfg["days_to_expiry"]))
        rec, score = scored["recommendation"], scored["conviction_score"]
        contracts = int(scored.get("position_size_contracts") or 1)
        scan_row = {"instance_id": iid, "session": session, "symbol": symbol,
                    "stock_price": c["stock_price"], "strike": c["strike_price"],
                    "expiry": c["expiry"], "premium_est": c["est_premium"], "score": score,
                    "recommendation": str(rec).upper(),
                    "reasoning": scored.get("reasoning") or ""}
        review_proposal = {"contract": None, "strike": c["strike_price"],
                           "expiry": c["expiry"], "qty": contracts, "limit_price": None,
                           "premium_est": c["est_premium"], "delta": None}

        def record(status, proposal, **extra):
            if existing is None:
                doc = signals_store.new_signal(
                    instance_id=iid, lane="wheel", symbol=symbol, session=session,
                    score=score, recommendation=rec, reasoning=scored.get("reasoning"),
                    key_risks=scored.get("key_risks"), size_adjustment=None,
                    proposal=proposal, status=status,
                    context={"stock_price": c["stock_price"], "otm_pct": c["otm_pct"],
                             "rsi": c["rsi"], "atr_pct": c["atr_pct"],
                             "earnings_days": c.get("earnings_days")})
                doc.update(extra)
                signals_store.insert_signal(doc)

        if rec == "reject":
            record("ai_rejected", review_proposal)
            signals_store.insert_wheel_scan(dict(scan_row, status="rejected", skip_reason=None))
            state["rejected"] += 1
            return None

        if rec == "review":
            record("pending", review_proposal)
            signals_store.insert_wheel_scan(dict(scan_row, status="pending", skip_reason=None))
            state["review"].append({"symbol": symbol, "strike_price": c["strike_price"],
                                    "conviction_score": score})
            if existing is None:
                notify.send("wheel_pending_review", iid,
                            f"⚠️ WHEEL REVIEW: {symbol} (score {score}/100)",
                            f"Sell ${c['strike_price']} put  exp {c['expiry']}\n"
                            f"Est. premium ~${c['est_premium']:.2f}/share\n"
                            f"{str(scored.get('reasoning') or '')[:120]}\n"
                            f"Approve or reject in IntelliStock (web or iOS).", priority=1)
            return None

        # approve
        if existing is not None and existing.get("status") != "auto_approved":
            return None
        # fix 1: an open short put, a working sell order, or a put this scan
        # already ordered on the underlying refuses a second one.
        duplicate = wheel_rules.duplicate_put_reason(symbol, positions, book, state["committed"])
        if duplicate:
            _log(f"  [wheel] {symbol}: {duplicate} — skipped (fix 1)")
            if existing is None:
                record("failed", review_proposal, error=duplicate)
                signals_store.insert_wheel_scan(dict(scan_row, status="skipped",
                                                     skip_reason=duplicate))
            return None
        try:
            order, error, meta = wheel_rules.build_put_order_live(
                dict(c, position_size_contracts=contracts), adapter=emu, cfg=cfg,
                equity=acct["equity"], cash=acct["cash"], option_positions=positions,
                open_orders=book, committed_collateral=state["committed"], signal_id=sid,
                session=session)
        except Exception as exc:
            # M1: an approved candidate whose order cannot be built is a
            # recorded failure the operator hears about, not a silent skip.
            order, error, meta = None, f"order build failed ({type(exc).__name__}: {exc})", {}
        if order is None:
            record("failed", review_proposal, error=error)
            signals_store.insert_wheel_scan(dict(scan_row, status="skipped", skip_reason=error))
            if _routine_sizing_skip(error):
                # G8a ruling 3(f): the lane's own sizing rules refused it (cash,
                # the per-name collateral cap). Routine, and "place manually"
                # would advise past the caps: a run-summary line, no push.
                notify.send("swing_run_summary", iid, f"Wheel put skipped: {symbol}",
                            f"Sell ${c['strike_price']} put  exp {c['expiry']} was not sent\n"
                            f"Reason: {error}\nScore: {score}", priority=0)
                return None
            # M3: a push, not the run summary.
            notify.send("wheel_position_alert", iid,
                        f"Wheel order failed — place manually: {symbol}",
                        f"No order was handed to the broker for {symbol}\n"
                        f"Reason: {error}\n"
                        f"Place manually: Sell ${c['strike_price']} put  exp {c['expiry']}\n"
                        f"Est. premium: ${c['est_premium']:.2f}/share\nScore: {score}",
                        priority=1)
            return None
        record("auto_approved", {"contract": order["contract"], "strike": order["strike"],
                                 "expiry": order["expiry"], "qty": order["qty"],
                                 "limit_price": order["limit_price"],
                                 "premium_est": c["est_premium"], "delta": meta.get("delta")})
        signals_store.insert_wheel_scan(dict(scan_row, strike=order["strike"],
                                             expiry=order["expiry"], status="placed",
                                             skip_reason=None))
        state["committed"][symbol] = order["strike"] * 100 * order["qty"]
        state["approved"].append({"symbol": symbol, "strike_price": order["strike"],
                                  "expiry": order["expiry"], "est_premium": c["est_premium"],
                                  "limit_price": order["limit_price"], "qty": order["qty"],
                                  "conviction_score": score})
        if existing is None:
            # I-3: intent, not outcome — the engine's gate may still refuse
            # it, and that refusal is alerted on its own.
            notify.send("wheel_put_placed", iid, f"Put order submitted: {symbol}",
                        f"Sell-to-open handed to the order gate; a refusal is alerted "
                        f"separately\n{symbol} ${order['strike']} put\n"
                        f"Expiry: {order['expiry']}\n"
                        f"Limit: ${order['limit_price']:.2f}/share "
                        f"(~${order['limit_price'] * 100:.0f}/contract)\n"
                        f"Contracts: {order['qty']}\nScore: {score}", priority=1)
        return order

    def _notify_results(self, state, session, iid):
        """wheel_trader.py:1151-1177, the run summary."""
        approved, review, rejected = state["approved"], state["review"], state["rejected"]
        scanned = state.get("scanned", 0)
        if not approved and not review:
            notify.send("swing_run_summary", iid, "Wheel Scanner ✅ Run Complete",
                        f"{session}\nNo put-selling opportunities found\n"
                        f"Scanned: {scanned} symbols | Rejected: {rejected}")
            return
        lines = [session]
        if approved:
            lines.append(f"✅ APPROVED ({len(approved)}):")
            for c in approved:
                lines.append(f"  {c['symbol']} ${c['strike_price']} put  exp {c['expiry']}  "
                             f"~${c['est_premium']:.2f}/sh  score {c['conviction_score']}")
        if review:
            lines.append(f"⚠️ REVIEW ({len(review)}):")
            for c in review:
                lines.append(f"  {c['symbol']} ${c['strike_price']} put  score {c['conviction_score']}")
        lines.append(f"Scanned: {scanned} | Rejected: {rejected}")
        notify.send("swing_run_summary", iid, "Wheel Scanner ✅ Run Complete", "\n".join(lines))

    # -- position checks (wheel_trader.py:230-449) -------------------------------

    @staticmethod
    def _assigned_shares(cache) -> dict:
        """Net shares the engine recorded as assigned to this lane (A-live
        contract addition 13): put assignments add, call assignments remove."""
        net = {}
        for a in (cache or {}).get("_engine_wheel_assignments") or []:
            try:
                u = str(a.get("underlying") or "").strip().upper()
                shares = int(float(a.get("shares") or 0))
            except (AttributeError, TypeError, ValueError):
                continue
            if not u or shares <= 0:
                continue
            side = str(a.get("side") or "").strip().lower()
            net[u] = net.get(u, 0) + (shares if side == "buy" else -shares)
        return {u: n for u, n in net.items() if n > 0}

    def _position_checks(self, session, today, iid, cfg, emu, cache):
        """ST ran check_open_wheel_positions and check_assigned_positions at
        the start of every scan. Returns the buy-to-close orders, and latches
        the session once the book was read (an unreadable book retries)."""
        positions, book = account.option_positions(emu), account.open_orders(emu)
        if positions is None or book is None:
            _log(f"StrategyWheel {session} | the option book is unreadable — the 2× "
                 "premium check and the assignment check are retried next tick", "yellow")
            return []

        orders = []
        for p in positions:
            # G8a ruling 3(c): one unreadable row is skipped and logged; it
            # never raises out of _weekly on every tick (the monitor alerts it).
            q = _qty(p)
            if q is None:
                _log(f"  [wheel-exit] {getattr(p, 'symbol', '?')}: unreadable qty "
                     f"{getattr(p, 'qty', None)!r} — row skipped", "yellow")
                continue
            if q >= 0:
                continue
            try:
                qty = abs(int(q))
                cost_basis = abs(float(p.avg_entry_price)) * qty * 100
                current_value = abs(float(p.market_value))
                if cost_basis > 0:
                    _log(f"  [wheel-exit] {p.symbol}: collected=${cost_basis:.2f}  "
                         f"current_cost_to_close=${current_value:.2f}  "
                         f"ratio={current_value / cost_basis:.2f}x")
            except (TypeError, ValueError):
                continue
        for order, info in wheel_rules.two_x_exits(positions, open_orders=book):
            order["session"] = session
            orders.append(order)
            _log(f"  [wheel-exit] ⚠️ {order['contract']} at {info['loss_ratio']:.1f}× "
                 "premium — buying to close at market", "yellow")
            notify.send("wheel_position_alert", iid, f"Auto-close ordered: {order['underlying']}",
                        f"Auto-close ordered: {order['contract']} — buy-to-close handed to "
                        f"the order gate; a refusal is alerted separately\n"
                        f"Reason: {info['loss_ratio']:.1f}× the premium collected\n"
                        f"Premium collected: ${info['cost_basis']:.2f}\n"
                        f"Cost to close: ${info['current_value']:.2f} "
                        f"({info['loss_ratio']:.1f}× premium)", priority=1)
        _log("  [wheel-exit] Position check complete")
        _remember_puts(cache, positions, today, complete=True)

        # Covered calls only for shares the ENGINE assigned to this lane. ST
        # treated every uncovered 100-share holding that was not swing
        # inventory as an assignment; on a shared instance that would write
        # calls against shares the wheel never bought.
        assigned = self._assigned_shares(cache)
        held = account.equity_positions(emu)
        mine = {s: dict(p, qty=min(float(p["qty"]), float(assigned[s])))
                for s, p in held.items() if s in assigned}
        for s in sorted(set(held) - set(mine)):
            if float(held[s]["qty"]) >= 100:
                _log(f"  [covered-call] {s}: {held[s]['qty']:g} shares, none assigned to the "
                     "wheel lane — not a covered-call candidate")
        swing_owned = signals_store.swing_owned_symbols(iid, set(held))
        candidates = wheel_rules.covered_call_candidates(mine, positions, book,
                                                         swing_owned=swing_owned)
        if candidates:
            expiry = wheel_rules.next_friday(today, clock.trading_days)
            for c in candidates:
                title, msg = wheel_rules.dry_run_message(c, expiry)
                if _truthy(cfg.get("auto_covered_call", False)):
                    msg += ("\nauto_covered_call is on, but the engine's option gate accepts "
                            "sell-to-open puts only (option.sell_to_open_requires_put), so no "
                            "call was sent. Sell it manually.")
                _log(f"  [covered-call] DRY-RUN {c['symbol']}: would sell {c['n_contracts']} "
                     f"call(s) strike ≥ ${c['call_strike']:.2f} exp {expiry} — no order placed")
                # A-live F12: the engine's activity poller sends the one
                # wheel_assignment notification; this dry run is a position alert.
                notify.send("wheel_position_alert", iid, title, msg, priority=1)
        _log("  [covered-call] Assignment check complete")
        cache[_CHECKS_KEY] = session
        return orders

    # -- the daily monitor (app.py:1259-1411) -------------------------------------

    def _monitor(self, now, session, today, iid, cfg, emu, cache, deadline):
        """Once a session, on the last grid tick before min(monitor_time_et, the
        session's close) (I-2). It latches only when it saw the whole book; a
        failure on its last eligible tick, or a window no tick completed, is
        alerted once."""
        if cfg.get("monitor_time_et") is None:
            return []                    # refused and logged at config load
        if cache.get(_MONITOR_KEY) == session:
            return []
        t = clock.as_utc(now)
        close = _session_close(today, cache, session)
        start = _monitor_start(today, cfg["monitor_time_et"], close)
        if t < start - _GRID_SLACK:
            return []
        if t >= close:
            self._monitor_after_close(session, today, iid, emu, cache)
            return []
        if not clock.is_rth(now):
            return []
        try:
            orders, reason = self._run_monitor(now, session, today, iid, cfg, emu, cache)
        except Exception as exc:
            orders, reason = [], f"the monitor raised {type(exc).__name__}: {exc}"
        if reason is None:
            cache[_MONITOR_KEY] = session
            calibration.record_outcomes(iid, emu, "wheel")
            return orders
        last = t + _GRID + timedelta(minutes=1) >= close
        _log(f"StrategyWheel {session} | the daily monitor did not complete — {reason}; "
             + ("this was its last tick before the close" if last else "it retries next tick"),
             "yellow")
        if last:
            self._monitor_incomplete_alert(session, today, iid, cache, reason)
        return orders

    def _run_monitor(self, now, session, today, iid, cfg, emu, cache):
        """app.py:1259-1411 over the rows it can read. Returns (orders,
        reason): reason is None only when the whole book was seen (I-1)."""
        rows, not_ready = account.option_book(emu)
        if rows is None:
            return [], not_ready
        book = account.open_orders(emu)
        if book is None:
            return [], "the working-order book is unreadable"
        puts, unknown = [], []
        for p in [p for p in rows if _is_short(p)]:
            kind = str(getattr(p, "option_type", "") or "").strip().lower()
            if kind == "call":
                # fix 6: short PUTS only; ST parsed any short OCC symbol as a put.
                _log(f"[wheel-monitor] {p.symbol}: a short call, not a wheel put — not "
                     "checked (fix 6)")
            elif kind == "put" and _readable_put(p):
                puts.append(p)
            else:
                unknown.append(p)        # I-1: alerted, never labelled "not a wheel put"
        _remember_puts(cache, [p for p in rows if _is_short(p)], today,
                       complete=not_ready is None and not unknown)
        for p in unknown:
            self._unknown_row_alert(p, session, today, iid, cache)
        working = {str(getattr(o, "symbol", "")).upper() for o in book
                   if str(getattr(o, "side", "")).lower() == "buy"}
        try:
            client = market_data.data_client(cfg.get("alpaca_key"), cfg.get("alpaca_secret"))
        except RuntimeError:
            client = None
        prices = market_data.live_prices(sorted({str(p.underlying).upper() for p in puts}),
                                         adapter=emu, client=client, now=now) if puts else {}
        orders, alerts, errors = [], 0, []
        for p in puts:
            try:
                d = wheel_rules.put_monitor_decision(
                    contract=p.symbol, underlying=str(p.underlying).upper(),
                    strike=float(p.strike), expiry=p.expiry,
                    stock_price=prices.get(str(p.underlying).upper()), today=today)
                _log(f"[wheel-monitor] {p.symbol}: underlying={d['underlying']} "
                     f"strike={d['strike']:.2f} stock={d['stock_price']} DTE={d['dte']} "
                     f"ITM={d['itm']} deep_itm={d['deep_itm']}")
                if d["stock_price"] is None:
                    # G3 minor 2: with no usable price the rules cannot see an
                    # ITM put; one near expiry is the operator's to check.
                    if d["dte"] <= 1 and self._no_price_alert(p, d, session, iid, cache):
                        alerts += 1
                    continue
                if d["action"] == "auto_close":
                    if str(p.symbol).upper() in working:
                        _log(f"[wheel-monitor] {p.symbol}: {d['reason']} — a buy-to-close "
                             "is already working; not sending another", "yellow")
                        continue
                    _log(f"[wheel-monitor] 🚨 AUTO-CLOSE ordered: {p.symbol} — {d['reason']}",
                         "red")
                    orders.append(wheel_rules.btc_order(p, abs(int(float(p.qty))), d["intent"],
                                                        session=session))
                if d["title"]:
                    # G8a ruling 3(e): an alert-only push is told once per
                    # contract and state per session, never again on a retry
                    # tick; a new state (OTM -> ITM) is still told.
                    if d["action"] != "auto_close" and not _row_alert_once(
                            cache, session, f"info:{str(p.symbol).upper()}:{d['action']}"):
                        continue
                    notify.send("wheel_position_alert", iid, d["title"], d["message"],
                                priority=int(d["priority"] or 0))
                    alerts += 1
            except Exception as exc:
                # G8a ruling 3(d): a put the rules could not evaluate keeps the
                # monitor from "Done": it retries next tick and, on its last
                # tick, alerts like an incomplete map.
                _log(f"[wheel-monitor] Error processing {p.symbol}: {exc}", "yellow")
                errors.append(f"{p.symbol} ({type(exc).__name__}: {exc})")
                continue
        if not_ready or unknown or errors:
            reason = "; ".join(r for r in (
                not_ready,
                unknown and f"{len(unknown)} short option(s) listed without contract fields",
                errors and (f"{len(errors)} short put(s) could not be evaluated: "
                            + ", ".join(errors))) if r)
            _log(f"[wheel-monitor] Incomplete — checked {len(puts)} readable short put(s), "
                 f"{alerts} alerts sent; {reason}", "yellow")
            return orders, reason
        _log(f"[wheel-monitor] Done — checked {len(puts)} short put positions, "
             f"{alerts} alerts sent")
        return orders, None

    def _monitor_after_close(self, session, today, iid, emu, cache):
        """No tick completed the monitor before the close (every eligible
        tick failed, or none landed in the window). Quiet only when a ready
        book shows nothing a put-monitor would check."""
        if cache.get(_MONITOR_ALERT_KEY) == session:
            return
        rows, not_ready = account.option_book(emu)
        at_risk = [p for p in rows or [] if _is_short(p)
                   and str(getattr(p, "option_type", "") or "").strip().lower() != "call"]
        if rows is not None and not_ready is None and not at_risk \
                and not _known_puts(cache, today):
            cache[_MONITOR_KEY] = session         # nothing was at risk
            return
        if rows is not None:
            _remember_puts(cache, at_risk, today, complete=False)
        self._monitor_incomplete_alert(
            session, today, iid, cache, "no tick completed the daily monitor before the close")

    def _monitor_incomplete_alert(self, session, today, iid, cache, reason):
        """I-2: once a session; priority 2 when a short put on record expires
        within a day."""
        if cache.get(_MONITOR_ALERT_KEY) == session:
            return
        cache[_MONITOR_ALERT_KEY] = session
        known = _known_puts(cache, today)
        listing = (", ".join(f"{c} ({d} DTE)" for c, d in sorted(known.items()))
                   or "none on record")
        _log(f"StrategyWheel {session} | ALERT — the daily put monitor did not complete: "
             f"{reason}", "red")
        notify.send("wheel_position_alert", iid,
                    "⚠️ Wheel monitor incomplete — check puts manually",
                    f"The daily put monitor did not complete on {session}: {reason}.\n"
                    "No monitor tick is left before the close, so an in-the-money put may "
                    "not have been bought back.\n"
                    f"Short puts on record: {listing}\n"
                    "Check them manually.",
                    priority=2 if any(d <= 1 for d in known.values()) else 1)

    def _unknown_row_alert(self, p, session, today, iid, cache):
        """I-1: a short option listed without its contract fields, once per
        contract per session. The OCC symbol says what it probably is."""
        symbol = str(getattr(p, "symbol", "") or "").upper()
        if not _row_alert_once(cache, session, f"unknown:{symbol}"):
            return
        parts = wheel_rules.occ_parts(symbol)
        urgent = False
        if parts:
            root, expiry, kind, strike = parts
            dte = (date.fromisoformat(expiry) - today).days
            urgent = kind == "put" and dte <= 1
            detail = (f"Its OCC symbol reads: {kind} ${strike:.2f} on {root}, expiring "
                      f"{expiry} ({dte} day(s)).")
        else:
            detail = "Its symbol is not a readable OCC symbol either."
        _log(f"[wheel-monitor] {symbol}: a short option without contract fields — alerting "
             "the operator (the rules cannot run on it)", "yellow")
        notify.send("wheel_position_alert", iid,
                    f"❓ Unreadable option — check manually: {symbol}",
                    f"{symbol}\nThe broker lists this short option without its contract "
                    "fields (type, underlying, strike or expiry), so the monitor cannot apply "
                    f"its rules to it.\n{detail}\nCheck it manually before the close.",
                    priority=2 if urgent else 1)

    def _no_price_alert(self, p, d, session, iid, cache) -> bool:
        """One "no price — check manually" alert per contract per session.
        Returns True when it was sent."""
        if not _row_alert_once(cache, session, f"noprice:{str(p.symbol).upper()}"):
            return False
        _log(f"[wheel-monitor] {p.symbol}: no usable price for {d['underlying']} with "
             f"{d['dte']} day(s) to expiry — alerting the operator", "yellow")
        notify.send("wheel_position_alert", iid,
                    f"❓ No price — check manually: {d['underlying']}",
                    f"{p.symbol}\nNo usable price for {d['underlying']}, so the monitor "
                    "cannot tell whether this put is in the money.\n"
                    f"Strike: ${d['strike']:.2f} | {d['dte']} day(s) to expiry\n"
                    "Check it manually before the close.",
                    priority=2 if d["dte"] <= 0 else 1)
        return True

    # -- the IV snapshot (iv_collector.py:232-257) ---------------------------------

    def _iv(self, now, session, today, iid, cfg, emu, cache, deadline):
        if cache.get(_IV_KEY) == session or not clock.at_or_after(now, IV_SNAPSHOT_TIME_ET):
            return []
        try:
            client = market_data.data_client(cfg.get("alpaca_key"), cfg.get("alpaca_secret"))
        except RuntimeError:
            client = None

        def spot_for(symbol):
            # iv_collector.py:128-133: the IEX latest trade, else the last close.
            if client is None:
                return None
            price = market_data.get_latest_price(symbol, client=client)
            if price is None:
                frame = market_data.get_daily_bars([symbol], days=7, client=client).get(symbol)
                if frame is None or frame.empty:
                    return None
                price = float(frame["Close"].iloc[-1])
            return price

        adapter = emu if callable(getattr(emu, "get_option_contracts", None)) else None
        summary = iv.run_iv_snapshot(store, adapter=adapter, spot_for=spot_for, today=today,
                                     deadline=deadline)
        if summary.get("complete"):
            cache[_IV_KEY] = session
        return []
