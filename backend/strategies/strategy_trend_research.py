# INTELLISTOCK_SCHEMA: {"strategy": "strategy_trend_research", "weight": 1.0, "execution_position": 10, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"strategy_trend_research_enabled": false, "tr_chain_id": "", "tr_model": "", "tr_prompt_version": "tr-v1", "research_model_cutoff": "", "max_positions": 12, "swap_margin": 2.0, "max_single_weight": 0.25, "max_levered_inverse_weight": 0.2, "stop_pct": 0.2, "max_llm_calls_per_build": 2000, "honour_single_position_cap": true, "broker_max_single_position_pct": 0.25}}
# INTELLISTOCK_DESCRIPTION: TR, the deep-research trend picker (backtest only). Once a week, at the first tick of the week's first session, it reads that week's research ledger row (written offline, before 09:30 ET) and holds 8-12 names: stocks plus a small set of commodity, defensive, inverse and leveraged ETFs, at most 25% of the account in one name and 20% in leveraged plus inverse ETFs together, with the rest in BIL. A holding is sold when its thesis breaks, when the research drops it, when a clearly better idea takes its slot, or when it closes 20% below its highest close since purchase; a stopped name stays out until the next weekly decision. A week with no research row keeps the current book. No AI calls during the backtest.
"""Strategy TR wrapper: replays the TR research ledger inside the engine.

The research runs offline (``trend_research.build_ledger``) and writes one
ledger row per week; this lane only reads the newest ``ok`` row whose
``as_of`` is before the decision and turns it into target weights through
``trend_research.book.target_book`` (pure). This file owns what needs the
broker: the emulator, the strategy cache, the weekly latch, the daily
trailing stop and the decision payload.

Clock (spec §7.3, C5, C7):

* Weekly decision -- the first tick at or after 09:30 ET on a NYSE session of
  a week not yet decided (normally the week's first session; a Monday holiday
  moves it to Tuesday). Latch: ``_tr_last_week`` = the week's Monday.
* Daily stop -- on the first tick of every session, peaks update from the
  latest PRIOR-session close of each held name and a name whose close is at or
  below ``(1 - stop_pct)`` x peak is exited. It stays out until the next weekly
  decision (a name stopped on the decision tick itself sits that week out).
* Every price the rules read comes from bars of sessions strictly before the
  decision session (``strategy_x.pit_daily_observations``, then a session cut).

A missing ledger week (no row, a ``failed`` row, or an older week's row) holds
the book: no new buys, no liquidation; the daily stop still runs.

Spec: docs/superpowers/specs/2026-11-05-deep-research-trend-picker-design.md §7.3
"""
import math
import os
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from db import store  # noqa: E402  (tests monkeypatch this name)
from strategy_x import _as_utc, pit_daily_observations, targets_to_orders  # noqa: E402
from swing_trader import clock  # noqa: E402
from trend_research import book, ledger  # noqa: E402

# Route through intellistock_logger: the backtest engine discards container
# stdout on success, and BacktestResults.logs is the log an operator reads.
try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="StrategyTrendResearch")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[StrategyTrendResearch] {msg}")


DEFAULTS = {
    "strategy_trend_research_enabled": False,
    "tr_chain_id": "",
    "tr_model": "",
    "tr_prompt_version": "tr-v1",
    "research_model_cutoff": "",
    **book.DEFAULTS,
    "stop_pct": 0.20,
    "max_llm_calls_per_build": 2000,
    "honour_single_position_cap": True,
    "broker_max_single_position_pct": 0.25,
}

#: Every exit is stamped `etf_sell`, as EB does: broker.py's Z2.1 check reads
#: `action_intent` off the strategy summary and flags a sell without one.
_SELL_INTENT = "etf_sell"
BIL = book.BIL

