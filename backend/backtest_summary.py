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

import hashlib
import json
import math
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable


class ExecutionProvenanceError(ValueError):
    """Raised when an equity backtest cannot be considered promotable."""


def _finite_nonnegative(value, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ExecutionProvenanceError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise ExecutionProvenanceError(
            f"{field} must be finite and nonnegative"
        )
    return number


def _aware_timestamp(value, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ExecutionProvenanceError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExecutionProvenanceError(
            f"{field} must be an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExecutionProvenanceError(f"{field} must be timezone-aware")
    return parsed


def assert_execution_provenance_promotable(summary) -> None:
    """Fail closed unless execution inputs and every fill are auditable."""
    if not isinstance(summary, dict):
        raise ExecutionProvenanceError("execution summary must be a mapping")
    if summary.get("execution_provenance_complete") is not True:
        raise ExecutionProvenanceError("execution provenance is incomplete")
    version = summary.get("execution_cost_model_version")
    if not isinstance(version, str) or not version.strip():
        raise ExecutionProvenanceError("execution cost model version is missing")
    model = summary.get("execution_cost_model")
    if not isinstance(model, dict):
        raise ExecutionProvenanceError("execution cost model is missing")
    for key in (
        "spread_bps",
        "slippage_bps",
        "fee_bps",
        "latency_seconds",
    ):
        _finite_nonnegative(model.get(key), field=f"execution_cost_model.{key}")
    for key in ("total_fees", "spread_cost", "slippage_cost"):
        _finite_nonnegative(summary.get(key), field=key)
    for key in ("unfilled_order_count", "rejected_order_count"):
        value = summary.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ExecutionProvenanceError(
                f"{key} must be a nonnegative integer"
            )
        if value:
            raise ExecutionProvenanceError(
                f"{key} must be zero for promotion"
            )
    fills = summary.get("fill_provenance")
    if not isinstance(fills, list):
        raise ExecutionProvenanceError("fill_provenance must be a list")
    required = {
        "order_id",
        "symbol",
        "side",
        "incremental_quantity",
        "cumulative_quantity",
        "price",
        "fees",
        "spread_cost",
        "slippage_cost",
        "quote_timestamp",
        "executed_at",
        "cost_model_version",
        "source",
    }
    for index, fill in enumerate(fills):
        if not isinstance(fill, dict) or not required.issubset(fill):
            raise ExecutionProvenanceError(
                f"fill_provenance[{index}] is incomplete"
            )
        if fill.get("cost_model_version") != version:
            raise ExecutionProvenanceError(
                f"fill_provenance[{index}] cost model mismatch"
            )
        if str(fill.get("side") or "").lower() not in {"buy", "sell"}:
            raise ExecutionProvenanceError(
                f"fill_provenance[{index}] side is invalid"
            )
        source = str(fill.get("source") or "").strip()
        if not source or source == "equity_backtest":
            raise ExecutionProvenanceError(
                f"fill_provenance[{index}] source is not explicit"
            )
        for key in (
            "incremental_quantity",
            "cumulative_quantity",
            "price",
            "fees",
            "spread_cost",
            "slippage_cost",
        ):
            number = _finite_nonnegative(
                fill.get(key),
                field=f"fill_provenance[{index}].{key}",
            )
            if key in {"incremental_quantity", "cumulative_quantity", "price"}:
                if number <= 0:
                    raise ExecutionProvenanceError(
                        f"fill_provenance[{index}].{key} must be positive"
                    )
        quote_at = _aware_timestamp(
            fill.get("quote_timestamp"),
            field=f"fill_provenance[{index}].quote_timestamp",
        )
        executed_at = _aware_timestamp(
            fill.get("executed_at"),
            field=f"fill_provenance[{index}].executed_at",
        )
        if executed_at < quote_at:
            raise ExecutionProvenanceError(
                f"fill_provenance[{index}] executes before its quote"
            )
from collections.abc import Mapping
from datetime import date, timezone


def _date_only(value) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValueError("date is required")
    return date.fromisoformat(text[:10])


def canonical_spy_content_hash(values: Mapping) -> str:
    """Hash sorted exact UTC valuation timestamps and adjusted closes."""
    import pandas as pd

    if not isinstance(values, Mapping):
        raise ValueError("SPY values must be a timestamp-keyed mapping")
    canonical = []
    seen = set()
    for raw_timestamp, raw_close in values.items():
        try:
            timestamp = pd.to_datetime(raw_timestamp, utc=True)
            close = Decimal(str(raw_close))
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise ValueError(
                "SPY content requires parseable timestamps and closes"
            ) from exc
        if pd.isna(timestamp):
            raise ValueError("SPY content timestamp must be parseable")
        if timestamp in seen:
            raise ValueError(
                f"duplicate SPY valuation timestamp {timestamp.isoformat()}"
            )
        seen.add(timestamp)
        if not close.is_finite() or close <= 0:
            raise ValueError("SPY adjusted closes must be finite and positive")
        timestamp_text = (
            timestamp.isoformat()
            .replace("+00:00", "Z")
        )
        close_text = format(close.normalize(), "f")
        canonical.append([timestamp_text, close_text])
    canonical.sort(key=lambda item: item[0])
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"spy-sha256-{hashlib.sha256(encoded).hexdigest()}"


def build_adjusted_spy_close_series(
    bars,
    *,
    start_date,
    end_date,
    session_close_resolver=None,
) -> dict:
    """Return timestamp-keyed adjusted SPY daily closes for the user window.

    The caller is responsible for requesting Alpaca ``adjustment=all``. This
    helper deliberately consumes the returned ``c`` field (not VWAP or an
    intraday strategy price), filters out warm-up/out-of-window bars, and
    rekeys each daily label to the authoritative XNYS session close. It never
    normalizes the valuation timestamp to midnight.
    """
    import pandas as pd

    if session_close_resolver is None:
        from live_calendar import nyse_session_close_utc

        session_close_resolver = nyse_session_close_utc
    if not callable(session_close_resolver):
        raise TypeError("session_close_resolver must be callable")
    start = _date_only(start_date)
    end = _date_only(end_date)
    if end < start:
        raise ValueError("SPY benchmark end_date precedes start_date")
    values = {}
    seen_days = set()
    for bar in bars or ():
        try:
            timestamp = pd.to_datetime((bar or {}).get("t"), utc=True)
            close = float((bar or {}).get("c"))
        except (TypeError, ValueError) as exc:
            raise ValueError("SPY benchmark bar requires timestamp t and close c") from exc
        day = timestamp.date()
        if day < start or day > end:
            continue
        if not math.isfinite(close) or close <= 0:
            raise ValueError(f"SPY adjusted close must be finite and positive on {day}")
        if day in seen_days:
            raise ValueError(f"duplicate SPY daily benchmark bar on {day}")
        seen_days.add(day)
        try:
            valuation_timestamp = pd.to_datetime(
                session_close_resolver(day),
                utc=True,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"authoritative XNYS close is unavailable for {day}"
            ) from exc
        if pd.isna(valuation_timestamp):
            raise ValueError(
                f"authoritative XNYS close is unavailable for {day}"
            )
        if valuation_timestamp in values:
            raise ValueError(
                "duplicate authoritative SPY valuation timestamp "
                f"{valuation_timestamp.isoformat()}"
            )
        values[valuation_timestamp] = close
    return dict(sorted(values.items(), key=lambda pair: pair[0]))


def _validate_spy_benchmark_manifest(manifest) -> dict:
    from experiment_registry import validate_benchmark_manifest

    return validate_benchmark_manifest(manifest)


def _daily_portfolio_value_series(snapshots, valuation_timestamps):
    """Select portfolio values only at the exact benchmark timestamps."""
    import pandas as pd

    try:
        authoritative = {
            pd.to_datetime(timestamp, utc=True)
            for timestamp in valuation_timestamps
        }
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "benchmark valuation timestamps must be parseable"
        ) from exc
    values = {}
    seen_timestamps = set()
    for snapshot in snapshots or ():
        try:
            timestamp = pd.to_datetime(
                (snapshot or {}).get("timestamp"),
                utc=True,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "portfolio snapshot timestamp must be parseable"
            ) from exc
        if pd.isna(timestamp):
            raise ValueError("portfolio snapshot timestamp must be parseable")
        if timestamp in seen_timestamps:
            raise ValueError(
                f"duplicate portfolio snapshot timestamp {timestamp.isoformat()}"
            )
        seen_timestamps.add(timestamp)
        if timestamp in authoritative:
            values[timestamp] = (snapshot or {}).get("value")
    return pd.Series(
        values,
        dtype=object,
    )


def compute_backtest_summary(
    emulator,
    snapshots,
    initial_cash,
    benchmark_values=None,
    *,
    benchmark_manifest=None,
    trials=None,
) -> dict:
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
    summary = {
        "final_value": final_value,
        "pnl": pnl,
        "pnl_percent": pnl_percent,
        "fees": fees,
        "execution_provenance_complete": False,
        "execution_cost_model_version": None,
        "execution_cost_model": None,
        "total_fees": None,
        "spread_cost": None,
        "slippage_cost": None,
        "unfilled_order_count": None,
        "rejected_order_count": None,
        "fill_provenance": [],
        "execution_promotion_eligible": False,
        "execution_promotion_error": "execution provenance is incomplete",
    }
    if emulator is not None and hasattr(emulator, "get_execution_summary"):
        try:
            execution_summary = emulator.get_execution_summary()
            if isinstance(execution_summary, dict):
                summary.update(execution_summary)
        except Exception:
            # A legacy/compatibility run remains explicitly non-promotable.
            pass
    try:
        assert_execution_provenance_promotable(summary)
        summary["execution_promotion_eligible"] = True
        summary["execution_promotion_error"] = None
    except ExecutionProvenanceError as exc:
        summary["execution_promotion_eligible"] = False
        summary["execution_promotion_error"] = str(exc)

    # Benchmark-relative enrichment is additive. The promoted path accepts
    # only timestamp-keyed adjusted SPY daily closes plus the exact request
    # convention and a positive registry trial count. Any gap remains visible
    # as an incomplete result instead of being zipped/dropped or defaulted.
    if benchmark_values is not None:
        summary.update(
            {
                "benchmark_complete": False,
                "benchmark_incomplete_reason": None,
                "benchmark_observations": 0,
                "benchmark_manifest": (
                    dict(benchmark_manifest)
                    if isinstance(benchmark_manifest, Mapping)
                    else benchmark_manifest
                ),
                "trial_count": trials,
            }
        )
        try:
            from benchmark_alpha.metrics import (
                align_return_series,
                compute_active_metrics,
            )

            manifest = _validate_spy_benchmark_manifest(benchmark_manifest)
            if not isinstance(benchmark_values, Mapping):
                raise ValueError(
                    "benchmark_values must be timestamp-keyed; positional "
                    "benchmark sequences are forbidden"
                )
            import pandas as pd

            expected_timestamps = pd.DatetimeIndex(
                pd.to_datetime(
                    manifest["valuation_timestamps"],
                    utc=True,
                )
            )
            actual_timestamps = pd.DatetimeIndex(
                pd.to_datetime(
                    list(benchmark_values.keys()),
                    utc=True,
                )
            )
            missing_benchmark = expected_timestamps.difference(
                actual_timestamps
            )
            unexpected_benchmark = actual_timestamps.difference(
                expected_timestamps
            )
            if len(missing_benchmark) or len(unexpected_benchmark):
                raise ValueError(
                    "timestamp coverage is incomplete: "
                    f"missing benchmark={len(missing_benchmark)}, "
                    "unexpected benchmark="
                    f"{len(unexpected_benchmark)}"
                )
            actual_content_hash = canonical_spy_content_hash(
                benchmark_values
            )
            if actual_content_hash != manifest["content_hash"]:
                raise ValueError(
                    "benchmark manifest content_hash does not match the "
                    "exact timestamp/adjusted-close content"
                )
            if (
                trials is None
                or isinstance(trials, bool)
                or int(trials) != trials
                or int(trials) < 1
            ):
                raise ValueError(
                    "trial count is missing; benchmark evidence requires the "
                    "actual experiment-registry count for its search scope"
                )
            portfolio_values = _daily_portfolio_value_series(
                snapshots,
                manifest["valuation_timestamps"],
            )
            aligned = align_return_series(portfolio_values, benchmark_values)
            m = compute_active_metrics(aligned, trials=int(trials))
            numeric = (
                m.benchmark_return,
                m.active_return,
                m.beta,
                m.tracking_error,
                m.information_ratio,
                m.deflated_sharpe_probability,
                m.max_drawdown_magnitude,
                m.bootstrap_active_low,
                m.bootstrap_active_high,
            )
            if not all(math.isfinite(value) for value in numeric):
                raise ValueError("non-finite benchmark metrics")
            summary.update(
                {
                    "benchmark_complete": True,
                    "benchmark_incomplete_reason": None,
                    "benchmark_manifest": manifest,
                    "benchmark_observations": m.observations,
                    "trial_count": m.trials,
                    "benchmark_return": m.benchmark_return,
                    "active_return": m.active_return,
                    "beta": m.beta,
                    "tracking_error": m.tracking_error,
                    "information_ratio": m.information_ratio,
                    "deflated_sharpe_probability": (
                        m.deflated_sharpe_probability
                    ),
                    "max_drawdown_magnitude": m.max_drawdown_magnitude,
                    "bootstrap_active_low": m.bootstrap_active_low,
                    "bootstrap_active_high": m.bootstrap_active_high,
                }
            )
        except Exception as exc:
            # Base P&L remains usable, but this row cannot be promoted.
            summary["benchmark_incomplete_reason"] = str(exc)
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
