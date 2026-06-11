#!/usr/bin/env python
"""
IntelliStock Discord Bot - Same interface as the CLI, via Discord.
Requires: DISCORD_BOT_TOKEN in environment. RethinkDB connection via RETHINKDB_HOST, RETHINKDB_PORT.
Enable in Discord Developer Portal: Bot → Privileged Gateway Intents → "Message Content Intent" ON
(needed so the bot can read !commands). Run: python engines/discord_bot.py (from backend dir)
"""

import asyncio
import logging
import os
import sys

import aiohttp.web

# Run from backend/engines/; backend is parent for imports and cwd
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
os.chdir(_backend_dir)

# ---------------------------------------------------------------------------
# Logging: level from DISCORD_BOT_LOG_LEVEL (DEBUG, INFO, WARNING, ERROR). Default DEBUG for full visibility.
# ---------------------------------------------------------------------------
_LOG_LEVEL = os.environ.get("DISCORD_BOT_LOG_LEVEL", "DEBUG").upper()
_LOG_LEVEL = getattr(logging, _LOG_LEVEL, logging.DEBUG)
logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s.%(msecs)03d | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
# Reduce noise from discord.py and asyncio unless DEBUG
if _LOG_LEVEL != logging.DEBUG:
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

log = logging.getLogger("discord_bot")

import discord
from discord.ext import commands
from dotenv import load_dotenv
load_dotenv()
load_dotenv(os.path.join(_backend_dir, '.env'))
load_dotenv(os.path.join(os.path.dirname(_backend_dir), '.env'))


from interactive_utils import (
    RETHINKDB_HOST,
    RETHINKDB_PORT,
    get_conn,
    parse_granularity_to_seconds,
    ensure_discord_outbox_table,
    ensure_discord_message_ids_table,
    action_get_pending_discord_messages,
    action_mark_discord_message_sent,
    action_mark_discord_message_failed,
    action_requeue_or_fail_discord_message,
    action_store_discord_message_id,
    action_get_discord_message_id,
    action_status,
    action_tickers,
    action_add_ticker,
    action_remove_ticker,
    action_prices,
    action_history,
    action_instances,
    action_create_instance,
    action_delete_instance,
    action_add_stock,
    action_remove_stock,
    action_start_instance,
    action_stop_instance,
    action_terminate_price,
    action_terminate_discover,
    action_start_broker,
    action_strategies,
    action_get_strategy,
    action_create_strategy,
    action_edit_strategy,
    action_delete_strategy,
    action_link_strategy,
    action_list_backtests,
    action_create_backtest,
    action_delete_backtest,
    action_stop_backtest,
    action_stop_all_backtests,
    action_pause_backtest,
    action_resume_backtest,
    action_summarize_backtest,
    action_backtest_logs,
    action_graph_backtest_data,
    action_agent_control_get,
    action_agent_control_set,
    action_agent_restart,
    action_resume_timer,
    action_agent_get_best,
    action_digest_control_get,
    action_digest_control_set,
    action_digest_trigger_send_now,
    action_nexus_control_get,
    action_nexus_control_set,
    action_nexus_status,
    action_nexus_rebuild,
    action_list_trends,
    action_end_trend,
    action_delete_trend,
    action_list_discovered_stocks,
    action_remove_discovered_stock,
    action_nexus_config_get,
    action_nexus_config_set,
    _normalize_nexus_phase_value,
    _nexus_phase_selector_from_value,
)

# Discord limits
EMBED_DESC_MAX = 4096
EMBED_FIELD_VALUE_MAX = 1024
MESSAGE_CONTENT_MAX = 2000


def _truncate(text: str, max_len: int = EMBED_FIELD_VALUE_MAX) -> str:
    if not text:
        return ""
    text = str(text)
    return (text[: max_len - 3] + "...") if len(text) > max_len else text


def _code_block(text: str, lang: str = "") -> str:
    return f"```{lang}\n{_truncate(text, EMBED_DESC_MAX - 10)}\n```"


def _get_conn_sync():
    return get_conn(host=RETHINKDB_HOST, port=RETHINKDB_PORT)


async def run_sync(sync_func, *args, **kwargs):
    """Run a blocking function in a thread so we don't block the event loop."""
    log.debug("run_sync: %s (args=%s, kwargs keys=%s)", sync_func.__name__, len(args), list(kwargs.keys()))
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: sync_func(*args, **kwargs))
        log.debug("run_sync: %s completed", sync_func.__name__)
        return result
    except Exception as e:
        log.exception("run_sync: %s failed: %s", sync_func.__name__, e)
        raise


def _instance_sort_key(row):
    iid = row.get("id")
    if iid is None:
        return (1, "")
    s = str(iid)
    if s.isdigit():
        return (0, int(s))
    return (1, s)


def _parse_nexus_phase_arg(raw):
    if raw in (None, ""):
        return None
    try:
        return _normalize_nexus_phase_value(raw)
    except Exception:
        return None


def _format_nexus_phase_selector(value, fallback):
    selector = _nexus_phase_selector_from_value(value)
    return selector.upper() if selector else fallback


# ---------------------------------------------------------------------------
# Command handlers (return discord.Embed or list of embeds / content)
# ---------------------------------------------------------------------------


async def cmd_help() -> discord.Embed:
    e = discord.Embed(title="IntelliStock CLI (Discord)", color=0x3498DB, description="Same commands as the CLI. Prefix: `!` — use them in this **#cli** channel.")
    e.add_field(
        name="General",
        value="`!help` `!status` `!tickers` `!add-ticker SYM [..]` `!remove-ticker SYM`\n`!prices` `!history [SYM] [N]`",
        inline=False,
    )
    e.add_field(
        name="Instances",
        value="`!instances` `!create-instance ID [name] [key] [secret] [granularity]`\n`!delete-instance ID` `!add-stock INSTANCE SYMBOL` `!remove-stock INSTANCE SYMBOL`\n`!start-instance ID` `!stop-instance ID`",
        inline=False,
    )
    e.add_field(
        name="Broker / Services",
        value="`!terminate-price` `!terminate-discover` `!start-broker`",
        inline=False,
    )
    e.add_field(
        name="Strategies",
        value="`!strategies [instance_id]` `!get-strategy ID` `!delete-strategy ID` `!link-strategy INSTANCE_ID STRATEGY_ID`\n*(create-strategy / edit-strategy: use CLI for interactive)*",
        inline=False,
    )
    e.add_field(
        name="Backtests",
        value="`!backtests` `!create-backtest INSTANCE STOCKS START END [granularity] [cash]`\n`!delete-backtest ID` `!stop-backtest ID` `!stop-all-backtests` `!pause-backtest ID` `!resume-backtest ID`\n`!summarize-backtest ID` `!backtest-logs ID`\n*(graph-backtest: use CLI)*",
        inline=False,
    )
    e.add_field(
        name="Agent",
        value="`!agent start [special request]` `!agent stop` `!agent restart [special request]`\n`!agent pause` `!agent resume` `!agent resume-timer DAYS HOURS MINUTES SECONDS`\n`!agent status` `!agent best`",
        inline=False,
    )
    e.add_field(
        name="Digest",
        value="`!digest start` `!digest stop` `!digest send-now` `!digest status`\n*(Daily briefs to #briefs at 6am/6pm UTC)*",
        inline=False,
    )
    e.add_field(
        name="Nexus",
        value="`!nexus start [start] [end]` `!nexus stop` `!nexus status` `!nexus rebuild`\n*(start only: from phase to 12; end only: 1 to phase; both: range)*",
        inline=False,
    )
    e.add_field(
        name="Trends & Discovery",
        value="`!trends [instance]` `!trends end ID` `!trends delete ID`\n`!discovered [instance]` `!discovered remove INST TICKER`\n`!nexus-config INST [KEY VALUE]`",
        inline=False,
    )
    e.set_footer(text="Example: !status | !tickers | !create-backtest 1 AAPL,MSFT 2024-01-01 2024-06-01")
    return e


async def cmd_status(conn) -> discord.Embed:
    out = await run_sync(action_status, conn)
    e = discord.Embed(title="Services & Engines", color=0x3498DB)
    if out.get("error"):
        e.description = out["error"]
        return e
    engines = out.get("engines") or []
    if engines:
        # Status -> emoji for Discord (no ANSI color support in embeds)
        def status_emoji(s):
            if not s:
                return "⚪"
            if s == "Running":
                return "🟢"
            if s in ("Stopped", "Stopped (terminate)"):
                return "🔴"
            if s == "Paused":
                return "🟡"
            return "⚪"

        # Build a compact table in a code block (monospace)
        w_name = min(22, max(4, max(len(r.get("name", r.get("id", ""))) for r in engines)))
        status_display_list = []
        for row in engines:
            s = row.get("status") or "?"
            status_display_list.append(status_emoji(s) + " " + s)
        w_status = min(28, max(8, max(len(d) for d in status_display_list)))
        lines = []
        lines.append(f"{'Engine':<{w_name}} | {'Status':<{w_status}} | Details")
        lines.append("-" * w_name + "-+-" + "-" * w_status + "-+-" + "-" * 32)
        for i, row in enumerate(engines):
            name = (row.get("name") or row.get("id") or "?")[:w_name].ljust(w_name)
            status = status_display_list[i][:w_status].ljust(w_status)
            details = (row.get("details") or "-")[:32]
            lines.append(f"{name} | {status} | {details}")
        table_text = "\n".join(lines)
        if len(table_text) > EMBED_FIELD_VALUE_MAX:
            table_text = table_text[: EMBED_FIELD_VALUE_MAX - 20] + "\n...(truncated)"
        e.add_field(name="Status", value=f"```\n{table_text}\n```", inline=False)
    else:
        e.add_field(name="Status", value="No engine status (EngineControl empty).", inline=False)
    config = out.get("config")
    if config:
        config_str = " · ".join(f"{k}: {v}" for k, v in (config or {}).items())
        e.add_field(name="Config", value=_truncate(config_str, 1024), inline=False)
    e.set_footer(text="Use !agent status, !digest status, !nexus status for details.")
    return e


