"""A negative lag is proof the parse is wrong, not evidence of a fast entry.

The first version of this measurement read "first seen" from two momentum-specific log lines.
Against bt 826225 it reported OILT at -27 days and SKYQ at -9 — names bought before they were
first seen — because names entering via the news or graph lanes never print those lines. It also
silently dropped most bought names (2 of 8 survived), which would have made an eight-name run
look like a two-name one.

Fixtures below are the VERBATIM line shapes the runs emit, log prefix included.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from entry_lag import compare, measure, summarize  # noqa: E402

# Real shapes from bt 333727 / bt 826225.
CONSIDERED = ("[2026-08-15 19:45:53] [BROKER] {sym} @ {date} 14:00:00 (${px}): "
              "hold action_intent=hold (weighted scores from 1 strategies)")
FILL = ("[2026-08-15 19:14:55] [BROKER] [execution] FILL BUY {sym} qty=13.01 "
        "cumulative=13.01 price=90.78 fees=0.03 quote={date} 15:00:00+00:00 "
        "model=equity-measured-v3-nbbo23 source=main_signal")


def _log(*rows):
    out = []
    for kind, sym, date in rows:
        tpl = CONSIDERED if kind == "see" else FILL
        out.append(tpl.format(sym=sym, date=date, px="100.00"))
    return "\n".join(out)


def test_lag_is_measured_from_the_real_line_shapes():
    text = _log(("see", "AAOI", "2026-04-06"), ("buy", "AAOI", "2026-04-20"))
    r = measure(text)
    assert r["parse_errors"] == []
    assert r["entries"] == [{"symbol": "AAOI", "considered": "2026-04-06",
                            "bought": "2026-04-20", "lag_days": 14}]


def test_the_FIRST_evaluation_wins_not_the_last():
    text = _log(("see", "AEHR", "2026-04-01"), ("see", "AEHR", "2026-04-20"),
                ("buy", "AEHR", "2026-04-24"))
    assert measure(text)["entries"][0]["lag_days"] == 23


def test_a_news_lane_name_is_NOT_missed():
    """THE REGRESSION.

    A name that never prints a momentum-discovery line must still be measured — missing it is
    what produced negative lags and dropped 6 of 8 names.
    """
    text = _log(("see", "MSFT", "2026-04-03"), ("buy", "MSFT", "2026-04-06"))
    r = measure(text)
    assert [e["symbol"] for e in r["entries"]] == ["MSFT"]
    assert r["entries"][0]["lag_days"] == 3


def test_a_negative_lag_is_a_PARSE_ERROR_not_a_datum():
    """Clamping it to zero would turn a broken parse into a flattering result."""
    text = _log(("see", "OILT", "2026-05-04"), ("buy", "OILT", "2026-04-07"))
    r = measure(text)
    assert r["entries"] == []
    assert len(r["parse_errors"]) == 1
    assert r["parse_errors"][0]["symbol"] == "OILT"
    assert "impossible" in r["parse_errors"][0]["reason"]
    assert r["stats"]["n"] == 0


def test_a_buy_with_no_evaluation_is_also_flagged():
    r = measure(_log(("buy", "GHOST", "2026-04-07")))
    assert r["entries"] == []
    assert r["parse_errors"][0]["reason"].startswith("bought but never evaluated")


def test_the_index_core_is_excluded():
    """SPY is the funding leg, not an alpha entry."""
    text = _log(("see", "SPY", "2026-04-01"), ("buy", "SPY", "2026-04-06"),
                ("see", "MXL", "2026-04-16"), ("buy", "MXL", "2026-04-28"))
    assert [e["symbol"] for e in measure(text)["entries"]] == ["MXL"]


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------
def test_the_slow_tail_is_reported_not_just_the_median():
    """bt 333727's median was 3 days while AAOI/AEHR/AXTI waited 14/23/35 — and those three
    are exactly the names it lost money on. A median alone hides that."""
    entries = [{"symbol": s, "lag_days": d} for s, d in
               [("AIFD", 0), ("BC", 0), ("D", 0), ("AIQ", 1), ("BOTZ", 1), ("RIVN", 3),
                ("AIOS", 12), ("MXL", 12), ("AAOI", 14), ("AEHR", 23), ("AXTI", 35)]]
    s = summarize(entries)
    assert s["n"] == 11
    assert s["median_days"] == 3
    assert s["max_days"] == 35
    assert s["slow_count"] == 5
    assert "AXTI" in s["slow_symbols"]


def test_empty_input_does_not_raise():
    assert measure("")["stats"] == {"n": 0}
    assert summarize([]) == {"n": 0}


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------
def test_low_overlap_is_reported_as_NOT_comparable():
    """The real pair shared 11% of traded names; the caveat must travel with the numbers."""
    ctl = measure(_log(("see", "AAOI", "2026-04-06"), ("buy", "AAOI", "2026-04-20"),
                       ("see", "AEHR", "2026-04-01"), ("buy", "AEHR", "2026-04-24")))
    trt = measure(_log(("see", "MSFT", "2026-04-03"), ("buy", "MSFT", "2026-04-06"),
                       ("see", "NVDA", "2026-04-03"), ("buy", "NVDA", "2026-04-06")))
    c = compare(ctl, trt)
    assert c["comparable"] is False
    assert c["overlap"] == 0.0
    assert "per-name property" in c["caveat"]


def test_full_overlap_is_comparable_and_carries_no_caveat():
    same = [("see", "MXL", "2026-04-16"), ("buy", "MXL", "2026-04-28"),
            ("see", "AIQ", "2026-04-01"), ("buy", "AIQ", "2026-04-02")]
    c = compare(measure(_log(*same)), measure(_log(*same)))
    assert c["comparable"] is True
    assert c["caveat"] == ""
    assert c["shared_symbols"] == ["AIQ", "MXL"]
