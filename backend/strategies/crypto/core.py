"""Shared crypto core: fees, 24/7 scheduling, bars fetch, sizing, orders.

Pure and dependency-light on purpose. External dependencies are limited to
``requests`` (for :func:`fetch_crypto_bars` / :func:`discover_universe`) and
``numpy`` (for the shared :func:`series` helper); every network call is
injectable via an ``http_get`` argument so unit tests never touch the network.
Nothing in here imports ``alpaca`` — a crypto instance runs through the same
broker/adapter as equities, and this module only supplies the crypto-specific
arithmetic and URL/param/order shapes plus the small helpers the strategies
share (:func:`series`, :func:`held_symbols`, :func:`position_qty`).

Symbols are Alpaca crypto slash-pairs throughout (``"BTC/USD"``). ``.upper()``
preserves the slash, so pairs stay well-formed after normalisation.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Mapping, Optional, Sequence

import numpy as np
import requests

# Alpaca trading-API bases (used only to list the tradable crypto universe for
# auto-discovery). The data feed lives at CRYPTO_BARS_URL below.
ALPACA_TRADING_LIVE: str = "https://api.alpaca.markets"
ALPACA_TRADING_PAPER: str = "https://paper-api.alpaca.markets"

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

    NOTE: ``crypto_config.band`` is the SINGLE SOURCE OF TRUTH for monitor
    cadence — it maps to ``monitor_interval_min`` via ``_BAND_MONITOR_MIN`` here.
    It should match the chosen strategy's intended trading frequency so the
    instance is not polled faster or slower than the strategy expects:
    Fast → "high" (poll often), Momentum → "medium", Allocator → "low" (slow).
    Keep the configured band aligned with the strategy you run.
    """
    interval = _BAND_MONITOR_MIN.get(str(band or "").strip().lower(), _BAND_MONITOR_MIN["medium"])
    return {
        "open_pt_min": 0,
        "close_pt_min": 1440,
        "weekdays_only": False,
        "full_anchor_pt_min": 0,
        "monitor_interval_min": interval,
    }


# NOTE: crypto_bars_url / crypto_bars_params / fetch_crypto_bars / build_crypto_order
# are the CANONICAL crypto bars + order helpers. The broker/adapter wires orders
# and bar fetches through these — do not delete or inline them.
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


# ---------------------------------------------------------------------------
# Shared strategy helpers. Single source of truth for the bar->array and
# holdings-lookup logic every crypto strategy needs (previously copy-pasted
# across allocator/fast/momentum).
# ---------------------------------------------------------------------------
def series(bars, key: str) -> np.ndarray:
    """A float ``np.ndarray`` of one OHLCV field across ``bars`` (missing -> 0)."""
    return np.array([float(b.get(key) or 0) for b in (bars or [])], dtype=float)


def position_qty(positions: Optional[Mapping], sym: str) -> float:
    """Shares held for ``sym``, tolerant of Alpaca's slash / slash-less crypto keys.

    Alpaca may key crypto positions either WITH the slash (``"BTC/USD"``) or
    WITHOUT it (``"BTCUSD"``); we look up both so held crypto is detected
    regardless of formatting. Non-numeric / missing entries read as ``0.0``.
    """
    positions = positions or {}
    raw = positions.get(sym)
    if raw is None:
        raw = positions.get(str(sym).replace("/", ""))
    if raw is None:
        # Last resort: match slash-stripped + uppercased on BOTH sides, so a
        # slash-less query ("BTCUSD") still finds a slash position ("BTC/USD").
        target = str(sym).replace("/", "").upper()
        for k, v in positions.items():
            if str(k).replace("/", "").upper() == target:
                raw = v
                break
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def held_symbols(portfolio_emulator, universe) -> set:
    """Set of ``universe`` symbols currently held (qty > 0), slash-format-agnostic."""
    held: set = set()
    if portfolio_emulator is None:
        return held
    try:
        positions = portfolio_emulator.get_positions() or {}
    except Exception:
        return held
    # TEMP PROBE (remove after crypto no-sells verification, 2026-07-12): if we
    # hold something but resolve no held symbols in `universe`, log why so the
    # verification backtest reveals the exact cause of the empty-held paradox.
    try:
        if positions:
            _resolved = {s for s in universe if position_qty(positions, s) > 0}
            if not _resolved:
                print(
                    f"[HELD-PROBE] positions={list(positions.keys())} "
                    f"universe={list(universe)} resolved_held=EMPTY"
                )
    except Exception:
        pass
    for sym in universe:
        if position_qty(positions, sym) > 0:
            held.add(sym)
    return held


