"""A trailing return computed across a split is a different number, not a small error.

AKTX reached bt 453789's momentum scan at `20d=+4554.2%`. Its end-of-run movement
summary read $0.12 -> $13.41 (+10,815%) while the decision log quoted the same name at
$3.05 rising to $24 — two different series for one symbol, spliced.

The momentum CEILING that sits beside this guard cannot catch that class, because it caps
the RETURN: it fires identically on a data artifact and on a genuine +90% mover, and
across five runs it discarded 102 distinct symbols against only 8 split-shaped ones. This
guard looks at the STEP between consecutive bars, which a real multi-week move never
produces.

These tests drive the REAL `_discover_stocks_from_momentum` and the REAL
`returns_are_trustworthy`. Nothing here re-implements the logic under test — two suites in
this repository stayed green over live defects by doing exactly that.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from split_detect import returns_are_trustworthy  # noqa: E402


def _bars(closes):
    return [{"c": float(c)} for c in closes]


# --------------------------------------------------------------------------
# the pure predicate
# --------------------------------------------------------------------------
def test_a_clean_uptrend_is_trustworthy():
    ok, why = returns_are_trustworthy([10.0 + i * 0.5 for i in range(80)])
    assert ok is True
    assert why == ""


def test_a_genuine_big_mover_is_NOT_flagged():
    """The whole point of the objective: +150% over weeks must survive.

    This is the failure mode that would make the guard harmful — if it tripped on
    sustained moves it would throw away exactly the names the system exists to catch.
    """
    closes = [20.0 * (1.0 + 1.5 * (i / 79.0)) for i in range(80)]
    assert closes[-1] / closes[0] >= 2.4          # the series really does +150%
    ok, why = returns_are_trustworthy(closes)
    assert ok is True, why


def test_a_listed_reverse_split_is_caught():
    ok, why = returns_are_trustworthy([5.0] * 30 + [40.0] * 30)   # 1-for-8
    assert ok is False
    assert "split-shaped" in why


def test_a_listed_forward_split_is_caught():
    """A forward split steps DOWN and reads as a crash — the direction that
    stop-losses a healthy position."""
    ok, why = returns_are_trustworthy([800.0] * 30 + [100.0] * 30)  # 8-for-1
    assert ok is False
    assert "split-shaped" in why


def test_an_unlisted_large_reverse_split_is_caught():
    """SPLIT_RATIOS stops at 20.0, but sub-dollar issuers reverse-split 1-for-100.

    This is the AKTX shape and the reason the ratio table alone was not enough.
    """
    ok, why = returns_are_trustworthy([0.12] * 30 + [12.0] * 30)
    assert ok is False
    assert "implausible step" in why


def test_the_bound_is_configurable_and_symmetric():
    # 3.5x, deliberately NOT a listed SPLIT_RATIOS value, so this exercises the
    # step bound rather than the ratio table.
    down = [35.0] * 10 + [10.0] * 10
    assert returns_are_trustworthy(down, max_step_ratio=3.0)[0] is False
    assert returns_are_trustworthy(down, max_step_ratio=5.0)[0] is True


def test_a_listed_ratio_is_caught_regardless_of_the_step_bound():
    """The ratio table and the step bound are independent nets.

    A 3-for-1 is a listed ratio, so raising the step bound must NOT let it
    through — otherwise a loose bound would silently disable split detection.
    """
    down = [30.0] * 10 + [10.0] * 10          # exactly 3.0x -> a listed ratio
    ok, why = returns_are_trustworthy(down, max_step_ratio=99.0)
    assert ok is False
    assert "split-shaped" in why


def test_degenerate_input_never_raises():
    assert returns_are_trustworthy(None)[0] is True
    assert returns_are_trustworthy([])[0] is True
    assert returns_are_trustworthy([1.0])[0] is True
    assert returns_are_trustworthy([1.0, 0.0])[0] is False        # non-positive
    assert returns_are_trustworthy(["x", 2.0])[0] is False        # non-numeric


# --------------------------------------------------------------------------
# the real discovery function
# --------------------------------------------------------------------------
class _FakeStore:
    """Just enough of db.store for the insert at the end of discovery.

    The real function swallows insert failures, so without this every result
    comes back empty and the whole suite would pass while testing nothing.
    """

    def insert(self, _table, _doc, *, conflict=None, durability="hard"):
        return {"inserted": 1}


def _run_discovery(monkeypatch, data, config):
    """Drive the real momentum discovery over the price_history source."""
    from strategies import graph_nexus_analysis as gna

    monkeypatch.setattr(gna, "_get_all_discovered_stocks", lambda *a, **k: [])
    monkeypatch.setattr(gna, "store", _FakeStore())
    captured = []
    monkeypatch.setattr(gna, "_log", lambda msg, *a, **k: captured.append(str(msg)))

    out = gna._discover_stocks_from_momentum(
        object(), data, "test_inst", set(), config, "2026-05-01",
    )
    return out, captured


BASE_CONFIG = {
    "momentum_discovery_enabled": True,
    "momentum_discovery_min_20d_return": 20.0,
    "momentum_discovery_min_60d_return": 50.0,
    "momentum_discovery_max_per_day": 5,
    "max_discovered_stocks": 30,
    # ceilings OFF so the ONLY thing that can exclude AKTX is the new guard —
    # otherwise this test would pass with the guard deleted.
    "momentum_discovery_max_20d_return": 0,
    "momentum_discovery_max_60d_return": 0,
}


def test_the_split_series_is_ranked_when_the_guard_is_OFF(monkeypatch):
    """ANTI-VACUITY GUARD.

    If the corrupted name were excluded for some other reason, the test below would
    pass with the feature deleted and certify nothing. This pins that AKTX IS
    discovered by default — the defect is real and reachable.
    """
    data = {"AKTX": _bars([0.12] * 40 + [12.0] * 30)}
    out, _ = _run_discovery(monkeypatch, data, dict(BASE_CONFIG))
    assert "AKTX" in [str(t).upper() for t in (out or [])], (
        "expected the discontinuous series to be discovered with the guard off; "
        "if it is not, this suite cannot prove the guard does anything"
    )


def test_the_split_series_is_skipped_when_the_guard_is_ON(monkeypatch):
    data = {"AKTX": _bars([0.12] * 40 + [12.0] * 30)}
    config = dict(BASE_CONFIG, momentum_skip_discontinuous_series_enabled=True)
    out, logs = _run_discovery(monkeypatch, data, config)
    assert "AKTX" not in [str(t).upper() for t in (out or [])]
    assert any("Momentum discontinuity skip: AKTX" in m for m in logs), (
        "the skip must announce itself — an unlogged lever is unprovable"
    )


def test_the_guard_does_not_cost_a_clean_mover(monkeypatch):
    """The guard must be surgical: a clean +100% name still gets discovered."""
    clean = [10.0 * (1.0 + 1.0 * (i / 69.0)) for i in range(70)]
    data = {"GOOD": _bars(clean)}
    config = dict(BASE_CONFIG, momentum_skip_discontinuous_series_enabled=True)
    out, _ = _run_discovery(monkeypatch, data, config)
    assert "GOOD" in [str(t).upper() for t in (out or [])]


def test_a_broken_detector_fails_OPEN(monkeypatch):
    """A detector that cannot run must not stop discovery — same doctrine as
    corporate_actions, which fails open on every path."""
    import split_detect

    def _boom(*a, **k):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(split_detect, "returns_are_trustworthy", _boom)
    clean = [10.0 * (1.0 + 1.0 * (i / 69.0)) for i in range(70)]
    data = {"GOOD": _bars(clean)}
    config = dict(BASE_CONFIG, momentum_skip_discontinuous_series_enabled=True)
    out, _ = _run_discovery(monkeypatch, data, config)
    assert "GOOD" in [str(t).upper() for t in (out or [])]