#: Cache keys (all `_tr_`-prefixed).
_LAST_WEEK_KEY = "_tr_last_week"        # Monday (ISO) of the week last decided or held
_OWNED_KEY = "_tr_owned"                # every symbol this lane ever targeted (set)
_PEAKS_KEY = "_tr_peaks"                # {symbol: highest prior-session close since purchase}
_STOPPED_KEY = "_tr_stopped"            # names stopped since the last decision (set)
_STOP_DAY_KEY = "_tr_stop_day"          # {symbol: session it was stopped in}
_TARGETS_KEY = "_tr_targets"            # the standing target book {symbol: weight}
_BOOK_WEEK_KEY = "_tr_book_week"        # Monday of the week whose ledger row built it
_SESSION_KEY = "_tr_last_session"       # session the daily stop last ran in
_LAST_DECISION_KEY = "_tr_last_decision"
_LOGGED_KEY = "_tr_logged"

#: The decision clock: the regular-session open, ET.
_DECISION_ET = dtime(9, 30)
#: A name "has bars at the decision date" when its latest prior-session close
#: is at most this many calendar days old (a delisted or renamed ticker's
#: bars stop; a long weekend plus a holiday is 4 days).
_STALE_DAYS = 7
#: Idle cash below this share of NAV is not swept into the book.
_SWEEP_MIN_PCT = 0.01
#: Bars handed to `pit_daily_observations` per read: the visible tail only, so
#: the per-tick cost does not grow with the run (26 bars a session at 900 s).
_TAIL_BARS = 400
_MAX_REASON_LINES = 40

#: The regular session's close, ET: no order is placed outside 09:30-16:00.
_CLOSE_ET = dtime(16, 0)
#: The disabled-lane notice, once per process per instance.
_DISABLED_LOGGED: set = set()
_ET = clock.NY


def _log_once(cache, reason, scope, msg, color="white"):
    """Log a standing condition at most once per `scope`."""
    seen = cache.get(_LOGGED_KEY)
    if not isinstance(seen, dict):
        seen = {}
        cache[_LOGGED_KEY] = seen
    if seen.get(reason) == scope:
        return
    seen[reason] = scope
    _log(msg, color)


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _num(value, default):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _price(value):
    """A finite positive float, or None."""
    p = _num(value, None)
    return p if p is not None and p > 0 else None


def _sym_set(value) -> set:
    """A cache value read back as a set of symbols (the engine's cache
    persistence round-trips sets; a list or None must not break the lane)."""
    if isinstance(value, (set, frozenset, list, tuple)):
        return {str(s).strip().upper() for s in value if str(s).strip()}
    return set()


def _bars_for(data, symbol):
    """The engine hands bars as either {sym: {"bars": [...]}} or {sym: [...]}."""
    if not isinstance(data, dict):
        return []
    entry = data.get(symbol)
    if isinstance(entry, dict):
        return entry.get("bars") or []
    return entry or []


def _bar_ts(bar):
    if not isinstance(bar, dict):
        return None
    return _as_utc(bar.get("t") or bar.get("timestamp") or bar.get("date"))


def _visible(bars, current_time):
    """The last `_TAIL_BARS` bars stamped at or before `current_time` -- a
    superset of what `pit_daily_observations` admits, so cutting first
    changes nothing but the cost. Nothing is memoised: the engine hands a
    fresh, causal list per symbol per tick
    (backtest_price_history.get_price_history_up_to_current), so the common
    case is O(1) -- the last bar is already at or before now -- and a list
    that runs past now (a test, or a full-window dict) is bisected with
    O(log n) timestamp parses. A list whose stamps do not parse, or are out
    of order, is handed over whole and `pit_daily_observations` cuts it."""
    if not bars:
        return []
    cutoff = current_time.timestamp()
    last = _bar_ts(bars[-1])
    if last is not None and last.timestamp() <= cutoff:
        return bars[-_TAIL_BARS:]
    lo, hi = 0, len(bars)
    while lo < hi:
        mid = (lo + hi) // 2
        ts = _bar_ts(bars[mid])
        if ts is None:
            return bars
        if ts.timestamp() <= cutoff:
            lo = mid + 1
        else:
            hi = mid
    tail = bars[max(0, lo - _TAIL_BARS):lo]
    stamps = [_bar_ts(b) for b in tail]
    if any(s is None for s in stamps) or any(b < a for a, b in zip(stamps, stamps[1:])):
        return bars
    return tail


