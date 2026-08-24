"""One chunk fetch must not be able to freeze a run.

`requests.get(..., timeout=60)` bounds a single HTTP call, but NOT the loop
around it: pagination is capped at 500 pages, so a chunk can legitimately spend
500 x 60s = over 8 HOURS before that cap trips — and nothing is logged between
pages, so the run looks frozen the entire time.

Observed 2026-08-24: bt 821959 stopped dead after
`Fetched chunk 1/2 for VAC`, log stuck at exactly 7,085 lines, zero LLM calls,
no error. It was killed by hand. Note also that `timeout=` in requests is a
BETWEEN-BYTES timeout, not a total-duration one, so a server trickling bytes
holds the connection open indefinitely without ever tripping it.

Two guards: a wall-clock deadline for the whole chunk, and a progress line while
paginating so a slow fetch is visible instead of silent.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_SRC = open(os.path.join(_backend, "broker.py")).read()


def test_a_chunk_fetch_has_a_wall_clock_deadline():
    assert "_chunk_deadline" in _SRC, (
        "a chunk fetch can paginate for hours with no time bound; "
        "requests' timeout= bounds one call, not the loop around it")


def test_the_deadline_is_bounded_well_under_an_hour():
    import re
    m = re.search(r"_CHUNK_FETCH_MAX_SECONDS\s*=\s*([0-9.]+)", _SRC)
    assert m, "no _CHUNK_FETCH_MAX_SECONDS constant"
    secs = float(m.group(1))
    assert 60 <= secs <= 900, (
        f"chunk deadline is {secs}s — it must be long enough for a legitimate "
        "slow page and short enough that a wedged fetch is not mistaken for a "
        "working run")


def test_pagination_logs_progress_so_a_slow_fetch_is_not_silent():
    assert "still paginating" in _SRC.lower(), (
        "nothing is logged between pages, so a slow or looping paginate is "
        "indistinguishable from a hang")


def test_the_deadline_returns_what_it_has_rather_than_raising():
    """Partial bars beat no bars and beat a crash."""
    i = _SRC.find("_CHUNK_FETCH_MAX_SECONDS")
    assert i > 0
    # the deadline branch must break out of the page loop, not raise
    seg = _SRC[_SRC.find("_chunk_deadline"):][:2500]
    assert "break" in seg, "the deadline branch does not break out of the loop"
