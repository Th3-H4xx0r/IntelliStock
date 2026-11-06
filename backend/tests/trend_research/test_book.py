"""TR book rules and trailing stop (Task 8) -- pure, no DB, no network.

The ledger row is the research output (spec C2); turning it into weights is
this module's deterministic job: drop symbols the lane cannot or may not
trade, keep holdings until their story breaks, swap only for a clearly better
idea, cap single names and the leveraged+inverse slice, BIL is the remainder.
"""
from __future__ import annotations

import os
import sys

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from trend_research import book  # noqa: E402


def note(sym, conviction=7.0, status="intact"):
    return {"symbol": sym, "conviction": conviction, "thesis": "t", "breaks_if": [],
            "priced_in": "partly", "thesis_status": status, "risks": ""}


def pick(sym, conviction=7.0, weight=0.10, role="core"):
    return {"symbol": sym, "conviction": conviction, "target_weight": weight, "role": role}


def row(notes=(), picks=(), recommended=(), defensive_share=0.0):
    return {"notes": list(notes),
            "book": {"picks": list(picks), "defensive_share": defensive_share, "notes": ""},
            "recommended": [{"symbol": s, "thesis": "t"} for s in recommended]}


def all_bars(_sym):
    return True


def picks_row(specs, **kw):
    """specs: (symbol, conviction, weight[, role]) -> row with matching notes."""
    ps = [pick(*s) for s in specs]
    ns = [note(s[0], s[1]) for s in specs]
    return row(notes=ns, picks=ps, **kw)


def non_bil(weights):
    return {s: w for s, w in weights.items() if s != "BIL"}


# --- symbols ---------------------------------------------------------------


def test_normalize_symbol():
    assert book.normalize_symbol("nvda") == "NVDA"
    assert book.normalize_symbol("TSM.US") == "TSM"
    assert book.normalize_symbol("brk-b") == "BRK.B"
    assert book.normalize_symbol(" $aapl ") == "AAPL"
    assert book.normalize_symbol("BRK/B") == "BRK.B"
    assert book.normalize_symbol("BRK.B") == "BRK.B"
    assert book.normalize_symbol("NASDAQ:NVDA") == "NVDA"
    assert book.normalize_symbol("SAP.DE") is None          # non-US listing
    assert book.normalize_symbol("") is None
    assert book.normalize_symbol("NOT A TICKER") is None
    assert book.normalize_symbol(None) is None


def test_is_allowed_symbol():
    assert book.is_allowed_symbol("GLD", is_etf=True)
    assert book.is_allowed_symbol("TQQQ", is_etf=True)
    assert book.is_allowed_symbol("SQQQ", is_etf=True)
    assert book.is_allowed_symbol("BIL", is_etf=True)
    assert not book.is_allowed_symbol("ITA", is_etf=True)
    assert not book.is_allowed_symbol("SMH", is_etf=True)
    # A theme ETF passed as a stock is still a theme ETF.
    assert not book.is_allowed_symbol("XBI", is_etf=False)
    assert book.is_allowed_symbol("NVDA", is_etf=False)
    assert book.is_allowed_symbol("BRK.B", is_etf=False)
    assert not book.is_allowed_symbol("nvda", is_etf=False)  # not normalized
    assert not book.is_allowed_symbol("NVDA", is_etf=True)   # not an allowed ETF


def test_etf_sets():
    assert {"GLD", "SLV", "USO", "XLE", "CPER", "URA"} <= book.COMMODITY_ETFS
    assert {"BIL", "TLT", "XLU", "XLP"} <= book.DEFENSIVE_ETFS
    assert {"SH", "SQQQ"} <= book.INVERSE_ETFS
    assert {"TQQQ", "SOXL"} <= book.LEVERAGED_ETFS
    assert book.ALL_ALLOWED_ETFS == (book.COMMODITY_ETFS | book.DEFENSIVE_ETFS
                                     | book.INVERSE_ETFS | book.LEVERAGED_ETFS)
    assert {"ITA", "SMH", "XBI", "ARKK", "SOXX", "XLK"} <= book.KNOWN_THEME_ETFS
    assert not (book.KNOWN_THEME_ETFS & book.ALL_ALLOWED_ETFS)
    assert book.is_etf_symbol("ITA") and book.is_etf_symbol("GLD")
    assert not book.is_etf_symbol("NVDA")


