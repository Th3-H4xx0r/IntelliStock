"""The scored set and the history map are disjoint; this covers the fallback.

bt 278531: 7,156 breakout evaluations, 100% exiting at `skip:bars=0<25`, zero
promotions, because `price_history` is built by the broker from
`symbols_for_data` BEFORE `run_once`, while `symbols_list` is populated by
discovery INSIDE it. bt 896168 `PH DIAG` confirmed the map itself is healthy
(map == symbols_for_data on 142/142 samples, empty=0), so the defect is the
symbol set, not the map.
"""
import ast
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_src_path = os.path.join(os.path.dirname(_here), "strategies", "graph_nexus_analysis.py")
_src = open(_src_path, encoding="utf-8").read()
_tree = ast.parse(_src)

_ns = {"_log": lambda *a, **k: None}


def _visible(bars, cache, date_key, config):
    """Stand-in for the real point-in-time filter."""
    return list(bars)[: int((cache or {}).get("_visible_n", len(bars)))]


_ns["_visible_overlay_bars"] = _visible
for _n in _tree.body:
    if isinstance(_n, ast.FunctionDef) and _n.name == "_breakout_history_fallback":
        exec(compile(ast.Module(body=[_n], type_ignores=[]), _src_path, "exec"), _ns)
fallback = _ns["_breakout_history_fallback"]

BARS = [{"c": 10.0, "v": 100} for _ in range(40)]


def test_default_off_returns_empty():
    assert fallback(["AAOI"], {}, {"_overlay_bars_raw": {"AAOI": BARS}}, "d", {}) == {}


def test_supplies_bars_for_a_discovered_symbol_absent_from_the_map():
    out = fallback(["AAOI"], {}, {"_overlay_bars_raw": {"AAOI": BARS}}, "d",
                   {"breakout_history_fallback_enabled": True})
    assert list(out) == ["AAOI"] and len(out["AAOI"]) == 40


def test_does_not_override_a_symbol_the_map_already_covers():
    """The broker map is authoritative where it has data."""
    out = fallback(["AAOI"], {"AAOI": BARS}, {"_overlay_bars_raw": {"AAOI": []}}, "d",
                   {"breakout_history_fallback_enabled": True})
    assert out == {}


def test_applies_the_point_in_time_filter():
    """Unfiltered bars would introduce lookahead; the visible filter must run."""
    out = fallback(["AAOI"], {}, {"_overlay_bars_raw": {"AAOI": BARS}, "_visible_n": 7},
                   "d", {"breakout_history_fallback_enabled": True})
    assert len(out["AAOI"]) == 7


def test_symbol_with_no_cached_bars_is_omitted_not_empty():
    """An empty list would still read as bars=0; omit instead."""
    out = fallback(["ZZZZ"], {}, {"_overlay_bars_raw": {"ZZZZ": []}}, "d",
                   {"breakout_history_fallback_enabled": True})
    assert out == {}


def test_uppercases_the_key_like_the_scorer_does():
    out = fallback(["aaoi"], {}, {"_overlay_bars_raw": {"AAOI": BARS}}, "d",
                   {"breakout_history_fallback_enabled": True})
    assert "AAOI" in out


def test_missing_or_malformed_cache_is_survivable():
    cfg = {"breakout_history_fallback_enabled": True}
    assert fallback(["AAOI"], {}, None, "d", cfg) == {}
    assert fallback(["AAOI"], {}, {"_overlay_bars_raw": "nonsense"}, "d", cfg) == {}
    assert fallback(None, None, {}, "d", cfg) == {}


def test_call_site_prefers_the_map_and_is_inert_when_empty():
    src = _src[_src.index("_bk_hist = price_history"):]
    src = src[: src.index("_compute_breakout_score_boost")]
    assert "if _bk_fallback:" in src, "must no-op when the fallback is empty"
    assert "not (price_history or {}).get(sym)" in src, "map wins where it has data"
