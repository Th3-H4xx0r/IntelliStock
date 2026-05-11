"""Dual-cadence backtest harness pre-flight checks.

Spec: docs/superpowers/specs/2026-05-02-nexus-dual-cadence-backtest-harness-design.md

Lives in its own module so it can be unit-tested without importing broker.py
(which calls argparse.parse_args() at module load and would sys.exit under
pytest). broker.py imports `dual_cadence_backtest_preflight` from here and
calls it once after bars are fetched, gated on the harness flag.

Errors raise DualCadencePreflightError (a RuntimeError subclass) so the
broker's outer except-Exception catch can log and mark the BacktestResults
row failed. Earlier versions raised SystemExit, which inherits from
BaseException — uncatchable without a bare except, leaving the result row
stuck in the "running" stub state.
"""

from __future__ import annotations

import datetime as _dt


class DualCadencePreflightError(RuntimeError):
    """Raised when the dual-cadence backtest harness pre-flight refuses to run."""


try:
    from zoneinfo import ZoneInfo as _ZI
except Exception:  # pragma: no cover - zoneinfo is stdlib in 3.9+
    _ZI = None


def _logger():
    """Resolve a logger that works whether broker is loaded (production) or
    not (unit tests). Prefer intellistock_logger directly so this module
    doesn't pull broker.py in at import time (broker.py runs argparse at
    module load and would sys.exit under pytest)."""
    try:
        from intellistock_logger import intellistock_logger

        def _bound(msg, color="white"):
            try:
                intellistock_logger.log(msg, color, service="BROKER")
            except Exception:
                print(f"[BROKER] {msg}")

        return _bound
    except Exception:
        def _fallback(msg, color="white"):
            print(f"[BROKER] {msg}")
        return _fallback


def _bar_dt_utc(bar):
    t = bar.get("t") if isinstance(bar, dict) else None
    if not t:
        return None
    try:
        s = t.replace("Z", "+00:00") if isinstance(t, str) else t
        if isinstance(s, str):
            d = _dt.datetime.fromisoformat(s)
        else:
            d = s
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d
    except Exception:
        return None


def validate_coupled_flags(run_once_specs):
    """Raise DualCadencePreflightError at backtest startup if any spec sets
    nexus_dual_cadence_backtest_simulation=True with nexus_dual_cadence_enabled=False.

    Called from broker.py BEFORE the replay loop so the misconfiguration fails
    fast, instead of in-strategy where the per-tick exception handler would
    swallow it in live mode and fire it every tick in backtest mode.
    """
    for spec in (run_once_specs or []):
        try:
            cfg = (spec or {}).get("config") or {}
        except Exception:
            continue
        if cfg.get("nexus_dual_cadence_backtest_simulation") and not cfg.get(
            "nexus_dual_cadence_enabled", True
        ):
            raise DualCadencePreflightError(
                "nexus_dual_cadence_backtest_simulation=True requires "
                "nexus_dual_cadence_enabled=True. Without it, the full pipeline "
                "would fire on every hourly tick (7x LLM cost, corrupted outcomes "
                "table, meaningless harness run)."
            )