async def cmd_tickers(conn) -> discord.Embed:
    out = await run_sync(action_tickers, conn)
    tickers = out.get("tickers", [])
    count = out.get("count", 0)
    e = discord.Embed(title="Tickers (price broker stream)", color=0x2ECC71)
    if not tickers:
        e.description = "No tickers in LivePricesStocks."
        return e
    e.add_field(name="Count", value=str(count), inline=True)
    ticker_list = ", ".join(tickers[:80])
    if count > 80:
        ticker_list += f" ... +{count - 80} more"
    e.add_field(name="Symbols", value=_truncate(ticker_list, 1024), inline=False)
    return e


async def cmd_add_ticker(conn, symbols: list) -> tuple[discord.Embed | None, str | None]:
    if not symbols:
        return None, "Usage: `!add-ticker SYMBOL [SYMBOL ...]`"
    try:
        out = await run_sync(action_add_ticker, conn, symbols)
        added = out.get("added", [])
        if added:
            e = discord.Embed(title="Add ticker", color=0x2ECC71, description=f"Added: {', '.join(added)}")
            e.set_footer(text="Restart the price broker (!start-broker) to pick up new tickers.")
            return e, None
        return None, "No valid symbols to add."
    except ValueError as err:
        return None, str(err)
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_remove_ticker(conn, symbol: str) -> tuple[discord.Embed | None, str | None]:
    if not symbol:
        return None, "Usage: `!remove-ticker SYMBOL`"
    try:
        out = await run_sync(action_remove_ticker, conn, symbol)
        if out.get("removed"):
            e = discord.Embed(title="Remove ticker", color=0x2ECC71, description=f"Removed: {out.get('ticker', symbol)}")
            e.set_footer(text="Restart the price broker (!start-broker) to apply.")
            return e, None
        return None, f"Ticker not found: {out.get('ticker', symbol)}"
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_prices(conn) -> discord.Embed:
    out = await run_sync(action_prices, conn)
    prices = out.get("prices", [])
    e = discord.Embed(title="Current prices (LivePrices)", color=0x2ECC71)
    if not prices:
        e.description = "No prices in LivePrices."
        return e
    e.add_field(name="Count", value=str(len(prices)), inline=True)
    # Batch into fields of ~8 tickers each for clean Discord layout (field value max 1024)
    chunk_size = 8
    for i in range(0, min(len(prices), 48), chunk_size):
        chunk = prices[i : i + chunk_size]
        lines = [f"**{item.get('ticker', '?')}** — {item.get('price', '?')}" for item in chunk]
        name = f"Prices ({i + 1}–{i + len(chunk)})" if len(prices) > chunk_size else "Prices"
        e.add_field(name=name, value="\n".join(lines), inline=False)
    if len(prices) > 48:
        e.set_footer(text=f"Showing 48 of {len(prices)} prices.")
    return e


async def cmd_history(conn, ticker=None, limit=30) -> discord.Embed:
    out = await run_sync(action_history, conn, ticker=ticker, limit=limit)
    history = out.get("history", [])
    e = discord.Embed(title="Price history (PriceHistory)", color=0x9B59B6)
    if not history:
        e.description = "No price history found."
        return e
    e.add_field(name="Showing", value=f"{len(history)} (ticker={ticker or 'all'}, limit={limit})", inline=True)
    # One field per batch of 10 rows for readable Discord layout
    chunk_size = 10
    for i in range(0, min(len(history), 50), chunk_size):
        chunk = history[i : i + chunk_size]
        lines = []
        for row in chunk:
            ts = row.get("timestamp", "?")
            if hasattr(ts, "isoformat"):
                ts = str(ts)[:16]
            lines.append(f"**{row.get('ticker', '?')}** {row.get('price', '?')} — {ts}")
        name = f"History ({i + 1}–{i + len(chunk)})" if len(history) > chunk_size else "History"
        e.add_field(name=name, value="\n".join(lines), inline=False)
    if len(history) > 50:
        e.set_footer(text=f"Showing 50 of {len(history)} rows.")
    return e


async def cmd_instances(conn) -> discord.Embed:
    out = await run_sync(action_instances, conn)
    instances = out.get("instances", [])
    e = discord.Embed(title="Instances", color=0xE67E22)
    if not instances:
        e.description = "No instances."
        return e
    instances = sorted(instances, key=_instance_sort_key)
    max_items = 25
    for row in instances[:max_items]:
        iid = str(row.get("id", "?"))
        name = (row.get("name") or "").strip() or "—"
        status = "Running" if row.get("runCommand", False) else "Stopped"
        strat = str(row.get("strategy_id")) if row.get("strategy_id") is not None else "—"
        stocks = row.get("stocks") or []
        stocks_str = ", ".join(str(s) for s in stocks[:10])
        if len(stocks) > 10:
            stocks_str += f" (+{len(stocks) - 10})"
        if not stocks_str:
            stocks_str = "(none)"
        value = (
            f"**Name:** {name}\n"
            f"**Status:** {status}\n"
            f"**Strategy ID:** {strat}\n"
            f"**Stocks:** {stocks_str}"
        )
        e.add_field(name=f"Instance {iid}", value=value, inline=False)
    if len(instances) > max_items:
        e.set_footer(text=f"Showing {max_items} of {len(instances)} instances.")
    return e


async def cmd_create_instance(conn, instance_id: str, name=None, key=None, secret=None, granularity=None) -> tuple[discord.Embed | None, str | None]:
    if not instance_id or not instance_id.strip():
        return None, "Usage: `!create-instance ID [name] [key] [secret] [granularity]`"
    instance_id = instance_id.strip()
    gran_sec = parse_granularity_to_seconds(granularity or "60")
    try:
        out = await run_sync(
            action_create_instance,
            conn,
            instance_id,
            name=name or None,
            strategy_id=None,
            key=key or None,
            secret=secret or None,
            granularity_time_increment=gran_sec,
        )
        e = discord.Embed(title="Create instance", color=0x2ECC71, description=f"Created: {out.get('id', instance_id)}")
        if out.get("name"):
            e.add_field(name="Name", value=out["name"], inline=True)
        e.set_footer(text="Add tickers: !add-stock <instance_id> SYMBOL")
        return e, None
    except ValueError as err:
        return None, str(err)
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_delete_instance(conn, instance_id: str, force: bool = False) -> tuple[discord.Embed | None, str | None]:
    if not instance_id or not instance_id.strip():
        return None, "Usage: `!delete-instance INSTANCE_ID`"
    try:
        out = await run_sync(action_delete_instance, conn, instance_id.strip(), force=force)
        e = discord.Embed(title="Delete instance", color=0xE74C3C, description=f"Deleted instance: {out.get('id', instance_id)}")
        return e, None
    except ValueError as err:
        return None, str(err)
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_add_stock(conn, instance_id: str, symbol: str) -> tuple[discord.Embed | None, str | None]:
    if not instance_id or not symbol:
        return None, "Usage: `!add-stock INSTANCE_ID SYMBOL`"
    try:
        await run_sync(action_add_stock, conn, instance_id.strip(), symbol.strip().upper())
        e = discord.Embed(title="Add stock", color=0x2ECC71, description=f"Added {symbol.strip().upper()} to instance {instance_id}.")
        return e, None
    except ValueError as err:
        return None, str(err)
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_remove_stock(conn, instance_id: str, symbol: str) -> tuple[discord.Embed | None, str | None]:
    if not instance_id or not symbol:
        return None, "Usage: `!remove-stock INSTANCE_ID SYMBOL`"
    try:
        await run_sync(action_remove_stock, conn, instance_id.strip(), symbol.strip().upper())
        e = discord.Embed(title="Remove stock", color=0x2ECC71, description=f"Removed {symbol.strip().upper()} from instance {instance_id}.")
        return e, None
    except ValueError as err:
        return None, str(err)
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_start_instance(conn, instance_id: str) -> tuple[discord.Embed | None, str | None]:
    if not instance_id:
        return None, "Usage: `!start-instance INSTANCE_ID`"
    try:
        await run_sync(action_start_instance, conn, instance_id.strip())
        e = discord.Embed(title="Start instance", color=0x2ECC71, description=f"Started instance {instance_id}. Server will spawn the process.")
        return e, None
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_stop_instance(conn, instance_id: str) -> tuple[discord.Embed | None, str | None]:
    if not instance_id:
        return None, "Usage: `!stop-instance INSTANCE_ID`"
    try:
        await run_sync(action_stop_instance, conn, instance_id.strip())
        e = discord.Embed(title="Stop instance", color=0xE67E22, description=f"Stopped instance {instance_id}.")
        return e, None
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_terminate_price(conn) -> tuple[discord.Embed | None, str | None]:
    try:
        await run_sync(action_terminate_price, conn)
        e = discord.Embed(title="Terminate price", color=0xE67E22, description="Requested price service to stop.")
        return e, None
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_terminate_discover(conn) -> tuple[discord.Embed | None, str | None]:
    try:
        await run_sync(action_terminate_discover, conn)
        e = discord.Embed(title="Terminate discover", color=0xE67E22, description="Requested discover service to stop.")
        return e, None
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_start_broker(conn) -> tuple[discord.Embed | None, str | None]:
    try:
        await run_sync(action_start_broker, conn)
        e = discord.Embed(title="Start broker", color=0x2ECC71, description="Set runPriceService=True. Price service will start the broker.")
        return e, None
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_strategies(conn, instance_filter=None) -> discord.Embed:
    out = await run_sync(action_strategies, conn, instance_filter=instance_filter)
    strategies = out.get("strategies", [])
    e = discord.Embed(title="Strategies", color=0x9B59B6)
    if not strategies:
        e.description = "No strategies found."
        return e
    max_items = 25
    for row in strategies[:max_items]:
        row_id = row.get("id", "?")
        name = row.get("name", f"id={row_id}")
        inst_ids = row.get("instances_using", [])
        inst_display = ", ".join(inst_ids) if inst_ids else "—"
        subs = row.get("strategies", [])
        sub_lines = []
        for idx, strat in enumerate(subs[:6]):
            if isinstance(strat, dict):
                sub_lines.append(f"• {strat.get('strategy', '?')} (w={strat.get('weight', '?')}, pos={strat.get('execution_position', '?')})")
        if len(subs) > 6:
            sub_lines.append(f"... +{len(subs) - 6} more")
        sub_str = "\n".join(sub_lines) if sub_lines else "—"
        value = (
            f"**Name:** {name}\n"
            f"**Used by instances:** {inst_display}\n"
            f"**Sub-strategies ({len(subs)}):**\n{sub_str}"
        )
        e.add_field(name=f"Strategy {row_id}", value=value, inline=False)
    if len(strategies) > max_items:
        e.set_footer(text=f"Showing {max_items} of {len(strategies)} strategies.")
    return e


