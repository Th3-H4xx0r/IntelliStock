"""Chatbot tool catalog.

Curated set of tools the assistant is allowed to call. Each tool wraps an
existing ``action_*`` function or has a render-only effect. Tools are
classified into one of three safety tiers:

  * ``safe``        — read-only or idempotent. Auto-executes by default.
  * ``write``       — creates / edits / starts something. Requires UI confirm.
  * ``destructive`` — deletes data, halts services, places live orders.
                       Requires UI confirm + typed acknowledgement.

There are also ``render_*`` tools and ``navigate_to`` which never touch the
backend; they exist so the LLM can emit chart / table / stat blocks or ask
the frontend to navigate to a route as part of a response.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from interactive_utils import (  # type: ignore[import-not-found]
    action_list_models,
    action_list_brokerages,
    action_instances,
    action_get_instance,
    action_strategies,
    action_get_strategy,
    action_list_backtests,
    action_summarize_backtest,
    action_graph_backtest_data,
    action_get_backtest_status,
    action_tickers,
    action_prices,
    action_engines_status,
    action_get_portfolio_history,
    action_create_instance,
    action_add_stock,
    action_link_brokerage_to_instance,
    action_link_strategy,
    action_unlink_strategy,
    action_start_instance,
    action_stop_instance,
    action_create_backtest,
    action_delete_instance,
    action_delete_backtest,
    action_stop_backtest,
    action_submit_live_command,
    action_discover_control_set,
    action_digest_control_set,
)


# ── Tool definition ────────────────────────────────────────────────────────


class Tool:
    """A single registered tool. ``executor`` receives a context dict
    (``conn``, ``user``) plus the validated argument dict and returns either
    a JSON-serialisable result or a structured ``block`` dict for render-only
    tools (``render_chart`` etc.)."""

    __slots__ = (
        "name",
        "description",
        "parameters",
        "safety",
        "executor",
        "render_only",
    )

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        safety: str,
        executor: Callable[[Dict[str, Any], Dict[str, Any]], Any],
        render_only: bool = False,
    ):
        if safety not in ("safe", "write", "destructive"):
            raise ValueError(f"invalid safety tier: {safety}")
        self.name = name
        self.description = description
        self.parameters = parameters
        self.safety = safety
        self.executor = executor
        self.render_only = render_only

    def to_openai_tool(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_gemini_declaration(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


# ── Schema helpers ─────────────────────────────────────────────────────────


def _obj(props: Dict[str, Dict[str, Any]], required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required or [],
    }


def _str(desc: str) -> Dict[str, Any]:
    return {"type": "string", "description": desc}


def _num(desc: str) -> Dict[str, Any]:
    return {"type": "number", "description": desc}


def _bool(desc: str) -> Dict[str, Any]:
    return {"type": "boolean", "description": desc}


def _arr(items: Dict[str, Any], desc: str) -> Dict[str, Any]:
    return {"type": "array", "items": items, "description": desc}


# ── Executors ──────────────────────────────────────────────────────────────


def _exe(action, *fields):
    """Build an executor that calls ``action(conn, **kwargs)`` with the named
    fields pulled from the LLM's argument dict (missing fields → None)."""

    def _run(ctx: Dict[str, Any], args: Dict[str, Any]) -> Any:
        kwargs = {f: args.get(f) for f in fields if args.get(f) is not None}
        return action(ctx["conn"], **kwargs)

    return _run


def _exe_noargs(action):
    def _run(ctx: Dict[str, Any], args: Dict[str, Any]) -> Any:
        return action(ctx["conn"])

    return _run


def _exe_render_chart(ctx: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "block",
        "block": {
            "type": "chart",
            "chart_type": args.get("chart_type") or "area",
            "title": args.get("title"),
            "series": args.get("series") or [],
            "options": args.get("options") or {},
            "x_axis_type": args.get("x_axis_type") or "category",
        },
    }


