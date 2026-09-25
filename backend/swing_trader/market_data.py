"""Alpaca market data, ported from ST.

    market_data.py (whole file)  get_daily_bars, get_latest_trade,
                                 get_latest_price, _bars_to_frame,
                                 _to_alpaca_symbol (verbatim bodies)
    app.py:673-706               _fetch_live_price -> fetch_live_price

Only I/O changed: ST built a module singleton from ALPACA_API_KEY/SECRET in
the environment; here each call takes a `client` built by data_client() from
the credentials the engine injects into every run_once strategy's config
(broker.py run_run_once_strategies: config["alpaca_key"], config["alpaca_secret"]).
ST's prints go to the strategy log.

frames_from_engine_bars turns the BACKTEST engine's bars into the same
per-symbol frames, keeping only sessions strictly before the NY date inside
ST's live window, so EWM warm-up matches what live computes.
"""
from __future__ import annotations

import hashlib
import math
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import yfinance as yf
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame

from swing_trader import clock
from swing_trader.constants import WARMUP_DAYS

try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="SwingMarketData")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[SwingMarketData] {msg}")

BATCH_SIZE    = 100   # symbols per bars request (rate-limit friendly)
RETRY_ATTEMPTS = 2    # attempts per batch
RETRY_WAIT     = 2    # seconds between attempts
#: app.py:692-697 — an IEX print older than this during market hours is stale.
STALE_TRADE_SECONDS = 600
#: paper_trader.py:216 — WARMUP_DAYS trading days scaled to calendar days.
LIVE_WINDOW_DAYS = int(WARMUP_DAYS * 1.5)

_clients: dict = {}


def data_client(api_key, secret) -> StockHistoricalDataClient:
    """One client per credential pair (ST: the _get_data_client singleton)."""
    if not api_key or not secret:
        raise RuntimeError("Alpaca data credentials missing: the broker injects "
                           "alpaca_key/alpaca_secret into every run_once config")
    fp = hashlib.sha256(f"{api_key}:{secret}".encode()).hexdigest()
    client = _clients.get(fp)
    if client is None:
        client = StockHistoricalDataClient(api_key, secret)
        _clients[fp] = client
    return client


def _bars_to_frame(bars: list) -> pd.DataFrame:
    """Convert a list of alpaca Bar objects to an Open/High/Low/Close/Volume
    DataFrame indexed by naive trading date.

    Alpaca stamps daily bars at 04:00/05:00 UTC (midnight ET); converting to
    ET before dropping tz yields the actual trading date, so downstream
    indicator code sees the same DatetimeIndex shape yfinance produced.
    """
    rows = {
        "Open":   [b.open   for b in bars],
        "High":   [b.high   for b in bars],
        "Low":    [b.low    for b in bars],
        "Close":  [b.close  for b in bars],
        "Volume": [b.volume for b in bars],
    }
    idx = pd.DatetimeIndex([b.timestamp for b in bars])
    if idx.tz is not None:
        idx = idx.tz_convert("America/New_York").tz_localize(None)
    df = pd.DataFrame(rows, index=idx.normalize())
    df.index.name = "Date"
    return df.sort_index()


def _to_alpaca_symbol(symbol: str) -> str:
    """yfinance-style class shares use a dash (BRK-B); Alpaca uses a dot
    (BRK.B). A dash symbol in a bars request 400s the ENTIRE batch — found
    2026-07-07 when batch 1 of the S&P universe (containing BRK-B and BF-B)
    returned 100 nulls on the live RSI watchlist."""
    return symbol.replace("-", ".")


