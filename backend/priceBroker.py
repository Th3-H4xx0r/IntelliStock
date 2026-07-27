# Price Broker: fetches stock prices every minute and writes to RethinkDB.
try:
    import time
    import os
    import threading
    import requests
    import yfinance as yf
    from datetime import datetime, timezone
    from dotenv import load_dotenv
    import sys
    from os import system
    from rethinkdb import RethinkDB
    from intellistock_logger import intellistock_logger
    from rethink_changefeed import run_reconnecting_changefeed
except Exception as e:
    import traceback
    traceback.print_exc()

# Load .env from backend dir or project root (when run as subprocess, cwd is backend/)
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BACKEND_DIR, '.env'))
load_dotenv(os.path.join(os.path.dirname(BACKEND_DIR), '.env'))

r = RethinkDB()
DB_NAME = 'IntelliStock'
RETHINKDB_HOST = os.environ.get('RETHINKDB_HOST', 'localhost')
RETHINKDB_PORT = int(os.environ.get('RETHINKDB_PORT', '28015'))
POLL_INTERVAL_SEC = 60

if os.name == 'nt':
    system("title " + "Price Broker")
intellistock_logger.log("Price Broker starting (1-minute poll).", "green", service="PriceBroker")


def get_conn():
    """Create a new RethinkDB connection (connections are not thread-safe)."""
    return r.connect(host=RETHINKDB_HOST, port=RETHINKDB_PORT)


intellistock_logger.log("Connecting to RethinkDB to load ticker list...", "white", service="PriceBroker")
conn = get_conn()
cursor = r.db(DB_NAME).table('LivePricesStocks').run(conn)
tickers = []
for document in cursor:
    data = document
    tickers.append('T.' + str(data['ticker']))
conn.close()

if not tickers:
    intellistock_logger.log("No tickers in LivePricesStocks; add tickers via CLI (add-ticker SYMBOL).", "yellow", service="PriceBroker")
else:
    intellistock_logger.log(f"Loaded {len(tickers)} ticker(s) from LivePricesStocks.", "green", service="PriceBroker")
    intellistock_logger.log(f"Tickers: {tickers}", "white", service="PriceBroker")


def run_config_pings_changefeed():
    """Watch Config.Pings and copy priceBrokerPing -> priceBrokerResponse.

    Self-heals: reconnects on any transient RethinkDB connection loss instead
    of dying on the first error."""
    intellistock_logger.log("Config Pings changefeed thread started; watching for pings.", "white", service="PriceBroker")

    def _handle(change, c):
        new_val = change.get('new_val')
        if new_val and 'priceBrokerPing' in new_val:
            try:
                request = new_val['priceBrokerPing']
                r.db(DB_NAME).table('Config').get('Pings').update({
                    'priceBrokerResponse': request
                }).run(c)
                intellistock_logger.log("Responded to priceBrokerPing.", "white", service="PriceBroker")
            except Exception as e:
                intellistock_logger.log(str(e), "red", service="PriceBroker")

    run_reconnecting_changefeed(
        lambda c: r.db(DB_NAME).table('Config').get('Pings').changes().run(c),
        _handle,
        "Config Pings",
        get_conn=get_conn,
        log=intellistock_logger.log,
    )


def run_config_doc_changefeed():
    """Watch Config.Config: exit when runPriceService is False or terminatePriceBroker is True.

    Self-heals: reconnects on any transient RethinkDB connection loss instead
    of dying on the first error (only the terminate/stop signal exits)."""

    def _handle(change, c):
        new_val = change.get('new_val')
        if new_val is None:
            return
        if new_val.get('terminatePriceBroker') is True:
            intellistock_logger.log("Terminate requested; exiting.", "yellow", service="PriceBroker")
            os._exit(0)
        if new_val.get('runPriceService') is False:
            intellistock_logger.log("runPriceService=False; exiting.", "yellow", service="PriceBroker")
            os._exit(0)

    run_reconnecting_changefeed(
        lambda c: r.db(DB_NAME).table('Config').get('Config').changes().run(c),
        _handle,
        "Config",
        get_conn=get_conn,
        log=intellistock_logger.log,
    )


# Changefeed threads for terminate / runPriceService
intellistock_logger.log("Starting Config changefeed threads (Pings, Config).", "white", service="PriceBroker")
config_pings_feed = threading.Thread(target=run_config_pings_changefeed, daemon=True)
config_pings_feed.start()
config_doc_feed = threading.Thread(target=run_config_doc_changefeed, daemon=True)
config_doc_feed.start()

# Main thread: poll public market data every minute and write to RethinkDB.
intellistock_logger.log("Opening RethinkDB connection for LivePrices / PriceHistory.", "white", service="PriceBroker")
conn = get_conn()
symbols = [t[2:] if t.startswith("T.") else t for t in tickers]
# symbol -> ticker_id for writes
symbol_to_ticker = {sym.upper(): tickers[i] for i, sym in enumerate(symbols)}

_first_run = True


def _latest_prices(symbol_names):
    """Fetch the latest available one-minute close for each symbol."""
    if not symbol_names:
        return {}
    frame = yf.download(
        tickers=symbol_names,
        period="1d",
        interval="1m",
        progress=False,
        auto_adjust=False,
        threads=True,
        group_by="column",
    )
    if frame is None or frame.empty:
        return {}
    closes = frame.get("Close")
    prices = {}
    if len(symbol_names) == 1:
        try:
            series = closes.dropna()
            if not series.empty:
                prices[symbol_names[0].upper()] = float(series.iloc[-1])
        except Exception:
            return {}
        return prices
    for symbol in symbol_names:
        try:
            series = closes[symbol].dropna()
            if not series.empty:
                prices[symbol.upper()] = float(series.iloc[-1])
        except Exception:
            continue
    return prices

while True:
    now = datetime.now(timezone.utc)
    bucket = now.strftime("%Y-%m-%dT%H:%M")
    storage_ts = bucket + ":00.000Z"
    if _first_run:
        intellistock_logger.log("Fetching quotes for all tickers...", "green", service="PriceBroker")
        _first_run = False

    latest = _latest_prices(symbols)
    if not latest:
        intellistock_logger.log("No quotes returned; skipping cycle.", "yellow", service="PriceBroker")
        time.sleep(POLL_INTERVAL_SEC)
        continue

    written = 0
    for sym, price in latest.items():
        ticker_id = symbol_to_ticker.get(sym)
        if not ticker_id:
            continue
        intellistock_logger.log(f"{ticker_id}: {price}", "white", service="PriceBroker")
        r.db(DB_NAME).table('LivePrices').insert({
            "id": ticker_id,
            "ticker": ticker_id,
            "price": price,
        }, conflict='replace').run(conn)
        r.db(DB_NAME).table('PriceHistory').insert({
            "ticker": ticker_id,
            "price": float(price),
            "timestamp": storage_ts,
            "type": "minute",
        }).run(conn)
        written += 1

    intellistock_logger.log(f"Cycle done at {storage_ts}: {written} tickers; sleeping {POLL_INTERVAL_SEC}s.", "white", service="PriceBroker")
    time.sleep(POLL_INTERVAL_SEC)
