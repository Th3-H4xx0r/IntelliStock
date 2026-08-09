"""Z4.1's regime cap must reach the gate that enforces it.

The strategy lifts max_positions per regime (chop 6->8, bull 6->14) and logs it,
while `resolve_max_positions_cap` reads the STATIC cfg["max_positions"]. Across
bt 820236 / 613166 / 201039 every one of 634 `max_positions gate armed` lines
read cap=6 and not one read 8 or 14, while the book sat at held=6/cap=6 on
87-94% of bars. Local replay of the recorded gate chain: 45 of 45 refusals in
820236 and 12 of 12 in 613166 would clear at the regime cap — including SNDK.

There are THREE hops and all three are required. Publishing into `scores` alone
was silently inert: bt 555694 logged zero "honouring the regime cap" lines while
every gate line still read cap=6, because the tick body reads `metadata`, which
is packed separately from the scores dict.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BROKER = open(os.path.join(os.path.dirname(__file__), "..", "broker.py")).read()
GNA = open(os.path.join(os.path.dirname(__file__), "..", "strategies",
                        "graph_nexus_analysis.py")).read()


def test_hop1_strategy_publishes_the_regime_adjusted_value():
    assert 'scores["_nexus_max_positions"] = int(_max_positions)' in GNA
    # and it must be published AFTER Z4.1 has had its say
    assert GNA.index("_max_positions = _z41_capped") < GNA.index(
        'scores["_nexus_max_positions"]')


def test_hop2_broker_pops_and_packs_it_into_metadata():
    """The hop that was missing. `scores` is not the channel the tick body reads."""
    assert 'raw.pop("_nexus_max_positions", None)' in BROKER
    assert 'metadata["_nexus_max_positions"] = nexus_max_positions' in BROKER


def test_hop2_pop_happens_before_the_ticker_loop():
    """Un-popped `_nexus_*` keys are walked as if they were tickers."""
    assert BROKER.index('raw.pop("_nexus_max_positions"') < BROKER.index(
        "for sym, val in raw.items():")


def test_hop3_tick_body_reads_it_from_metadata():
    assert 'meta.get("_nexus_max_positions")' in BROKER


def test_tightening_never_needs_the_flag():
    """A de-risked bear cap must apply without an opt-in."""
    seg = BROKER[BROKER.index("_mpg_widen = _max_positions_honour_regime_cap"):]
    seg = seg[:400]
    assert "nexus_max_positions < _mpg_cap or _mpg_widen" in seg


def test_widening_requires_the_flag():
    assert "_max_positions_honour_regime_cap" in BROKER
    assert 'cfg.get("max_positions_honour_regime_cap", False)' in BROKER


def test_several_specs_take_the_tightest():
    seg = BROKER[BROKER.index('_nmp = meta.get("_nexus_max_positions")'):][:900]
    assert "min(nexus_max_positions, _nmp_i)" in seg


def test_absent_key_leaves_the_static_cap_alone():
    seg = BROKER[BROKER.index("nexus_max_positions = None"):][:700]
    assert "if _nmp is not None" in seg