async def cmd_get_strategy(conn, strategy_id: str) -> tuple[discord.Embed | None, str | None]:
    if not strategy_id:
        return None, "Usage: `!get-strategy STRATEGY_ID`"
    try:
        out = await run_sync(action_get_strategy, conn, strategy_id)
        e = discord.Embed(title=f"Strategy id={out.get('id')}", color=0x9B59B6)
        e.add_field(name="Name", value=out.get("name", "—"), inline=True)
        subs = out.get("strategies", [])
        lines = []
        for i, s in enumerate(subs):
            lines.append(f"**[{i}]** {s.get('strategy')} — weight {s.get('weight')}, pos {s.get('execution_position')}")
        e.add_field(name="Sub-strategies", value="\n".join(lines) if lines else "—", inline=False)
        return e, None
    except ValueError as err:
        return None, str(err)
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_delete_strategy(conn, strategy_id: str, force: bool = False) -> tuple[discord.Embed | None, str | None]:
    if not strategy_id:
        return None, "Usage: `!delete-strategy STRATEGY_ID`"
    try:
        out = await run_sync(action_delete_strategy, conn, strategy_id, force=force)
        e = discord.Embed(title="Delete strategy", color=0xE74C3C, description=f"Deleted strategy id={out.get('id', strategy_id)}.")
        return e, None
    except ValueError as err:
        return None, str(err)
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_link_strategy(conn, instance_id: str, strategy_id: str) -> tuple[discord.Embed | None, str | None]:
    if not instance_id or not strategy_id:
        return None, "Usage: `!link-strategy INSTANCE_ID STRATEGY_ID`"
    try:
        out = await run_sync(action_link_strategy, conn, instance_id.strip(), strategy_id.strip())
        e = discord.Embed(title="Link strategy", color=0x2ECC71, description=f"Linked instance {out.get('instance_id')} to strategy id={out.get('strategy_id')}.")
        return e, None
    except ValueError as err:
        return None, str(err)
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_list_backtests(conn) -> discord.Embed:
    out = await run_sync(action_list_backtests, conn)
    backtests = out.get("backtests", [])
    e = discord.Embed(title="Backtests", color=0x1ABC9C)
    if not backtests:
        e.description = "No backtests in queue."
        return e
    # Discord max 25 fields; one field per backtest for clear, readable layout
    max_items = 25
    for row in backtests[:max_items]:
        rid = str(row.get("id", ""))
        inst = str(row.get("instance", ""))
        status = str(row.get("status", ""))
        prog = row.get("progress")
        progress_str = f"{prog:.1f}%" if isinstance(prog, (int, float)) else (str(prog) if prog else "—")
        pnl = row.get("pnl")
        pnl_str = f"${pnl:,.2f}" if isinstance(pnl, (int, float)) else str(pnl) if pnl else "—"
        pct = row.get("pnl_percent")
        pnl_pct_str = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else str(pct) if pct else "—"
        start = str(row.get("start_date", ""))[:10]
        end = str(row.get("end_date", ""))[:10]
        stocks = row.get("stocks", [])
        tickers = ", ".join(str(s) for s in stocks[:8])
        if len(stocks) > 8:
            tickers += f" (+{len(stocks) - 8})"
        value = (
            f"**Instance:** {inst}\n"
            f"**Status:** {status}\n"
            f"**Progress:** {progress_str}\n"
            f"**P&L:** {pnl_str} ({pnl_pct_str})\n"
            f"**Period:** {start} → {end}\n"
            f"**Tickers:** {tickers or '—'}"
        )
        e.add_field(name=f"ID {rid}", value=value, inline=False)
    if len(backtests) > max_items:
        e.set_footer(text=f"Showing {max_items} of {len(backtests)} backtests.")
    return e


async def cmd_create_backtest(
    conn, instance_id: str, stocks_str: str, start_date: str, end_date: str, granularity=None, initial_cash=None
) -> tuple[discord.Embed | None, str | None]:
    if not instance_id or not stocks_str or not start_date or not end_date:
        return None, "Usage: `!create-backtest INSTANCE STOCKS START END [granularity] [cash]` e.g. `!create-backtest 1 AAPL,MSFT 2024-01-01 2024-06-01 60 100000`"
    stocks = [s.strip().upper() for s in stocks_str.replace(",", " ").split() if s.strip()]
    if not stocks:
        return None, "At least one stock required."
    gran_sec = parse_granularity_to_seconds(granularity or "60")
    cash = 100000.0
    if initial_cash is not None:
        try:
            cash = float(initial_cash)
            if cash <= 0:
                cash = 100000.0
        except (TypeError, ValueError):
            pass
    try:
        # Read key/secret from instance so the broker can fetch prices for scheduled trades
        instance_doc = await run_sync(lambda c: r.db(DB_NAME).table("Instances").get(instance_id.strip()).run(c), conn)
        inst_key = (instance_doc.get("key") or "") if instance_doc else ""
        inst_secret = (instance_doc.get("secret") or "") if instance_doc else ""
        out = await run_sync(
            action_create_backtest,
            conn,
            instance_id.strip(),
            stocks,
            start_date.strip()[:10],
            end_date.strip()[:10],
            granularity_sec=gran_sec,
            key=inst_key or None,
            secret=inst_secret or None,
            initial_cash=cash,
        )
        e = discord.Embed(title="Create backtest", color=0x2ECC71)
        e.add_field(name="ID", value=str(out["id"]), inline=True)
        e.add_field(name="Instance", value=out["instance"], inline=True)
        e.add_field(name="Period", value=f"{out['start_date']} → {out['end_date']}", inline=True)
        e.add_field(name="Tickers", value=", ".join(out["stocks"][:15]), inline=False)
        e.set_footer(text="Run backtest engine to process.")
        return e, None
    except ValueError as err:
        return None, str(err)
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_delete_backtest(conn, backtest_id: str) -> tuple[discord.Embed | None, str | None]:
    if not backtest_id:
        return None, "Usage: `!delete-backtest ID`"
    try:
        out = await run_sync(action_delete_backtest, conn, backtest_id)
        e = discord.Embed(title="Delete backtest", color=0xE74C3C, description=f"Deleted backtest id={out.get('id', backtest_id)} from queue.")
        return e, None
    except ValueError as err:
        return None, str(err)
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_stop_backtest(conn, backtest_id: str) -> tuple[discord.Embed | None, str | None]:
    if not backtest_id:
        return None, "Usage: `!stop-backtest ID`"
    try:
        out = await run_sync(action_stop_backtest, conn, backtest_id)
        e = discord.Embed(title="Stop backtest", color=0xE67E22, description=f"Stop requested for backtest id={out.get('id', backtest_id)}. Broker will exit.")
        return e, None
    except ValueError as err:
        return None, str(err)
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_stop_all_backtests(conn) -> tuple[discord.Embed | None, str | None]:
    try:
        out = await run_sync(action_stop_all_backtests, conn)
        n = out.get("stopped", 0)
        ids = out.get("ids", [])
        desc = f"Stopped and cleared **{n}** backtest(s) (running + queue). IDs: {ids}"
        c = out.get("containers_stopped", 0)
        if c > 0:
            desc += f" Stopped **{c}** orphan container(s)."
        e = discord.Embed(title="Stop all backtests", color=0xE67E22, description=desc)
        return e, None
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_pause_backtest(conn, backtest_id: str) -> tuple[discord.Embed | None, str | None]:
    if not backtest_id:
        return None, "Usage: `!pause-backtest ID`"
    try:
        out = await run_sync(action_pause_backtest, conn, backtest_id)
        e = discord.Embed(title="Pause backtest", color=0xE67E22, description=f"Pause requested for backtest id={out.get('id', backtest_id)}.")
        return e, None
    except ValueError as err:
        return None, str(err)
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_resume_backtest(conn, backtest_id: str) -> tuple[discord.Embed | None, str | None]:
    if not backtest_id:
        return None, "Usage: `!resume-backtest ID`"
    try:
        out = await run_sync(action_resume_backtest, conn, backtest_id)
        e = discord.Embed(title="Resume backtest", color=0x2ECC71, description=f"Resume requested for backtest id={out.get('id', backtest_id)}.")
        return e, None
    except ValueError as err:
        return None, str(err)
    except Exception as err:
        return None, f"Error: {err}"


