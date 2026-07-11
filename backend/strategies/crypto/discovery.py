"""Auto-coin-discovery: build and rank a tradable crypto universe.

Everything here is PURE and point-in-time. External data arrives through
injected callables (``assets_provider`` / ``bars_provider``) so the module runs
with no network and no ``alpaca`` import in tests. :func:`rank_universe`
consumes ONLY the bars it is handed — no lookahead — so replaying with an
earlier ``as_of`` (fewer/older bars) can legitimately change the ranking.
"""

from __future__ import annotations

import math
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# Alpaca crypto pair: base of 2-10 alnum, quote of 3-4 letters (USD/USDC/USDT).
_PAIR_RE = re.compile(r"^[A-Z0-9]{2,10}/[A-Z]{3,4}$")

# Per-band weight applied to the volatility z-score in the composite. Fast bands
# WANT liquid + volatile names; the allocator wants broad + steady, so it
# penalises volatility.
_BAND_VOL_WEIGHT: Dict[str, float] = {
    "high": 0.5,
    "fast": 0.5,
    "medium": 0.0,
    "momentum": 0.0,
    "low": -0.5,
    "allocator": -0.5,
}


def is_valid_crypto_pair(sym) -> bool:
    """True iff ``sym`` matches ``^[A-Z0-9]{2,10}/[A-Z]{3,4}$`` (e.g. ``BTC/USD``)."""
    return bool(_PAIR_RE.match(str(sym or "").strip().upper()))


def is_usd_pair(sym) -> bool:
    """True iff ``sym`` is a valid pair quoted in USD (e.g. ``BTC/USD``)."""
    s = str(sym or "").strip().upper()
    if not is_valid_crypto_pair(s):
        return False
    return s.split("/", 1)[1] == "USD"


def list_tradable_pairs(assets_provider: Callable[[], Sequence[dict]]) -> List[str]:
    """Filter an Alpaca-style asset list to tradable, active, USD-quoted pairs.

    ``assets_provider()`` returns a list of asset dicts (fields like ``symbol``,
    ``tradable``, ``status``). Injected for testing — no network. Returns a
    sorted, de-duplicated list of pair strings.
    """
    assets = assets_provider() or []
    out: set = set()
    for a in assets:
        try:
            sym = str(a.get("symbol") or "").strip().upper()
        except AttributeError:
            continue
        if not is_usd_pair(sym):
            continue
        if not a.get("tradable", True):
            continue
        status = str(a.get("status") or "active").strip().lower()
        if status and status != "active":
            continue
        out.add(sym)
    return sorted(out)


def _zscores(values: Sequence[float]) -> List[float]:
    """Standard scores; a zero-variance series maps to all-zeros (neutral)."""
    n = len(values)
    if n == 0:
        return []
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    std = math.sqrt(var)
    if std == 0:
        return [0.0] * n
    return [(v - mean) / std for v in values]


def _bar_metrics(bars: Sequence[dict]) -> Tuple[float, float, float]:
    """(dollar_volume, momentum_return, volatility) for one symbol's bars.

    - dollar_volume: mean of ``close * volume`` across the window (liquidity).
    - momentum:     total return over the window (``last/first - 1``).
    - volatility:   std of per-bar simple returns.
    All point-in-time — computed only from the bars given.
    """
    closes = [float(b.get("c") or 0) for b in (bars or []) if b.get("c") is not None]
    vols = [float(b.get("v") or 0) for b in (bars or []) if b.get("c") is not None]
    if len(closes) < 2:
        return 0.0, 0.0, 0.0
    dollar_vol = sum(c * v for c, v in zip(closes, vols)) / len(closes)
    first = closes[0]
    momentum = (closes[-1] / first - 1.0) if first > 0 else 0.0
    rets = [
        (closes[i] / closes[i - 1] - 1.0)
        for i in range(1, len(closes))
        if closes[i - 1] > 0
    ]
    if rets:
        m = sum(rets) / len(rets)
        vol = math.sqrt(sum((r - m) ** 2 for r in rets) / len(rets))
    else:
        vol = 0.0
    return dollar_vol, momentum, vol


def rank_universe(bars_by_symbol: Dict[str, List[dict]], band: str) -> List[Tuple[str, float]]:
    """Rank symbols by a composite of liquidity, momentum, and band-weighted vol.

    ``composite = z(dollar_volume) + z(momentum) + w_band * z(volatility)`` where
    ``w_band`` is +0.5 for fast/high (favour liquid + volatile), -0.5 for
    allocator/low (favour broad + steady), 0 otherwise. Pure and deterministic:
    ties break by symbol name so the order is stable. Returns
    ``[(symbol, score), ...]`` sorted by score descending.
    """
    symbols = sorted(bars_by_symbol.keys())
    if not symbols:
        return []
    dvs, moms, vols = [], [], []
    for sym in symbols:
        dv, mom, vol = _bar_metrics(bars_by_symbol.get(sym) or [])
        dvs.append(dv)
        moms.append(mom)
        vols.append(vol)
    z_dv = _zscores(dvs)
    z_mom = _zscores(moms)
    z_vol = _zscores(vols)
    w = _BAND_VOL_WEIGHT.get(str(band or "").strip().lower(), 0.0)
    scored = [
        (sym, z_dv[i] + z_mom[i] + w * z_vol[i])
        for i, sym in enumerate(symbols)
    ]
    # Descending by score; ties broken deterministically by symbol.
    scored.sort(key=lambda t: (-t[1], t[0]))
    return scored


def discover(
    band: str,
    k: int,
    assets_provider: Callable[[], Sequence[dict]],
    bars_provider: Callable[[Sequence[str], Optional[object]], Dict[str, List[dict]]],
    as_of: Optional[object] = None,
) -> List[str]:
    """Return the top-``k`` symbols for ``band`` as of ``as_of``.

    Deterministic given its inputs. ``bars_provider(symbols, as_of)`` returns
    point-in-time bars per symbol (only data up to ``as_of``), so an earlier
    ``as_of`` can change the ranking — proving there is no lookahead.
    """
    pairs = list_tradable_pairs(assets_provider)
    if not pairs:
        return []
    bars_by_symbol = bars_provider(pairs, as_of) or {}
    ranked = rank_universe(bars_by_symbol, band)
    return [sym for sym, _ in ranked[: max(0, int(k))]]
