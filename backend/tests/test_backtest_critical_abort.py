import importlib
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def _reset_state():
    from backend import backtest_critical_abort, llm_critical_guard, backtest_bar_snapshot
    importlib.reload(llm_critical_guard)
    importlib.reload(backtest_bar_snapshot)
    importlib.reload(backtest_critical_abort)
    # The BacktestResults pause write goes through the split store now, so
    # patching _get_store no longer cages it. Cage the seam itself: a
    # unit test must never touch a real database.
    backtest_critical_abort._write_backtest_pause_status = lambda *a, **kw: None
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
    with patch.object(backtest_critical_abort, "_get_store", return_value=fake_r), \
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
    with patch.object(backtest_critical_abort, "_get_store", return_value=MagicMock()), \
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
    with patch.object(backtest_critical_abort, "_get_store", return_value=MagicMock()), \
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
    with patch.object(backtest_critical_abort, "_get_store", return_value=fake_r), \
         patch.object(backtest_critical_abort, "_enqueue_discord", return_value=None), \
         patch.object(backtest_critical_abort, "_bs_restore", return_value=({}, {}, None)), \
         patch.object(backtest_critical_abort, "_apply_restore", return_value=None):
        backtest_critical_abort.handle(
            backtest_id="357345", instance_id="main", failure=_make_failure(),
        )
    # Verify store.update("BacktestInstances", 357345, {"paused": True}) was
    # called — the store takes the table by name, not a ReQL chain.
    found = any(call.args and call.args[0] == "BacktestInstances"
                for call in fake_r.update.call_args_list)
    assert found, "BacktestInstances was never written to"


def test_handle_writes_paused_llm_critical_status_with_diagnostic_fields():
    from backend import backtest_critical_abort
    fake_conn = MagicMock()
    fake_r = MagicMock()
    captured_update = {}

    def _record_update(backtest_id, payload):
        # The BacktestResults pause write now goes through the split-store
        # seam, not a ReQL chain — cage the seam and capture its payload.
        if isinstance(payload, dict) and payload.get("status") == "paused_llm_critical":
            captured_update.update(payload)

    with patch.object(backtest_critical_abort, "_write_backtest_pause_status",
                      side_effect=_record_update), \
         patch.object(backtest_critical_abort, "_get_store", return_value=fake_r), \
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
    with patch.object(backtest_critical_abort, "_get_store", return_value=MagicMock()), \
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
    with patch.object(backtest_critical_abort, "_get_store", return_value=MagicMock()), \
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
    """BacktestResults PK is int, and the id must be passed as an int.

    Under RethinkDB a get('str') on an int-keyed table silently missed
    ({skipped: 1}, not an exception); the store raises instead, but the
    contract this pins is the same one: the handler must not hand a string
    to an int-keyed table."""
    from backend import backtest_critical_abort
    fake_conn = MagicMock()
    fake_r = MagicMock()
    captured_get_args = []

    # Capture the row id the handler hands the store for BacktestInstances.
    def _record_update(table, row_id, patch, *a, **k):
        captured_get_args.append(row_id)
        return {"replaced": 1}
    fake_r.update.side_effect = _record_update

    with patch.object(backtest_critical_abort, "_get_store", return_value=fake_r), \
         patch.object(backtest_critical_abort, "_enqueue_discord", return_value=None), \
         patch.object(backtest_critical_abort, "_bs_restore", return_value=({}, {}, None)), \
         patch.object(backtest_critical_abort, "_apply_restore", return_value=None):
        backtest_critical_abort.handle(
            backtest_id="357345", instance_id="main", failure=_make_failure(),
        )
    assert 357345 in captured_get_args, \
        f"int 357345 not in store row ids: {captured_get_args}"
    assert "357345" not in captured_get_args, \
        f"str '357345' leaked into store row ids: {captured_get_args}"


def test_handle_idempotent():
    from backend import backtest_critical_abort
    mock_apply = MagicMock()
    with patch.object(backtest_critical_abort, "_get_store", return_value=MagicMock()), \
         patch.object(backtest_critical_abort, "_enqueue_discord", return_value=None), \
         patch.object(backtest_critical_abort, "_bs_restore", return_value=({}, {}, None)), \
         patch.object(backtest_critical_abort, "_apply_restore", mock_apply):
        backtest_critical_abort.handle(backtest_id="357345", instance_id="main", failure=_make_failure())
        backtest_critical_abort.handle(backtest_id="357345", instance_id="main", failure=_make_failure())
    assert mock_apply.call_count == 1


def test_cleared_pause_fields_nulls_all_pause_metadata():
    """On resume the broker must clear every pause_* field handle() wrote, so a
    finished run doesn't carry misleading pause metadata. The set must mirror
    handle()'s payload (minus status, which resume sets separately)."""
    from backend.backtest_critical_abort import cleared_pause_fields
    cleared = cleared_pause_fields()
    expected = {
        "pause_reason_tag", "pause_reason_text", "pause_provider", "pause_model",
        "pause_call_site", "pause_attempts", "pause_bar_time", "pause_sample",
        "paused_at",
    }
    assert set(cleared) == expected
    assert all(v is None for v in cleared.values())
    assert "status" not in cleared
