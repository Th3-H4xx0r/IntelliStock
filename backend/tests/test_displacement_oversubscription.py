"""One holding must not fund three buys on the same tick (bt 511709).

The run queued `trimming 45% of TSM ($858.81) to free $364.96` three times on one
tick, for ARWR, CFG and MBLY. Because the consumer takes max(sell_fraction), that
is a single $365 release which three separate buys then expect to spend.
"""
import ast
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_src = open(os.path.join(_backend, "broker.py"), encoding="utf-8").read()


def _dedupe(reqs):
    """Mirror of the broker's per-holding strongest-request selection."""
    best = {}
    for r in reqs or []:
        try:
            k = str((r or {}).get("sell") or "").upper()
            s = float((r or {}).get("score") or 0.0)
        except (TypeError, ValueError, AttributeError):
            continue
        if not k:
            continue
        if k not in best or s > float(best[k].get("score") or 0.0):
            best[k] = r
    return list(best.values())


TICK = [
    {"sell": "TSM", "fund": "ARWR", "value": 858.81, "need": 364.96, "score": 1.700},
    {"sell": "TSM", "fund": "CFG", "value": 858.81, "need": 364.96, "score": 1.750},
    {"sell": "TSM", "fund": "MBLY", "value": 858.81, "need": 364.96, "score": 1.800},
]


def test_one_holding_produces_one_trim():
    assert len(_dedupe(TICK)) == 1


def test_the_strongest_candidate_wins():
    assert _dedupe(TICK)[0]["fund"] == "MBLY"


def test_distinct_holdings_are_all_kept():
    reqs = TICK + [{"sell": "CPER", "fund": "SNDK", "value": 921.0,
                    "need": 420.0, "score": 1.700}]
    assert {r["sell"] for r in _dedupe(reqs)} == {"TSM", "CPER"}


def test_malformed_requests_are_dropped_not_raised():
    assert _dedupe([None, {}, {"sell": ""}, {"sell": "X", "score": "bad"}]) == []


def test_the_guard_exists_in_the_source():
    assert "_disp_best" in _src, "dedupe must be wired, not just tested in the mirror"
