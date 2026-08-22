"""A transient DB outage must not flood the log: the telemetry flusher logs the
FIRST failure + every 20th (never the giant query payload), and backs off."""
import llm_telemetry as t


class FakeR:
    """The store handle whose insert() always raises a HUGE, multi-line error
    (mimics a driver write error that stringifies the entire insert query)."""
    def __init__(self, exc):
        self.exc = exc

    def insert(self, _table, _rows, **_kw):
        raise self.exc


def test_flush_failure_is_throttled_and_truncated(capsys):
    t._reset_for_tests()
    first_line = "Cannot perform write: primary replica for shard not available"
    huge = Exception(first_line + "\n" + "GIANT_QUERY_PAYLOAD " * 2000)
    t.configure(db_conn_factory=lambda: object(), enabled=True,
                auto_start_flusher=False, r_module=FakeR(huge))
    t._buffer.append({"id": "x", "provider": "p", "model": "m", "ok": True})

    for _ in range(25):        # sustained outage: same re-queued row keeps failing
        t._do_flush()
    err = capsys.readouterr().err

    assert "GIANT_QUERY_PAYLOAD" not in err          # never dumps the query payload
    assert first_line[:40] in err                    # does log the real cause (truncated)
    assert err.count("flush failed") <= 3            # throttled (logs #1 and #20), not 25×
    # consecutive-failure counter tracked for backoff
    assert t._state.get("consecutive_flush_failures", 0) >= 20
    t._reset_for_tests()


def test_success_resets_failure_counter():
    t._reset_for_tests()
    t._state["consecutive_flush_failures"] = 7  # pretend we were failing

    class OKR:
        def insert(self, _table, rows, **_kw):
            return {"inserted": len(rows)}

    t.configure(db_conn_factory=lambda: object(), enabled=True,
                auto_start_flusher=False, r_module=OKR())
    t._buffer.append({"id": "y", "provider": "p", "model": "m", "ok": True})
    t._do_flush()
    assert t._state.get("consecutive_flush_failures", 0) == 0   # recovered -> reset
    t._reset_for_tests()
