"""Shared crypto core: fees, 24/7 scheduling, bars fetch, sizing, orders.

Pure and dependency-light on purpose. The ONLY external dependency is
``requests`` (for :func:`fetch_crypto_bars`), and even that call is injectable
via the ``http_get`` argument so unit tests never touch the network. Nothing in
here imports ``alpaca`` — a crypto instance runs through the same broker/adapter
as equities, and this module only supplies the crypto-specific arithmetic and
URL/param/order shapes.

Symbols are Alpaca crypto slash-pairs throughout (``"BTC/USD"``). ``.upper()``
preserves the slash, so pairs stay well-formed after normalisation.
"""

from __future__ import annotations

from typing import Callable, List, Mapping, Optional

import requests

# ---------------------------------------------------------------------------
# Fee model (Alpaca crypto, tier 1). Crypto is NEVER commission-free: apply
# these in sizing AND in backtest fills.
# ---------------------------------------------------------------------------
CRYPTO_FEES: dict = {"maker": 0.0015, "taker": 0.0025}

# Crypto data REST base (v1beta3, unified US crypto feed — no per-symbol path,
# no ``feed`` query param).
CRYPTO_BARS_URL: str = "https://data.alpaca.markets/v1beta3/crypto/us/bars"

# Resting-limit offset for maker orders: sit ~10 bps inside the touch so the
# order rests (adds liquidity) rather than crossing the spread.
MAKER_OFFSET: float = 0.001

# Extra spread cushion added on top of the round-trip fee before a tactical
# strategy is willing to take a trade.
SPREAD_BUFFER: float = 0.001

# Band -> monitor cadence (minutes). Faster bands poll more often.
_BAND_MONITOR_MIN: dict = {"high": 5, "medium": 15, "low": 60}


def is_crypto_instance(instance_doc: Mapping) -> bool:
    """True iff the Instances row is a crypto instance (``kind == "crypto"``)."""
    try:
        return instance_doc.get("kind") == "crypto"
    except AttributeError:
        return False


def round_trip_fee(maker_in: bool, maker_out: bool) -> float:
    """Total fee fraction for a full round trip (entry + exit).

    Each leg pays the maker fee if it rests (``maker_*`` True) or the taker fee
    if it crosses. ``round_trip_fee(False, False) == 0.005`` (taker both ways);
    ``round_trip_fee(True, True) == 0.003`` (maker both ways).
    """
    fee_in = CRYPTO_FEES["maker"] if maker_in else CRYPTO_FEES["taker"]
    fee_out = CRYPTO_FEES["maker"] if maker_out else CRYPTO_FEES["taker"]
    return fee_in + fee_out


def crypto_scheduler_config(band: str) -> dict:
    """Return a ``scheduler.get_next_wake`` config that runs the instance 24/7.

    The session spans the full day (``open_pt_min=0``..``close_pt_min=1440``),
    weekends included (``weekdays_only=False``), so the scheduler always
    schedules a next wake — crypto never closes. Monitor cadence is paced by
    band; an unknown band falls back to ``medium``.
    """
    interval = _BAND_MONITOR_MIN.get(str(band or "").strip().lower(), _BAND_MONITOR_MIN["medium"])
    return {
        "open_pt_min": 0,
        "close_pt_min": 1440,
        "weekdays_only": False,
        "full_anchor_pt_min": 0,
        "monitor_interval_min": interval,
    }


def crypto_bars_url(timeframe: Optional[str] = None) -> str:
    """The v1beta3 unified crypto bars endpoint (no ``/stocks/`` path).

    ``timeframe`` is accepted for call-site symmetry but is a *query* param
    (see :func:`crypto_bars_params`), not part of the path, so it is ignored
    here.
    """
    return CRYPTO_BARS_URL