def get_daily_bars(symbols: list, days: int, *, client) -> dict:
    """Fetch `days` calendar days of daily bars for `symbols`.

    Returns {symbol: DataFrame} with columns Open/High/Low/Close/Volume and a
    naive date DatetimeIndex, keyed by the CALLER'S symbol spelling (dash
    class-share symbols are translated to Alpaca's dot form for the request
    and mapped back here). Symbols with no data are simply absent from the
    dict (callers already tolerate missing symbols). A batch that fails after
    all retries logs a warning and is skipped — remaining batches still run.
    """
    if not symbols:
        return {}

    to_caller = {_to_alpaca_symbol(s): s for s in symbols}
    start = datetime.now(timezone.utc) - timedelta(days=days)
    alpaca_symbols = [_to_alpaca_symbol(s) for s in symbols]
    batches = [alpaca_symbols[i:i + BATCH_SIZE] for i in range(0, len(alpaca_symbols), BATCH_SIZE)]
    result: dict = {}

    for batch_num, batch in enumerate(batches, 1):
        req = StockBarsRequest(
            symbol_or_symbols=batch,
            timeframe=TimeFrame.Day,
            start=start,
            adjustment=Adjustment.ALL,
            feed=DataFeed.SIP,
        )
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                barset = client.get_stock_bars(req)
                data = getattr(barset, "data", None) or {}
                for sym, bars in data.items():
                    if bars:
                        result[to_caller.get(sym, sym)] = _bars_to_frame(bars)
                _log(f"  [market_data] Batch {batch_num}/{len(batches)} OK "
                     f"({len(data)}/{len(batch)} symbols)")
                break
            except Exception as exc:
                if attempt < RETRY_ATTEMPTS:
                    _log(f"  [market_data] Batch {batch_num}/{len(batches)} attempt "
                         f"{attempt} failed — {exc}; retrying in {RETRY_WAIT}s")
                    time.sleep(RETRY_WAIT)
                else:
                    _log(f"  [market_data] WARNING: batch {batch_num}/{len(batches)} "
                         f"failed after {RETRY_ATTEMPTS} attempts — {exc}")

    return result


def get_latest_trade(symbol: str, *, client):
    """Latest trade via the IEX feed. Returns (price, timestamp) or (None, None).

    timestamp is the trade's tz-aware datetime — callers that care about
    staleness (approve-time pricing) can inspect it.
    """
    try:
        alpaca_sym = _to_alpaca_symbol(symbol)
        req = StockLatestTradeRequest(symbol_or_symbols=alpaca_sym, feed=DataFeed.IEX)
        trades = client.get_stock_latest_trade(req)
        trade = trades[alpaca_sym]
        price = float(trade.price)
        if price <= 0:
            return None, None
        return price, trade.timestamp
    except Exception as exc:
        _log(f"  [market_data] latest trade failed for {symbol} — {exc}")
        return None, None


def get_latest_price(symbol: str, *, client):
    """Latest trade price via IEX; None on any failure."""
    price, _ = get_latest_trade(symbol, client=client)
    return price


def _is_stale(ts, now) -> bool:
    """app.py:688-697: stale only during market hours and older than 10 min.

    fix (G2-I1): a naive `now` is read as UTC (clock.as_utc) instead of making
    the age subtraction raise into a swallowed "fresh", and a missing or
    unparseable trade time is stale at any hour — its age cannot be judged."""
    try:
        now_utc = clock.as_utc(now) or datetime.now(timezone.utc)  # fix (G2-I1)
        ts_utc = clock.as_utc(ts)  # fix (G2-I1)
        if ts_utc is None:  # fix (G2-I1): no readable trade time is never fresh
            return True
        now_et = now_utc.astimezone(clock.NY)
        market_open = (
            now_et.weekday() < 5
            and (now_et.hour, now_et.minute) >= (9, 30)
            and now_et.hour < 16
        )
        age = (now_utc - ts_utc).total_seconds()
        return market_open and age > STALE_TRADE_SECONDS
    except Exception:
        return True  # fix (G2-I1): an error judging the age is not "fresh"


def yf_last_price(symbol: str):
    try:
        return float(yf.Ticker(symbol).fast_info["last_price"])
    except Exception as exc:
        _log(f"  [live-price] {symbol}: yfinance fallback failed — {exc}")
        return None


def fetch_live_price(symbol: str, *, client, now=None):
    """Live price: Alpaca IEX latest trade → yfinance fast_info → None (R8-06).

    IEX prints can be thin on less-liquid names, and approve-time price feeds
    bracket math — so a stale print is as bad as no print. If the IEX trade
    timestamp is older than 10 minutes during market hours, treat it as a
    failure and fall through to yfinance.
    """
    price, ts = get_latest_trade(symbol, client=client)
    if price is not None:
        if not _is_stale(ts, now):  # fix (G2-I1): a print with no time is stale
            return price
        _log(f"  [live-price] {symbol}: IEX trade is stale — falling back to yfinance")
    return yf_last_price(symbol)


