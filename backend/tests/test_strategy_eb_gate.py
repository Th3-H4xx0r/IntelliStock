"""The frozen G1-G6 acceptance gate (spec section 11), evaluated offline.

The gate decides whether Strategy EB ships enabled. That makes its own
arithmetic the thing most worth testing: a gate that silently passes is worse
than no gate, and a gate that silently fails re-runs the XS story where the
strategy was blamed for what was really a measurement bug.

Everything here drives the PURE evaluator on synthetic curves. No API call, no
database, no clock.
"""
import datetime as dt
import os
import sys

import pytest

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_scripts = os.path.join(_root, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

import strategy_eb_gate as gate  # noqa: E402


# ------------------------------------------------------------------ fixtures
def _months(count, year=2021, month=11):
    """`count` month-end-ish stamps starting at (year, month)."""
    out = []
    for i in range(count):
        y, m = divmod((month - 1) + i, 12)
        out.append(dt.datetime(year + y, m + 1, 28))
    return out


def _pv(stamps, equity, spy):
    return [{"timestamp": s.isoformat() + "Z", "value": e,
             "cash": 0.0, "positions_snapshot": {}, "prices": {"SPY": p}}
            for s, e, p in zip(stamps, equity, spy)]


def _compound(stamps, annual, start=6000.0):
    return [start * (1.0 + annual) ** (i / 12.0) for i in range(len(stamps))]


def _trades(count, notional, stamps):
    return [{"timestamp": stamps[i % len(stamps)].isoformat(), "action": "buy",
             "ticker": "TQQQ", "shares": 1.0, "price": notional,
             "total": notional} for i in range(count)]


def _clean_run(eb_annual=0.20, spy_annual=0.10, months=58, trade_count=20):
    """A run where all six conditions hold, by construction."""
    stamps = _months(months)
    equity = _compound(stamps, eb_annual)
    spy = _compound(stamps, spy_annual, start=400.0)
    return _pv(stamps, equity, spy), _trades(trade_count, 500.0, stamps), []


def _named(result, name):
    return next(c for c in result.checks if c.name == name)


def _passed(result, name):
    return _named(result, name).passed


# ------------------------------------------------------------------ the gate
def test_a_clean_run_passes_all_six():
    pv, trades, logs = _clean_run()
    result = gate.evaluate(pv, trades, logs)
    assert [c.name for c in result.checks] == ["G1", "G2", "G3", "G4", "G5", "G6"]
    assert result.passed, [c for c in result.checks if not c.passed]
    assert result.exit_code == 0


def test_g1_fails_when_the_cagr_edge_is_under_four_points():
    pv, trades, logs = _clean_run(eb_annual=0.12, spy_annual=0.10)
    result = gate.evaluate(pv, trades, logs)
    assert not _passed(result, "G1")
    assert not result.passed
    assert result.exit_code == 1


def test_g1_passes_just_above_the_four_point_bound():
    """The margin is measured against the ACCRUED benchmark, so the target is
    the dividend-inclusive SPY CAGR plus 4pp -- half a point clear of it."""
    stamps = _months(58)
    spy = _compound(stamps, 0.10, start=400.0)
    years = (stamps[-1] - stamps[0]).days / 365.25
    accrued = gate._accrue(list(zip(stamps, spy)))
    spy_cagr = (accrued[-1][1] / accrued[0][1]) ** (1.0 / years) - 1.0
    end = 6000.0 * (1.0 + spy_cagr + 0.0405) ** years
    equity = [6000.0 * (end / 6000.0) ** (i / (len(stamps) - 1))
              for i in range(len(stamps))]
    result = gate.evaluate(_pv(stamps, equity, spy), _trades(20, 500.0, stamps), [])
    assert _passed(result, "G1"), result.metrics
    assert result.metrics["spy_cagr_pct"] == pytest.approx(spy_cagr * 100.0)


def test_g1_fails_on_the_margin_the_dividend_would_have_paid_for():
    """A curve that clears price-only SPY by 4pp but not total-return SPY.
    Without the accrual this run passes; with it, it does not."""
    stamps = _months(58)
    spy = _compound(stamps, 0.10, start=400.0)
    years = (stamps[-1] - stamps[0]).days / 365.25
    price_cagr = (spy[-1] / spy[0]) ** (1.0 / years) - 1.0
    end = 6000.0 * (1.0 + price_cagr + 0.045) ** years
    equity = [6000.0 * (end / 6000.0) ** (i / (len(stamps) - 1))
              for i in range(len(stamps))]
    result = gate.evaluate(_pv(stamps, equity, spy), [], [])
    assert result.metrics["eb_cagr_pct"] >= price_cagr * 100.0 + 4.0
    assert not _passed(result, "G1")


# --------------------------------------------------- the benchmark is total return
def test_the_benchmark_is_accrued_to_total_return():
    stamps = _months(58)
    spy = _compound(stamps, 0.10, start=400.0)
    result = gate.evaluate(_pv(stamps, _compound(stamps, 0.20), spy), [], [])
    years = (stamps[-1] - stamps[0]).days / 365.25
    price_cagr = ((spy[-1] / spy[0]) ** (1.0 / years) - 1.0) * 100.0
    assert result.metrics["spy_cagr_pct"] == pytest.approx(
        ((1.0 + price_cagr / 100.0) * 1.0125 - 1.0) * 100.0, abs=1e-6)
    assert result.metrics["benchmark_dividend_yield"] == 0.0125


def test_accrual_is_continuous_in_time_not_per_row():
    """Dropping half the snapshots must not drop half the dividend: the same
    date has to carry the same accrual whatever the sampling."""
    stamps = _months(58)
    spy = _compound(stamps, 0.10, start=400.0)
    dense = dict(gate._accrue(list(zip(stamps, spy))))
    sparse = dict(gate._accrue(list(zip(stamps, spy))[::2]))
    assert len(sparse) * 2 < len(dense) + 3
    for stamp, price in sparse.items():
        assert price == pytest.approx(dense[stamp])


def test_accrual_leaves_the_first_observation_alone():
    stamps = _months(12)
    accrued = gate._accrue([(s, 400.0) for s in stamps])
    assert accrued[0][1] == 400.0
    assert accrued[-1][1] > 400.0


def test_the_six_thresholds_are_the_frozen_ones():
    """Spec section 11, frozen before any engine run. Changing a number here
    is re-tuning the gate to pass, which the spec forbids."""
    assert gate.CAGR_MARGIN_PP == 4.0
    assert gate.DRAWDOWN_MULTIPLE == 1.2
    assert gate.YEAR_2022_TOLERANCE_PP == 12.0
    assert gate.MIN_ROLLING_WIN_RATE_PCT == 60.0
    assert gate.MAX_TURNOVER_PCT_PER_YEAR == 400.0
    assert gate.BENCHMARK == "SPY"


def test_g2_fails_when_the_drawdown_is_worse_than_one_point_two_times_spy():
    stamps = _months(58)
    equity = _compound(stamps, 0.20)
    spy = _compound(stamps, 0.10, start=400.0)
    # SPY -10% at the midpoint, EB -40%: 40 > 1.2 * 10.
    spy[30] *= 0.90
    equity[30] *= 0.60
    result = gate.evaluate(_pv(stamps, equity, spy), _trades(20, 500.0, stamps), [])
    assert not _passed(result, "G2")
    assert result.metrics["eb_max_drawdown_pct"] < result.metrics["spy_max_drawdown_pct"]


def test_g2_passes_when_the_drawdown_is_inside_the_multiple():
    stamps = _months(58)
    equity = _compound(stamps, 0.20)
    spy = _compound(stamps, 0.10, start=400.0)
    spy[30] *= 0.80
    equity[30] *= 0.79      # -21% against SPY's -20%, inside 1.2x
    result = gate.evaluate(_pv(stamps, equity, spy), _trades(20, 500.0, stamps), [])
    assert _passed(result, "G2"), result.metrics


def test_g3_fails_when_2022_lags_spy_by_more_than_twelve_points():
    stamps = _months(58)
    equity = _compound(stamps, 0.20)
    spy = _compound(stamps, 0.10, start=400.0)
    # A real 2022 bear for EB: -6% a month through the year, then flat again.
    decay, k = 0.94, 0
    for i, s in enumerate(stamps):
        if s.year == 2022:
            k += 1
        equity[i] *= decay ** min(k, 12)
    result = gate.evaluate(_pv(stamps, equity, spy), _trades(20, 500.0, stamps), [])
    assert not _passed(result, "G3")
    assert result.metrics["eb_2022_pct"] < result.metrics["spy_2022_pct"] - 12.0


def test_g3_fails_loudly_when_2022_is_not_in_the_window():
    stamps = _months(30, year=2023, month=1)
    equity = _compound(stamps, 0.20)
    spy = _compound(stamps, 0.10, start=400.0)
    result = gate.evaluate(_pv(stamps, equity, spy), _trades(20, 500.0, stamps), [])
    check = _named(result, "G3")
    assert not check.passed
    assert "2022" in check.description


def test_g4_fails_when_rolling_windows_lose_more_often_than_they_win():
    stamps = _months(58)
    spy = _compound(stamps, 0.10, start=400.0)
    # Flat for four years, then a single jump: the CAGR is fine, the
    # consistency the gate asks for is not.
    equity = [6000.0] * 50 + [24000.0] * 8
    result = gate.evaluate(_pv(stamps, equity, spy), _trades(20, 500.0, stamps), [])
    assert not _passed(result, "G4")
    assert result.metrics["rolling_12m_windows"] > 0
    assert result.metrics["rolling_12m_win_rate_pct"] < 60.0


def test_g4_counts_one_window_per_calendar_month_not_per_row():
    """Two snapshots inside one month must not become two windows."""
    stamps = _months(58)
    equity = _compound(stamps, 0.20)
    spy = _compound(stamps, 0.10, start=400.0)
    once = gate.evaluate(_pv(stamps, equity, spy), [], [])
    doubled_stamps, doubled_equity, doubled_spy = [], [], []
    for s, e, p in zip(stamps, equity, spy):
        doubled_stamps += [s.replace(day=14), s]
        doubled_equity += [e * 0.99, e]
        doubled_spy += [p * 0.99, p]
    twice = gate.evaluate(_pv(doubled_stamps, doubled_equity, doubled_spy), [], [])
    assert once.metrics["rolling_12m_windows"] == twice.metrics["rolling_12m_windows"]


def test_three_rolling_spans_are_measured_and_only_twelve_is_gated():
    pv, trades, logs = _clean_run()
    result = gate.evaluate(pv, trades, logs)
    for span in (3, 6, 12):
        assert result.metrics["rolling_%dm_windows" % span] > 0
        assert result.metrics["rolling_%dm_win_rate_pct" % span] == 100.0
    assert gate.GATE_ROLLING_MONTHS == 12
    assert gate.ROLLING_SPANS == (3, 6, 12)
    # G4 reads the 12-month rate, not the best of the three.
    assert "12m" in _named(result, "G4").description


def test_a_shorter_span_yields_strictly_more_windows():
    pv, trades, logs = _clean_run()
    m = gate.evaluate(pv, trades, logs).metrics
    assert m["rolling_3m_windows"] > m["rolling_6m_windows"] > m["rolling_12m_windows"]


def test_the_report_prints_all_three_rolling_rates():
    pv, trades, logs = _clean_run()
    text = "\n".join(gate.render(gate.evaluate(pv, trades, logs), 1))
    assert "rolling win rate" in text
    for span in ("3m", "6m", "12m"):
        assert span in text
    assert "total return" in text


def test_a_short_run_reports_the_spans_it_can_and_fails_g4():
    """Six months of history: 3m windows exist, 12m ones do not, and G4 fails
    on absence rather than passing on a rate computed from nothing."""
    stamps = _months(7)
    pv = _pv(stamps, _compound(stamps, 0.20), _compound(stamps, 0.10, 400.0))
    result = gate.evaluate(pv, [], [])
    assert result.metrics["rolling_3m_windows"] > 0
    assert result.metrics["rolling_12m_windows"] == 0
    assert not _passed(result, "G4")
    assert any("12-month window" in w for w in result.warnings)


# ------------------------------------------- the calendar year has a real base
def test_the_2022_return_is_measured_from_the_2021_close():
    """Not from the first January print: a run that holds through a January
    selloff must be charged for it."""
    stamps = _months(58)                     # starts 2021-11
    equity = _compound(stamps, 0.0)
    spy = _compound(stamps, 0.0, start=400.0)
    jan = next(i for i, s in enumerate(stamps) if (s.year, s.month) == (2022, 1))
    for i in range(jan, len(stamps)):        # -20% in January, then flat
        equity[i] *= 0.80
    result = gate.evaluate(_pv(stamps, equity, spy), [], [])
    assert result.metrics["eb_2022_pct"] == pytest.approx(-20.0)


def test_a_run_starting_inside_the_year_falls_back_to_its_first_observation():
    stamps = _months(24, year=2022, month=2)
    equity = _compound(stamps, 0.0)
    result = gate.evaluate(_pv(stamps, equity, _compound(stamps, 0.0, 400.0)),
                           [], [])
    assert result.metrics["eb_2022_pct"] == pytest.approx(0.0)


def test_g5_fails_above_four_hundred_percent_turnover():
    stamps = _months(58)
    equity = _compound(stamps, 0.20)
    spy = _compound(stamps, 0.10, start=400.0)
    trades = _trades(100, 2000.0, stamps)      # $200k against ~$8k mean equity
    result = gate.evaluate(_pv(stamps, equity, spy), trades, [])
    assert not _passed(result, "G5")
    assert result.metrics["turnover_pct_per_year"] > 400.0


def test_g5_sums_both_legs_like_the_research_harness():
    """`traded` is buys PLUS sells, matching scripts/strategy_xs_matrix.py:152.
    Halving it here would quietly double the turnover budget."""
    stamps = _months(58)
    equity = [6000.0] * len(stamps)
    spy = _compound(stamps, 0.0, start=400.0)
    years = (stamps[-1] - stamps[0]).days / 365.25
    trades = [{"total": 600.0, "action": "buy"}, {"total": 600.0, "action": "sell"}]
    result = gate.evaluate(_pv(stamps, equity, spy), trades, [])
    assert result.metrics["turnover_pct_per_year"] == pytest.approx(
        1200.0 / 6000.0 / years * 100.0)


def test_g6_fails_on_a_ghost_sell_observation():
    pv, trades, _ = _clean_run()
    logs = ["something ordinary",
            "[ghost_sell_observation] symbol=TQQQ intents=['x'] pre_action=None "
            "would_block_in_phase2=True"]
    result = gate.evaluate(pv, trades, logs)
    assert not _passed(result, "G6")
    assert result.metrics["ghost_sell_lines"] == 1


def test_g6_fails_on_a_broker_position_cap_trim():
    pv, trades, _ = _clean_run()
    logs = ["Broker single-position cap: TQQQ cash_to_use $3900.00 trimmed to "
            "$0.00 (existing=$0.00, cap=15%=$900.00)"]
    result = gate.evaluate(pv, trades, logs)
    assert not _passed(result, "G6")
    assert result.metrics["cap_trim_lines"] == 1


def test_g6_passes_on_a_quiet_log():
    pv, trades, _ = _clean_run()
    result = gate.evaluate(pv, trades, ["tick 1 ok", "tick 2 ok"])
    assert _passed(result, "G6")


# ------------------------------------------------------- honesty of the read
def test_truncated_logs_warn_because_g6_is_then_a_lower_bound():
    pv, trades, _ = _clean_run()
    result = gate.evaluate(pv, trades, ["tick ok"], logs_complete=False)
    assert _passed(result, "G6")
    assert any("lower bound" in w for w in result.warnings)


def test_complete_logs_do_not_warn():
    pv, trades, _ = _clean_run()
    result = gate.evaluate(pv, trades, ["tick ok"], logs_complete=True)
    assert not any("lower bound" in w for w in result.warnings)


def test_a_trade_list_at_the_tail_cap_warns_that_turnover_is_understated():
    stamps = _months(58)
    equity = _compound(stamps, 0.20)
    spy = _compound(stamps, 0.10, start=400.0)
    trades = _trades(gate.TRADE_TAIL_CAP, 1.0, stamps)
    result = gate.evaluate(_pv(stamps, equity, spy), trades, [])
    assert any("tail cap" in w for w in result.warnings)


def test_a_run_with_no_spy_prices_refuses_rather_than_guessing():
    stamps = _months(58)
    rows = _pv(stamps, _compound(stamps, 0.20), [0.0] * len(stamps))
    with pytest.raises(gate.GateError, match="SPY"):
        gate.evaluate(rows, [], [])


def test_a_run_with_one_snapshot_refuses():
    stamps = _months(1)
    with pytest.raises(gate.GateError, match="portfolio value history"):
        gate.evaluate(_pv(stamps, [6000.0], [400.0]), [], [])


def test_mixed_naive_and_offset_timestamps_do_not_raise():
    stamps = _months(58)
    rows = _pv(stamps, _compound(stamps, 0.20), _compound(stamps, 0.10, 400.0))
    rows[0]["timestamp"] = stamps[0].isoformat()              # naive
    rows[1]["timestamp"] = stamps[1].isoformat() + "+00:00"   # aware
    rows[2]["timestamp"] = stamps[2]                          # a datetime
    result = gate.evaluate(rows, [], [])
    assert result.metrics["years"] > 4.0


def test_rows_are_sorted_by_timestamp_before_anything_is_measured():
    """A pv array that arrives out of order must not invent a drawdown."""
    stamps = _months(58)
    rows = _pv(stamps, _compound(stamps, 0.20), _compound(stamps, 0.10, 400.0))
    shuffled = rows[30:] + rows[:30]
    assert gate.evaluate(shuffled, [], []).metrics == gate.evaluate(rows, [], []).metrics


# ------------------------------------------------------------------ report
def test_the_report_names_every_condition_with_its_measured_number():
    pv, trades, logs = _clean_run()
    lines = gate.render(gate.evaluate(pv, trades, logs), 812345)
    text = "\n".join(lines)
    for name in ("G1", "G2", "G3", "G4", "G5", "G6"):
        assert name in text
    assert "812345" in text
    assert text.count("PASS") >= 6
    assert "ALL SIX PASS" in text


def test_a_failing_report_says_it_is_not_re_tuned():
    pv, trades, _ = _clean_run(eb_annual=0.10, spy_annual=0.10)
    text = "\n".join(gate.render(gate.evaluate(pv, trades, []), 1))
    assert "FAIL" in text
    assert "DISABLED" in text
    assert "re-tune" in text


def test_warnings_are_printed_not_swallowed():
    pv, trades, _ = _clean_run()
    result = gate.evaluate(pv, trades, ["ok"], logs_complete=False)
    assert any("WARNING" in line for line in gate.render(result, 7))


# ------------------------------------------------------------------ sources
def test_api_payload_is_unpacked_into_the_three_series():
    stamps = _months(58)
    payload = {"portfolio_value_history":
               _pv(stamps, _compound(stamps, 0.20), _compound(stamps, 0.10, 400.0)),
               "backtest_trades": _trades(3, 100.0, stamps)}
    logs = {"source": "file", "logs": ["a", "b"]}
    pv, trades, lines, complete = gate.unpack_api(payload, logs)
    assert len(pv) == 58 and len(trades) == 3 and lines == ["a", "b"]
    assert complete is True


def test_db_sourced_logs_are_reported_as_incomplete():
    _, _, _, complete = gate.unpack_api({"portfolio_value_history": [{}]},
                                        {"source": "db", "logs": ["a"]})
    assert complete is False


def test_postgres_step_rows_prefer_the_finalized_generation():
    """assemble() lets final rows supersede the live ones; so must the fallback."""
    rows = [
        ("pv", 1, False, {"timestamp": "2021-11-28T00:00:00Z", "value": 1.0}),
        ("pv", 1, True, {"timestamp": "2021-11-28T00:00:00Z", "value": 2.0}),
        ("trade", 1, False, {"total": 5.0}),
        ("log", 1, True, "hello"),
    ]
    pv, trades, lines = gate.unpack_steps(rows)
    assert [row["value"] for row in pv] == [2.0]
    assert [t["total"] for t in trades] == [5.0]
    assert lines == ["hello"]


def test_postgres_step_rows_keep_sequence_order():
    rows = [("log", 2, True, "second"), ("log", 1, True, "first")]
    assert gate.unpack_steps(rows)[2] == ["first", "second"]


def test_the_seq_zero_finalized_marker_is_skipped():
    """finalize_steps writes seq=0 with a JSON null before the entries. Letting
    it through reaches _ts() as None["timestamp"]."""
    rows = [
        ("pv", 0, True, None),
        ("pv", 1, True, {"timestamp": "2021-11-28T00:00:00Z", "value": 1.0}),
        ("trade", 0, True, None),
        ("log", 0, True, None),
    ]
    pv, trades, lines = gate.unpack_steps(rows)
    assert [row["value"] for row in pv] == [1.0]
    assert trades == [] and lines == []


def test_a_finalized_empty_kind_reads_as_empty_not_as_the_live_rows():
    """seq=0 alone means "finalized with zero entries"; the live rows it
    supersedes must not come back instead."""
    rows = [("trade", 0, True, None), ("trade", 1, False, {"total": 9.0})]
    assert gate.unpack_steps(rows)[1] == []


def test_the_postgres_read_is_read_only_and_carries_no_credential():
    assert "default_transaction_read_only=on" in gate.PG_READ_ONLY_OPTIONS


def test_fetch_pg_selects_the_three_kinds_and_unpacks_them():
    """No database: the connection is a recorder."""
    executed = []

    class _Cursor:
        def execute(self, sql, params=None):
            executed.append((sql, params))

        def fetchall(self):
            return [("pv", 0, True, None),
                    ("pv", 1, True, {"timestamp": "2021-11-28T00:00:00Z",
                                     "value": 6000.0, "cash": 0.0,
                                     "prices": {"SPY": 400.0},
                                     "positions_snapshot": {}}),
                    ("log", 1, True, "tick ok")]

        def close(self):
            executed.append(("close-cursor", None))

    class _Conn:
        def cursor(self):
            return _Cursor()

        def close(self):
            executed.append(("close-conn", None))

    pv, trades, lines, complete = gate.fetch_pg(812345, connect=_Conn)
    sql, params = executed[0]
    assert '"BacktestSteps"' in sql and "'pv', 'trade', 'log'" in sql
    assert params == ("812345",)
    assert [row["value"] for row in pv] == [6000.0]
    assert trades == [] and lines == ["tick ok"] and complete is True
    assert ("close-conn", None) in executed


def test_fetch_pg_says_so_when_the_run_has_no_rows():
    class _Cursor:
        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return []

        def close(self):
            pass

    class _Conn:
        def cursor(self):
            return _Cursor()

        def close(self):
            pass

    with pytest.raises(gate.GateError, match="no BacktestSteps rows"):
        gate.fetch_pg(1, connect=_Conn)


# ------------------------------------------------------------------ self-test
def test_the_self_test_runs_offline_and_agrees_with_its_constructions(capsys):
    assert gate.self_test() == 0
    out = capsys.readouterr().out
    assert "ALL SIX PASS" in out and "GATE FAILED" in out
    assert "SELF-TEST OK" in out


def test_main_routes_self_test_without_a_backtest_id(capsys):
    assert gate.main(["--self-test"]) == 0
    assert "SELF-TEST OK" in capsys.readouterr().out


def test_main_refuses_a_bare_invocation_rather_than_guessing_an_id():
    with pytest.raises(SystemExit):
        gate.main([])
