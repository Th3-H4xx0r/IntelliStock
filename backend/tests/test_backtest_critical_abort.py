# backend/tests/test_backtest_critical_abort.py
import importlib
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def _reset_state():
    from backend import backtest_critical_abort, llm_critical_guard
    importlib.reload(llm_critical_guard)
    importlib.reload(backtest_critical_abort)
    yield


def _make_failure():
    from backend.llm_critical_guard import LLMCriticalFailure
    return LLMCriticalFailure(
        class_tag="azure_403_blocked",
        provider="azure",
        model="gpt-5.4-mini-MEDIUM",
        attribution={"backtest_id": "357345", "instance_id": "main", "call_site": "overlay"},
        attempts=[
            {"attempt": 1, "class_tag": "azure_403_blocked", "http_status": 403,
             "body_sample": "HTTP 403: 'temporarily blocked'", "ts": 1716370585.0},
            {"attempt": 4, "class_tag": "azure_403_blocked", "http_status": 403,
             "body_sample": "HTTP 403: 'temporarily blocked'", "ts": 1716370592.0},
        ],
    )


def test_handle_updates_backtestresults_idempotent():
    from backend import backtest_critical_abort
    fake_conn = MagicMock()
    fake_r = MagicMock()

    with patch.object(backtest_critical_abort, "_get_conn_and_r", return_value=(fake_conn, fake_r)), \
         patch.object(backtest_critical_abort, "_enqueue_discord", return_value=None):
        backtest_critical_abort.handle(
            backtest_id="357345",
            instance_id="main",
            failure=_make_failure(),
        )
        # First call -> r.db().table().get().update() should be invoked once
        assert fake_r.db.called
        # Second call -> flagged as already-alerted, no second update
        backtest_critical_abort.handle(
            backtest_id="357345",
            instance_id="main",
            failure=_make_failure(),
        )
        assert backtest_critical_abort._already_alerted is True


def test_handle_sets_skip_snapshot_persist():
    from backend import backtest_critical_abort
    fake_conn = MagicMock()
    fake_r = MagicMock()
    with patch.object(backtest_critical_abort, "_get_conn_and_r", return_value=(fake_conn, fake_r)), \
         patch.object(backtest_critical_abort, "_enqueue_discord", return_value=None):
        assert backtest_critical_abort._skip_snapshot_persist is False
        backtest_critical_abort.handle(
            backtest_id="357345", instance_id="main", failure=_make_failure(),
        )
        assert backtest_critical_abort._skip_snapshot_persist is True


def test_handle_enqueues_discord():
    from backend import backtest_critical_abort
    captured = {}
    def fake_enqueue(channel, content, embed):
        captured["channel"] = channel
        captured["content"] = content
        captured["embed"] = embed

    with patch.object(backtest_critical_abort, "_get_conn_and_r", return_value=(MagicMock(), MagicMock())), \
         patch.object(backtest_critical_abort, "_enqueue_discord", side_effect=fake_enqueue):
        backtest_critical_abort.handle(
            backtest_id="357345", instance_id="main", failure=_make_failure(),
        )
    assert captured["channel"] == "backtests"
    assert "BACKTEST ABORT" in captured["content"]
    assert "azure_403_blocked" in captured["content"]
    embed = captured["embed"]
    # Required fields
    field_names = {f["name"] for f in embed["fields"]}
    assert {"backtest_id", "instance_id", "class", "provider", "model",
            "attempts", "sample", "next steps"}.issubset(field_names)


def test_handle_truncates_sample_text():
    from backend import backtest_critical_abort
    failure = _make_failure()
    failure.attempts[-1]["body_sample"] = "x" * 2000

    captured = {}
    def fake_enqueue(channel, content, embed):
        captured["embed"] = embed

    with patch.object(backtest_critical_abort, "_get_conn_and_r", return_value=(MagicMock(), MagicMock())), \
         patch.object(backtest_critical_abort, "_enqueue_discord", side_effect=fake_enqueue):
        backtest_critical_abort.handle(
            backtest_id="357345", instance_id="main", failure=failure,
        )

    sample_field = next(f for f in captured["embed"]["fields"] if f["name"] == "sample")
    assert len(sample_field["value"]) <= 600  # 500 + formatting overhead


def test_handle_handles_missing_attribution_keys():
    """If attribution dict is empty, handle still works (uses provided args)."""
    from backend import backtest_critical_abort
    failure = _make_failure()
    failure.attribution = {}

    with patch.object(backtest_critical_abort, "_get_conn_and_r", return_value=(MagicMock(), MagicMock())), \
         patch.object(backtest_critical_abort, "_enqueue_discord", return_value=None):
        # Should not raise
        backtest_critical_abort.handle(
            backtest_id="357345", instance_id="main", failure=failure,
        )
