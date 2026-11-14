"""Split daily returns into market, sector and stock-specific parts. Pure
maths, no I/O.

For day t the betas are estimated on the ``lookback`` trading days BEFORE t,
so a day's own move never influences how it is split.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

LOOKBACK = 120
MIN_OBS = 30
DEFAULT_SIGMA = 2.5
DEFAULT_MIN_MOVE_PCT = 3.0


@dataclass(frozen=True)
class Bar:
    date: str
    close: float
    volume: Optional[float]
    ret_pct: Optional[float]
    market_pct: Optional[float]
    sector_pct: Optional[float]
    specific_pct: Optional[float]
    #: None when there was too little history to estimate it (1.0 was used).
    beta: Optional[float]
    flagged: bool
    #: True when the day is flagged but under half the move is stock-specific.
    market_driven: bool

    def as_dict(self) -> dict:
        return {
            "date": self.date,
            "close": self.close,
            "volume": self.volume,
            "ret_pct": _r(self.ret_pct),
            "market_pct": _r(self.market_pct),
            "sector_pct": _r(self.sector_pct),
            "specific_pct": _r(self.specific_pct),
            "flagged": self.flagged,
        }


def _r(value: Optional[float], places: int = 3) -> Optional[float]:
    return None if value is None else round(value, places)


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def returns(prices: Sequence[Optional[float]]) -> list:
    """Simple returns; None where either price is missing or not positive."""
    out: list = [None] if len(prices) else []
    for i in range(1, len(prices)):
        a, b = prices[i - 1], prices[i]
        if _finite(a) and _finite(b) and a > 0 and b > 0:
            out.append(b / a - 1.0)
        else:
            out.append(None)
    return out


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _slope(ys: Sequence[float], xs: Sequence[float]) -> Optional[float]:
    """cov(y, x) / var(x); None when x has no variance."""
    mx, my = _mean(xs), _mean(ys)
    var = sum((x - mx) ** 2 for x in xs)
    if var <= 1e-18:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var


def _sd(xs: Sequence[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def decompose(
    dates: Sequence[str],
    closes: Sequence[float],
    price_basis: Sequence[Optional[float]],
    market_basis: Sequence[Optional[float]],
    sector_basis: Optional[Sequence[Optional[float]]] = None,
    volumes: Optional[Sequence[Optional[float]]] = None,
    *,
    sigma: float = DEFAULT_SIGMA,
    min_move_pct: float = DEFAULT_MIN_MOVE_PCT,
    lookback: int = LOOKBACK,
    min_obs: int = MIN_OBS,
) -> list:
    """One ``Bar`` per date.

    ``closes`` are the prices shown to the reader. ``price_basis``,
    ``market_basis`` and ``sector_basis`` are the dividend- and split-adjusted
    series the returns come from, aligned with ``dates``.
    """
    n = len(dates)
    if not (len(closes) == len(price_basis) == len(market_basis) == n):
        raise ValueError("series must be the same length as dates")
    if sector_basis is not None and len(sector_basis) != n:
        raise ValueError("sector series must be the same length as dates")

    r = returns(price_basis)
    m = returns(market_basis)
    s = returns(sector_basis) if sector_basis is not None else [None] * n

    bars: list = []
    for i in range(n):
        vol = volumes[i] if volumes is not None else None
        vol = float(vol) if _finite(vol) else None
        if r[i] is None or m[i] is None:
            bars.append(Bar(dates[i], float(closes[i]), vol, _pct(r[i]), None,
                            None, None, None, False, False))
            continue

        lo = max(1, i - lookback)
        idx = [j for j in range(lo, i) if r[j] is not None and m[j] is not None]
        beta = 1.0
        estimated = False
        gamma = 0.0
        beta_s = None
        sd_specific = None
        use_sector = False
        if len(idx) >= min_obs:
            fitted = _slope([r[j] for j in idx], [m[j] for j in idx])
            if fitted is not None:
                beta = fitted
                estimated = True
            sidx = [j for j in idx if s[j] is not None]
            if s[i] is not None and len(sidx) >= min_obs:
                beta_s = _slope([s[j] for j in sidx], [m[j] for j in sidx])
                if beta_s is not None:
                    xs = [s[j] - beta_s * m[j] for j in sidx]
                    resid = [r[j] - beta * m[j] for j in sidx]
                    fitted_g = _slope(resid, xs)
                    if fitted_g is not None:
                        gamma = fitted_g
                        use_sector = True
            if use_sector:
                hist = [r[j] - beta * m[j] - gamma * (s[j] - beta_s * m[j])
                        if s[j] is not None else r[j] - beta * m[j]
                        for j in idx]
            else:
                hist = [r[j] - beta * m[j] for j in idx]
            sd_specific = _sd(hist)

        market = beta * m[i]
        sector = gamma * (s[i] - beta_s * m[i]) if use_sector else 0.0
        specific = r[i] - market - sector

        threshold = min_move_pct / 100.0
        if sd_specific is not None:
            threshold = max(threshold, sigma * sd_specific)
        flagged = abs(specific) >= threshold or abs(r[i]) >= 2 * min_move_pct / 100.0
        market_driven = flagged and abs(specific) < 0.5 * abs(r[i])

        bars.append(Bar(dates[i], float(closes[i]), vol, _pct(r[i]), _pct(market),
                        _pct(sector), _pct(specific), beta if estimated else None,
                        flagged, market_driven))
    return bars


def _pct(value: Optional[float]) -> Optional[float]:
    return None if value is None else value * 100.0