def test_disallowed_etf_dropped_with_reason():
    r = picks_row([("ITA", 9.0, 0.10), ("SMH", 9.0, 0.10), ("GLD", 8.0, 0.10),
                   ("NVDA", 8.0, 0.10)])
    weights, log = book.target_book(r, held={}, has_bars=all_bars, cfg={})
    assert "ITA" not in weights and "SMH" not in weights
    assert weights["GLD"] == pytest.approx(0.10)
    assert weights["NVDA"] == pytest.approx(0.10)
    assert any("ITA" in line and "ETF" in line for line in log)
    assert any("SMH" in line and "ETF" in line for line in log)


def test_bad_symbols_dropped_before_book_with_reason():
    """Review focus 3: no bars, delisted/renamed, lowercase/suffixed, theme ETF."""
    bars = {"NVDA", "TSM", "AMD"}

    def has_bars(sym):
        return sym in bars

    r = picks_row([("nvda", 8.0, 0.10),        # lowercase -> NVDA, kept
                   ("TSM.US", 8.0, 0.10),      # exchange suffix -> TSM, kept
                   ("FB", 8.0, 0.10),          # renamed (META): no bars at decision date
                   ("SIVB", 8.0, 0.10),        # delisted: no bars
                   ("XBI", 8.0, 0.10),         # forbidden theme ETF
                   ("SAP.DE", 8.0, 0.10),      # not a US listing
                   ("AMD", 8.0, 0.10)])
    weights, log = book.target_book(r, held={}, has_bars=has_bars, cfg={})
    assert set(non_bil(weights)) == {"NVDA", "TSM", "AMD"}
    for bad, why in (("FB", "no bars"), ("SIVB", "no bars"), ("XBI", "ETF"),
                     ("SAP.DE", "symbol")):
        assert any(bad in line and why in line for line in log), (bad, log)


# --- caps -------------------------------------------------------------------


def test_caps_single_025():
    r = picks_row([("NVDA", 9.0, 0.40), ("AMD", 8.0, 0.10)])
    weights, log = book.target_book(r, held={}, has_bars=all_bars, cfg={})
    assert weights["NVDA"] == pytest.approx(0.25)
    assert weights["AMD"] == pytest.approx(0.10)
    assert weights["BIL"] == pytest.approx(0.65)
    assert any("NVDA" in line and "0.25" in line for line in log)
    assert max(non_bil(weights).values()) <= 0.25 + 1e-12


def test_levered_plus_inverse_capped_020():
    r = picks_row([("TQQQ", 9.0, 0.15, "leveraged"), ("SQQQ", 9.0, 0.15, "inverse"),
                   ("NVDA", 8.0, 0.10)])
    weights, log = book.target_book(r, held={}, has_bars=all_bars, cfg={})
    assert weights["TQQQ"] + weights["SQQQ"] == pytest.approx(0.20)
    assert weights["TQQQ"] == pytest.approx(0.10)       # pro-rata
    assert weights["SQQQ"] == pytest.approx(0.10)
    assert weights["NVDA"] == pytest.approx(0.10)
    assert weights["BIL"] == pytest.approx(0.70)
    assert any("leveraged+inverse" in line for line in log)


def test_levered_cap_counts_by_symbol_not_role():
    # SOXL mislabelled "core" and SH labelled "defensive" still count.
    r = picks_row([("SOXL", 9.0, 0.20, "core"), ("SH", 8.0, 0.20, "defensive")],
                  defensive_share=0.20)
    weights, _ = book.target_book(r, held={}, has_bars=all_bars, cfg={})
    assert weights["SOXL"] + weights["SH"] == pytest.approx(0.20)


def test_cfg_overrides_caps():
    r = picks_row([("NVDA", 9.0, 0.25), ("TQQQ", 9.0, 0.20, "leveraged")])
    cfg = {"max_single_weight": 0.15, "max_levered_inverse_weight": 0.10}
    weights, _ = book.target_book(r, held={}, has_bars=all_bars, cfg=cfg)
    assert weights["NVDA"] == pytest.approx(0.15)
    assert weights["TQQQ"] == pytest.approx(0.10)


# --- holdings, swaps, exits -------------------------------------------------


def _full_book_row(new_conviction):
    held_syms = ["HA" + chr(65 + i) for i in range(12)]          # HAA..HAL
    notes = [note(s, 7.0 if s == "HAA" else 8.0) for s in held_syms]
    notes.append(note("NEWCO", new_conviction))
    picks = [pick(s, 8.0, 0.08) for s in held_syms[1:]] + [pick("NEWCO", new_conviction, 0.08)]
    return row(notes=notes, picks=picks), {s: 1.0 / 12 for s in held_syms}


