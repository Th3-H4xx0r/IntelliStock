"""End-to-end critical-guard test: simulates a backtest worker that hits
4 consecutive Azure 403s, asserts the whole pipeline fires correctly:
  - _call_llm_with_critical_guard escalates after 3 retries
  - LLMCriticalFailure carries full attribution
  - backtest_critical_abort.handle is invokable with the failure
  - Discord embed is correctly shaped
  - persist_backtest_snapshot is no-op afterward
"""
import importlib
import time
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def _reset_modules():
    from backend import llm_critical_guard, backtest_critical_abort, live_critical_abort
    importlib.reload(llm_critical_guard)
    importlib.reload(backtest_critical_abort)
    importlib.reload(live_critical_abort)
    yield


def test_e2e_backtest_abort_flow(monkeypatch):
    """Simulate the bt357345 scenario end-to-end."""
    monkeypatch.setattr(time, "sleep", lambda s: None)  # skip backoff waits

    # Patch _call_with_capture to return Azure 403 blocked, 4 times
    from backend import llm_utils
    seq = iter([("", 403, "Your resource has been temporarily blocked because we detected unusual behavior.", None)] * 4)
    monkeypatch.setattr(llm_utils, "_call_with_capture",
                        lambda *a, **kw: next(seq))

    # 1. Call the wrapper as graph_nexus_analysis would
    from backend.llm_critical_guard import LLMCriticalFailure
    with pytest.raises(LLMCriticalFailure) as excinfo:
        llm_utils._call_llm_with_critical_guard(
            "azure", "k", "gpt-5.4-mini-MEDIUM", "p",
            attribution_keys={"backtest_id": "357345", "instance_id": "main",
                              "call_site": "overlay"},
        )

    failure = excinfo.value
    assert failure.class_tag == "azure_403_blocked"
    assert failure.attribution["backtest_id"] == "357345"
    assert len(failure.attempts) == 4

    # 2. broker.py-style routing: backtest mode → backtest_critical_abort
    from backend import backtest_critical_abort

    discord_captured = {}
    def fake_enqueue(channel, content, embed):
        discord_captured["channel"] = channel
        discord_captured["content"] = content
        discord_captured["embed"] = embed

    fake_conn = MagicMock()
    fake_r = MagicMock()

    with patch.object(backtest_critical_abort, "_get_conn_and_r",
                      return_value=(fake_conn, fake_r)), \
         patch.object(backtest_critical_abort, "_enqueue_discord",
                      side_effect=fake_enqueue):
        backtest_critical_abort.handle(
            backtest_id="357345",
            instance_id="main",
            failure=failure,
        )

    # 3. Verify Discord payload
    assert discord_captured["channel"] == "backtests"
    assert "BACKTEST ABORT" in discord_captured["content"]
    assert "357345" in discord_captured["content"]
    assert "azure_403_blocked" in discord_captured["content"]
    embed_field_names = {f["name"] for f in discord_captured["embed"]["fields"]}
    assert "backtest_id" in embed_field_names
    assert "sample" in embed_field_names

    # 4. Skip-snapshot flag is now set
    assert backtest_critical_abort._skip_snapshot_persist is True


def test_e2e_live_abort_flow(monkeypatch):
    """Simulate a live-mode critical hit end-to-end."""
    monkeypatch.setattr(time, "sleep", lambda s: None)

    from backend import llm_utils
    seq = iter([("", 401, '{"error":"invalid_api_key"}', None)] * 4)
    monkeypatch.setattr(llm_utils, "_call_with_capture",
                        lambda *a, **kw: next(seq))

    from backend.llm_critical_guard import LLMCriticalFailure
    with pytest.raises(LLMCriticalFailure) as excinfo:
        llm_utils._call_llm_with_critical_guard(
            "openai", "bad-key", "gpt-4", "p",
            attribution_keys={"instance_id": "main"},
        )

    failure = excinfo.value
    assert failure.class_tag == "auth_failure"

    # Route to live_critical_abort
    from backend import live_critical_abort
    halt_mock = MagicMock(return_value={"instances_halted": 1, "orders_canceled": 2, "errors": []})
    alert_mock = MagicMock()
    with patch.object(live_critical_abort, "_halt_live_trading", halt_mock), \
         patch.object(live_critical_abort, "_alert_strategy_error", alert_mock):
        live_critical_abort.handle(instance_id="main", failure=failure)

    halt_mock.assert_called_once()
    alert_mock.assert_called_once()
    assert "auth_failure" in alert_mock.call_args.kwargs.get("message", "")
