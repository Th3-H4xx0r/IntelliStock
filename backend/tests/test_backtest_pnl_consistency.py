# backend/tests/test_backtest_pnl_consistency.py
"""Round-2 Task 8: one truthful P&L + duplicate end-date bar fix.

Reproduces incident 586767 in miniature: two different closing bars for the
same end date, plus a divergent snapshot mark. Asserts:
  1. the summary's pnl equals the equity curve's end EXACTLY, and
  2. the price-series seeding no longer emits two same-date bars per symbol.
"""
import datetime as _dt

import pytest

from backend.backtest_summary import (
    compute_backtest_summary,
    build_backtest_price_series,
    resolve_end_prices,
    compute_per_stock_pnl,
    compute_stock_price_change,
)
from backend.portfolio_emulator import PortfolioEmulator
from backend.simulated_execution import SimulationFill


def _bar_time_to_datetime(t):
    """Minimal stand-in for broker._bar_time_to_datetime (naive-UTC)."""
    if t is None:
        return None
    if isinstance(t, _dt.datetime):
        dt = t
    else:
        dt = _dt.datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return dt


class _FakeEmulator:
    """Just enough surface for compute_backtest_summary's fallback path."""

    def __init__(self, cash, positions, last_prices):
        self._cash = cash
        self._positions = positions
        self._last_prices = last_prices

    def get_portfolio_value(self, prices):
        value = self._cash
        for tkr, sh in self._positions.items():
            p = (prices or {}).get(tkr)
            if p is not None:
                value += sh * float(p)
        return value


# --- Fixtures: duplicate end-date bars with DIFFERENT closes -----------------

INITIAL_CASH = 100000.0

# Two positions, held through the end date.
POSITIONS = {"AAPL": 10.0, "MSFT": 5.0}
CASH_REMAINING = 50000.0

# The end date carries TWO bars per symbol with different closes. The equity
# snapshot marked positions with the HIGHER (first) close; a "latest bar"
# resolver would have used the LOWER (second) one — the historical divergence.
END = "2026-07-01"
SNAPSHOT_PRICES = {"AAPL": 200.0, "MSFT": 400.0}   # marks the sim ran on
LATER_LOWER_CLOSE = {"AAPL": 150.0, "MSFT": 300.0}  # the phantom second bar

# Equity-curve end value uses the snapshot marks.
SNAP_END_VALUE = CASH_REMAINING + 10.0 * 200.0 + 5.0 * 400.0  # 54,000

SNAPSHOTS = [
    {
        "timestamp": _dt.datetime(2026, 6, 30, 21, 0, 0),
        "value": 52000.0,
        "cash": CASH_REMAINING,
        "prices": {"AAPL": 190.0, "MSFT": 380.0},
    },
    {
        # Snapshot timestamp differs in time-of-day from the raw bar below,
        # so an exact (timestamp, symbol) de-dup would let both through.
        "timestamp": _dt.datetime(2026, 7, 1, 20, 0, 0),
        "value": SNAP_END_VALUE,
        "cash": CASH_REMAINING,
        "prices": SNAPSHOT_PRICES,
    },
]

# data[sym] intentionally holds two 2026-07-01 bars with different closes.
DATA = {
    "AAPL": [
        {"t": "2026-06-30T21:00:00+00:00", "c": 190.0},
        {"t": "2026-07-01T13:30:00+00:00", "c": SNAPSHOT_PRICES["AAPL"]},
        {"t": "2026-07-01T21:00:00+00:00", "c": LATER_LOWER_CLOSE["AAPL"]},
    ],
    "MSFT": [
        {"t": "2026-06-30T21:00:00+00:00", "c": 380.0},
        {"t": "2026-07-01T13:30:00+00:00", "c": SNAPSHOT_PRICES["MSFT"]},
        {"t": "2026-07-01T21:00:00+00:00", "c": LATER_LOWER_CLOSE["MSFT"]},
    ],
}


# Trades consistent with the cash accounting above: initial 100,000 minus
# 50,000 of buys leaves CASH_REMAINING = 50,000 (prices are contrived so the
# arithmetic reconciles exactly; that is the property under test).
TRADES = [
    {"timestamp": _dt.datetime(2026, 6, 30, 14, 0, 0), "action": "buy",
     "ticker": "AAPL", "shares": 10.0, "price": 2500.0, "total": 25000.0},
    {"timestamp": _dt.datetime(2026, 6, 30, 14, 5, 0), "action": "buy",
     "ticker": "MSFT", "shares": 5.0, "price": 5000.0, "total": 25000.0},
]