def test_swap_requires_margin():
    r, held = _full_book_row(8.9)
    weights, log = book.target_book(r, held=held, has_bars=all_bars, cfg={})
    assert "NEWCO" not in weights
    assert "HAA" in weights                                    # weakest kept
    assert len(non_bil(weights)) == 12

    r, held = _full_book_row(9.0)
    weights, log = book.target_book(r, held=held, has_bars=all_bars, cfg={})
    assert "NEWCO" in weights
    assert "HAA" not in weights
    assert len(non_bil(weights)) == 12
    assert any("swap" in line and "HAA" in line and "NEWCO" in line for line in log)


def test_swap_margin_from_cfg():
    r, held = _full_book_row(8.0)
    weights, _ = book.target_book(r, held=held, has_bars=all_bars, cfg={"swap_margin": 1.0})
    assert "NEWCO" in weights and "HAA" not in weights


def test_broken_thesis_exits():
    r = row(notes=[note("NVDA", 8.0, "broken"), note("AMD", 7.0)],
            picks=[pick("NVDA", 8.0, 0.10), pick("AMD", 7.0, 0.10)])
    weights, log = book.target_book(r, held={"NVDA": 0.10, "AMD": 0.10},
                                    has_bars=all_bars, cfg={})
    assert "NVDA" not in weights
    assert weights["AMD"] == pytest.approx(0.10)
    assert any("NVDA" in line and "broken" in line for line in log)


def test_latest_note_wins():
    r = row(notes=[note("NVDA", 8.0, "intact"), note("NVDA", 6.0, "broken")])
    weights, _ = book.target_book(r, held={"NVDA": 0.10}, has_bars=all_bars, cfg={})
    assert "NVDA" not in weights


def test_holding_absent_from_notes_and_recommended_exits():
    r = picks_row([("AMD", 7.0, 0.10)], recommended=["MU"])
    weights, log = book.target_book(r, held={"NVDA": 0.10, "MU": 0.10, "AMD": 0.10},
                                    has_bars=all_bars, cfg={})
    assert "NVDA" not in weights
    assert "MU" in weights                     # still recommended: hold
    assert "AMD" in weights
    assert any("NVDA" in line and "absent" in line for line in log)


def test_kept_holding_not_in_picks_gets_default_weight():
    r = row(notes=[note("MU", 6.0)], picks=[pick("AMD", 7.0, 0.10)])
    weights, _ = book.target_book(r, held={"MU": 0.30}, has_bars=all_bars,
                                  cfg={"max_positions": 10})
    assert weights["MU"] == pytest.approx(0.10)          # 1 / max_positions


def test_held_first_then_conviction_when_over_max():
    held = {"HA" + chr(65 + i): 0.1 for i in range(10)}             # 10 held, conviction 5
    notes = [note(s, 5.0) for s in held]
    specs = [("NEW%s" % c, 6.0 + i * 0.1, 0.08) for i, c in enumerate("ABCD")]
    notes += [note(s[0], s[1]) for s in specs]
    r = row(notes=notes, picks=[pick(*s) for s in specs])
    weights, _ = book.target_book(r, held=held, has_bars=all_bars, cfg={"max_positions": 12})
    names = set(non_bil(weights))
    assert set(held) <= names                                       # all held kept
    assert names - set(held) == {"NEWD", "NEWC"}                    # top-2 by conviction


def test_fewer_than_eight_names_rest_in_bil():
    r = picks_row([("NVDA", 8.0, 0.10), ("AMD", 7.0, 0.10), ("GLD", 6.0, 0.10)])
    weights, _ = book.target_book(r, held={}, has_bars=all_bars, cfg={})
    assert set(non_bil(weights)) == {"NVDA", "AMD", "GLD"}
    assert weights["BIL"] == pytest.approx(0.70)


def test_max_positions_clamped_to_8_12():
    specs = [("S%s" % c, 9.0 - i * 0.1, 0.05) for i, c in enumerate("ABCDEFGHIJKLMNOP")]
    weights, log = book.target_book(picks_row(specs), held={}, has_bars=all_bars,
                                    cfg={"max_positions": 3})
    assert len(non_bil(weights)) == 8
    weights, _ = book.target_book(picks_row(specs), held={}, has_bars=all_bars,
                                  cfg={"max_positions": 20})
    assert len(non_bil(weights)) == 12


