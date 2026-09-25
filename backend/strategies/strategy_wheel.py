# INTELLISTOCK_SCHEMA: {"strategy": "strategy_wheel", "weight": 1.0, "execution_position": 20, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"strategy_wheel_enabled": false, "rsi_min": 30, "rsi_max": 60, "sma_trend": 50, "atr_period": 14, "strike_atr_mult": 0.5, "min_premium_pct": 0.005, "target_delta": 0.25, "days_to_expiry": 7, "max_collateral_pct": 0.25, "max_per_sector": 2, "auto_covered_call": false, "approve_threshold": 75, "review_threshold": 50, "earnings_block_days": 7, "limit_bid_mult": 0.95, "scan_weekday": 0, "scan_time_et": "10:30", "monitor_time_et": "15:45", "conviction_llm_model_id": ""}}
# INTELLISTOCK_DESCRIPTION: ST's options wheel (github.com/tmasters2876/swing-trader), live and paper only: every Monday at 10:30 ET — Tuesday if Monday's scan did not finish — it screens the S&P 500 for RSI 30–60, price above its 50-day SMA, ATR above 1.5% and a strike at least 1.5% out of the money, skips earnings within 7 days, caps two names per sector, and has the linked conviction model score each one. Scores of 75+ sell the weekly put nearest 0.25 delta at bid × 0.95, capped at 25% of equity per name and by cash; 50–74 wait for your approval. Daily at 15:45 ET it buys back puts 10% in the money (5% within two days of expiry, any amount on expiry day), and each scan buys back a put worth twice its premium. Inert in backtests: there is no historical option data.
"""Strategy Wheel wrapper: the options lane of the swing-trader port.

ST's wheel rules live in backend/swing_trader/wheel_rules.py. This file owns
the broker contract: the resumable weekly scan, the daily monitor, the IV
snapshot, and the `_nexus_option_orders` payload (interface contract §1).

Spec: docs/superpowers/specs/2026-09-24-swing-trader-port-design.md §5.2
"""
import os
import re
import sys
from datetime import date

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

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
_LOGGED_KEY = "_wheel_logged"

#: One yfinance earnings lookup.
EARNINGS_RESERVE_S = 5.0


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


def _emit_options(orders) -> dict:
    if not orders:
        return {}
    return {"_nexus_position_sizes": {"_cash_reserve_floor_pct": 0.0},
            "_nexus_option_orders": list(orders)}


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
        return _emit_options(orders)

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
                _log(f"StrategyWheel {session} | the option book is unreadable — no put "
                     "is sold this tick (fix 1 needs it); the scan resumes next tick",
                     "yellow")
                return orders
            acct = account.account_options(emu)
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
        order, error, meta = wheel_rules.build_put_order_live(
            dict(c, position_size_contracts=contracts), adapter=emu, cfg=cfg,
            equity=acct["equity"], cash=acct["cash"], option_positions=positions,
            open_orders=book, committed_collateral=state["committed"], signal_id=sid,
            session=session)
        if order is None:
            record("failed", review_proposal, error=error)
            signals_store.insert_wheel_scan(dict(scan_row, status="skipped", skip_reason=error))
            notify.send("swing_run_summary", iid, f"⚠️ Wheel Order Failed: {symbol}",
                        f"⚠️ ORDER NOT SENT — {symbol}\nReason: {error}\n"
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
            notify.send("wheel_put_placed", iid, f"✅ Wheel: {symbol} Put Sold",
                        f"✅ PUT ORDER SENT\n{symbol} ${order['strike']} put\n"
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

    # -- Task 19 replaces these three ------------------------------------------

    def _position_checks(self, session, today, iid, cfg, emu, cache):
        return []

    def _monitor(self, now, session, today, iid, cfg, emu, cache, deadline):
        return []

    def _iv(self, now, session, today, iid, cfg, emu, cache, deadline):
        return []
