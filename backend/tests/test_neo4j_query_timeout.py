"""A Neo4j query must not be able to hang a run forever.

The driver is built with `connection_timeout=30` and
`connection_acquisition_timeout=60`, but neither bounds a query that is already
RUNNING — that is a per-transaction setting. bt 232783 froze at
`Macro sector flow query: start | seeds=75` on bar 39 of ~45 and had to be
killed; it was the THIRD independent unbounded blocking call found that day,
after the trade-overlay LLM batch and the Alpaca bar fetch.

There are 31 `session.run(` call sites, so the bound is applied once at the
driver seam rather than 31 times. `neo4j.Query(text, timeout=...)` is passed
straight through to `session.run()`, which returns the SAME Result object — so
`.single()`, `.data()`, and streaming iteration are all unaffected. That
property is what makes this safe to do on the module that runs the live
instance; a wrapper that materialised rows inside a bounded transaction would
have changed the interface at every call site.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_SRC = open(os.path.join(_backend, "strategies",
                        "graph_nexus_analysis.py")).read()


class _FakeResult:
    def __init__(self, q):
        self.q = q

    def single(self):
        return {"ok": True}


class _FakeSession:
    def __init__(self):
        self.seen = []

    def run(self, query, **params):
        self.seen.append(query)
        return _FakeResult(query)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeDriver:
    def __init__(self):
        self.sess = _FakeSession()

    def session(self, **kw):
        return self.sess

    def close(self):
        pass


def test_the_bounded_driver_wrapper_exists():
    assert "_BoundedNeo4jDriver" in _SRC, (
        "nothing bounds a running Neo4j query; connection_timeout does not")


def test_a_string_query_is_given_a_timeout():
    from strategies.graph_nexus_analysis import _BoundedNeo4jDriver
    d = _BoundedNeo4jDriver(_FakeDriver(), 25.0)
    with d.session() as s:
        s.run("MATCH (n) RETURN n")
    sent = d._driver.sess.seen[0]
    assert getattr(sent, "timeout", None) == 25.0, (
        f"query was not wrapped with a timeout: {sent!r}")


def test_the_result_object_is_passed_through_untouched():
    """The whole point: `.single()` and iteration must still work."""
    from strategies.graph_nexus_analysis import _BoundedNeo4jDriver
    d = _BoundedNeo4jDriver(_FakeDriver(), 25.0)
    with d.session() as s:
        res = s.run("MATCH (n) RETURN n")
    assert res.single() == {"ok": True}


def test_a_zero_timeout_disables_the_bound():
    from strategies.graph_nexus_analysis import _BoundedNeo4jDriver
    d = _BoundedNeo4jDriver(_FakeDriver(), 0)
    with d.session() as s:
        s.run("MATCH (n) RETURN n")
    assert d._driver.sess.seen[0] == "MATCH (n) RETURN n", (
        "a disabled bound should pass the raw string through unchanged")


def test_unknown_session_attributes_still_reach_the_real_session():
    from strategies.graph_nexus_analysis import _BoundedNeo4jDriver
    d = _BoundedNeo4jDriver(_FakeDriver(), 25.0)
    s = d.session()
    s.close()          # must not raise: proxied to the real session
