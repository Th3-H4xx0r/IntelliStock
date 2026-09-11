"""B1: the module DEFAULTS and the shipped header must be the same config.

`_f/_i/_s` resolve a missing key against `strategy_eb.DEFAULTS`. The wrapper's
INTELLISTOCK_SCHEMA header on line 1 of `strategies/strategy_eb.py` is the
SHIPPED config — the bil25 champion — and it differed from DEFAULTS on seven
keys, `trend_filter_bars` (0 vs 25), both remainder books (empty vs the
GLD/GDX/XLE pair), `risk_off_symbol` ("" vs BIL), `core_off_damp` (1.0 vs 0.0)
and the vol windows (20/60 vs 10/40) among them. Two consequences, both silent:

  * any caller that reads a key the document did not carry got the OTHER
    strategy — a two-leg SPY remainder with no trend machine at all, sized off
    a 20/60 vol pair instead of 10/40; and
  * `scripts/strategy_eb_sync_schema.py` copies DEFAULTS over the header, so
    running it REWROTE the shipped champion into that other strategy. It did
    exactly that during the 2026-09-10 guard task and had to be restored
    byte-identical.

The ruling (2026-09-11 audit, item 9): DEFAULTS follows the shipped header.
"""
import json
import os
import re
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategy_eb import DEFAULTS, drain_config_fallbacks, _f, _i, _s  # noqa: E402

_WRAPPER = os.path.join(_backend, "strategies", "strategy_eb.py")


def header_schema():
    source = open(_WRAPPER).read()
    match = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", source)
    assert match, "the wrapper lost its INTELLISTOCK_SCHEMA header"
    return json.loads(match.group(1))


def test_the_header_config_is_exactly_the_module_defaults():
    config = header_schema()["config"]
    assert set(config) == set(DEFAULTS), (
        f"only in the header: {sorted(set(config) - set(DEFAULTS))}; "
        f"only in DEFAULTS: {sorted(set(DEFAULTS) - set(config))}")
    differing = {k: (config[k], DEFAULTS[k])
                 for k in config if config[k] != DEFAULTS[k]}
    assert not differing, f"header vs DEFAULTS: {differing}"


def test_the_sync_script_is_now_a_no_op_on_the_shipped_header():
    """The script writes `json.dumps({**schema, "config": DEFAULTS})`. With the
    two in agreement that reproduces the header it read, so running it can no
    longer rewrite the champion."""
    schema = header_schema()
    rebuilt = dict(schema)
    rebuilt["config"] = dict(DEFAULTS)
    rebuilt["execution_position"] = 10
    assert json.dumps(rebuilt) == json.dumps(schema)


def test_the_shipped_champion_values_are_the_ones_pinned():
    """Named explicitly, so a future edit to either side has to say so."""
    assert DEFAULTS["trend_filter_bars"] == 25
    assert DEFAULTS["risk_off_symbol"] == "BIL"
    assert DEFAULTS["core_off_damp"] == 0.0
    assert DEFAULTS["vol_fast_bars"] == 10
    assert DEFAULTS["vol_slow_bars"] == 40
    assert DEFAULTS["trend_on_book"] == {"GLD": 0.5, "GDX": 0.25, "XLE": 0.25}
    assert DEFAULTS["trend_off_book"] == {"GLD": 0.375, "GDX": 0.1875,
                                          "XLE": 0.1875}


# --- the fallback is now recorded -------------------------------------------

def test_a_key_read_off_a_partial_config_is_recorded():
    drain_config_fallbacks()
    _f({}, "target_vol")
    _i({}, "vol_fast_bars")
    _s({}, "core_symbol")
    assert drain_config_fallbacks() == ["core_symbol", "target_vol",
                                        "vol_fast_bars"]


def test_a_key_the_config_carries_is_not_recorded():
    drain_config_fallbacks()
    _f({"target_vol": 0.3}, "target_vol")
    _s({"core_symbol": "QLD"}, "core_symbol")
    assert drain_config_fallbacks() == []


def test_the_record_is_a_set_so_a_whole_session_reports_once():
    drain_config_fallbacks()
    for _ in range(30):
        _f({}, "target_vol")
    assert drain_config_fallbacks() == ["target_vol"]


def test_draining_clears():
    drain_config_fallbacks()
    _f({}, "target_vol")
    assert drain_config_fallbacks() == ["target_vol"]
    assert drain_config_fallbacks() == []


def test_the_wrapper_drains_and_logs():
    """A source assertion: recording fallbacks and never reporting them is the
    same silence this item is about."""
    source = open(_WRAPPER).read()
    assert "drain_config_fallbacks" in source
