"""Trim the long book down to the regime cap in a sustained bear (2026-07-25).

THE DEFECT: `max_positions_bear` is an ENTRY gate, not a position limit. On
bt#498252 the book opened 10 names on 2026-03-02 while the regime was chop, the
regime downgraded to bear on 03-03 (cap 8 -> 2), and nothing sold. The portfolio
then carried ~9 decaying longs *alongside* a 35%-NAV SQQQ inverse hedge for the
whole bear leg — long and short the same market, paying both sides.

Measured cost: the bear-tuned reference (bt#418917) held its long book to -$175
and finished +6.88%; this generation bleeds ~-420..-470 on longs and finishes
~+0.25% with a comparable SQQQ gain.

DESIGN CONSTRAINT — the regime flip-flops (bear 03-03, chop 03-06, bear 03-12,
chop 03-17, bear 03-18). Dumping the book on every bear bar would churn four
times in three weeks and realize a loss at each local bottom. So the trim
requires `bear_book_trim_min_bear_bars` CONSECUTIVE confirmed-bear bars, and
bleeds out at most `bear_book_trim_max_per_bar` names per bar.

The sleeve legs (SQQQ) are never trimmed — in a bear that is the position
generating the gains, and selling it caused an infinite kill<->deploy churn in a
prior incident (see the kill-tier exemption).
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g

_CFG = {
    "max_positions": 14,
    "max_positions_chop": 8,
    "max_positions_bear": 2,
    "max_positions_crash": 0,
    "residual_sleeve_enabled": True,
    "residual_sleeve_bear_symbol": "SQQQ",
    "residual_sleeve_symbol": "SPY",
    "bear_book_trim_enabled": True,
    "bear_book_trim_min_bear_bars": 2,
    "bear_book_trim_max_per_bar": 3,
}

# worst-first ordering matters: ALM is the recurring big loser
_PNL = {"ALM": -15.2, "IFF": -8.1, "SMG": -6.0, "UNP": -3.2,
        "GD": -1.0, "HII": 0.5, "TRP": 2.0, "ITA": 3.1, "SQQQ": 19.0}


def _trim(cfg=None, regime="bear", bear_bars=3, pnl=None, recovery=False,
          book_size=None):
    return g._bear_book_trim_targets(
        cfg or _CFG, regime, bear_bars, pnl or _PNL, recovery_flag=recovery,
        book_size=book_size)


# ---------------------------------------------------------------- default-safe
def test_disabled_by_default_returns_nothing():
    cfg = dict(_CFG)
    del cfg["bear_book_trim_enabled"]
    assert _trim(cfg) == []


def test_no_trim_outside_bear():
    for regime in ("chop", "bull", ""):
        assert _trim(regime=regime) == []


def test_no_trim_before_dwell_satisfied():
    """A one-bar bear blip must not dump the book (the flip-flop guard)."""
    assert _trim(bear_bars=1) == []
    assert _trim(bear_bars=2) != []


# ---------------------------------------------------------------- the fix
def test_trims_worst_first_down_to_bear_cap():
    """8 longs, cap 2 -> 6 excess, but capped at 3 per bar, worst first."""
    out = _trim()
    assert out == ["ALM", "IFF", "SMG"], out


def test_never_trims_the_sleeve_leg():
    """SQQQ is the position generating the bear gain — never sell it."""
    for _ in range(5):
        assert "SQQQ" not in _trim()
    # even when the sleeve is the worst performer
    pnl = dict(_PNL, SQQQ=-99.0)
    assert "SQQQ" not in _trim(pnl=pnl)


def test_converges_to_the_cap_over_successive_bars():
    """Repeated application reaches the cap and then stops."""
    pnl = dict(_PNL)
    for _ in range(6):
        out = _trim(pnl=pnl)
        for s in out:
            pnl.pop(s, None)
    longs = [s for s in pnl if s != "SQQQ"]
    assert len(longs) == 2, longs      # == max_positions_bear
    assert _trim(pnl=pnl) == []        # steady state: nothing more to do


def test_crash_cap_zero_trims_all_longs_but_keeps_sleeve():
    pnl = dict(_PNL)
    for _ in range(6):
        for s in _trim(cfg=_CFG, regime="crash", pnl=pnl):
            pnl.pop(s, None)
    assert [s for s in pnl if s != "SQQQ"] == []
    assert "SQQQ" in pnl


def test_recovery_flag_raises_the_cap_and_stops_trimming():
    """A confirmed recovery must not keep liquidating into the turn."""
    cfg = dict(_CFG, max_positions_recovery=14)
    assert _trim(cfg=cfg, recovery=True) == []


def test_max_per_bar_is_respected():
    cfg = dict(_CFG, bear_book_trim_max_per_bar=1)
    assert len(_trim(cfg=cfg)) == 1


# ---------------------------------------------------------------- bug-sweep regressions
def test_sleeve_exemption_survives_whitespace_padded_config():
    """A padded `residual_sleeve_bear_symbol` produced an exemption set that no
    live position key matches -- so the hedge was ranked and SOLD FIRST. In a
    backtest the emulator keys positions by the same padded string, so this only
    ever bites in live."""
    for raw in (" SQQQ ", "sqqq", " sqqq\t"):
        cfg = dict(_CFG, residual_sleeve_bear_symbol=raw)
        assert "SQQQ" not in _trim(cfg=cfg), raw


def test_zero_is_honoured_not_rewritten_by_falsy_or():
    """`or 3` silently turned an operator's 0 into 3."""
    cfg = dict(_CFG, bear_book_trim_min_bear_bars=0)
    assert _trim(cfg=cfg, bear_bars=0) != []      # 0 means "trim immediately"
    cfg2 = dict(_CFG, bear_book_trim_max_per_bar=0)
    assert len(_trim(cfg=cfg2)) == 1              # clamps to >=1, not to 3


def test_book_size_counts_unpriceable_holdings():
    """held_pnl_pct only holds names we could price; using it as the denominator
    under-counts and no-ops the feature exactly when the book is most bloated."""
    pnl = {"ALM": -15.2, "IFF": -8.1}          # only 2 priceable...
    assert _trim(pnl=pnl, book_size=2) == []   # ...2 vs cap 2 -> nothing
    # ...but the real book is 9 -> excess is real, trim the priceable worst
    assert _trim(pnl=pnl, book_size=9) == ["ALM", "IFF"]


def test_book_size_never_shrinks_below_priced_names():
    """A bogus small book_size must not mask names we can actually see."""
    assert _trim(book_size=0) == ["ALM", "IFF", "SMG"]