async def cmd_summarize_backtest(conn, backtest_id: str) -> tuple[discord.Embed | None, str | None]:
    if not backtest_id or not str(backtest_id).strip():
        return None, "Usage: `!summarize-backtest ID`"
    try:
        doc = await run_sync(action_summarize_backtest, conn, backtest_id)
    except ValueError as err:
        return None, str(err)
    except Exception as err:
        return None, f"Error: {err}"

    bid = doc.get("id")
    status = doc.get("status") or "unknown"
    start_date = doc.get("start_date") or "N/A"
    end_date = doc.get("end_date") or "N/A"
    tickers = doc.get("tickers") or []
    pnl = doc.get("pnl")
    pnl_percent = doc.get("pnl_percent")
    portfolio_start = doc.get("portfolio_start_value")
    portfolio_end = doc.get("portfolio_end_value")
    total_trades = doc.get("total_trades", 0)
    total_buys = doc.get("total_buys", 0)
    total_sells = doc.get("total_sells", 0)
    round_trips = doc.get("round_trips", 0)
    winning = doc.get("winning_round_trips", 0)
    losing = doc.get("losing_round_trips", 0)
    breakeven = doc.get("breakeven_round_trips", 0)
    win_rate = doc.get("win_rate_percent", 0)
    avg_win = doc.get("avg_winning_round_trip")
    avg_loss = doc.get("avg_losing_round_trip")

    e = discord.Embed(title=f"Backtest Summary (id={bid})", color=0x1ABC9C)
    e.add_field(name="Status", value=status, inline=True)
    e.add_field(name="Period", value=f"{start_date} → {end_date}", inline=True)
    e.add_field(name="Tickers", value=", ".join(tickers[:10]) if tickers else "N/A", inline=False)
    if pnl is not None:
        e.add_field(name="P&L", value=f"${pnl:,.2f}", inline=True)
    if pnl_percent is not None:
        e.add_field(name="P&L %", value=f"{pnl_percent:+.2f}%", inline=True)
    if portfolio_start is not None:
        e.add_field(name="Start value", value=f"${portfolio_start:,.2f}", inline=True)
    if portfolio_end is not None:
        e.add_field(name="End value", value=f"${portfolio_end:,.2f}", inline=True)
    e.add_field(name="Trades", value=f"Total: {total_trades} (Buys: {total_buys}, Sells: {total_sells})", inline=False)
    e.add_field(name="Round-trips", value=f"{round_trips} (W: {winning} L: {losing} BE: {breakeven}) | Win rate: {win_rate:.1f}%", inline=False)
    if winning and avg_win is not None:
        e.add_field(name="Avg winning RT", value=f"${avg_win:,.2f}", inline=True)
    if losing and avg_loss is not None:
        e.add_field(name="Avg losing RT", value=f"${avg_loss:,.2f}", inline=True)
    pnl_per_stock = doc.get("pnl_per_stock") or {}
    if pnl_per_stock and len(pnl_per_stock) <= 15:
        lines = [f"{sym}: ${v:,.2f}" for sym, v in pnl_per_stock.items()]
        e.add_field(name="P&L per stock", value=_truncate("\n".join(lines), 1024), inline=False)
    elif pnl_per_stock:
        e.add_field(name="P&L per stock", value=f"{len(pnl_per_stock)} tickers (see CLI for full table)", inline=False)
    return e, None


async def cmd_backtest_logs(conn, backtest_id: str) -> tuple[discord.Embed | None, str | None]:
    if not backtest_id or not str(backtest_id).strip():
        return None, "Usage: `!backtest-logs ID`"
    try:
        out = await run_sync(action_backtest_logs, conn, backtest_id)
    except ValueError as err:
        return None, str(err)
    except Exception as err:
        return None, f"Error: {err}"
    bid = out.get("id")
    status = out.get("status", "?")
    logs = out.get("logs", [])
    error_msg = out.get("error")
    e = discord.Embed(title=f"Backtest logs (id={bid})", color=0x95A5A6)
    e.add_field(name="Status", value=status, inline=True)
    e.add_field(name="Lines", value=str(len(logs)), inline=True)
    if error_msg:
        e.add_field(name="Error", value=_truncate(error_msg, 1024), inline=False)
    if not logs:
        e.description = "No logs saved for this backtest."
        return e, None
    log_text = "\n".join(str(line) for line in logs[-80:])
    if len(logs) > 80:
        log_text = f"... {len(logs) - 80} earlier lines ...\n" + log_text
    e.add_field(name="Log (last lines)", value=_code_block(log_text), inline=False)
    return e, None


async def cmd_graph_backtest(conn, backtest_id: str) -> tuple[discord.Embed | None, str | None]:
    if not backtest_id:
        return None, "Usage: `!graph-backtest ID`"
    e = discord.Embed(
        title="Graph backtest",
        color=0x3498DB,
        description=f"Graph is available in the CLI only (opens a plot window). Backtest ID: {backtest_id}",
    )
    return e, None


# --- Agent ---


