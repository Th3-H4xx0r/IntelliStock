"""
Send messages to Discord from other IntelliStock services without running the bot.
Uses the DiscordOutbox table in RethinkDB: enqueue a message here and the Discord bot
(discord_bot.py) will pick it up and post it.

Usage (from backend or any service with RethinkDB access):

    from discord_sender import enqueue_discord_message

    conn = get_conn()  # from interactive_utils
    enqueue_discord_message(conn, "notifications", "Market opened. AAPL +1.2%")
    enqueue_discord_message(conn, "trades", "BUY 10 AAPL @ 150.00", embed={"title": "Trade", "color": 0x2ECC71})

Channel short names: cli, notifications, trades, briefs, backtests, ai-backtest-agent, best-strategies
(or use full names with emoji: briefs📋, notifications🔔, trades📈, ai-backtest-agent🤖, best-strategies🏆, etc.)
"""

import os
import sys

_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from interactive_utils import get_conn, action_enqueue_discord_message, action_enqueue_discord_edit, action_enqueue_discord_delete


def enqueue_discord_message(conn, channel, content, embed=None, guild_id=None, message_key=None):
    """
    Enqueue a message for the Discord bot to send.

    Args:
        conn: RethinkDB connection (from interactive_utils.get_conn).
        channel: Channel name: "notifications", "trades", "briefs", "backtests", "cli",
                 or full names like "notifications🔔", "trades📈", etc.
        content: Message text (optional if embed provided).
        embed: Optional dict for rich embed: {"title": "...", "description": "...", "color": 0x2ECC71,
               "fields": [{"name": "...", "value": "...", "inline": True}]}.
        guild_id: Optional Discord server (guild) ID; if omitted the bot uses its first guild.
        message_key: Optional string; if set, the bot stores the sent message ID so it can be edited later via enqueue_discord_edit.

    Returns:
        {"id": "<uuid>", "channel": "...", "status": "pending"}
    """
    return action_enqueue_discord_message(conn, channel, content, embed=embed, guild_id=guild_id, message_key=message_key)


def enqueue_discord_edit(conn, channel, message_key, content=None, embed=None, guild_id=None):
    """
    Enqueue an edit of an existing Discord message that was sent with the same message_key.
    The bot will look up the message and update its content/embed.
    """
    return action_enqueue_discord_edit(conn, channel, message_key, content=content, embed=embed, guild_id=guild_id)


def enqueue_discord_delete(conn, channel, message_key, guild_id=None):
    """
    Enqueue a delete of an existing Discord message that was sent with the same message_key.
    Use when agent stops mid-cycle to remove the current chunk's strategy slot messages.
    """
    return action_enqueue_discord_delete(conn, channel, message_key, guild_id=guild_id)