def fresh_trade_price(price, ts, *, now=None):
    """A (price, timestamp) pair from the broker adapter, or None when it is
    missing, non-positive or stale by the rule above (a missing or
    unparseable timestamp is stale — fix (G2-I1))."""
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(p) or p <= 0:
        return None
    if _is_stale(ts, now):  # fix (G2-I1): was skipped when ts was None
        return None
    return p


def live_prices(symbols, *, adapter=None, client=None, now=None) -> dict:
    """Latest prices for `symbols`: the adapter's latest trades when it has
    them (plan A-live get_latest_trades), else ST's IEX path, else yfinance."""
    wanted = [str(s) for s in (symbols or []) if str(s).strip()]
    trades = {}
    if adapter is not None and wanted:
        try:
            trades = adapter.get_latest_trades(wanted) or {}
        except Exception:
            trades = {}
    out = {}
    for sym in wanted:
        price, ts = (trades.get(sym) or (None, None))
        p = fresh_trade_price(price, ts, now=now) if price is not None else None
        if p is None:
            p = (fetch_live_price(sym, client=client, now=now) if client is not None
                 else yf_last_price(sym))
        if p is not None:
            out[sym] = p
    return out


def _bars_for(data, symbol):
    """The engine hands bars as {sym: {"bars": [...]}} or {sym: [...]}."""
    if not isinstance(data, dict):
        return []
    entry = data.get(symbol)
    if isinstance(entry, dict):
        return entry.get("bars") or []
    return entry or []


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def frames_from_engine_bars(data, symbols, as_of, *,
                            lookback_days: int = LIVE_WINDOW_DAYS) -> dict:
    """BACKTEST bars -> ST's per-symbol daily frames, point-in-time.

    Keeps only sessions STRICTLY BEFORE the NY date of `as_of` (a daily bar
    stamped with the session carries its 16:00 close) and within the same
    calendar window ST's live fetch uses. Daily bars keep their date label
    (Alpaca stamps 1Day bars 04:00/05:00Z); intraday bars roll up by NY
    session (first open, max high, min low, last close, summed volume). A
    NaN close stays NaN: get_latest_indicators' guard owns that case.
    """
    cutoff = clock.ny_date(as_of)
    floor = (date.fromisoformat(cutoff) - timedelta(days=int(lookback_days))).isoformat()
    out = {}
    for symbol in symbols or []:
        stamped = []
        for bar in _bars_for(data, symbol):
            if not isinstance(bar, dict):
                continue
            ts = clock.as_utc(bar.get("t") or bar.get("timestamp") or bar.get("date"))
            if ts is not None:
                stamped.append((ts, bar))
        if not stamped:
            continue
        stamped.sort(key=lambda item: item[0])
        gaps = [(b[0] - a[0]).total_seconds() for a, b in zip(stamped, stamped[1:])
                if (b[0] - a[0]).total_seconds() > 0]
        daily = not gaps or min(gaps) >= 23 * 3600
        rows = []
        for ts, bar in stamped:
            day = (ts.date() if daily else ts.astimezone(clock.NY).date()).isoformat()
            if not (floor <= day < cutoff):
                continue
            rows.append((day, _num(bar.get("o", bar.get("open"))),
                         _num(bar.get("h", bar.get("high"))),
                         _num(bar.get("l", bar.get("low"))),
                         _num(bar.get("c", bar.get("close"))),
                         _num(bar.get("v", bar.get("volume")))))
        if not rows:
            continue
        frame = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        if daily:
            frame = frame.drop_duplicates("Date", keep="last")
        else:
            frame = frame.groupby("Date", sort=True).agg(
                {"Open": "first", "High": "max", "Low": "min",
                 "Close": "last", "Volume": "sum"}).reset_index()
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("Date")))
        frame.index.name = "Date"
        out[symbol] = frame.sort_index()
    return out
