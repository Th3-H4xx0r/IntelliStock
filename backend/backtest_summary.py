"""Truthful backtest P&L + a de-duplicated end-of-run price series.

Two pieces of the backtest wrap-up were separately re-deriving "the final
price", and they disagreed (incident 586767: the equity curve reported
+$437.54 while the row's P&L reported -$2,318.58 for the SAME run):

  1. The row P&L valued open positions with ``final_prices`` resolved by
     ``_get_prices_at_time(data, symbols, end_date)`` — a "latest bar
     at-or-before end_date" lookup. When ``data[sym]`` carried TWO bars for
     the end date with different closes, that resolver picked the later
     (systematically ~$2,756 lower across 11 positions) bar.
  2. The equity curve (daily snapshots) valued the same positions with the
     marks the simulation actually ran on.

The equity curve is the truthful number — it is what the simulation traded
against. ``compute_backtest_summary`` therefore derives ``final_value`` from
the LAST snapshot's own stored value, so
``pnl == snapshots[-1]["value"] - initial_cash`` holds by construction.

Separately, the DB/UI price series ``backtest_prices`` was seeded from BOTH
the raw bars AND the snapshot-derived prices, de-duplicated only on an exact
``(timestamp, symbol)`` key. A snapshot whose timestamp differed in
time-of-day from an existing same-date bar slipped past that key and produced
two rows for the same date with different closes — the visible duplicate.
``build_backtest_price_series`` de-dupes on ``(date, symbol)`` for the
snapshot seeding: it only fills a (date, symbol) the bars didn't already
cover, so at most one snapshot-derived close per date/symbol is emitted and a
bar always wins over a snapshot for the same date.

broker.py is not import-safe (module load runs argparse + the backtest main
path), so this computation lives here and is wired in with a thin call.
Time-conversion is injected because broker.py defines ``_bar_time_to_datetime``
locally (same pattern as backtest_bar_snapshot / backtest_price_history).
"""
from __future__ import annotations

from typing import Any, Callable, Iterable


def compute_backtest_summary(emulator, snapshots, initial_cash, benchmark_values=None) -> dict:
    """Final P&L derived from the equity curve's own end mark.

    ``final_value`` is the last portfolio snapshot's stored ``value`` (the
    mark the simulation ran on), so ``pnl == snapshots[-1]["value"] -
    initial_cash`` exactly. Falls back to valuing current positions at the
    emulator's last-known prices only when there are no snapshots at all.

    Returns ``{"final_value", "pnl", "pnl_percent"}`` (values may be None
    when they cannot be computed).
    """
    final_value = None
    if snapshots:
        last = snapshots[-1] or {}
        v = last.get("value")
        if v is not None:
            try:
                final_value = float(v)
            except (TypeError, ValueError):
                final_value = None
    if final_value is None and emulator is not None:
        # No usable snapshot value — value current positions at last-known
        # prices so a run that recorded no snapshots still reports a number.
        try:
            last_prices = getattr(emulator, "_last_prices", {}) or {}
            final_value = emulator.get_portfolio_value(last_prices)
        except Exception:
            final_value = None

    pnl = None
    pnl_percent = None
    if final_value is not None and initial_cash is not None:
        pnl = final_value - initial_cash
        if initial_cash:
            pnl_percent = (final_value - initial_cash) / initial_cash * 100.0

    # Crypto fee accounting (None for pure-equity runs, which are commission-free).
    fees = None
    if emulator is not None:
        try:
            fs = emulator.get_fee_summary()
            if fs and (fs.get("total_fees") or fs.get("total_volume")):
                fees = fs
        except Exception:
            fees = None
    summary = {"final_value": final_value, "pnl": pnl, "pnl_percent": pnl_percent, "fees": fees}

    # Task 9 (benchmark-alpha): MERGE benchmark-relative fields when a
    # benchmark value series is supplied. Existing P&L fields are never
    # replaced, and legacy callers without benchmark_values are byte-identical.
    if benchmark_values is not None and snapshots:
        try:
            import pandas as _pd

            from benchmark_alpha.metrics import compute_active_metrics
            port = [float((s or {}).get("value") or 0.0) for s in snapshots]
            bench = [float(v) for v in benchmark_values]
            n = min(len(port), len(bench))
            if n >= 2:
                aligned = _pd.DataFrame(
                    {"portfolio": port[:n], "benchmark": bench[:n]},
                    index=_pd.RangeIndex(n))
                m = compute_active_metrics(aligned)
                summary.update({
                    "benchmark_return": m.benchmark_return,
                    "active_return": m.active_return,
                    "beta": m.beta,
                    "tracking_error": m.tracking_error,
                    "information_ratio": m.information_ratio,
                    "max_drawdown_magnitude": m.max_drawdown_magnitude,
                    "bootstrap_active_low": m.bootstrap_active_low,
                    "bootstrap_active_high": m.bootstrap_active_high,
                })
        except Exception:
            # Benchmark enrichment is additive; failure never blocks base P&L.
            pass
    return summary


