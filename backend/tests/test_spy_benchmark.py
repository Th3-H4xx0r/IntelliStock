"""spy_benchmark must refuse thin series and read fills, not the monitor stream."""
import importlib.util
import os

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_spec = importlib.util.spec_from_file_location(
    "spy_benchmark", os.path.join(_root, "scripts", "spy_benchmark.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _fill(sym, price, date):
    return (f"[BROKER] FILL BUY {sym} qty=1.0 cumulative=1.0 price={price} "
            f"side=buy quote={date} 10:00:00")


def _quote(sym, px, date):
    """A `benchmark_quote_logging_enabled` line (broker.py:13714)."""
    return f"[BROKER] BENCHMARK QUOTE: {sym} {date} 15:00 {px}"


def test_extracts_dated_spy_points_only():
    lines = [_fill("SPY", "100.00", "2026-01-02"),
             _fill("AAPL", "200.00", "2026-01-02"),
             _fill("SPY", "110.00", "2026-02-26")]
    pts, source = _mod.spy_points(lines)
    assert pts == [("2026-01-02", 100.0), ("2026-02-26", 110.0)]
    assert source == "fills"


def test_deduplicates_by_date():
    lines = [_fill("SPY", "100.00", "2026-01-02"),
             _fill("SPY", "101.00", "2026-01-02")]
    assert len(_mod.spy_points(lines)[0]) == 1


def test_tick_quotes_win_over_fills():
    """A fill series samples only the days the core lane happened to trade —
    four points for bt 523085. When the tick-logged quote is present it is the
    benchmark, and the fills are ignored entirely."""
    lines = [_fill("SPY", "100.00", "2026-01-02"),
             _quote("SPY", "700.0000", "2026-01-02"),
             _quote("QQQ", "500.0000", "2026-01-02"),
             _quote("SPY", "770.0000", "2026-02-26")]
    pts, source = _mod.spy_points(lines)
    assert source == "tick quotes"
    assert pts == [("2026-01-02", 700.0), ("2026-02-26", 770.0)]


def test_a_named_symbol_selects_its_own_quotes():
    lines = [_quote("SPY", "700.0000", "2026-01-02"),
             _quote("QQQ", "500.0000", "2026-01-02"),
             _quote("QQQ", "550.0000", "2026-02-26")]
    pts, source = _mod.spy_points(lines, "QQQ")
    assert source == "tick quotes"
    assert pts == [("2026-01-02", 500.0), ("2026-02-26", 550.0)]


def test_refuses_a_series_too_thin_to_be_a_benchmark(capsys, tmp_path):
    """spy_series returned FOUR points for bt 523085; four is not a benchmark."""
    p = tmp_path / "l.log"
    p.write_text(_fill("SPY", "100.00", "2026-01-02") + "\n"
                 + _fill("SPY", "101.00", "2026-01-03"))
    assert _mod.main(["123", "--log", str(p)]) == 2
    assert "REFUSING" in capsys.readouterr().out


def test_reports_span_and_verdict(capsys, tmp_path):
    p = tmp_path / "l.log"
    p.write_text("\n".join(_fill("SPY", f"{100 + i}.00", f"2026-01-0{i + 1}")
                            for i in range(5)))
    assert _mod.main(["123", "--log", str(p), "--return", "6.0"]) == 0
    out = capsys.readouterr().out
    assert "2026-01-01 -> 2026-01-05" in out
    assert "NOISE" in out and "CHECK THE SPAN" in out
