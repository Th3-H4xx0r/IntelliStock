from __future__ import annotations

import pytest

from point_in_time_data import PointInTimeDataError
from point_in_time_graph import RecordingGraphDriver, ReplayGraphDriver


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def __iter__(self):
        return iter(self._rows)


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def run(self, query, parameters=None, **kwargs):
        params = dict(parameters or {})
        params.update(kwargs)
        self.calls.append((query, params))
        return _Result(self.responses.pop(0))


class _Driver:
    def __init__(self, responses):
        self.raw_session = _Session(responses)
        self.closed = False

    def session(self, **_kwargs):
        return self.raw_session

    def verify_connectivity(self):
        return None

    def close(self):
        self.closed = True


def test_recording_driver_round_trips_query_results():
    delegate = _Driver(
        [
            [
                {"ticker": "AAPL", "score": 1.25},
                {"ticker": "MSFT", "score": 0.75},
            ]
        ]
    )
    recorder = RecordingGraphDriver(delegate)

    with recorder.session() as session:
        result = session.run(
            "MATCH (c:Company) WHERE c.ticker IN $tickers RETURN c.ticker AS ticker",
            tickers=["AAPL", "MSFT"],
        )
        assert result.data()[0]["ticker"] == "AAPL"
        assert result.single()["ticker"] == "AAPL"
        assert result.consume().counters == {}

    replay = ReplayGraphDriver(recorder.export())
    with replay.session() as session:
        rows = list(
            session.run(
                "MATCH (c:Company) WHERE c.ticker IN $tickers RETURN c.ticker AS ticker",
                {"tickers": ["AAPL", "MSFT"]},
            )
        )

    assert rows == [
        {"ticker": "AAPL", "score": 1.25},
        {"ticker": "MSFT", "score": 0.75},
    ]


def test_replay_parameters_are_mapping_order_invariant():
    delegate = _Driver([[{"ok": True}]])
    recorder = RecordingGraphDriver(delegate)
    with recorder.session() as session:
        list(session.run("RETURN $a AS a", {"a": 1, "b": 2}))

    replay = ReplayGraphDriver(recorder.export())
    with replay.session() as session:
        assert list(session.run("RETURN $a AS a", {"b": 2, "a": 1})) == [
            {"ok": True}
        ]


def test_repeated_query_occurrences_replay_in_original_order():
    delegate = _Driver([[{"value": 1}], [{"value": 2}]])
    recorder = RecordingGraphDriver(delegate)
    with recorder.session() as session:
        assert list(session.run("RETURN 1")) == [{"value": 1}]
        assert list(session.run("RETURN 1")) == [{"value": 2}]

    replay = ReplayGraphDriver(recorder.export())
    with replay.session() as session:
        assert list(session.run("RETURN 1")) == [{"value": 1}]
        assert list(session.run("RETURN 1")) == [{"value": 2}]
        with pytest.raises(PointInTimeDataError, match="occurrences exhausted"):
            list(session.run("RETURN 1"))


def test_replay_fails_closed_for_unrecorded_query():
    replay = ReplayGraphDriver({"recording_version": 1, "queries": {}})

    with replay.session() as session:
        with pytest.raises(PointInTimeDataError, match="no recorded query"):
            session.run("MATCH (n) RETURN n")


def test_replay_audit_remembers_a_swallowed_query_miss():
    replay = ReplayGraphDriver({"recording_version": 1, "queries": {}})
    try:
        replay.session().run("MATCH (n) RETURN n")
    except PointInTimeDataError:
        pass

    with pytest.raises(PointInTimeDataError, match="replay failed"):
        replay.assert_replay_complete()


def test_replay_audit_rejects_unconsumed_recorded_queries():
    recorder = RecordingGraphDriver(_Driver([[{"ticker": "AAPL"}]]))
    with recorder.session() as session:
        list(session.run("MATCH (n:Company) RETURN n.ticker AS ticker"))
    replay = ReplayGraphDriver(recorder.export())

    with pytest.raises(PointInTimeDataError, match="unconsumed"):
        replay.assert_replay_complete()


@pytest.mark.parametrize(
    "query",
    [
        "CREATE (n:Company)",
        "MATCH (n) SET n.flag = true",
        "MATCH (n) DELETE n",
        "MERGE (n:Company {ticker: 'AAPL'})",
    ],
)
def test_recording_driver_rejects_write_queries(query):
    recorder = RecordingGraphDriver(_Driver([]))

    with recorder.session() as session:
        with pytest.raises(PointInTimeDataError, match="read-only"):
            session.run(query)


def test_close_and_connectivity_are_driver_compatible():
    delegate = _Driver([])
    recorder = RecordingGraphDriver(delegate)

    assert recorder.verify_connectivity() is None
    recorder.close()

    assert delegate.closed is True
    replay = ReplayGraphDriver({"recording_version": 1, "queries": {}})
    assert replay.verify_connectivity() is None
    assert replay.close() is None
