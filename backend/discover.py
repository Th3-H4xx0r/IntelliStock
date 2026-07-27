try:
    # Imports: yfinance for market history; ticker list from Nasdaq API.
    import requests
    import yfinance as yf
    import pandas as pd
    import datetime
    import time
    import os
    from os import system

    from dotenv import load_dotenv
    from rethinkdb import RethinkDB
    from intellistock_logger import intellistock_logger
    from tqdm import tqdm
    from stock_metrics import add_all_metrics, StockMetrics
    from fundamentals_util import get_fundamentals, earnings_within_days

    NASDAQ_SCREENER_HEADERS = {
        "authority": "api.nasdaq.com",
        "accept": "application/json, text/plain, */*",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.141 Safari/537.36",
        "origin": "https://www.nasdaq.com",
        "sec-fetch-site": "same-site",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        "referer": "https://www.nasdaq.com/",
        "accept-language": "en-US,en;q=0.9",
    }

    def _tickers_from_nasdaq_screener():
        """Fetch all stock symbols from Nasdaq screener API (single call returns full list). Returns list of ticker strings."""
        symbols = []
        try:
            r = requests.get(
                "https://api.nasdaq.com/api/screener/stocks",
                headers=NASDAQ_SCREENER_HEADERS,
                params={"tableonly": "true", "limit": 10000, "offset": 0, "download": "true"},
                timeout=60,
            )
            r.raise_for_status()
            data = r.json().get("data") or {}
            rows = data.get("rows") or []
            for row in rows:
                if isinstance(row, dict):
                    sym = (row.get("symbol") or row.get("ticker") or "").strip().upper()
                else:
                    sym = (row[0] if row else "").strip().upper() if isinstance(row, (list, tuple)) else ""
                if sym and sym not in ("N/A", ""):
                    symbols.append(sym)
        except Exception as e:
            intellistock_logger.log(f"Nasdaq screener error: {e}", "red", service="Discover")
        return symbols

    def _yahoo_fallback(symbol, start_date, end_date):
        """Fetch historical OHLC from Yahoo via yfinance."""
        df = yf.download(symbol, start=start_date, end=end_date, progress=False, auto_adjust=False)
        if df.empty:
            return df
        # yf.download can return MultiIndex columns in some versions; flatten to match expected columns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if "Adj Close" not in df.columns and "Close" in df.columns:
            df["Adj Close"] = df["Close"]
        return df

    CACHE_TABLE = 'DiscoverPriceCache'
    CACHE_MAX_DAYS = 400  # keep ~1.5 years for SMA200 etc.

    def _df_to_cache_rows(df):
        """Convert DataFrame (DatetimeIndex, OHLCV) to list of dicts for DB storage."""
        if df is None or df.empty:
            return []
        df = df.sort_index()
        rows = []
        for ts in df.index:
            d = ts.date() if hasattr(ts, 'date') else ts
            rows.append({
                "date": str(d),
                "Open": float(df.loc[ts, "Open"]) if "Open" in df.columns else 0,
                "High": float(df.loc[ts, "High"]) if "High" in df.columns else 0,
                "Low": float(df.loc[ts, "Low"]) if "Low" in df.columns else 0,
                "Close": float(df.loc[ts, "Close"]) if "Close" in df.columns else 0,
                "Adj Close": float(df.loc[ts, "Adj Close"]) if "Adj Close" in df.columns else float(df.loc[ts, "Close"]),
                "Volume": int(df.loc[ts, "Volume"]) if "Volume" in df.columns else 0,
            })
        return rows

    def _cache_rows_to_df(rows):
        """Convert cached rows from DB to DataFrame with DatetimeIndex."""
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["Date"] = pd.to_datetime(df["date"])
        df = df.set_index("Date").sort_index()
        return df[["Open", "High", "Low", "Close", "Adj Close", "Volume"]]

    def _get_cached_price_data(conn, ticker):
        """Load cached price data for ticker. Returns (DataFrame or None, lastDate str or None)."""
        try:
            doc = r.db(DB_NAME).table(CACHE_TABLE).get(ticker).run(conn)
            if not doc or not doc.get("rows"):
                return None, None
            last = doc.get("lastDate")
            df = _cache_rows_to_df(doc["rows"])
            return df, last
        except Exception:
            return None, None

    def _save_price_cache(conn, ticker, df):
        """Save merged price DataFrame to cache for ticker."""
        if df is None or df.empty:
            return
        df = df.sort_index().tail(CACHE_MAX_DAYS)
        last_date = df.index[-1]
        last_str = last_date.strftime("%Y-%m-%d") if hasattr(last_date, 'strftime') else str(last_date)[:10]
        rows = _df_to_cache_rows(df)
        r.db(DB_NAME).table(CACHE_TABLE).insert({
            "id": ticker,
            "ticker": ticker,
            "lastDate": last_str,
            "rows": rows,
            "updatedAt": datetime.datetime.now().isoformat(),
        }, conflict="replace").run(conn)

    def get_or_fetch_price_data(ticker, start_date, end_date, conn):
        """
        Return OHLCV DataFrame for ticker: use cache + incremental fetch.
        Loads cache; if cache has data, fetches only from (lastDate+1) to end_date via yfinance and merges.
        If no cache, fetches the full requested window and saves it to cache.
        """
        cached_df, last_date_str = _get_cached_price_data(conn, ticker)
        end_d = end_date.date() if hasattr(end_date, 'date') else end_date

        if cached_df is not None and not cached_df.empty and last_date_str:
            try:
                from datetime import timedelta
                last_dt = datetime.datetime.strptime(last_date_str, "%Y-%m-%d").date()
                if last_dt >= end_d:
                    add_all_metrics(cached_df)
                    return cached_df
                fetch_start = last_dt + timedelta(days=1)
                if fetch_start > end_date:
                    add_all_metrics(cached_df)
                    return cached_df
                new_df = _yahoo_fallback(ticker, fetch_start, end_d)
                if new_df.empty:
                    add_all_metrics(cached_df)
                    return cached_df
                combined = pd.concat([cached_df, new_df]).drop_duplicates().sort_index()
                combined = combined[~combined.index.duplicated(keep='last')]
                combined = combined.tail(CACHE_MAX_DAYS)
                _save_price_cache(conn, ticker, combined)
                add_all_metrics(combined)
                return combined
            except Exception:
                pass

        df = _yahoo_fallback(ticker, start_date, end_date)
        if not df.empty:
            _save_price_cache(conn, ticker, df)
        return df

    BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(BACKEND_DIR, '.env'))
    load_dotenv(os.path.join(os.path.dirname(BACKEND_DIR), '.env'))

    if os.name == 'nt':
        system("title " + "Discover Broker")

    intellistock_logger.log("=" * 60, "cyan", service="Discover")
    intellistock_logger.log("Discover process started", "green", service="Discover")
    intellistock_logger.log("=" * 60, "cyan", service="Discover")

    r = RethinkDB()
    DB_NAME = 'IntelliStock'
    RETHINKDB_HOST = os.environ.get('RETHINKDB_HOST', 'localhost')
    RETHINKDB_PORT = int(os.environ.get('RETHINKDB_PORT', '28015'))

    def _is_retryable_rethink_error(exc):
        msg = str(exc).lower()
        return "primary replica" in msg or "not available" in msg or "connection refused" in msg

    def _get_conn():
        return r.connect(host=RETHINKDB_HOST, port=RETHINKDB_PORT)

    def _run_rethink_with_retry(description, fn, max_attempts=30, delay=2):
        last_error = None
        current_delay = delay
        for attempt in range(1, max_attempts + 1):
            connection = None
            try:
                connection = _get_conn()
                return fn(connection)
            except Exception as e:
                last_error = e
                if attempt == max_attempts or not _is_retryable_rethink_error(e):
                    raise
                intellistock_logger.log(
                    f"{description}: RethinkDB primary not ready (attempt {attempt}/{max_attempts}), retrying in {current_delay}s...",
                    "yellow",
                    service="Discover",
                )
                time.sleep(current_delay)
                current_delay = min(current_delay * 1.5, 30)
            finally:
                if connection:
                    try:
                        connection.close()
                    except Exception:
                        pass
        raise last_error

    # Ensure database and DiscoverStocks table exist
    dbs = list(_run_rethink_with_retry("Checking databases", lambda connection: r.db_list().run(connection)))
    if DB_NAME not in dbs:
        _run_rethink_with_retry("Creating IntelliStock database", lambda connection: r.db_create(DB_NAME).run(connection))
    tables = list(_run_rethink_with_retry("Checking Discover tables", lambda connection: r.db(DB_NAME).table_list().run(connection)))
    if 'DiscoverStocks' not in tables:
        intellistock_logger.log("Creating table 'DiscoverStocks'", "yellow", service="Discover")
        _run_rethink_with_retry("Creating DiscoverStocks table", lambda connection: r.db(DB_NAME).table_create('DiscoverStocks').run(connection))
    if 'DiscoverPriceCache' not in tables:
        intellistock_logger.log("Creating table 'DiscoverPriceCache'", "yellow", service="Discover")
        _run_rethink_with_retry("Creating DiscoverPriceCache table", lambda connection: r.db(DB_NAME).table_create('DiscoverPriceCache').run(connection))

    _run_rethink_with_retry(
        "Waiting for Discover tables",
        lambda connection: list(r.db(DB_NAME).table('DiscoverStocks').limit(1).run(connection)),
    )
    connection = _get_conn()

    # Clear existing discover results for this run
    intellistock_logger.log("Clearing previous discover results", "yellow", service="Discover")
    deleted_count = _run_rethink_with_retry(
        "Clearing previous discover results",
        lambda retry_conn: r.db(DB_NAME).table('DiscoverStocks').delete().run(retry_conn).get('deleted', 0),
    )
    intellistock_logger.log(f"Deleted {deleted_count} previous results", "cyan", service="Discover")


    intellistock_logger.log("Fetching ticker list from Nasdaq screener API", "cyan", service="Discover")
    all_tickers = _tickers_from_nasdaq_screener()
    all_tickers = [t.replace(".", "-") for t in all_tickers]
    
    # Normalize tickers: remove preferred stock suffixes (^P, ^Q, ^A, etc.) and get base ticker
    def normalize_ticker(ticker):
        """Remove preferred stock suffixes (^P, ^Q, etc.) and return base ticker."""
        if "^" in ticker:
            return ticker.split("^")[0]
        return ticker
    
    normalized_tickers = [normalize_ticker(t) for t in all_tickers]
    # Remove duplicates while preserving order
    seen = set()
    unique_tickers = []
    for t in normalized_tickers:
        if t not in seen:
            seen.add(t)
            unique_tickers.append(t)
    
    intellistock_logger.log(f"Fetched {len(all_tickers)} tickers, normalized to {len(unique_tickers)} unique base tickers", "green", service="Discover")
    
    # Single universe from Nasdaq; compare each stock to S&P 500
    indexes = [unique_tickers]
    indexSymbols = ['^GSPC']
    index_names = ['S&P 500']
    
    total_tickers = len(unique_tickers)

    symbolIndex = 0
    total_stocks_found = 0
    total_short = 0
    total_long = 0

    # Variables

    for indexHERE in indexes:
        tickers = indexHERE
        tickers = [item.replace(".", "-") for item in tickers] # Yahoo Finance uses dashes instead of dots
        index_name = indexSymbols[symbolIndex]
        readable_index_name = index_names[symbolIndex]
        start_date = datetime.datetime.now() - datetime.timedelta(days=365)
        end_date = datetime.date.today()
        exportList = pd.DataFrame(columns=['Stock', "RS_Rating", "50 Day MA", "150 Day Ma", "200 Day MA", "52 Week Low", "52 week High"])
        returns_multiples = []

        if not tickers:
            intellistock_logger.log(f"Skipping {readable_index_name}: no tickers from Wikipedia", "yellow", service="Discover")
            symbolIndex += 1
            continue

        intellistock_logger.log("=" * 60, "cyan", service="Discover")
        intellistock_logger.log(f"Processing {readable_index_name} ({index_name})", "yellow", service="Discover")
        intellistock_logger.log(f"Total tickers to analyze: {len(tickers)}", "cyan", service="Discover")
        intellistock_logger.log("=" * 60, "cyan", service="Discover")

        intellistock_logger.log(
            f"Fetching {readable_index_name} historical data",
            "cyan",
            service="Discover",
        )
        index_df = _yahoo_fallback(index_name, start_date, end_date)
        
        if index_df.empty or len(index_df) < 2:
            intellistock_logger.log(f"Skipping {readable_index_name}: insufficient index data", "yellow", service="Discover")
            symbolIndex += 1
            continue

        index_df['Percent Change'] = index_df['Adj Close'].pct_change()
        index_return = (index_df['Percent Change'] + 1).cumprod().iloc[-1]
        intellistock_logger.log(f"{readable_index_name} annual return: {(index_return - 1) * 100:.2f}%", "cyan", service="Discover")

        index_process = 0
        index_stocks_found = 0
        short_count = 0
        long_count = 0

        # Progress bar for this index
        pbar = tqdm(
            tickers,
            desc=f"{readable_index_name}",
            unit="ticker",
            dynamic_ncols=True,
            leave=True,
            ncols=100,
        )

        # Apply short-term / long-term / universal criteria per stock
        for ticker in pbar:
            try:
                pbar.set_postfix_str(f"short: {short_count} long: {long_count} | {ticker[:8]}")

                df = get_or_fetch_price_data(ticker, start_date, end_date, connection)
                # Cache price data for every stock we fetch (even if empty/short or doesn't meet criteria)
                if not df.empty:
                    _save_price_cache(connection, ticker, df)
                if df.empty or len(df) < 200:
                    index_process += 1
                    continue

                m = StockMetrics(df)
                fund = get_fundamentals(ticker)

                # Universal: size & liquidity
                market_cap = (fund.get("marketCap") or 0)
                dollar_vol = (m.volume_latest or 0) * m.price
                univ_size = market_cap >= 2e9 and dollar_vol >= 10e6

                # Universal: exclusions
                no_earnings_soon = not earnings_within_days(fund.get("nextEarningsDate"), 7)
                drawdown_ok = (m.drawdown_pct or 0) <= 15.0

                # Universal: sentiment (when data exists: Buy/Strong Buy, target 15%+ upside)
                rec = (fund.get("recommendationKey") or "").strip().lower()
                sentiment_ok = (rec in ("buy", "strong_buy")) if rec else True
                target = fund.get("targetMeanPrice") or 0
                upside_ok = (target is None or target <= 0) or (m.price > 0 and (target / m.price - 1) >= 0.15)

                # Universal: quality (positive FCF, current ratio > 1.5)
                fcf = fund.get("freeCashflow") or 0
                cr = fund.get("currentRatio") or 0
                quality_ok = fcf > 0 and cr >= 1.5

                universal_ok = univ_size and no_earnings_soon and drawdown_ok and sentiment_ok and upside_ok and quality_ok

                # Short-term criteria (1-3 months)
                rsi_val = m.rsi or 0
                short_momentum = (50 <= rsi_val <= 70 and m.macd_above_signal and m.macd_hist_rising
                    and m.price > (m.sma_20 or 0) and m.price > (m.sma_50 or 0)
                    and m.price_within_pct_of_52w_high(15))
                short_vol = m.volume_latest > 500_000 and (m.volume_avg_20 or 0) > 0 and m.volume_latest >= 1.2 * m.volume_avg_20
                short_obv = m.obv_trending_up_30
                atr_pct = m.atr_pct or 0
                short_volatility = 2 <= atr_pct <= 5
                short_ok = short_momentum and short_vol and short_obv and short_volatility

                # Long-term criteria (6+ months)
                long_trend = m.price > (m.sma_200 or 0) and (m.sma_50 or 0) > (m.sma_200 or 0) and (m.adx or 0) > 25
                pe = fund.get("trailingPE") or 0
                peg = fund.get("pegRatio") or 0
                roe = fund.get("returnOnEquity") or 0  # decimal e.g. 0.15 = 15%
                de = fund.get("debtToEquity") or 999
                long_fund = (10 < pe < 50 and 0.5 <= peg <= 2.0 and roe > 0.15 and de < 1.5)
                rev_g = fund.get("revenueGrowth") or 0   # decimal
                earn_g = fund.get("earningsGrowth") or 0
                long_growth = rev_g > 0.10 and earn_g > 0.15
                fwd_pe = fund.get("forwardPE") or 0
                long_pe_ok = fwd_pe > 0 and pe > 0 and fwd_pe < pe
                long_ok = long_trend and long_fund and long_growth and long_pe_ok

                if universal_ok and (short_ok or long_ok):
                    short_term = short_ok
                    long_term = long_ok
                    intellistock_logger.log(
                        f"✓ {ticker} | ${m.price:.2f} | short={short_term} long={long_term}",
                        "green",
                        service="Discover",
                    )
                    r.db(DB_NAME).table("DiscoverStocks").insert({
                        "id": ticker,
                        "ticker": ticker,
                        "short_term": short_term,
                        "long_term": long_term,
                        "price": m.price,
                        "marketCap": market_cap,
                        "52wHigh": m.high_52w,
                        "52wLow": m.low_52w,
                        "SMA_20": m.sma_20,
                        "SMA_50": m.sma_50,
                        "SMA_200": m.sma_200,
                        "RSI": m.rsi,
                        "ADX": m.adx,
                        "trailingPE": fund.get("trailingPE"),
                        "forwardPE": fund.get("forwardPE"),
                        "pegRatio": fund.get("pegRatio"),
                        "returnOnEquity": fund.get("returnOnEquity"),
                        "debtToEquity": fund.get("debtToEquity"),
                    }, conflict="replace").run(connection)
                    if short_term:
                        short_count += 1
                    if long_term:
                        long_count += 1
                    index_stocks_found += 1
                    total_stocks_found += 1
                    pbar.set_postfix_str(f"short: {short_count} long: {long_count} | {ticker[:8]}")

                index_process += 1
            except Exception as e:
                intellistock_logger.log(f"Error {ticker}: {str(e)}", "red", service="Discover")
                index_process += 1
        
        pbar.close()
        
        intellistock_logger.log("=" * 60, "cyan", service="Discover")
        intellistock_logger.log(
            f"Completed {readable_index_name}: {index_stocks_found} stocks (short: {short_count}, long: {long_count})",
            "green",
            service="Discover",
        )
        intellistock_logger.log("=" * 60, "cyan", service="Discover")
        
        symbolIndex += 1

    now = datetime.datetime.now()

    intellistock_logger.log("=" * 60, "cyan", service="Discover")
    intellistock_logger.log("DISCOVER RUN COMPLETE", "green", service="Discover")
    intellistock_logger.log(f"Total: {total_stocks_found} (short-term: {total_short}, long-term: {total_long})", "green", service="Discover")
    intellistock_logger.log(f"Completion time: {now.strftime('%Y-%m-%d %H:%M:%S')}", "cyan", service="Discover")
    intellistock_logger.log("=" * 60, "cyan", service="Discover")

    # Store last discover run time in RethinkDB Config (no Firebase)
    r.db(DB_NAME).table('Config').insert({
        'id': 'Cache',
        'discoverLastUpdate': str(now),
    }, conflict='update').run(connection)

    connection.close()
    intellistock_logger.log("Database connection closed", "cyan", service="Discover")

except Exception as e:
    import traceback
    try:
        from intellistock_logger import intellistock_logger
        intellistock_logger.log(f"CRITICAL ERROR in discover process: {str(e)}", "red", service="Discover")
        intellistock_logger.log(f"Stack trace: {traceback.format_exc()}", "red", service="Discover")
    except NameError:
        print(f"CRITICAL ERROR in discover process: {e}")
        print(traceback.format_exc())
    try:
        connection.close()
    except Exception:
        pass
