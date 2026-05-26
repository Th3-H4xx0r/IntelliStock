"""Tests for the winner-depth + propagation-hygiene fix (A+B).

B = a fail-safe per-seed propagation fan-out cap that stops one news item
(e.g. a HOOD earnings hit) from injecting its entire COMPETES_WITH cohort
into scoring and flooding the backfill queue.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
import pathlib

from strategies.graph_nexus_analysis import _cap_propagation_fanout_per_seed, _get_effective_nexus_config


def _edges(src, n, conf_base=0.5):
    return [
        {"source": src, "target": f"T{i}", "confidence": conf_base + i * 0.01, "revenue_pct": 0.0}
        for i in range(n)
    ]


def test_caps_over_limit_seed_keeping_strongest():
    edges = _edges("HOOD", 20)
    out = _cap_propagation_fanout_per_seed(edges, {"propagation_max_per_seed": 8})
    assert len(out) == 8
    kept = {e["target"] for e in out}
    # strongest confidence = highest index (conf_base + i*0.01)
    assert kept == {f"T{i}" for i in range(12, 20)}


def test_under_limit_seed_unchanged():
    edges = _edges("HOOD", 5)
    out = _cap_propagation_fanout_per_seed(edges, {"propagation_max_per_seed": 8})
    assert [e["target"] for e in out] == [f"T{i}" for i in range(5)]


def test_absent_or_zero_is_noop():
    edges = _edges("HOOD", 50)
    assert _cap_propagation_fanout_per_seed(edges, {}) is edges
    assert _cap_propagation_fanout_per_seed(edges, {"propagation_max_per_seed": 0}) is edges
    assert _cap_propagation_fanout_per_seed(edges, {"propagation_max_per_seed": -1}) is edges


def test_multiple_seeds_capped_independently_order_preserved():
    edges = _edges("AAA", 10) + _edges("BBB", 3)
    out = _cap_propagation_fanout_per_seed(edges, {"propagation_max_per_seed": 4})
    assert sum(1 for e in out if e["source"] == "AAA") == 4
    assert sum(1 for e in out if e["source"] == "BBB") == 3
    # original relative order preserved among kept edges
    assert out == [e for e in edges if e in out]


def test_empty_edges_is_noop():
    assert _cap_propagation_fanout_per_seed([], {"propagation_max_per_seed": 8}) == []


def test_effective_config_resolves_per_seed_default_and_value():
    # absent -> code default 0 (disabled, backward-safe)
    assert _get_effective_nexus_config({})["propagation_max_per_seed"] == 0
    assert _get_effective_nexus_config({"propagation_max_per_seed": 8})["propagation_max_per_seed"] == 8


def test_schema_line_is_valid_json_and_declares_new_knob():
    src = pathlib.Path(__file__).resolve().parents[1] / "strategies" / "graph_nexus_analysis.py"
    first_line = src.read_text(encoding="utf-8").splitlines()[0]
    blob = first_line.split("INTELLISTOCK_SCHEMA:", 1)[1].strip()
    parsed = json.loads(blob)
    assert parsed["config"]["propagation_max_per_seed"] == 8