def test_summary_pnl_equals_equity_curve_end_exactly():
    emulator = _FakeEmulator(CASH_REMAINING, POSITIONS, SNAPSHOT_PRICES)
    summary = compute_backtest_summary(emulator, SNAPSHOTS, INITIAL_CASH)

    expected_pnl = SNAPSHOTS[-1]["value"] - INITIAL_CASH
    assert summary["final_value"] == SNAPSHOTS[-1]["value"]
    # The load-bearing invariant: pnl == equity-curve end, EXACTLY.
    assert summary["pnl"] == expected_pnl
    assert summary["pnl_percent"] == expected_pnl / INITIAL_CASH * 100.0


def test_summary_ignores_divergent_resolver_price():
    # A "latest bar at-or-before end" resolver would value positions with the
    # lower close and produce a very different (wrong) pnl. Prove the summary
    # does NOT depend on that path.
    emulator = _FakeEmulator(CASH_REMAINING, POSITIONS, SNAPSHOT_PRICES)
    wrong_value = emulator.get_portfolio_value(LATER_LOWER_CLOSE)
    assert wrong_value != SNAPSHOTS[-1]["value"]  # sanity: paths diverge

    summary = compute_backtest_summary(emulator, SNAPSHOTS, INITIAL_CASH)
    assert summary["pnl"] != (wrong_value - INITIAL_CASH)
    assert summary["pnl"] == SNAPSHOTS[-1]["value"] - INITIAL_CASH


def test_no_snapshots_falls_back_to_last_prices():
    emulator = _FakeEmulator(CASH_REMAINING, POSITIONS, SNAPSHOT_PRICES)
    summary = compute_backtest_summary(emulator, [], INITIAL_CASH)
    expected = emulator.get_portfolio_value(SNAPSHOT_PRICES)
    assert summary["final_value"] == expected
    assert summary["pnl"] == expected - INITIAL_CASH


def test_price_series_has_no_duplicate_snapshot_bar_for_a_bar_date():
    """The snapshot seeding must not add a second same-date close for a symbol
    that already has a bar on that date."""
    series = build_backtest_price_series(
        DATA,
        SNAPSHOTS,
        price_symbols=["AAPL", "MSFT"],
        start_date_only=_dt.date(2026, 6, 30),
        end_date_only=_dt.date(2026, 7, 1),
        bar_time_to_datetime=_bar_time_to_datetime,
    )

    # Count rows per (date, symbol) that ORIGINATE from the snapshot seeding.
    # Bars themselves may legitimately be intraday (multiple per date); the
    # regression is specifically a snapshot row duplicating a bar's date.
    # Assert: no (date, symbol) appears with a close equal to a snapshot mark
    # in ADDITION to the bar closes — i.e. snapshot never seeded AAPL/MSFT
    # because bars already cover 2026-07-01.
    per_date_symbol = {}
    for row in series:
        d = _bar_time_to_datetime(row["timestamp"]).date()
        per_date_symbol.setdefault((d, row["symbol"]), []).append(row["close"])

    # AAPL and MSFT are covered by bars on 07-01, so the snapshot must NOT
    # have added a THIRD row. Two bars (13:30 and 21:00) is the real intraday
    # data; the phantom snapshot row would have been a 3rd.
    assert len(per_date_symbol[(_dt.date(2026, 7, 1), "AAPL")]) == 2
    assert len(per_date_symbol[(_dt.date(2026, 7, 1), "MSFT")]) == 2
    # And specifically the snapshot's mark (200.0/400.0) appears only via the
    # bar (13:30), not doubled by the snapshot seeding.
    assert per_date_symbol[(_dt.date(2026, 7, 1), "AAPL")].count(200.0) == 1


def test_snapshot_seeds_only_symbols_without_bars():
    """Non-watchlist traded symbols (no bars) SHOULD be filled from snapshots,
    exactly once per date."""
    data_no_nflx = {"AAPL": DATA["AAPL"]}
    snaps = [
        {
            "timestamp": _dt.datetime(2026, 7, 1, 20, 0, 0),
            "value": 1.0,
            "prices": {"AAPL": 200.0, "NFLX": 900.0},
        },
    ]
    series = build_backtest_price_series(
        data_no_nflx,
        snaps,
        price_symbols=["AAPL", "NFLX"],
        start_date_only=_dt.date(2026, 7, 1),
        end_date_only=_dt.date(2026, 7, 1),
        bar_time_to_datetime=_bar_time_to_datetime,
    )
    nflx_rows = [r for r in series if r["symbol"] == "NFLX"]
    assert len(nflx_rows) == 1
    assert nflx_rows[0]["close"] == 900.0
    # AAPL still comes only from its bar, not doubled by the snapshot.
    aapl_rows = [r for r in series if r["symbol"] == "AAPL"]
    assert all(r["close"] != 0 for r in aapl_rows)
    assert 200.0 in [r["close"] for r in aapl_rows]
    assert [r["close"] for r in aapl_rows].count(200.0) == 1