async def cmd_agent(conn, sub: str, args: list) -> tuple[discord.Embed | None, str | None]:
    sub = (sub or "").strip().lower()
    if sub == "start":
        try:
            special_request = " ".join(args).strip() if args else None
            await run_sync(action_agent_control_set, conn, running=True, special_request=special_request)
            e = discord.Embed(title="Agent", color=0x2ECC71, description="AI backtesting agent started.")
            if special_request:
                e.add_field(name="Special request", value=_truncate(special_request, 1024), inline=False)
            return e, None
        except Exception as err:
            return None, f"Error: {err}"
    if sub == "stop":
        try:
            await run_sync(action_agent_control_set, conn, running=False)
            e = discord.Embed(title="Agent", color=0xE74C3C, description="AI backtesting agent stopped.")
            return e, None
        except Exception as err:
            return None, f"Error: {err}"
    if sub == "pause":
        try:
            await run_sync(action_agent_control_set, conn, paused=True)
            e = discord.Embed(title="Agent", color=0xE67E22, description="Agent paused (current backtest will pause until resume).")
            return e, None
        except Exception as err:
            return None, f"Error: {err}"
    if sub == "resume":
        try:
            await run_sync(action_agent_control_set, conn, paused=False)
            e = discord.Embed(title="Agent", color=0x2ECC71, description="Agent resumed.")
            return e, None
        except Exception as err:
            return None, f"Error: {err}"
    if sub == "resume-timer":
        if len(args) < 4:
            return None, "Usage: `!agent resume-timer DAYS HOURS MINUTES SECONDS`"
        try:
            days = int(args[0]) if args[0].isdigit() else 0
            hours = int(args[1]) if args[1].isdigit() else 0
            minutes = int(args[2]) if args[2].isdigit() else 0
            seconds = int(args[3]) if args[3].isdigit() else 0
            if days == 0 and hours == 0 and minutes == 0 and seconds == 0:
                return None, "Total time must be > 0."
            await run_sync(action_resume_timer, conn, days=days, hours=hours, minutes=minutes, seconds=seconds)
            from datetime import datetime, timedelta
            total_sec = (days * 86400) + (hours * 3600) + (minutes * 60) + seconds
            resume_at = datetime.utcnow() + timedelta(seconds=total_sec)
            e = discord.Embed(title="Agent resume timer", color=0x2ECC71)
            e.add_field(name="Resume at (UTC)", value=resume_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
            e.add_field(name="In", value=f"{days}d {hours}h {minutes}m {seconds}s", inline=True)
            return e, None
        except Exception as err:
            return None, f"Error: {err}"
    if sub == "status":
        try:
            out = await run_sync(action_agent_control_get, conn)
            e = discord.Embed(title="Agent status", color=0x3498DB)
            e.add_field(name="Running", value=str(out.get("running", False)), inline=True)
            e.add_field(name="Paused", value=str(out.get("paused", False)), inline=True)
            e.add_field(name="Backtests today", value=str(out.get("count_today", 0)), inline=True)
            e.add_field(name="Last run date", value=str(out.get("last_run_date") or "-"), inline=True)
            if out.get("special_request"):
                e.add_field(name="Special request", value=_truncate(out.get("special_request"), 1024), inline=False)
            resume_at = out.get("resume_at")
            if resume_at:
                e.add_field(name="Scheduled resume", value=resume_at, inline=False)
            return e, None
        except Exception as err:
            return None, f"Error: {err}"
    if sub == "best":
        try:
            best = await run_sync(action_agent_get_best, conn)
            if not best:
                e = discord.Embed(title="Agent best", color=0xF1C40F, description="No best strategy set yet. Run the agent and keep a strategy to set one.")
                return e, None
            e = discord.Embed(title="Best strategy (tag Best)", color=0x2ECC71)
            e.add_field(name="ID", value=str(best.get("id")), inline=True)
            e.add_field(name="Name", value=best.get("name", "—"), inline=True)
            e.add_field(name="Overall profit", value=str(best.get("overall_profit", "—")), inline=True)
            e.add_field(name="P&L %", value=str(best.get("pnl_percent", "—")), inline=True)
            e.add_field(name="Updated at", value=str(best.get("updated_at", "—")), inline=False)
            subs = best.get("strategies") or []
            if subs:
                lines = []
                for i, s in enumerate(subs[:10]):
                    cfg = s.get("config") or {}
                    cond = s.get("conditions") or {}
                    lines.append(f"{i + 1}. {s.get('strategy')} weight={s.get('weight')} config={cfg} conditions={cond}")
                e.add_field(name="Sub-strategies", value=_truncate("\n".join(lines), 1024), inline=False)
            return e, None
        except Exception as err:
            return None, f"Error: {err}"
    if sub == "restart":
        try:
            special_request = " ".join(args).strip() if args else None
            e = discord.Embed(title="Agent", color=0xF39C12, description="Restarting AI backtesting agent (stop → wait 5s → start)...")
            if special_request:
                e.add_field(name="Special request", value=_truncate(special_request, 1024), inline=False)
            # Send the "restarting" message first, then do the blocking restart
            # Use run_sync which runs in executor so it won't block the event loop
            await run_sync(action_agent_restart, conn, special_request)
            e = discord.Embed(title="Agent", color=0x2ECC71, description="AI backtesting agent restarted successfully.")
            if special_request:
                e.add_field(name="Special request", value=_truncate(special_request, 1024), inline=False)
            return e, None
        except Exception as err:
            return None, f"Error: {err}"
    return None, "Usage: `!agent start [special request] | stop | restart [special request] | pause | resume | resume-timer | status | best`"


# --- Digest ---


async def cmd_digest(conn, sub: str, args: list) -> tuple[discord.Embed | None, str | None]:
    sub = (sub or "").strip().lower()
    if sub == "start":
        try:
            await run_sync(action_digest_control_set, conn, running=True)
            e = discord.Embed(title="Digest", color=0x2ECC71, description="Daily digest engine started. Briefs at 6am & 6pm UTC to #briefs.")
            return e, None
        except Exception as err:
            return None, f"Error: {err}"
    if sub == "stop":
        try:
            await run_sync(action_digest_control_set, conn, running=False)
            e = discord.Embed(title="Digest", color=0xE74C3C, description="Daily digest engine stopped.")
            return e, None
        except Exception as err:
            return None, f"Error: {err}"
    if sub == "send-now":
        try:
            await run_sync(action_digest_trigger_send_now, conn)
            e = discord.Embed(title="Digest", color=0x3498DB, description="Triggered. A brief will be sent to #briefs within ~1 minute.")
            return e, None
        except Exception as err:
            return None, f"Error: {err}"
    if sub == "status":
        try:
            out = await run_sync(action_digest_control_get, conn)
            e = discord.Embed(title="Digest status", color=0x3498DB)
            e.add_field(name="Running", value=str(out.get("running", True)), inline=True)
            e.add_field(name="Send now pending", value=str(out.get("send_now", False)), inline=True)
            e.add_field(name="Last sent", value=str(out.get("last_sent_at") or "—"), inline=False)
            e.add_field(name="Last morning", value=str(out.get("last_morning_at") or "—"), inline=True)
            e.add_field(name="Last evening", value=str(out.get("last_evening_at") or "—"), inline=True)
            return e, None
        except Exception as err:
            return None, f"Error: {err}"
    return None, "Usage: `!digest start|stop|send-now|status`"


# --- Nexus ---


async def cmd_nexus(conn, sub: str, args: list) -> tuple[discord.Embed | None, str | None]:
    sub = (sub or "").strip().lower()
    if sub == "start":
        try:
            start_phase = None
            end_phase = None
            if len(args) >= 1:
                start_phase = _parse_nexus_phase_arg(args[0])
            if len(args) >= 2:
                end_phase = _parse_nexus_phase_arg(args[1])
            await run_sync(action_nexus_control_set, conn, running=True, start_phase=start_phase, end_phase=end_phase)
            desc = "Graph Nexus service start requested. Server will spawn the process."
            if start_phase is not None or end_phase is not None:
                desc += " Phases %s-%s will run (even if graph already built)." % (
                    _format_nexus_phase_selector(start_phase or 1, str(start_phase or 1)),
                    _format_nexus_phase_selector(end_phase or 14, str(end_phase or 14)),
                )
            e = discord.Embed(title="Nexus", color=0x2ECC71, description=desc)
            return e, None
        except Exception as err:
            return None, f"Error: {err}"
    if sub == "stop":
        try:
            await run_sync(action_nexus_control_set, conn, running=False)
            e = discord.Embed(title="Nexus", color=0xE74C3C, description="Graph Nexus service stop requested. Nexus will save progress and exit.")
            return e, None
        except Exception as err:
            return None, f"Error: {err}"
    if sub == "status":
        try:
            from interactive_utils import format_nexus_status_for_discord
            out = await run_sync(action_nexus_status, conn)
            embed_dict = format_nexus_status_for_discord(out)
            e = discord.Embed(
                title=embed_dict.get("title", "Nexus status"),
                description=embed_dict.get("description", ""),
                color=embed_dict.get("color", 0x3498DB),
            )
            for f in embed_dict.get("fields", []):
                e.add_field(name=f.get("name", ""), value=f.get("value", "—"), inline=f.get("inline", False))
            return e, None
        except Exception as err:
            return None, f"Error: {err}"
    if sub == "rebuild":
        confirmed = args and (args[0] or "").strip().lower() == "confirm"
        if not confirmed:
            e = discord.Embed(title="Nexus rebuild", color=0xF39C12)
            e.description = "This will reset the **entire graph** (Neo4j), **progress** (RethinkDB), and **cached CSVs**. The next `!nexus start` will rebuild from phase 1."
            e.add_field(name="Confirm", value="Reply with `!nexus rebuild confirm` to proceed.", inline=False)
            return e, None
        try:
            out = await run_sync(action_nexus_rebuild, conn, destructive=True)
            if out.get("success"):
                e = discord.Embed(title="Nexus rebuild", color=0x2ECC71, description=out.get("message", "Done."))
                if out.get("neo4j_cleared"):
                    e.add_field(name="Neo4j", value="Graph cleared", inline=True)
                if out.get("rethinkdb_cleared"):
                    e.add_field(name="RethinkDB", value="Progress cleared", inline=True)
                if out.get("cache_cleared"):
                    e.add_field(name="Cache", value="CSVs and sec_edgar_filings cleared", inline=True)
                return e, None
            e = discord.Embed(title="Nexus rebuild", color=0xE74C3C, description=out.get("message", "Failed."))
            if out.get("error"):
                e.add_field(name="Error", value=_truncate(out.get("error"), 1024), inline=False)
            return e, None
        except Exception as err:
            return None, f"Error: {err}"
    return None, "Usage: `!nexus start [start_phase] [end_phase]` | stop | status | rebuild. Phase selectors accept 1-14 or labels like 2b / 6b."


# --- Trends ---

async def cmd_trends(conn, sub: str, args: list) -> tuple:
    """Show/manage market trends."""
    sub = sub.strip().lower()
    if sub == "end":
        if not args:
            return None, "Usage: `!trends end TREND_ID`"
        try:
            out = await run_sync(action_end_trend, conn, args[0])
            if out.get("ended"):
                return discord.Embed(title="Trend Ended", color=0x2ECC71, description=f"Trend `{args[0]}` ended."), None
            return discord.Embed(title="Trend", color=0xF39C12, description=out.get("message", "Could not end trend.")), None
        except Exception as err:
            return None, f"Error: {err}"
    elif sub == "delete":
        if not args:
            return None, "Usage: `!trends delete TREND_ID`"
        try:
            await run_sync(action_delete_trend, conn, args[0])
            return discord.Embed(title="Trend Deleted", color=0x2ECC71, description=f"Trend `{args[0]}` deleted."), None
        except Exception as err:
            return None, f"Error: {err}"
    else:
        # List trends; sub may be instance_id
        instance_id = sub if sub else None
        try:
            out = await run_sync(action_list_trends, conn, instance_id=instance_id, status="active")
            trends = out.get("trends", [])
            e = discord.Embed(title=f"Market Trends ({len(trends)} active)", color=0x3498DB)
            if not trends:
                e.description = "No active trends."
                return e, None
            for t in trends[:20]:
                direction = t.get("direction", "?")
                strength = t.get("strength", 0)
                tickers = ", ".join(t.get("affected_tickers", [])[:5])
                name = t.get("name", t.get("id", "?"))
                arrow = "\u25B2" if direction == "bullish" else ("\u25BC" if direction == "bearish" else "\u2014")
                desc = t.get("description", "")[:80]
                tickers_display = tickers or "\u2014"
                value = f"{arrow} **{direction}** (strength={strength:.2f})\nTickers: {tickers_display}"
                if desc:
                    value += f"\n{desc}"
                e.add_field(name=name[:256], value=_truncate(value, 1024), inline=False)
            if len(trends) > 20:
                e.set_footer(text=f"Showing 20 of {len(trends)} trends.")
            return e, None
        except Exception as err:
            return None, f"Error: {err}"


async def cmd_discovered(conn, sub: str, args: list) -> tuple:
    """Show/manage discovered stocks."""
    sub = sub.strip().lower()
    if sub == "remove":
        if len(args) < 2:
            return None, "Usage: `!discovered remove INSTANCE_ID TICKER`"
        try:
            out = await run_sync(action_remove_discovered_stock, conn, args[0], args[1])
            return discord.Embed(title="Stock Removed", color=0x2ECC71,
                                 description=f"Removed `{args[1].upper()}` from discovered stocks."), None
        except Exception as err:
            return None, f"Error: {err}"
    else:
        instance_id = sub if sub else None
        try:
            out = await run_sync(action_list_discovered_stocks, conn, instance_id=instance_id, status="active")
            stocks = out.get("stocks", [])
            e = discord.Embed(title=f"Discovered Stocks ({len(stocks)} active)", color=0x9B59B6)
            if not stocks:
                e.description = "No active discovered stocks."
                return e, None
            for s in stocks[:25]:
                tk = s.get("ticker", "?")
                source = s.get("source_trend", "?")
                discovered = s.get("discovered_at", "?")[:10]
                e.add_field(name=tk, value=f"Source: {source}\nDiscovered: {discovered}", inline=True)
            if len(stocks) > 25:
                e.set_footer(text=f"Showing 25 of {len(stocks)} stocks.")
            return e, None
        except Exception as err:
            return None, f"Error: {err}"


async def cmd_nexus_config(conn, sub: str, args: list) -> tuple:
    """Show/set nexus config."""
    if not sub:
        return None, "Usage: `!nexus-config INSTANCE_ID [KEY VALUE]`"
    instance_id = sub
    if len(args) >= 2:
        key, raw_value = args[0], args[1]
        v = raw_value
        if v.lower() in ("true", "yes"):
            v = True
        elif v.lower() in ("false", "no"):
            v = False
        else:
            try:
                v = int(v)
            except ValueError:
                try:
                    v = float(v)
                except ValueError:
                    pass
        try:
            out = await run_sync(action_nexus_config_set, conn, instance_id, {key: v})
            applied = out.get("applied", {})
            if applied:
                return discord.Embed(title="Config Updated", color=0x2ECC71,
                                     description="\n".join(f"**{k}:** {val}" for k, val in applied.items())), None
            return discord.Embed(title="Config", color=0xF39C12, description="No valid keys to update."), None
        except Exception as err:
            return None, f"Error: {err}"
    else:
        try:
            out = await run_sync(action_nexus_config_get, conn, instance_id)
            config = out.get("config", {})
            e = discord.Embed(title=f"Nexus Config ({instance_id})", color=0x3498DB)
            for k, v in sorted(config.items()):
                e.add_field(name=k, value=str(v), inline=True)
            return e, None
        except Exception as err:
            return None, f"Error: {err}"


# ---------------------------------------------------------------------------
# Bot setup and command router
# ---------------------------------------------------------------------------

CLI_CHANNEL_NAME = "cli🤖"

# Channels the bot ensures exist in each guild (cli, notifications, trades, briefs, backtests, ai-backtest-agent, best-strategies)
BOT_CHANNELS = ("cli🤖", "notifications🔔", "trades📈", "briefs📋", "backtests📊", "ai-backtest-agent🤖", "best-strategies🏆")

# Short names → full channel name (for API and outbox)
CHANNEL_ALIASES = {
    "cli": "cli🤖",
    "notifications": "notifications🔔",
    "trades": "trades📈",
    "briefs": "briefs📋",
    "morning-briefs": "briefs📋",
    "morning_briefs": "briefs📋",
    "backtests": "backtests📊",
    "ai-backtest-agent": "ai-backtest-agent🤖",
    "ai_backtest_agent": "ai-backtest-agent🤖",
    "best-strategies": "best-strategies🏆",
    "best_strategies": "best-strategies🏆",
}

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


async def ensure_channel(guild: discord.Guild, channel_name: str) -> discord.TextChannel | None:
    """Create a text channel with the given name if it doesn't exist. Returns the channel or None on failure."""
    name_lower = channel_name.lower()
    for ch in guild.text_channels:
        if ch.name.lower() == name_lower:
            log.debug("ensure_channel: found existing #%s (id=%s) in '%s'", ch.name, ch.id, guild.name)
            return ch
    log.info("ensure_channel: creating #%s in guild '%s' (id=%s)", channel_name, guild.name, guild.id)
    try:
        channel = await guild.create_text_channel(channel_name)
        log.info("ensure_channel: created #%s (id=%s) in guild '%s'", channel.name, channel.id, guild.name)
        return channel
    except discord.Forbidden as e:
        log.warning("ensure_channel: cannot create #%s in '%s': %s (need Manage Channels)", channel_name, guild.name, e)
        return None
    except Exception as e:
        log.exception("ensure_channel: failed for #%s in '%s': %s", channel_name, guild.name, e)
        return None


async def ensure_all_bot_channels(guild: discord.Guild) -> list[discord.TextChannel]:
    """Ensure all BOT_CHANNELS exist in the guild. Returns list of channels that were found or created."""
    created_or_found = []
    for name in BOT_CHANNELS:
        ch = await ensure_channel(guild, name)
        if ch:
            created_or_found.append(ch)
    return created_or_found


def _resolve_channel_name(channel_key: str) -> str:
    """Resolve short name to full channel name with emoji."""
    key = (channel_key or "").strip().lower()
    return CHANNEL_ALIASES.get(key, channel_key.strip())


def _get_guild_for_send(guild_id=None) -> discord.Guild | None:
    """Get guild by id or first available."""
    if not bot.guilds:
        return None
    if guild_id is not None:
        g = bot.get_guild(int(guild_id))
        if g is not None:
            return g
    return bot.guilds[0]


async def get_channel_for_send(channel_key: str, guild_id=None) -> discord.TextChannel | None:
    """Resolve channel key (short or full name) to a TextChannel. Uses first guild if guild_id omitted."""
    guild = _get_guild_for_send(guild_id)
    if guild is None:
        return None
    name = _resolve_channel_name(channel_key)
    for ch in guild.text_channels:
        if ch.name == name or ch.name.lower() == name.lower():
            return ch
    return None


async def send_to_discord_channel(channel_key: str, content: str = "", embed_dict: dict | None = None, guild_id=None) -> tuple[bool, discord.Message | None]:
    """
    Send a message to a Discord channel by name. Used by HTTP API and outbox poller.
    channel_key: short name (notifications, trades, ...) or full name with emoji.
    Returns (True, sent_message) if sent, (False, None) otherwise. sent_message is the Discord Message for storing id (edits).
    """
    ch = await get_channel_for_send(channel_key, guild_id)
    if ch is None:
        log.warning("send_to_discord_channel: channel not found for key=%s guild_id=%s", channel_key, guild_id)
        return False, None
    try:
        if embed_dict and isinstance(embed_dict, dict):
            embed = discord.Embed(
                title=embed_dict.get("title"),
                description=embed_dict.get("description"),
                color=embed_dict.get("color"),
                url=embed_dict.get("url"),
            )
            for f in embed_dict.get("fields") or []:
                embed.add_field(
                    name=f.get("name", ""),
                    value=f.get("value", ""),
                    inline=f.get("inline", True),
                )
            if embed_dict.get("footer"):
                embed.set_footer(text=embed_dict["footer"])
            sent = await ch.send(content=content or None, embed=embed)
        else:
            sent = await ch.send(content=content or "(no content)")
        log.info("send_to_discord_channel: sent to #%s (guild=%s)", ch.name, ch.guild.name)
        return True, sent
    except Exception as e:
        log.exception("send_to_discord_channel: failed for #%s: %s", channel_key, e)
        return False, None


# ---------------------------------------------------------------------------
# HTTP API and RethinkDB outbox (for other services to send messages)
# ---------------------------------------------------------------------------

_http_app = None
_http_runner = None
_outbox_task = None

HTTP_PORT = int(os.environ.get("DISCORD_BOT_HTTP_PORT", "8050"))
API_KEY = os.environ.get("DISCORD_BOT_API_KEY", "").strip()


async def _handle_send(request):
    """POST /send — body: { "channel": "notifications", "content": "...", "embed": {...}, "guild_id": optional }."""
    if API_KEY:
        auth = request.headers.get("X-API-Key") or request.headers.get("Authorization") or ""
        if auth.replace("Bearer ", "").strip() != API_KEY:
            return aiohttp.web.json_response({"ok": False, "error": "Unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception as e:
        return aiohttp.web.json_response({"ok": False, "error": str(e)}, status=400)
    channel = data.get("channel") or data.get("channel_name")
    if not channel:
        return aiohttp.web.json_response({"ok": False, "error": "channel required"}, status=400)
    content = data.get("content", "")
    embed = data.get("embed")
    guild_id = data.get("guild_id")
    ok, _ = await send_to_discord_channel(channel, content=content, embed_dict=embed, guild_id=guild_id)
    if ok:
        return aiohttp.web.json_response({"ok": True, "channel": channel})
    return aiohttp.web.json_response({"ok": False, "error": "channel not found or send failed"}, status=500)


async def _handle_health(request):
    """GET /health — liveness."""
    return aiohttp.web.json_response({"ok": True, "service": "discord_bot"})


async def _outbox_poller():
    """Background task: poll DiscordOutbox for pending messages and send them."""
    log.debug("outbox_poller: started")
    while True:
        try:
            await asyncio.sleep(5)
            conn = None
            try:
                conn = await run_sync(_get_conn)
                ensure_discord_outbox_table(conn)
                ensure_discord_message_ids_table(conn)
                pending = await run_sync(action_get_pending_discord_messages, conn, 50)
            except Exception as e:
                log.warning("outbox_poller: get pending failed: %s", e)
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass
                continue
            for msg in pending:
                msg_id = msg.get("id")
                channel = msg.get("channel")
                content = msg.get("content", "")
                embed = msg.get("embed")
                guild_id = msg.get("guild_id")
                delete_key = msg.get("delete_key")
                edit_key = msg.get("edit_key")
                message_key = msg.get("message_key")
                try:
                    if delete_key:
                        # Delete existing message by message_key (e.g. agent stop — remove current chunk slots)
                        stored = await run_sync(action_get_discord_message_id, conn, channel, delete_key)
                        if not stored or not stored.get("message_id"):
                            await run_sync(action_mark_discord_message_failed, conn, msg_id, "delete_key: message not found")
                            continue
                        ch = await get_channel_for_send(channel, stored.get("guild_id"))
                        if ch is None:
                            await run_sync(action_mark_discord_message_failed, conn, msg_id, "channel not found for delete")
                            continue
                        discord_msg = await ch.fetch_message(int(stored["message_id"]))
                        await discord_msg.delete()
                        await run_sync(action_mark_discord_message_sent, conn, msg_id)
                    elif edit_key:
                        # Edit existing message by message_key
                        stored = await run_sync(action_get_discord_message_id, conn, channel, edit_key)
                        if not stored or not stored.get("message_id"):
                            await run_sync(action_mark_discord_message_failed, conn, msg_id, "edit_key: message not found")
                            continue
                        ch = await get_channel_for_send(channel, stored.get("guild_id"))
                        if ch is None:
                            await run_sync(action_mark_discord_message_failed, conn, msg_id, "channel not found for edit")
                            continue
                        discord_msg = await ch.fetch_message(int(stored["message_id"]))
                        if embed and isinstance(embed, dict):
                            new_embed = discord.Embed(
                                title=embed.get("title"),
                                description=embed.get("description"),
                                color=embed.get("color"),
                                url=embed.get("url"),
                            )
                            for f in embed.get("fields") or []:
                                new_embed.add_field(
                                    name=f.get("name", ""),
                                    value=f.get("value", ""),
                                    inline=f.get("inline", True),
                                )
                            if embed.get("footer"):
                                new_embed.set_footer(text=embed["footer"])
                            await discord_msg.edit(content=content or None, embed=new_embed)
                        else:
                            await discord_msg.edit(content=content or "(no content)", embed=None)
                        await run_sync(action_mark_discord_message_sent, conn, msg_id)
                    else:
                        ok, sent = await send_to_discord_channel(channel, content=content, embed_dict=embed, guild_id=guild_id)
                        if ok:
                            await run_sync(action_mark_discord_message_sent, conn, msg_id)
                            if message_key and sent:
                                await run_sync(action_store_discord_message_id, conn, channel, message_key, sent.id, guild_id)
                        else:
                            # Transient send failure: requeue with backoff
                            # (gives up only after RETRY_MAX_ATTEMPTS) so a
                            # message isn't silently dropped on first failure.
                            await run_sync(action_requeue_or_fail_discord_message, conn, msg_id, "channel not found or send failed")
                except Exception as e:
                    log.exception("outbox_poller: send/edit failed for id=%s: %s", msg_id, e)
                    try:
                        await run_sync(action_requeue_or_fail_discord_message, conn, msg_id, str(e))
                    except Exception:
                        pass
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
        except asyncio.CancelledError:
            log.debug("outbox_poller: cancelled")
            break
        except Exception as e:
            log.exception("outbox_poller: %s", e)


async def _start_http_and_outbox():
    """Start HTTP server and outbox poller (called from on_ready)."""
    global _http_app, _http_runner, _outbox_task
    app = aiohttp.web.Application()
    app.router.add_post("/send", _handle_send)
    app.router.add_get("/health", _handle_health)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "0.0.0.0", HTTP_PORT)
    await site.start()
    _http_app = app
    _http_runner = runner
    log.info("HTTP API listening on 0.0.0.0:%s — POST /send (channel, content, embed?), GET /health", HTTP_PORT)
    _outbox_task = asyncio.create_task(_outbox_poller())


@bot.event
async def on_ready():
    log.info("Discord bot logged in as %s (ID: %s)", bot.user, bot.user.id if bot.user else None)
    log.info("Prefix: ! — commands only work in the #cli channel.")
    for guild in bot.guilds:
        channels = await ensure_all_bot_channels(guild)
        if channels:
            names = ", ".join(f"#{c.name}" for c in channels)
            log.info("Guild '%s' (id=%s): channels ready — %s", guild.name, guild.id, names)
        else:
            log.warning("Guild '%s' (id=%s): could not ensure any channels (need Manage Channels)", guild.name, guild.id)
    log.debug("on_ready: finished; %d guilds", len(bot.guilds))
    # Start HTTP API and outbox poller so other services can send messages
    await _start_http_and_outbox()


@bot.event
async def on_guild_join(guild: discord.Guild):
    """When the bot is added to a server, create #cli, #notifications, #trades, #morning-briefs if they don't exist."""
    log.info("on_guild_join: joined guild '%s' (id=%s)", guild.name, guild.id)
    channels = await ensure_all_bot_channels(guild)
    cli_ch = next((c for c in channels if c.name == CLI_CHANNEL_NAME), None)
    if cli_ch:
        try:
            await cli_ch.send("Use `!help` here to see IntelliStock CLI commands.")
            log.debug("on_guild_join: sent welcome message to #cli in '%s'", guild.name)
        except discord.Forbidden as e:
            log.warning("on_guild_join: cannot send message in #cli in '%s': %s", guild.name, e)
        except Exception as e:
            log.exception("on_guild_join: failed to send welcome message: %s", e)
    if not channels:
        log.warning("on_guild_join: no bot channels in '%s'", guild.name)


@bot.event
async def on_connect():
    log.debug("on_connect: connected to Discord gateway")


@bot.event
async def on_disconnect():
    log.warning("on_disconnect: disconnected from Discord gateway")


@bot.event
async def on_error(event, *args, **kwargs):
    log.exception("on_error: event=%s args=%s kwargs=%s", event, args, kwargs)


def _get_conn():
    return _get_conn_sync()


@bot.event
async def on_message(message: discord.Message):
    # Skip other bots
    if message.author.bot:
        log.debug("on_message: ignoring message from bot author")
        return
    # Only accept CLI commands in the dedicated #cli channel
    if message.channel.name.lower() != CLI_CHANNEL_NAME.lower():
        log.debug("on_message: ignoring channel '%s' (not #cli)", message.channel.name)
        return
    raw = message.content.strip()
    if not raw.lower().startswith("!"):
        log.debug("on_message: content does not start with ! (len=%d)", len(raw))
        return
    parts = raw.split()
    if not parts:
        log.debug("on_message: empty parts after split")
        return
    cmd = parts[0].lower().lstrip("!")
    args = parts[1:] if len(parts) > 1 else []
    log.info(
        "on_message: command='!%s' args=%s | guild=%s channel=%s user=%s (id=%s)",
        cmd, args, message.guild.name if message.guild else "DM", message.channel.name, message.author, message.author.id,
    )

    conn = None
    try:
        log.debug("on_message: connecting to RethinkDB (%s:%s)", RETHINKDB_HOST, RETHINKDB_PORT)
        conn = await run_sync(_get_conn)
        log.debug("on_message: RethinkDB connected")
    except Exception as err:
        log.exception("on_message: RethinkDB connection failed: %s", err)
        await message.reply(embed=discord.Embed(title="Error", color=0xE74C3C, description=f"Cannot connect to RethinkDB: {err}"))
        return

    try:
        result_embed = None
        error_msg = None
        log.debug("on_message: dispatching command !%s with args %s", cmd, args)

        if cmd in ("help", "h"):
            result_embed = await cmd_help()
        elif cmd in ("status", "s"):
            result_embed = await cmd_status(conn)
        elif cmd in ("tickers", "t"):
            result_embed = await cmd_tickers(conn)
        elif cmd == "add-ticker":
            result_embed, error_msg = await cmd_add_ticker(conn, args)
        elif cmd == "remove-ticker":
            result_embed, error_msg = await cmd_remove_ticker(conn, args[0] if args else "")
        elif cmd in ("prices", "p"):
            result_embed = await cmd_prices(conn)
        elif cmd in ("history", "ph"):
            ticker_arg = None
            limit_arg = 30
            if args:
                if args[0].isdigit():
                    limit_arg = min(int(args[0]), 500)
                else:
                    ticker_arg = args[0]
                    if len(args) >= 2 and args[1].isdigit():
                        limit_arg = min(int(args[1]), 500)
            result_embed = await cmd_history(conn, ticker=ticker_arg, limit=limit_arg)
        elif cmd in ("instances", "i"):
            result_embed = await cmd_instances(conn)
        elif cmd == "create-instance":
            id_arg = args[0] if args else ""
            name_arg = args[1] if len(args) > 1 else None
            key_arg = args[2] if len(args) > 2 else None
            secret_arg = args[3] if len(args) > 3 else None
            gran_arg = args[4] if len(args) > 4 else None
            result_embed, error_msg = await cmd_create_instance(conn, id_arg, name=name_arg, key=key_arg, secret=secret_arg, granularity=gran_arg)
        elif cmd == "delete-instance":
            inst_arg = args[0] if args else ""
            force = "force" in [a.lower() for a in args[1:]]
            result_embed, error_msg = await cmd_delete_instance(conn, inst_arg, force=force)
        elif cmd == "add-stock":
            inst_arg = args[0] if args else ""
            sym_arg = args[1] if len(args) > 1 else ""
            result_embed, error_msg = await cmd_add_stock(conn, inst_arg, sym_arg)
        elif cmd == "remove-stock":
            inst_arg = args[0] if args else ""
            sym_arg = args[1] if len(args) > 1 else ""
            result_embed, error_msg = await cmd_remove_stock(conn, inst_arg, sym_arg)
        elif cmd == "start-instance":
            result_embed, error_msg = await cmd_start_instance(conn, args[0] if args else "")
        elif cmd == "stop-instance":
            result_embed, error_msg = await cmd_stop_instance(conn, args[0] if args else "")
        elif cmd == "terminate-price":
            result_embed, error_msg = await cmd_terminate_price(conn)
        elif cmd == "terminate-discover":
            result_embed, error_msg = await cmd_terminate_discover(conn)
        elif cmd == "start-broker":
            result_embed, error_msg = await cmd_start_broker(conn)
        elif cmd in ("strategies", "st"):
            inst_filter = args[0] if args else None
            result_embed = await cmd_strategies(conn, instance_filter=inst_filter)
        elif cmd == "create-strategy":
            result_embed = discord.Embed(
                title="Create strategy",
                color=0xF1C40F,
                description="Strategy creation is interactive. Use the CLI: `python backend/cli.py` then run `create-strategy`.",
            )
        elif cmd == "edit-strategy":
            result_embed = discord.Embed(
                title="Edit strategy",
                color=0xF1C40F,
                description="Strategy editing is interactive. Use the CLI: `python backend/cli.py` then run `edit-strategy <ID>`.",
            )
        elif cmd == "get-strategy":
            result_embed, error_msg = await cmd_get_strategy(conn, args[0] if args else "")
        elif cmd == "delete-strategy":
            sid_arg = args[0] if args else ""
            force = "force" in [a.lower() for a in args[1:]]
            result_embed, error_msg = await cmd_delete_strategy(conn, sid_arg, force=force)
        elif cmd == "link-strategy":
            if len(args) < 2:
                error_msg = "Usage: `!link-strategy INSTANCE_ID STRATEGY_ID`"
            else:
                result_embed, error_msg = await cmd_link_strategy(conn, args[0], args[1])
        elif cmd in ("backtests", "bt"):
            result_embed = await cmd_list_backtests(conn)
        elif cmd == "create-backtest":
            if len(args) < 4:
                error_msg = "Usage: `!create-backtest INSTANCE STOCKS START END [granularity] [cash]`"
            else:
                result_embed, error_msg = await cmd_create_backtest(
                    conn, args[0], args[1], args[2], args[3],
                    granularity=args[4] if len(args) > 4 else None,
                    initial_cash=args[5] if len(args) > 5 else None,
                )
        elif cmd == "delete-backtest":
            result_embed, error_msg = await cmd_delete_backtest(conn, args[0] if args else "")
        elif cmd == "stop-backtest":
            result_embed, error_msg = await cmd_stop_backtest(conn, args[0] if args else "")
        elif cmd == "stop-all-backtests":
            result_embed, error_msg = await cmd_stop_all_backtests(conn)
        elif cmd == "pause-backtest":
            result_embed, error_msg = await cmd_pause_backtest(conn, args[0] if args else "")
        elif cmd == "resume-backtest":
            result_embed, error_msg = await cmd_resume_backtest(conn, args[0] if args else "")
        elif cmd == "summarize-backtest":
            result_embed, error_msg = await cmd_summarize_backtest(conn, args[0] if args else "")
        elif cmd == "backtest-logs":
            result_embed, error_msg = await cmd_backtest_logs(conn, args[0] if args else "")
        elif cmd == "graph-backtest":
            result_embed, error_msg = await cmd_graph_backtest(conn, args[0] if args else "")
        elif cmd == "agent":
            agent_sub = args[0] if args else ""
            agent_args = args[1:] if len(args) > 1 else []
            result_embed, error_msg = await cmd_agent(conn, agent_sub, agent_args)
        elif cmd == "digest":
            digest_sub = args[0] if args else ""
            digest_args = args[1:] if len(args) > 1 else []
            result_embed, error_msg = await cmd_digest(conn, digest_sub, digest_args)
        elif cmd == "nexus":
            nexus_sub = args[0] if args else ""
            nexus_args = args[1:] if len(args) > 1 else []
            result_embed, error_msg = await cmd_nexus(conn, nexus_sub, nexus_args)
        elif cmd == "trends":
            trends_sub = args[0] if args else ""
            trends_args = args[1:] if len(args) > 1 else []
            result_embed, error_msg = await cmd_trends(conn, trends_sub, trends_args)
        elif cmd == "discovered":
            disc_sub = args[0] if args else ""
            disc_args = args[1:] if len(args) > 1 else []
            result_embed, error_msg = await cmd_discovered(conn, disc_sub, disc_args)
        elif cmd in ("nexus-config", "nexusconfig"):
            nc_sub = args[0] if args else ""
            nc_args = args[1:] if len(args) > 1 else []
            result_embed, error_msg = await cmd_nexus_config(conn, nc_sub, nc_args)
        else:
            error_msg = f"Unknown command: `!{cmd}`. Use `!help` for commands."
            log.debug("on_message: unknown command !%s", cmd)

        if error_msg:
            log.warning("on_message: command !%s returned error: %s", cmd, error_msg)
            await message.reply(embed=discord.Embed(title="Error", color=0xE74C3C, description=_truncate(error_msg, EMBED_DESC_MAX)))
        elif result_embed is not None:
            log.debug("on_message: command !%s success, sending embed reply", cmd)
            await message.reply(embed=result_embed)
        else:
            log.debug("on_message: command !%s produced no embed and no error", cmd)
    except Exception as err:
        log.exception("on_message: unexpected error handling !%s: %s", cmd, err)
        await message.reply(embed=discord.Embed(title="Error", color=0xE74C3C, description=_truncate(str(err), EMBED_DESC_MAX)))
    finally:
        if conn:
            try:
                conn.close()
                log.debug("on_message: RethinkDB connection closed")
            except Exception as e:
                log.warning("on_message: error closing RethinkDB conn: %s", e)

    # Do not call bot.process_commands(message): all ! commands are handled above. process_commands would look for a discord.py @bot.command("nexus") etc. and raise CommandNotFound.


def main():
    log.info("Starting IntelliStock Discord bot (log level=%s)", logging.getLevelName(log.level))
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        log.error("DISCORD_BOT_TOKEN is not set. Set it in .env or environment.")
        sys.exit(1)
    log.debug("Token present (len=%d), starting bot.run()", len(token))
    try:
        bot.run(token)
    except discord.errors.PrivilegedIntentsRequired as e:
        log.error(
            "PrivilegedIntentsRequired: Message Content Intent not enabled. Enable it at "
            "https://discord.com/developers/applications/ → your app → Bot → Message Content Intent ON. %s",
            e,
        )
        print(
            "\n*** Message Content Intent is required but not enabled. ***\n"
            "1. Go to https://discord.com/developers/applications/\n"
            "2. Select your application → Bot\n"
            "3. Under 'Privileged Gateway Intents', turn ON 'Message Content Intent'\n"
            "4. Save and restart the bot.\n"
        )
        sys.exit(1)
    except Exception as e:
        log.exception("Bot run failed: %s", e)
        raise


if __name__ == "__main__":
    main()
