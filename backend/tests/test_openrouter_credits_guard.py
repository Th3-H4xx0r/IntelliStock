"""Round-2 Task 4: OpenRouter preflight credit guard + 402 affordability de-cliff.

Two safety layers (see backend/openrouter_credits.py):

  1. PREFLIGHT GUARD — ``check_credit_guard`` reads the account balance and
     returns "ok"/"warn"/"halt" vs configured thresholds ($3 warn / $0.50 halt),
     warning at most ONCE per process, and FAILING OPEN (None balance → "ok")
     so a flaky credits endpoint never blocks trading on its own.

  2. 402 DE-CLIFF — ``_call_openrouter`` retries ONCE with a clamped max_tokens
     when a 402 body says "can only afford N"; a second 402 falls through to the
     normal terminal path (Task-2 classify → insufficient_credits). Exactly one
     telemetry row per HTTP call.

Fully caged: requests.get/.post mocked (the ONLY real network allowed by the
task is an optional manual curl to verify the endpoint shape, never in tests);
the live kill-switch + alert seams are stubbed; telemetry uses a null DB.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("socketio", MagicMock())
_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import openrouter_credits as oc  # noqa: E402


# ── Caging ───────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def cage(monkeypatch):
    """AUTOUSE cage. Resets the module's process-global latch/caches before
    every test, and stubs every production side effect the guard path could
    reach — the live kill switch, live/notification alert seams — so nothing
    escapes even if a test forgets to mock it. NO network: requests.get is
    replaced with a hard failure so any un-mocked balance fetch degrades to
    None rather than dialing OpenRouter."""
    oc.reset_state()

    import live_alerts
    import live_kill_switch
    import live_critical_abort
    import notifications

    def _boom(*a, **k):
        raise AssertionError("no test should reach live_kill_switch")

    monkeypatch.setattr(live_kill_switch, "halt_live_trading", _boom, raising=False)
    monkeypatch.setattr(live_critical_abort, "_halt_live_trading", _boom, raising=False)
    monkeypatch.setattr(live_alerts, "alert_strategy_error", lambda **k: None, raising=False)
    monkeypatch.setattr(live_alerts, "notify", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(notifications, "notify", lambda *a, **k: None, raising=False)

    def _no_net_get(*a, **k):
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr("requests.get", _no_net_get)
    yield
    oc.reset_state()


class _FakeGet:
    def __init__(self, *, status_code=200, payload=None, raise_exc=None, bad_json=False):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._raise = raise_exc
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


def _mock_get(**resp_kw):
    def _side(*a, **k):
        r = _FakeGet(**resp_kw)
        if r._raise is not None:
            raise r._raise
        return r
    return _side


def _credits_payload(total_credits, total_usage):
    return {"data": {"total_credits": total_credits, "total_usage": total_usage}}


# ── get_balance: math + None-on-error ────────────────────────────────────────
def test_get_balance_math():
    with patch("requests.get", side_effect=_mock_get(payload=_credits_payload(12.5, 4.25))):
        assert oc.get_balance("sk-or-x") == pytest.approx(8.25)
    # side effect: cached
    assert oc.get_cached_balance() == pytest.approx(8.25)


def test_get_balance_no_key_returns_none():
    assert oc.get_balance("") is None
    assert oc.get_balance(None) is None


def test_get_balance_timeout_returns_none():
    import requests
    with patch("requests.get", side_effect=_mock_get(raise_exc=requests.exceptions.Timeout())):
        assert oc.get_balance("sk-or-x") is None


def test_get_balance_http_500_returns_none():
    with patch("requests.get", side_effect=_mock_get(status_code=500, payload={})):
        assert oc.get_balance("sk-or-x") is None


def test_get_balance_bad_json_returns_none():
    with patch("requests.get", side_effect=_mock_get(bad_json=True)):
        assert oc.get_balance("sk-or-x") is None


def test_get_balance_missing_fields_returns_none():
    with patch("requests.get", side_effect=_mock_get(payload={"data": {}})):
        assert oc.get_balance("sk-or-x") is None


# ── check_credit_guard: thresholds + one-shot warn latch ─────────────────────
def _guard_at(balance, notify_fn=None, config=None):
    with patch("requests.get", side_effect=_mock_get(payload=_credits_payload(balance, 0.0))):
        return oc.check_credit_guard("sk-or-x", config or {}, notify_fn)


def test_guard_ok_above_warn():
    assert _guard_at(10.0) == "ok"


def test_guard_warn_fires_once_then_latches():
    seen = []
    assert _guard_at(2.9, notify_fn=lambda m: seen.append(m)) == "warn"
    assert len(seen) == 1  # warned once
    # Second call still classifies as warn, but the latch suppresses the page.
    assert _guard_at(2.9, notify_fn=lambda m: seen.append(m)) == "warn"
    assert len(seen) == 1  # NO second page


def test_guard_halt_at_low_balance():
    assert _guard_at(0.4) == "halt"


def test_guard_halt_takes_priority_over_warn():
    seen = []
    assert _guard_at(0.4, notify_fn=lambda m: seen.append(m)) == "halt"
    assert seen == []  # halt never pages the warn channel


def test_guard_none_balance_degrades_to_ok():
    """Fetch failure (None) must NOT block — degrade to reactive-402."""
    import requests
    with patch("requests.get", side_effect=_mock_get(raise_exc=requests.exceptions.Timeout())):
        assert oc.check_credit_guard("sk-or-x", {}, None) == "ok"


def test_guard_respects_configured_thresholds():
    cfg = {"openrouter_low_credit_warn_usd": 20.0, "openrouter_halt_credit_usd": 5.0}
    assert _guard_at(4.0, config=cfg) == "halt"
    oc.reset_state()
    assert _guard_at(15.0, config=cfg) == "warn"
    oc.reset_state()
    assert _guard_at(25.0, config=cfg) == "ok"


# ── pure de-cliff / pre-clamp helpers ────────────────────────────────────────
def test_parse_affordable_tokens():
    assert oc.parse_affordable_tokens(
        "This request requires more credits. You can only afford 10773 tokens."
    ) == 10773
    assert oc.parse_affordable_tokens("no number here") is None
    assert oc.parse_affordable_tokens("") is None
    assert oc.parse_affordable_tokens(None) is None


def test_decliff_max_tokens():
    assert oc.decliff_max_tokens(10773) == 10261  # 10773 - 512
    assert oc.decliff_max_tokens(100) == 2048     # floored


def test_preclamp_only_reduces():
    # Nothing cached → no-op.
    assert oc.preclamp_max_tokens(32768, None) == 32768
    # Cached budget clamps a large default down.
    assert oc.preclamp_max_tokens(32768, 10773) == 10261
    # Never RAISES a smaller explicit cap.
    assert oc.preclamp_max_tokens(4096, 10773) == 4096
    # Floor honoured.
    assert oc.preclamp_max_tokens(32768, 100) == 2048


# ── broker-wiring helpers ────────────────────────────────────────────────────
def test_find_openrouter_credentials_from_specs():
    specs = [
        {"config": {"sentiment_llm_provider": "openai", "sentiment_llm_api_key": "sk-oa"}},
        {"config": {"overlay_llm_provider": "openrouter", "overlay_llm_api_key": "sk-or-abc"}},
    ]
    found = oc.find_openrouter_credentials(specs)
    assert found is not None
    api_key, cfg = found
    assert api_key == "sk-or-abc"


def test_find_openrouter_credentials_none_when_absent():
    specs = [{"config": {"x_llm_provider": "nvidia", "x_llm_api_key": "k"}}]
    assert oc.find_openrouter_credentials(specs) is None
    # empty key with openrouter provider → not usable
    assert oc.find_openrouter_credentials(
        [{"config": {"a_llm_provider": "openrouter", "a_llm_api_key": ""}}]
    ) is None


def test_run_credit_guard_skip_when_no_openrouter():
    assert oc.run_credit_guard([{"config": {"m_llm_provider": "openai"}}]) == "skip"


def test_run_credit_guard_halt_flows_through():
    specs = [{"config": {"m_llm_provider": "openrouter", "m_llm_api_key": "sk-or-x"}}]
    with patch("requests.get", side_effect=_mock_get(payload=_credits_payload(0.10, 0.0))):
        assert oc.run_credit_guard(specs) == "halt"


# ── 402 de-cliff inside _call_openrouter ─────────────────────────────────────
@pytest.fixture
def telemetry_clean():
    import llm_telemetry
    llm_telemetry._reset_for_tests()
    llm_telemetry.configure(
        db_conn_factory=lambda: None, enabled=True,
        auto_start_flusher=False, pricing_yaml_path=None,
    )
    yield llm_telemetry
    llm_telemetry._reset_for_tests()


class _PostResp:
    def __init__(self, *, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = {}

    def json(self):
        return self._payload


_NEMOTRON = "nvidia/nemotron-3-ultra-550b-a55b"
_402_TEXT = ('{"error": {"message": "This request requires more credits. '
             'You can only afford 10773 tokens.", "code": 402}}')


def _ok_payload():
    return {"choices": [{"message": {"content": '{"text": "hi"}'}}],
            "usage": {"prompt_tokens": 80, "completion_tokens": 30, "cost": 0.004}}


def test_decliff_retries_clamped_and_records_two_rows(telemetry_clean):
    import llm_utils
    bodies = []

    def _fake_post(url, headers=None, json=None, timeout=None):
        bodies.append(dict(json or {}))
        if len(bodies) == 1:
            return _PostResp(status_code=402, payload={"error": {"code": 402}}, text=_402_TEXT)
        return _PostResp(payload=_ok_payload())

    with patch("requests.post", side_effect=_fake_post):
        out = llm_utils._call_openrouter("sk-or-x", _NEMOTRON, "decide", max_output_tokens=0)

    assert out == '{"text": "hi"}'
    # Two HTTP calls: the 402'd first, then the clamped retry.
    assert len(bodies) == 2
    assert bodies[0]["max_tokens"] == llm_utils._OPENROUTER_UNCAPPED_MAX_OUTPUT_TOKENS
    assert bodies[1]["max_tokens"] == 10261  # 10773 - 512
    # One row per HTTP call: 402 failure + clamped success.
    rows = telemetry_clean.get_recent_calls(10)
    assert len(rows) == 2
    ok_flags = sorted(r["ok"] for r in rows)
    assert ok_flags == [False, True]
    ok_row = [r for r in rows if r["ok"]][0]
    assert ok_row["total_cost_usd"] == pytest.approx(0.004)
    # The affordable budget is cached for later pre-clamps.
    assert oc.get_cached_affordable_tokens() == 10773


def test_double_402_is_terminal(telemetry_clean):
    import llm_utils
    calls = {"n": 0}

    def _fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        return _PostResp(status_code=402, payload={"error": {"code": 402}}, text=_402_TEXT)

    with patch("requests.post", side_effect=_fake_post):
        out = llm_utils._call_openrouter("sk-or-x", _NEMOTRON, "decide", max_output_tokens=0)

    assert out == ""                 # terminal
    assert calls["n"] == 2           # first 402 + one clamped retry, then stop
    # Task-2 classification sees a 402 stashed for this thread.
    import threading
    with llm_utils._LAST_HTTP_LOCK:
        stashed = llm_utils._LAST_HTTP_PER_THREAD.get(threading.get_ident())
    assert stashed is not None and stashed.get("status") == 402
    # Two HTTP calls → two failure rows.
    rows = telemetry_clean.get_recent_calls(10)
    assert len(rows) == 2
    assert all(r["ok"] is False for r in rows)


def test_no_decliff_when_402_has_no_affordable_hint(telemetry_clean):
    """A 402 without the 'can only afford N' phrase is NOT de-cliffed — it goes
    straight to terminal so we don't retry blindly."""
    import llm_utils
    calls = {"n": 0}

    def _fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        return _PostResp(status_code=402, payload={"error": {"code": 402}},
                         text='{"error": {"message": "Insufficient credits", "code": 402}}')

    with patch("requests.post", side_effect=_fake_post):
        out = llm_utils._call_openrouter("sk-or-x", _NEMOTRON, "decide", max_output_tokens=0)

    assert out == ""
    assert calls["n"] == 1  # no retry
    rows = telemetry_clean.get_recent_calls(10)
    assert len(rows) == 1
    assert rows[0]["ok"] is False


def test_preclamp_applies_cached_budget_before_wire(telemetry_clean):
    """After a 402 caches an affordable budget, the NEXT uncapped call is
    pre-clamped before it hits the wire."""
    import llm_utils
    oc.note_affordable_tokens(10773)
    bodies = []

    def _fake_post(url, headers=None, json=None, timeout=None):
        bodies.append(dict(json or {}))
        return _PostResp(payload=_ok_payload())

    with patch("requests.post", side_effect=_fake_post):
        out = llm_utils._call_openrouter("sk-or-x", _NEMOTRON, "decide", max_output_tokens=0)

    assert out == '{"text": "hi"}'
    assert len(bodies) == 1
    assert bodies[0]["max_tokens"] == 10261  # pre-clamped from uncapped
