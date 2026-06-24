from types import SimpleNamespace

import pytest

from kalshi.runner import _run_with_crash_alert


def test_crash_alert_notifies_and_reraises():
    calls = []
    def fake_notify(**kw):
        calls.append(kw)
    def boom(cfg):
        raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError):
        _run_with_crash_alert(SimpleNamespace(instance_id="i1"), run=boom, notify=fake_notify)

    assert calls and calls[0]["category"] == "kalshi_runtime"
    assert calls[0]["instance_id"] == "i1"
    assert "kaboom" in calls[0]["body"]
    assert calls[0]["discord_channel"] == "notifications"


def test_no_notify_on_clean_return():
    calls = []
    _run_with_crash_alert(SimpleNamespace(instance_id="i"),
                          run=lambda c: None, notify=lambda **k: calls.append(k))
    assert calls == []


def test_keyboardinterrupt_reraised_without_notify():
    calls = []
    def ki(cfg):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        _run_with_crash_alert(SimpleNamespace(instance_id="i"),
                              run=ki, notify=lambda **k: calls.append(k))
    assert calls == []


def test_notify_failure_does_not_mask_original_error():
    def boom(cfg):
        raise ValueError("original")
    def bad_notify(**kw):
        raise RuntimeError("notify down")
    with pytest.raises(ValueError, match="original"):
        _run_with_crash_alert(SimpleNamespace(instance_id="i"), run=boom, notify=bad_notify)
