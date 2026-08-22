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
    import sys
    from backend import llm_critical_guard, backtest_critical_abort, live_critical_abort
    importlib.reload(llm_critical_guard)
    importlib.reload(backtest_critical_abort)
    importlib.reload(live_critical_abort)
    # llm_utils imports llm_critical_guard via the bare name. If an earlier
    # test file loaded the module under the bare alias as well, the two
    # aliases are DISTINCT module objects with their own LLMCriticalFailure
    # class identity — pytest.raises won't match. Force the bare alias to
    # share the reloaded backend module so both resolve to the same class.
    sys.modules["llm_critical_guard"] = llm_critical_guard
    for _alias in ("llm_critical_guard", "backend.llm_critical_guard"):
        _mod = sys.modules.get(_alias)
        if _mod is not None and hasattr(_mod, "reset_state"):
            try:
                _mod.reset_state()
            except Exception:
                pass
    # The BacktestResults pause write goes through the split store now, so
    # patching _get_conn_and_r no longer cages it. Cage the seam itself: a
    # unit test must never touch a real database.
    backtest_critical_abort._write_backtest_pause_status = lambda *a, **kw: None
    yield


def test_e2e_backtest_pause_flow(monkeypatch):
    """Simulate the bt357345 scenario end-to-end, now ending in PAUSE (not abort)."""
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

    # 2. broker.py-style routing: backtest mode → backtest_critical_abort (now PAUSE)
    from backend import backtest_critical_abort

    discord_captured = {}
    def fake_enqueue(channel, content, embed):
        discord_captured["channel"] = channel
        discord_captured["content"] = content
        discord_captured["embed"] = embed

    fake_conn = MagicMock()
    fake_r = MagicMock()

    # Capture the BacktestResults update payload to verify the new pause status fields
    captured_results_update = {}

    def _record_update(backtest_id, payload):
        # The BacktestResults pause write now goes through the split-store
        # seam, not a ReQL chain — cage the seam and capture its payload.
        if isinstance(payload, dict) and payload.get("status") == "paused_llm_critical":
            captured_results_update.update(payload)

    with patch.object(backtest_critical_abort, "_write_backtest_pause_status",
                      side_effect=_record_update), \
         patch.object(backtest_critical_abort, "_get_conn_and_r",
                      return_value=(fake_conn, fake_r)), \
         patch.object(backtest_critical_abort, "_enqueue_discord",
                      side_effect=fake_enqueue), \
         patch.object(backtest_critical_abort, "_bs_restore",
                      return_value=({}, {}, None)), \
         patch.object(backtest_critical_abort, "_apply_restore",
                      return_value=None):
        # handle() must return normally — NO sys.exit call. If it did exit, the
        # test process would die before reaching the assertions below.
        backtest_critical_abort.handle(
            backtest_id="357345",
            instance_id="main",
            failure=failure,
        )

    # 3. Verify Discord payload — yellow pause styling, "paused" verbiage
    assert discord_captured["channel"] == "backtests"
    assert "PAUSED" in discord_captured["content"].upper() or "paused" in discord_captured["content"]
    assert "ABORT" not in discord_captured["content"].upper()
    assert "357345" in discord_captured["content"]
    assert "azure_403_blocked" in discord_captured["content"]
    assert discord_captured["embed"]["color"] == 0xF1C40F  # yellow (was 0xE74C3C red)
    assert "paused" in discord_captured["embed"]["title"].lower()
    embed_field_names = {f["name"] for f in discord_captured["embed"]["fields"]}
    assert "backtest_id" in embed_field_names
    assert "sample" in embed_field_names

    # 4. BacktestResults was updated with status='paused_llm_critical' + diagnostic fields
    assert captured_results_update.get("status") == "paused_llm_critical"
    assert captured_results_update.get("pause_reason_tag") == "azure_403_blocked"
    assert captured_results_update.get("pause_provider") == "azure"

    # 5. Skip-snapshot flag stays False — the pause flow does NOT corrupt the
    #    eventual end-of-run snapshot; the bar will re-execute cleanly on resume.
    assert backtest_critical_abort._skip_snapshot_persist is False


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