def test_new_pick_with_broken_note_not_bought():
    r = row(notes=[note("NVDA", 9.0, "broken")], picks=[pick("NVDA", 9.0, 0.10)])
    weights, _ = book.target_book(r, held={}, has_bars=all_bars, cfg={})
    assert "NVDA" not in weights


def test_held_without_bars_dropped():
    r = picks_row([("NVDA", 8.0, 0.10)])
    weights, log = book.target_book(r, held={"NVDA": 0.1}, has_bars=lambda s: False, cfg={})
    assert non_bil(weights) == {}
    assert weights["BIL"] == pytest.approx(1.0)


# --- defensive share and BIL ------------------------------------------------


def test_defensive_share_routed_to_defensive_picks():
    r = picks_row([("NVDA", 8.0, 0.25), ("AMD", 8.0, 0.25), ("MU", 8.0, 0.25),
                   ("AVGO", 8.0, 0.25),
                   ("TLT", 6.0, 0.10, "defensive"), ("XLU", 6.0, 0.30, "defensive")],
                  defensive_share=0.40)
    weights, _ = book.target_book(r, held={}, has_bars=all_bars, cfg={})
    core = sum(weights[s] for s in ("NVDA", "AMD", "MU", "AVGO"))
    assert core == pytest.approx(0.60)                       # scaled into 1 - share
    assert weights["TLT"] == pytest.approx(0.10)             # 0.40 split 1:3
    assert weights["XLU"] == pytest.approx(0.25)             # 0.30 capped at 0.25
    assert weights["BIL"] == pytest.approx(0.05)             # cap excess


def test_defensive_share_goes_to_bil_without_defensive_picks():
    r = picks_row([("NVDA", 8.0, 0.25), ("AMD", 8.0, 0.25), ("MU", 8.0, 0.25),
                   ("AVGO", 8.0, 0.25)], defensive_share=0.50)
    weights, log = book.target_book(r, held={}, has_bars=all_bars, cfg={})
    assert sum(non_bil(weights).values()) == pytest.approx(0.50)
    assert weights["BIL"] == pytest.approx(0.50)
    assert any("BIL" in line and "defensive" in line for line in log)


def test_weights_sum_to_one_with_bil():
    cases = [
        picks_row([("NVDA", 8.0, 0.25), ("AMD", 8.0, 0.25), ("MU", 8.0, 0.25),
                   ("AVGO", 8.0, 0.25), ("TSM", 8.0, 0.25)]),            # over-full
        picks_row([("NVDA", 8.0, 0.05)]),                                 # under-full
        picks_row([("TQQQ", 8.0, 0.25, "leveraged"), ("GLD", 6.0, 0.2, "defensive")],
                  defensive_share=0.3),
        row(),                                                            # empty week
    ]
    for r in cases:
        weights, _ = book.target_book(r, held={"AMD": 0.2}, has_bars=all_bars, cfg={})
        assert "BIL" in weights
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-12)
        assert all(w > 0 for s, w in weights.items() if s != "BIL")
        assert weights["BIL"] >= 0
        assert all(w <= 0.25 + 1e-12 for w in non_bil(weights).values())


def test_bil_pick_is_not_a_position():
    r = picks_row([("BIL", 9.0, 0.25, "defensive"), ("NVDA", 8.0, 0.10)],
                  defensive_share=0.5)
    weights, _ = book.target_book(r, held={"BIL": 0.5}, has_bars=all_bars, cfg={})
    assert weights["BIL"] == pytest.approx(0.90)
    assert weights["NVDA"] == pytest.approx(0.10)


def test_target_book_is_deterministic_and_pure():
    r = picks_row([("NVDA", 8.0, 0.10), ("AMD", 8.0, 0.10), ("MU", 7.0, 0.10)])
    held = {"MU": 0.1}
    snapshot = repr((r, held))
    a = book.target_book(r, held=held, has_bars=all_bars, cfg={})
    b = book.target_book(r, held=held, has_bars=all_bars, cfg={})
    assert a == b
    assert repr((r, held)) == snapshot


# --- trailing stop ----------------------------------------------------------


def test_trailing_stop_at_80pct():
    assert book.trailing_stop_hits({"X": 100.0}, {"X": 80.0}) == ["X"]
    assert book.trailing_stop_hits({"X": 100.0}, {"X": 80.01}) == []
    assert book.trailing_stop_hits({"X": 100.0}, {"X": 79.0}) == ["X"]
    assert book.trailing_stop_hits({"X": 100.0}, {}) == []          # no close today
    assert book.trailing_stop_hits({"X": 100.0}, {"X": 90.0}, stop_pct=0.10) == ["X"]