def resolve_end_prices(resolver_prices, snapshots) -> dict:
    """End-of-run marks for valuing open positions, on the equity curve's basis.

    The LAST snapshot's own prices win (they are the marks the simulation
    actually ran on — the same basis as ``compute_backtest_summary``'s
    final_value); the resolver-derived ``resolver_prices`` (latest-bar lookup /
    fetch) fill only symbols the last snapshot lacks. This keeps per-stock P&L
    and ``stock_price_change.end_price`` reconcilable with the headline pnl
    when the data carries duplicate end-date bars (incident 586767).

    Returns a NEW dict — never mutates either input (the resolver fallback may
    alias a snapshot's stored prices dict, which is history).
    """
    out = dict(resolver_prices or {})
    if snapshots:
        snap_prices = (snapshots[-1] or {}).get("prices") or {}
        if isinstance(snap_prices, dict):
            for sym, val in snap_prices.items():
                try:
                    f = float(val)
                except (TypeError, ValueError):
                    continue
                if f > 0:
                    out[sym] = f
    return out


def compute_per_stock_pnl(trades, positions, end_prices, all_traded, *, log=None) -> tuple:
    """Per-stock P&L: realized (sells - buys) + open positions marked at
    ``end_prices``. When ``end_prices`` comes from ``resolve_end_prices`` the
    sum over all traded symbols reconciles with the headline pnl
    (``compute_backtest_summary``) by construction, because both value open
    positions at the last snapshot's marks.

    Returns ``(pnl_per_stock, pnl_percent_per_stock)``.
    """
    pnl_per_stock: dict = {}
    pnl_percent_per_stock: dict = {}
    trades = trades or []
    for sym in (all_traded or []):
        buys = sum(t.get("total", 0) or 0 for t in trades
                   if t.get("ticker") == sym and t.get("action") == "buy")
        sells = sum(t.get("total", 0) or 0 for t in trades
                    if t.get("ticker") == sym and t.get("action") == "sell")
        pos_shares = (positions or {}).get(sym, 0.0) or 0.0
        end_price = (end_prices or {}).get(sym)
        pos_value = (pos_shares * float(end_price)) if end_price is not None else 0.0
        if pos_shares > 0 and end_price is None and log is not None:
            log("[Backtest] Per-stock P&L incomplete for %s (no end price; position valued at 0)" % sym, "yellow")
        pnl = sells - buys + pos_value
        pnl_per_stock[sym] = round(pnl, 4)
        if buys and float(buys) != 0:
            pnl_percent_per_stock[sym] = round((pnl / float(buys)) * 100.0, 4)
        else:
            pnl_percent_per_stock[sym] = None
    return pnl_per_stock, pnl_percent_per_stock


