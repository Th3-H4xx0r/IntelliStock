#!/usr/bin/env python
"""
IntelliStock CLI - Control and inspect the backend server via RethinkDB.
Run from project root: python backend/cli.py
Or from backend: python cli.py
"""

import json
import os
import sys
import time

# Ensure backend dir is on path and cwd so imports and RethinkDB access work
_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
os.chdir(_backend_dir)

from rethinkdb import RethinkDB
from rethinkdb.errors import ReqlDriverError
from intellistock_logger import intellistock_logger

from interactive_utils import (
    DB_NAME,
    RETHINKDB_HOST,
    RETHINKDB_PORT,
    get_conn,
    ensure_live_prices_stocks_table,
    ensure_instances_table,
    ensure_strategies_table,
    ensure_backtest_instances_table,
    next_strategy_id,
    parse_granularity_to_seconds,
    insert_backtest_with_unique_id,
    round_trip_stats,
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
    action_list_brokerages,
    action_link_alpaca,
    action_link_robinhood_tokens,
    action_fetch_robinhood_accounts,
    action_delete_brokerage,
    action_refresh_robinhood,
    _normalize_nexus_phase_value,
    _nexus_phase_selector_from_value,
)

r = RethinkDB()
RETHINKDB_PRESET_REMOTE = os.environ.get('RETHINKDB_REMOTE_HOST', 'localhost')  # preset for 'r' option, override via env


def cmd_help():
    """Show available commands."""
    intellistock_logger.log("\n--- IntelliStock CLI ---", "cyan", service="CLI")
    intellistock_logger.log("  help, h              Show this help", "white", service="CLI")
    intellistock_logger.log("  status, s            Show service/config status", "white", service="CLI")
    intellistock_logger.log("  tickers, t           List tickers price broker is streaming", "white", service="CLI")
    intellistock_logger.log("  add-ticker SYM [..]  Add ticker(s) to LivePricesStocks (price broker will stream them)", "white", service="CLI")
    intellistock_logger.log("  remove-ticker SYM    Remove a ticker from LivePricesStocks", "white", service="CLI")
    intellistock_logger.log("  prices, p            Show current prices from LivePrices", "white", service="CLI")
    intellistock_logger.log("  history [SYM] [N]   Show price history (timestamped); SYM optional, N=limit (default 30)", "white", service="CLI")
    intellistock_logger.log("  instances, i         List instances (broker threads)", "white", service="CLI")
    intellistock_logger.log("  create-instance ID [KEY [SECRET [GRANULARITY]]]  Create instance; GRANULARITY=e.g. 60, 1m, 1h (or prompted)", "white", service="CLI")
    intellistock_logger.log("  delete-instance ID   Delete an instance (shows associated strategies, asks confirmation)", "white", service="CLI")
    intellistock_logger.log("  add-stock INSTANCE_ID SYMBOL       Add ticker to instance.stocks (broker relaunches with new list)", "white", service="CLI")
    intellistock_logger.log("  remove-stock INSTANCE_ID SYMBOL    Remove ticker from instance.stocks", "white", service="CLI")
    intellistock_logger.log("  start-instance ID    Set runCommand=True (server will spawn instance.py for this ID)", "white", service="CLI")
    intellistock_logger.log("  stop-instance ID     Set runCommand=False (instance process will exit)", "white", service="CLI")
    intellistock_logger.log("  terminate-price      Request price service to stop (no restart)", "white", service="CLI")
    intellistock_logger.log("  terminate-discover   Request discover service to stop (no restart)", "white", service="CLI")
    intellistock_logger.log("  start-broker         Set runPriceService=True (price service starts broker)", "white", service="CLI")
    intellistock_logger.log("  strategies, st       List strategies (from Strategies table)", "white", service="CLI")
    intellistock_logger.log("  create-strategy      Interactive: create a standalone strategy (assign to instance via strategy_id)", "white", service="CLI")
    intellistock_logger.log("  edit-strategy STRATEGY_ID     Edit strategy by ID; prints current strategy then prompts for new values (like create-strategy)", "white", service="CLI")
    intellistock_logger.log("  delete-strategy STRATEGY_ID  Delete a standalone strategy by ID", "white", service="CLI")
    intellistock_logger.log("  link-strategy INSTANCE_ID STRATEGY_ID  Link a strategy to an instance (sets instance.strategy_id)", "white", service="CLI")
    intellistock_logger.log("  backtests, bt        List queued/running backtests (BacktestInstances)", "white", service="CLI")
    intellistock_logger.log("  create-backtest      Interactive: queue a backtest (instance, stocks, dates, key/secret)", "white", service="CLI")
    intellistock_logger.log("  delete-backtest ID   Remove a backtest from queue by id", "white", service="CLI")
    intellistock_logger.log("  stop-backtest ID     Set run=false for a running backtest (broker will exit and row is removed)", "white", service="CLI")
    intellistock_logger.log("  stop-all-backtests   Stop all running backtests and clear the queue", "white", service="CLI")
    intellistock_logger.log("  pause-backtest ID    Pause a running backtest (broker waits until resume)", "white", service="CLI")
    intellistock_logger.log("  resume-backtest ID   Resume a paused backtest", "white", service="CLI")
    intellistock_logger.log("  summarize-backtest ID  Show detailed backtest stats (P&L, per-stock, trades, round-trips)", "white", service="CLI")
    intellistock_logger.log("  backtest-logs ID     Show last 500 log lines for a backtest run (saved in BacktestResults)", "white", service="CLI")
    intellistock_logger.log("  graph-backtest ID    Load backtest result from DB and plot (portfolio + price + trades)", "white", service="CLI")
    intellistock_logger.log("  agent start [request] Start AI backtesting agent; optional special request for strategy generation", "white", service="CLI")
    intellistock_logger.log("  agent stop           Stop AI backtesting agent", "white", service="CLI")
    intellistock_logger.log("  agent pause          Pause agent (and its current backtest)", "white", service="CLI")
    intellistock_logger.log("  agent resume         Resume paused agent (and its current backtest)", "white", service="CLI")
    intellistock_logger.log("  digest start         Start daily digest engine (6am/6pm UTC briefs to #briefs)", "white", service="CLI")
    intellistock_logger.log("  digest stop          Stop daily digest engine", "white", service="CLI")
    intellistock_logger.log("  digest send-now      Trigger digest engine to send a brief right now", "white", service="CLI")
    intellistock_logger.log("  digest status        Show digest engine status", "white", service="CLI")
    intellistock_logger.log("  nexus start [--start=N] [--end=M]  Start Nexus; selectors accept execution-order 1-14 or labels like 2b / 6b", "white", service="CLI")
    intellistock_logger.log("  nexus stop           Stop Graph Nexus service (saves progress and exits)", "white", service="CLI")
    intellistock_logger.log("  nexus status         Show Graph Nexus status (control, build phase, scraper progress)", "white", service="CLI")
    intellistock_logger.log("  nexus rebuild        Reset graph and cache for full rebuild from phase 1 (prompts y/n)", "white", service="CLI")
    intellistock_logger.log("  trends [INSTANCE]    Show active market trends (from nexus trend tracking)", "white", service="CLI")
    intellistock_logger.log("  trends end ID        End an active trend by ID", "white", service="CLI")
    intellistock_logger.log("  trends delete ID     Delete a trend by ID", "white", service="CLI")
    intellistock_logger.log("  discovered [INST]    Show nexus-discovered stocks", "white", service="CLI")
    intellistock_logger.log("  discovered remove INST TICKER  Remove a discovered stock", "white", service="CLI")
    intellistock_logger.log("  nexus-config INST    Show nexus trend/discovery config for instance", "white", service="CLI")
    intellistock_logger.log("  nexus-config INST KEY VALUE  Set a nexus config key", "white", service="CLI")
    intellistock_logger.log("  agent resume-timer    Schedule agent to resume after specified time (interactive)", "white", service="CLI")
    intellistock_logger.log("  agent status         Show agent status (running, paused, count_today, resume_at)", "white", service="CLI")
    intellistock_logger.log("  agent best           Show best strategy (tag Best) with details and settings", "white", service="CLI")
    intellistock_logger.log("  brokerages, bk       List linked brokerage accounts", "white", service="CLI")
    intellistock_logger.log("  link-brokerage       Interactive: link a new brokerage account (Alpaca or Robinhood)", "white", service="CLI")
    intellistock_logger.log("  unlink-brokerage ID  Remove a linked brokerage account by ID", "white", service="CLI")
    intellistock_logger.log("  exit, quit, q        Exit CLI", "white", service="CLI")
    intellistock_logger.log("------------------------\n", "cyan", service="CLI")


def cmd_status(conn):
    """Show engines/services status table and Config."""
    try:
        out = action_status(conn)
        engines = out.get("engines") or []
        config = out.get("config")
        if out.get("error"):
            intellistock_logger.log("  " + out["error"], "yellow", service="CLI")

        # Engines table (pretty print)
        intellistock_logger.log("\n--- Services & Engines ---", "cyan", service="CLI")
        if not engines:
            intellistock_logger.log("  No engine status (EngineControl missing or empty).", "yellow", service="CLI")
        else:
            table_rows = []
            for row in engines:
                name = row.get("name", row.get("id", "?"))
                status = row.get("status", "?")
                details = (row.get("details") or "").strip()
                if len(details) > 48:
                    details = details[:45] + "..."
                table_rows.append({"name": name, "status": status, "details": details or "—"})
            w_name = max(4, min(24, max(len(r["name"]) for r in table_rows)))
            w_status = max(6, max(len(r["status"]) for r in table_rows))
            w_details = max(7, min(50, max(len(r["details"]) for r in table_rows)))

            def sep():
                return "+" + "-" * (w_name + 2) + "+" + "-" * (w_status + 2) + "+" + "-" * (w_details + 2) + "+"

            def status_color(s):
                if s == "Running":
                    return "green"
                if s in ("Stopped", "Stopped (terminate)"):
                    return "red"
                if s == "Paused":
                    return "yellow"
                return "white"

            def line(d):
                name_cell = (d["name"][:w_name] if len(d["name"]) > w_name else d["name"]).ljust(w_name)
                det_cell = (d["details"][:w_details] if len(d["details"]) > w_details else d["details"]).ljust(w_details)
                return "| " + name_cell + " | " + d["status"].ljust(w_status) + " | " + det_cell + " |"

            intellistock_logger.log(sep(), "cyan", service="CLI")
            intellistock_logger.log(line({"name": "Engine", "status": "Status", "details": "Details"}), "cyan", service="CLI")
            intellistock_logger.log(sep(), "cyan", service="CLI")
            for tr in table_rows:
                color = status_color(tr["status"])
                intellistock_logger.log(line(tr), color, service="CLI")
            intellistock_logger.log(sep(), "cyan", service="CLI")

        # Config section (compact)
        intellistock_logger.log("\n--- Config (flags) ---", "cyan", service="CLI")
        if config is None:
            intellistock_logger.log("  No Config document.", "yellow", service="CLI")
        else:
            for key, val in config.items():
                intellistock_logger.log(f"  {key}: {val}", "white", service="CLI")
        intellistock_logger.log("", service="CLI")
    except ReqlDriverError as e:
        if "Connection is closed" in str(e):
            raise
        intellistock_logger.log(f"  Error: {e}", "red", service="CLI")
    except Exception as e:
        intellistock_logger.log(f"  Error: {e}", "red", service="CLI")


def cmd_tickers(conn):
    """List tickers that the price broker is getting prices for (from LivePricesStocks)."""
    intellistock_logger.log("\n--- Tickers (price broker stream) ---", "cyan", service="CLI")
    try:
        out = action_tickers(conn)
        tickers = out.get("tickers", [])
        count = out.get("count", 0)
        if not tickers:
            intellistock_logger.log("  No tickers in LivePricesStocks.", "yellow", service="CLI")
            return
        intellistock_logger.log(f"  Count: {count}", "white", service="CLI")
        intellistock_logger.log("  " + ", ".join(tickers[:50]), "green", service="CLI")
        if count > 50:
            intellistock_logger.log(f"  ... and {count - 50} more", "white", service="CLI")
        intellistock_logger.log("", service="CLI")
    except ReqlDriverError as e:
        if "Connection is closed" in str(e):
            raise
        intellistock_logger.log(f"  Error: {e}", "red", service="CLI")
    except Exception as e:
        intellistock_logger.log(f"  Error: {e}", "red", service="CLI")


