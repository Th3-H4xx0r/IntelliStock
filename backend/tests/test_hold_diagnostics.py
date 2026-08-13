"""The largest funnel stage must be observable (bt 873929).

84 of 103 names that moved >=30% never received a buy intent; 52 of those appear in
no scoring or queue line at all. The decision log prints `hold` without the score,
so a mover that scored 0.2 is indistinguishable from one that scored 1.49 against a
1.50 threshold. This telemetry is log-only and default OFF.
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
    if isinstance(_n, ast.FunctionDef) and _n.name == "_hold_diagnostics_enabled":
        exec(compile(ast.Module(body=[_n], type_ignores=[]), "broker.py", "exec"), _ns)
_enabled = _ns["_hold_diagnostics_enabled"]


def test_default_off_and_never_raises():
    assert _enabled(None) is False
    assert _enabled([]) is False
    assert _enabled(object()) is False
    assert _enabled([{"config": {}}]) is False


def test_reads_the_documented_key():
    assert _enabled([{"config": {"hold_diagnostics_enabled": True}}]) is True


def test_only_fires_on_hold():
    assert "if decision == 0 and _hold_diagnostics_enabled(" in _src, (
        "diagnostics must not fire on buy/sell decisions")


def _diag_block():
    """Exactly the diagnostic block: the `if` line plus everything indented under it."""
    lines = _src.splitlines()
    i = next(k for k, l in enumerate(lines)
             if "if decision == 0 and _hold_diagnostics_enabled(" in l)
    indent = len(lines[i]) - len(lines[i].lstrip())
    out = [lines[i]]
    for l in lines[i + 1:]:
        if l.strip() and (len(l) - len(l.lstrip())) <= indent:
            break
        out.append(l)
    return "\n".join(out)


def test_is_log_only_and_cannot_alter_the_decision():
    body = _diag_block()
    # assignment, not comparison: `decision == 0` is the guard and is fine
    for name in ("decision", "action", "_trade_action_intent"):
        assert re.search(rf"\b{name}\s*=(?!=)", body) is None, (
            f"telemetry must not assign to {name}")
    for forbidden in ("_trade_intents.append", "return ", "raise "):
        assert forbidden not in body, f"telemetry must not alter control flow: {forbidden!r}"
    assert body.count("_log(") >= 1


def test_reads_votes_as_a_list_not_a_dict():
    """bt 511709: 1,625 HOLD DIAG ERRORs — weighted_scores is list[(weight, vote)]."""
    body = _diag_block()
    assert ".items()" not in body, "weighted_scores is a list, not a mapping"
    assert "for _w, _s in _votes" in body


def test_reports_absent_raw_score_explicitly():
    """The 52 movers with no scoring trace must read `raw=absent`, not be skipped."""
    assert '"absent"' in _diag_block()


def test_failure_is_loud_not_silent():
    assert "HOLD DIAG ERROR" in _diag_block(), (
        "a swallowed exception makes the telemetry silently inert, which is the "
        "exact failure bt 550605 cost a run to discover")
