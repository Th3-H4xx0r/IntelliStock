"""Live-trading Discord alerts.

Thin wrapper around discord_sender.enqueue_discord_message for live-mode events.
Gracefully degrades to a log line when the Discord outbox is unreachable so
trading never blocks on an alert failure.

Channel routing:
- order submit/fill/reject  -> #live-alerts (falls back to #trades)
- strategy error / halt     -> #live-alerts (falls back to #notifications)
- broker health / reconcile -> #notifications
"""

from __future__ import annotations

import os
from typing import Any, Optional

from intellistock_logger import intellistock_logger
from notifications import notify


def _channel(default: str = "trades") -> str:
    # 2026-04-30 fix: default was "live-alerts" which is NOT a registered
    # Discord channel — the bot rejected every alert as status='failed'
    # in DiscordOutbox. Valid channels per discord_sender.py docstring:
    # cli, notifications, trades, briefs, backtests, ai-backtest-agent,
    # best-strategies. Default to "trades" since order events are the
    # most frequent caller; operator can still override via the
    # LIVE_ALERTS_CHANNEL env var if a dedicated channel is created.
    return os.environ.get("LIVE_ALERTS_CHANNEL", default)


def _scrub_embed(embed: Optional[dict]) -> Optional[dict]:
    """Pass embed fields through the logger redact filter so caller-supplied
    values (order notes, reason_text, extra kwargs) can never leak a key or
    token into Discord."""
    if not embed:
        return embed
    try:
        from intellistock_logger import _redact
    except Exception:
        return embed
    out = dict(embed)
    if "description" in out and isinstance(out["description"], str):
        out["description"] = _redact(out["description"])
    if "title" in out and isinstance(out["title"], str):
        out["title"] = _redact(out["title"])
    fields = out.get("fields")
    if isinstance(fields, list):
        scrubbed_fields = []
        for f in fields:
            if not isinstance(f, dict):
                scrubbed_fields.append(f)
                continue
            ff = dict(f)
            for k in ("name", "value"):
                v = ff.get(k)
                if isinstance(v, str):
                    ff[k] = _redact(v)
            scrubbed_fields.append(ff)
        out["fields"] = scrubbed_fields
    return out


def _safe_enqueue(channel: str, content: str, embed: Optional[dict] = None,
                  notif_key: Optional[str] = None) -> None:
    """Enqueue a Discord message; on ANY failure log and continue.

    2026-05-07: emit a success/failure log line for EVERY call so operators
    can confirm the Discord pipeline is alive without waiting for a
    Discord client to be open. Success logs are cyan; failures are yellow.
    The ``content`` is truncated to 200 chars in the log to keep things
    readable.
    """
    try:
        from intellistock_logger import _redact
        content = _redact(content) if isinstance(content, str) else content
    except Exception:
        pass
    embed = _scrub_embed(embed)
    _content_preview = (content[:200] + "…") if isinstance(content, str) and len(content) > 200 else str(content)
    try:
        from discord_sender import enqueue_discord_message
        from interactive_utils import get_conn
        conn = get_conn()
        try:
            enqueue_discord_message(conn, channel, content, embed=embed, notif_key=notif_key)
            try:
                intellistock_logger.log(
                    f"discord ENQUEUED channel={channel} content={_content_preview}",
                    "cyan",
                    service="LIVE-ALERT",
                )
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as e:
        try:
            intellistock_logger.log(
                f"discord ENQUEUE FAILED channel={channel} err={type(e).__name__}: {e} content={_content_preview}",
                "yellow",
                service="LIVE-ALERT",
            )
        except Exception:
            pass


