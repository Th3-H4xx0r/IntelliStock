"""Bear capacity latch: hold the bear cap through chop interludes (2026-07-25).

THE DEFECT: `max_positions_bear` releases the instant the regime label leaves
bear, so one chop bar restores full chop capacity (8) and the book refills --
and the bear-book trim's own success is what creates the room. The trim and the
chop capacity gate fight each other, and the chop gate wins.

Measured on bt#500437: trim cut the book to 2 on 03-05; chop 03-06..03-11
rebuilt it; bear resumed 03-12 and the DWELL restarted from zero, so the
rebuilt book bled untouched until the trim could fire again on 03-16.
**19 of 19 bear-leg long entries were opened on CHOP-labelled bars; zero on
bear bars.** Same-date proof from the +6.88% reference (bt#418917), which had no
trim, therefore no headroom, and was hard-blocked from buying:
    REGIME CAP HARD BLOCK: ANAB skipped — held=8 >= cap=8 (regime=chop)

THE RELEASE MATTERS AS MUCH AS THE LATCH. Blocking the April rebuild
(04-02..04-07) costs -$342.71 in forgone AMD +356.27 / MARA +55.98 / PYPL +40.06
/ AAOI +59.19 against FTH -151.68 and VG -36.07. A latch that stays defensive
into the recovery turns a +3.5pp win into a ~-2pp loss, so it must let go on a
genuine turn.

THE RELEASE CANNOT KEY ON ret20 AT ALL. That signal is inverted here: the
BLEEDING March chop bars ran ret20 -2.20 / +0.46 / -0.30 / -2.43 / -1.76 while
the PROFITABLE April chop bars ran -3.76 / -3.76 / -2.02 / -2.81. A depth
threshold releases on exactly the wrong bars, and a `ret20 > 0` release fires on
03-05/03-06 (+0.54/+0.46) — mid-downtrend — which is what the first draft of
this test caught.

What separates them is PROVENANCE, not depth: March chop is PLAIN chop, whereas
the April turn is `recover->chop` produced by `_recovery_override_regime`, which
sets `_market_regime_recovery`. So the latch releases on confirmed BULL or on
that recovery flag.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g

_ON = {"bear_capacity_latch_enabled": True}
_OFF = {}


def _nxt(prev, regime, recovery=False, cfg=None):
    return g._next_bear_capacity_latch(prev, regime, recovery, cfg if cfg is not None else _ON)


# ---------------------------------------------------------------- default-safe
def test_disabled_by_default():
    for regime in ("bear", "crash", "chop", "bull"):
        assert _nxt(True, regime, False, _OFF) is False


def test_capacity_regime_is_identity_without_the_latch():
    for regime in ("bear", "chop", "bull", "crash", "", None):
        assert g._capacity_regime(regime, False) == regime
        assert g._capacity_regime(regime, None) == regime


# ---------------------------------------------------------------- latching
def test_latches_on_bear_and_crash():
    assert _nxt(False, "bear", recovery=False) is True
    assert _nxt(False, "crash", recovery=False) is True


def test_holds_through_a_chop_interlude():
    """The 03-06..03-11 stretch that rebuilt the book."""
    latch = _nxt(False, "bear")                 # 03-03
    assert latch is True
    for _ in range(6):                          # 03-04..03-11, PLAIN chop
        latch = _nxt(latch, "chop", recovery=False)
    assert latch is True, "latch must survive a 6-session plain-chop interlude"


def test_capacity_regime_maps_latched_chop_to_bear():
    assert g._capacity_regime("chop", True) == "bear"
    # only chop is remapped -- bull/bear/crash pass through untouched
    assert g._capacity_regime("bull", True) == "bull"
    assert g._capacity_regime("bear", True) == "bear"
    assert g._capacity_regime("crash", True) == "crash"


# ---------------------------------------------------------------- releasing
def test_releases_on_confirmed_bull():
    """04-13 in the real sequence."""
    assert _nxt(True, "bull") is False


def test_releases_on_the_recovery_flag():
    """The April turn is `recover->chop`, which sets _market_regime_recovery."""
    assert _nxt(True, "chop", recovery=True) is False
    # recovery beats even a bear label -- the turn is the turn
    assert _nxt(True, "bear", recovery=True) is False


def test_plain_chop_never_releases_however_deep():
    """PROVENANCE, not depth, distinguishes the bars. A plain chop bar keeps the
    cap regardless of how the tape looks."""
    for _ in range(10):
        assert _nxt(True, "chop", recovery=False) is True


def test_falsy_recovery_values_do_not_release():
    """Fail safe: absent/None recovery state must not silently drop the cap."""
    for val in (None, 0, "", False):
        assert _nxt(True, "chop", val) is True


def test_full_march_to_april_sequence():
    """End-to-end over the real confirmed-regime path."""
    # (regime, recovery_flag). March chop = PLAIN; the April turn is recover->chop.
    seq = [("chop", 0), ("bear", 0), ("bear", 0), ("bear", 0),
           ("chop", 0), ("chop", 0), ("chop", 0), ("chop", 0),     # 03-06..03-11
           ("bear", 0), ("bear", 0), ("bear", 0), ("chop", 0),     # 03-12..03-17
           ("bear", 0), ("bear", 0), ("bear", 0), ("bear", 0),
           ("bear", 0), ("bear", 0), ("bear", 0), ("bear", 0),
           ("bear", 0), ("bear", 0), ("bear", 0),                  # ..04-01
           ("chop", 1), ("chop", 1), ("chop", 1), ("chop", 1),     # 04-02.. recovery
           ("chop", 1), ("chop", 1), ("chop", 1), ("bull", 0)]     # ..04-13
    latch, history = False, []
    for regime, rec in seq:
        latch = _nxt(latch, regime, rec)
        history.append(latch)
    assert history[1] is True, "03-03 bear latches"
    assert all(history[4:8]), ("03-06..03-11 plain chop must stay latched", history[4:8])
    assert history[11] is True, "03-17 single chop bar must not release"
    assert all(history[12:23]), "the long 03-18..04-01 bear stays latched"
    assert history[23] is False, "04-02 recover->chop must RELEASE for the rebuild"
    assert not any(history[23:]), "stays released through the recovery and bull leg"
