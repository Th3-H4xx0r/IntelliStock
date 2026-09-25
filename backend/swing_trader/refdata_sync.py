"""The swing lane's own reference data: fetch and store what is missing.

StrategySwing calls this on its first run in any mode (the engine is not
involved): synchronously on a backtest's first session, for the run's window,
and in a daemon thread at most once per process per NY day in live
(start_background_sync), so the 09:15 ET scan never waits on the network.

sync_reference_data(start, end) makes sure the three reference tables cover
[start, end], then returns the lane's point-in-time universe for the window:

  (a) VIX. When SwingMacroDaily does not cover [start - 30 days,
      min(end, yesterday)] with no gap longer than refdata.VIX_MAX_STALE_DAYS
      (the reader's own staleness bound), Cboe, falling back to FRED.
  (b) Membership. When the newest SwingIndexMembership row is more than 45
      days older than min(end, today), or no row precedes `start`, the newest
      fja05680/sp500 historical-components CSV.
  (c) Symbols. Every member visible on some session in [start, end] (the
      list in effect at `start`, plus every change dated before `end`: the
      rule of scripts/swing_lab_setup.py lab_watchlist), plus SPY, QQQ and the
      lane's defensive universe.
  (d) Sectors, for those symbols: ST's overrides, then Wikipedia's GICS
      sector, then yfinance, inside a time budget. A symbol no source knows
      gets a retry marker (sector "unknown", source "yfinance", retried_at);
      a marker, or any stored empty or "unknown" yfinance sector, is looked
      up again at most once per SECTOR_RETRY_DAYS. refdata.sector_map skips
      such rows, so the lane reads the symbol as absent (unknown in a
      backtest, the live yfinance fallback in live), exactly as before.

Only rows whose id is not stored are written (the store's insert is a
SAVEPOINT per row, so a full reload over Tailscale takes ~20 minutes), with
deterministic ids and conflict="replace": two runs starting together write
the same rows, and a concurrent full build by the CLI is harmless. With
every row present (and every retry marker resting) a run costs six reads and
no request.

Every step is best-effort: a failure is logged yellow with its reason and
the run continues on what is stored. Nothing here raises into the lane; the
lane's own refusals ("no SwingIndexMembership row", VIX unavailable) stay the
visible failure.
"""
from __future__ import annotations

import functools
import threading
import time
from datetime import date, datetime, timedelta

from db import store as _db_store

from swing_trader import refdata, refdata_build, sectors
from swing_trader.constants import DEFENSIVE_UNIVERSE
from swing_trader.universe import norm_symbol

try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="SwingRefdataSync")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[SwingRefdataSync] {msg}", flush=True)

#: Seconds the per-symbol yfinance lookups may spend in one run.
SECTOR_BUDGET_S = 300.0
#: A failed yfinance sector is not asked again for this many days.
SECTOR_RETRY_DAYS = 7
SECTOR_PROGRESS_EVERY = 50
#: VIX coverage starts this many days before the window.
VIX_LEAD_DAYS = 30
#: Membership older than this, counted back from min(end, today), is refreshed.
MEMBERSHIP_MAX_AGE_DAYS = 45
INDEX = "SPX"
BENCHMARKS = ("SPY", "QQQ")
_TAG = "[swing-refdata]"
#: Fewer, shorter attempts than the CLI's: a run waits on these.
_default_fetch = functools.partial(refdata_build.fetch_text, attempts=2, timeout=30)