def alert_order_submit(
    *,
    instance_id: str,
    symbol: str,
    side: str,
    qty: Optional[float],
    notional: Optional[float],
    client_order_id: str,
    broker_order_id: str,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    qty_str = f"{qty:.4f}" if qty is not None else f"${notional:.2f}"
    content = f"ORDER SUBMIT [{instance_id}] {side.upper()} {qty_str} {symbol}"
    embed = {
        "title": f"Order submitted: {symbol}",
        "color": 0x3498DB,
        "fields": [
            {"name": "side", "value": side, "inline": True},
            {"name": "qty/notional", "value": qty_str, "inline": True},
            {"name": "client_order_id", "value": client_order_id, "inline": False},
            {"name": "broker_order_id", "value": broker_order_id, "inline": False},
        ],
    }
    if extra:
        for k, v in extra.items():
            embed["fields"].append({"name": str(k), "value": str(v), "inline": True})
    notify(
        category="order_submit", instance_id=instance_id,
        title=f"Order submitted: {symbol}", body=content,
        discord_channel=_channel(), discord_embed=embed,
        push_body=f"SUBMIT {side.upper()} {qty_str} {symbol}",
    )


def alert_order_fill(
    *,
    instance_id: str,
    symbol: str,
    side: str,
    filled_qty: float,
    filled_avg_price: float,
    client_order_id: str,
    broker_order_id: str = "",
) -> None:
    """2026-04-22 Round 4 Fix 6: ``broker_order_id`` made optional (default
    empty). The Alpaca-assigned order id is the cross-reference that lets
    operators match Discord fills to Alpaca's dashboard / CSV exports. We
    already have it at the fill callsite; surfacing it here costs one
    extra embed field.
    """
    total = filled_qty * filled_avg_price
    content = (
        f"ORDER FILL [{instance_id}] {side.upper()} {filled_qty:.4f} {symbol} "
        f"@ ${filled_avg_price:.2f} = ${total:.2f}"
    )
    _fields = [
        {"name": "side", "value": side, "inline": True},
        {"name": "filled_qty", "value": f"{filled_qty:.4f}", "inline": True},
        {"name": "avg_price", "value": f"${filled_avg_price:.2f}", "inline": True},
        {"name": "total", "value": f"${total:.2f}", "inline": True},
        {"name": "client_order_id", "value": client_order_id, "inline": False},
    ]
    if broker_order_id:
        _fields.append({"name": "broker_order_id", "value": str(broker_order_id), "inline": False})
    embed = {
        "title": f"Filled: {symbol}",
        "color": 0x2ECC71,
        "fields": _fields,
    }
    notify(
        category="order_fill", instance_id=instance_id,
        title=f"Filled: {symbol}", body=content,
        discord_channel=_channel(), discord_embed=embed,
        push_body=f"{side.upper()} {filled_qty:.4f} {symbol} @ ${filled_avg_price:.2f}",
    )


def alert_order_reject(
    *,
    instance_id: str,
    symbol: str,
    side: str,
    reason_class: str,
    reason_text: str,
    client_order_id: str,
) -> None:
    content = f"ORDER REJECT [{instance_id}] {side.upper()} {symbol} :: {reason_class}: {reason_text}"
    embed = {
        "title": f"Rejected: {symbol}",
        "color": 0xE74C3C,
        "fields": [
            {"name": "reason_class", "value": reason_class, "inline": True},
            {"name": "reason", "value": reason_text[:500], "inline": False},
            {"name": "client_order_id", "value": client_order_id, "inline": False},
        ],
    }
    notify(
        category="order_reject", instance_id=instance_id,
        title=f"Rejected: {symbol}", body=content,
        discord_channel=_channel(), discord_embed=embed,
        push_body=f"REJECT {side.upper()} {symbol}: {reason_class}",
    )


def alert_order_retry(
    *,
    instance_id: str,
    symbol: str,
    side: str,
    original_qty: Optional[float],
    retry_qty: float,
    reason_class: str,
    reason_text: str,
    client_order_id: str,
) -> None:
    """2026-04-30 — paged when an order is being retried after a recoverable
    rejection (e.g. Alpaca 'not fractionable' -> floor qty and retry).
    Distinct from alert_order_reject (final failure) and alert_order_submit
    (initial submit) so operators can audit retry behavior.
    """
    _orig = f"{original_qty:.4f}" if original_qty is not None else "-"
    content = (
        f"ORDER RETRY [{instance_id}] {side.upper()} {symbol}: "
        f"orig_qty={_orig} -> retry_qty={retry_qty:.4f} "
        f"({reason_class}: {reason_text[:80]})"
    )
    embed = {
        "title": f"Retrying: {symbol}",
        "color": 0xF39C12,  # orange — between submit (blue) and reject (red)
        "fields": [
            {"name": "side", "value": side, "inline": True},
            {"name": "original_qty", "value": _orig, "inline": True},
            {"name": "retry_qty", "value": f"{retry_qty:.4f}", "inline": True},
            {"name": "reason_class", "value": reason_class, "inline": True},
            {"name": "reason", "value": reason_text[:500], "inline": False},
            {"name": "client_order_id", "value": client_order_id, "inline": False},
        ],
    }
    notify(
        category="order_retry", instance_id=instance_id,
        title=f"Retrying: {symbol}", body=content,
        discord_channel=_channel(), discord_embed=embed,
        push_body=f"RETRY {side.upper()} {symbol} -> {retry_qty:.4f}",
    )


def alert_strategy_error(
    *,
    instance_id: str,
    tag: str,
    message: str,
) -> None:
    content = f"STRATEGY ERROR [{instance_id}] {tag}: {message[:300]}"
    embed = {
        "title": f"Strategy error [{tag}]",
        "color": 0xE67E22,
        "description": message[:1500],
    }
    notify(
        category="strategy_error", instance_id=instance_id,
        title=f"Strategy error [{tag}]", body=content,
        discord_channel=_channel(), discord_embed=embed,
        push_body=f"{tag}: {message[:120]}",
    )


def alert_halt(*, instance_id: str, reason: str) -> None:
    content = f"HALT [{instance_id}] {reason}"
    embed = {"title": "Live trading HALTED", "color": 0x992D22, "description": reason[:1500]}
    notify(
        category="halt", instance_id=instance_id,
        title="Live trading HALTED", body=content,
        discord_channel=_channel("notifications"), discord_embed=embed,
        push_body=reason[:160],
    )


def alert_drawdown_halt(
    *,
    instance_id: str,
    drawdown_pct: float,
    peak_value: float,
    current_value: float,
) -> None:
    """2026-04-22 Round 4 Fix 7: paged on drawdown-halt activation.

    Distinct from ``alert_halt`` (manual UI/supervisor halt) — this fires
    the instant the strategy's internal risk-off guard trips, so operators
    know buys are paused and the portfolio has entered drawdown-recovery
    mode before the UI even refreshes the banner.
    """
    content = (
        f"DRAWDOWN HALT [{instance_id}] -{drawdown_pct:.1f}% from peak "
        f"(peak=${peak_value:.0f} -> current=${current_value:.0f})"
    )
    embed = {
        "title": "Drawdown halt activated",
        "color": 0xE67E22,
        "fields": [
            {"name": "drawdown_pct", "value": f"-{drawdown_pct:.2f}%", "inline": True},
            {"name": "peak_value", "value": f"${peak_value:,.0f}", "inline": True},
            {"name": "current_value", "value": f"${current_value:,.0f}", "inline": True},
            {"name": "effect", "value": "New buys paused; backfill graduated", "inline": False},
        ],
    }
    # Routed to #trades (via LIVE_TRADES_CHANNEL env var) because a drawdown
    # halt is a trade-flow event (buys paused) rather than infrastructure
    # signal. Operators watching the trades channel see it immediately.
    notify(
        category="drawdown_halt", instance_id=instance_id,
        title="Drawdown halt activated", body=content,
        discord_channel=os.environ.get("LIVE_TRADES_CHANNEL", "trades"),
        discord_embed=embed,
        push_body=f"-{drawdown_pct:.1f}% from peak; new buys paused",
    )


def alert_strategy_start(
    *,
    instance_id: str,
    strategy_name: str,
    date_key: str,
    symbols_count: int,
    held_count: int,
    equity: float,
) -> None:
    """2026-04-30 — paged once per (instance, strategy, NY date) when
    the live strategy fires its first run of the session.
    """
    content = (
        f"STRATEGY START [{instance_id}] {strategy_name} "
        f"date={date_key} symbols={symbols_count} held={held_count} "
        f"equity=${equity:,.0f}"
    )
    embed = {
        "title": f"Strategy starting: {strategy_name}",
        "color": 0x3498DB,
        "fields": [
            {"name": "instance", "value": instance_id, "inline": True},
            {"name": "date_key", "value": date_key, "inline": True},
            {"name": "symbols", "value": str(symbols_count), "inline": True},
            {"name": "held", "value": str(held_count), "inline": True},
            {"name": "equity", "value": f"${equity:,.0f}", "inline": True},
        ],
    }
    notify(
        category="strategy_start", instance_id=instance_id,
        title=f"Strategy starting: {strategy_name}", body=content,
        discord_channel=_channel(), discord_embed=embed,
        push_body=f"{strategy_name} {date_key} ({symbols_count} symbols)",
    )


def alert_crash_loop(*, instance_id: str, restarts: int, window_sec: int) -> None:
    content = (
        f"CRASH LOOP [{instance_id}] {restarts} restarts in {window_sec}s. "
        "Broker subprocess will NOT be restarted until operator intervention."
    )
    embed = {
        "title": "Crash loop detected",
        "color": 0x992D22,
        "fields": [
            {"name": "restarts", "value": str(restarts), "inline": True},
            {"name": "window_sec", "value": str(window_sec), "inline": True},
        ],
    }
    notify(
        category="crash_loop", instance_id=instance_id,
        title="Crash loop detected", body=content,
        discord_channel=_channel("notifications"), discord_embed=embed,
        push_body=f"{restarts} restarts in {window_sec}s; broker NOT auto-restarted",
    )