def compute_stock_price_change(all_traded, start_prices, end_prices) -> dict:
    """Start -> end price movement per symbol. ``end_prices`` should be the
    ``resolve_end_prices`` result so ``end_price`` shows the mark the sim ran
    on, not a phantom duplicate end-date bar."""
    stock_price_change: dict = {}
    for sym in (all_traded or []):
        sp = (start_prices or {}).get(sym)
        ep = (end_prices or {}).get(sym)
        if sp is not None and ep is not None and float(sp) != 0:
            pct = (float(ep) - float(sp)) / float(sp) * 100.0
            stock_price_change[sym] = {
                "start_price": round(float(sp), 4),
                "end_price": round(float(ep), 4),
                "change_percent": round(pct, 4),
            }
        elif sp is not None or ep is not None:
            stock_price_change[sym] = {
                "start_price": round(float(sp), 4) if sp is not None else None,
                "end_price": round(float(ep), 4) if ep is not None else None,
                "change_percent": None,
            }
    return stock_price_change


def build_backtest_price_series(
    data: dict,
    snapshots: Iterable,
    price_symbols: Iterable,
    start_date_only,
    end_date_only,
    *,
    bar_time_to_datetime: Callable[[Any], Any],
) -> list:
    """Build the ``backtest_prices`` series (list of {timestamp, symbol, close}).

    Raw bars from ``data`` are emitted first (intraday bars preserved as-is).
    Snapshot-derived prices then FILL ONLY the (date, symbol) pairs that the
    bars did not already cover, so no second same-date close is ever appended
    for a symbol that already has a bar (or an earlier snapshot) on that date.
    """
    price_symbols = list(price_symbols or [])
    rows_to_write: list = []
    # (date, symbol) pairs already represented by a real bar. Snapshot seeding
    # must not add a second close for any of these — that was the dup bug.
    seen_dates: set = set()

    for sym in price_symbols:
        for b in (data.get(sym) or []):
            t = b.get("t")
            c = b.get("c")
            if t is None or c is None:
                continue
            bt = bar_time_to_datetime(t)
            if bt is None:
                continue
            bar_date = bt.date()
            if start_date_only is not None and bar_date < start_date_only:
                continue
            if end_date_only is not None and bar_date > end_date_only:
                continue
            ts = t.isoformat() if hasattr(t, "isoformat") else str(t)
            rows_to_write.append((ts, sym, c))
            seen_dates.add((bar_date, sym))

    # Add snapshot-derived prices so non-watchlist traded symbols are
    # represented too — but only for (date, symbol) the bars didn't cover.
    for snap in (snapshots or []):
        ts_raw = snap.get("timestamp")
        bt = bar_time_to_datetime(ts_raw) if ts_raw is not None else None
        if bt is None:
            continue
        snap_date = bt.date()
        if start_date_only is not None and snap_date < start_date_only:
            continue
        if end_date_only is not None and snap_date > end_date_only:
            continue
        ts = ts_raw.isoformat() if hasattr(ts_raw, "isoformat") else str(ts_raw)
        sp = snap.get("prices") or {}
        if not isinstance(sp, dict):
            continue
        for sym in price_symbols:
            key_date = (snap_date, sym)
            if key_date in seen_dates:
                continue
            val = sp.get(sym)
            try:
                close_f = float(val)
            except (TypeError, ValueError):
                continue
            if close_f <= 0:
                continue
            seen_dates.add(key_date)
            rows_to_write.append((ts, sym, close_f))

    # Last-resort fallback: if nothing survived the date filter (e.g. the
    # dates couldn't be resolved), emit every well-formed bar unfiltered so
    # the UI at least has a series.
    if len(rows_to_write) == 0:
        for sym in price_symbols:
            for b in (data.get(sym) or []):
                t = b.get("t")
                c = b.get("c")
                if t is not None and c is not None:
                    bt = bar_time_to_datetime(t)
                    if bt is not None:
                        ts = t.isoformat() if hasattr(t, "isoformat") else str(t)
                        rows_to_write.append((ts, sym, c))

    rows_to_write.sort(key=lambda x: (x[0], x[1]))
    return [
        {"timestamp": ts, "symbol": sym, "close": float(c)}
        for (ts, sym, c) in rows_to_write
    ]