def cmd_add_ticker(conn, symbols):
    """Add ticker(s) to LivePricesStocks. Price service / price broker use this table for streaming."""
    if not symbols:
        intellistock_logger.log("Usage: add-ticker SYMBOL [SYMBOL ...]", "yellow", service="CLI")
        return
    try:
        out = action_add_ticker(conn, symbols)
        added = out.get("added", [])
        if added:
            intellistock_logger.log(f"Added ticker(s): {', '.join(added)}", "green", service="CLI")
            intellistock_logger.log("Restart the price broker (start-broker) to pick up new tickers.", "yellow", service="CLI")
        else:
            intellistock_logger.log("No valid symbols to add.", "yellow", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except ReqlDriverError as e:
        if "Connection is closed" in str(e):
            raise
        intellistock_logger.log(f"Error: {e}", "red", service="CLI")
    except Exception as e:
        intellistock_logger.log(f"Error: {e}", "red", service="CLI")


def cmd_remove_ticker(conn, symbol):
    """Remove a ticker from LivePricesStocks."""
    if not symbol:
        intellistock_logger.log("Usage: remove-ticker SYMBOL", "yellow", service="CLI")
        return
    try:
        out = action_remove_ticker(conn, symbol)
        if out.get("removed"):
            intellistock_logger.log(f"Removed ticker: {out.get('ticker', symbol)}", "green", service="CLI")
            intellistock_logger.log("Restart the price broker (start-broker) to apply.", "yellow", service="CLI")
        else:
            intellistock_logger.log(f"Ticker not found: {out.get('ticker', symbol)}", "yellow", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except ReqlDriverError as e:
        if "Connection is closed" in str(e):
            raise
        intellistock_logger.log(f"Error: {e}", "red", service="CLI")
    except Exception as e:
        intellistock_logger.log(f"Error: {e}", "red", service="CLI")


def cmd_prices(conn):
    """Show current prices from LivePrices (last known per ticker)."""
    intellistock_logger.log("\n--- Current prices (LivePrices) ---", "cyan", service="CLI")
    try:
        out = action_prices(conn)
        prices = out.get("prices", [])
        if not prices:
            intellistock_logger.log("  No prices in LivePrices.", "yellow", service="CLI")
            return
        intellistock_logger.log(f"  Tickers with price: {len(prices)}", "white", service="CLI")
        for item in prices[:30]:
            intellistock_logger.log(f"    {item.get('ticker', '?')}: {item.get('price', '?')}", "green", service="CLI")
        if len(prices) > 30:
            intellistock_logger.log(f"  ... and {len(prices) - 30} more", "white", service="CLI")
        intellistock_logger.log("", service="CLI")
    except ReqlDriverError as e:
        if "Connection is closed" in str(e):
            raise
        intellistock_logger.log(f"  Error: {e}", "red", service="CLI")
    except Exception as e:
        intellistock_logger.log(f"  Error: {e}", "red", service="CLI")


def cmd_history(conn, ticker=None, limit=30):
    """Show price history from PriceHistory (timestamped); optional ticker filter and limit."""
    intellistock_logger.log("\n--- Price history (PriceHistory) ---", "cyan", service="CLI")
    try:
        out = action_history(conn, ticker=ticker, limit=limit)
        history = out.get("history", [])
        if not history:
            intellistock_logger.log("  No price history found.", "yellow", service="CLI")
            return
        intellistock_logger.log(f"  Showing {len(history)} most recent (ticker={ticker or 'all'}, limit={limit})", "white", service="CLI")
        for row in history[:50]:
            intellistock_logger.log(f"    {row.get('timestamp', '?')}  {row.get('ticker', '?')}  {row.get('price', '?')}  ({row.get('type', '')})", "green", service="CLI")
        if len(history) > 50:
            intellistock_logger.log(f"  ... and {len(history) - 50} more", "white", service="CLI")
        intellistock_logger.log("", service="CLI")
    except ReqlDriverError as e:
        if "Connection is closed" in str(e):
            raise
        intellistock_logger.log(f"  Error: {e}", "red", service="CLI")
    except Exception as e:
        intellistock_logger.log(f"  Error: {e}", "red", service="CLI")


def _instance_sort_key(row):
    """Sort by id: numeric ids in ascending order, then string ids."""
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


def cmd_instances(conn):
    """List instances (broker instances from Instances table) in a table, sorted by id ascending."""
    intellistock_logger.log("\n--- Instances ---", "cyan", service="CLI")
    try:
        out = action_instances(conn)
        instances = out.get("instances", [])
        if not instances:
            intellistock_logger.log("  No instances.", "yellow", service="CLI")
            return
        instances = sorted(instances, key=_instance_sort_key)
        table_rows = []
        for row in instances:
            iid = str(row.get("id", "?"))
            name = (row.get("name") or "").strip() or "-"
            run_cmd = row.get("runCommand", False)
            status = "Running" if run_cmd else "Stopped"
            strategy_id = row.get("strategy_id")
            strat_str = str(strategy_id) if strategy_id is not None else "-"
            stocks = row.get("stocks") or []
            stocks_str = ", ".join(str(s) for s in stocks[:8])
            if len(stocks) > 8:
                stocks_str += " (+%d)" % (len(stocks) - 8)
            if not stocks_str:
                stocks_str = "(none)"
            table_rows.append({
                "id": iid,
                "name": name,
                "status": status,
                "strategy_id": strat_str,
                "stocks": stocks_str,
            })
        w_id = max(4, max(len(r["id"]) for r in table_rows))
        w_name = max(6, min(20, max(len(r["name"]) for r in table_rows)))
        w_status = max(6, max(len(r["status"]) for r in table_rows))
        w_strat = max(10, max(len(r["strategy_id"]) for r in table_rows))
        w_stocks = min(40, max(7, max(len(r["stocks"]) for r in table_rows)))

        def sep():
            return "+" + "-" * (w_id + 2) + "+" + "-" * (w_name + 2) + "+" + "-" * (w_status + 2) + "+" + "-" * (w_strat + 2) + "+" + "-" * (w_stocks + 2) + "+"

        def line(d):
            name_cell = (d["name"][:w_name] if len(d["name"]) > w_name else d["name"]).ljust(w_name)
            stocks_cell = (d["stocks"][:w_stocks] if len(d["stocks"]) > w_stocks else d["stocks"]).ljust(w_stocks)
            return (
                "| " + d["id"].ljust(w_id) + " |"
                + " " + name_cell + " |"
                + " " + d["status"].ljust(w_status) + " |"
                + " " + d["strategy_id"].ljust(w_strat) + " |"
                + " " + stocks_cell + " |"
            )

        header = {"id": "ID", "name": "Name", "status": "Status", "strategy_id": "Strategy", "stocks": "Stocks"}
        intellistock_logger.log(sep(), "cyan", service="CLI")
        intellistock_logger.log(line(header), "cyan", service="CLI")
        intellistock_logger.log(sep(), "cyan", service="CLI")
        for tr in table_rows:
            intellistock_logger.log(line(tr), "white", service="CLI")
        intellistock_logger.log(sep(), "cyan", service="CLI")
        intellistock_logger.log("", service="CLI")
    except ReqlDriverError as e:
        if "Connection is closed" in str(e):
            raise
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_strategies(conn, instance_filter=None):
    """List strategies from Strategies table."""
    intellistock_logger.log("\n--- Strategies ---", "cyan", service="CLI")
    try:
        out = action_strategies(conn)
        strategies = out.get("strategies", [])
        if not strategies:
            intellistock_logger.log("  No strategies found.", "yellow", service="CLI")
            return
        for row in strategies:
            row_id = row.get("id", "?")
            strategy_name = row.get("name", "id=%s" % row_id)
            strategies_array = row.get("strategies", [])
            inst_ids = row.get("instances_using", [])
            inst_display = " (used by instances: %s)" % ", ".join(inst_ids) if inst_ids else " (not assigned)"
            intellistock_logger.log("  id=%s  '%s'%s  (%d sub-strategies)" % (row_id, strategy_name, inst_display, len(strategies_array)), "cyan", service="CLI")
            for idx, strat in enumerate(strategies_array):
                if isinstance(strat, dict):
                    intellistock_logger.log("    [%d] type=%s  weight=%s  execution_position=%s  phase=%s" % (idx, strat.get("strategy", "?"), strat.get("weight", "?"), strat.get("execution_position", "?"), strat.get("decision_phase", "pre")), "green", service="CLI")
        intellistock_logger.log("", service="CLI")
    except ReqlDriverError as e:
        if "Connection is closed" in str(e):
            raise
        intellistock_logger.log(f"  Error: {e}", "red", service="CLI")
    except Exception as e:
        intellistock_logger.log(f"  Error: {e}", "red", service="CLI")


def _parse_strategy_definitions(args):
    """Parse command-line arguments into list of strategy definitions.
    Format: STRATEGY WEIGHT POS [CONDITIONS_JSON] STRATEGY WEIGHT POS [CONDITIONS_JSON] ...
    Returns: list of dicts with 'strategy', 'weight', 'execution_position', 'conditions'
    """
    strategies = []
    i = 0
    while i < len(args):
        if i + 3 > len(args):
            break  # Need at least 3 args: strategy, weight, pos
        strategy_name = args[i].strip()
        try:
            weight = float(args[i + 1])
            execution_position = int(args[i + 2])
        except (ValueError, TypeError):
            break  # Invalid weight or pos, stop parsing
        # Check if next arg is JSON (conditions)
        conditions = {}
        i += 3
        if i < len(args):
            # Try to parse as JSON - if it starts with {, try to parse it
            potential_json = args[i]
            if potential_json.strip().startswith('{'):
                try:
                    # First try parsing just this arg
                    conditions = json.loads(potential_json.strip())
                    i += 1  # Consumed the JSON arg
                except json.JSONDecodeError:
                    # JSON might be split across multiple args - join and find complete JSON object
                    remaining = ' '.join(args[i:])
                    brace_count = 0
                    json_end = -1
                    for j, char in enumerate(remaining):
                        if char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                json_end = j + 1
                                break
                    if json_end > 0:
                        try:
                            conditions = json.loads(remaining[:json_end].strip())
                            # Calculate how many args were consumed by finding which args, when joined, match the JSON length
                            consumed_count = 1
                            for j in range(i + 1, len(args) + 1):
                                test_join = ' '.join(args[i:j])
                                if len(test_join) >= json_end:
                                    # Check if this joined string contains the complete JSON
                                    if test_join[:json_end].strip().startswith('{') and test_join[:json_end].count('{') == test_join[:json_end].count('}'):
                                        consumed_count = j - i
                                        break
                            i += consumed_count
                        except json.JSONDecodeError:
                            # Invalid JSON, treat as no conditions and continue
                            pass
        strategies.append({
            'strategy': strategy_name,
            'weight': weight,
            'execution_position': execution_position,
            'conditions': conditions,
        })
    return strategies


def _prompt_strategy_type_and_config(strategy_type, config, defaults_config, conditions=None):
    """Fill config dict for given strategy_type using interactive prompts. defaults_config can pre-fill values (Enter to keep).
    conditions: optional dict to also fill with strategy-level conditions. Earnings uses config for alpaca_key/alpaca_secret."""
    def _normalize_llm_provider(provider):
        name = (provider or 'gemini').strip().lower()
        return name if name in ('gemini', 'deepseek', 'openai', 'azure', 'nvidia', 'claude-cli', 'anthropic') else 'gemini'

    def _default_llm_model(provider, gemini_model='gemini-3-flash-preview', deepseek_model='deepseek-chat'):
        provider = _normalize_llm_provider(provider)
        if provider == 'deepseek':
            return deepseek_model
        if provider == 'openai':
            return 'gpt-4.1-mini'
        if provider == 'azure':
            return os.environ.get('AZURE_OPENAI_DEPLOYMENT', '').strip() or os.environ.get('AZURE_OPENAI_MODEL', '').strip() or 'gpt-4.1-mini'
        if provider == 'nvidia':
            return 'nvidia/nemotron-3-super-120b-a12b'
        if provider in ('claude-cli', 'anthropic'):
            return 'claude-sonnet-4-6'
        return gemini_model

    def _provider_key_env(provider):
        provider = _normalize_llm_provider(provider)
        if provider == 'deepseek':
            return 'DEEPSEEK_API_KEY'
        if provider == 'openai':
            return 'OPENAI_API_KEY'
        if provider == 'azure':
            return 'AZURE_OPENAI_API_KEY'
        if provider == 'nvidia':
            return 'NVIDIA_API_KEY'
        if provider == 'anthropic':
            return 'ANTHROPIC_API_KEY'
        if provider == 'claude-cli':
            # claude-cli authenticates via the local binary, not an env-var
            # API key. Return a placeholder name to keep the function's
            # type consistent; callers should never actually read this.
            return ''
        return 'GEMINI_API_KEY'

    def _prompt_llm_provider_settings(
        cfg,
        defaults,
        *,
        model_key='llm_model',
        prompt_label='LLM',
        gemini_model='gemini-3-flash-preview',
        deepseek_model='deepseek-chat',
    ):
        cur_provider = _normalize_llm_provider(defaults.get('llm_provider') or cfg.get('llm_provider') or 'gemini')
        provider_input = input(
            f"    {prompt_label} provider (gemini, deepseek, openai, azure, nvidia, default {cur_provider}): "
        ).strip().lower()
        cfg['llm_provider'] = _normalize_llm_provider(provider_input or cur_provider)
        cur_model = defaults.get(model_key) or defaults.get('llm_model') or _default_llm_model(
            cfg['llm_provider'],
            gemini_model=gemini_model,
            deepseek_model=deepseek_model,
        )
        model_input = input(f"    {prompt_label} model (default {cur_model}): ").strip()
        cfg[model_key] = model_input if model_input else cur_model
        key_env = _provider_key_env(cfg.get('llm_provider'))
        llm_key_input = input(f"    {prompt_label} API key (Enter to use {key_env} env): ").strip()
        if llm_key_input:
            cfg['llm_api_key'] = llm_key_input
            if cfg.get('llm_provider') == 'azure':
                cfg['azure_openai_api_key'] = llm_key_input
        elif defaults.get('azure_openai_api_key') and cfg.get('llm_provider') == 'azure':
            cfg['azure_openai_api_key'] = defaults['azure_openai_api_key']
            cfg.setdefault('llm_api_key', defaults['azure_openai_api_key'])
        elif defaults.get('llm_api_key'):
            cfg['llm_api_key'] = defaults['llm_api_key']
        if cfg.get('llm_provider') == 'azure':
            cur_endpoint = defaults.get('azure_openai_endpoint') or ''
            endpoint_input = input(
                f"    Azure OpenAI endpoint (Enter to use AZURE_OPENAI_ENDPOINT env{', current ' + cur_endpoint if cur_endpoint else ''}): "
            ).strip()
            if endpoint_input:
                cfg['azure_openai_endpoint'] = endpoint_input
            elif cur_endpoint:
                cfg['azure_openai_endpoint'] = cur_endpoint
            cur_api_version = defaults.get('azure_openai_api_version') or '2024-10-21'
            version_input = input(f"    Azure OpenAI API version (default {cur_api_version}): ").strip()
            cfg['azure_openai_api_version'] = version_input if version_input else cur_api_version
        elif cfg.get('llm_provider') == 'openai':
            cur_base_url = defaults.get('openai_base_url') or ''
            base_url_input = input(
                f"    OpenAI base URL (optional; Enter to use OPENAI_BASE_URL env{', current ' + cur_base_url if cur_base_url else ''}): "
            ).strip()
            if base_url_input:
                cfg['openai_base_url'] = base_url_input
            elif cur_base_url:
                cfg['openai_base_url'] = cur_base_url
        elif cfg.get('llm_provider') == 'nvidia':
            cur_base_url = defaults.get('nvidia_base_url') or 'https://integrate.api.nvidia.com/v1'
            base_url_input = input(
                f"    NVIDIA NIM base URL (default {cur_base_url}): "
            ).strip()
            cfg['nvidia_base_url'] = base_url_input if base_url_input else cur_base_url
            cur_effort = defaults.get('llm_reasoning_effort') or 'medium'
            effort_input = input(
                f"    NVIDIA reasoning effort (none, low, medium, high, default {cur_effort}): "
            ).strip().lower()
            cfg['llm_reasoning_effort'] = effort_input if effort_input in ('none', 'low', 'medium', 'high') else cur_effort

    if strategy_type.lower() == 'risk_tolerance':
        intellistock_logger.log("  Risk Tolerance Strategy - Set parameters:", "cyan", service="CLI")
        cur = defaults_config.get('max_loss_percent')
        max_loss_input = input("    Max loss (%, e.g. 5.0, Enter to skip" + (f", current {cur}" if cur is not None else "") + "): ").strip()
        if max_loss_input:
            try:
                config['max_loss_percent'] = float(max_loss_input)
            except ValueError:
                pass
        elif cur is not None:
            config['max_loss_percent'] = cur
        cur = defaults_config.get('take_profit_percent')
        take_profit_input = input("    Take profit (%, e.g. 10.0, Enter to skip" + (f", current {cur}" if cur is not None else "") + "): ").strip()
        if take_profit_input:
            try:
                config['take_profit_percent'] = float(take_profit_input)
            except ValueError:
                pass
        elif cur is not None:
            config['take_profit_percent'] = cur
        if not config:
            intellistock_logger.log("    Warning: No max_loss_percent or take_profit_percent set.", "yellow", service="CLI")
    elif strategy_type.lower() == 'tiered_risk':
        intellistock_logger.log("  Tiered Risk Strategy - Set parameters (press Enter for each to use default):", "cyan", service="CLI")
        _tr_defaults = {
            'atr_multiplier': 2.0, 'hard_stop_pct': 8.0, 'target1_multiplier': 2.0, 'target1_sell_pct': 50.0,
            'stop_lock_pct': 5.0, 'max_days_no_profit': 10, 'min_profit_threshold_pct': 3.0,
            'trailing_ma_period': 20, 'consecutive_closes': 2, 'rsi_threshold': 80.0,
            'distance_from_200sma_pct': 70.0, 'volume_decline_ratio': 80.0, 'daily_loss_limit_pct': 6.0,
            'blacklist_profit_threshold_pct': 40.0, 'blacklist_days': 30,
        }
        for key, default in _tr_defaults.items():
            cur = defaults_config.get(key) if defaults_config else None
            try:
                prompt = f"    {key} (default {cur if cur is not None else default}): "
                val_input = input(prompt).strip()
                if val_input:
                    config[key] = int(float(val_input)) if key in ['max_days_no_profit', 'trailing_ma_period', 'consecutive_closes', 'blacklist_days'] else float(val_input)
                else:
                    config[key] = cur if cur is not None else default
            except (ValueError, TypeError):
                config[key] = cur if cur is not None else default
        intellistock_logger.log("    Using defaults for any invalid or skipped values.", "white", service="CLI")
    elif strategy_type.lower() == 'position_sizing':
        intellistock_logger.log("  Position Sizing (Fixed %% Risk) - Set parameters (press Enter for default):", "cyan", service="CLI")
        cur_r = (defaults_config or {}).get('risk_pct', 2.0)
        cur_s = (defaults_config or {}).get('stop_loss_pct', 5.0)
        risk_input = input(f"    Risk %% of account per trade (default {cur_r}): ").strip()
        config['risk_pct'] = float(risk_input) if risk_input else cur_r
        stop_input = input(f"    Stop loss %% below entry (default {cur_s}): ").strip()
        config['stop_loss_pct'] = float(stop_input) if stop_input else cur_s
        intellistock_logger.log("    Position sizing does not vote; broker calls it after each buy/sell decision.", "white", service="CLI")
    elif strategy_type.lower() == 'news':
        intellistock_logger.log("  The standalone News strategy has been removed. Use ml_news or graph_nexus_analysis instead.", "yellow", service="CLI")
    elif strategy_type.lower() == 'earnings':
        intellistock_logger.log("  Earnings Strategy - Set parameters (LLM required for sentiment):", "cyan", service="CLI")
        _prompt_llm_provider_settings(
            config,
            defaults_config,
            model_key='model_name',
            prompt_label='LLM',
            gemini_model='gemini-3-flash-preview',
            deepseek_model='deepseek-chat',
        )
        cur_pct = defaults_config.get('capital_pct', 0.10)
        # Accept 0-100 (e.g. 10) or 0-1 (e.g. 0.10)
        cur_display = cur_pct * 100.0 if isinstance(cur_pct, (int, float)) and 0 < cur_pct <= 1 else (cur_pct if isinstance(cur_pct, (int, float)) else 10.0)
        pct_input = input(f"    Capital %% of portfolio to set aside for earnings trades (0-100, e.g. 100 for earnings-only strategy, default {cur_display}): ").strip()
        if pct_input:
            try:
                v = float(pct_input)
                config['capital_pct'] = v / 100.0 if v > 1 else v
            except ValueError:
                config['capital_pct'] = cur_pct if isinstance(cur_pct, (int, float)) and 0 <= cur_pct <= 1 else 0.10
        else:
            config['capital_pct'] = cur_pct if isinstance(cur_pct, (int, float)) and 0 <= cur_pct <= 1 else 0.10
        config['capital_pct'] = max(0.0, min(1.0, float(config.get('capital_pct', 0.10))))
        lookahead_input = input(f"    Lookahead days for earnings (default 14): ").strip()
        if lookahead_input:
            try:
                config['lookahead_days'] = int(lookahead_input)
            except ValueError:
                config['lookahead_days'] = 14
        else:
            config['lookahead_days'] = int(defaults_config.get('lookahead_days', 14))
        cur_max = int(defaults_config.get('max_earnings_stocks', 50))
        max_input = input(f"    Max number of earnings stocks to consider (ranked by importance, default {cur_max}): ").strip()
        if max_input:
            try:
                config['max_earnings_stocks'] = max(1, int(max_input))
            except ValueError:
                config['max_earnings_stocks'] = cur_max
        else:
            config['max_earnings_stocks'] = cur_max
        if not config.get('llm_api_key') and not config.get('azure_openai_api_key'):
            intellistock_logger.log("    Warning: No LLM API key set. Strategy will skip sentiment unless a provider-specific env key is set.", "yellow", service="CLI")
        # Alpaca key/secret for news fetching (stored in config)
        cur_alpaca_key = (defaults_config or {}).get('alpaca_key', '')
        alpaca_key_in = input(f"    Alpaca API key (for news, leave blank to use KEY env{', currently set' if cur_alpaca_key else ''}): ").strip()
        if alpaca_key_in:
            config['alpaca_key'] = alpaca_key_in
        elif cur_alpaca_key:
            config['alpaca_key'] = cur_alpaca_key
        cur_alpaca_secret = (defaults_config or {}).get('alpaca_secret', '')
        alpaca_secret_in = input(f"    Alpaca API secret (for news, leave blank to use SECRET env{', currently set' if cur_alpaca_secret else ''}): ").strip()
        if alpaca_secret_in:
            config['alpaca_secret'] = alpaca_secret_in
        elif cur_alpaca_secret:
            config['alpaca_secret'] = cur_alpaca_secret
        intellistock_logger.log("    Earnings runs once per pass; uses LLM for sentiment and future cache for scheduled trades. 100%% is valid for an earnings-only strategy.", "white", service="CLI")
    elif strategy_type.lower() == 'ml_news':
        intellistock_logger.log("  ML News Pipeline (run_once) - FinBERT + LLM news scoring:", "cyan", service="CLI")
        # enable_llm
        cur_llm = defaults_config.get('enable_llm', True)
        llm_input = input(f"    Enable LLM classification for high-impact articles (true/false) ({cur_llm}): ").strip().lower()
        config['enable_llm'] = llm_input in ('true', '1', 'yes') if llm_input else cur_llm
        # LLM provider
        _prompt_llm_provider_settings(
            config,
            defaults_config,
            model_key='model_name',
            prompt_label='LLM',
            gemini_model='gemini-3-flash-preview',
            deepseek_model='deepseek-chat',
        )
        # Alpaca keys
        cur_alpaca_key = defaults_config.get('alpaca_key', '')
        alpaca_key_in = input(f"    Alpaca API key (Enter to use KEY env{', currently set' if cur_alpaca_key else ''}): ").strip()
        if alpaca_key_in:
            config['alpaca_key'] = alpaca_key_in
        elif cur_alpaca_key:
            config['alpaca_key'] = cur_alpaca_key
        cur_alpaca_secret = defaults_config.get('alpaca_secret', '')
        alpaca_secret_in = input(f"    Alpaca API secret (Enter to use SECRET env{', currently set' if cur_alpaca_secret else ''}): ").strip()
        if alpaca_secret_in:
            config['alpaca_secret'] = alpaca_secret_in
        elif cur_alpaca_secret:
            config['alpaca_secret'] = cur_alpaca_secret
        # Thresholds
        _ml_defaults = {
            'LLM_IMPULSE_TRIGGER': ('Min |sentiment_impulse| to trigger LLM', 0.55),
            'SENTIMENT_AVG_LONG': ('Sentiment avg threshold for BUY (-1 to 1)', 0.35),
            'SENTIMENT_AVG_SHORT': ('Sentiment avg threshold for SELL (-1 to 1)', -0.35),
            'MIN_LLM_RELEVANCE': ('Min LLM relevance_score (1-5)', 3),
            'MIN_LLM_STRENGTH': ('Min LLM impact_strength (1-5)', 3),
            'MIN_ARTICLES_FOR_SIGNAL': ('Min articles required for BUY/SELL signal', 2),
            'MAX_HOLD_DAYS_DEFAULT': ('Default max hold days for risk manager', 7),
            'max_articles_per_symbol': ('Max articles per symbol', 10),
            'news_lookback_hours': ('News lookback hours', 24),
        }
        for key, (label, default) in _ml_defaults.items():
            cur = defaults_config.get(key, default)
            val_input = input(f"    {label} ({cur}): ").strip()
            if val_input:
                try:
                    config[key] = int(val_input) if isinstance(default, int) else float(val_input)
                except ValueError:
                    config[key] = cur
            else:
                config[key] = cur
        intellistock_logger.log("    ML News runs once per pass: Alpaca ingest -> FinBERT score -> selective LLM classify -> aggregate -> vote.", "white", service="CLI")
    elif strategy_type.lower() == 'risk_manager':
        intellistock_logger.log("  Risk Manager - Stop-loss, take-profit, and max-hold exits:", "cyan", service="CLI")
        _rm_defaults = {
            'stop_atr_mult': ('ATR multiplier for stop-loss', 2.0),
            'take_profit_atr_mult': ('ATR multiplier for take-profit', 3.0),
            'max_hold_days': ('Max days to hold a position', 7),
            'fallback_stop_pct': ('Fallback stop-loss %% when ATR unavailable (decimal)', 0.08),
            'trail_atr_mult': ('ATR multiplier for trailing stop', 1.5),
            'trail_activation_atr': ('ATR gain to activate trailing stop', 1.0),
        }
        for key, (label, default) in _rm_defaults.items():
            cur = defaults_config.get(key, default)
            val_input = input(f"    {label} ({cur}): ").strip()
            if val_input:
                try:
                    config[key] = int(val_input) if isinstance(default, int) else float(val_input)
                except ValueError:
                    config[key] = cur
            else:
                config[key] = cur
        intellistock_logger.log("    Overrides weight to 1.0 on stop/take-profit/max-hold exits to force sell.", "white", service="CLI")
    elif strategy_type.lower() == 'pdt_guard':
        intellistock_logger.log("  PDT Guard - Pattern Day Trade protection:", "cyan", service="CLI")
        cur = defaults_config.get('block_same_day_exit', True)
        val_input = input(f"    Block same-day exits (true/false) ({cur}): ").strip().lower()
        config['block_same_day_exit'] = val_input in ('true', '1', 'yes') if val_input else cur
        intellistock_logger.log("    Blocks selling a position on the same day it was opened. Overrides weight to 1.0.", "white", service="CLI")
    elif strategy_type.lower() == 'exposure_limits':
        intellistock_logger.log("  Exposure Limits - Portfolio-level position constraints:", "cyan", service="CLI")
        _el_defaults = {
            'MAX_POSITION_PCT': ('Max single position as fraction of equity (decimal)', 0.20),
            'MAX_CONCURRENT_POSITIONS': ('Max number of simultaneous positions', 3),
            'CASH_BUFFER_PCT': ('Min cash to keep as fraction of equity (decimal)', 0.20),
        }
        for key, (label, default) in _el_defaults.items():
            cur = defaults_config.get(key, default)
            val_input = input(f"    {label} ({cur}): ").strip()
            if val_input:
                try:
                    config[key] = int(val_input) if isinstance(default, int) else float(val_input)
                except ValueError:
                    config[key] = cur
            else:
                config[key] = cur
        intellistock_logger.log("    Blocks buys when max positions reached or cash buffer violated. Overrides weight to 1.0.", "white", service="CLI")
    elif strategy_type.lower() == 'cooldown_manager':
        intellistock_logger.log("  Cooldown Manager - Re-entry cooldown after exit:", "cyan", service="CLI")
        cur = defaults_config.get('cooldown_days', 4)
        val_input = input(f"    Cooldown days after selling before allowing re-buy ({cur}): ").strip()
        if val_input:
            try:
                config['cooldown_days'] = int(val_input)
            except ValueError:
                config['cooldown_days'] = cur
        else:
            config['cooldown_days'] = cur
        intellistock_logger.log("    Blocks re-entry into a symbol for N days after exit to avoid churn. Overrides weight to 1.0.", "white", service="CLI")
    elif strategy_type.lower() == 'trade_allocator':
        intellistock_logger.log("  Trade Allocator (post-decision) - Position sizing:", "cyan", service="CLI")
        _ta_defaults = {
            'MAX_POSITION_PCT': ('Max single position as fraction of equity (decimal)', 0.20),
            'CASH_BUFFER_PCT': ('Min cash to keep as fraction of equity (decimal)', 0.20),
        }
        for key, (label, default) in _ta_defaults.items():
            cur = defaults_config.get(key, default)
            val_input = input(f"    {label} ({cur}): ").strip()
            if val_input:
                try:
                    config[key] = float(val_input)
                except ValueError:
                    config[key] = cur
            else:
                config[key] = cur
        intellistock_logger.log("    Runs after buy/sell decision. Computes buy_cash or sell_fraction=1.0.", "white", service="CLI")
    elif strategy_type.lower() == 'graph_nexus_analysis':
        intellistock_logger.log("  Graph Nexus Analysis (run_once) - Graph-based news contagion + Benzinga data sources:", "cyan", service="CLI")
        # ── LLM settings ───────────────────────────────────────────────────
        _prompt_llm_provider_settings(
            config,
            defaults_config,
            model_key='llm_model',
            prompt_label='LLM',
            gemini_model='gemini-3-flash-preview',
            deepseek_model='deepseek-reasoner',
        )
        # ── Per-step LLM overrides (optional) ────────────────────────────────
        intellistock_logger.log("    Per-step LLM overrides (Enter to use default LLM for each step):", "cyan", service="CLI")
        for _role, _role_label in [
            ('sentiment',       'Daily Sentiment'),
            ('company_article', 'Company Article Classification'),
            ('macro_article',   'Macro Article Classification'),
            ('event_maintenance','Event Maintenance'),
            ('overlay',         'Trade Overlay'),
        ]:
            _cur_rp = defaults_config.get(f'{_role}_llm_provider', '')
            _cur_rm = defaults_config.get(f'{_role}_llm_model', '')
            _cur_display = f"{_cur_rp}/{_cur_rm}" if _cur_rp else "default"
            _rp_input = input(f"      {_role_label} — provider (Enter=default{f', current {_cur_display}' if _cur_rp else ''}): ").strip().lower()
            if _rp_input:
                config[f'{_role}_llm_provider'] = _normalize_llm_provider(_rp_input)
                _rm_input = input(f"      {_role_label} — model: ").strip()
                if _rm_input:
                    config[f'{_role}_llm_model'] = _rm_input
                elif _cur_rm:
                    config[f'{_role}_llm_model'] = _cur_rm
            elif _cur_rp:
                config[f'{_role}_llm_provider'] = _cur_rp
                if _cur_rm:
                    config[f'{_role}_llm_model'] = _cur_rm
        # ── Benzinga API key ────────────────────────────────────────────────
        cur_bz = defaults_config.get('benzinga_api_key', '')
        bz_input = input(f"    Benzinga API key (Enter to use BENZINGA_API_KEY env{', currently set' if cur_bz else ''}): ").strip()
        if bz_input:
            config['benzinga_api_key'] = bz_input
        elif cur_bz:
            config['benzinga_api_key'] = cur_bz
        # ── Benzinga data sources (enable/disable each) ─────────────────────
        intellistock_logger.log("    Benzinga data sources (y/n for each, Enter=keep/default):", "cyan", service="CLI")
        _bz_sources = [
            ('benzinga_ratings_enabled',          'Analyst Ratings (upgrades/downgrades/initiations)',      True),
            ('benzinga_insights_enabled',          'Analyst Insights (detailed research commentary)',         True),
            ('benzinga_insider_trades_enabled',    'Insider Transactions (SEC Form 4 filings)',               True),
            ('benzinga_gov_trades_enabled',        'Government Trades (Congressional disclosures)',           True),
            ('benzinga_ma_enabled',                'Mergers & Acquisitions (announced deals)',                True),
            ('benzinga_ipo_enabled',               'Upcoming IPOs (publicly registered, look-ahead safe)',    True),
            ('benzinga_splits_enabled',            'Stock Splits (announced splits, look-ahead safe)',        True),
            ('benzinga_earnings_calendar_enabled', 'Earnings Calendar (dates+estimates; actuals stripped)',   True),
            ('benzinga_company_actions_enabled',   'Company Actions / Dividends',                             True),
            ('benzinga_prediction_markets_enabled','Prediction Markets / Bulls vs Bears',                     False),
        ]
        for key_name, label, default in _bz_sources:
            cur = defaults_config.get(key_name, default)
            cur_str = 'y' if cur else 'n'
            val = input(f"      {label} [{cur_str}]: ").strip().lower()
            if val in ('y', 'yes', '1', 'true'):
                config[key_name] = True
            elif val in ('n', 'no', '0', 'false'):
                config[key_name] = False
            else:
                config[key_name] = cur
        # ── Benzinga look-ahead (for calendar sources) ──────────────────────
        cur_la = defaults_config.get('benzinga_lookahead_days', 0)
        la_input = input(f"    Benzinga look-ahead days for calendar events (0=no lookahead/strict backtest, default {cur_la}): ").strip()
        try:
            config['benzinga_lookahead_days'] = int(la_input) if la_input else cur_la
        except ValueError:
            config['benzinga_lookahead_days'] = cur_la
        # ── Outcome tracking ────────────────────────────────────────────────
        cur_ot = defaults_config.get('outcome_tracking_enabled', True)
        ot_input = input(f"    Enable outcome tracking (track event→price outcomes for LLM context) [{'y' if cur_ot else 'n'}]: ").strip().lower()
        config['outcome_tracking_enabled'] = (cur_ot if not ot_input else ot_input in ('y', 'yes', '1', 'true'))
        # ── Learning stage ──────────────────────────────────────────────────
        cur_ls = defaults_config.get('learning_stage_enabled', True)
        ls_input = input(f"    Enable learning stage (analyze past outcomes before first trade) [{'y' if cur_ls else 'n'}]: ").strip().lower()
        config['learning_stage_enabled'] = (cur_ls if not ls_input else ls_input in ('y', 'yes', '1', 'true'))
        cur_lsd = defaults_config.get('learning_stage_days', 14)
        lsd_input = input(f"    Learning stage lookback days (default {cur_lsd}): ").strip()
        try:
            config['learning_stage_days'] = int(lsd_input) if lsd_input else cur_lsd
        except ValueError:
            config['learning_stage_days'] = cur_lsd
        # ── Analyst Panel (multi-agent debate) ──────────────────────────────
        cur_ap = defaults_config.get('analyst_panel_enabled', False)
        ap_input = input(f"    Enable Analyst Panel (10-agent adversarial debate framework) [{'y' if cur_ap else 'n'}]: ").strip().lower()
        config['analyst_panel_enabled'] = (cur_ap if not ap_input else ap_input in ('y', 'yes', '1', 'true'))
        if config.get('analyst_panel_enabled', False):
            intellistock_logger.log("    Analyst Panel options (press Enter for defaults):", "cyan", service="CLI")
            intellistock_logger.log("    -- LLM settings (leave blank to inherit from main strategy LLM) --", "cyan", service="CLI")
            for _ap_key, _ap_label, _ap_default, _ap_type in [
                ('analyst_panel_llm_provider', 'LLM provider (azure/openai/anthropic)', 'azure', str),
                ('analyst_panel_llm_model', 'Model for R1+R2 agents (e.g. gpt-4.1-mini)', 'gpt-4.1-mini', str),
                ('analyst_panel_moderator_llm_model', 'Model for R3 moderator synthesis (e.g. gpt-5-mini)', 'gpt-5-mini', str),
                ('analyst_panel_azure_openai_endpoint', 'Azure endpoint (leave blank = inherit)', '', str),
                ('analyst_panel_azure_openai_api_version', 'Azure API version', '2024-10-21', str),
                ('analyst_panel_llm_reasoning_effort', 'Reasoning effort (low/medium/high)', 'medium', str),
            ]:
                _ap_cur = defaults_config.get(_ap_key, _ap_default)
                _ap_disp = _ap_cur if _ap_cur else '(inherit from main)'
                _ap_val = input(f"      {_ap_label} [{_ap_disp}]: ").strip()
                if _ap_val:
                    try: config[_ap_key] = _ap_type(_ap_val)
                    except ValueError: config[_ap_key] = _ap_cur
                elif _ap_cur:
                    config[_ap_key] = _ap_cur
            intellistock_logger.log("    -- Behavior settings --", "cyan", service="CLI")
            for _ap_key, _ap_label, _ap_default, _ap_type in [
                ('analyst_panel_rounds', 'Debate rounds (1=independent, 2=+debate, 3=+synthesis)', 3, int),
                ('analyst_panel_debate_style', 'Debate style (adversarial/collaborative/structured)', 'adversarial', str),
                ('analyst_panel_max_workers', 'Max parallel agent workers', 5, int),
                ('analyst_panel_timeout_sec', 'Timeout per agent (seconds)', 120, int),
                ('analyst_panel_score_weight', 'Panel score weight (0.0-1.0)', 0.15, float),
                ('analyst_panel_memory_days', 'Agent memory lookback (days)', 14, int),
                ('analyst_panel_max_stocks', 'Max stocks to rate per agent', 10, int),
                ('analyst_panel_max_llm_calls', 'Max LLM calls per bar (cost cap)', 25, int),
                ('analyst_panel_cooldown_seconds', 'Stagger between agent submissions (sec)', 0.5, float),
                ('analyst_panel_sector_specialist_sector', 'Sector specialist focus', 'Technology', str),
            ]:
                _ap_cur = defaults_config.get(_ap_key, _ap_default)
                _ap_val = input(f"      {_ap_label} [{_ap_cur}]: ").strip()
                if _ap_val:
                    try:
                        config[_ap_key] = _ap_type(_ap_val)
                    except ValueError:
                        config[_ap_key] = _ap_cur
                else:
                    config[_ap_key] = _ap_cur
            # Scoped prompt-hash cache for panel LLM calls (independent of nexus_fast_mode).
            # Default False — opt-in only. When True, panel R1/R2/R3 calls use the
            # GraphNexusLLMPromptCache RethinkDB table and skip live LLM calls on cache hits.
            _cur_apc = defaults_config.get('analyst_panel_cache_enabled', False)
            _apc_input = input(f"      Enable prompt caching for analyst panel LLM calls [{'y' if _cur_apc else 'n'}]: ").strip().lower()
            config['analyst_panel_cache_enabled'] = (_cur_apc if not _apc_input else _apc_input in ('y', 'yes', '1', 'true'))
        # ── TOON format ─────────────────────────────────────────────────────
        cur_toon = defaults_config.get('use_toon_format', True)
        toon_input = input(f"    Use TOON format for LLM inputs (~40%% fewer tokens vs JSON) [{'y' if cur_toon else 'n'}]: ").strip().lower()
        config['use_toon_format'] = (cur_toon if not toon_input else toon_input in ('y', 'yes', '1', 'true'))
        # ── Neo4j / graph settings ──────────────────────────────────────────
        for k, label, env_var, default in [
            ('neo4j_uri',      'Neo4j URI',      'NEO4J_URI',      'bolt://localhost:7687'),
            ('neo4j_user',     'Neo4j user',     'NEO4J_USER',     'neo4j'),
            ('neo4j_password', 'Neo4j password', 'NEO4J_PASSWORD', 'intellistock'),
        ]:
            cur = defaults_config.get(k, '')
            val = input(f"    {label} (Enter to use {env_var} env{f', current {cur}' if cur else ''}): ").strip()
            if val:
                config[k] = val
            elif cur:
                config[k] = cur
        # ── Thresholds ──────────────────────────────────────────────────────
        _gn_defaults = {
            'buy_threshold':  ('Buy threshold (raw score, default 0.15)',  0.15),
            'sell_threshold': ('Sell threshold (raw score, default -0.15)', -0.15),
            'nexus_portfolio_pct': ('Max portfolio % for nexus stock buys (0-1, default 0.80)', 0.80),
            'etf_portfolio_pct': ('Max portfolio % for nexus ETF buys (0-1, default 0.10)', 0.10),
            'pool_a_base': ('Pool A base slots for direct/high-priority stock buys (default 12)', 12),
            'pool_b_base': ('Pool B base slots for propagation stock buys (default 6)', 6),
            'pool_a_min': ('Pool A minimum guaranteed slots (default 3)', 3),
            'pool_b_min': ('Pool B minimum guaranteed slots (default 2)', 2),
            'max_stock_buys_per_day': ('Max stock buys per day after pool merge (default 12)', 12),
            'cash_reserve_floor_pct': ('Cash reserve floor % of starting capital (default 0.10)', 0.10),
            'cash_reserve_hard_min_positions': ('Min open positions before reserve can relax (default 5)', 5),
            'cash_reserve_release_min_score': ('Min raw score to release reserve after position threshold (default 0.50)', 0.50),
            'cash_reserve_release_cap_pct': ('Max share of reserve releasable after threshold (default 1.00)', 1.00),
            'buy_price_floor': ('Minimum stock price allowed for buys (default 5)', 5.0),
            'trailing_stop_pct': ('Trailing-stop drawdown % from peak (default 8)', 8.0),
            'trailing_stop_commodity_etf_pct': ('Trailing-stop % for commodity ETFs (default 12)', 12.0),
            'trailing_stop_sector_etf_pct': ('Trailing-stop % for sector ETFs (default 10)', 10.0),
            'profit_take_gain_pct': ('Profit-take trigger gain % (default 40)', 40.0),
            'profit_take_sell_fraction': ('Profit-take sell fraction (default 0.50)', 0.50),
            'rotation_min_delta': ('Rotation minimum score delta (default 0.15)', 0.15),
            'rotation_min_hold_days': ('Rotation minimum hold days before replacement sells (default 10)', 10),
            'rotation_profitable_min_delta': ('Rotation delta required to replace a profitable hold (default 1.50)', 1.50),
            'rotation_profitable_full_exit_min_hold_days': ('Minimum hold days before a profitable full rotation exit is allowed (default 20)', 20),
            'rotation_profitable_min_incoming_raw_score': ('Incoming raw score required to fully replace a profitable hold (default 2.0)', 2.0),
            'rotation_replace_loss_threshold_pct': ('Rotation loss %% threshold for replacing losers (default -0.5)', -0.5),
            'rotation_ml_weight': ('Rotation ML confidence weight (default 0.20)', 0.20),
            'rotation_winner_lock_min_hold_days': ('Winner-lock minimum hold days before blocking profitable full exits (default 5)', 5),
            'rotation_winner_lock_min_pnl_pct': ('Winner-lock minimum unrealized gain %% (default 3.0)', 3.0),
            'rotation_winner_lock_min_raw_score': ('Winner-lock minimum held raw score (default -0.10)', -0.10),
            'rotation_winner_lock_max_peak_drawdown_pct': ('Winner-lock maximum drawdown from post-entry peak %% (default 8.0)', 8.0),
            'rotation_break_glass_raw_score': ('Break-glass incoming raw score for partial trims (default 2.75)', 2.75),
            'rotation_break_glass_delta': ('Break-glass rotation delta for partial trims (default 2.25)', 2.25),
            'rotation_break_glass_sell_fraction': ('Break-glass sell fraction for partial trims (default 0.50)', 0.50),
            'portfolio_drawdown_halt_pct': ('Portfolio drawdown halt % from peak (default 15)', 15.0),
            'portfolio_drawdown_resume_up_days': ('Drawdown halt resume up-days (default 2)', 2),
            'portfolio_drawdown_halt_backfill_budget_pct': ('Backfill budget share allowed at halt threshold (default 0.50)', 0.50),
            'portfolio_drawdown_halt_backfill_reduce_pct': ('Drawdown % where backfill budget reduces (default 20)', 20.0),
            'portfolio_drawdown_halt_backfill_reduce_budget_pct': ('Backfill budget share after reduce threshold (default 0.25)', 0.25),
            'portfolio_drawdown_halt_backfill_stop_pct': ('Drawdown % where backfill fully stops (default 25)', 25.0),
            'deployment_bar1_cap_pct': ('Deployment ramp bar-1 cap % of starting capital (default 0.50)', 0.50),
            'deployment_bar2_cap_pct': ('Deployment ramp bar-2 cap % of starting capital (default 0.70)', 0.70),
            'deployment_bar3_cap_pct': ('Deployment ramp bar-3 cap % of starting capital (default 0.90)', 0.90),
            'macro_risk_scale_step': ('Macro risk scale step per bearish net point (default 0.10)', 0.10),
            'macro_risk_scale_min': ('Macro risk minimum buy-budget scale (default 0.60)', 0.60),
            'trend_max_age_days': ('Trend max age in days (default 21)', 21),
            'max_hold_days': ('Max days to hold a position before auto-sell (default 45)', 45),
            'max_discovered_stocks': ('Max discovered stocks in active list (default 90)', 90),
            'max_sector_peer_discoveries_per_day': ('Max sector-peer discoveries per day (default 3)', 3),
            'max_competitor_discoveries_per_day': ('Max competitor discoveries per day (default 3)', 3),
            'sector_fill_max_per_sector': ('Max discovered names per sector fill pass (default 6)', 6),
            'sector_watchlist_reserved_slots': ('Reserved discovery slots for sector watchlist names (default 0)', 0),
            'sector_watchlist_max_per_sector': ('Max watchlist discoveries per matching sector (default 0)', 0),
            'consecutive_sell_days_to_prune': ('Days of consecutive sell signals to auto-prune (default 5)', 5),
            'propagation_discovery_min_score': ('Min propagation raw score to auto-discover (default 0.25)', 0.25),
            'propagation_discovery_min_paths': ('Min graph paths for propagation discovery (default 2)', 2),
            'propagation_min_paths': ('Min graph paths for V9 propagation buy quality filter (default 2)', 2),
            'propagation_min_raw_score': ('Min raw score for low-path propagation buys (default 0.30)', 0.30),
            'backfill_queue_grace_bars': ('Grace bars for broker-skipped backfill names before score rejection (default 3)', 3),
            'backfill_queue_priority_grace_bars': ('Grace bars for priority watchlist/propagation backfill names (default 8)', 8),
            'backfill_queue_max_size': ('Max protected backfill queue size (default 30)', 30),
            'backfill_queue_reserved_priority_slots': ('Reserved queue slots for watchlist/propagation priority names (default 10)', 10),
            'min_market_cap': ('Minimum market cap for new stock buys (default 500000000)', 500000000),
            'min_avg_volume': ('Minimum average daily volume for new stock buys (default 200000)', 200000),
            'sell_enforcement_min_hold_days': ('Min hold days before trend sell enforcement (default 5)', 5),
            'sell_enforcement_hysteresis_threshold': ('Trend sell hysteresis raw-score threshold (default -0.50)', -0.50),
            'sell_enforcement_consecutive_neutral_days': ('Consecutive neutral/down days before trend sell enforcement (default 3)', 3),
            'backfill_budget_reserve_pct': ('Share of buy budget reserved for existing backfill queue (default 0.20)', 0.20),
            'watchlist_sector_protected_slots': ('Protected buy slots per active watchlist sector under sector concentration (default 0)', 0),
            'watchlist_priority_requires_active_sector': ('Only grant watchlist priority when the configured sector is active (default False)', False),
            'watchlist_priority_slots': ('Reserved executable slots for active watchlist priority names (default 0)', 0),
            'watchlist_priority_min_raw_score': ('Min raw score for watchlist priority tranche (default 0.35)', 0.35),
            'propagation_expansion_reserved_slots': ('Reserved executable slots for propagation expansion buys (default 4)', 4),
            'propagation_expansion_min_raw_score': ('Min raw score for propagation expansion tranche (default 0.50)', 0.50),
            'priority_min_position_size': ('Reduced minimum position size for priority watchlist/propagation buys (default 100)', 100),
            'allocation_max_new_stock_buys': ('Max executable new stock buys in the balanced allocation slate (default 6)', 6),
            'allocation_execute_min_raw_score': ('Min raw score for immediate executable slate funding (default 0.35)', 0.35),
            'allocation_top2_min_raw_score': ('Min raw score for top-2 balanced allocation preference (default 0.50)', 0.50),
            'winner_add_min_hold_days': ('Winner add-on minimum hold days (default 5)', 5),
            'winner_add_min_pnl_pct': ('Winner add-on minimum unrealized gain %% (default 8.0)', 8.0),
            'winner_add_min_raw_score': ('Winner add-on minimum held raw score (default 0.25)', 0.25),
            'winner_add_max_drawdown_from_peak_pct': ('Winner add-on maximum drawdown from peak %% (default 5.0)', 5.0),
            'winner_add_fraction_of_initial': ('Winner add-on size as a fraction of initial entry notional (default 0.50)', 0.50),
            'winner_add_max_count': ('Maximum winner add-on count per live position (default 1)', 1),
            'max_propagated_scoring_slots': ('Max ephemeral propagation scoring expansion slots (default 15)', 15),
            'overlay_price_lookback_days': ('Price history lookback for LLM overlay (default 30)', 30),
            'llm_overlay_max_stock_candidates': ('Max stock candidates for LLM trade overlay (default 40)', 40),
            'llm_overlay_max_etf_candidates': ('Max ETF candidates for LLM trade overlay (default 6)', 6),
            'etf_min_trend_strength': ('Min trend strength to allocate ETF (0-1, default 0.4)', 0.4),
            'max_trend_etfs': ('Max ETFs per trend keyword match (default 6)', 6),
            'max_etf_buys_per_day': ('Max ETF buys per day to avoid dilution (default 3)', 3),
            'momentum_discovery_min_20d_return': ('Min 20-day return %% for momentum discovery (default 15)', 15.0),
            'momentum_discovery_min_60d_return': ('Min 60-day return %% for momentum discovery (default 40)', 40.0),
            'momentum_discovery_max_per_day': ('Max momentum discoveries per day (default 6)', 6),
            'momentum_discovery_protect_days': ('Days a momentum-discovered stock is shielded from eviction (default 10)', 10),
            'ml_signal_weight': ('ML model signal weight (0-1, default 0.50)', 0.50),
            'price_trend_bull_20d': ('Price trend bullish 20d return threshold %% (default 8)', 8.0),
            'price_trend_bull_60d': ('Price trend bullish 60d return threshold %% (default 12)', 12.0),
        }
        for k, (label, default) in _gn_defaults.items():
            if k in config:
                continue  # already set above
            cur = defaults_config.get(k, default)
            val = input(f"    {label} [{cur}]: ").strip()
            if val:
                try:
                    config[k] = float(val) if isinstance(default, float) else int(val)
                except ValueError:
                    config[k] = cur
            else:
                config[k] = cur
        cur_alloc_profile = str(defaults_config.get('allocation_profile', 'balanced') or 'balanced').strip().lower()
        alloc_profile = input(f"    Allocation profile for new stock buys (balanced/equal) [{cur_alloc_profile}]: ").strip().lower()
        config['allocation_profile'] = alloc_profile if alloc_profile in ('balanced', 'equal') else cur_alloc_profile
        # ── Feature toggles ─────────────────────────────────────────────────
        _gn_toggles = [
            ('google_news_enabled',            'Enable Google News macro classification',            True),
            ('trend_tracking_enabled',         'Enable market trend tracking',                       True),
            ('stock_finder_enabled',           'Enable stock discovery from trends',                 True),
            ('propagation_discovery_enabled',  'Enable stock discovery from graph propagation',      True),
            ('benzinga_discovery_enabled',     'Enable stock discovery from Benzinga signals',       True),
            ('sell_enforcement_enabled',       'Enable sell enforcement on trend reversals',         True),
            ('earnings_penalty_enabled',       'Dampen single-path earnings-only buys',              True),
            ('etf_allocation_enabled',         'Enable ETF allocation based on market trends',       True),
            ('llm_overlay_enabled',            'Enable LLM trade overlay for final scoring',         True),
            ('momentum_discovery_enabled',    'Enable stock discovery from price momentum',          True),
            ('sector_price_context_enabled',  'Enable sector/commodity price context for LLM trends', True),
            ('price_trend_detection_enabled', 'Enable price-based trend detection (independent of LLM)', True),
            ('cash_reserve_floor_hard',       'Enforce hard cash reserve floor',                     True),
            ('cash_reserve_release_after_min_positions', 'Allow high-conviction buys to use reserve after min positions', True),
            ('portfolio_drawdown_halt_enabled', 'Enable portfolio drawdown halt',                    True),
            ('deployment_ramp_enabled',       'Enable staged capital deployment ramp',               True),
            ('macro_risk_scaling_enabled',    'Scale buy budget down when macro backdrop turns bearish', True),
            ('propagation_floor_requires_min_paths', 'Require propagation floor candidates to satisfy min path count', True),
            ('rotation_winner_lock_enabled',  'Enable winner-lock protection against early profitable full exits', True),
            ('winner_add_enabled',            'Enable one-time winner add-ons for healthy leaders',  True),
            ('priority_budget_can_bypass_regular_min', 'Allow priority buys to bypass the regular minimum position size', True),
        ]
        intellistock_logger.log("    Feature toggles (y/n, Enter=default):", "cyan", service="CLI")
        for k, label, default in _gn_toggles:
            cur = defaults_config.get(k, default)
            val = input(f"      {label} [{'y' if cur else 'n'}]: ").strip().lower()
            if val in ('y', 'yes', '1', 'true'):
                config[k] = True
            elif val in ('n', 'no', '0', 'false'):
                config[k] = False
            else:
                config[k] = cur
        cur_missing_meta = str(defaults_config.get('quality_filter_missing_metadata_policy', 'warn') or 'warn')
        missing_meta_input = input(f"      Missing market-cap metadata policy (warn/allow/block) [{cur_missing_meta}]: ").strip().lower()
        config['quality_filter_missing_metadata_policy'] = missing_meta_input if missing_meta_input in ('warn', 'allow', 'block') else cur_missing_meta
        cur_watchlist = json.dumps(defaults_config.get('sector_watchlist', {}), separators=(',', ':'))
        watchlist_input = input(f"      Sector watchlist JSON [{cur_watchlist or '{}'}]: ").strip()
        if watchlist_input:
            try:
                parsed_watchlist = json.loads(watchlist_input)
                config['sector_watchlist'] = parsed_watchlist if isinstance(parsed_watchlist, dict) else defaults_config.get('sector_watchlist', {})
            except json.JSONDecodeError:
                config['sector_watchlist'] = defaults_config.get('sector_watchlist', {})
        elif 'sector_watchlist' in defaults_config:
            config['sector_watchlist'] = defaults_config.get('sector_watchlist', {})
        # ── Dual-cadence live-mode optimization ─────────────────────────────
        # Full cycle once per trading day at-or-after 9:30 ET; hourly
        # risk-only monitor cycles after. Backtest behavior unaffected.
        # Parse y/n strictly: empty preserves the prior value, 'y/yes/1/true'
        # is True, 'n/no/0/false' is False, anything else re-prompts (so a
        # typo can't silently disable the feature).
        def _yn_strict(prompt_label, default_val):
            _truthy = ('y', 'yes', '1', 'true')
            _falsy = ('n', 'no', '0', 'false')
            while True:
                _ans = input(
                    f"    {prompt_label} [{'y' if default_val else 'n'}]: "
                ).strip().lower()
                if not _ans:
                    return default_val
                if _ans in _truthy:
                    return True
                if _ans in _falsy:
                    return False
                print("    Please answer y/yes/n/no.")
        cur_dc = defaults_config.get('nexus_dual_cadence_enabled', True)
        config['nexus_dual_cadence_enabled'] = _yn_strict(
            "Enable dual-cadence live mode (full cycle at market open, hourly risk-only monitor after)",
            cur_dc,
        )
        cur_sd = defaults_config.get('nexus_dual_cadence_monitor_block_same_day_exit', True)
        config['nexus_dual_cadence_monitor_block_same_day_exit'] = _yn_strict(
            "Block same-day exits in monitor mode (day-trading prevention)",
            cur_sd,
        )
        print("    NOTE: backtest-simulation runs hourly cadence. Headline returns are NOT comparable to the +49% baseline.")
        cur_dc_bt = defaults_config.get('nexus_dual_cadence_backtest_simulation', False)
        config['nexus_dual_cadence_backtest_simulation'] = _yn_strict(
            "Enable dual-cadence backtest harness (behavior validation only, NOT a P&L A/B)",
            cur_dc_bt,
        )
        intellistock_logger.log("    Graph Nexus runs once per pass: news→LLM sentiment→Neo4j propagation→signals.", "white", service="CLI")
    else:
        config_input = input(f"  Config (JSON dict, optional, e.g. {{\"key\":\"value\"}}, press Enter for {{}}): ").strip()
        if config_input:
            try:
                parsed = json.loads(config_input)
                if isinstance(parsed, dict):
                    config.update(parsed)
            except json.JSONDecodeError as e:
                intellistock_logger.log(f"    Invalid JSON: {e}. Using empty dict.", "yellow", service="CLI")


def _collect_one_sub_strategy_interactive(defaults=None):
    """Collect a single sub-strategy. defaults: optional dict with strategy, execution_position, weight, conditions, config (Enter to keep). Returns one dict or None if cancelled."""
    defaults = defaults or {}
    try:
        cur_type = defaults.get('strategy', '')
        prompt = f"Sub-strategy type (e.g. rsi, macd, earnings, ml_news, graph_nexus_analysis, risk_manager, pdt_guard, exposure_limits, cooldown_manager, trade_allocator, tiered_risk, position_sizing){f', current {cur_type}' if cur_type else ''}: "
        strategy_type = input(prompt).strip()
        if strategy_type.lower() in ('done', 'exit', 'quit', 'q', ''):
            if not strategy_type and cur_type:
                strategy_type = cur_type
            else:
                return None
        if not strategy_type:
            return None
        cur_pos = defaults.get('execution_position')
        pos_prompt = f"  Execution position (int){f', current {cur_pos}' if cur_pos is not None else ''}: "
        pos_input = input(pos_prompt).strip()
        if pos_input.lower() in ('done', 'exit', 'quit', 'q'):
            return None
        execution_position = int(pos_input) if pos_input else (cur_pos if cur_pos is not None else 0)
        cur_w = defaults.get('weight')
        weight_prompt = f"  Weight (float){f', current {cur_w}' if cur_w is not None else ''}: "
        weight_input = input(weight_prompt).strip()
        if weight_input.lower() in ('done', 'exit', 'quit', 'q'):
            return None
        weight = float(weight_input) if weight_input else (cur_w if cur_w is not None else 0.5)
        cur_cond = defaults.get('conditions') or {}
        cond_str = json.dumps(cur_cond) if cur_cond else '{}'
        conditions_input = input(f"  Conditions (JSON, Enter for {cond_str}): ").strip()
        if conditions_input.lower() in ('done', 'exit', 'quit', 'q'):
            return None
        conditions = json.loads(conditions_input) if conditions_input else cur_cond
        if not isinstance(conditions, dict):
            conditions = {}
        # Auto-set phase for strategies with fixed phases
        _post_phase_types = ("trade_allocator", "position_sizing")
        if strategy_type.lower() in _post_phase_types:
            decision_phase = 'post'
            intellistock_logger.log(f"  Decision phase: post (auto-set for {strategy_type})", "white", service="CLI")
        else:
            cur_phase = (defaults.get('decision_phase') or 'pre').strip().lower()
            phase_input = input(f"  Decision phase (pre=vote for buy/sell, post=order size/pricing){f', current {cur_phase}' if cur_phase else ''} [pre]: ").strip().lower()
            decision_phase = (phase_input or cur_phase or 'pre')[:4]
            if decision_phase != 'post':
                decision_phase = 'pre'
        config = {}
        _prompt_strategy_type_and_config(strategy_type, config, defaults.get('config') or {}, conditions)
        # Strategies with run_once-only implementations must keep run_once scope.
        _run_once_types = ("earnings", "ml_news", "graph_nexus_analysis")
        execution_scope = "run_once" if strategy_type.lower() in _run_once_types else (defaults.get("execution_scope") or "per_symbol")
        result = {
            'weight': weight,
            'execution_position': execution_position,
            'decision_phase': decision_phase,
            'execution_scope': execution_scope,
            'strategy': strategy_type,
            'conditions': conditions,
            'config': config,
        }
        return result
    except (EOFError, KeyboardInterrupt):
        return None


def _collect_sub_strategies_interactive():
    """Interactive loop to collect sub-strategy entries (type, position, weight, conditions, config). Returns list of dicts; empty list if user finishes without adding any or cancels."""
    new_strategies = []
    intellistock_logger.log("Enter sub-strategy details (type 'done' or 'exit' when finished):", "white", service="CLI")
    intellistock_logger.log("", service="CLI")
    while True:
        try:
            strategy_type = input(f"Sub-strategy type (e.g. rsi, macd, earnings, ml_news, graph_nexus_analysis, risk_manager, pdt_guard, exposure_limits, cooldown_manager, trade_allocator, tiered_risk, position_sizing) [{len(new_strategies) + 1}]: ").strip()
            if strategy_type.lower() in ('done', 'exit', 'quit', 'q'):
                break
            if not strategy_type:
                intellistock_logger.log("Strategy type cannot be empty. Skipping.", "yellow", service="CLI")
                continue
            while True:
                pos_input = input(f"  Execution position (int): ").strip()
                if pos_input.lower() in ('done', 'exit', 'quit', 'q'):
                    strategy_type = None
                    break
                try:
                    execution_position = int(pos_input)
                    break
                except ValueError:
                    intellistock_logger.log("    Invalid. Must be an integer. Try again or type 'done' to finish.", "yellow", service="CLI")
            if strategy_type is None:
                break
            while True:
                weight_input = input(f"  Weight (float): ").strip()
                if weight_input.lower() in ('done', 'exit', 'quit', 'q'):
                    strategy_type = None
                    break
                try:
                    weight = float(weight_input)
                    break
                except ValueError:
                    intellistock_logger.log("    Invalid. Must be a number. Try again or type 'done' to finish.", "yellow", service="CLI")
            if strategy_type is None:
                break
            conditions_input = input(f"  Conditions (JSON dict, optional, press Enter for {{}}): ").strip()
            conditions = {}
            if conditions_input:
                if conditions_input.lower() in ('done', 'exit', 'quit', 'q'):
                    break
                try:
                    conditions = json.loads(conditions_input)
                    if not isinstance(conditions, dict):
                        conditions = {}
                except json.JSONDecodeError:
                    conditions = {}
            # Auto-set phase for strategies with fixed phases
            _post_phase_types = ("trade_allocator", "position_sizing")
            if strategy_type.lower() in _post_phase_types:
                decision_phase = 'post'
                intellistock_logger.log(f"  Decision phase: post (auto-set for {strategy_type})", "white", service="CLI")
            else:
                phase_input = input(f"  Decision phase (pre=vote, post=size/pricing) [pre]: ").strip().lower()
                decision_phase = 'post' if phase_input == 'post' else 'pre'
            config = {}
            _prompt_strategy_type_and_config(strategy_type, config, {}, conditions)
            if not isinstance(config, dict):
                config = {}
            # Strategies with run_once-only implementations must keep run_once scope.
            _run_once_types = ("earnings", "ml_news", "graph_nexus_analysis")
            execution_scope = "run_once" if strategy_type.lower() in _run_once_types else "per_symbol"
            new_strategies.append({
                'weight': weight,
                'execution_position': execution_position,
                'decision_phase': decision_phase,
                'execution_scope': execution_scope,
                'strategy': strategy_type,
                'conditions': conditions,
                'config': config,
            })
            intellistock_logger.log(f"  ✓ Added sub-strategy type={strategy_type} (weight={weight}, pos={execution_position}, phase={decision_phase}, scope={execution_scope})", "green", service="CLI")
            intellistock_logger.log("", service="CLI")
        except (EOFError, KeyboardInterrupt):
            intellistock_logger.log("\nCancelled.", "yellow", service="CLI")
            break
    return new_strategies


def cmd_create_strategy(conn, instance_id=None):
    """Interactive command to create a standalone strategy (not tied to an instance)."""
    try:
        ensure_strategies_table(conn)
        try:
            strategy_name = input("Strategy name (e.g. 'Momentum Strategy', 'News Sentiment Bot'): ").strip()
            if not strategy_name:
                intellistock_logger.log("Strategy name is required.", "yellow", service="CLI")
                return
        except (EOFError, KeyboardInterrupt):
            intellistock_logger.log("Cancelled.", "yellow", service="CLI")
            return
        intellistock_logger.log(f"\n--- Creating strategy: {strategy_name} ---", "cyan", service="CLI")
        new_strategies = _collect_sub_strategies_interactive()
        if not new_strategies:
            intellistock_logger.log("No sub-strategies added. Strategy not created.", "yellow", service="CLI")
            return
        out = action_create_strategy(conn, strategy_name, new_strategies)
        next_id = out["id"]
        intellistock_logger.log("\n✓ Created strategy id=%s '%s' with %d sub-strategy(ies):" % (next_id, strategy_name, len(new_strategies)), "green", service="CLI")
        for s in out.get("strategies", new_strategies):
            intellistock_logger.log("  - %s (weight=%s, pos=%s)" % (s.get("strategy"), s.get("weight"), s.get("execution_position")), "green", service="CLI")
        intellistock_logger.log(f"  Assign this strategy to an instance with: create-instance <ID> ... (then set strategy_id={next_id})", "white", service="CLI")
        intellistock_logger.log("", service="CLI")
    except Exception as e:
        intellistock_logger.log(f"Error: {e}", "red", service="CLI")


def cmd_delete_strategy(conn, strategy_id):
    """Delete a standalone strategy by ID."""
    if not strategy_id:
        intellistock_logger.log("Usage: delete-strategy STRATEGY_ID", "yellow", service="CLI")
        intellistock_logger.log("  Example: delete-strategy 5", "white", service="CLI")
        return
    try:
        sid = int(strategy_id)
    except (TypeError, ValueError):
        intellistock_logger.log("STRATEGY_ID must be an integer.", "yellow", service="CLI")
        return
    try:
        doc = action_get_strategy(conn, sid)
        strategy_name = doc.get("name", "id=%s" % sid)
        strategies_array = doc.get("strategies", [])
        instances_using = list(r.db(DB_NAME).table("Instances").filter(r.row["strategy_id"] == sid).run(conn))
        if instances_using:
            inst_ids = [str(inst.get("id", "?")) for inst in instances_using]
            intellistock_logger.log("\nWarning: Strategy id=%s '%s' is used by %d instance(s): %s" % (sid, strategy_name, len(instances_using), ", ".join(inst_ids)), "yellow", service="CLI")
            try:
                confirm = input("Delete anyway? (y/n): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                intellistock_logger.log("\nCancelled.", "yellow", service="CLI")
                return
            if confirm not in ("y", "yes"):
                intellistock_logger.log("Deletion cancelled.", "yellow", service="CLI")
                return
        action_delete_strategy(conn, sid, force=True)
        intellistock_logger.log("Deleted strategy id=%s '%s' (%d sub-strategies)" % (sid, strategy_name, len(strategies_array)), "green", service="CLI")
        if instances_using:
            intellistock_logger.log("  Note: %d instance(s) still reference this strategy. Update their strategy_id." % len(instances_using), "yellow", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_edit_strategy(conn, strategy_id_str):
    """Edit a strategy by ID: print current strategy, then menu to edit one sub-strategy by execution position, add, remove one, remove all, replace all, or done."""
    if not strategy_id_str:
        intellistock_logger.log("Usage: edit-strategy STRATEGY_ID", "yellow", service="CLI")
        intellistock_logger.log("  Example: edit-strategy 5", "white", service="CLI")
        return
    try:
        sid = int(strategy_id_str)
    except (TypeError, ValueError):
        intellistock_logger.log("STRATEGY_ID must be an integer.", "yellow", service="CLI")
        return
    try:
        strategy_doc = action_get_strategy(conn, sid)
        current_name = strategy_doc.get("name", "id=%s" % sid)
        strategies_array = list(strategy_doc.get("strategies", []) or [])

        # Prompt for new name once (default: current)
        try:
            name_prompt = f"Strategy name (current: '{current_name}', press Enter to keep): "
            new_name_input = input(name_prompt).strip()
            new_name = new_name_input if new_name_input else current_name
        except (EOFError, KeyboardInterrupt):
            intellistock_logger.log("\nCancelled.", "yellow", service="CLI")
            return

        # Menu loop: edit one, add, remove one, remove all, replace all, done
        while True:
            intellistock_logger.log(f"\n--- Sub-strategies ({len(strategies_array)}) ---", "cyan", service="CLI")
            for idx, strat in enumerate(strategies_array):
                if isinstance(strat, dict):
                    st = strat.get('strategy', '?')
                    w = strat.get('weight', '?')
                    pos = strat.get('execution_position', '?')
                    intellistock_logger.log(f"  [{idx}] type={st}  weight={w}  execution_position={pos}", "green", service="CLI")
            intellistock_logger.log("  1) Edit one sub-strategy (by execution position or index)", "white", service="CLI")
            intellistock_logger.log("  2) Add a new sub-strategy", "white", service="CLI")
            intellistock_logger.log("  3) Remove one sub-strategy (by execution position or index)", "white", service="CLI")
            intellistock_logger.log("  4) Remove all sub-strategies", "white", service="CLI")
            intellistock_logger.log("  5) Replace all sub-strategies (re-enter from scratch)", "white", service="CLI")
            intellistock_logger.log("  6) Done (save and exit)", "white", service="CLI")
            try:
                choice = input("Choice (1-6): ").strip()
            except (EOFError, KeyboardInterrupt):
                intellistock_logger.log("\nCancelled.", "yellow", service="CLI")
                return

            if choice == '6' or choice.lower() == 'done':
                break
            if choice == '1':
                if not strategies_array:
                    intellistock_logger.log("  No sub-strategies to edit.", "yellow", service="CLI")
                    continue
                which = input("  Execution position or index [0-based] to edit: ").strip()
                if not which:
                    continue
                try:
                    pos_or_idx = int(which)
                except ValueError:
                    intellistock_logger.log("  Invalid number.", "yellow", service="CLI")
                    continue
                # Find by execution_position first, else by index
                found_idx = None
                for i, s in enumerate(strategies_array):
                    if isinstance(s, dict) and s.get('execution_position') == pos_or_idx:
                        found_idx = i
                        break
                if found_idx is None and 0 <= pos_or_idx < len(strategies_array):
                    found_idx = pos_or_idx
                if found_idx is None:
                    intellistock_logger.log(f"  No sub-strategy with execution_position or index {pos_or_idx}.", "yellow", service="CLI")
                    continue
                existing = strategies_array[found_idx]
                intellistock_logger.log(f"  Editing: {existing.get('strategy')} (pos={existing.get('execution_position')})", "cyan", service="CLI")
                one = _collect_one_sub_strategy_interactive(defaults=existing)
                if one:
                    strategies_array[found_idx] = one
                    intellistock_logger.log("  ✓ Sub-strategy updated.", "green", service="CLI")
            elif choice == '2':
                intellistock_logger.log("  Add new sub-strategy:", "cyan", service="CLI")
                one = _collect_one_sub_strategy_interactive()
                if one:
                    strategies_array.append(one)
                    intellistock_logger.log("  ✓ Sub-strategy added.", "green", service="CLI")
            elif choice == '3':
                if not strategies_array:
                    intellistock_logger.log("  No sub-strategies to remove.", "yellow", service="CLI")
                    continue
                which = input("  Execution position or index [0-based] to remove: ").strip()
                if not which:
                    continue
                try:
                    pos_or_idx = int(which)
                except ValueError:
                    intellistock_logger.log("  Invalid number.", "yellow", service="CLI")
                    continue
                found_idx = None
                for i, s in enumerate(strategies_array):
                    if isinstance(s, dict) and s.get('execution_position') == pos_or_idx:
                        found_idx = i
                        break
                if found_idx is None and 0 <= pos_or_idx < len(strategies_array):
                    found_idx = pos_or_idx
                if found_idx is None:
                    intellistock_logger.log(f"  No sub-strategy with execution_position or index {pos_or_idx}.", "yellow", service="CLI")
                    continue
                removed = strategies_array.pop(found_idx)
                intellistock_logger.log(f"  ✓ Removed: {removed.get('strategy')} (pos={removed.get('execution_position')})", "green", service="CLI")
            elif choice == '4':
                if not strategies_array:
                    intellistock_logger.log("  Already empty.", "white", service="CLI")
                    continue
                try:
                    confirm = input("  Remove ALL sub-strategies? (y/n): ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    continue
                if confirm in ('y', 'yes'):
                    strategies_array = []
                    intellistock_logger.log("  ✓ All sub-strategies removed.", "green", service="CLI")
            elif choice == '5':
                intellistock_logger.log("  Enter new sub-strategies (type 'done' when finished):", "cyan", service="CLI")
                new_list = _collect_sub_strategies_interactive()
                if new_list:
                    strategies_array = new_list
                    intellistock_logger.log(f"  ✓ Replaced with {len(strategies_array)} sub-strategy(ies).", "green", service="CLI")
                else:
                    intellistock_logger.log("  No sub-strategies entered; list unchanged.", "yellow", service="CLI")
            else:
                intellistock_logger.log("  Enter 1-6.", "yellow", service="CLI")

        action_edit_strategy(conn, sid, name=new_name, strategies=strategies_array)
        intellistock_logger.log("\n✓ Updated strategy id=%s '%s' with %d sub-strategy(ies):" % (sid, new_name, len(strategies_array)), "green", service="CLI")
        for s in strategies_array:
            intellistock_logger.log("  - %s (weight=%s, pos=%s)" % (s.get("strategy", "?"), s.get("weight"), s.get("execution_position")), "green", service="CLI")
        intellistock_logger.log("", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_list_backtests(conn):
    """List queued/running backtests from BacktestInstances and show status from BacktestResults in a table."""
    intellistock_logger.log("\n--- Backtests ---", "cyan", service="CLI")
    try:
        out = action_list_backtests(conn)
        backtests = out.get("backtests", [])
        if not backtests:
            intellistock_logger.log("  No backtests in queue.", "white", service="CLI")
            return
        table_rows = []
        for row in backtests:
            rid = str(row.get("id", ""))
            inst = str(row.get("instance", ""))
            stocks = row.get("stocks") or []
            tickers_str = ", ".join(str(s) for s in stocks[:8])
            if len(stocks) > 8:
                tickers_str += " (+%d)" % (len(stocks) - 8)
            prog = row.get("progress")
            progress_str = "%.1f%%" % prog if isinstance(prog, (int, float)) else (str(prog) if prog else "")
            pnl = row.get("pnl")
            pnl_str = "$%s" % ("{:,.2f}".format(pnl)) if isinstance(pnl, (int, float)) else (str(pnl) if pnl else "")
            pct = row.get("pnl_percent")
            pnl_pct_str = "%+.2f%%" % pct if isinstance(pct, (int, float)) else (str(pct) if pct else "")
            table_rows.append({
                "id": rid,
                "instance": inst,
                "status": str(row.get("status", "")),
                "progress": progress_str,
                "pnl": pnl_str,
                "pnl_pct": pnl_pct_str,
                "start": row.get("start_date", ""),
                "end": row.get("end_date", ""),
                "tickers": tickers_str,
            })

        # Column widths (min widths for header)
        w_id = max(4, len("ID"))
        w_inst = max(5, max(len(r["instance"]) for r in table_rows))
        w_status = max(6, max(len(r["status"]) for r in table_rows))
        w_prog = max(6, max(len(r["progress"]) for r in table_rows))
        w_pnl = max(10, max(len(r["pnl"]) for r in table_rows))
        w_pct = max(6, max(len(r["pnl_pct"]) for r in table_rows))
        w_start = 10
        w_end = 10
        w_tickers = min(32, max(7, max(len(r["tickers"]) for r in table_rows)))

        def sep():
            return "+" + "-" * (w_id + 2) + "+" + "-" * (w_inst + 2) + "+" + "-" * (w_status + 2) + "+" + "-" * (w_prog + 2) + "+" + "-" * (w_pnl + 2) + "+" + "-" * (w_pct + 2) + "+" + "-" * (w_start + 2) + "+" + "-" * (w_end + 2) + "+" + "-" * (w_tickers + 2) + "+"

        def line(d):
            return (
                "| " + d["id"].ljust(w_id) + " |"
                + " " + d["instance"].ljust(w_inst) + " |"
                + " " + d["status"].ljust(w_status) + " |"
                + " " + d["progress"].rjust(w_prog) + " |"
                + " " + d["pnl"].rjust(w_pnl) + " |"
                + " " + d["pnl_pct"].rjust(w_pct) + " |"
                + " " + d["start"].ljust(w_start) + " |"
                + " " + d["end"].ljust(w_end) + " |"
                + " " + (d["tickers"][:w_tickers] if len(d["tickers"]) > w_tickers else d["tickers"]).ljust(w_tickers) + " |"
            )

        header = {
            "id": "ID", "instance": "Inst", "status": "Status", "progress": "Progress",
            "pnl": "P&L", "pnl_pct": "P&L %", "start": "Start", "end": "End", "tickers": "Tickers",
        }
        intellistock_logger.log(sep(), "cyan", service="CLI")
        intellistock_logger.log(line(header), "cyan", service="CLI")
        intellistock_logger.log(sep(), "cyan", service="CLI")
        for tr in table_rows:
            intellistock_logger.log(line(tr), "white", service="CLI")
        intellistock_logger.log(sep(), "cyan", service="CLI")
        intellistock_logger.log("", service="CLI")
    except Exception as e:
        intellistock_logger.log(f"Error: {e}", "red", service="CLI")


def cmd_create_backtest(conn):
    """Interactive: queue a backtest. Asks for instance, stocks, dates, granularity_sec, key/secret (blank = use instance's)."""
    intellistock_logger.log("\n--- Queue a backtest (run by backtest engine) ---", "cyan", service="CLI")
    try:
        try:
            inst_in = input("Instance ID (required): ").strip()
        except EOFError:
            intellistock_logger.log("Cancelled.", "yellow", service="CLI")
            return
        if not inst_in:
            intellistock_logger.log("Instance ID is required.", "yellow", service="CLI")
            return
        try:
            stocks_in = input("Stocks (comma or space separated, e.g. AAPL MSFT GOOGL): ").strip()
        except EOFError:
            intellistock_logger.log("Cancelled.", "yellow", service="CLI")
            return
        if not stocks_in:
            intellistock_logger.log("At least one stock is required.", "yellow", service="CLI")
            return
        stocks = [s.strip().upper() for s in stocks_in.replace(",", " ").split() if s.strip()]
        if not stocks:
            intellistock_logger.log("No valid tickers.", "yellow", service="CLI")
            return
        try:
            start_date = input("Start date (YYYY-MM-DD): ").strip()
        except EOFError:
            intellistock_logger.log("Cancelled.", "yellow", service="CLI")
            return
        if not start_date:
            intellistock_logger.log("Start date is required.", "yellow", service="CLI")
            return
        try:
            end_date = input("End date (YYYY-MM-DD): ").strip()
        except EOFError:
            intellistock_logger.log("Cancelled.", "yellow", service="CLI")
            return
        if not end_date:
            intellistock_logger.log("End date is required.", "yellow", service="CLI")
            return
        try:
            gran_in = input("Granularity (e.g. 60, 1m, 5m, 15m, 1h, 1d; default 60): ").strip() or "60"
        except EOFError:
            gran_in = "60"
        granularity_sec = parse_granularity_to_seconds(gran_in)
        instance_doc = r.db(DB_NAME).table("Instances").get(inst_in).run(conn) or (r.db(DB_NAME).table("Instances").get(int(inst_in)).run(conn) if str(inst_in).isdigit() else None)
        default_key = (instance_doc.get("key") or "") if instance_doc else ""
        default_secret = (instance_doc.get("secret") or "") if instance_doc else ""
        try:
            key_in = input("Key (leave blank to use instance's key): ").strip()
            secret_in = input("Secret (leave blank to use instance's secret): ").strip()
            cash_in = input("Initial cash (default 100000): ").strip() or "100000"
        except EOFError:
            key_in = secret_in = ""
            cash_in = "100000"
        key = key_in if key_in else default_key
        secret = secret_in if secret_in else default_secret
        try:
            initial_cash = float(cash_in)
            if initial_cash <= 0:
                initial_cash = 100000.0
        except ValueError:
            initial_cash = 100000.0
        out = action_create_backtest(conn, inst_in, stocks, start_date, end_date, granularity_sec=granularity_sec, key=key, secret=secret, initial_cash=initial_cash)
        backtest_id = out["id"]
        intellistock_logger.log("Queued backtest id=%s (instance=%s, %s to %s, %d tickers). Run backtest engine to process." % (backtest_id, inst_in, start_date, end_date, len(stocks)), "green", service="CLI")
        intellistock_logger.log("  Start with: python backend/engines/backtest_engine.py", "white", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_delete_backtest(conn, backtest_id):
    """Remove a backtest from the queue by id (only pending/running can be removed; engine may have already deleted it)."""
    if not backtest_id:
        intellistock_logger.log("Usage: delete-backtest ID", "yellow", service="CLI")
        intellistock_logger.log("  Example: delete-backtest 3", "white", service="CLI")
        return
    try:
        out = action_delete_backtest(conn, backtest_id)
        intellistock_logger.log("Deleted backtest id=%s from queue." % out.get("id", backtest_id), "green", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_stop_backtest(conn, backtest_id):
    """Set run=false for a running backtest; the broker will see it and exit, then the engine removes the row."""
    if not backtest_id:
        intellistock_logger.log("Usage: stop-backtest ID", "yellow", service="CLI")
        intellistock_logger.log("  Example: stop-backtest 2", "white", service="CLI")
        return
    try:
        out = action_stop_backtest(conn, backtest_id)
        intellistock_logger.log("Stop requested for backtest id=%s. Broker will exit and the row will be removed." % out.get("id", backtest_id), "green", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_stop_all_backtests(conn):
    """Stop all running backtests and remove all from queue."""
    try:
        out = action_stop_all_backtests(conn)
        n = out.get("stopped", 0)
        msg = "Stopped and cleared %d backtest(s) (running + queue). IDs: %s" % (n, out.get("ids", []))
        c = out.get("containers_stopped", 0)
        if c > 0:
            msg += " Stopped %d orphan container(s)." % c
        intellistock_logger.log(msg, "green", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_pause_backtest(conn, backtest_id):
    """Set paused=true for a running backtest; the broker will stop advancing time until resume."""
    if not backtest_id:
        intellistock_logger.log("Usage: pause-backtest ID", "yellow", service="CLI")
        intellistock_logger.log("  Example: pause-backtest 2", "white", service="CLI")
        return
    try:
        out = action_pause_backtest(conn, backtest_id)
        intellistock_logger.log("Pause requested for backtest id=%s. Broker will wait until resume." % out.get("id", backtest_id), "green", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_resume_backtest(conn, backtest_id):
    """Set paused=false for a paused backtest; the broker will continue."""
    if not backtest_id:
        intellistock_logger.log("Usage: resume-backtest ID", "yellow", service="CLI")
        intellistock_logger.log("  Example: resume-backtest 2", "white", service="CLI")
        return
    try:
        out = action_resume_backtest(conn, backtest_id)
        intellistock_logger.log("Resume requested for backtest id=%s." % out.get("id", backtest_id), "green", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_summarize_backtest(conn, backtest_id):
    """Fetch a backtest result by id and display detailed statistics (P&L, per-stock, trades, round-trips)."""
    if not backtest_id or not str(backtest_id).strip():
        intellistock_logger.log("Usage: summarize-backtest ID", "yellow", service="CLI")
        intellistock_logger.log("  Example: summarize-backtest 242430", "white", service="CLI")
        return
    try:
        doc = action_summarize_backtest(conn, backtest_id)
        bid = doc.get("id")
        status = doc.get("status") or "unknown"
        start_date = doc.get("start_date") or "N/A"
        end_date = doc.get("end_date") or "N/A"
        tickers = doc.get("tickers") or []
        pnl = doc.get("pnl")
        pnl_percent = doc.get("pnl_percent")
        pnl_per_stock = doc.get("pnl_per_stock") or {}
        stock_price_change = doc.get("stock_price_change") or {}
        time_elapsed = doc.get("time_elapsed_seconds")
        portfolio_start_val = doc.get("portfolio_start_value")
        portfolio_end_val = doc.get("portfolio_end_value")
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
        total_cycle_pnl = doc.get("total_round_trip_pnl")
        max_val = doc.get("portfolio_value_high")
        min_val = doc.get("portfolio_value_low")

        intellistock_logger.log("\n" + "=" * 60, "cyan", service="CLI")
        intellistock_logger.log("  BACKTEST SUMMARY (id=%s)" % bid, "cyan", service="CLI")
        intellistock_logger.log("=" * 60, "cyan", service="CLI")
        intellistock_logger.log("  Status: %s" % status, "white", service="CLI")
        intellistock_logger.log("  Period: %s  to  %s" % (start_date, end_date), "white", service="CLI")
        if time_elapsed is not None:
            intellistock_logger.log("  Time elapsed: %.1f s" % time_elapsed, "white", service="CLI")
        intellistock_logger.log("  Tickers: %s" % (", ".join(tickers) if tickers else "N/A"), "white", service="CLI")
        intellistock_logger.log("", service="CLI")
        intellistock_logger.log("--- Total P&L ---", "cyan", service="CLI")
        if pnl is not None:
            color = "green" if pnl >= 0 else "red"
            intellistock_logger.log("  P&L: $%s" % "{:,.2f}".format(pnl), color, service="CLI")
        else:
            intellistock_logger.log("  P&L: N/A", "yellow", service="CLI")
        if pnl_percent is not None:
            color = "green" if pnl_percent >= 0 else "red"
            intellistock_logger.log("  P&L %%: %+.2f%%" % pnl_percent, color, service="CLI")
        else:
            intellistock_logger.log("  P&L %: N/A", "yellow", service="CLI")
        if portfolio_start_val is not None:
            intellistock_logger.log("  Start portfolio value: $%s" % "{:,.2f}".format(float(portfolio_start_val)), "white", service="CLI")
        if portfolio_end_val is not None:
            intellistock_logger.log("  End portfolio value:   $%s" % "{:,.2f}".format(float(portfolio_end_val)), "white", service="CLI")
        intellistock_logger.log("", service="CLI")
        intellistock_logger.log("--- Per-stock: Strategy P&L vs Buy-and-hold (start→end) ---", "cyan", service="CLI")
        if not tickers:
            intellistock_logger.log("  No tickers.", "white", service="CLI")
        else:
            intellistock_logger.log("  %-8s  %14s  %12s  %10s  %10s  %12s" % ("Symbol", "Strategy P&L", "Start $", "End $", "B&H %", "Vs B&H"), "white", service="CLI")
            intellistock_logger.log("  " + "-" * 68, "white", service="CLI")
            for sym in tickers:
                spnl = pnl_per_stock.get(sym)
                spnl_str = "$%s" % "{:,.2f}".format(spnl) if spnl is not None else "N/A"
                sc = stock_price_change.get(sym) or {}
                start_p = sc.get("start_price")
                end_p = sc.get("end_price")
                bh_pct = sc.get("change_percent")
                start_str = "$%.2f" % start_p if start_p is not None else "N/A"
                end_str = "$%.2f" % end_p if end_p is not None else "N/A"
                bh_str = "%+.2f%%" % bh_pct if bh_pct is not None else "N/A"
                vs_bh = "—"
                if spnl is not None and bh_pct is not None and start_p is not None and start_p != 0:
                    strategy_pct = (spnl / start_p) * 100.0 if start_p else 0
                    vs_bh = "Outperformed" if strategy_pct > bh_pct else ("Underperformed" if strategy_pct < bh_pct else "Same")
                intellistock_logger.log("  %-8s  %14s  %12s  %10s  %10s  %12s" % (sym, spnl_str, start_str, end_str, bh_str, vs_bh), "white", service="CLI")
        intellistock_logger.log("", service="CLI")
        intellistock_logger.log("--- Trades ---", "cyan", service="CLI")
        intellistock_logger.log("  Total trades: %d  (Buys: %d, Sells: %d)" % (total_trades, total_buys, total_sells), "white", service="CLI")
        intellistock_logger.log("  Round-trips (closed buy→sell cycles): %d" % round_trips, "white", service="CLI")
        if round_trips:
            intellistock_logger.log("    Winning: %d  |  Losing: %d  |  Breakeven: %d" % (winning, losing, breakeven), "white", service="CLI")
            intellistock_logger.log("    Win rate: %.1f%%" % win_rate, "white", service="CLI")
            if winning and avg_win is not None:
                intellistock_logger.log("    Avg winning round-trip: $%s" % "{:,.2f}".format(avg_win), "green", service="CLI")
            if losing and avg_loss is not None:
                intellistock_logger.log("    Avg losing round-trip:  $%s" % "{:,.2f}".format(avg_loss), "red", service="CLI")
            if total_cycle_pnl is not None:
                intellistock_logger.log("    Total P&L from round-trips: $%s" % "{:,.2f}".format(total_cycle_pnl), "white", service="CLI")
        intellistock_logger.log("", service="CLI")
        if max_val is not None and min_val is not None:
            intellistock_logger.log("--- Portfolio value range ---", "cyan", service="CLI")
            intellistock_logger.log("  High: $%s  |  Low: $%s" % ("{:,.2f}".format(max_val), "{:,.2f}".format(min_val)), "white", service="CLI")
            if max_val > 0:
                drawdown_pct = (max_val - min_val) / max_val * 100.0
                intellistock_logger.log("  Drawdown (high to low): %.1f%%" % drawdown_pct, "white", service="CLI")
            intellistock_logger.log("", service="CLI")
        intellistock_logger.log("=" * 60 + "\n", "cyan", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_backtest_logs(conn, backtest_id):
    """Fetch and display the last 500 log lines for a backtest run (from BacktestResults.logs)."""
    if not backtest_id or not str(backtest_id).strip():
        intellistock_logger.log("Usage: backtest-logs ID", "yellow", service="CLI")
        intellistock_logger.log("  Example: backtest-logs 242430", "white", service="CLI")
        return
    try:
        out = action_backtest_logs(conn, backtest_id)
        bid = out.get("id")
        logs = out.get("logs", [])
        status = out.get("status", "?")
        error_msg = out.get("error")
        intellistock_logger.log("\n--- Backtest logs (id=%s, status=%s, %d lines) ---\n" % (bid, status, len(logs)), "cyan", service="CLI")
        if error_msg:
            intellistock_logger.log("  Error: %s" % error_msg, "red", service="CLI")
            intellistock_logger.log("", service="CLI")
        if not logs:
            intellistock_logger.log("  No logs saved for this backtest (run may be pending or logs not yet written).", "yellow", service="CLI")
        else:
            for line in logs:
                print(line)
        intellistock_logger.log("", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_graph_backtest(conn, backtest_id):
    """Load a backtest result from DB by ID and plot it (portfolio value, prices, trades)."""
    if not backtest_id or not str(backtest_id).strip():
        intellistock_logger.log("Usage: graph-backtest ID", "yellow", service="CLI")
        intellistock_logger.log("  Example: graph-backtest 999", "white", service="CLI")
        return
    import tempfile
    import csv as csv_module
    try:
        data = action_graph_backtest_data(conn, backtest_id)
        bid = data.get("id")
        portfolio_value_history = data.get("portfolio_value_history") or []
        backtest_trades = data.get("backtest_trades") or []
        backtest_prices = data.get("backtest_prices") or []
        tmpdir = tempfile.mkdtemp(prefix="graph_backtest_")
        portfolio_csv = os.path.join(tmpdir, "backtest_portfolio_value.csv")
        trades_csv = os.path.join(tmpdir, "backtest_trades.csv")
        prices_csv = os.path.join(tmpdir, "backtest_prices.csv")
        with open(portfolio_csv, "w", newline="", encoding="utf-8") as f:
            w = csv_module.writer(f)
            w.writerow(["timestamp", "value", "cash"])
            for s in portfolio_value_history:
                w.writerow([s.get("timestamp"), s.get("value"), s.get("cash")])
        with open(trades_csv, "w", newline="", encoding="utf-8") as f:
            w = csv_module.writer(f)
            w.writerow(["timestamp", "action", "ticker", "shares", "price", "total", "cash_after"])
            for t in backtest_trades:
                w.writerow([t.get("timestamp"), t.get("action"), t.get("ticker"), t.get("shares"), t.get("price"), t.get("total"), t.get("cash_after")])
        if backtest_prices:
            with open(prices_csv, "w", newline="", encoding="utf-8") as f:
                w = csv_module.writer(f)
                w.writerow(["timestamp", "symbol", "close"])
                for p in backtest_prices:
                    w.writerow([p.get("timestamp"), p.get("symbol"), p.get("close")])
        _project_root = os.path.dirname(_backend_dir)
        if _project_root not in sys.path:
            sys.path.insert(0, _project_root)
        from plot_util import plot_backtest_from_csv
        intellistock_logger.log("Plotting backtest id=%s (data from DB)..." % bid, "cyan", service="CLI")
        plot_backtest_from_csv(portfolio_value_csv=portfolio_csv, trades_csv=trades_csv, prices_csv=prices_csv if backtest_prices else None, save_path=None, show_plot=True, save_plot=False)
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
        import traceback
        intellistock_logger.log(traceback.format_exc(), "red", service="CLI")


def cmd_create_instance(conn, instance_id, key=None, secret=None, granularity_time_increment=None):
    """Create a new instance in the Instances table. Server will spawn instance.py when runCommand=True."""
    if not instance_id or not instance_id.strip():
        intellistock_logger.log("Usage: create-instance ID [KEY [SECRET [GRANULARITY]]]", "yellow", service="CLI")
        intellistock_logger.log("  ID is required (e.g. my-instance-1). KEY, SECRET optional. GRANULARITY = time increment (e.g. 60, 1m, 1h).", "white", service="CLI")
        return
    instance_id = instance_id.strip()
    key = (key or "").strip() if key is not None else ""
    secret = (secret or "").strip() if secret is not None else ""
    try:
        name_input = input("Instance name (optional, press Enter to skip): ").strip()
    except EOFError:
        name_input = ""
    instance_name = name_input if name_input else None
    try:
        strategy_id_input = input("Strategy ID (optional, press Enter to skip): ").strip()
        strategy_id = int(strategy_id_input) if strategy_id_input and strategy_id_input.isdigit() else None
    except (EOFError, ValueError):
        strategy_id = None
    if not key:
        try:
            key = input("API Key (optional, press Enter to skip): ").strip() or ""
        except EOFError:
            key = ""
    if not secret:
        try:
            secret = input("API Secret (optional, press Enter to skip): ").strip() or ""
        except EOFError:
            secret = ""
    if granularity_time_increment is None or (isinstance(granularity_time_increment, str) and not granularity_time_increment.strip()):
        try:
            raw = input("Granularity time increment (e.g. 60, 1m, 5m, 1h, 1d; default 60): ").strip() or "60"
        except EOFError:
            raw = "60"
        granularity_sec = parse_granularity_to_seconds(raw)
    else:
        granularity_sec = parse_granularity_to_seconds(granularity_time_increment)
    # Brokerage selection
    brokerage_id = None
    try:
        from interactive_utils import action_list_brokerages
        brok_data = action_list_brokerages(conn)
        accounts = brok_data.get("accounts") or []
        if accounts:
            intellistock_logger.log("\nAvailable brokerages:", "cyan", service="CLI")
            for i, acct in enumerate(accounts):
                intellistock_logger.log("  [%d] %s (%s)" % (i + 1, acct.get("account_name", "?"), acct.get("brokerage_type", "?")), "white", service="CLI")
            intellistock_logger.log("  [0] None (no brokerage)", "white", service="CLI")
            try:
                brok_input = input("Select brokerage [0-%d] (default 0): " % len(accounts)).strip()
                brok_idx = int(brok_input) if brok_input.isdigit() else 0
                if 1 <= brok_idx <= len(accounts):
                    brokerage_id = accounts[brok_idx - 1].get("id")
                    intellistock_logger.log("Selected: %s" % accounts[brok_idx - 1].get("account_name"), "green", service="CLI")
            except (EOFError, ValueError):
                pass
    except Exception:
        pass
    # Max usage
    max_usage = None
    if brokerage_id:
        try:
            mu_input = input("Max usage ($, e.g. 10000; press Enter to skip): ").strip()
            if mu_input:
                max_usage = float(mu_input)
        except (EOFError, ValueError):
            pass
    try:
        out = action_create_instance(
            conn, instance_id, name=instance_name, strategy_id=strategy_id,
            key=key or None, secret=secret or None, granularity_time_increment=granularity_sec,
            brokerage_id=brokerage_id, max_usage=max_usage,
        )
        name_display = " '%s'" % out.get("name") if out.get("name") else ""
        intellistock_logger.log("Created instance: %s%s" % (out.get("id", instance_id), name_display), "green", service="CLI")
        intellistock_logger.log("Server will spawn the instance process if it is running. To run manually: python instance.py <instance_id> (key/secret read from DB)", "white", service="CLI")
        intellistock_logger.log("Add tickers for the instance with: add-stock <instance_id> SYMBOL", "white", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_delete_instance(conn, instance_id):
    """Delete an instance and show associated strategies with confirmation prompt."""
    if not instance_id or not instance_id.strip():
        intellistock_logger.log("Usage: delete-instance INSTANCE_ID", "yellow", service="CLI")
        intellistock_logger.log("  Example: delete-instance 13", "white", service="CLI")
        return
    instance_id = instance_id.strip()
    try:
        out = action_instances(conn)
        instance_doc = next((i for i in (out.get("instances") or []) if str(i.get("id")) == instance_id), None)
        if not instance_doc:
            try:
                instance_doc = r.db(DB_NAME).table("Instances").get(instance_id).run(conn) or r.db(DB_NAME).table("Instances").get(int(instance_id)).run(conn)
            except (TypeError, ValueError):
                pass
        if not instance_doc:
            intellistock_logger.log("Instance not found: %s" % instance_id, "yellow", service="CLI")
            return
        instance_name = instance_doc.get("name", "")
        stocks = instance_doc.get("stocks") or []
        run_cmd = instance_doc.get("runCommand", False)
        strategy_id = instance_doc.get("strategy_id")
        strategies_list = []
        strategy_name = None
        if strategy_id is not None:
            try:
                strategy_doc = r.db(DB_NAME).table("Strategies").get(strategy_id).run(conn)
                if strategy_doc:
                    strategy_name = strategy_doc.get("name", "id=%s" % strategy_id)
                    strategies_list = strategy_doc.get("strategies") or []
            except Exception:
                pass
        intellistock_logger.log("\n--- Delete Instance ---", "cyan", service="CLI")
        name_display = " '%s'" % instance_name if instance_name else ""
        intellistock_logger.log("Instance ID: %s%s" % (instance_id, name_display), "white", service="CLI")
        intellistock_logger.log("  runCommand: %s" % run_cmd, "white", service="CLI")
        stocks_str = ", ".join(str(s) for s in stocks[:10]) + (" (+%d more)" % (len(stocks) - 10) if len(stocks) > 10 else "")
        intellistock_logger.log("  Stocks: %s" % (stocks_str if stocks else "(none)"), "white", service="CLI")
        if strategies_list:
            intellistock_logger.log("\nAssociated strategy: id=%s '%s' (%d sub-strategies)" % (strategy_id, strategy_name or "", len(strategies_list)), "yellow", service="CLI")
            for idx, strat in enumerate(strategies_list):
                if isinstance(strat, dict):
                    intellistock_logger.log("  [%d] type=%s weight=%s pos=%s phase=%s" % (idx, strat.get("strategy", "?"), strat.get("weight", "?"), strat.get("execution_position", "?"), strat.get("decision_phase", "pre")), "yellow", service="CLI")
            intellistock_logger.log("  Note: Strategy will NOT be deleted (strategies are standalone).", "yellow", service="CLI")
        else:
            intellistock_logger.log("\nNo strategy assigned (strategy_id not set).", "white", service="CLI")
        try:
            confirm = input("\nAre you sure you want to delete this instance? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            intellistock_logger.log("\nCancelled.", "yellow", service="CLI")
            return
        if confirm not in ("y", "yes"):
            intellistock_logger.log("Deletion cancelled.", "yellow", service="CLI")
            return
        action_delete_instance(conn, instance_id, force=True)
        intellistock_logger.log("Deleted instance: %s%s" % (instance_id, name_display), "green", service="CLI")
        if strategy_id is not None:
            intellistock_logger.log("  Note: Strategy id=%s still exists (strategies are standalone)." % strategy_id, "white", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_link_strategy(conn, instance_id, strategy_id):
    """Link a strategy to an instance by setting instance.strategy_id."""
    if not instance_id or not strategy_id:
        intellistock_logger.log("Usage: link-strategy INSTANCE_ID STRATEGY_ID", "yellow", service="CLI")
        intellistock_logger.log("  Example: link-strategy 12 5", "white", service="CLI")
        return
    try:
        out = action_link_strategy(conn, instance_id, strategy_id)
        intellistock_logger.log("✓ Linked strategy id=%s to instance %s" % (out.get("strategy_id"), out.get("instance_id")), "green", service="CLI")
        intellistock_logger.log("  If the instance is running, broker will reload with the new strategy.", "white", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_add_stock(conn, instance_id, symbol):
    """Add a symbol to an instance's stocks list. Instance will relaunch broker with the new list (if running)."""
    if not instance_id or not symbol:
        intellistock_logger.log("Usage: add-stock INSTANCE_ID SYMBOL", "yellow", service="CLI")
        intellistock_logger.log("  Example: add-stock my-instance-1 AAPL", "white", service="CLI")
        return
    try:
        out = action_add_stock(conn, instance_id, symbol)
        if out.get("added"):
            intellistock_logger.log("Added %s to instance %s (stocks: %d ticker(s))" % (out.get("symbol"), instance_id, out.get("stocks_count", 0)), "green", service="CLI")
            intellistock_logger.log("Also added to LivePricesStocks for live pricing.", "green", service="CLI")
        else:
            intellistock_logger.log(out.get("message", "Symbol already in instance"), "yellow", service="CLI")
        intellistock_logger.log("If the instance is running, broker will relaunch with the updated tickers.", "white", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_remove_stock(conn, instance_id, symbol):
    """Remove a symbol from an instance's stocks list. Instance will relaunch broker (if running)."""
    if not instance_id or not symbol:
        intellistock_logger.log("Usage: remove-stock INSTANCE_ID SYMBOL", "yellow", service="CLI")
        intellistock_logger.log("  Example: remove-stock my-instance-1 AAPL", "white", service="CLI")
        return
    try:
        out = action_remove_stock(conn, instance_id, symbol)
        if out.get("removed"):
            intellistock_logger.log("Removed %s from instance %s (stocks: %d ticker(s))" % (out.get("symbol"), instance_id, out.get("stocks_count", 0)), "green", service="CLI")
        else:
            intellistock_logger.log(out.get("message", "Symbol not in instance"), "yellow", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_start_instance(conn, instance_id):
    """Set runCommand=True for an instance. Server will spawn instance.py if it is running."""
    if not instance_id or not instance_id.strip():
        intellistock_logger.log("Usage: start-instance ID", "yellow", service="CLI")
        intellistock_logger.log("  Example: start-instance my-instance-1", "white", service="CLI")
        return
    try:
        out = action_start_instance(conn, instance_id)
        intellistock_logger.log("Started instance: %s (runCommand=True). Server will spawn instance.py if running." % out.get("id", instance_id), "green", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_stop_instance(conn, instance_id):
    """Set runCommand=False for an instance. The instance process will exit when it sees the change."""
    if not instance_id or not instance_id.strip():
        intellistock_logger.log("Usage: stop-instance ID", "yellow", service="CLI")
        intellistock_logger.log("  Example: stop-instance my-instance-1", "white", service="CLI")
        return
    try:
        out = action_stop_instance(conn, instance_id)
        intellistock_logger.log("Stopped instance: %s (runCommand=False). Instance process will exit." % out.get("id", instance_id), "green", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_terminate_price(conn):
    """Set terminatePriceService=True so the server stops and does not restart price service."""
    try:
        action_terminate_price(conn)
        intellistock_logger.log("Set terminatePriceService=True. Price service will exit and not be restarted.", "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_terminate_discover(conn):
    """Set terminateDiscoverService=True so the server stops and does not restart discover service."""
    try:
        action_terminate_discover(conn)
        intellistock_logger.log("Set terminateDiscoverService=True. Discover service will exit and not be restarted.", "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_start_broker(conn):
    """Set runPriceService=True so price service starts the price broker."""
    try:
        action_start_broker(conn)
        intellistock_logger.log("Set runPriceService=True. Price service will start the broker if running.", "green", service="CLI")
    except Exception as e:
        intellistock_logger.log("Error: %s" % e, "red", service="CLI")


def cmd_trends(conn, sub=None, arg=None, instance_id=None):
    """Show/manage market trends."""
    try:
        if sub == "end":
            if not arg:
                intellistock_logger.log("Usage: trends end TREND_ID", "yellow", service="CLI")
                return
            out = action_end_trend(conn, arg)
            if out.get("ended"):
                intellistock_logger.log(f"Trend ended: {arg}", "green", service="CLI")
            else:
                intellistock_logger.log(out.get("message", "Could not end trend"), "yellow", service="CLI")
            return
        elif sub == "delete":
            if not arg:
                intellistock_logger.log("Usage: trends delete TREND_ID", "yellow", service="CLI")
                return
            out = action_delete_trend(conn, arg)
            intellistock_logger.log(f"Trend deleted: {arg}", "green", service="CLI")
            return
        # Default: list trends
        out = action_list_trends(conn, instance_id=instance_id, status="active")
        trends = out.get("trends", [])
        intellistock_logger.log(f"\n--- Market Trends ({len(trends)} active) ---", "cyan", service="CLI")
        if not trends:
            intellistock_logger.log("  No active trends.", "white", service="CLI")
        for t in trends:
            direction = t.get("direction", "?")
            strength = t.get("strength", 0)
            tickers = ", ".join(t.get("affected_tickers", [])[:5])
            name = t.get("name", t.get("id", "?"))
            arrow = "▲" if direction == "bullish" else ("▼" if direction == "bearish" else "—")
            color = "green" if direction == "bullish" else ("red" if direction == "bearish" else "white")
            intellistock_logger.log(f"  {arrow} {name} (strength={strength:.2f}) → {tickers}", color, service="CLI")
            if t.get("description"):
                intellistock_logger.log(f"    {t['description'][:80]}", "white", service="CLI")
        intellistock_logger.log("", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except ReqlDriverError as e:
        if "Connection is closed" in str(e):
            raise
        intellistock_logger.log(f"Error: {e}", "red", service="CLI")
    except Exception as e:
        intellistock_logger.log(f"Error: {e}", "red", service="CLI")


def cmd_discovered(conn, sub=None, instance_id=None, ticker=None):
    """Show/manage nexus-discovered stocks."""
    try:
        if sub == "remove":
            if not instance_id or not ticker:
                intellistock_logger.log("Usage: discovered remove INSTANCE_ID TICKER", "yellow", service="CLI")
                return
            out = action_remove_discovered_stock(conn, instance_id, ticker)
            intellistock_logger.log(f"Removed discovered stock: {ticker} from {instance_id}", "green", service="CLI")
            return
        # Default: list discovered stocks
        out = action_list_discovered_stocks(conn, instance_id=instance_id, status="active")
        stocks = out.get("stocks", [])
        intellistock_logger.log(f"\n--- Discovered Stocks ({len(stocks)} active) ---", "cyan", service="CLI")
        if not stocks:
            intellistock_logger.log("  No active discovered stocks.", "white", service="CLI")
        for s in stocks:
            tk = s.get("ticker", "?")
            source = s.get("source_trend", "?")
            discovered = s.get("discovered_at", "?")[:10]
            intellistock_logger.log(f"  {tk} (from: {source}, discovered: {discovered})", "green", service="CLI")
        intellistock_logger.log("", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except ReqlDriverError as e:
        if "Connection is closed" in str(e):
            raise
        intellistock_logger.log(f"Error: {e}", "red", service="CLI")
    except Exception as e:
        intellistock_logger.log(f"Error: {e}", "red", service="CLI")


def cmd_nexus_config(conn, instance_id=None, key=None, value=None):
    """Show/set nexus trend/discovery configuration."""
    try:
        if not instance_id:
            intellistock_logger.log("Usage: nexus-config INSTANCE_ID [KEY VALUE]", "yellow", service="CLI")
            return
        if key and value is not None:
            # Parse value to appropriate type
            v = value
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
            out = action_nexus_config_set(conn, instance_id, {key: v})
            applied = out.get("applied", {})
            if applied:
                intellistock_logger.log(f"Updated nexus config: {applied}", "green", service="CLI")
            else:
                intellistock_logger.log(f"No valid keys to update (allowed: trend_tracking_enabled, stock_finder_enabled, etc.)", "yellow", service="CLI")
            return
        # Default: show config
        out = action_nexus_config_get(conn, instance_id)
        config = out.get("config", {})
        intellistock_logger.log(f"\n--- Nexus Config ({instance_id}) ---", "cyan", service="CLI")
        for k, v in sorted(config.items()):
            intellistock_logger.log(f"  {k}: {v}", "white", service="CLI")
        intellistock_logger.log("", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except ReqlDriverError as e:
        if "Connection is closed" in str(e):
            raise
        intellistock_logger.log(f"Error: {e}", "red", service="CLI")
    except Exception as e:
        intellistock_logger.log(f"Error: {e}", "red", service="CLI")


def cmd_brokerages(conn):
    """List all linked brokerage accounts."""
    intellistock_logger.log("\n--- Linked Brokerage Accounts ---", "cyan", service="CLI")
    try:
        out = action_list_brokerages(conn)
        accounts = out.get("accounts", [])
        if not accounts:
            intellistock_logger.log("  No brokerage accounts linked. Use 'link-brokerage' to add one.", "yellow", service="CLI")
            return
        for acct in accounts:
            btype = acct.get("brokerage_type", "?").upper()
            name = acct.get("account_name", "?")
            status = acct.get("status", "?")
            aid = acct.get("id", "?")
            status_color = "green" if status == "active" else ("red" if status == "expired" else "yellow")
            intellistock_logger.log(f"  [{btype}] {name}  status={status}  id={aid}", status_color, service="CLI")
            if btype == "ALPACA":
                intellistock_logger.log(f"    key={acct.get('alpaca_key', '?')}  paper={acct.get('alpaca_paper', '?')}  acct={acct.get('alpaca_account_number', '?')}", "white", service="CLI")
            elif btype == "ROBINHOOD":
                intellistock_logger.log(f"    acct={acct.get('robinhood_account_number', '?')}  last_refresh={acct.get('last_refresh_at', '?')}", "white", service="CLI")
            if acct.get("last_error"):
                intellistock_logger.log(f"    error: {acct['last_error']}", "red", service="CLI")
        intellistock_logger.log("", service="CLI")
    except Exception as e:
        intellistock_logger.log(f"Error: {e}", "red", service="CLI")


def cmd_link_brokerage(conn):
    """Interactive: link a new brokerage account."""
    intellistock_logger.log("\n--- Link Brokerage Account ---", "cyan", service="CLI")
    try:
        btype = input("Brokerage type (alpaca/robinhood): ").strip().lower()
        if btype not in ("alpaca", "robinhood"):
            intellistock_logger.log("Invalid type. Must be 'alpaca' or 'robinhood'.", "yellow", service="CLI")
            return

        account_name = input("Account name (e.g. 'My Paper Trading'): ").strip()
        if not account_name:
            intellistock_logger.log("Account name is required.", "yellow", service="CLI")
            return

        if btype == "alpaca":
            key = input("Alpaca API Key ID: ").strip()
            secret = input("Alpaca Secret Key: ").strip()
            paper_input = input("Paper trading? (y/n, default y): ").strip().lower()
            paper = paper_input not in ("n", "no")
            if not key or not secret:
                intellistock_logger.log("Key and secret are required.", "yellow", service="CLI")
                return
            intellistock_logger.log("Validating Alpaca credentials...", "white", service="CLI")
            try:
                out = action_link_alpaca(conn, account_name, key, secret, paper)
                acct = out.get("account", {})
                intellistock_logger.log(f"  Alpaca account linked: {acct.get('account_name')}  (id={acct.get('id')})", "green", service="CLI")
                intellistock_logger.log(f"  Alpaca account number: {acct.get('alpaca_account_number', 'N/A')}", "white", service="CLI")
                intellistock_logger.log(f"  Mode: {'Paper' if paper else 'Live'}", "white", service="CLI")
            except ValueError as e:
                intellistock_logger.log(f"Failed to link: {e}", "red", service="CLI")

        elif btype == "robinhood":
            intellistock_logger.log("Robinhood linking method:", "cyan", service="CLI")
            intellistock_logger.log("  1) Paste access token + refresh token (recommended)", "white", service="CLI")
            intellistock_logger.log("  2) Login with email and password (interactive, handles MFA)", "white", service="CLI")
            method = input("Choose method (1/2): ").strip()

            if method == "1":
                access_token = input("Access token (paste, 'Bearer ' prefix will be stripped): ").strip()
                refresh_token = input("Refresh token: ").strip()
                device_token = input("Device token (optional, press Enter to generate): ").strip() or None
                if not access_token or not refresh_token:
                    intellistock_logger.log("access_token and refresh_token are required.", "yellow", service="CLI")
                    return
                intellistock_logger.log("Validating tokens and fetching accounts...", "white", service="CLI")
                try:
                    chosen = _cli_pick_robinhood_account(access_token, refresh_token, device_token)
                    if chosen is None:
                        return
                    out = action_link_robinhood_tokens(conn, account_name, access_token, refresh_token, device_token,
                                                      account_number=chosen)
                    acct = out.get("account", {})
                    intellistock_logger.log(f"  Robinhood account linked: {acct.get('account_name')}  (id={acct.get('id')})", "green", service="CLI")
                    intellistock_logger.log(f"  Robinhood account number: {acct.get('robinhood_account_number', 'N/A')}", "white", service="CLI")
                except ValueError as e:
                    intellistock_logger.log(f"Failed to link: {e}", "red", service="CLI")

            elif method == "2":
                _cmd_link_robinhood_password(conn, account_name)
            else:
                intellistock_logger.log("Invalid choice.", "yellow", service="CLI")
    except (EOFError, KeyboardInterrupt):
        intellistock_logger.log("\nCancelled.", "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log(f"Error: {e}", "red", service="CLI")


def _cli_pick_robinhood_account(access_token, refresh_token=None, device_token=None):
    """Fetch Robinhood accounts, display them, and interactively return the chosen account_number.
    Returns the selected account_number string, or None if the user cancelled."""
    try:
        result = action_fetch_robinhood_accounts(access_token, refresh_token, device_token)
        accounts = result.get("accounts") or []
    except ValueError as e:
        intellistock_logger.log(f"Failed to fetch accounts: {e}", "red", service="CLI")
        return None

    if not accounts:
        intellistock_logger.log("No accounts found with these credentials.", "yellow", service="CLI")
        return None

    # If only one account, auto-select
    if len(accounts) == 1:
        a = accounts[0]
        intellistock_logger.log(f"  Auto-selected account: {a['account_number']} ({a.get('account_type', '').capitalize()}  ${a.get('equity') or 0:,.2f})", "green", service="CLI")
        return a["account_number"]

    intellistock_logger.log("\nDetected Robinhood accounts:", "cyan", service="CLI")
    for i, a in enumerate(accounts, 1):
        equity = a.get("equity") or 0.0
        bp     = a.get("buying_power") or 0.0
        atype  = (a.get("account_type") or "").capitalize()
        intellistock_logger.log(
            f"  [{i}] {a['account_number']}   Type: {atype}   Equity: ${equity:,.2f}   Buying Power: ${bp:,.2f}",
            "white", service="CLI"
        )

    try:
        choice = input(f"Select account [1-{len(accounts)}]: ").strip()
        idx = int(choice) - 1
        if not (0 <= idx < len(accounts)):
            raise ValueError()
    except (ValueError, EOFError):
        intellistock_logger.log("Invalid selection, cancelled.", "yellow", service="CLI")
        return None

    chosen = accounts[idx]["account_number"]
    intellistock_logger.log(f"  Selected: {chosen}", "green", service="CLI")
    return chosen


def _cmd_link_robinhood_password(conn, account_name):
    """Interactive Robinhood login via email/password with MFA/challenge support."""
    import getpass
    from robinhood_engine import RobinhoodClient, RobinhoodSessionState, RobinhoodMFARequired, RobinhoodChallengeRequired, RobinhoodAPIError

    email = input("Robinhood email: ").strip()
    try:
        password = getpass.getpass("Password: ")
    except Exception:
        password = input("Password (visible): ").strip()

    if not email or not password:
        intellistock_logger.log("Email and password are required.", "yellow", service="CLI")
        return

    client = RobinhoodClient()
    challenge_response_id = None
    mfa_code = None

    for attempt in range(5):
        try:
            intellistock_logger.log("Logging in to Robinhood...", "white", service="CLI")
            client.login_password(
                username=email,
                password=password,
                mfa_code=mfa_code,
                challenge_response_id=challenge_response_id,
            )
            break  # success
        except RobinhoodMFARequired as e:
            intellistock_logger.log(f"MFA required (type: {e.mfa_type or 'app'}). Check your authenticator app.", "yellow", service="CLI")
            mfa_code = input("Enter MFA code: ").strip()
            challenge_response_id = None
        except RobinhoodChallengeRequired as e:
            cid = e.challenge_id
            intellistock_logger.log(f"SMS/email challenge required (id={cid}). Check your phone/email.", "yellow", service="CLI")
            code = input("Enter challenge code: ").strip()
            try:
                challenge_response_id = client.respond_to_challenge(cid, code)
            except Exception as ce:
                intellistock_logger.log(f"Challenge response failed: {ce}", "red", service="CLI")
                return
            mfa_code = None
        except RobinhoodAPIError as e:
            intellistock_logger.log(f"Login failed: {e.detail or str(e)}", "red", service="CLI")
            return
        except Exception as e:
            intellistock_logger.log(f"Login error: {e}", "red", service="CLI")
            return
    else:
        intellistock_logger.log("Too many login attempts.", "red", service="CLI")
        return

    if not client.state.access_token:
        intellistock_logger.log("Login did not return an access token.", "red", service="CLI")
        return

    intellistock_logger.log("Login successful. Fetching available accounts...", "green", service="CLI")
    chosen = _cli_pick_robinhood_account(
        client.state.access_token,
        client.state.refresh_token or "",
        client.state.device_token,
    )
    if chosen is None:
        return

    try:
        out = action_link_robinhood_tokens(
            conn,
            account_name,
            client.state.access_token,
            client.state.refresh_token or "",
            client.state.device_token,
            client.state.expires_in,
            client.state.obtained_at_epoch,
            account_number=chosen,
        )
        acct = out.get("account", {})
        intellistock_logger.log(f"  Robinhood account linked: {acct.get('account_name')}  (id={acct.get('id')})", "green", service="CLI")
        intellistock_logger.log(f"  Robinhood account number: {acct.get('robinhood_account_number', 'N/A')}", "white", service="CLI")
    except ValueError as e:
        intellistock_logger.log(f"Failed to save: {e}", "red", service="CLI")


def cmd_unlink_brokerage(conn, brokerage_id):
    """Remove a linked brokerage account."""
    if not brokerage_id:
        intellistock_logger.log("Usage: unlink-brokerage ID", "yellow", service="CLI")
        return
    try:
        out = action_list_brokerages(conn)
        accts = {a["id"]: a for a in out.get("accounts", []) if a.get("id") == brokerage_id}
        if not accts:
            intellistock_logger.log(f"No account found with id={brokerage_id}", "yellow", service="CLI")
            return
        acct = accts[brokerage_id]
        confirm = input(f"Remove '{acct.get('account_name')}' ({acct.get('brokerage_type').upper()})? (y/n): ").strip().lower()
        if confirm not in ("y", "yes"):
            intellistock_logger.log("Cancelled.", "yellow", service="CLI")
            return
        action_delete_brokerage(conn, brokerage_id)
        intellistock_logger.log(f"Removed brokerage account: {acct.get('account_name')}", "green", service="CLI")
    except (EOFError, KeyboardInterrupt):
        intellistock_logger.log("\nCancelled.", "yellow", service="CLI")
    except ValueError as e:
        intellistock_logger.log(str(e), "yellow", service="CLI")
    except Exception as e:
        intellistock_logger.log(f"Error: {e}", "red", service="CLI")


def main():
    default_port = os.environ.get('RETHINKDB_PORT', str(RETHINKDB_PORT))

    intellistock_logger.log("IntelliStock CLI (backend control via RethinkDB)", "cyan", service="CLI")
    try:
        prompt = (
            "RethinkDB host: enter IP address, or l=localhost, r=preset ({}) : ".format(RETHINKDB_PRESET_REMOTE)
        )
        host_input = input(prompt).strip().lower()
        if not host_input:
            host = RETHINKDB_HOST  # env or localhost
        elif host_input == 'l':
            host = 'localhost'
        elif host_input == 'r':
            host = RETHINKDB_PRESET_REMOTE
        else:
            host = host_input  # user typed an IP or hostname (already stripped)
    except (EOFError, KeyboardInterrupt):
        intellistock_logger.log("Bye.", "green", service="CLI")
        sys.exit(0)
    intellistock_logger.log("Connecting to RethinkDB at {} ...".format(host), "white", service="CLI")
    intellistock_logger.log("Type 'help' for commands.", "white", service="CLI")

    try:
        conn = get_conn(host=host, port=default_port)
    except Exception as e:
        intellistock_logger.log(f"Cannot connect to RethinkDB: {e}", "red", service="CLI")
        intellistock_logger.log("Check host and RETHINKDB_PORT (default 28015).", "yellow", service="CLI")
        sys.exit(1)

    def _reconnect_conn(max_attempts=3, delay=1.0):
        """
        Attempt to reconnect to RethinkDB up to max_attempts times.
        Returns True on success, False if all attempts fail.
        """
        nonlocal conn
        try:
            conn.close()
        except Exception:
            pass
        for attempt in range(1, max_attempts + 1):
            try:
                intellistock_logger.log(
                    f"RethinkDB connection lost. Reconnecting (attempt {attempt}/{max_attempts})...",
                    "yellow",
                    service="CLI",
                )
                conn = get_conn(host=host, port=default_port)
                intellistock_logger.log("Reconnected to RethinkDB.", "green", service="CLI")
                return True
            except Exception as e:
                if attempt == max_attempts:
                    intellistock_logger.log(
                        f"Failed to reconnect to RethinkDB after {max_attempts} attempts: {e}",
                        "red",
                        service="CLI",
                    )
                else:
                    time.sleep(delay)
        return False

    while True:
        try:
            line = input("intellistock> ").strip()
        except (EOFError, KeyboardInterrupt):
            intellistock_logger.log("Bye.", "green", service="CLI")
            break
        if not line:
            continue

        parts = line.split()
        cmd = (parts[0].lower() if parts else "")

        # Handle exit/quit directly so we can break the loop cleanly
        if cmd in ('exit', 'quit', 'q'):
            intellistock_logger.log("Bye.", "green", service="CLI")
            break

        def _handle_command():
            if cmd in ('help', 'h'):
                cmd_help()
            elif cmd in ('status', 's'):
                cmd_status(conn)
            elif cmd in ('tickers', 't'):
                cmd_tickers(conn)
            elif cmd == 'add-ticker':
                cmd_add_ticker(conn, parts[1:] if len(parts) > 1 else [])
            elif cmd == 'remove-ticker':
                cmd_remove_ticker(conn, parts[1] if len(parts) > 1 else "")
            elif cmd in ('prices', 'p'):
                cmd_prices(conn)
            elif cmd in ('history', 'ph'):
                # history [SYM] [N] -> ticker optional, N=limit default 30
                args = parts[1:]
                ticker_arg = None
                limit_arg = 30
                if len(args) >= 1:
                    if args[0].isdigit():
                        limit_arg = min(int(args[0]), 500)
                    else:
                        ticker_arg = args[0]
                        if len(args) >= 2 and args[1].isdigit():
                            limit_arg = min(int(args[1]), 500)
                cmd_history(conn, ticker=ticker_arg, limit=limit_arg)
            elif cmd in ('instances', 'i'):
                cmd_instances(conn)
            elif cmd == 'create-instance':
                args = parts[1:] if len(parts) > 1 else []
                id_arg = args[0] if len(args) > 0 else ""
                key_arg = args[1] if len(args) > 1 else ""
                secret_arg = args[2] if len(args) > 2 else ""
                granularity_arg = args[3] if len(args) > 3 else None
                cmd_create_instance(conn, id_arg, key_arg or None, secret_arg or None, granularity_arg)
            elif cmd == 'delete-instance':
                inst_arg = parts[1] if len(parts) > 1 else ""
                cmd_delete_instance(conn, inst_arg)
            elif cmd == 'add-stock':
                args = parts[1:] if len(parts) > 1 else []
                inst_arg = args[0] if len(args) > 0 else ""
                sym_arg = args[1] if len(args) > 1 else ""
                cmd_add_stock(conn, inst_arg, sym_arg)
            elif cmd == 'remove-stock':
                args = parts[1:] if len(parts) > 1 else []
                inst_arg = args[0] if len(args) > 0 else ""
                sym_arg = args[1] if len(args) > 1 else ""
                cmd_remove_stock(conn, inst_arg, sym_arg)
            elif cmd == 'start-instance':
                inst_arg = parts[1] if len(parts) > 1 else ""
                cmd_start_instance(conn, inst_arg)
            elif cmd == 'stop-instance':
                inst_arg = parts[1] if len(parts) > 1 else ""
                cmd_stop_instance(conn, inst_arg)
            elif cmd == 'terminate-price':
                cmd_terminate_price(conn)
            elif cmd == 'terminate-discover':
                cmd_terminate_discover(conn)
            elif cmd == 'start-broker':
                cmd_start_broker(conn)
            elif cmd in ('strategies', 'st'):
                inst_arg = parts[1] if len(parts) > 1 else None
                cmd_strategies(conn, instance_filter=inst_arg)
            elif cmd == 'create-strategy':
                cmd_create_strategy(conn)
            elif cmd == 'edit-strategy':
                sid_arg = parts[1] if len(parts) > 1 else ""
                cmd_edit_strategy(conn, sid_arg)
            elif cmd == 'delete-strategy':
                sid_arg = parts[1] if len(parts) > 1 else ""
                cmd_delete_strategy(conn, sid_arg)
            elif cmd == 'link-strategy':
                args = parts[1:] if len(parts) > 1 else []
                if len(args) < 2:
                    intellistock_logger.log("Usage: link-strategy INSTANCE_ID STRATEGY_ID", "yellow", service="CLI")
                    intellistock_logger.log("  Example: link-strategy 12 5", "white", service="CLI")
                else:
                    cmd_link_strategy(conn, args[0], args[1])
            elif cmd in ('backtests', 'bt'):
                cmd_list_backtests(conn)
            elif cmd == 'create-backtest':
                cmd_create_backtest(conn)
            elif cmd == 'delete-backtest':
                bid_arg = parts[1] if len(parts) > 1 else ""
                cmd_delete_backtest(conn, bid_arg)
            elif cmd == 'stop-backtest':
                bid_arg = parts[1] if len(parts) > 1 else ""
                cmd_stop_backtest(conn, bid_arg)
            elif cmd == 'stop-all-backtests':
                cmd_stop_all_backtests(conn)
            elif cmd == 'pause-backtest':
                bid_arg = parts[1] if len(parts) > 1 else ""
                cmd_pause_backtest(conn, bid_arg)
            elif cmd == 'resume-backtest':
                bid_arg = parts[1] if len(parts) > 1 else ""
                cmd_resume_backtest(conn, bid_arg)
            elif cmd == 'summarize-backtest':
                bid_arg = parts[1] if len(parts) > 1 else ""
                cmd_summarize_backtest(conn, bid_arg)
            elif cmd == 'backtest-logs':
                bid_arg = parts[1] if len(parts) > 1 else ""
                cmd_backtest_logs(conn, bid_arg)
            elif cmd == 'graph-backtest':
                bid_arg = parts[1] if len(parts) > 1 else ""
                cmd_graph_backtest(conn, bid_arg)
            elif cmd == 'agent':
                agent_sub = (parts[1] if len(parts) > 1 else "").strip().lower()
                if agent_sub == 'start':
                    try:
                        special_request = " ".join(parts[2:]).strip() if len(parts) > 2 else None
                        action_agent_control_set(conn, running=True, special_request=special_request)
                        intellistock_logger.log("AI backtesting agent started. The agent service will pick up and run.", "green", service="CLI")
                        if special_request:
                            intellistock_logger.log("  Special request: %s" % special_request, "cyan", service="CLI")
                    except ReqlDriverError as e:
                        if "Connection is closed" in str(e):
                            raise
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                    except Exception as e:
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                elif agent_sub == 'stop':
                    try:
                        action_agent_control_set(conn, running=False)
                        intellistock_logger.log("AI backtesting agent stopped.", "green", service="CLI")
                    except ReqlDriverError as e:
                        if "Connection is closed" in str(e):
                            raise
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                    except Exception as e:
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                elif agent_sub == 'pause':
                    try:
                        action_agent_control_set(conn, paused=True)
                        intellistock_logger.log("AI backtesting agent paused (current backtest will pause until agent resume).", "green", service="CLI")
                    except ReqlDriverError as e:
                        if "Connection is closed" in str(e):
                            raise
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                    except Exception as e:
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                elif agent_sub == 'resume':
                    try:
                        action_agent_control_set(conn, paused=False)
                        intellistock_logger.log("AI backtesting agent resumed.", "green", service="CLI")
                    except ReqlDriverError as e:
                        if "Connection is closed" in str(e):
                            raise
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                    except Exception as e:
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                elif agent_sub == 'resume-timer':
                    try:
                        intellistock_logger.log("Schedule agent to resume after specified time.", "cyan", service="CLI")
                        days = input("  Days (0-365): ").strip()
                        hours = input("  Hours (0-23): ").strip()
                        minutes = input("  Minutes (0-59): ").strip()
                        seconds = input("  Seconds (0-59): ").strip()
                        days = int(days) if days.isdigit() else 0
                        hours = int(hours) if hours.isdigit() else 0
                        minutes = int(minutes) if minutes.isdigit() else 0
                        seconds = int(seconds) if seconds.isdigit() else 0
                        if days == 0 and hours == 0 and minutes == 0 and seconds == 0:
                            intellistock_logger.log("  Error: Total time must be greater than 0", "red", service="CLI")
                        else:
                            from datetime import datetime, timedelta
                            total_seconds = (days * 86400) + (hours * 3600) + (minutes * 60) + seconds
                            resume_at = datetime.utcnow() + timedelta(seconds=total_seconds)
                            out = action_resume_timer(conn, days=days, hours=hours, minutes=minutes, seconds=seconds)
                            intellistock_logger.log("  Agent scheduled to resume at: %s UTC" % resume_at.strftime("%Y-%m-%d %H:%M:%S"), "green", service="CLI")
                            intellistock_logger.log("  Time remaining: %d days, %d hours, %d minutes, %d seconds" % (days, hours, minutes, seconds), "cyan", service="CLI")
                    except ValueError as e:
                        intellistock_logger.log("  Error: %s" % str(e), "red", service="CLI")
                    except ReqlDriverError as e:
                        if "Connection is closed" in str(e):
                            raise
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                    except Exception as e:
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                elif agent_sub == 'status':
                    try:
                        out = action_agent_control_get(conn)
                        running = out.get("running", False)
                        paused = out.get("paused", False)
                        count = out.get("count_today", 0)
                        last = out.get("last_run_date") or "-"
                        resume_at = out.get("resume_at")
                        intellistock_logger.log("  Agent running: %s" % running, "cyan", service="CLI")
                        intellistock_logger.log("  Agent paused: %s" % paused, "cyan", service="CLI")
                        intellistock_logger.log("  Backtests today: %s" % count, "white", service="CLI")
                        intellistock_logger.log("  Last run date: %s" % last, "white", service="CLI")
                        if out.get("special_request"):
                            intellistock_logger.log("  Special request: %s" % out.get("special_request"), "cyan", service="CLI")
                        if resume_at:
                            from datetime import datetime
                            try:
                                resume_dt = datetime.fromisoformat(resume_at.replace("Z", "+00:00"))
                                now = datetime.utcnow().replace(tzinfo=resume_dt.tzinfo) if resume_dt.tzinfo else datetime.utcnow()
                                delta = resume_dt - now
                                total_sec = int(delta.total_seconds())
                                if total_sec > 0:
                                    intellistock_logger.log("  Scheduled resume: %s UTC (in %d:%02d:%02d:%02d)" % (
                                        resume_dt.strftime("%Y-%m-%d %H:%M:%S"),
                                        total_sec // 86400, (total_sec % 86400) // 3600,
                                        (total_sec % 3600) // 60, total_sec % 60
                                    ), "cyan", service="CLI")
                                else:
                                    intellistock_logger.log("  Scheduled resume: %s UTC (time reached)" % resume_dt.strftime("%Y-%m-%d %H:%M:%S"), "yellow", service="CLI")
                            except Exception:
                                intellistock_logger.log("  Scheduled resume: %s" % resume_at, "cyan", service="CLI")
                    except ReqlDriverError as e:
                        if "Connection is closed" in str(e):
                            raise
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                    except Exception as e:
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                elif agent_sub == 'best':
                    try:
                        best = action_agent_get_best(conn)
                        if not best:
                            intellistock_logger.log("  No best strategy set yet. Run the agent and keep a strategy to set one.", "yellow", service="CLI")
                        else:
                            intellistock_logger.log("  Best strategy (tag Best)", "cyan", service="CLI")
                            intellistock_logger.log("  ID: %s" % best.get("id"), "white", service="CLI")
                            intellistock_logger.log("  Name: %s" % best.get("name"), "white", service="CLI")
                            intellistock_logger.log("  Overall profit: %s" % best.get("overall_profit"), "white", service="CLI")
                            intellistock_logger.log("  PnL %%: %s" % best.get("pnl_percent"), "white", service="CLI")
                            intellistock_logger.log("  Updated at: %s" % best.get("updated_at"), "white", service="CLI")
                            subs = best.get("strategies") or []
                            intellistock_logger.log("  Sub-strategies (%d):" % len(subs), "cyan", service="CLI")
                            for i, s in enumerate(subs):
                                cfg = (s.get("config") or {})
                                cond = (s.get("conditions") or {})
                                intellistock_logger.log(
                                    "    %d. %s (weight=%.2f, config=%s, conditions=%s)" % (i + 1, s.get("strategy"), float(s.get("weight", 0.5)), cfg, cond),
                                    "white", service="CLI",
                                )
                    except ReqlDriverError as e:
                        if "Connection is closed" in str(e):
                            raise
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                    except Exception as e:
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                elif agent_sub == 'restart':
                    try:
                        special_request = " ".join(parts[2:]).strip() if len(parts) > 2 else None
                        intellistock_logger.log("Restarting AI backtesting agent (stop → wait 5s → start)...", "cyan", service="CLI")
                        result = action_agent_restart(conn, special_request=special_request)
                        intellistock_logger.log("AI backtesting agent restarted.", "green", service="CLI")
                        if special_request:
                            intellistock_logger.log("  Special request: %s" % special_request, "cyan", service="CLI")
                    except ReqlDriverError as e:
                        if "Connection is closed" in str(e):
                            raise
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                    except Exception as e:
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                else:
                    intellistock_logger.log("Usage: agent start | agent stop | agent restart | agent pause | agent resume | agent resume-timer | agent status | agent best", "yellow", service="CLI")
            elif cmd == 'digest':
                digest_sub = (parts[1] if len(parts) > 1 else "").strip().lower()
                if digest_sub == 'start':
                    try:
                        action_digest_control_set(conn, running=True)
                        intellistock_logger.log("Daily digest engine started. Briefs will be sent at 6am and 6pm UTC to #briefs.", "green", service="CLI")
                    except ReqlDriverError as e:
                        if "Connection is closed" in str(e):
                            raise
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                    except Exception as e:
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                elif digest_sub == 'stop':
                    try:
                        action_digest_control_set(conn, running=False)
                        intellistock_logger.log("Daily digest engine stopped.", "green", service="CLI")
                    except ReqlDriverError as e:
                        if "Connection is closed" in str(e):
                            raise
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                    except Exception as e:
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                elif digest_sub == 'send-now':
                    try:
                        action_digest_trigger_send_now(conn)
                        intellistock_logger.log("Digest send-now triggered (engine started if needed). Brief should appear in #briefs shortly.", "green", service="CLI")
                    except ReqlDriverError as e:
                        if "Connection is closed" in str(e):
                            raise
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                    except Exception as e:
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                elif digest_sub == 'status':
                    try:
                        out = action_digest_control_get(conn)
                        intellistock_logger.log("  Digest running: %s" % out.get("running", True), "cyan", service="CLI")
                        intellistock_logger.log("  Send now pending: %s" % out.get("send_now", False), "white", service="CLI")
                        intellistock_logger.log("  Last sent: %s" % (out.get("last_sent_at") or "—"), "white", service="CLI")
                        intellistock_logger.log("  Last morning: %s" % (out.get("last_morning_at") or "—"), "white", service="CLI")
                        intellistock_logger.log("  Last evening: %s" % (out.get("last_evening_at") or "—"), "white", service="CLI")
                    except ReqlDriverError as e:
                        if "Connection is closed" in str(e):
                            raise
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                    except Exception as e:
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                else:
                    intellistock_logger.log("Usage: digest start | digest stop | digest send-now | digest status", "yellow", service="CLI")
            elif cmd == 'nexus':
                nexus_sub = (parts[1] if len(parts) > 1 else "").strip().lower()
                if nexus_sub == 'start':
                    try:
                        start_phase, end_phase = None, None
                        for arg in (parts[2:] if len(parts) > 2 else []):
                            if arg.startswith("--start="):
                                start_phase = _parse_nexus_phase_arg(arg.split("=", 1)[1] if "=" in arg else None)
                            elif arg.startswith("--end="):
                                end_phase = _parse_nexus_phase_arg(arg.split("=", 1)[1] if "=" in arg else None)
                        action_nexus_control_set(conn, running=True, start_phase=start_phase, end_phase=end_phase)
                        msg = "Graph Nexus service start requested. Server will spawn the process."
                        if start_phase is not None or end_phase is not None:
                            msg += " Phases %s-%s will run (even if graph already built)." % (
                                _format_nexus_phase_selector(start_phase or 1, str(start_phase or 1)),
                                _format_nexus_phase_selector(end_phase or 14, str(end_phase or 14)),
                            )
                        intellistock_logger.log(msg, "green", service="CLI")
                    except ReqlDriverError as e:
                        if "Connection is closed" in str(e):
                            raise
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                    except Exception as e:
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                elif nexus_sub == 'stop':
                    try:
                        action_nexus_control_set(conn, running=False)
                        intellistock_logger.log("Graph Nexus service stop requested. Nexus will save progress and exit.", "green", service="CLI")
                    except ReqlDriverError as e:
                        if "Connection is closed" in str(e):
                            raise
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                    except Exception as e:
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                elif nexus_sub == 'status':
                    try:
                        out = action_nexus_status(conn)
                        from interactive_utils import format_nexus_status_for_cli
                        formatted = format_nexus_status_for_cli(out)
                        for line in formatted.split("\n"):
                            intellistock_logger.log(line, "white", service="CLI")
                    except ReqlDriverError as e:
                        if "Connection is closed" in str(e):
                            raise
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                    except Exception as e:
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                elif nexus_sub == 'rebuild':
                    try:
                        intellistock_logger.log("This will reset the entire graph (Neo4j), progress (RethinkDB), and cached CSVs. Next nexus start will rebuild from phase 1.", "yellow", service="CLI")
                        confirm = input("Rebuild graph from scratch and clear cache? (y/n): ").strip().lower()
                        if confirm in ('y', 'yes'):
                            out = action_nexus_rebuild(conn, destructive=True)
                            if out.get("success"):
                                intellistock_logger.log(out.get("message", "Done."), "green", service="CLI")
                                if out.get("neo4j_cleared"):
                                    intellistock_logger.log("  Neo4j graph cleared.", "white", service="CLI")
                                if out.get("rethinkdb_cleared"):
                                    intellistock_logger.log("  RethinkDB progress cleared.", "white", service="CLI")
                                if out.get("cache_cleared"):
                                    intellistock_logger.log("  Cache (CSVs, sec_edgar_filings) cleared.", "white", service="CLI")
                            else:
                                intellistock_logger.log(out.get("message", "Rebuild reset failed."), "red", service="CLI")
                                if out.get("error"):
                                    intellistock_logger.log("  %s" % out.get("error"), "red", service="CLI")
                        else:
                            intellistock_logger.log("Rebuild cancelled.", "cyan", service="CLI")
                    except ReqlDriverError as e:
                        if "Connection is closed" in str(e):
                            raise
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                    except Exception as e:
                        intellistock_logger.log("Error: %s" % e, "red", service="CLI")
                else:
                    intellistock_logger.log("Usage: nexus start [--start=N] [--end=M] | nexus stop | nexus status | nexus rebuild", "yellow", service="CLI")
            elif cmd == 'trends':
                sub = (parts[1] if len(parts) > 1 else "").strip().lower()
                if sub in ("end", "delete"):
                    cmd_trends(conn, sub=sub, arg=parts[2] if len(parts) > 2 else None)
                else:
                    # sub might be an instance_id filter, or empty
                    inst = sub if sub and sub not in ("end", "delete") else None
                    cmd_trends(conn, instance_id=inst)

            elif cmd == 'discovered':
                sub = (parts[1] if len(parts) > 1 else "").strip().lower()
                if sub == "remove":
                    cmd_discovered(conn, sub="remove",
                                   instance_id=parts[2] if len(parts) > 2 else None,
                                   ticker=parts[3] if len(parts) > 3 else None)
                else:
                    inst = sub if sub else None
                    cmd_discovered(conn, instance_id=inst)

            elif cmd == 'nexus-config':
                inst = parts[1] if len(parts) > 1 else None
                k = parts[2] if len(parts) > 2 else None
                v = parts[3] if len(parts) > 3 else None
                cmd_nexus_config(conn, instance_id=inst, key=k, value=v)

            elif cmd in ('brokerages', 'bk'):
                cmd_brokerages(conn)
            elif cmd == 'link-brokerage':
                cmd_link_brokerage(conn)
            elif cmd == 'unlink-brokerage':
                bid_arg = parts[1] if len(parts) > 1 else ""
                cmd_unlink_brokerage(conn, bid_arg)

            else:
                intellistock_logger.log(f"Unknown command: {cmd}. Type 'help' for commands.", "yellow", service="CLI")

        try:
            _handle_command()
        except ReqlDriverError as e:
            if "Connection is closed" in str(e) and _reconnect_conn():
                try:
                    _handle_command()
                except ReqlDriverError as e2:
                    intellistock_logger.log("Error: %s" % e2, "red", service="CLI")
            else:
                intellistock_logger.log("Error: %s" % e, "red", service="CLI")

    try:
        conn.close()
    except Exception:
        # Connection may already be closed or broken; ignore errors on shutdown
        pass


if __name__ == '__main__':
    main()
