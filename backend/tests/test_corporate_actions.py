"""Authoritative split lookup (2026-08-02). See backend/corporate_actions.py.

The module must FAIL OPEN: no token, no network, or an API change all degrade
to inference-only behaviour. A corporate-actions lookup can never be allowed to
stop trading.
"""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import corporate_actions as ca  # noqa: E402


def setup_function(_fn):
    ca.reset_cache()


def test_no_token_fails_open(monkeypatch):
    monkeypatch.delenv("BENZINGA_API_KEY", raising=False)
    monkeypatch.delenv("BENZINGA_TOKEN", raising=False)
    assert ca.split_multiplier_for("VGT", "2026-04-21") is None


def test_fetch_exception_fails_open(monkeypatch):
    monkeypatch.setenv("BENZINGA_API_KEY", "x")
    import benzinga_client
    monkeypatch.setattr(benzinga_client, "_fetch_splits_raw",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ca.split_multiplier_for("VGT", "2026-04-21") is None


def _stub(monkeypatch, records):
    monkeypatch.setenv("BENZINGA_API_KEY", "x")
    import benzinga_client
    monkeypatch.setattr(benzinga_client, "_fetch_splits_raw", lambda *a, **k: records)


def test_exact_date_match(monkeypatch):
    _stub(monkeypatch, [{"ticker": "VGT", "date": "2026-04-21", "share_multiplier": 8.0}])
    assert ca.split_multiplier_for("VGT", "2026-04-21") == 8.0


def test_near_date_match_within_window(monkeypatch):
    """An unadjusted feed can show the step a bar or two off the ex-date."""
    _stub(monkeypatch, [{"ticker": "VGT", "date": "2026-04-21", "share_multiplier": 8.0}])
    assert ca.split_multiplier_for("VGT", "2026-04-23") == 8.0


def test_far_date_does_not_match(monkeypatch):
    _stub(monkeypatch, [{"ticker": "VGT", "date": "2026-04-21", "share_multiplier": 8.0}])
    assert ca.split_multiplier_for("VGT", "2026-06-01") is None


def test_other_ticker_does_not_match(monkeypatch):
    _stub(monkeypatch, [{"ticker": "VGT", "date": "2026-04-21", "share_multiplier": 8.0}])
    assert ca.split_multiplier_for("NVDA", "2026-04-21") is None


def test_reverse_split_multiplier(monkeypatch):
    _stub(monkeypatch, [{"ticker": "ABC", "date": "2026-04-21", "share_multiplier": 0.1}])
    assert ca.split_multiplier_for("ABC", "2026-04-21") == 0.1


def test_malformed_records_are_skipped(monkeypatch):
    _stub(monkeypatch, [
        {"ticker": "", "date": "2026-04-21", "share_multiplier": 8.0},
        {"ticker": "AAA", "date": "", "share_multiplier": 8.0},
        {"ticker": "BBB", "date": "2026-04-21", "share_multiplier": None},
        {"ticker": "CCC", "date": "2026-04-21", "share_multiplier": "x"},
        {"ticker": "DDD", "date": "2026-04-21", "share_multiplier": 1.0},   # no-op
        {"ticker": "GOOD", "date": "2026-04-21", "share_multiplier": 4.0},
    ])
    for t in ("AAA", "BBB", "CCC", "DDD"):
        assert ca.split_multiplier_for(t, "2026-04-21") is None
    assert ca.split_multiplier_for("GOOD", "2026-04-21") == 4.0


def test_blank_inputs_are_safe(monkeypatch):
    _stub(monkeypatch, [])
    assert ca.split_multiplier_for("", "2026-04-21") is None
    assert ca.split_multiplier_for("VGT", None) is None
    assert ca.split_multiplier_for("VGT", "not-a-date") is None