# --- Review fast-follow: per-stock P&L / end_price on the same snapshot basis


def test_end_prices_prefer_last_snapshot_marks_over_resolver():
    # The resolver returned the phantom (lower) duplicate-bar closes; the last
    # snapshot's marks must win, and the resolver only fills missing symbols.
    resolver = dict(LATER_LOWER_CLOSE)
    resolver["NFLX"] = 900.0  # not in any snapshot
    end_prices = resolve_end_prices(resolver, SNAPSHOTS)
    assert end_prices["AAPL"] == SNAPSHOT_PRICES["AAPL"]
    assert end_prices["MSFT"] == SNAPSHOT_PRICES["MSFT"]
    assert end_prices["NFLX"] == 900.0  # resolver fallback preserved
    # Inputs not mutated.
    assert resolver["AAPL"] == LATER_LOWER_CLOSE["AAPL"]
    assert SNAPSHOTS[-1]["prices"] == SNAPSHOT_PRICES


def test_per_stock_pnl_reconciles_with_headline_pnl():
    """Dup end-date bars: sum of per-stock P&L (realized + open marks) must
    reconcile with the headline pnl because both use the snapshot basis."""
    emulator = _FakeEmulator(CASH_REMAINING, POSITIONS, SNAPSHOT_PRICES)
    headline = compute_backtest_summary(emulator, SNAPSHOTS, INITIAL_CASH)["pnl"]

    end_prices = resolve_end_prices(dict(LATER_LOWER_CLOSE), SNAPSHOTS)
    pnl_per_stock, pnl_pct = compute_per_stock_pnl(
        TRADES, POSITIONS, end_prices, ["AAPL", "MSFT"],
    )
    assert sum(pnl_per_stock.values()) == headline

    # On the phantom resolver basis it would NOT have reconciled — prove the
    # divergence is real and the merge is what closes it.
    wrong_pnl, _ = compute_per_stock_pnl(
        TRADES, POSITIONS, dict(LATER_LOWER_CLOSE), ["AAPL", "MSFT"],
    )
    assert sum(wrong_pnl.values()) != headline
    # pnl_percent is against cost basis (buys).
    assert pnl_pct["AAPL"] == round(pnl_per_stock["AAPL"] / 25000.0 * 100.0, 4)


def test_confirmed_fill_totals_reconcile_per_stock_and_headline_pnl():
    emulator = PortfolioEmulator(10_000.0)
    fill_at = _dt.datetime(2026, 7, 1, 15, 0, tzinfo=_dt.timezone.utc)
    emulator.apply_fill(
        SimulationFill(
            order_id="costed-buy",
            symbol="AAPL",
            side="buy",
            incremental_quantity=10.0,
            cumulative_quantity=10.0,
            price=101.0,
            fees=1.0,
            spread_cost=5.0,
            slippage_cost=2.0,
            quote_timestamp=fill_at,
            executed_at=fill_at,
            cost_model_version="equity-next-event-v1",
            source="main_signal",
        )
    )
    emulator.save_portfolio_snapshot({"AAPL": 100.0}, timestamp=fill_at)

    snapshots = emulator.get_portfolio_history()
    headline = compute_backtest_summary(emulator, snapshots, 10_000.0)
    per_stock, _ = compute_per_stock_pnl(
        emulator.get_trade_history(),
        emulator.get_positions(),
        {"AAPL": 100.0},
        ["AAPL"],
    )

    assert headline["pnl"] == pytest.approx(-11.0)
    assert per_stock["AAPL"] == pytest.approx(headline["pnl"])


def test_stock_price_change_end_price_is_snapshot_mark():
    end_prices = resolve_end_prices(dict(LATER_LOWER_CLOSE), SNAPSHOTS)
    start_prices = {"AAPL": 190.0, "MSFT": 380.0}
    spc = compute_stock_price_change(["AAPL", "MSFT"], start_prices, end_prices)
    assert spc["AAPL"]["end_price"] == SNAPSHOT_PRICES["AAPL"]
    assert spc["MSFT"]["end_price"] == SNAPSHOT_PRICES["MSFT"]
    assert spc["AAPL"]["end_price"] != LATER_LOWER_CLOSE["AAPL"]
    assert spc["AAPL"]["change_percent"] == round((200.0 - 190.0) / 190.0 * 100.0, 4)