def test_trailing_stop_sorted_and_ignores_bil():
    peaks = {"B": 10.0, "A": 10.0, "BIL": 100.0}
    closes = {"B": 7.0, "A": 7.0, "BIL": 1.0}
    assert book.trailing_stop_hits(peaks, closes) == ["A", "B"]


def test_update_peaks():
    peaks = {"NVDA": 100.0, "SOLD": 50.0}
    closes = {"NVDA": 95.0, "AMD": 40.0, "SOLD": 60.0, "MU": 10.0}
    out = book.update_peaks(peaks, closes, {"NVDA": 0.1, "AMD": 0.1, "MU": 0.1})
    assert out == {"NVDA": 100.0, "AMD": 40.0, "MU": 10.0}       # sold name forgotten
    out = book.update_peaks(out, {"NVDA": 120.0}, ["NVDA", "AMD"])
    assert out == {"NVDA": 120.0, "AMD": 40.0}                   # AMD: no close, keep peak
    assert peaks == {"NVDA": 100.0, "SOLD": 50.0}                # input not mutated


# --- review fix round 1 -----------------------------------------------------


def _twelve_held():
    held_syms = ["HA" + chr(65 + i) for i in range(12)]           # HAA..HAL, HAA weakest
    notes = [note(s, 7.0 if s == "HAA" else 8.0) for s in held_syms]
    return held_syms, notes, {s: 1.0 / 12 for s in held_syms}


def test_new_pick_at_zero_weight_is_not_a_buy():
    held_syms, notes, held = _twelve_held()
    notes.append(note("NEWCO", 10.0))
    r = row(notes=notes, picks=[pick("NEWCO", 10.0, 0.0)])
    weights, log = book.target_book(r, held=held, has_bars=all_bars, cfg={})
    assert "NEWCO" not in weights
    assert set(non_bil(weights)) == set(held_syms)                 # HAA not swapped out
    assert not any("swap" in line for line in log)
    assert "skip NEWCO: proposal weight 0" in log


def test_held_pick_at_zero_weight_exits():
    held_syms, notes, held = _twelve_held()
    r = row(notes=notes, picks=[pick("HAB", 8.0, 0.0)])
    weights, log = book.target_book(r, held=held, has_bars=all_bars, cfg={})
    assert "HAB" not in weights
    assert set(non_bil(weights)) == set(held_syms) - {"HAB"}
    assert "exit HAB: proposal weight 0" in log


def test_held_defensive_pick_at_zero_weight_exits():
    r = picks_row([("TLT", 6.0, 0.0, "defensive"), ("XLU", 6.0, 0.10, "defensive")],
                  defensive_share=0.30)
    weights, log = book.target_book(r, held={"TLT": 0.1}, has_bars=all_bars, cfg={})
    assert "TLT" not in weights
    assert weights["XLU"] == pytest.approx(0.25)
    assert "exit TLT: proposal weight 0" in log


def test_nan_close_does_not_disable_stop():
    nan = float("nan")
    peaks = book.update_peaks({}, {"X": nan}, ["X"])               # bad first print
    assert peaks == {}
    peaks = book.update_peaks(peaks, {"X": 100.0}, ["X"])
    assert peaks == {"X": 100.0}
    peaks = book.update_peaks(peaks, {"X": nan}, ["X"])            # bad print later
    assert peaks == {"X": 100.0}
    assert book.trailing_stop_hits(peaks, {"X": nan}) == []
    assert book.trailing_stop_hits(peaks, {"X": 80.0}) == ["X"]
    # A peak already poisoned by NaN is replaced by the next finite close.
    peaks = book.update_peaks({"X": nan}, {"X": 50.0}, ["X"])
    assert peaks == {"X": 50.0}
    assert book.trailing_stop_hits(peaks, {"X": 40.0}) == ["X"]
    assert book.trailing_stop_hits({"X": nan}, {"X": 1.0}) == []
    assert book.update_peaks({}, {"X": float("inf")}, ["X"]) == {}


def test_defensive_picks_take_slots_in_full_book():
    held_syms, notes, held = _twelve_held()
    notes.append(note("TLT", 6.0))
    r = row(notes=notes, picks=[pick("TLT", 6.0, 0.20, "defensive")], defensive_share=0.20)
    weights, log = book.target_book(r, held=held, has_bars=all_bars, cfg={})
    assert "TLT" in weights
    assert "HAA" not in weights                                    # weakest forced out
    assert len(non_bil(weights)) == 12
    assert "exit HAA: no slot left (conviction 7.0)" in log