def dual_cadence_backtest_preflight(data, time_increment, symbols):
    """Validate the dual-cadence backtest harness pre-conditions and RTH-filter the bars.

    Checks — raise DualCadencePreflightError on failure (do NOT silently degrade):
      1) granularity_sec must be a positive int in [30, 3600]. The strategy
         gate's sanity floor is 30s; anything sub-30s would tick faster than
         the RethinkDB persistence cadence can keep up. Anything > 3600
         (e.g. daily) means the dual-cadence gate would never activate
         (only one tick per day → no monitor cycles).
      2) per-symbol bar coverage >= 90% of expected RTH bars in window
         (expected = ceil(6.5h * 3600 / time_increment) per RTH day, anchored
         to symbol's own data; skip symbols with 0 bars).
      3) RTH-only filter: drop bars whose NY-time is outside 9:30-16:00 ET.

    2026-05-07 scheduler refactor: relaxed from `time_increment == 3600` to
    accept any sub-hourly cadence the strategy gate would activate at, so
    operators can backtest at 5/15/30-min granularities with the harness.
    """
    log = _logger()

    try:
        ti_seconds = int(time_increment)
    except (TypeError, ValueError):
        ti_seconds = -1
    if ti_seconds < 30 or ti_seconds > 3600:
        _orig = repr(time_increment) if ti_seconds == -1 else str(ti_seconds)
        log(
            f"dual-cadence backtest harness requires granularity_sec in [30, 3600] "
            f"(got {_orig}). Sub-30s would outrun cache persistence; >3600s would "
            "fire only once per day so the dual-cadence gate would never activate.",
            "red",
        )
        raise DualCadencePreflightError(
            f"dual-cadence backtest harness requires granularity_sec in [30, 3600] (got {_orig})"
        )

    ny_tz = _ZI("America/New_York") if _ZI is not None else None
    if ny_tz is None:
        log(
            "dual-cadence pre-flight: ZoneInfo unavailable (zoneinfo / tzdata missing). "
            "RTH filter and per-NY-day coverage check will be SKIPPED. Install tzdata "
            "or use Python 3.9+ to enable the full pre-flight.",
            "red",
        )

    # Compute per-symbol coverage AGAINST the original (pre-RTH-filter) bar
    # timestamps so an extended-hours-heavy feed can't game the denominator.
    # Each symbol's expected count = `bars_per_rth_day * NY-trading-days that
    # symbol has bars for` — anchored to its own data, not a global union.
    # bars_per_rth_day = ceil(6.5h * 3600 / time_increment_sec): 7 for hourly,
    # ~26 for 15-min, ~78 for 5-min.
    import math as _math
    bars_per_rth_day = max(1, int(_math.ceil(6.5 * 3600.0 / float(ti_seconds))))
    failed = []
    for sym in (symbols or []):
        original_bars = data.get(sym) or []
        if not original_bars:
            # Skip — likely delisted or feed gap; broker has separate logic.
            continue
        if ny_tz is None:
            # Without ZoneInfo we can't compute NY trading days — skip the check.
            continue
        sym_days = set()
        rth_count = 0
        for b in original_bars:
            d = _bar_dt_utc(b)
            if d is None:
                continue
            ny = d.astimezone(ny_tz)
            sym_days.add(ny.strftime("%Y-%m-%d"))
            hh, mm = ny.hour, ny.minute
            if (hh == 9 and mm >= 30) or (10 <= hh <= 15):
                rth_count += 1
        sym_expected = max(1, bars_per_rth_day * len(sym_days))
        ratio = rth_count / float(sym_expected)
        if ratio < 0.90:
            log(
                f"dual-cadence pre-flight: {sym} RTH coverage {rth_count}/{sym_expected} "
                f"({ratio:.1%}) is below 90% (granularity_sec={ti_seconds}, "
                f"~{bars_per_rth_day} bars/RTH-day expected). Pre-stage bars or shorten the window.",
                "yellow",
            )
            failed.append((sym, rth_count, sym_expected, ratio))
    if failed:
        raise DualCadencePreflightError(
            "dual-cadence backtest harness refused to run: "
            f"{len(failed)} symbol(s) below 90% RTH-bar coverage."
        )

    # Coverage passed — NOW apply the RTH filter (mutates data in-place).
    # Filter runs after coverage so a symbol with many extended-hours bars
    # but few RTH bars cannot pass coverage on raw count and then have its
    # data silently emptied here.
    if ny_tz is not None:
        for sym in list(data.keys()):
            bars = data.get(sym) or []
            kept = []
            dropped = 0
            for b in bars:
                d = _bar_dt_utc(b)
                if d is None:
                    continue
                ny = d.astimezone(ny_tz)
                # RTH window: keep bars whose start hour is 9:30 ET or in [10..15].
                # The 15:30 ET bar covers 15:30-16:00 (NYSE close); the 16:00+
                # bar is post-close and is dropped.
                hh, mm = ny.hour, ny.minute
                if hh == 9 and mm >= 30:
                    kept.append(b)
                elif 10 <= hh <= 15:
                    kept.append(b)
                else:
                    dropped += 1
            if dropped > 0:
                log(
                    f"dual-cadence pre-flight: dropped {dropped} non-RTH bars for {sym}.",
                    "cyan",
                )
            data[sym] = kept
