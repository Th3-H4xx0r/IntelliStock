"""Halt-field hygiene on healthy boot (spec A5).

A halted equities instance keeps `halt_reason`/`halted_at` on its Instances row
until an operator clears it. instance.py's startup update — which only runs when
the server spawns the process for a row with runCommand=True (i.e. already
un-halted) — must clear those stale fields alongside the crash flags.

Side-effect safety: instance.py imports python-socketio at module load, so it is
stubbed before import (as in test_instance_crash_handling.py). Inside the test,
`inst.r` is replaced with a MagicMock and `get_conn`/`safe_close` are patched, so
no real RethinkDB connection is ever opened. Execution is stopped right after the
startup update (via a BaseException from a patched get_symbols_for_instance)
before any socket, alert, or crash-keepalive path can run.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("socketio", MagicMock())

import instance as inst  # noqa: E402


class _StopAfterUpdate(BaseException):
    """Halt run() immediately after the startup update.

    Subclasses BaseException (not Exception) so run()'s `except Exception` startup
    guard does not swallow it into the crash-keepalive path — the test wants the
    update payload, nothing downstream."""


def test_startup_update_clears_stale_halt_fields(monkeypatch):
    fake_r = MagicMock()
    monkeypatch.setattr(inst, "r", fake_r)
    monkeypatch.setattr(inst, "get_conn", lambda: MagicMock())
    monkeypatch.setattr(inst, "safe_close", lambda conn: None)
    monkeypatch.setattr(inst, "args_list", ["instance.py", "alpaca-main"])
    monkeypatch.setattr(inst.os, "system", lambda *a, **k: 0)

    def _stop(*a, **k):
        raise _StopAfterUpdate()

    # Stop right after the startup update, before socket/alert/keepalive paths.
    monkeypatch.setattr(inst, "get_symbols_for_instance", _stop)

    with pytest.raises(_StopAfterUpdate):
        inst.run()

    update = fake_r.db.return_value.table.return_value.get.return_value.update
    update.assert_called_once()
    payload = update.call_args[0][0]

    # Healthy-boot invariants preserved …
    assert payload["running"] is True
    assert payload["crashed"] is False
    assert payload["crashed_at"] is None
    # … plus the new halt-field hygiene.
    assert payload["halt_reason"] is None
    assert payload["halted_at"] is None
