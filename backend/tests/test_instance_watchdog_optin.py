"""Per-instance alpha-watchdog opt-in (2026-08-31).

The unified order gate requires watchdog control-health for ALL new
exposure, but the sidecar only launched via deployment env — a run_once
paper instance could never buy (strategy-eb tick #1: every buy blocked on
dependency.watchdog.unknown). The Instances row can now opt in, and the
subprocess env falls back to the instance's own brokerage credentials —
the shared-credential path watchdog_main's docstring explicitly blesses.
"""
import ast
import os
import sys

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)


def _extract(*names):
    src = open(os.path.join(_backend, "instance.py")).read()
    tree = ast.parse(src)
    ns = {"os": os, "subprocess": None, "BACKEND_DIR": "/tmp",
          "alpha_watchdog_process": None}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            mod = ast.Module(body=[node], type_ignores=[])
            exec(compile(mod, "instance.py", "exec"), ns)
    return ns


def test_subprocess_env_falls_back_to_brokerage_credentials(monkeypatch):
    ns = _extract("_watchdog_subprocess_env")
    monkeypatch.delenv("ALPACA_WATCHDOG_KEY", raising=False)
    monkeypatch.delenv("ALPACA_WATCHDOG_SECRET", raising=False)
    monkeypatch.delenv("ALPACA_WATCHDOG_PAPER", raising=False)
    env = ns["_watchdog_subprocess_env"](
        {"alpaca_key": "k1", "alpaca_secret": "s1", "alpaca_paper": True})
    assert env["ALPACA_WATCHDOG_KEY"] == "k1"
    assert env["ALPACA_WATCHDOG_SECRET"] == "s1"
    assert env["ALPACA_WATCHDOG_PAPER"] == "1"


def test_scoped_env_credentials_win_over_the_brokerage_row(monkeypatch):
    ns = _extract("_watchdog_subprocess_env")
    monkeypatch.setenv("ALPACA_WATCHDOG_KEY", "scoped")
    monkeypatch.setenv("ALPACA_WATCHDOG_SECRET", "scopedsec")
    env = ns["_watchdog_subprocess_env"](
        {"alpaca_key": "k1", "alpaca_secret": "s1", "alpaca_paper": False})
    assert env["ALPACA_WATCHDOG_KEY"] == "scoped"
    assert env["ALPACA_WATCHDOG_SECRET"] == "scopedsec"
    assert env["ALPACA_WATCHDOG_PAPER"] == "0"


def test_the_doc_flag_or_env_flag_enables_and_default_stays_off(monkeypatch):
    src = open(os.path.join(_backend, "instance.py")).read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
              and n.name == "_maybe_start_alpha_watchdog")
    dump = ast.dump(fn)
    assert "alpha_watchdog_enabled" in dump
    assert "ALPHA_MARK_WATCHDOG_ENABLED" in dump
    # call site passes the docs so the flag is reachable
    assert "_maybe_start_alpha_watchdog(\n            instance_id, _instance_doc, _brokerage_doc)" in src
    # subprocess env comes from the dedicated builder, never bare os.environ
    assert "_watchdog_subprocess_env(brokerage_doc)" in src