def _day(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _ids_between(st, table, prefix, lo, hi) -> set:
    """Stored ids `prefix|lo` .. `prefix|hi`, both ends inclusive."""
    upper = f"{prefix}|{(hi + timedelta(days=1)).isoformat()}"
    rows = st.run(st.between(table, f"{prefix}|{lo.isoformat()}", upper))
    return {str(r.get("id")) for r in rows}


def _covered(days, lo, hi, max_gap) -> bool:
    """True when sorted `days` reach within `max_gap` days of both ends and
    never leave a longer gap between them."""
    if not days:
        return False
    points = [lo] + sorted(days) + [hi]
    return all((b - a).days <= max_gap for a, b in zip(points, points[1:]))


def _sync_vix(st, fetch, start, end, today, log) -> int:
    lo = start - timedelta(days=VIX_LEAD_DAYS)
    hi = min(end, today - timedelta(days=1))
    if hi < lo:
        return 0
    stored = _ids_between(st, refdata.MACRO_TABLE, "VIX", lo, hi)
    days = [date.fromisoformat(i.split("|", 1)[1]) for i in stored]
    if _covered(days, lo, hi, refdata.VIX_MAX_STALE_DAYS):
        return 0
    rows = refdata_build.load_vix(fetch, lo.isoformat(),
                                  log=lambda m: log(f"{_TAG} {m}", "yellow"))
    new = [r for r in rows if r["date"] <= hi.isoformat() and r["id"] not in stored]
    if new:
        st.insert(refdata.MACRO_TABLE, new, conflict="replace")
    return len(new)


def _newest_membership(st):
    rows = st.run(st.limit(st.order_by(
        st.between(refdata.MEMBERSHIP_TABLE, f"{INDEX}|", f"{INDEX}}}"),
        index="id", desc=True), 1))
    return _day(str(rows[0].get("date") or rows[0]["id"].split("|", 1)[1])) if rows else None


def _sync_membership(st, fetch, start, end, today, log) -> int:
    newest = _newest_membership(st)
    horizon = min(end, today) - timedelta(days=MEMBERSHIP_MAX_AGE_DAYS)
    if (newest is not None and newest >= horizon
            and refdata.members_before(st, start, INDEX) is not None):
        return 0
    url = refdata_build.membership_csv_url(fetch, log=lambda m: log(f"{_TAG} {m}", "yellow"))
    rows = refdata_build.membership_rows(
        refdata_build.parse_membership(fetch(url)), start.isoformat())
    if not rows:
        raise refdata_build.RefdataUnavailable(f"no dated rows in {url}")
    stored = _ids_between(st, refdata.MEMBERSHIP_TABLE, INDEX,
                          _day(rows[0]["date"]), _day(rows[-1]["date"]))
    new = [r for r in rows if r["id"] not in stored]
    if new:
        st.insert(refdata.MEMBERSHIP_TABLE, new, conflict="replace")
    return len(new)


def _extras(defensive_universe) -> set:
    raw = DEFENSIVE_UNIVERSE if defensive_universe is None else defensive_universe
    if isinstance(raw, str):
        raw = raw.split(",")
    return set(BENCHMARKS) | {norm_symbol(s) for s in raw if norm_symbol(s)}


def window_symbols(st, start, end, defensive_universe=None) -> list:
    """Members visible on some session in [start, end] (lab_watchlist's rule),
    plus SPY, QQQ and the defensive universe."""
    first = refdata.members_before(st, start, INDEX) or []
    later = st.run(st.between(refdata.MEMBERSHIP_TABLE, f"{INDEX}|{start.isoformat()}",
                              f"{INDEX}|{end.isoformat()}"))
    union = {norm_symbol(s) for s in first}
    union |= {norm_symbol(s) for r in later for s in (r.get("members") or [])}
    return sorted(s for s in union | _extras(defensive_universe) if s)


def _retry_due(row, today) -> bool:
    """A stored row that records only a failed yfinance lookup, last tried at
    least SECTOR_RETRY_DAYS ago (retried_at, else as_of)."""
    if str(row.get("source") or "") != "yfinance":
        return False
    if str(row.get("sector") or "").strip().lower() not in ("", "unknown"):
        return False
    try:
        tried = _day(row.get("retried_at") or row.get("as_of"))
    except (TypeError, ValueError):
        return True
    return (today - tried).days >= SECTOR_RETRY_DAYS


def _no_lookup(symbol):
    return None


def _sync_sectors(st, fetch, symbols, sector_of, today, budget_s, clock, log) -> int:
    stored = {str(r.get("id")): r for r in st.get_all(refdata.SECTOR_TABLE, *symbols)}
    overrides, lookups = [], []
    for sym in symbols:
        row = stored.get(sym)
        if sym in sectors._SECTOR_OVERRIDES:
            # ST's overrides always win, over any stored row.
            if (row is None or row.get("source") != "override"
                    or row.get("sector") != sectors._SECTOR_OVERRIDES[sym]):
                overrides.append(sym)
        elif row is None:
            lookups.append(sym)
    # Never-seen symbols first, then the resting failures that came due.
    lookups += [s for s in symbols if s in stored and s not in sectors._SECTOR_OVERRIDES
                and _retry_due(stored[s], today)]
    as_of = today.isoformat()
    rows = [refdata_build.sector_row(s, sector_of=_no_lookup, as_of=as_of) for s in overrides]
    added, markers = len(rows), []
    if lookups:
        wikipedia = refdata_build.wikipedia_sectors(
            fetch, log=lambda m: log(f"{_TAG} {m}", "yellow"))
        yahoo = [s for s in lookups if s not in wikipedia]
        deadline, asked, left = clock() + float(budget_s), 0, 0
        for sym in lookups:
            if sym not in wikipedia:
                # The budget bounds the per-symbol yfinance calls; a symbol it
                # does not reach stays as it is for the next run.
                if clock() >= deadline:
                    left += 1
                    continue
                asked += 1
            row = refdata_build.sector_row(sym, sector_of=sector_of, as_of=as_of,
                                           wikipedia=wikipedia)
            if sym not in wikipedia and asked % SECTOR_PROGRESS_EVERY == 0:
                log(f"{_TAG} sector lookups {asked}/{len(yahoo)}", "cyan")
            if row is None:
                markers.append({"id": sym, "symbol": sym, "sector": "unknown",
                                "as_of": as_of, "source": "yfinance", "retried_at": as_of})
            else:
                rows.append(row)
                added += 1
        if left:
            log(f"{_TAG} sector budget of {float(budget_s):.0f}s spent: {left} symbol(s) "
                "left for the next run", "yellow")
    if rows or markers:
        st.insert(refdata.SECTOR_TABLE, rows + markers, conflict="replace")
    return added


def sync_reference_data(start, end, *, defensive_universe=None, store=None, fetch=None,
                        sector_of=None, today=None, sector_budget_s=SECTOR_BUDGET_S,
                        clock=time.monotonic, log=None) -> list:
    """Fill what the reference tables lack for [start, end]; return the lane's
    universe for the window (see the module docstring). Never raises an
    Exception."""
    log = _log if log is None else log
    t0 = clock()
    counts = {"vix": 0, "membership": 0, "sectors": 0}
    symbols = sorted(_extras(defensive_universe))
    try:
        st = store if store is not None else _db_store
        start, end = _day(start), _day(end)
        today = _day(today) if today is not None else date.today()
        fetch = fetch or _default_fetch
        sector_of = sector_of or refdata_build.yf_sector
    except Exception as exc:
        log(f"{_TAG} skipped: {type(exc).__name__}: {exc}", "yellow")
        return symbols
    if st is _db_store:
        try:
            from swing_trader import signals_store
            signals_store.ensure_tables()
        except Exception as exc:
            log(f"{_TAG} tables unavailable: {type(exc).__name__}: {exc}", "yellow")
    log(f"{_TAG} reference sync start for {start}..{end}: VIX, membership, sectors",
        "cyan")
    for key, label, step in (
            ("vix", "VIX", lambda: _sync_vix(st, fetch, start, end, today, log)),
            ("membership", "membership",
             lambda: _sync_membership(st, fetch, start, end, today, log))):
        t_step = clock()
        try:
            counts[key] = step()
            log(f"{_TAG} {label} phase done: {counts[key]} new row(s) stored, "
                f"{clock() - t_step:.1f}s", "cyan")
        except Exception as exc:
            log(f"{_TAG} {key} not refreshed ({type(exc).__name__}: {exc}); the run "
                "continues on what is stored", "yellow")
    try:
        symbols = window_symbols(st, start, end, defensive_universe)
    except Exception as exc:
        log(f"{_TAG} membership unreadable ({type(exc).__name__}: {exc}); universe is "
            "SPY, QQQ and the defensive ETFs only", "yellow")
    t_step = clock()
    log(f"{_TAG} sectors phase start: {len(symbols)} symbol(s) in the window's universe",
        "cyan")
    try:
        counts["sectors"] = _sync_sectors(st, fetch, symbols, sector_of, today,
                                          sector_budget_s, clock, log)
        log(f"{_TAG} sectors phase done: {counts['sectors']} new sector row(s), "
            f"{clock() - t_step:.1f}s", "cyan")
    except Exception as exc:
        log(f"{_TAG} sectors not refreshed ({type(exc).__name__}: {exc})", "yellow")
    log(f"{_TAG} {start}..{end}: VIX rows added {counts['vix']}, membership rows added "
        f"{counts['membership']}, sectors added {counts['sectors']}, {len(symbols)} "
        f"symbols, {clock() - t0:.1f}s", "cyan")
    return symbols


_background_lock = threading.Lock()
_background_day = {"day": None}


def start_background_sync(ny_day, *, defensive_universe=None, sync=None,
                          thread_factory=threading.Thread, log=None) -> bool:
    """Live: run sync_reference_data(ny_day, ny_day) in a daemon thread, at
    most once per process per NY day. True when a thread was started."""
    log = _log if log is None else log
    day = _day(ny_day)
    with _background_lock:
        if _background_day["day"] == day:
            return False
        _background_day["day"] = day
    target = sync or sync_reference_data

    def run():
        t0 = time.monotonic()
        try:
            target(day, day, defensive_universe=defensive_universe)
        except Exception as exc:  # the sync never raises; a stub might
            log(f"{_TAG} live sync failed after {time.monotonic() - t0:.1f}s: "
                f"{type(exc).__name__}: {exc}; the scan runs on the stored rows", "yellow")
            return
        log(f"{_TAG} live sync for {day} finished in {time.monotonic() - t0:.1f}s", "cyan")

    thread = thread_factory(target=run, name=f"swing-refdata-{day}", daemon=True)
    thread.start()
    log(f"{_TAG} live sync for {day} started in a background thread (once a day; the "
        "scan never waits on it)", "cyan")
    return True