def held_positions(portfolio_emulator) -> set:
    """All held symbols (qty > 0) straight from the emulator, NOT filtered by
    ``universe``. Used so a held coin with no bars this tick can still be exited."""
    if portfolio_emulator is None:
        return set()
    try:
        positions = portfolio_emulator.get_positions() or {}
    except Exception:
        return set()
    return {str(k) for k in positions if position_qty(positions, k) > 0}


def exit_blind_held(result: dict, portfolio_emulator, data, evaluated) -> list:
    """Emit a sell (-1) for every held coin that the strategy did NOT evaluate
    this tick (absent from ``evaluated`` and with no bars in ``data``).

    Entry signals ignore holdings, so a coin we hold but can't see this tick
    (empty/degenerate window, or dropped out of the discovery universe) would
    otherwise be silently kept forever — the crypto no-sells bug. We can't rank
    a coin without data, so the safe action is a risk-off exit. Mutates
    ``result`` in place; returns the list of coins exited this way."""
    evaluated = set(evaluated or [])
    data = data or {}
    blind = []
    for s in held_positions(portfolio_emulator):
        if s not in evaluated and not data.get(s):
            result[s] = -1
            blind.append(s)
    return blind


# ---------------------------------------------------------------------------
# Auto-coin-discovery wiring. Builds live Alpaca providers from the creds the
# broker injects into a strategy's config and ranks a tradable universe via
# ``discovery``. Robust by construction: ANY failure (no creds, network error,
# empty result) returns ``[]`` so the caller falls back to its hardcoded majors
# and the tick never crashes.
# ---------------------------------------------------------------------------
def discover_universe(
    band: str,
    k: int,
    config: Optional[Mapping],
    timeframe: str,
    as_of: Optional[object] = None,
    http_get: Optional[Callable] = None,
) -> List[str]:
    """Return up to ``k`` auto-discovered crypto pairs for ``band``, or ``[]``.

    Creds come from ``config`` (``alpaca_key`` / ``alpaca_secret`` /
    ``alpaca_paper``). The assets provider lists active tradable crypto assets
    from the Alpaca trading API; the bars provider fetches point-in-time bars
    via :func:`fetch_crypto_bars`. Never raises — returns ``[]`` on any error so
    the strategy can fall back to its default majors.
    """
    try:
        from strategies.crypto import discovery  # lazy: keep core import-light

        cfg = config or {}
        key = cfg.get("alpaca_key")
        secret = cfg.get("alpaca_secret")
        if not key or not secret:
            return []
        base = ALPACA_TRADING_PAPER if cfg.get("alpaca_paper") else ALPACA_TRADING_LIVE
        hg = http_get or requests.get

        def assets_provider() -> Sequence[dict]:
            resp = hg(
                base + "/v2/assets",
                params={"asset_class": "crypto", "status": "active"},
                headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            )
            raw = resp.json()
            return raw if isinstance(raw, list) else []

        def bars_provider(symbols: Sequence[str], _as_of) -> Dict[str, List[dict]]:
            return fetch_crypto_bars(list(symbols), timeframe, key, secret, http_get=hg)

        pairs = discovery.discover(band, int(k), assets_provider, bars_provider, as_of=as_of)
        return list(pairs or [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Fixed + dynamic allocation overlay. Turns per-coin allocations
# (crypto_config.allocations) into rebalance signals + the CORRECT dict-valued
# _nexus_position_sizes the broker expects. NEVER emits bare floats.
# ---------------------------------------------------------------------------
def overlay_allocations(out, allocations, portfolio_value, prices, positions,
                        tol_frac: float = 0.03):
    """Overlay fixed per-coin allocations onto a strategy's ``run_once`` output.

    ``allocations`` items are ``{"symbol": "BTC/USD", "pct": 0.10}`` (fraction of
    portfolio value) and/or ``{"symbol": "SOL/USD", "usd": 5000}`` (absolute USD).
    Fixed coins are rebalanced toward target (buy the shortfall / sell the excess
    outside a ``tol_frac`` band); the remaining budget ``(1 - sum(fixed))`` is
    split across the strategy's OWN dynamic buys. Sets top-level control keys so
    the broker deploys 100% and applies no price floor. Empty allocations ⇒ ``out``
    unchanged (100% dynamic). Mutates and returns ``out``.
    """
    allocs = [a for a in (allocations or []) if isinstance(a, Mapping) and a.get("symbol")]
    pv = float(portfolio_value or 0.0)
    if not allocs or pv <= 0:
        return out

    prices = prices or {}
    positions = positions or {}
    sizes = out.get("_nexus_position_sizes")
    if not isinstance(sizes, dict):
        sizes = {}
    discovered = list(out.get("_nexus_discovered") or [])

    fixed_syms: set = set()
    fixed_frac_total = 0.0
    for a in allocs:
        sym = str(a.get("symbol") or "").strip().upper()
        if not sym:
            continue
        if a.get("pct") is not None:
            target = max(0.0, float(a["pct"])) * pv
        elif a.get("usd") is not None:
            target = max(0.0, float(a["usd"]))
        else:
            continue
        fixed_syms.add(sym)
        fixed_frac_total += (target / pv) if pv > 0 else 0.0
        px = float(prices.get(sym) or 0.0)
        current = position_qty(positions, sym) * px if px > 0 else 0.0
        tol = target * tol_frac
        if px <= 0:
            out[sym] = 1  # no price to value against — signal a buy toward target
            sizes[sym] = {"buy_cash": round(target, 2), "asset_class": "crypto", "high_conviction": True}
        elif current < target - tol:
            out[sym] = 1
            sizes[sym] = {"buy_cash": round(target - current, 2), "asset_class": "crypto", "high_conviction": True}
        elif current > target + tol:
            out[sym] = -1
            sizes[sym] = {"sell_fraction": round(min(1.0, (current - target) / current), 4), "asset_class": "crypto"}
        else:
            out[sym] = 0  # within tolerance band — hold
        if sym not in discovered:
            discovered.append(sym)

    # Dynamic remainder split across the strategy's own buys (signal 1, not fixed).
    dyn_budget = max(0.0, 1.0 - min(1.0, fixed_frac_total)) * pv
    dyn_buys = [s for s, v in list(out.items())
                if isinstance(s, str) and not s.startswith("_") and s not in fixed_syms and v == 1]
    if dyn_buys and dyn_budget > 0:
        per = round(dyn_budget / len(dyn_buys), 2)
        for s in dyn_buys:
            sizes[s] = {"buy_cash": per, "asset_class": "crypto"}

    sizes["_cash_reserve_floor_pct"] = 0.0  # deploy 100% (default holds back 10%)
    sizes["_buy_price_floor"] = 0.0         # crypto has no $5 floor
    out["_nexus_position_sizes"] = sizes
    out["_nexus_discovered"] = discovered
    return out


def apply_crypto_config(out, config, prices, portfolio_emulator):
    """Convenience overlay: pull ``allocations`` from the injected ``crypto_config``
    and apply :func:`overlay_allocations`. No-op when there are no allocations
    (100% dynamic). Call at the END of a non-IDLE ``run_once``. Never raises."""
    try:
        cc = (config or {}).get("crypto_config") or {}
        allocs = cc.get("allocations")
        if not allocs:
            return out
        pv, positions = 0.0, {}
        if portfolio_emulator is not None:
            try:
                pv = float(portfolio_emulator.get_portfolio_value(prices) or 0.0)
            except Exception:
                pv = 0.0
            try:
                positions = portfolio_emulator.get_positions() or {}
            except Exception:
                positions = {}
        return overlay_allocations(out, allocs, pv, prices, positions)
    except Exception:
        return out
