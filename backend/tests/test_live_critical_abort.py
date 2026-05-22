# backend/tests/test_live_critical_abort.py
import importlib
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def _reset_state():
    from backend import live_critical_abort, llm_critical_guard
    importlib.reload(llm_critical_guard)
    importlib.reload(live_critical_abort)
    yield


def _make_failure():
    from backend.llm_critical_guard import LLMCriticalFailure
    return LLMCriticalFailure(
        class_tag="azure_403_blocked",
        provider="azure",
        model="gpt-5.4-mini-MEDIUM",
        attribution={"instance_id": "main"},
        attempts=[
            {"attempt": 1, "class_tag": "azure_403_blocked", "http_status": 403,
             "body_sample": "HTTP 403: 'temporarily blocked'", "ts": 1716370585.0},
        ],
    )


def test_handle_calls_halt_live_trading():
    from backend import live_critical_abort
    mock_halt = MagicMock(return_value={"instances_halted": 1, "orders_canceled": 0, "errors": []})
    mock_alert = MagicMock()
    with patch.object(live_critical_abort, "_halt_live_trading", mock_halt), \
         patch.object(live_critical_abort, "_alert_strategy_error", mock_alert):
        live_critical_abort.handle(instance_id="main", failure=_make_failure())
    mock_halt.assert_called_once()
    reason_arg = mock_halt.call_args.kwargs.get("reason") or mock_halt.call_args.args[0]
    assert "azure_403_blocked" in reason_arg


def test_handle_calls_alert_strategy_error():
    from backend import live_critical_abort
    mock_halt = MagicMock(return_value={"instances_halted": 1, "orders_canceled": 0, "errors": []})
    mock_alert = MagicMock()
    with patch.object(live_critical_abort, "_halt_live_trading", mock_halt), \
         patch.object(live_critical_abort, "_alert_strategy_error", mock_alert):
        live_critical_abort.handle(instance_id="main", failure=_make_failure())
    mock_alert.assert_called_once()
    kw = mock_alert.call_args.kwargs
    assert kw.get("tag") == "llm_critical"
    assert kw.get("instance_id") == "main"
    assert "azure_403_blocked" in kw.get("message", "")


def test_handle_idempotent():
    from backend import live_critical_abort
    mock_halt = MagicMock(return_value={"instances_halted": 1, "orders_canceled": 0, "errors": []})
    mock_alert = MagicMock()
    with patch.object(live_critical_abort, "_halt_live_trading", mock_halt), \
         patch.object(live_critical_abort, "_alert_strategy_error", mock_alert):
        live_critical_abort.handle(instance_id="main", failure=_make_failure())
        live_critical_abort.handle(instance_id="main", failure=_make_failure())
    # Each isolated function called once despite handle() being called twice.
    assert mock_halt.call_count == 1
    assert mock_alert.call_count == 1


def test_handle_survives_halt_failure():
    """If halt_live_trading raises, alert still fires and handle() doesn't crash."""
    from backend import live_critical_abort
    mock_halt = MagicMock(side_effect=Exception("connection lost"))
    mock_alert = MagicMock()
    with patch.object(live_critical_abort, "_halt_live_trading", mock_halt), \
         patch.object(live_critical_abort, "_alert_strategy_error", mock_alert):
        # Should NOT raise
        live_critical_abort.handle(instance_id="main", failure=_make_failure())
    mock_alert.assert_called_once()  # alert still fires
