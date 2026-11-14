"""The market / sector / stock-specific split (move_analysis.decompose)."""
from __future__ import annotations

import math
import os
import random
import sys

import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from move_analysis import decompose as dc  # noqa: E402


def _prices(rets, start=100.0):
    out = [start]
    for r in rets:
        out.append(out[-1] * (1 + r))
    return out


def _series(n=200, beta=1.5, gamma=0.8, seed=7, noise=0.002):
    rng = random.Random(seed)
    market = [rng.gauss(0, 0.01) for _ in range(n)]
    sector_own = [rng.gauss(0, 0.006) for _ in range(n)]
    sector = [1.1 * m + x for m, x in zip(market, sector_own)]
    stock = [beta * m + gamma * x + rng.gauss(0, noise)
             for m, x in zip(market, sector_own)]
    dates = [f"d{i:04d}" for i in range(n + 1)]
    return dates, stock, market, sector


def test_parts_sum_to_the_return_on_every_bar():
    dates, stock, market, sector = _series()
    p, m, s = _prices(stock), _prices(market), _prices(sector)
    bars = dc.decompose(dates, p, p, m, s)
    checked = 0
    for bar in bars:
        if bar.market_pct is None:
            continue
        assert math.isclose(bar.market_pct + bar.sector_pct + bar.specific_pct,
                            bar.ret_pct, abs_tol=1e-9)
        checked += 1
    assert checked == len(bars) - 1


def test_recovers_a_known_beta_and_attributes_the_sector_part():
    dates, stock, market, sector = _series(n=260, beta=1.5, gamma=0.8, noise=0.0005)
    p, m, s = _prices(stock), _prices(market), _prices(sector)
    last = dc.decompose(dates, p, p, m, s)[-1]
    assert last.beta == pytest.approx(1.5, abs=0.08)
    # With little idiosyncratic noise almost nothing is left stock-specific.
    assert abs(last.specific_pct) < 0.5


def test_a_stock_specific_jump_is_flagged_and_stays_specific():
    dates, stock, market, sector = _series()
    stock[-1] = market[-1] * 1.5 + 0.09
    p, m, s = _prices(stock), _prices(market), _prices(sector)
    last = dc.decompose(dates, p, p, m, s)[-1]
    assert last.flagged and not last.market_driven
    assert last.specific_pct == pytest.approx(9.0, abs=1.5)


def test_a_market_wide_drop_is_flagged_as_market_driven():
    dates, stock, market, sector = _series(noise=0.0005)
    market[-1] = -0.05
    sector[-1] = 1.1 * -0.05
    stock[-1] = 1.5 * -0.05
    p, m, s = _prices(stock), _prices(market), _prices(sector)
    last = dc.decompose(dates, p, p, m, s, min_move_pct=3.0)[-1]
    assert last.ret_pct == pytest.approx(-7.5, abs=0.01)
    assert last.flagged and last.market_driven
    assert abs(last.market_pct) > abs(last.specific_pct)


def test_an_ordinary_day_is_not_flagged():
    dates, stock, market, sector = _series()
    p, m, s = _prices(stock), _prices(market), _prices(sector)
    bars = dc.decompose(dates, p, p, m, s)
    assert sum(1 for b in bars if b.flagged) <= 3


def test_short_history_uses_beta_one_and_no_sector_part():
    dates = [f"d{i}" for i in range(6)]
    p = [100, 101, 99, 104, 103, 110]
    m = [50, 50.5, 50, 50.2, 50.1, 50.3]
    bars = dc.decompose(dates, p, p, m, m)
    assert bars[0].ret_pct is None and bars[0].flagged is False
    for bar in bars[1:]:
        assert bar.beta == 1.0
        assert bar.sector_pct == 0.0
        assert math.isclose(bar.market_pct + bar.specific_pct, bar.ret_pct, abs_tol=1e-9)
    assert bars[-1].flagged  # +6.8% clears 2 x min_move_pct on its own


def test_thresholds_move_the_flag():
    dates, stock, market, sector = _series()
    stock[-1] = market[-1] * 1.5 + 0.04
    p, m, s = _prices(stock), _prices(market), _prices(sector)
    assert dc.decompose(dates, p, p, m, s, min_move_pct=3.0)[-1].flagged
    assert not dc.decompose(dates, p, p, m, s, min_move_pct=10.0)[-1].flagged


def test_missing_market_price_leaves_the_bar_unsplit():
    dates = ["a", "b", "c"]
    bars = dc.decompose(dates, [10, 11, 12], [10, 11, 12], [5, None, 6])
    assert bars[1].market_pct is None and bars[1].flagged is False
    assert bars[1].ret_pct == pytest.approx(10.0)
    assert bars[2].market_pct is None  # no return across the gap


def test_returns_come_from_the_adjusted_series_not_the_shown_close():
    # A 2-for-1 split: shown close halves, adjusted basis does not.
    dates = ["a", "b"]
    bars = dc.decompose(dates, [200, 100], [100, 100], [50, 50])
    assert bars[1].close == 100.0
    assert bars[1].ret_pct == pytest.approx(0.0)
    assert not bars[1].flagged


def test_mismatched_lengths_are_rejected():
    with pytest.raises(ValueError):
        dc.decompose(["a", "b"], [1, 2], [1, 2], [1])


def test_as_dict_matches_the_api_bar_shape():
    dates, stock, market, sector = _series(n=40)
    p, m, s = _prices(stock), _prices(market), _prices(sector)
    row = dc.decompose(dates, p, p, m, s, volumes=[1000.0] * len(p))[-1].as_dict()
    assert set(row) == {"date", "close", "volume", "ret_pct", "market_pct",
                        "sector_pct", "specific_pct", "flagged"}
    assert row["volume"] == 1000.0
