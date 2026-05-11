"""Tests for the 2026-05-07 Connection: close defense.

Bug context: a single ``requests.Session`` was shared across the
strategy thread, the 60s fills-polling thread, the snapshot daemon
(3s), and the changefeed watcher. urllib3's keep-alive connection
pool persists handed-out connections across calls. If one thread's
HTTP call wedged on a half-closed TCP connection, the next thread
to call ANY Robinhood endpoint could pick that wedged connection
out of the pool and inherit the wedge.

The fix: force ``Connection: close`` on the shared session so
keep-alive is disabled. Cost: one TLS handshake per request (~50ms).
Benefit: a wedged HTTP call in one thread cannot poison a different
thread's call.

Tests:
1. By default the patched header is set.
2. The env override (``RH_DISABLE_KEEP_ALIVE=false``) skips it for
   environments that need keep-alive amortization (e.g. tight test
   loops where TLS handshake cost matters more than safety).
"""

from __future__ import annotations

import os
import sys

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def test_default_session_has_connection_close_header(monkeypatch):
    monkeypatch.delenv("RH_DISABLE_KEEP_ALIVE", raising=False)
    from robinhood_engine import RobinhoodClient

    client = RobinhoodClient()
    # Header keys are case-insensitive in requests; both forms work.
    assert client.session.headers.get("Connection") == "close"


def test_explicit_true_keeps_connection_close(monkeypatch):
    monkeypatch.setenv("RH_DISABLE_KEEP_ALIVE", "true")
    from robinhood_engine import RobinhoodClient

    client = RobinhoodClient()
    assert client.session.headers.get("Connection") == "close"


def test_explicit_false_disables_connection_close(monkeypatch):
    monkeypatch.setenv("RH_DISABLE_KEEP_ALIVE", "false")
    from robinhood_engine import RobinhoodClient

    client = RobinhoodClient()
    # Header should not be the close-forcing value when env opts out.
    assert client.session.headers.get("Connection") != "close"