def _prior_closes(data, symbol, session, current_time) -> list:
    """(session_id, close) of sessions strictly before `session`, oldest first."""
    obs = pit_daily_observations(_visible(_bars_for(data, symbol), current_time),
                                 current_time)
    return [(d, c) for d, c in obs if d < session]


def _last_close(data, symbol, current_time):
    """The last visible bar close at `current_time`: a mark, the same number a
    quote would carry on this bar (EB's fallback for unlisted legs)."""
    obs = pit_daily_observations(_visible(_bars_for(data, symbol), current_time),
                                 current_time)
    return _price(obs[-1][1]) if obs else None


def week_monday(session: str) -> str:
    return clock.week_monday(date.fromisoformat(session)).isoformat()


# -- broker wiring: the symbols this lane needs bars for -----------------------


def trend_research_universe(cfg, *, start=None, end=None, store_=None) -> list:
    """Every symbol the lane can trade in a backtest of `start`..`end`: the
    fixed ETF set plus every pick and recommended name of the configured
    chain's ``ok`` ledger rows whose week falls in the window (all weeks when
    no window is given). Rows are read once. A store failure returns the fixed
    set and logs: the fetch must not fail because the ledger is unreadable."""
    out = sorted(book.ALL_ALLOWED_ETFS)
    cfg = cfg or {}
    chain = str(cfg.get("tr_chain_id") or "").strip()
    model = str(cfg.get("tr_model") or "").strip()
    pv = str(cfg.get("tr_prompt_version") or DEFAULTS["tr_prompt_version"]).strip()
    if not chain or not model or "|" in chain:
        return out
    lo = chain + "|"
    hi = chain + "|~"                                # '~' sorts above every digit
    s_day, e_day = _as_date(start), _as_date(end)
    if s_day is not None:
        lo = chain + "|" + clock.week_monday(s_day).isoformat()
    if e_day is not None:
        hi = chain + "|" + (e_day + timedelta(days=1)).isoformat()
    st = store_ if store_ is not None else store
    names = set()
    try:
        for row in st.iter(st.between(ledger.TABLE, lo, hi)):
            if (row.get("chain_id") != chain or row.get("model") != model
                    or row.get("prompt_version") != pv or row.get("status") != "ok"):
                continue
            picks = ((row.get("book") or {}).get("picks") or [])
            for item in list(picks) + list(row.get("recommended") or []):
                sym = book.normalize_symbol((item or {}).get("symbol")
                                            if isinstance(item, dict) else None)
                if sym and book.is_allowed_symbol(sym, is_etf=book.is_etf_symbol(sym)):
                    names.add(sym)
    except Exception as exc:
        _log(f"StrategyTrendResearch | ledger unreadable for the bar fetch "
             f"({type(exc).__name__}: {exc}); fetching the fixed ETF set only", "yellow")
        return out
    return out + sorted(names - set(out))


def _as_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# -- payload -------------------------------------------------------------------


def _emit(decisions, sizes, universe) -> dict:
    """The broker payload, EB's shape. Nothing to do is {}."""
    if not decisions:
        return {}
    out = dict(decisions)
    sizes = dict(sizes)
    # The broker's buy gate otherwise reserves `_cash_reserve_floor_pct`
    # (default 0.10) of starting value as untouchable cash; this book's cash
    # is BIL, which the target weights already size.
    sizes["_cash_reserve_floor_pct"] = 0.0
    out["_nexus_position_sizes"] = sizes
    out["_nexus_discovered"] = sorted(universe)
    out["_nexus_executable_buys"] = sorted(s for s, d in decisions.items() if d == 1)
    out["_nexus_sell_enforcement"] = sorted(s for s, d in decisions.items() if d == -1)
    out["_nexus_action_intents"] = {s: _SELL_INTENT
                                    for s, d in sorted(decisions.items()) if d == -1}
    return out


def _spendable(emulator, prices) -> float:
    """Buying power when the emulator offers it, else settled cash (EB)."""
    try:
        bp = emulator.get_buying_power(prices=prices)
    except (AttributeError, TypeError):
        bp = None
    if bp is None:
        return float(emulator.get_cash() or 0.0)
    return float(bp or 0.0)


