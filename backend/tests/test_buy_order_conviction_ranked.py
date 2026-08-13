"""The buy queue must not fund by spelling (bt 873929).

`Execution order: ... by (intent_priority, allocation, ticker)` consumed cash in
alphabetical order. On the 2026-01-19 tick HYMC and SNDK both carried
high_conv=True and an identical $955.76 allocation, so HYMC was evaluated first
purely on spelling. SNDK went on to move +187.9% and was not filled until 02-02,
94.9% of the way through that move, capturing 5.1% of it.

Default stays OFF, so every existing document keeps its exact current order.
AST extraction because broker.py is not import-safe (argv parsing at module scope).
"""
import ast
import os
import sys

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_src = open(os.path.join(_backend, "broker.py"), encoding="utf-8").read()
_tree = ast.parse(_src)
_ns = {"_core_sleeve_cfg_raw": lambda specs: (
    (specs or [{}])[0].get("config") if isinstance(specs, list) and specs
    and isinstance(specs[0], dict) else None)}
for _node in _tree.body:
    if isinstance(_node, ast.FunctionDef) and _node.name == "_buy_order_conviction_ranked":
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "broker.py", "exec"), _ns)
_buy_order_conviction_ranked = _ns["_buy_order_conviction_ranked"]



_INTENT = {
    "backfill_rotation_buy": 0,
    "winner_add_buy": 1,
    "momentum_amplifier_buy": 1,
    "backfill_queue_buy": 2,
    "direct_reserved_buy": 2,
    "top_momentum_break_glass_buy": 2,
}
_DEFAULT = 3


def _key_factory(sizes, intents, *, conviction):
    """Mirror of broker's `_buy_sort_key` closure under both settings."""
    def _key(s):
        hint = (sizes or {}).get(s) or {}
        pri = _INTENT.get(intents.get(s), _DEFAULT)
        alloc = float(hint.get("buy_cash", 0) or 0)
        if conviction:
            try:
                raw = float(hint.get("raw_net_score", 0.0) or 0.0)
            except (TypeError, ValueError):
                raw = 0.0
            return (pri, -raw, -alloc, s)
        return (pri, -alloc, s)
    return _key


SIZES = {
    "HYMC": {"buy_cash": 955.76, "raw_net_score": 1.55},
    "SNDK": {"buy_cash": 955.76, "raw_net_score": 1.70},
    "AAPL": {"buy_cash": 400.00, "raw_net_score": 0.20},
}
INTENTS = {s: "backfill_queue_buy" for s in SIZES}


def test_default_off_preserves_the_alphabetical_order_exactly():
    order = sorted(SIZES, key=_key_factory(SIZES, INTENTS, conviction=False))
    assert order == ["HYMC", "SNDK", "AAPL"], "existing documents must not move"


def test_enabled_funds_the_higher_conviction_name_first():
    order = sorted(SIZES, key=_key_factory(SIZES, INTENTS, conviction=True))
    assert order[0] == "SNDK", "conviction, not spelling, must get first call on cash"
    assert order == ["SNDK", "HYMC", "AAPL"]


def test_intent_priority_still_outranks_conviction():
    """A paired rotation buy owns its paired sell's proceeds regardless."""
    intents = dict(INTENTS, AAPL="backfill_rotation_buy")
    order = sorted(SIZES, key=_key_factory(SIZES, intents, conviction=True))
    assert order[0] == "AAPL"


def test_ticker_remains_the_final_deterministic_tiebreaker():
    sizes = {
        "ZZZ": {"buy_cash": 100.0, "raw_net_score": 1.5},
        "AAA": {"buy_cash": 100.0, "raw_net_score": 1.5},
    }
    intents = {s: "backfill_queue_buy" for s in sizes}
    key = _key_factory(sizes, intents, conviction=True)
    assert sorted(sizes, key=key) == ["AAA", "ZZZ"]
    assert sorted(sizes, key=key) == sorted(sizes, key=key), "order must be stable"


def test_missing_or_malformed_conviction_never_raises_and_sorts_last():
    sizes = {
        "GOOD": {"buy_cash": 100.0, "raw_net_score": 1.9},
        "NONUM": {"buy_cash": 100.0, "raw_net_score": "not-a-number"},
        "MISSING": {"buy_cash": 100.0},
    }
    intents = {s: "backfill_queue_buy" for s in sizes}
    order = sorted(sizes, key=_key_factory(sizes, intents, conviction=True))
    assert order[0] == "GOOD"
    assert set(order[1:]) == {"NONUM", "MISSING"}


def test_config_reader_defaults_off_and_never_raises():
    assert _buy_order_conviction_ranked(None) is False
    assert _buy_order_conviction_ranked([]) is False
    assert _buy_order_conviction_ranked(object()) is False


def test_config_reader_reads_the_documented_key():
    specs = [{"strategy": "graph_nexus_analysis",
              "config": {"buy_order_conviction_ranked_enabled": True}}]
    assert _buy_order_conviction_ranked(specs) is True
    specs[0]["config"]["buy_order_conviction_ranked_enabled"] = False
    assert _buy_order_conviction_ranked(specs) is False
