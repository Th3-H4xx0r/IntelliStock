"""The pair that motivated this scored 20% overlap and would have been quoted as neutral.

Fixtures are built from the REAL shape a run prints — the end-of-run "P&L per stock"
block — not from an invented format. Two suites in this repository stayed green over live
defects by testing a shape the producer never emits.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pair_validity import (  # noqa: E402
    DEFAULT_MIN_OVERLAP,
    MEASURED_DISPERSION_PP,
    assess_pair,
    overlap,
    traded_symbols,
)

# Verbatim shape from bt 333727's log, timestamps and prefix included.
REAL_BLOCK = """[2026-08-15 19:55:40] [BROKER] ---------- Backtest summary: P&L per stock ----------
[2026-08-15 19:55:40] [BROKER]   AAOI: P&L = $-85.83 (-12.33%)
[2026-08-15 19:55:40] [BROKER]   AEHR: P&L = $-74.82 (-10.93%)
[2026-08-15 19:55:40] [BROKER]   MXL: P&L = $501.14 (+73.66%)
[2026-08-15 19:55:40] [BROKER]   SPY: P&L = $358.48 (+14.95%)
[2026-08-15 19:55:40] [BROKER] ---------- Stock movement (start -> end) ----------
[2026-08-15 19:55:40] [BROKER]   AAOI: $84.60 -> $184.61  (+118.22%)
"""


def test_symbols_come_from_the_real_block_shape():
    got = traded_symbols(REAL_BLOCK)
    assert got == {"AAOI", "AEHR", "MXL", "SPY"}


def test_the_movement_block_is_not_mistaken_for_a_pnl_line():
    """`AAOI: $84.60 -> $184.61` sits four lines below and must not be counted."""
    assert traded_symbols(REAL_BLOCK) == traded_symbols(
        REAL_BLOCK.split("Stock movement")[0]
    )


def test_overlap_is_symmetric_and_bounded():
    assert overlap({"A", "B"}, {"A", "B"}) == 1.0
    assert overlap({"A"}, {"B"}) == 0.0
    assert overlap(set(), set()) == 0.0
    assert overlap({"A", "B"}, {"B", "C"}) == overlap({"B", "C"}, {"A", "B"})


def test_overlap_punishes_a_treatment_that_only_ADDS_names():
    """Asymmetric "share of control still present" would score this 1.0.

    A treatment that keeps every control name and adds eight more is just as
    contaminating, so Jaccard is the right measure and this pins it.
    """
    ctl = {"A", "B"}
    trt = {"A", "B", "C", "D", "E", "F", "G", "H"}
    assert overlap(ctl, trt) == 0.25


# --------------------------------------------------------------------------
# the verdict
# --------------------------------------------------------------------------
def test_the_real_contaminated_pair_is_VOID():
    """bt 333727 vs bt 453789, the actual books. 4 shared of 20."""
    ctl = {"symbols": {"AAOI", "AEHR", "AIFD", "AIQ", "BC", "BOTZ", "D", "RIVN",
                       "AIOS", "AXTI", "MXL", "SPY"}, "return_pct": 20.53}
    trt = {"symbols": {"AKTX", "ARGX", "ETH", "FCEL", "MSFT", "NVDA", "STT", "TEXU",
                       "AIOS", "AXTI", "MXL", "SPY"}, "return_pct": 19.25}
    r = assess_pair(ctl, trt)
    assert r["verdict"] == "VOID"
    assert abs(r["overlap"] - 0.20) < 1e-9
    assert r["shared"] == ["AIOS", "AXTI", "MXL", "SPY"]
    assert "measures which names discovery drew" in r["reason"]


def test_VOID_outranks_NOISE():
    """A contaminated pair must never be reported as 'inside the noise floor'.

    That phrasing implies the arms were comparable and the lever did nothing, which is a
    stronger claim than the data supports — it is the exact mistake this module exists to
    prevent, and the delta here (-1.28pp) is small enough to invite it.
    """
    ctl = {"symbols": {"A", "B", "C", "D"}, "return_pct": 20.53}
    trt = {"symbols": {"W", "X", "Y", "D"}, "return_pct": 19.25}
    assert assess_pair(ctl, trt)["verdict"] == "VOID"


def test_a_comparable_pair_inside_dispersion_is_NOISE():
    shared = {"A", "B", "C", "D", "E"}
    r = assess_pair({"symbols": shared, "return_pct": 10.0},
                    {"symbols": shared, "return_pct": 14.0})
    assert r["verdict"] == "NOISE"
    assert r["overlap"] == 1.0


def test_a_comparable_pair_outside_dispersion_is_READABLE():
    shared = {"A", "B", "C", "D", "E"}
    r = assess_pair({"symbols": shared, "return_pct": 5.0},
                    {"symbols": shared, "return_pct": 25.0})
    assert r["verdict"] == "READABLE"
    assert "still n=1" in r["reason"]


def test_a_stopped_run_that_printed_no_summary_is_VOID_not_zero():
    """bt 443154 stopped at 97.65% and printed no P&L block.

    An empty universe must read as 'cannot compare', never as a clean 0% overlap or a
    real return — reading a stopped run's P&L is a documented error here.
    """
    r = assess_pair({"symbols": set(), "return_pct": None},
                    {"symbols": {"A", "B"}, "return_pct": 8.19})
    assert r["verdict"] == "VOID"
    assert "traded nothing" in r["reason"]


def test_a_missing_return_is_VOID_even_when_universes_match():
    shared = {"A", "B", "C"}
    r = assess_pair({"symbols": shared, "return_pct": None},
                    {"symbols": shared, "return_pct": 12.0})
    assert r["verdict"] == "VOID"


def test_the_thresholds_are_the_measured_ones():
    """Pin the constants: they are measurements, not preferences.

    ~10pp is the same-config dispersion measured on bt 873929 (+16.41%) vs bt 523085
    (+6.00%). If someone loosens these, it should be a deliberate edit with a reason.
    """
    assert MEASURED_DISPERSION_PP == 10.0
    assert DEFAULT_MIN_OVERLAP == 0.60


# --------------------------------------------------------------------------
# cold-start dispersion (measured 2026-08-16 by an explicit A/A)
# --------------------------------------------------------------------------
def test_the_cold_floor_is_the_measured_one():
    """bt 479057 vs bt 193668: same doc and window, each preceded by a verified cold
    clear, produced 100% overlap and +10.1463% vs +10.1586% — 0.012pp apart.

    0.5 is set with an order of magnitude of headroom over that, because one A/A pair
    is not a distribution.
    """
    from pair_validity import COLD_START_DISPERSION_PP
    assert COLD_START_DISPERSION_PP == 0.5
    assert COLD_START_DISPERSION_PP < MEASURED_DISPERSION_PP


def test_the_real_AA_pair_reads_as_NOISE_on_either_floor():
    shared = {"MSFT", "NVDA", "NVTS", "OIH", "RIVN", "SPY", "VDE"}
    ctl = {"symbols": shared, "return_pct": 10.1463}
    trt = {"symbols": shared, "return_pct": 10.1586}
    assert assess_pair(ctl, trt)["verdict"] == "NOISE"
    assert assess_pair(ctl, trt, cold_start=True)["verdict"] == "NOISE"
    assert assess_pair(ctl, trt)["overlap"] == 1.0


def test_a_3pp_lever_is_INVISIBLE_warm_and_VISIBLE_cold():
    """This is the whole reason the project returned nulls for months.

    Against a 10pp floor a genuine 3pp improvement is indistinguishable from noise.
    Against the cold floor it is a result.
    """
    shared = {"A", "B", "C", "D", "E"}
    ctl = {"symbols": shared, "return_pct": 10.0}
    trt = {"symbols": shared, "return_pct": 13.0}
    assert assess_pair(ctl, trt)["verdict"] == "NOISE"
    assert assess_pair(ctl, trt, cold_start=True)["verdict"] == "READABLE"


def test_cold_start_does_NOT_rescue_a_contaminated_pair():
    """Overlap is checked before dispersion, so a cold floor cannot launder a VOID."""
    ctl = {"symbols": {"A", "B", "C", "D"}, "return_pct": 10.0}
    trt = {"symbols": {"W", "X", "Y", "D"}, "return_pct": 30.0}
    assert assess_pair(ctl, trt, cold_start=True)["verdict"] == "VOID"