def crypto_bars_params(
    symbols: List[str],
    timeframe: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 1000,
) -> dict:
    """Build the query params for the crypto bars endpoint.

    Symbols are comma-joined into ONE ``symbols`` param (the v1beta3 endpoint is
    multi-symbol). There is deliberately NO ``feed`` key — the US crypto feed is
    implied by the path.
    """
    params: dict = {
        "symbols": ",".join(str(s).strip().upper() for s in symbols),
        "timeframe": timeframe,
        "limit": int(limit),
    }
    if start is not None:
        params["start"] = start
    if end is not None:
        params["end"] = end
    return params


def fetch_crypto_bars(
    symbols: List[str],
    timeframe: str,
    key: str,
    secret: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 1000,
    http_get: Callable = requests.get,
) -> dict:
    """Fetch crypto bars, returning ``{symbol: [bar, ...]}``.

    ``http_get`` is injectable (defaults to ``requests.get``) so tests can pass
    a canned responder and never hit the network. The response envelope is
    ``{"bars": {symbol: [...]}}``; we return the inner ``bars`` mapping.
    """
    url = crypto_bars_url(timeframe)
    params = crypto_bars_params(symbols, timeframe, start=start, end=end, limit=limit)
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    resp = http_get(url, params=params, headers=headers)
    raw = resp.json()
    return raw.get("bars", {}) if isinstance(raw, dict) else {}


def build_crypto_order(
    symbol: str,
    side: str,
    qty: float,
    prefer_maker: bool = True,
    last_price: Optional[float] = None,
) -> dict:
    """Build a fee-aware crypto order payload.

    Alpaca crypto order rules are baked in: ``tif`` is ALWAYS ``"gtc"`` and
    ``extended_hours`` is ALWAYS ``False`` (``day``/``opg`` and extended hours
    are rejected for crypto).

    - Maker (``prefer_maker`` and a usable ``last_price``): a resting *limit*
      just inside the book — buy slightly below last, sell slightly above — so
      the order adds liquidity and pays the maker fee.
    - Taker (or no price to anchor a limit): a marketable ``market`` order.
    """
    side_norm = str(side or "").strip().lower()
    is_buy = side_norm in ("buy", "b", "1", "long")

    order: dict = {
        "order_type": "market",
        "limit_price": None,
        "tif": "gtc",
        "extended_hours": False,
        "symbol": str(symbol).strip().upper(),
        "side": "buy" if is_buy else "sell",
        "qty": float(qty),
    }

    if prefer_maker and last_price is not None and float(last_price) > 0:
        px = float(last_price)
        offset = px * MAKER_OFFSET
        order["order_type"] = "limit"
        order["limit_price"] = round(px - offset if is_buy else px + offset, 8)
    return order


def vol_target_size(
    equity_usd: float,
    price: float,
    recent_vol: float,
    target_vol: float = 0.02,
    max_frac: float = 0.25,
) -> float:
    """Volatility-targeted position size, in coin units (fractional qty).

    Scales exposure so a position's expected volatility approaches
    ``target_vol`` of equity: fraction = ``target_vol / recent_vol``, capped at
    ``max_frac``. Notional is therefore capped at ``max_frac * equity_usd``.
    Guards against div-by-zero / non-positive inputs (returns ``0.0``).
    """
    if price is None or float(price) <= 0 or equity_usd is None or float(equity_usd) <= 0:
        return 0.0
    if recent_vol is not None and float(recent_vol) > 0:
        frac = float(target_vol) / float(recent_vol)
    else:
        frac = float(max_frac)
    frac = max(0.0, min(frac, float(max_frac)))
    notional = frac * float(equity_usd)
    return notional / float(price)


def min_edge_to_trade(maker: bool) -> float:
    """Minimum expected move (fraction) needed before a trade clears costs.

    = round-trip fee (both legs at the given liquidity role) + a small spread
    buffer. Taker is always ``>= 0.005`` (the taker round-trip alone).
    """
    return round_trip_fee(maker, maker) + SPREAD_BUFFER


def risk_off_targets(symbols) -> dict:
    """All-zero target weights ⇒ hold USD/USDC (fully de-risked)."""
    return {str(s).strip().upper(): 0.0 for s in (symbols or [])}