def _pending(emulator) -> set:
    try:
        return {str(s).upper() for s in (emulator.pending_execution_symbols() or ())}
    except Exception:
        return set()


class StrategyTrendResearch:
    # The class name is NOT free: broker.py resolves a run-once strategy by
    # CamelCasing its id -- strategy_trend_research -> StrategyTrendResearch --
    # and runs the whole backtest inert when it misses.

    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = {**DEFAULTS, **(config or {})}
        if not _truthy(cfg.get("strategy_trend_research_enabled", False)):
            who = str(cfg.get("instance_id") or cfg.get("_telemetry_backtest_id") or "")
            if who not in _DISABLED_LOGGED:
                _DISABLED_LOGGED.add(who)
                _log("StrategyTrendResearch | lane DISABLED (strategy_trend_research_enabled "
                     "is off) -- inert: no decisions, no stops.", "yellow")
            return {}
        cache = strategy_cache if isinstance(strategy_cache, dict) else {}
        now = _as_utc(current_time)
        if now is None:
            _log_once(cache, "no-clock", "run",
                      f"StrategyTrendResearch | REFUSING to trade -- unreadable time "
                      f"{current_time!r}.", "red")
            return {}
        session = clock.ny_date(now)
        if data is None or _truthy(cfg.get("_nexus_is_live_mode", False)):
            _log_once(cache, "live", session,
                      "StrategyTrendResearch | REFUSING to trade -- this lane is "
                      "backtest-only (no bars were handed in, or the tick is live).", "red")
            return {}
        if portfolio_emulator is None:
            _log_once(cache, "no-emulator", session,
                      "StrategyTrendResearch | REFUSING to trade -- no portfolio emulator, "
                      "so the book cannot be read.", "red")
            return {}
        # The engine's legacy window ticks on NYSE holidays too; a holiday tick
        # carries the previous session's prices and must decide nothing.
        if not clock.is_trading_day(date.fromisoformat(session)):
            return {}
        # Orders, the stop check and the weekly decision run only inside the
        # regular session: a pre-open or after-hours tick places nothing.
        ny_time = now.astimezone(_ET).time()
        if ny_time < _DECISION_ET or ny_time >= _CLOSE_ET:
            return {}
        try:
            return self._tick(prices, now, session, cfg, data, portfolio_emulator, cache)
        except Exception as exc:
            _log_once(cache, "tick-error", session,
                      f"StrategyTrendResearch {session} | REFUSING to trade this tick "
                      f"({type(exc).__name__}: {exc}).", "red")
            return {}

    # -- one tick ------------------------------------------------------------

    def _tick(self, prices, now, session, cfg, data, emu, cache):
        owned = _sym_set(cache.get(_OWNED_KEY))
        targets = cache.get(_TARGETS_KEY)
        targets = ({str(s).upper(): float(w) for s, w in targets.items()}
                   if isinstance(targets, dict) else None)
        try:
            positions = {str(s).strip().upper(): float(q or 0.0)
                         for s, q in (emu.get_positions() or {}).items()}
        except Exception as exc:
            _log_once(cache, "positions", session,
                      f"StrategyTrendResearch {session} | REFUSING to trade -- positions "
                      f"unreadable ({type(exc).__name__}: {exc}).", "red")
            return {}
        held = sorted(s for s, q in positions.items() if q > 0 and s in owned)

        # Marks: the broker's prices first, then the last visible bar close.
        eff = {}
        for s, v in (prices or {}).items():
            p = _price(v)
            if p is not None:
                eff[str(s).strip().upper()] = p
        for s in sorted(set(held) | set(targets or {}) | {BIL}):
            if s not in eff:
                p = _last_close(data, s, now)
                if p is not None:
                    eff[s] = p
        try:
            nav = float(emu.get_portfolio_value(eff) or 0.0)
        except Exception:
            nav = float("nan")
        if not math.isfinite(nav) or nav <= 0:
            _log_once(cache, "nav", session,
                      f"StrategyTrendResearch {session} | REFUSING to trade -- NAV reads "
                      f"{nav!r}. An unreadable account is not an empty one.", "red")
            return {}

        stopped_now = []
        if cache.get(_SESSION_KEY) != session:
            stopped_now = self._daily_stop(cache, cfg, data, now, session, held)
            cache[_SESSION_KEY] = session
            if stopped_now and targets is not None:
                targets = self._drop_to_bil(targets, stopped_now)
                cache[_TARGETS_KEY] = dict(targets)

        monday = week_monday(session)
        if cache.get(_LAST_WEEK_KEY) != monday:
            out = self._weekly(cache, cfg, data, emu, now, session, monday, positions,
                               held, eff, nav, owned)
            if out is not None:
                return out
            targets = cache.get(_TARGETS_KEY)
            targets = dict(targets) if isinstance(targets, dict) else None

        if targets is None:
            orphans = sorted(s for s, q in positions.items() if q > 0)
            if orphans:
                _log_once(cache, "no-targets", "run",
                          f"StrategyTrendResearch {session} | no standing target book while "
                          f"{', '.join(orphans)} are held -- nothing is bought or sold until "
                          "a ledger week is applied", "yellow")
            return {}
        return self._maintain(cache, cfg, emu, session, monday, positions, held, eff, nav,
                              owned, targets)

    # -- the daily trailing stop ---------------------------------------------

    def _daily_stop(self, cache, cfg, data, now, session, held) -> list:
        closes = {}
        for s in held:
            if s == BIL:
                continue
            obs = _prior_closes(data, s, session, now)
            if obs:
                closes[s] = obs[-1][1]
        peaks = cache.get(_PEAKS_KEY)
        peaks = dict(peaks) if isinstance(peaks, dict) else {}
        peaks = book.update_peaks(peaks, closes, [s for s in held if s != BIL])
        cache[_PEAKS_KEY] = peaks
        stop_pct = _num(cfg.get("stop_pct"), DEFAULTS["stop_pct"])
        if not 0.0 < stop_pct < 1.0:
            _log_once(cache, "stop-pct", "run",
                      f"StrategyTrendResearch | stop_pct {cfg.get('stop_pct')!r} is outside "
                      f"(0, 1); using {DEFAULTS['stop_pct']}", "yellow")
            stop_pct = DEFAULTS["stop_pct"]
        hits = book.trailing_stop_hits(peaks, closes, stop_pct=stop_pct)
        if hits:
            stopped = _sym_set(cache.get(_STOPPED_KEY)) | set(hits)
            cache[_STOPPED_KEY] = stopped
            day = dict(cache.get(_STOP_DAY_KEY) or {})
            for s in hits:
                day[s] = session
            cache[_STOP_DAY_KEY] = day
            _log(f"StrategyTrendResearch {session} | STOP "
                 + ", ".join(f"{s} close {closes[s]:.2f} <= {1 - stop_pct:.0%} of peak "
                             f"{peaks[s]:.2f}" for s in hits)
                 + " -- to BIL until the next weekly decision", "yellow")
        return hits

    @staticmethod
    def _let_winners_run(weights, held, cfg, reasons) -> dict:
        """No profit-taking (spec 5.7, C4): a kept holding's target is
        ``min(max(book target, current weight), max_single_weight)``, so a
        winner is trimmed only above the single-name cap. Leveraged plus
        inverse names still fit ``max_levered_inverse_weight`` together (C3).
        The room the winners take comes out of BIL first, then out of the
        part of other names' targets that would be NEW money (never below
        what those names already hold), so nothing else is trimmed to pay
        for a winner."""
        cap = _num(cfg.get("max_single_weight"), book.DEFAULTS["max_single_weight"])
        lev_cap = _num(cfg.get("max_levered_inverse_weight"),
                       book.DEFAULTS["max_levered_inverse_weight"])
        out = dict(weights)
        raised = {}
        for s in sorted(out):
            if s == BIL or s not in held:
                continue
            new = min(max(out[s], held[s]), cap)
            if new > out[s] + 1e-12:
                raised[s] = (out[s], new)
                out[s] = new
        if not raised:
            return weights
        lev = [s for s in out if s in book.LEVERAGED_ETFS or s in book.INVERSE_ETFS]
        lev_total = sum(out[s] for s in lev)
        if lev_total > lev_cap + 1e-12:
            k = lev_cap / lev_total
            for s in lev:
                out[s] *= k
        names = [s for s in out if s != BIL]
        if sum(out[s] for s in names) > 1.0 + 1e-12:
            fixed = sum(out[s] for s in names if s in raised)
            others = [s for s in names if s not in raised]
            base = {s: min(out[s], held.get(s, 0.0)) for s in others}
            extra = {s: out[s] - base[s] for s in others}
            room = 1.0 - fixed - sum(base.values())
            total = sum(extra.values())
            k = max(0.0, min(1.0, room / total)) if total > 0 else 0.0
            for s in others:
                out[s] = base[s] + extra[s] * k
        out = {s: w for s, w in out.items() if s == BIL or w > 0}
        out[BIL] = max(0.0, 1.0 - sum(w for s, w in out.items() if s != BIL))
        reasons.append("let winners run (no profit-taking): " + ", ".join(
            "%s %.1f%% -> %.1f%%" % (s, 100 * a, 100 * out.get(s, 0.0))
            for s, (a, _b) in sorted(raised.items())))
        return out

    @staticmethod
    def _drop_to_bil(targets, names) -> dict:
        out = dict(targets)
        for s in names:
            w = out.pop(s, 0.0)
            if s != BIL and w > 0:
                out[BIL] = out.get(BIL, 0.0) + w
        return out

    # -- the weekly decision ---------------------------------------------------

    def _weekly(self, cache, cfg, data, emu, now, session, monday, positions, held, eff,
                nav, owned):
        """Apply this week's ledger row. Returns the payload, or None when the
        week is held (the caller then maintains the standing book)."""
        chain = str(cfg.get("tr_chain_id") or "").strip()
        model = str(cfg.get("tr_model") or "").strip()
        pv = str(cfg.get("tr_prompt_version") or DEFAULTS["tr_prompt_version"]).strip()
        if not chain or not model:
            cache[_LAST_WEEK_KEY] = monday
            _log_once(cache, "no-selector", "run",
                      f"StrategyTrendResearch {session} | tr_chain_id / tr_model not set -- "
                      "no ledger to replay; holding the book", "red")
            return None
        try:
            row = ledger.latest_row(chain, model, pv, now, store=store)
        except Exception as exc:
            # Not latched: a read error is retried on the next tick.
            _log_once(cache, "ledger-error", monday,
                      f"StrategyTrendResearch {session} | ledger unreadable "
                      f"({type(exc).__name__}: {exc}) -- holding the book; retrying "
                      "next tick", "red")
            return None
        if not isinstance(row, dict) or str(row.get("week_start")) != monday:
            cache[_LAST_WEEK_KEY] = monday
            seen = row.get("week_start") if isinstance(row, dict) else None
            _log_once(cache, "ledger-missing", monday,
                      f"StrategyTrendResearch {session} | no ok ledger row for week {monday} "
                      f"(chain {chain!r}, model {model!r}, {pv}; newest ok row: "
                      f"{seen or 'none'}) -- holding the book: no buys, no liquidation; "
                      "the daily stop still runs", "yellow")
            return None

        stop_day = cache.get(_STOP_DAY_KEY) or {}
        blocked = sorted(s for s, d in stop_day.items() if d == session)
        held_for_book = {s: (positions[s] * eff.get(s, 0.0)) / nav
                         for s in held if s not in blocked}

        def has_bars(sym):
            obs = _prior_closes(data, sym, session, now)
            if not obs:
                return False
            last = date.fromisoformat(obs[-1][0])
            return (date.fromisoformat(session) - last).days <= _STALE_DAYS

        weights, reasons = book.target_book(row, held=held_for_book, has_bars=has_bars,
                                            cfg=cfg)
        weights = self._let_winners_run(weights, held_for_book, cfg, reasons)
        if blocked:
            weights = self._drop_to_bil(weights, blocked)
            reasons.append("blocked until next week (stopped today): " + ", ".join(blocked))
        cache[_STOPPED_KEY] = set(blocked)
        cache[_STOP_DAY_KEY] = {s: d for s, d in stop_day.items() if d == session}
        owned = owned | set(weights) | set(held)
        cache[_OWNED_KEY] = owned
        cache[_TARGETS_KEY] = dict(weights)
        cache[_BOOK_WEEK_KEY] = monday
        cache[_LAST_WEEK_KEY] = monday

        cash = max(0.0, _spendable(emu, eff))
        decisions, sizes = targets_to_orders(weights, nav=nav, positions=positions, prices=eff,
                                             cash=cash, config=cfg, owned=owned)
        unpriced = sorted(s for s in held if s not in weights and s not in eff)
        cutoff = str(cfg.get("research_model_cutoff") or "").strip()
        period = ("" if not cutoff else
                  " | optimistic (before model cutoff)" if monday < cutoff else " | honest")
        cache[_LAST_DECISION_KEY] = {"session": session, "week": monday, "row": row.get("id"),
                                     "targets": dict(weights), "orders": len(decisions)}
        _log(f"StrategyTrendResearch {session} | WEEK {monday} row {row.get('id')}{period} | "
             "targets=" + ", ".join(f"{s} {w:.1%}" for s, w in
                                    sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])))
             + f" | orders={len(decisions)} | nav=${nav:,.0f}", "cyan")
        if reasons:
            shown = reasons[:_MAX_REASON_LINES]
            more = len(reasons) - len(shown)
            _log(f"StrategyTrendResearch {session} | why: " + "; ".join(shown)
                 + (f"; +{more} more" if more > 0 else ""), "white")
        if unpriced:
            _log(f"StrategyTrendResearch {session} | exit of {', '.join(unpriced)} waits: "
                 "no price this tick; retried next tick", "yellow")
        return _emit(decisions, sizes, owned | set(weights))

    # -- between decisions: finish exits, deploy idle cash -----------------------

    def _maintain(self, cache, cfg, emu, session, monday, positions, held, eff, nav, owned,
                  targets):
        pending = _pending(emu)
        decisions, sizes = {}, {}
        # Exits the book already decided (a name the decision dropped whose
        # sell had no price, or a stop): sold in full, retried each tick.
        waiting = []
        for s in held:
            if targets.get(s, 0.0) > 0 or s in pending:
                continue
            if _price(eff.get(s)) is None:
                waiting.append(s)
                continue
            decisions[s] = -1
            sizes[s] = {"sell_fraction": 1.0}
        if waiting:
            _log_once(cache, "exit-unpriced", session + ":" + ",".join(waiting),
                      f"StrategyTrendResearch {session} | exit of {', '.join(waiting)} waits: "
                      "no price this tick; retried next tick", "yellow")
        if decisions:
            _log(f"StrategyTrendResearch {session} | EXIT {', '.join(sorted(decisions))}",
                 "cyan")
            return _emit(decisions, sizes, owned | set(targets))

        # Idle cash (a stop's or a rotation's settled proceeds) goes back into
        # the book, buys only. A held week buys nothing but BIL.
        if pending & (owned | set(targets)):
            return {}
        cash = max(0.0, _spendable(emu, eff))
        if cash / nav <= _SWEEP_MIN_PCT:
            return {}
        stopped = _sym_set(cache.get(_STOPPED_KEY))
        if cache.get(_BOOK_WEEK_KEY) == monday:
            want = {s: w for s, w in targets.items() if s not in stopped}
        else:
            want = {BIL: targets.get(BIL, 0.0)}
        decisions, sizes = targets_to_orders(want, nav=nav, positions=positions, prices=eff,
                                             cash=cash, config=cfg, owned=owned)
        decisions = {s: d for s, d in decisions.items() if d == 1}
        sizes = {s: v for s, v in sizes.items() if s in decisions}
        if not decisions:
            return {}
        _log(f"StrategyTrendResearch {session} | SWEEP ${cash:,.0f} idle "
             f"({cash / nav:.0%} of NAV) into {', '.join(sorted(decisions))}", "cyan")
        return _emit(decisions, sizes, owned | set(targets))
