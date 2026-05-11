"""Smoke test for live_kill_switch."""
import os
import sys

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)


def test_module_imports():
    import live_kill_switch as lks
    assert callable(lks.halt_live_trading)
    assert callable(lks.main)


def test_main_accepts_reason_arg(monkeypatch, capsys):
    import live_kill_switch as lks

    calls = {}

    def fake_halt(reason="manual halt", cancel_open_orders=True):
        calls["reason"] = reason
        return {"instances_halted": 0, "orders_canceled": 0, "errors": []}

    monkeypatch.setattr(lks, "halt_live_trading", fake_halt)
    rc = lks.main(["risk-breach"])
    assert rc == 0
    assert calls["reason"] == "risk-breach"
    out = capsys.readouterr().out
    assert "HALT SUMMARY" in out
