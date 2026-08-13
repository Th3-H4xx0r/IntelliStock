"""Separate the surviving explanations for the empty history map (bt 278531).

2,922 breakout evaluations exited at `bars=0` with zero promotions, while 217 of the
skipped symbols had bars loaded into `data`. Three causes were advanced and falsified:
`breakout_min_history_bars` (all movers carry >=111 bars), widening to `data.keys()`
(empty lists are stored deliberately), and the discarding call sites (they handle
sleeve legs and sells, not discoveries).

These counts distinguish what is left:
  map << data              -> membership gap
  map ~= data, empty high  -> causal filter in get_price_history_up_to_current
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
    if isinstance(_n, ast.FunctionDef) and _n.name == "_price_history_diagnostics":
        exec(compile(ast.Module(body=[_n], type_ignores=[]), "broker.py", "exec"), _ns)
_enabled = _ns["_price_history_diagnostics"]


def _block():
    lines = _src.splitlines()
    i = next(k for k, l in enumerate(lines) if "if _price_history_diagnostics(" in l)
    indent = len(lines[i]) - len(lines[i].lstrip())
    out = [lines[i]]
    for l in lines[i + 1:]:
        if l.strip() and (len(l) - len(l.lstrip())) <= indent:
            break
        out.append(l)
    return "\n".join(out)


def test_default_off_and_never_raises():
    assert _enabled(None) is False
    assert _enabled([]) is False
    assert _enabled(object()) is False
    assert _enabled([{"config": {}}]) is False


def test_reads_the_documented_key():
    assert _enabled([{"config": {"price_history_diagnostics_enabled": True}}]) is True


def test_runs_before_the_scoring_call():
    """A diagnostic after the call would describe the wrong tick."""
    assert _src.index("if _price_history_diagnostics(") < _src.index(
        "run_once_results = run_run_once_strategies(")


def test_reports_every_count_needed_to_discriminate():
    b = _block()
    for field in ("map=", "empty=", "symbols_for_data=", "data=", "scored="):
        assert field in b, f"missing {field!r}: the counts must separate the hypotheses"


def test_is_log_only():
    b = _block()
    # anchor to statement start: the f-string legitimately contains
    # "symbols_for_data=" as literal report text, which is not an assignment
    for name in ("price_history", "symbols_for_data", "run_once_results", "prices"):
        assert re.search(rf"^\s*{name}\s*=(?!=)", b, re.M) is None, (
            f"must not assign {name}")
    assert "_log(" in b


def test_failure_is_loud():
    assert "PH DIAG ERROR" in _block()