def test_defensive_share_zero_held_defensive_pick_log():
    r = picks_row([("TLT", 6.0, 0.10, "defensive"), ("XLU", 6.0, 0.10, "defensive")])
    weights, log = book.target_book(r, held={"TLT": 0.1}, has_bars=all_bars, cfg={})
    assert "TLT" in weights                                        # kept as a holding
    assert "XLU" not in weights
    assert "TLT: defensive_share 0, judged as a holding" in log
    assert "skip XLU: defensive pick with defensive_share 0" in log
    assert not any(line.startswith("skip TLT") for line in log)


def test_absent_exit_log_names_all_three_sources():
    r = picks_row([("AMD", 7.0, 0.10)])
    _, log = book.target_book(r, held={"NVDA": 0.1}, has_bars=all_bars, cfg={})
    assert "exit NVDA: absent from notes, recommended and picks" in log


# --- fix wave B ------------------------------------------------------------------


def test_snap_weights_drops_float_residue_and_keeps_bil():
    # 1.0 - sum(...) reads ~1e-16 rather than 0: as a target that is a TRIM
    # (sell_fraction 0.99999...), which leaves dust the emulator never sells.
    w = book.snap_weights({"AAA": 0.1, "BBB": 0.2, "CCC": 0.7, "DDD": 1e-9,
                           "BIL": 1.0 - (0.1 + 0.2 + 0.7)})
    assert w == {"AAA": 0.1, "BBB": 0.2, "CCC": 0.7, "BIL": 0.0}
    assert book.snap_weights({"AAA": 0.25})["BIL"] == pytest.approx(0.75)
    assert book.snap_weights({}) == {"BIL": 1.0}


def test_target_book_bil_residue_is_exactly_zero():
    # Ten picks at 0.1 sum to 0.9999999999999999 in floats.
    specs = [("N%s" % chr(65 + i), 7.0, 0.1) for i in range(10)]
    w, _ = book.target_book(picks_row(specs), held={}, has_bars=all_bars,
                            cfg={"max_positions": 10})
    assert w["BIL"] == 0.0
    assert all(v >= book.WEIGHT_FLOOR for s, v in w.items() if s != "BIL")


def test_unresearched_pick_is_never_bought():
    """NVDX is a 2x NVDA ETF missing from KNOWN_THEME_ETFS: as a pick with no
    research note it would be bought as a stock, outside the 20% leveraged
    cap (final review I5). Dropped with a reason; an allowed ETF pick needs
    no note."""
    r = row(notes=[note("NVDA")],
            picks=[pick("NVDA"), pick("NVDX", 9.0, 0.2), pick("GLD", 5.0, 0.1)])
    w, log = book.target_book(r, held={}, has_bars=all_bars, cfg={})
    assert "NVDX" not in w
    assert w["NVDA"] == pytest.approx(0.10) and w["GLD"] == pytest.approx(0.10)
    assert "drop NVDX (pick): no research note this week" in log


def test_new_pick_without_bars_leaves_its_slot_to_an_absent_holding():
    """The row swaps OLD out for NEW, but NEW has no bars at the decision:
    OLD keeps the slot instead of being liquidated into BIL. A broken
    holding still exits."""
    held = {s: 0.11 for s in ("HA", "HB", "HC", "OLD", "BRK")}
    specs = [("HA", 7.0, 0.11), ("HB", 7.0, 0.11), ("HC", 7.0, 0.11), ("NEW", 8.0, 0.11)]
    r = picks_row(specs)
    r["notes"].append(note("BRK", status="broken"))
    stats = {}
    w, log = book.target_book(r, held=held, has_bars=lambda s: s != "NEW", cfg={},
                              stats=stats)
    assert stats == {"stock_picks": ["HA", "HB", "HC", "NEW"], "picks_without_bars": ["NEW"]}
    assert "NEW" not in w and "BRK" not in w
    assert w["OLD"] == pytest.approx(1 / 12)                # kept at the default weight
    assert any(line.startswith("keep OLD") for line in log)
    assert "exit BRK: thesis broken" in log

    # With bars, the row's swap goes through as before.
    w, log = book.target_book(r, held=held, has_bars=all_bars, cfg={})
    assert "OLD" not in w and w["NEW"] == pytest.approx(0.11)
    assert "exit OLD: absent from notes, recommended and picks" in log
