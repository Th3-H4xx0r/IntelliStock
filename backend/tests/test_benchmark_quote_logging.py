"""Make the SPY benchmark independent of whether the core lane traded.

Every SPY comparison published before 2026-08-14 was built from SPY *fills*, so
the series existed only when the core lane happened to trade: 4 points for
bt 523085, 11 for bt 873929, and 4 points spanning 9 days for the bear window
that was reported as a +26.13pp regime win. Only W3 ever had a series covering
its window, and that one is a 10.14pp loss.
"""
import ast
import os
import re
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_src = open(os.path.join(_backend, "broker.py"), encoding="utf-8").read()
_tree = ast.parse(_src)
_ns = {"_core_sleeve_cfg_raw": lambda specs: (
    (specs or [{}])[0].get("config") if isinstance(specs, list) and specs
    and isinstance(specs[0], dict) else None)}
for _n in _tree.body:
    if isinstance(_n, ast.FunctionDef) and _n.name == "_benchmark_quote_logging":
        exec(compile(ast.Module(body=[_n], type_ignores=[]), "broker.py", "exec"), _ns)
enabled = _ns["_benchmark_quote_logging"]


def _block():
    lines = _src.splitlines()
    i = next(k for k, l in enumerate(lines) if "if _benchmark_quote_logging(" in l)
    indent = len(lines[i]) - len(lines[i].lstrip())
    out = [lines[i]]
    for l in lines[i + 1:]:
        if l.strip() and (len(l) - len(l.lstrip())) <= indent:
            break
        out.append(l)
    return "\n".join(out)


def test_default_off_and_never_raises():
    assert enabled(None) is False
    assert enabled([]) is False
    assert enabled(object()) is False
    assert enabled([{"config": {}}]) is False


def test_reads_the_documented_key():
    assert enabled([{"config": {"benchmark_quote_logging_enabled": True}}]) is True


def test_logs_both_benchmarks():
    b = _block()
    assert '"SPY"' in b and '"QQQ"' in b, "QQQ matters for the SQQQ bear leg"


def test_emits_a_timestamp_so_the_span_is_checkable():
    """A series whose span is unknown cannot be differenced against a return."""
    b = _block()
    assert "%Y-%m-%d" in b, "each quote must carry its date"


def test_is_log_only():
    b = _block()
    for name in ("prices", "price_history", "run_once_results", "symbols_for_data"):
        assert re.search(rf"^\s*{name}\s*=(?!=)", b, re.M) is None, (
            f"benchmark logging must not assign {name}")
    assert "_log(" in b


def test_failure_is_loud_and_contained():
    b = _block()
    assert "BENCHMARK QUOTE ERROR" in b
    assert "except Exception" not in b, "bare except would hide a broken benchmark"


def test_runs_before_the_scoring_call():
    assert _src.index("if _benchmark_quote_logging(") < _src.index(
        "run_once_results = run_run_once_strategies(")
