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


def test_persist_backtest_snapshot_honors_skip_flag(monkeypatch):
    """When _skip_snapshot_persist is True, persist_backtest_snapshot is a no-op.

    Test kwargs match the actual signature of
    ``strategy_cache_persistence.persist_backtest_snapshot`` (instance_id,
    strategy_name, cache, config_hash, module_hash, start_date, end_date);
    the substantive assertion is that no DB calls happen when the flag is set.

    The guard inside ``persist_backtest_snapshot`` uses a bare import
    (``from backtest_critical_abort import _skip_snapshot_persist``) because
    production runs with ``backend/`` on ``sys.path``. Tests run from repo
    root, so we mirror that here by inserting backend/ on sys.path before
    flipping the flag.
    """
    import os
    import sys
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    backend_path = os.path.join(repo_root, "backend")
    sys_path_added = False
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
        sys_path_added = True
    try:
        # Import the production-import-path module reference (the one the
        # guard inside persist_backtest_snapshot will resolve to).
        import backtest_critical_abort as _bca_bare  # noqa: WPS433
        from backend import strategy_cache_persistence
        # Also alias via the `backend.` package path so tests that touch the
        # same module reference stay coherent.
        from backend import backtest_critical_abort as _bca_pkg

        _bca_bare._skip_snapshot_persist = True
        _bca_pkg._skip_snapshot_persist = True
        try:
            # If guard works, no DB calls happen. Pass stubs that would raise
            # if they were used.
            class _ExplodingConn:
                def __getattr__(self, name):
                    raise RuntimeError("guard didn't fire — conn was contacted")

            class _ExplodingR:
                def __getattr__(self, name):
                    raise RuntimeError("guard didn't fire — r-module was contacted")

            result = strategy_cache_persistence.persist_backtest_snapshot(
                _ExplodingConn(),
                _ExplodingR(),
                instance_id="main",
                strategy_name="graph_nexus_analysis",
                cache={"some_key": "some_value"},
                config_hash="x" * 16,
                module_hash="y" * 16,
                start_date="2026-05-01",
                end_date="2026-05-22",
            )
            # Guard returns False (matches the function's declared bool
            # return type); success returns True. The guard short-circuits
            # with False so callers doing `if ok is False:` correctly see
            # a non-success outcome.
            assert result is False, (
                "Guard didn't fire — persist_backtest_snapshot returned "
                f"{result!r} (expected False). DB stubs would have raised if "
                "the function had progressed past the guard."
            )
        finally:
            _bca_bare._skip_snapshot_persist = False
            _bca_pkg._skip_snapshot_persist = False
    finally:
        if sys_path_added:
            try:
                sys.path.remove(backend_path)
            except ValueError:
                pass
