import importlib
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def _reset_state():
    from backend import backtest_critical_abort, llm_critical_guard, backtest_bar_snapshot
    importlib.reload(llm_critical_guard)
    importlib.reload(backtest_bar_snapshot)
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


def test_handle_does_not_exit():
    """handle() must NOT call sys.exit (the whole point of the redesign)."""
    from backend import backtest_critical_abort
    fake_conn = MagicMock()
    fake_r = MagicMock()
    with patch.object(backtest_critical_abort, "_get_conn_and_r", return_value=(fake_conn, fake_r)), \
         patch.object(backtest_critical_abort, "_enqueue_discord", return_value=None), \
         patch.object(backtest_critical_abort, "_bs_restore", return_value=({"x": 1}, {}, None)), \
         patch.object(backtest_critical_abort, "_apply_restore", return_value=None), \
         patch("sys.exit", side_effect=AssertionError("sys.exit must NOT be called")):
        # Should complete without raising AssertionError
        backtest_critical_abort.handle(
            backtest_id="357345", instance_id="main", failure=_make_failure(),
        )


def test_handle_calls_snapshot_restore():
    from backend import backtest_critical_abort
    mock_restore = MagicMock(return_value=({"strategy_state": "good"}, {"cash": 100}, None))
    mock_apply = MagicMock()
    with patch.object(backtest_critical_abort, "_get_conn_and_r", return_value=(MagicMock(), MagicMock())), \
         patch.object(backtest_critical_abort, "_enqueue_discord", return_value=None), \
         patch.object(backtest_critical_abort, "_bs_restore", mock_restore), \
         patch.object(backtest_critical_abort, "_apply_restore", mock_apply):
        backtest_critical_abort.handle(
            backtest_id="357345", instance_id="main", failure=_make_failure(),
        )
    mock_restore.assert_called_once()
    mock_apply.assert_called_once()
    # apply must receive the restored values
    apply_kwargs = mock_apply.call_args.kwargs
    assert apply_kwargs.get("strategy_caches") == {"strategy_state": "good"}
    assert apply_kwargs.get("portfolio_emulator") == {"cash": 100}


def test_handle_survives_no_snapshot():
    """If no snapshot was ever captured (critical fires before first bar), pause still works."""
    from backend import backtest_critical_abort
    with patch.object(backtest_critical_abort, "_get_conn_and_r", return_value=(MagicMock(), MagicMock())), \
         patch.object(backtest_critical_abort, "_enqueue_discord", return_value=None), \
         patch.object(backtest_critical_abort, "_bs_restore", return_value=None):
        # Should NOT raise even though restore returned None
        backtest_critical_abort.handle(
            backtest_id="357345", instance_id="main", failure=_make_failure(),
        )


def test_handle_writes_paused_true_to_backtestinstances():
    from backend import backtest_critical_abort
    fake_conn = MagicMock()
    fake_r = MagicMock()
    with patch.object(backtest_critical_abort, "_get_conn_and_r", return_value=(fake_conn, fake_r)), \
         patch.object(backtest_critical_abort, "_enqueue_discord", return_value=None), \
         patch.object(backtest_critical_abort, "_bs_restore", return_value=({}, {}, None)), \
         patch.object(backtest_critical_abort, "_apply_restore", return_value=None):
        backtest_critical_abort.handle(
            backtest_id="357345", instance_id="main", failure=_make_failure(),
        )
    # Verify .table("BacktestInstances").get(357345).update({"paused": True})
    # was called somewhere in the call chain — inspect mock call args
    found = False
    for call in fake_r.db.return_value.table.call_args_list:
        if call.args and call.args[0] == "BacktestInstances":
            found = True
    assert found, "BacktestInstances was never written to"


def test_handle_writes_paused_llm_critical_status_with_diagnostic_fields():
    from backend import backtest_critical_abort
    fake_conn = MagicMock()
    fake_r = MagicMock()
    captured_update = {}

    def _record_update(payload):
        # Each .update() call in the BacktestResults chain — capture the payload
        if isinstance(payload, dict) and payload.get("status") == "paused_llm_critical":
            captured_update.update(payload)
        return fake_r  # chainable

    fake_r.db.return_value.table.return_value.get.return_value.update.side_effect = _record_update

    with patch.object(backtest_critical_abort, "_get_conn_and_r", return_value=(fake_conn, fake_r)), \
         patch.object(backtest_critical_abort, "_enqueue_discord", return_value=None), \
         patch.object(backtest_critical_abort, "_bs_restore", return_value=({}, {}, None)), \
         patch.object(backtest_critical_abort, "_apply_restore", return_value=None):
        backtest_critical_abort.handle(
            backtest_id="357345", instance_id="main", failure=_make_failure(),
        )
    assert captured_update.get("status") == "paused_llm_critical"
    assert captured_update.get("pause_reason_tag") == "azure_403_blocked"
    assert captured_update.get("pause_provider") == "azure"
    assert captured_update.get("pause_model") == "gpt-5.4-mini-MEDIUM"
    assert captured_update.get("pause_attempts") == 2
    assert "pause_sample" in captured_update