def _exe_render_table(ctx: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "block",
        "block": {
            "type": "table",
            "title": args.get("title"),
            "headers": args.get("headers") or [],
            "rows": args.get("rows") or [],
        },
    }


def _exe_render_stat(ctx: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "block",
        "block": {
            "type": "stat",
            "label": args.get("label") or "",
            "value": args.get("value") or "",
            "detail": args.get("detail"),
            "trend": args.get("trend"),
        },
    }


def _exe_render_portfolio_chart(ctx: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    """Render the dashboard-style portfolio equity-curve chart. Refetches
    history server-side (60-second cache) so the LLM only has to emit the
    small (brokerage_id, range) arg pair — never a full series."""
    brokerage_id = args.get("brokerage_id")
    if not brokerage_id:
        return {"ok": False, "error": "brokerage_id is required"}
    range_str = str(args.get("range") or "1M").upper()
    history = action_get_portfolio_history(ctx["conn"], brokerage_id, range_str=range_str)
    if not isinstance(history, dict):
        history = {}
    return {
        "type": "block",
        "block": {
            "type": "portfolio",
            "title": args.get("title") or history.get("account_name") or "Portfolio",
            "range": range_str,
            "timestamps": history.get("timestamps") or [],
            "values": history.get("values") or [],
            "current_value": history.get("current_value"),
            "open_value": history.get("open_value"),
            "change_abs": history.get("change_abs"),
            "change_pct": history.get("change_pct"),
        },
    }


def _exe_get_price_history(ctx: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch close-price history for one or more tickers from the configured
    market-data provider. Range chooses the granularity bucket
    automatically (1D=5min, 1W=10min, 1M=hourly, 3M/YTD/1Y=daily, ALL=weekly)."""
    symbols = args.get("symbols") or args.get("symbol") or ""
    if isinstance(symbols, list):
        symbols = ",".join(str(s) for s in symbols)
    symbols = str(symbols).strip()
    if not symbols:
        raise ValueError("symbols is required (single ticker or comma-separated list)")
    range_str = (args.get("range") or "1D")
    # Local import to avoid a circular import on cold start (api.main imports chatbot).
    from api.main import fetch_symbol_historicals  # type: ignore[import-not-found]
    return fetch_symbol_historicals(symbols, range_str)


def _exe_render_price_chart(ctx: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    """Render a polished price chart for one or more tickers. Refetches the
    history server-side so the LLM only has to emit (symbols, range) — never
    a multi-kilobyte series payload through its own context (which would
    eat its token budget on reasoning models and produce empty replies)."""
    symbols = args.get("symbols") or args.get("symbol") or ""
    if isinstance(symbols, list):
        symbols = ",".join(str(s) for s in symbols)
    symbols = str(symbols).strip()
    if not symbols:
        return {"ok": False, "error": "symbols is required"}
    range_str = str(args.get("range") or "1D").upper()
    from api.main import fetch_symbol_historicals  # type: ignore[import-not-found]
    history = fetch_symbol_historicals(symbols, range_str)
    series_in = []
    results = history.get("results") or {}
    for sym, points in results.items():
        if not isinstance(points, list):
            continue
        series_in.append({"symbol": sym, "points": points})
    return {
        "type": "block",
        "block": {
            "type": "price",
            "title": args.get("title") or (
                ", ".join(results.keys()) + f" · {history.get('range') or range_str}"
                if results else f"Price history · {range_str}"
            ),
            "range": history.get("range") or range_str,
            "series": series_in,
        },
    }


def _exe_navigate(ctx: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    route = str(args.get("route") or "").strip()
    if not route.startswith("/") or route.startswith("//") or route.startswith("/\\"):
        return {"ok": False, "error": "route must be a relative internal path starting with /"}
    return {"type": "navigate", "route": route, "ok": True}


def _exe_get_portfolio_history(ctx: Dict[str, Any], args: Dict[str, Any]):
    bid = args.get("brokerage_id")
    rng = (args.get("range") or "1M").upper()
    if not bid:
        raise ValueError("brokerage_id is required")
    return action_get_portfolio_history(ctx["conn"], bid, range_str=rng)


def _exe_get_instance(ctx, args):
    iid = args.get("instance_id")
    if not iid:
        raise ValueError("instance_id is required")
    return action_get_instance(ctx["conn"], iid)


def _require_str(args, key):
    v = args.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        raise ValueError(f"argument {key!r} is required")
    return v if not isinstance(v, str) else v.strip()


def _exe_create_instance(ctx, args):
    iid = _require_str(args, "id")
    # Convert granularity to int seconds. action_create_instance expects the
    # kwarg `granularity_time_increment` (int seconds, default 60).
    g_raw = args.get("granularity")
    try:
        g_int = int(str(g_raw)) if g_raw is not None else 60
    except (TypeError, ValueError):
        g_int = 60
    return action_create_instance(
        ctx["conn"],
        iid,
        name=args.get("name"),
        granularity_time_increment=g_int,
        run_command=False,
        # action_create_instance accepts only ("ai", "user"); chatbot acts on
        # behalf of the user.
        created_by="user",
    )


def _exe_add_stock(ctx, args):
    return action_add_stock(ctx["conn"], _require_str(args, "instance_id"), _require_str(args, "symbol"))


def _exe_link_brokerage(ctx, args):
    return action_link_brokerage_to_instance(
        ctx["conn"], _require_str(args, "instance_id"), args.get("brokerage_id"),
    )


def _exe_link_strategy(ctx, args):
    sid = args.get("strategy_id")
    if sid is None:
        raise ValueError("argument 'strategy_id' is required")
    try:
        sid_int = int(sid)
    except (TypeError, ValueError):
        raise ValueError(f"strategy_id must be an integer (got {sid!r})")
    return action_link_strategy(ctx["conn"], _require_str(args, "instance_id"), sid_int)


def _exe_start_instance(ctx, args):
    return action_start_instance(ctx["conn"], _require_str(args, "instance_id"))


def _exe_stop_instance(ctx, args):
    return action_stop_instance(ctx["conn"], _require_str(args, "instance_id"))


def _exe_create_backtest(ctx, args):
    # action_create_backtest's granularity kwarg is `granularity_sec` (int).
    g_raw = args.get("granularity")
    try:
        g_sec = int(str(g_raw)) if g_raw is not None else 60
    except (TypeError, ValueError):
        g_sec = 60
    return action_create_backtest(
        ctx["conn"],
        instance_id=args.get("instance_id"),
        stocks=args.get("stocks") or None,
        start_date=_require_str(args, "start_date"),
        end_date=_require_str(args, "end_date"),
        granularity_sec=g_sec,
        initial_cash=float(args.get("initial_cash") or 100000.0),
    )


def _exe_get_backtest_summary(ctx, args):
    return action_summarize_backtest(ctx["conn"], _require_str(args, "backtest_id"))


def _exe_get_backtest_graph_data(ctx, args):
    return action_graph_backtest_data(ctx["conn"], _require_str(args, "backtest_id"))


def _exe_get_backtest_status(ctx, args):
    return action_get_backtest_status(ctx["conn"], _require_str(args, "backtest_id"))


def _exe_delete_instance(ctx, args):
    return action_delete_instance(
        ctx["conn"],
        _require_str(args, "instance_id"),
        force=bool(args.get("force") or False),
    )


def _exe_delete_backtest(ctx, args):
    return action_delete_backtest(ctx["conn"], _require_str(args, "backtest_id"))


def _exe_stop_backtest(ctx, args):
    return action_stop_backtest(ctx["conn"], _require_str(args, "backtest_id"))


def _exe_live_command(ctx, args):
    cmd_type = (args.get("type") or "").strip().lower()
    if cmd_type not in ("halt", "close_position", "submit_order"):
        raise ValueError("type must be one of: halt | close_position | submit_order")
    payload = args.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if cmd_type == "close_position" and not payload.get("symbol"):
        raise ValueError("close_position requires payload.symbol")
    if cmd_type == "submit_order":
        for k in ("symbol", "side", "qty"):
            if not payload.get(k):
                raise ValueError(f"submit_order requires payload.{k}")
    submitted_by = (ctx.get("user") or {}).get("username") or "chatbot"
    return action_submit_live_command(
        ctx["conn"],
        _require_str(args, "instance_id"),
        cmd_type,
        payload,
        submitted_by=submitted_by,
    )


def _exe_engine_control(ctx, args):
    engine = (args.get("engine") or "").lower()
    running = bool(args.get("running"))
    if engine == "discover":
        return action_discover_control_set(ctx["conn"], running=running)
    if engine == "digest":
        return action_digest_control_set(ctx["conn"], running=running)
    raise ValueError(f"engine must be 'discover' or 'digest' (got {engine!r})")


# ── Tool registry ──────────────────────────────────────────────────────────


_TOOLS: List[Tool] = [
    # ── Tier 1: read-only ──────────────────────────────────────────────
    Tool(
        name="list_models",
        description="List all configured LLM models the user has saved.",
        parameters=_obj({}),
        safety="safe",
        executor=lambda ctx, a: {"models": action_list_models(ctx["conn"])},
    ),
    Tool(
        name="list_brokerages",
        description="List all linked brokerage accounts with masked credentials.",
        parameters=_obj({}),
        safety="safe",
        executor=lambda ctx, a: {"brokerages": action_list_brokerages(ctx["conn"])},
    ),
    Tool(
        name="list_instances",
        description="List all autonomous trading instances (status, linked strategy, linked brokerage, max usage).",
        parameters=_obj({}),
        safety="safe",
        executor=lambda ctx, a: {"instances": action_instances(ctx["conn"])},
    ),
    Tool(
        name="get_instance",
        description="Fetch a single instance by id with its full live config.",
        parameters=_obj({"instance_id": _str("Instance id")}, required=["instance_id"]),
        safety="safe",
        executor=_exe_get_instance,
    ),
    Tool(
        name="list_strategies",
        description="List saved strategies (id, name, type, link counts).",
        parameters=_obj({}),
        safety="safe",
        executor=lambda ctx, a: {"strategies": action_strategies(ctx["conn"])},
    ),
    Tool(
        name="get_strategy",
        description="Fetch a single strategy by id (name + strategies array).",
        parameters=_obj({"strategy_id": _num("Strategy id")}, required=["strategy_id"]),
        safety="safe",
        executor=lambda ctx, a: action_get_strategy(ctx["conn"], int(a["strategy_id"])),
    ),
    Tool(
        name="list_backtests",
        description="List backtests (most recent first). Filter by instance_id if provided.",
        parameters=_obj({
            "instance_id": _str("Optional: filter to one instance"),
            "page": _num("Page number (default 1)"),
            "per_page": _num("Items per page (default 20, max 100)"),
        }),
        safety="safe",
        executor=lambda ctx, a: action_list_backtests(
            ctx["conn"],
            instance_id=a.get("instance_id"),
            page=int(a.get("page") or 1),
            per_page=min(int(a.get("per_page") or 20), 100),
        ),
    ),
    Tool(
        name="get_backtest_summary",
        description="Get the high-level summary of a finished backtest (P&L, win rate, sharpe, drawdown, trades).",
        parameters=_obj({"backtest_id": _str("Backtest id")}, required=["backtest_id"]),
        safety="safe",
        executor=_exe_get_backtest_summary,
    ),
    Tool(
        name="get_backtest_graph_data",
        description="Get equity-curve and trade-marker data for a backtest, suitable for rendering a chart block.",
        parameters=_obj({"backtest_id": _str("Backtest id")}, required=["backtest_id"]),
        safety="safe",
        executor=_exe_get_backtest_graph_data,
    ),
    Tool(
        name="get_backtest_status",
        description="Get current status / progress / queue position of a running or queued backtest.",
        parameters=_obj({"backtest_id": _str("Backtest id")}, required=["backtest_id"]),
        safety="safe",
        executor=_exe_get_backtest_status,
    ),
    Tool(
        name="list_tickers",
        description="List the tickers currently being watched at the global level.",
        parameters=_obj({}),
        safety="safe",
        executor=lambda ctx, a: {"tickers": action_tickers(ctx["conn"])},
    ),
    Tool(
        name="get_prices",
        description="Get current cached prices for all watched tickers.",
        parameters=_obj({}),
        safety="safe",
        executor=lambda ctx, a: {"prices": action_prices(ctx["conn"])},
    ),
    Tool(
        name="get_engines_status",
        description="Get the status of all background engines (price, discover, broker, nexus, agent, digest).",
        parameters=_obj({}),
        safety="safe",
        executor=lambda ctx, a: action_engines_status(ctx["conn"]),
    ),
    Tool(
        name="get_portfolio_history",
        description="Equity-curve history for a brokerage, ready for a chart block. range one of: 1D, 1W, 1M, 3M, YTD, 1Y, ALL.",
        parameters=_obj({
            "brokerage_id": _str("Brokerage account id"),
            "range": _str("Range code: 1D | 1W | 1M | 3M | YTD | 1Y | ALL (default 1M)"),
        }, required=["brokerage_id"]),
        safety="safe",
        executor=_exe_get_portfolio_history,
    ),

    # ── Tier 2: write (require UI confirm) ─────────────────────────────
    Tool(
        name="create_instance",
        description="Create a new autonomous trading instance. Strategy / brokerage are linked separately.",
        parameters=_obj({
            "id": _str("Unique short id (letters/digits/dash/underscore, e.g. 'momentum-live')"),
            "name": _str("Optional display name"),
            "granularity": _str("Cycle cadence in seconds: '60' | '300' | '900' | '3600' (default 60)"),
        }, required=["id"]),
        safety="write",
        executor=_exe_create_instance,
    ),
    Tool(
        name="add_ticker_to_instance",
        description="Add a stock symbol to an instance's watched list.",
        parameters=_obj({
            "instance_id": _str("Instance id"),
            "symbol": _str("Stock ticker (e.g. AAPL)"),
        }, required=["instance_id", "symbol"]),
        safety="write",
        executor=_exe_add_stock,
    ),
    Tool(
        name="link_brokerage_to_instance",
        description="Link an existing brokerage account to an instance for live order execution.",
        parameters=_obj({
            "instance_id": _str("Instance id"),
            "brokerage_id": _str("Brokerage id (or null to unlink)"),
        }, required=["instance_id"]),
        safety="write",
        executor=_exe_link_brokerage,
    ),
    Tool(
        name="link_strategy_to_instance",
        description="Link an existing strategy to an instance.",
        parameters=_obj({
            "instance_id": _str("Instance id"),
            "strategy_id": _num("Strategy id"),
        }, required=["instance_id", "strategy_id"]),
        safety="write",
        executor=_exe_link_strategy,
    ),
    Tool(
        name="start_instance",
        description="Start an instance (boots its broker subprocess and begins live cycles).",
        parameters=_obj({"instance_id": _str("Instance id")}, required=["instance_id"]),
        safety="write",
        executor=_exe_start_instance,
    ),
    Tool(
        name="stop_instance",
        description="Stop an instance (halts the broker; positions are NOT closed).",
        parameters=_obj({"instance_id": _str("Instance id")}, required=["instance_id"]),
        safety="write",
        executor=_exe_stop_instance,
    ),
    Tool(
        name="create_backtest",
        description="Queue a new backtest. start_date and end_date are ISO dates (YYYY-MM-DD).",
        parameters=_obj({
            "instance_id": _str("Optional instance to inherit strategy / stocks from"),
            "stocks": _arr({"type": "string"}, "Stocks to include (overrides instance stocks)"),
            "start_date": _str("Start date YYYY-MM-DD"),
            "end_date": _str("End date YYYY-MM-DD"),
            "granularity": _str("Bar granularity (e.g. '60' or '1Day')"),
            "initial_cash": _num("Starting cash (default 100000)"),
        }, required=["start_date", "end_date"]),
        safety="write",
        executor=_exe_create_backtest,
    ),
    Tool(
        name="control_engine",
        description="Start or stop a background engine. engine: 'discover' | 'digest'.",
        parameters=_obj({
            "engine": _str("Engine name: 'discover' | 'digest'"),
            "running": _bool("True to start, false to stop"),
        }, required=["engine", "running"]),
        safety="write",
        executor=_exe_engine_control,
    ),

    # ── Tier 3: destructive ────────────────────────────────────────────
    Tool(
        name="delete_instance",
        description="DESTRUCTIVE: delete an instance. Pass force=true to delete even if it has linked resources.",
        parameters=_obj({
            "instance_id": _str("Instance id"),
            "force": _bool("Force delete despite linked resources"),
        }, required=["instance_id"]),
        safety="destructive",
        executor=_exe_delete_instance,
    ),
    Tool(
        name="delete_backtest",
        description="DESTRUCTIVE: delete a backtest record (does NOT delete the parent instance).",
        parameters=_obj({"backtest_id": _str("Backtest id")}, required=["backtest_id"]),
        safety="destructive",
        executor=_exe_delete_backtest,
    ),
    Tool(
        name="stop_backtest",
        description="DESTRUCTIVE: terminate a running backtest before completion.",
        parameters=_obj({"backtest_id": _str("Backtest id")}, required=["backtest_id"]),
        safety="destructive",
        executor=_exe_stop_backtest,
    ),
    Tool(
        name="live_command",
        description=(
            "DESTRUCTIVE — REAL MONEY. Submit a live broker command. "
            "type: 'halt' (stop new orders) | 'close_position' (payload: {symbol}) | "
            "'submit_order' (payload: {symbol, side, qty, type, ...})."
        ),
        parameters=_obj({
            "instance_id": _str("Instance id"),
            "type": _str("Command type: halt | close_position | submit_order"),
            "payload": _obj({}, []),
        }, required=["instance_id", "type"]),
        safety="destructive",
        executor=_exe_live_command,
    ),

    # ── Render-only / UI tools ─────────────────────────────────────────
    Tool(
        name="render_chart",
        description=(
            "Render a generic inline chart. chart_type: 'area' | 'line' | 'bar' | 'scatter'. "
            "series: list of {name, data:[[x,y],...]}. Use AFTER fetching numeric data — never invent numbers. "
            "For portfolio equity-curve from get_portfolio_history, prefer render_portfolio_chart."
        ),
        parameters=_obj({
            "chart_type": _str("Chart type: area | line | bar | scatter"),
            "title": _str("Optional chart title"),
            "series": _arr({"type": "object"}, "ApexCharts-style series array"),
            "options": _obj({}, []),
            "x_axis_type": _str("'datetime' or 'category' (default 'category')"),
        }, required=["chart_type", "series"]),
        safety="safe",
        executor=_exe_render_chart,
        render_only=True,
    ),
    Tool(
        name="render_portfolio_chart",
        description=(
            "Render the dashboard-style portfolio equity-curve chart for a linked brokerage. "
            "Just pass the brokerage_id + range — the server fetches the history itself. "
            "Use this directly without first calling get_portfolio_history; the chart will appear "
            "inline and you can summarise the numbers in your follow-up text."
        ),
        parameters=_obj({
            "brokerage_id": _str("Brokerage account id (from list_brokerages)"),
            "range": _str("Range / granularity: 1D | 1W | 1M | 3M | YTD | 1Y | ALL (default 1M)"),
            "title": _str("Optional title override"),
        }, required=["brokerage_id"]),
        safety="safe",
        executor=_exe_render_portfolio_chart,
        render_only=True,
    ),
    Tool(
        name="get_price_history",
        description=(
            "Fetch close-price history for one or more stock tickers from the configured "
            "market-data provider. The range param picks both span and granularity automatically: "
            "1D = 5-min bars over a day, 1W = 10-min bars over a week, 1M = hourly bars over a month, "
            "3M = daily over 3 months, YTD = daily year-to-date, 1Y = daily over a year, ALL = weekly over 5 years. "
            "Use this when the user wants the raw numbers — for a chart, prefer render_price_chart "
            "directly (it fetches and renders in one step)."
        ),
        parameters=_obj({
            "symbols": _str("Single ticker or comma-separated list (max 75). e.g. 'AAPL' or 'AAPL,MSFT,NVDA'"),
            "range": _str("Range / granularity: 1D | 1W | 1M | 3M | YTD | 1Y | ALL (default 1D)"),
        }, required=["symbols"]),
        safety="safe",
        executor=_exe_get_price_history,
    ),
    Tool(
        name="render_price_chart",
        description=(
            "Render a polished line chart of one or more stock tickers' closing prices. Just pass "
            "the symbols + range — the server fetches the history and pairs timestamps with closes "
            "itself. Use this whenever the user asks to SEE a chart for a stock symbol; you do NOT "
            "need to call get_price_history first."
        ),
        parameters=_obj({
            "symbols": _str("Single ticker or comma-separated list (max 75). e.g. 'SNDK' or 'AAPL,MSFT'"),
            "range": _str("Range / granularity: 1D | 1W | 1M | 3M | YTD | 1Y | ALL (default 1D)"),
            "title": _str("Optional chart title override"),
        }, required=["symbols"]),
        safety="safe",
        executor=_exe_render_price_chart,
        render_only=True,
    ),
    Tool(
        name="render_table",
        description="Render an inline table. headers is a list of {label, key, align?}, rows is a list of objects keyed by header.key.",
        parameters=_obj({
            "title": _str("Optional title"),
            "headers": _arr({"type": "object"}, "[{label, key, align?}]"),
            "rows": _arr({"type": "object"}, "Array of row objects"),
        }, required=["headers", "rows"]),
        safety="safe",
        executor=_exe_render_table,
        render_only=True,
    ),
    Tool(
        name="render_stat",
        description="Render a stat card (label + big value + optional detail + trend up/down).",
        parameters=_obj({
            "label": _str("Small uppercase label"),
            "value": _str("Big primary value (already formatted)"),
            "detail": _str("Optional secondary line"),
            "trend": _str("'up' | 'down' (optional)"),
        }, required=["label", "value"]),
        safety="safe",
        executor=_exe_render_stat,
        render_only=True,
    ),
    Tool(
        name="navigate_to",
        description=(
            "Ask the frontend to navigate to a route (e.g. '/instances', '/models', '/backtests/abc'). "
            "Internal paths only."
        ),
        parameters=_obj({"route": _str("Internal path starting with /")}, required=["route"]),
        safety="safe",
        executor=_exe_navigate,
        render_only=True,
    ),
]


_TOOLS_BY_NAME: Dict[str, Tool] = {t.name: t for t in _TOOLS}


def all_tools() -> List[Tool]:
    return list(_TOOLS)


def get_tool(name: str) -> Optional[Tool]:
    return _TOOLS_BY_NAME.get(name)


def openai_tool_definitions() -> List[Dict[str, Any]]:
    return [t.to_openai_tool() for t in _TOOLS]


def gemini_tool_declarations() -> List[Dict[str, Any]]:
    return [t.to_gemini_declaration() for t in _TOOLS]


def tool_catalog() -> List[Dict[str, Any]]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "safety": t.safety,
            "render_only": t.render_only,
        }
        for t in _TOOLS
    ]
