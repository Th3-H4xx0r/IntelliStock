from kalshi.clv import compute_clv, summarize_clv


def test_positive_clv_when_price_rose_after_entry():
    # bought YES at 48c, closed at 52c -> got in cheaper than the close
    assert abs(compute_clv(entry_cents=48, close_cents=52) - 0.04) < 1e-9


def test_negative_clv_when_price_fell():
    assert compute_clv(entry_cents=52, close_cents=44) < 0


def test_summarize_empty():
    assert summarize_clv([]) == {"overall": {"avg_clv": 0.0, "n": 0}, "by_league": {}}


def test_summarize_groups_by_league():
    rows = [
        {"league": "EPL", "clv": 0.02},
        {"league": "EPL", "clv": 0.04},
        {"league": "Serie B", "clv": -0.01},
    ]
    s = summarize_clv(rows)
    assert s["overall"]["n"] == 3
    assert abs(s["overall"]["avg_clv"] - (0.02 + 0.04 - 0.01) / 3) < 1e-9
    assert s["by_league"]["EPL"]["n"] == 2
    assert abs(s["by_league"]["EPL"]["avg_clv"] - 0.03) < 1e-9