def test_handle_does_not_set_skip_snapshot_persist():
    """The old auto-abort behavior set _skip_snapshot_persist=True to prevent
    Phase 1's end-of-run snapshot from seeding live mode with corrupt state.
    The new pause behavior does NOT set this flag — the bar will rerun on
    resume; eventual completion is fine to snapshot."""
    from backend import backtest_critical_abort
    with patch.object(backtest_critical_abort, "_get_conn_and_r", return_value=(MagicMock(), MagicMock())), \
         patch.object(backtest_critical_abort, "_enqueue_discord", return_value=None), \
         patch.object(backtest_critical_abort, "_bs_restore", return_value=({}, {}, None)), \
         patch.object(backtest_critical_abort, "_apply_restore", return_value=None):
        backtest_critical_abort.handle(
            backtest_id="357345", instance_id="main", failure=_make_failure(),
        )
    assert backtest_critical_abort._skip_snapshot_persist is False


def test_handle_discord_yellow_pause_styling():
    from backend import backtest_critical_abort
    captured = {}
    def fake_enqueue(channel, content, embed):
        captured["channel"] = channel
        captured["content"] = content
        captured["embed"] = embed
    with patch.object(backtest_critical_abort, "_get_conn_and_r", return_value=(MagicMock(), MagicMock())), \
         patch.object(backtest_critical_abort, "_enqueue_discord", side_effect=fake_enqueue), \
         patch.object(backtest_critical_abort, "_bs_restore", return_value=({}, {}, None)), \
         patch.object(backtest_critical_abort, "_apply_restore", return_value=None):
        backtest_critical_abort.handle(
            backtest_id="357345", instance_id="main", failure=_make_failure(),
        )
    assert captured["channel"] == "backtests"
    assert "PAUSED" in captured["content"].upper() or "paused" in captured["content"]
    assert captured["embed"]["color"] == 0xF1C40F  # yellow
    assert "paused" in captured["embed"]["title"].lower()


def test_handle_uses_int_backtest_id_for_db_lookup():
    """BacktestResults PK is int; RethinkDB get('str') silently misses
    (returns {skipped: 1} not an exception). Every .get() call in the DB
    chain — both BacktestInstances and BacktestResults — must receive int."""
    from backend import backtest_critical_abort
    fake_conn = MagicMock()
    fake_r = MagicMock()
    captured_get_args = []

    # Capture every .get(...) arg in the table chain (covers both
    # BacktestInstances and BacktestResults — they share the same mock chain
    # because fake_r.db.return_value.table.return_value resolves identically
    # regardless of the table-name argument passed by the handler).
    def _record_get(arg):
        captured_get_args.append(arg)
        return fake_r.db.return_value.table.return_value.get.return_value
    fake_r.db.return_value.table.return_value.get.side_effect = _record_get

    with patch.object(backtest_critical_abort, "_get_conn_and_r", return_value=(fake_conn, fake_r)), \
         patch.object(backtest_critical_abort, "_enqueue_discord", return_value=None), \
         patch.object(backtest_critical_abort, "_bs_restore", return_value=({}, {}, None)), \
         patch.object(backtest_critical_abort, "_apply_restore", return_value=None):
        backtest_critical_abort.handle(
            backtest_id="357345", instance_id="main", failure=_make_failure(),
        )
    # Both BacktestInstances and BacktestResults .get() must use int (not str)
    assert 357345 in captured_get_args, \
        f"int 357345 not in get() args: {captured_get_args}"
    # Belt-and-suspenders: NO string '357345' should appear — that would mean
    # one of the two .get() calls is still using the str path.
    assert "357345" not in captured_get_args, \
        f"str '357345' leaked into get() args: {captured_get_args}"


def test_handle_idempotent():
    from backend import backtest_critical_abort
    mock_apply = MagicMock()
    with patch.object(backtest_critical_abort, "_get_conn_and_r", return_value=(MagicMock(), MagicMock())), \
         patch.object(backtest_critical_abort, "_enqueue_discord", return_value=None), \
         patch.object(backtest_critical_abort, "_bs_restore", return_value=({}, {}, None)), \
         patch.object(backtest_critical_abort, "_apply_restore", mock_apply):
        backtest_critical_abort.handle(backtest_id="357345", instance_id="main", failure=_make_failure())
        backtest_critical_abort.handle(backtest_id="357345", instance_id="main", failure=_make_failure())
    assert mock_apply.call_count == 1
