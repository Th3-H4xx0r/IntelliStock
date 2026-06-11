"""
Shared logic for CLI and REST API: RethinkDB operations and pure actions.
No input() or pretty-print; all functions return JSON-serializable data or raise ValueError.
"""

import json
import os
import random
import re
import shutil
import sys
import threading
import datetime
import time
from contextlib import suppress

_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from rethinkdb import RethinkDB

r = RethinkDB()
DB_NAME = "IntelliStock"
RETHINKDB_HOST = os.environ.get("RETHINKDB_HOST", "localhost")
RETHINKDB_PORT = int(os.environ.get("RETHINKDB_PORT", "28015"))


def get_conn(host=None, port=None):
    """Create a RethinkDB connection."""
    h = host if host is not None else RETHINKDB_HOST
    p = int(port) if port is not None else RETHINKDB_PORT
    # 2026-05-05 live-hang investigation: a half-open TCP socket on the
    # rdb side can block r.connect() forever without an explicit timeout.
    return r.connect(host=h, port=p, timeout=10)


def _resolve_instance_doc(conn, instance_id):
    """Return instance document by id (string or int). Returns None if not found."""
    doc = r.db(DB_NAME).table("Instances").get(instance_id).run(conn)
    if doc is not None:
        return doc
    try:
        doc = r.db(DB_NAME).table("Instances").get(int(instance_id)).run(conn)
        return doc
    except (TypeError, ValueError):
        return None


# --- Table ensure ---


def ensure_live_prices_stocks_table(conn):
    dbs = list(r.db_list().run(conn))
    if DB_NAME not in dbs:
        r.db_create(DB_NAME).run(conn)
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "LivePricesStocks" not in tables:
        r.db(DB_NAME).table_create("LivePricesStocks").run(conn)


def ensure_instances_table(conn):
    dbs = list(r.db_list().run(conn))
    if DB_NAME not in dbs:
        r.db_create(DB_NAME).run(conn)
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "Instances" not in tables:
        r.db(DB_NAME).table_create("Instances").run(conn)


def ensure_stocks_table(conn):
    dbs = list(r.db_list().run(conn))
    if DB_NAME not in dbs:
        r.db_create(DB_NAME).run(conn)
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "Stocks" not in tables:
        r.db(DB_NAME).table_create("Stocks").run(conn)


def ensure_strategies_table(conn):
    dbs = list(r.db_list().run(conn))
    if DB_NAME not in dbs:
        r.db_create(DB_NAME).run(conn)
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "Strategies" not in tables:
        r.db(DB_NAME).table_create("Strategies").run(conn)


# Strategy difficulty (1-10) from strategies/*.py, for Discord backtest embeds. Lazy-loaded.
# Use same canonical normalization as backtest_engine so DB "TieredRisk" / "Tiered Risk" match file "TieredRisk".
_strategy_difficulty_cache = None
DEFAULT_DIFFICULTY = 3.0
HIGH_DIFFICULTY_THRESHOLD = 8.0


def _normalize_strategy_id(name):
    """Canonical form for strategy names (CamelCase -> snake_case, lower, spaces -> underscore)."""
    if not name or not isinstance(name, str):
        return ""
    s = name.strip().replace(" ", "_")
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()
    s = re.sub(r"_+", "_", s).strip("_")
    return s or ""


_available_strategy_meta_cache = None


def _load_available_strategy_meta():
    """Return ({alias -> meta}, {canonical_id -> meta}) for discovered strategies."""
    global _available_strategy_meta_cache
    if _available_strategy_meta_cache is not None:
        return _available_strategy_meta_cache
    try:
        from strategies_meta import get_available_strategies
        items = get_available_strategies() or []
    except Exception:
        items = []
    by_alias = {}
    by_canonical = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        schema = item.get("schema") if isinstance(item.get("schema"), dict) else {}
        canonical_id = _normalize_strategy_id(
            schema.get("strategy") or item.get("id") or item.get("name") or ""
        )
        if not canonical_id:
            continue
        meta = {
            "id": item.get("id"),
            "name": item.get("name"),
            "schema": schema,
            "description": item.get("description") or "",
        }
        by_canonical[canonical_id] = meta
        aliases = {
            canonical_id,
            _normalize_strategy_id(item.get("id") or ""),
            _normalize_strategy_id(item.get("name") or ""),
            _normalize_strategy_id(schema.get("strategy") or ""),
        }
        for alias in aliases:
            if alias:
                by_alias[alias] = meta
    _available_strategy_meta_cache = (by_alias, by_canonical)
    return _available_strategy_meta_cache


def _resolve_strategy_meta(strategy_name):
    alias = _normalize_strategy_id(strategy_name or "")
    if not alias:
        return None
    by_alias, _by_canonical = _load_available_strategy_meta()
    return by_alias.get(alias)


def _merge_strategy_config_conditions(config, conditions):
    """Treat config as canonical, but preserve legacy condition keys as config fallbacks."""
    merged = {}
    if isinstance(conditions, dict):
        merged.update(conditions)
    if isinstance(config, dict):
        merged.update(config)
    return merged


def _apply_strategy_config_aliases(strategy_name, config):
    """Promote legacy config aliases into their canonical names."""
    normalized = dict(config or {})
    strategy_id = _normalize_strategy_id(strategy_name or "")
    if strategy_id == "graph_nexus_analysis":
        if normalized.get("max_daily_alpaca_articles") is None and normalized.get("num_articles") is not None:
            normalized["max_daily_alpaca_articles"] = normalized.get("num_articles")
        normalized.pop("num_articles", None)
        if normalized.get("max_daily_google_news_articles") is None and normalized.get("google_news_max_articles") is not None:
            normalized["max_daily_google_news_articles"] = normalized.get("google_news_max_articles")
        normalized.pop("google_news_max_articles", None)
    return normalized


def _normalize_strategy_payload_item(strategy_doc, strict=True):
    """Normalize a strategy payload item from UI/API/agent into runtime-safe canonical form."""
    if not isinstance(strategy_doc, dict):
        return None
    raw_strategy = str(strategy_doc.get("strategy", "")).strip()
    if not raw_strategy:
        if strict:
            raise ValueError("Each sub-strategy must have a 'strategy' type")
        return None

    meta = _resolve_strategy_meta(raw_strategy)
    if meta is None and strict:
        raise ValueError("Unknown sub-strategy type: %s" % raw_strategy)

    schema = meta.get("schema") if isinstance(meta, dict) else {}
    canonical_strategy = (
        (schema.get("strategy") if isinstance(schema, dict) else None)
        or (meta.get("id") if isinstance(meta, dict) else None)
        or _normalize_strategy_id(raw_strategy)
        or raw_strategy
    )

    phase_default = str((schema.get("decision_phase") if isinstance(schema, dict) else "pre") or "pre").strip().lower() or "pre"
    scope_default = str((schema.get("execution_scope") if isinstance(schema, dict) else "per_symbol") or "per_symbol").strip().lower() or "per_symbol"

    decision_phase = str(strategy_doc.get("decision_phase", phase_default)).strip().lower() or phase_default
    if phase_default == "post":
        decision_phase = "post"
    elif decision_phase != "post":
        decision_phase = "pre"

    execution_scope = str(strategy_doc.get("execution_scope", scope_default)).strip().lower() or scope_default
    if scope_default == "run_once":
        execution_scope = "run_once"
    elif execution_scope not in {"per_symbol", "run_once"}:
        execution_scope = "per_symbol"

    try:
        weight = float(strategy_doc.get("weight", 0.5))
    except (TypeError, ValueError):
        if strict:
            raise
        weight = 0.5

    try:
        execution_position = int(strategy_doc.get("execution_position", 0))
    except (TypeError, ValueError):
        if strict:
            raise
        execution_position = 0

    merged_config = _merge_strategy_config_conditions(
        strategy_doc.get("config") if isinstance(strategy_doc.get("config"), dict) else {},
        strategy_doc.get("conditions") if isinstance(strategy_doc.get("conditions"), dict) else {},
    )
    merged_config = _apply_strategy_config_aliases(canonical_strategy, merged_config)

    return {
        "strategy": canonical_strategy,
        "weight": weight,
        "execution_position": execution_position,
        "decision_phase": decision_phase,
        "execution_scope": execution_scope,
        # Conditions are promoted into config for editing/storage; broker/runtime still supports legacy reads.
        "conditions": {},
        "config": merged_config,
    }


def _load_strategy_difficulties():
    """Scan backend/strategies/*.py for # DIFFICULTY and # INTELLISTOCK_SCHEMA; cache result."""
    global _strategy_difficulty_cache
    if _strategy_difficulty_cache is not None:
        return
    strategies_dir = os.path.join(_backend_dir, "strategies")
    loaded = {}
    if os.path.isdir(strategies_dir):
        for fname in os.listdir(strategies_dir):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            fpath = os.path.join(strategies_dir, fname)
            strategy_id = None
            difficulty = None
            try:
                with open(fpath, encoding="utf-8") as f:
                    for i, line in enumerate(f):
                        if i > 10:
                            break
                        line = line.strip()
                        if line.startswith("# INTELLISTOCK_SCHEMA:"):
                            try:
                                schema_json = line[len("# INTELLISTOCK_SCHEMA:"):].strip()
                                schema = json.loads(schema_json)
                                raw_id = (schema.get("strategy") or "").strip()
                                if raw_id:
                                    strategy_id = _normalize_strategy_id(raw_id)
                            except Exception:
                                pass
                        elif line.startswith("# DIFFICULTY:"):
                            try:
                                difficulty = float(line[len("# DIFFICULTY:"):].strip())
                            except Exception:
                                pass
            except Exception:
                continue
            if strategy_id and difficulty is not None:
                loaded[strategy_id] = difficulty
    _strategy_difficulty_cache = loaded


def get_instance_avg_difficulty(conn, instance_id):
    """Return average difficulty (1-10) for the instance's strategy. Uses DEFAULT_DIFFICULTY if unknown."""
    _load_strategy_difficulties()
    if not instance_id:
        return DEFAULT_DIFFICULTY
    try:
        inst = _resolve_instance_doc(conn, instance_id)
        if not inst:
            return DEFAULT_DIFFICULTY
        strategy_id = inst.get("strategy_id")
        if strategy_id is None:
            return DEFAULT_DIFFICULTY
        strat_doc = r.db(DB_NAME).table("Strategies").get(strategy_id).run(conn)
        if not strat_doc:
            return DEFAULT_DIFFICULTY
        subs = strat_doc.get("strategies") or []
        if not subs:
            return DEFAULT_DIFFICULTY
        total = 0.0
        for sub in subs:
            sid = _normalize_strategy_id(sub.get("strategy") or "")
            total += _strategy_difficulty_cache.get(sid, DEFAULT_DIFFICULTY)
        return total / len(subs)
    except Exception:
        return DEFAULT_DIFFICULTY


def get_instance_high_usage(conn, instance_id):
    """True if the instance's strategy has any substrategy with difficulty >= HIGH_DIFFICULTY_THRESHOLD."""
    _load_strategy_difficulties()
    if not instance_id:
        return False
    try:
        inst = _resolve_instance_doc(conn, instance_id)
        if not inst:
            return False
        strategy_id = inst.get("strategy_id")
        if strategy_id is None:
            return False
        strat_doc = r.db(DB_NAME).table("Strategies").get(strategy_id).run(conn)
        if not strat_doc:
            return False
        for sub in (strat_doc.get("strategies") or []):
            sid = _normalize_strategy_id(sub.get("strategy") or "")
            if not sid:
                continue
            d = _strategy_difficulty_cache.get(sid)
            if d is not None and d >= HIGH_DIFFICULTY_THRESHOLD:
                return True
        return False
    except Exception:
        return False


def ensure_backtest_instances_table(conn):
    dbs = list(r.db_list().run(conn))
    if DB_NAME not in dbs:
        r.db_create(DB_NAME).run(conn)
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "BacktestInstances" not in tables:
        r.db(DB_NAME).table_create("BacktestInstances").run(conn)
    # 2026-04-26 perf: secondary indexes on instance lookups so the list
    # endpoint can use .get_all(instance, index=...) instead of full-table
    # .filter() scans. With ~1000 BacktestResults rows and growing, every
    # backtest-list page load was scanning the entire table; the index
    # collapses that to a sub-ms range read.
    try:
        existing = set(r.db(DB_NAME).table("BacktestInstances").index_list().run(conn))
        if "instance" not in existing:
            r.db(DB_NAME).table("BacktestInstances").index_create("instance").run(conn)
            r.db(DB_NAME).table("BacktestInstances").index_wait("instance").run(conn)
    except Exception:
        pass
    if "BacktestResults" in tables:
        try:
            existing = set(r.db(DB_NAME).table("BacktestResults").index_list().run(conn))
            if "instance_or_instance_id" not in existing:
                # The instance identifier lives in either `instance_id` (newer
                # rows) or `instance` (legacy). Index against a coalesced
                # lambda so .get_all(...) covers both.
                r.db(DB_NAME).table("BacktestResults").index_create(
                    "instance_or_instance_id",
                    lambda row: row["instance_id"].default(row["instance"].default("")).coerce_to("string"),
                ).run(conn)
                r.db(DB_NAME).table("BacktestResults").index_wait("instance_or_instance_id").run(conn)
        except Exception:
            pass


def ensure_discord_outbox_table(conn):
    """Table for other services to enqueue Discord messages. Bot (engines/discord_bot.py) polls and sends."""
    dbs = list(r.db_list().run(conn))
    if DB_NAME not in dbs:
        r.db_create(DB_NAME).run(conn)
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "DiscordOutbox" not in tables:
        r.db(DB_NAME).table_create("DiscordOutbox").run(conn)


def ensure_discord_message_ids_table(conn):
    """Table for bot to store Discord message IDs by (channel, message_key) so messages can be edited later."""
    dbs = list(r.db_list().run(conn))
    if DB_NAME not in dbs:
        r.db_create(DB_NAME).run(conn)
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "DiscordMessageIds" not in tables:
        r.db(DB_NAME).table_create("DiscordMessageIds").run(conn)


def action_enqueue_discord_message(conn, channel, content, embed=None, guild_id=None, message_key=None):
    """
    Enqueue a message for the Discord bot to send. Bot polls DiscordOutbox and sends to the given channel.
    channel: one of cli, notifications, trades, briefs, backtests (short names) or full names with emoji.
    content: text (optional if embed provided).
    embed: optional dict with title, description, color, fields (list of {name, value, inline}), etc.
    guild_id: optional Discord guild ID; if omitted bot uses first guild.
    message_key: optional string; if set, bot will store the sent message's Discord ID under this key for later edits.
    Returns: { "id": "<uuid>", "channel": "...", "status": "pending" }.
    """
    ensure_discord_outbox_table(conn)
    import uuid
    from datetime import datetime, timezone
    doc = {
        "id": str(uuid.uuid4()),
        "channel": str(channel).strip(),
        "content": str(content).strip() if content else "",
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat() + "Z",
    }
    if embed is not None and isinstance(embed, dict):
        doc["embed"] = embed
    if guild_id is not None:
        doc["guild_id"] = str(guild_id)
    if message_key is not None and str(message_key).strip():
        doc["message_key"] = str(message_key).strip()
    r.db(DB_NAME).table("DiscordOutbox").insert(doc).run(conn)
    return {"id": doc["id"], "channel": doc["channel"], "status": "pending"}


def action_enqueue_discord_edit(conn, channel, message_key, content=None, embed=None, guild_id=None):
    """
    Enqueue an edit of an existing Discord message. Bot will look up the message by (channel, message_key)
    and call edit_message with the new content/embed.
    message_key: must match the message_key used when the message was first sent.
    """
    ensure_discord_outbox_table(conn)
    import uuid
    from datetime import datetime, timezone
    doc = {
        "id": str(uuid.uuid4()),
        "channel": str(channel).strip(),
        "content": (str(content).strip() if content else "") or "",
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat() + "Z",
        "edit_key": str(message_key).strip(),
    }
    if embed is not None and isinstance(embed, dict):
        doc["embed"] = embed
    if guild_id is not None:
        doc["guild_id"] = str(guild_id)
    r.db(DB_NAME).table("DiscordOutbox").insert(doc).run(conn)
    return {"id": doc["id"], "channel": doc["channel"], "status": "pending"}


def action_enqueue_discord_delete(conn, channel, message_key, guild_id=None):
    """
    Enqueue a delete of an existing Discord message. Bot will look up the message by (channel, message_key),
    delete it, and mark the outbox item done. Use to remove strategy slot messages when agent stops mid-cycle.
    """
    ensure_discord_outbox_table(conn)
    import uuid
    from datetime import datetime, timezone
    doc = {
        "id": str(uuid.uuid4()),
        "channel": str(channel).strip(),
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat() + "Z",
        "delete_key": str(message_key).strip(),
    }
    if guild_id is not None:
        doc["guild_id"] = str(guild_id)
    r.db(DB_NAME).table("DiscordOutbox").insert(doc).run(conn)
    return {"id": doc["id"], "channel": doc["channel"], "status": "pending"}


def action_store_discord_message_id(conn, channel, message_key, message_id, guild_id=None):
    """Store Discord message ID for (channel, message_key) so the bot can edit it later."""
    ensure_discord_message_ids_table(conn)
    key = "%s:%s" % (str(channel).strip(), str(message_key).strip())
    r.db(DB_NAME).table("DiscordMessageIds").insert({
        "id": key,
        "channel": str(channel).strip(),
        "message_key": str(message_key).strip(),
        "message_id": str(message_id),
        "guild_id": str(guild_id) if guild_id else None,
    }, conflict="replace").run(conn)


def action_get_discord_message_id(conn, channel, message_key):
    """Return {"message_id": "...", "guild_id": "..."} or None if not found."""
    ensure_discord_message_ids_table(conn)
    key = "%s:%s" % (str(channel).strip(), str(message_key).strip())
    doc = r.db(DB_NAME).table("DiscordMessageIds").get(key).run(conn)
    if doc is None:
        return None
    return {"message_id": doc.get("message_id"), "guild_id": doc.get("guild_id")}


# 2026-06-10 outbox hardening: retry transient send failures with backoff
# instead of dropping a message on its first failure (a silent way fill
# notifications went missing). Pure decision so it's unit-testable.
RETRY_MAX_ATTEMPTS = 5


def discord_retry_decision(attempts, now_ts):
    """Given the current attempt count, decide whether to requeue (with a
    backoff timestamp) or give up.

    Returns ``(status, next_retry_at, new_attempts)``:
      - status == "pending" and next_retry_at is a future epoch-seconds float
        while attempts < RETRY_MAX_ATTEMPTS,
      - status == "failed" and next_retry_at is None at/after the cap.
    """
    attempts = int(attempts or 0)
    if attempts >= RETRY_MAX_ATTEMPTS:
        return ("failed", None, attempts)
    backoff = min(300, 5 * (2 ** attempts))  # 5,10,20,40,80 … capped at 300s
    return ("pending", float(now_ts) + backoff, attempts + 1)


def action_get_pending_discord_messages(conn, limit=50):
    """Get sendable pending messages from DiscordOutbox for the bot.

    Skips rows whose ``next_retry_at`` is still in the future so a backed-off
    message isn't retried before its window.
    """
    ensure_discord_outbox_table(conn)
    now_epoch = r.now().to_epoch_time()
    cursor = (
        r.db(DB_NAME)
        .table("DiscordOutbox")
        .filter(
            lambda d: (d["status"] == "pending")
            & ((~d.has_fields("next_retry_at")) | (d["next_retry_at"].le(now_epoch)))
        )
        .order_by("created_at")
        .limit(limit)
        .run(conn)
    )
    return list(cursor)


def action_requeue_or_fail_discord_message(conn, msg_id, error):
    """On send failure, requeue with backoff (or mark failed at the cap).

    Reads the current ``attempts``, applies ``discord_retry_decision`` and
    writes ``status``/``attempts``/``next_retry_at``/``error``.
    """
    ensure_discord_outbox_table(conn)
    import time as _time
    from datetime import datetime, timezone
    doc = r.db(DB_NAME).table("DiscordOutbox").get(msg_id).run(conn)
    attempts = int((doc or {}).get("attempts", 0) or 0)
    status, next_at, new_attempts = discord_retry_decision(attempts, _time.time())
    update = {
        "status": status,
        "attempts": new_attempts,
        "error": str(error)[:500],
        "updated_at": datetime.now(timezone.utc).isoformat() + "Z",
        "next_retry_at": next_at,  # float epoch seconds, or None when failed
    }
    r.db(DB_NAME).table("DiscordOutbox").get(msg_id).update(update).run(conn)
    return {"status": status, "attempts": new_attempts}


def action_mark_discord_message_sent(conn, msg_id):
    """Mark a DiscordOutbox message as sent."""
    ensure_discord_outbox_table(conn)
    from datetime import datetime, timezone
    r.db(DB_NAME).table("DiscordOutbox").get(msg_id).update(
        {"status": "sent", "updated_at": datetime.now(timezone.utc).isoformat() + "Z"}
    ).run(conn)


def action_mark_discord_message_failed(conn, msg_id, error):
    """Mark a DiscordOutbox message as failed."""
    ensure_discord_outbox_table(conn)
    from datetime import datetime, timezone
    r.db(DB_NAME).table("DiscordOutbox").get(msg_id).update(
        {"status": "failed", "error": str(error)[:500], "updated_at": datetime.now(timezone.utc).isoformat() + "Z"}
    ).run(conn)


# ----------------------------------------------------------------------------
# Per-category notification preferences (Discord and/or iOS push routing).
# One doc per user (id == user_id), categories nested as a map so a single
# read serves notify() and a single write serves a settings save.
# ----------------------------------------------------------------------------

# The full notification taxonomy — single source of truth in notification_types.py.
# Covers the live-alert categories plus broker health, backtests, agent runs,
# briefs, infra, etc. Defaults preserve today's behavior (every type's Discord
# ON, push off / opt-in).
from notification_types import NOTIFICATION_TYPE_KEYS as NOTIFICATION_CATEGORIES
from notification_types import default_routing as _default_notification_categories


def _coerce_route(v):
    if not isinstance(v, dict):
        return {"discord": True, "push": False}
    return {"discord": bool(v.get("discord", True)), "push": bool(v.get("push", False))}


def _merge_notification_categories(stored):
    """Fill a full 9-category matrix from a (possibly partial) stored map,
    ignoring any unknown stored keys."""
    cats = _default_notification_categories()
    if isinstance(stored, dict):
        for c, v in stored.items():
            if c in cats:
                cats[c] = _coerce_route(v)
    return cats


def _validate_notification_categories(categories):
    """Return a full cleaned matrix; raise ValueError on any unknown category."""
    cats = _default_notification_categories()
    for c, v in (categories or {}).items():
        if c not in cats:
            raise ValueError(f"unknown notification category: {c}")
        cats[c] = _coerce_route(v)
    return cats


def _validate_provided_categories(categories):
    """Return cleaned routes for ONLY the provided keys; raise on unknown.

    Unlike _validate_notification_categories this does NOT fill omitted
    categories with defaults — callers overlay it onto the stored matrix so an
    omitted category keeps its stored value (a save from a client that doesn't
    know about a future category can't silently reset it)."""
    out = {}
    for c, v in (categories or {}).items():
        if c not in NOTIFICATION_CATEGORIES:
            raise ValueError(f"unknown notification category: {c}")
        out[c] = _coerce_route(v)
    return out


def ensure_notification_preferences_table(conn):
    dbs = list(r.db_list().run(conn))
    if DB_NAME not in dbs:
        r.db_create(DB_NAME).run(conn)
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "NotificationPreferences" not in tables:
        r.db(DB_NAME).table_create("NotificationPreferences").run(conn)


def action_get_notification_preferences(conn, user_id):
    """Return ``{"user_id", "categories"}`` with all 9 categories filled in
    (defaults where unset)."""
    ensure_notification_preferences_table(conn)
    doc = r.db(DB_NAME).table("NotificationPreferences").get(str(user_id)).run(conn)
    stored = doc.get("categories") if isinstance(doc, dict) else None
    return {"user_id": str(user_id), "categories": _merge_notification_categories(stored)}


def action_set_notification_preferences(conn, user_id, categories):
    """Validate + persist preferences. Provided categories are OVERLAID onto the
    stored matrix (omitted categories keep their stored value), so a save from a
    client that doesn't know a future category can't silently reset it. Raises
    ValueError on an unknown category."""
    from datetime import datetime, timezone
    ensure_notification_preferences_table(conn)
    provided = _validate_provided_categories(categories)  # raises on unknown
    existing_doc = r.db(DB_NAME).table("NotificationPreferences").get(str(user_id)).run(conn)
    stored = existing_doc.get("categories") if isinstance(existing_doc, dict) else None
    merged = _merge_notification_categories(stored)  # full 9, stored values kept
    merged.update(provided)
    doc = {
        "id": str(user_id),
        "user_id": str(user_id),
        "categories": merged,
        "updated_at": datetime.now(timezone.utc).isoformat() + "Z",
    }
    r.db(DB_NAME).table("NotificationPreferences").insert(doc, conflict="replace").run(conn)
    return {"user_id": str(user_id), "categories": merged}


# ----------------------------------------------------------------------------
# iOS push device registry (APNs device tokens). One row per device token
# (id == token) so register is idempotent — re-register rewrites the row
# (refreshing last_seen + app_version).
# ----------------------------------------------------------------------------

def _push_device_doc(user_id, token, platform="ios", env="prod", app_version=None, now_iso=None):
    from datetime import datetime, timezone
    now_iso = now_iso or (datetime.now(timezone.utc).isoformat() + "Z")
    return {
        "id": str(token),
        "user_id": str(user_id),
        "device_token": str(token),
        "platform": str(platform or "ios"),
        "env": str(env or "prod"),
        "app_version": app_version,
        "created_at": now_iso,
        "last_seen": now_iso,
    }


def ensure_push_devices_table(conn):
    dbs = list(r.db_list().run(conn))
    if DB_NAME not in dbs:
        r.db_create(DB_NAME).run(conn)
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "PushDevices" not in tables:
        r.db(DB_NAME).table_create("PushDevices").run(conn)


def action_register_push_device(conn, user_id, token, platform="ios", env="prod", app_version=None):
    """Idempotently register/refresh an APNs device token (id == token)."""
    ensure_push_devices_table(conn)
    doc = _push_device_doc(user_id, token, platform=platform, env=env, app_version=app_version)
    r.db(DB_NAME).table("PushDevices").insert(doc, conflict="replace").run(conn)
    return doc


def action_list_push_devices(conn, user_id, env=None):
    """Return a user's registered devices, optionally filtered by env."""
    ensure_push_devices_table(conn)
    sel = r.db(DB_NAME).table("PushDevices").filter({"user_id": str(user_id)})
    if env:
        sel = sel.filter({"env": str(env)})
    return list(sel.run(conn))


def action_push_device_user_ids(conn):
    """Distinct user_ids that have at least one registered push device.
    Used to resolve the operator for the live-alert push path (single-operator)."""
    ensure_push_devices_table(conn)
    rows = r.db(DB_NAME).table("PushDevices").pluck("user_id").run(conn)
    return sorted({row.get("user_id") for row in rows if isinstance(row, dict) and row.get("user_id")})


def action_set_push_device_env(conn, token, env):
    """Correct a device's stored APNs env (e.g. after a fallback-host send
    succeeds), so future sends hit the right host first."""
    ensure_push_devices_table(conn)
    r.db(DB_NAME).table("PushDevices").get(str(token)).update({"env": str(env)}).run(conn)
    return {"token": str(token), "env": str(env)}


def action_delete_push_device(conn, token, user_id=None):
    """Remove a device token (logout / APNs 410 prune).

    When ``user_id`` is provided the delete is scoped to that owner (a user
    can't delete another user's token). The internal 410-prune path calls
    without a user_id since it has no user context."""
    ensure_push_devices_table(conn)
    row = r.db(DB_NAME).table("PushDevices").get(str(token))
    if user_id is not None:
        doc = row.run(conn)
        if doc and doc.get("user_id") != str(user_id):
            return {"deleted": None, "reason": "not_owner"}
    row.delete().run(conn)
    return {"deleted": str(token)}


def next_strategy_id(conn):
    """Return next available integer id for Strategies table."""
    cursor = r.db(DB_NAME).table("Strategies").run(conn)
    rows = list(cursor)
    if not rows:
        return 1
    ids = [x.get("id") for x in rows if isinstance(x.get("id"), (int, float))]
    if not ids:
        return 1
    return int(max(ids)) + 1


def parse_granularity_to_seconds(s, default_seconds=60):
    """Convert granularity string to seconds (e.g. 1m -> 60, 1h -> 3600)."""
    if s is None:
        return default_seconds
    s = str(s).strip().lower()
    if not s:
        return default_seconds
    if s.isdigit():
        return max(1, int(s))
    num_str = ""
    for c in s:
        if c in "0123456789.":
            num_str += c
        else:
            break
    try:
        num = float(num_str) if num_str else 1.0
    except ValueError:
        return default_seconds
    unit = s[len(num_str) :].strip() or "s"
    if unit.startswith("y"):
        return max(1, int(num * 365 * 24 * 3600))
    if unit.startswith("d"):
        return max(1, int(num * 24 * 3600))
    if unit.startswith("h"):
        return max(1, int(num * 3600))
    if unit.startswith("m"):
        return max(1, int(num * 60))
    if unit.startswith("s"):
        return max(1, int(num))
    return default_seconds


def random_backtest_id():
    return random.randint(100000, 999999)


def insert_backtest_with_unique_id(conn, doc, max_attempts=10):
    for _ in range(max_attempts):
        doc = dict(doc)
        doc["id"] = random_backtest_id()
        try:
            r.db(DB_NAME).table("BacktestInstances").insert(doc).run(conn)
            return doc["id"]
        except Exception as e:
            if "Duplicate" in str(e) or "duplicate" in str(e).lower() or "primary key" in str(e).lower():
                continue
            raise
    raise RuntimeError("Could not generate unique backtest id after %d attempts" % max_attempts)


def round_trip_stats(trades):
    """From list of trade dicts, return total_trades, total_buys, total_sells, cycle_pnls, winning, losing, breakeven."""
    if not trades:
        return 0, 0, 0, [], 0, 0, 0
    total_buys = sum(1 for t in trades if (t.get("action") or "").lower() == "buy")
    total_sells = sum(1 for t in trades if (t.get("action") or "").lower() == "sell")

    def ts_key(t):
        ts = t.get("timestamp")
        if ts is None:
            return ""
        return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

    sorted_trades = sorted(trades, key=ts_key)
    position = {}
    cost_basis = {}
    cycle_pnl_accum = {}
    cycle_pnls = []

    def parse_float(x):
        try:
            return float(x) if x is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    for t in sorted_trades:
        action = (t.get("action") or "").strip().lower()
        ticker = (t.get("ticker") or "").strip().upper()
        if not ticker or action not in ("buy", "sell"):
            continue
        shares = parse_float(t.get("shares"))
        total = parse_float(t.get("total"))
        if ticker not in position:
            position[ticker] = 0.0
            cost_basis[ticker] = 0.0
            cycle_pnl_accum[ticker] = 0.0
        if action == "buy":
            position[ticker] += shares
            cost_basis[ticker] += total
        else:
            if shares <= 0 or position[ticker] <= 0:
                continue
            sell_shares = min(shares, position[ticker])
            cost_sold = cost_basis[ticker] * (sell_shares / position[ticker]) if position[ticker] > 0 else 0.0
            proceeds = total * (sell_shares / shares) if shares else 0.0
            position[ticker] -= sell_shares
            cost_basis[ticker] -= cost_sold
            cycle_pnl_accum[ticker] += proceeds - cost_sold
            if position[ticker] == 0:
                cycle_pnls.append(cycle_pnl_accum[ticker])
                cycle_pnl_accum[ticker] = 0.0
    winning = sum(1 for p in cycle_pnls if p > 0)
    losing = sum(1 for p in cycle_pnls if p < 0)
    breakeven = sum(1 for p in cycle_pnls if p == 0)
    return len(trades), total_buys, total_sells, cycle_pnls, winning, losing, breakeven


# --- Actions (return JSON-serializable data; raise ValueError on error) ---

# Display names for engines (for status table)
ENGINE_DISPLAY_NAMES = {
    "backtest_engine": "Backtest Engine",
    "daily_digest_engine": "Daily Digest Engine",
    "discord_bot": "Discord Bot",
    "nexus_graph_engine": "Nexus Graph Engine",
    "price_engine": "Price Engine",
    "ai_backtest_engine": "AI Backtest Engine",
    "discover_engine": "Discover Engine",
}


def action_engines_status(conn):
    """Return status for all engines from EngineControl. List of { id, name, status, details }."""
    try:
        from engine_control import ALL_ENGINE_IDS, get_engine_doc, ensure_engine_control_table
    except ImportError:
        return {"engines": []}
    ensure_engine_control_table(conn)
    engines = []
    for eid in ALL_ENGINE_IDS:
        doc = get_engine_doc(conn, eid)
        name = ENGINE_DISPLAY_NAMES.get(eid, eid.replace("_", " ").title())
        running = bool(doc.get("running", False) if doc else False)
        terminate = bool(doc.get("terminate", False) if doc else False)
        details_parts = []
        if eid == "price_engine":
            if terminate:
                status = "Stopped (terminate)"
            else:
                status = "Running" if running else "Stopped"
            if doc and doc.get("run_price_service"):
                details_parts.append("broker on")
        elif eid == "discover_engine":
            if terminate:
                status = "Stopped (terminate)"
            else:
                status = "Running" if running else "Stopped"
        elif eid == "ai_backtest_engine":
            if doc and doc.get("paused"):
                status = "Paused"
                details_parts.append("paused")
            else:
                status = "Running" if running else "Stopped"
            if doc:
                if doc.get("resume_at"):
                    details_parts.append("resume @ " + str(doc.get("resume_at", ""))[:19])
                c = doc.get("count_today")
                if c is not None:
                    details_parts.append("today: %d" % int(c))
        elif eid == "daily_digest_engine":
            status = "Running" if running else "Stopped"
            if doc and doc.get("send_now"):
                details_parts.append("send_now")
            last = (doc or {}).get("last_sent_at") or (doc or {}).get("last_morning_at")
            if last:
                details_parts.append("last: " + str(last)[:16])
        elif eid == "nexus_graph_engine":
            status = "Running" if running else "Stopped"
            try:
                nexus_out = action_nexus_status(conn)
                gb = (nexus_out.get("graph_build") or {})
                pct = gb.get("progress_pct")
                if pct is not None:
                    details_parts.append("build %.0f%%" % float(pct))
                phase = (gb.get("stages") or [{}])[-1] if (gb.get("stages")) else {}
                if phase.get("message"):
                    details_parts.append(phase.get("message", "")[:30])
            except Exception:
                pass
        else:
            status = "Running" if running else "Stopped"
        details_str = " · ".join(details_parts) if details_parts else ""
        engines.append({
            "id": eid,
            "name": name,
            "status": status,
            "details": details_str,
        })
    return {"engines": engines}


def action_status(conn):
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "Config" not in tables:
        return {"config": None, "error": "Config table not found", "engines": []}
    config = r.db(DB_NAME).table("Config").get("Config").run(conn)
    config_out = None
    if config is not None:
        keys = ["runPriceService", "terminatePriceService", "terminatePriceBroker", "terminateDiscoverService"]
        config_out = {k: config.get(k) for k in keys}
    engines_out = action_engines_status(conn).get("engines", [])
    return {"config": config_out, "engines": engines_out}


def action_tickers(conn):
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "LivePricesStocks" not in tables:
        return {"tickers": [], "count": 0}
    cursor = r.db(DB_NAME).table("LivePricesStocks").run(conn)
    rows = list(cursor)
    tickers = [str(row.get("ticker", row.get("id", ""))) for row in rows]
    return {"tickers": tickers, "count": len(tickers)}


def action_add_ticker(conn, symbols):
    if not symbols:
        raise ValueError("At least one symbol required")
    ensure_live_prices_stocks_table(conn)
    added = []
    for sym in symbols:
        ticker = sym.upper().strip()
        if not ticker:
            continue
        r.db(DB_NAME).table("LivePricesStocks").insert(
            {"id": ticker, "ticker": ticker}, conflict="replace"
        ).run(conn)
        added.append(ticker)
    return {"added": added}


def action_remove_ticker(conn, symbol):
    if not symbol or not str(symbol).strip():
        raise ValueError("Symbol required")
    ticker = str(symbol).upper().strip()
    result = r.db(DB_NAME).table("LivePricesStocks").get(ticker).delete().run(conn)
    deleted = result.get("deleted", 0)
    return {"removed": deleted > 0, "ticker": ticker}


def action_prices(conn):
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "LivePrices" not in tables:
        return {"prices": []}
    cursor = r.db(DB_NAME).table("LivePrices").run(conn)
    rows = list(cursor)
    rows_sorted = sorted(rows, key=lambda x: str(x.get("ticker", "")))
    prices = [
        {"ticker": row.get("ticker", row.get("id")), "price": row.get("price")}
        for row in rows_sorted
    ]
    return {"prices": prices, "count": len(prices)}


def action_history(conn, ticker=None, limit=30):
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "PriceHistory" not in tables:
        return {"history": []}
    q = r.db(DB_NAME).table("PriceHistory")
    if ticker:
        sym = ticker.upper().strip()
        if not sym.startswith("T."):
            sym = "T." + sym
        q = q.filter(r.row["ticker"] == sym)
    q = q.order_by(r.desc("timestamp")).limit(min(int(limit) if limit else 30, 500))
    rows = list(q.run(conn))
    history = [
        {
            "ticker": row.get("ticker"),
            "price": row.get("price"),
            "timestamp": row.get("timestamp"),
            "type": row.get("type"),
        }
        for row in rows
    ]
    return {"history": history, "count": len(history)}


def action_instances(conn):
    ensure_instances_table(conn)
    cursor = (
        r.db(DB_NAME)
        .table("Instances")
        .pluck(
            "id",
            "name",
            "runCommand",
            "stocks",
            "strategy_id",
            "created_by",
            "brokerage_id",
            "max_usage",
        )
        .run(conn)
    )
    rows = list(cursor)
    instances = []
    for row in rows:
        instances.append({
            "id": row.get("id"),
            "name": row.get("name"),
            "runCommand": row.get("runCommand", False),
            "stocks": row.get("stocks") or [],
            "strategy_id": row.get("strategy_id"),
            "created_by": row.get("created_by", "user"),
            "brokerage_id": row.get("brokerage_id"),
            "max_usage": row.get("max_usage"),
        })
    return {"instances": instances}


def action_create_instance(
    conn,
    instance_id,
    name=None,
    strategy_id=None,
    key=None,
    secret=None,
    granularity_time_increment=60,
    run_command=False,
    created_by="user",
    brokerage_id=None,
    max_usage=None,
):
    if not instance_id or not str(instance_id).strip():
        raise ValueError("Instance ID required")
    instance_id = str(instance_id).strip()
    ensure_instances_table(conn)
    granularity_sec = (
        parse_granularity_to_seconds(granularity_time_increment)
        if granularity_time_increment is not None
        else 60
    )
    # Default Alpaca key/secret from env so backtests and live runs can fetch bars (e.g. AI-created instances)
    key_val = (key or "").strip()
    secret_val = (secret or "").strip()
    if not key_val:
        key_val = (os.environ.get("APCA_API_KEY_ID") or os.environ.get("KEY") or "").strip()
    if not secret_val:
        secret_val = (os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("SECRET") or "").strip()
    doc = {
        "id": instance_id,
        "key": key_val,
        "secret": secret_val,
        "runCommand": bool(run_command),
        "stocks": [],
        "granularity_time_increment": granularity_sec,
        "created_by": created_by if created_by in ("ai", "user") else "user",
    }
    if name:
        doc["name"] = name
    if brokerage_id:
        doc["brokerage_id"] = str(brokerage_id)
    if max_usage is not None:
        try:
            doc["max_usage"] = float(max_usage)
        except (TypeError, ValueError):
            pass
    if strategy_id is not None:
        try:
            doc["strategy_id"] = int(strategy_id)
        except (TypeError, ValueError):
            pass
    r.db(DB_NAME).table("Instances").insert(doc, conflict="replace").run(conn)
    return {"id": instance_id, "name": name, "strategy_id": doc.get("strategy_id")}


def action_edit_instance(
    conn,
    instance_id,
    name=None,
    granularity_time_increment=None,
    max_usage=None,
    brokerage_id=None,
):
    if not instance_id or not str(instance_id).strip():
        raise ValueError("Instance ID required")
    instance_id = str(instance_id).strip()

    ensure_instances_table(conn)
    doc = _resolve_instance_doc(conn, instance_id)
    if doc is None:
        raise ValueError("Instance not found: %s" % instance_id)

    instance_id_actual = doc.get("id", instance_id)
    updates = {}

    if name is not None:
        n = str(name).strip()
        updates["name"] = n if n else None

    if granularity_time_increment is not None:
        updates["granularity_time_increment"] = parse_granularity_to_seconds(granularity_time_increment)

    if max_usage is not None:
        try:
            updates["max_usage"] = float(max_usage)
        except (TypeError, ValueError):
            raise ValueError("max_usage must be a number")

    if brokerage_id is not None:
        bid = str(brokerage_id).strip()
        if bid:
            _ensure_brokerage_accounts_table(conn)
            brok = r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE).get(bid).run(conn)
            if brok is None:
                raise ValueError("Brokerage account not found: %s" % bid)
            updates["brokerage_id"] = bid
        else:
            updates["brokerage_id"] = None

    if not updates:
        raise ValueError("No editable fields provided")

    r.db(DB_NAME).table("Instances").get(instance_id_actual).update(updates).run(conn)
    return action_get_instance(conn, instance_id_actual)


def action_delete_instance(conn, instance_id, force=False):
    if not instance_id or not str(instance_id).strip():
        raise ValueError("Instance ID required")
    instance_id = instance_id.strip()
    ensure_instances_table(conn)
    doc = _resolve_instance_doc(conn, instance_id)
    if doc is None:
        return {"deleted": False, "id": instance_id}
    instance_id_actual = doc.get("id", instance_id)
    strategy_id = doc.get("strategy_id")
    if strategy_id is not None and not force:
        instances_using = list(
            r.db(DB_NAME).table("Instances").filter(r.row["strategy_id"] == strategy_id).run(conn)
        )
        if any(str(i.get("id")) == str(instance_id_actual) for i in instances_using):
            strat_doc = r.db(DB_NAME).table("Strategies").get(strategy_id).run(conn)
            strat_name = strat_doc.get("name", strategy_id) if strat_doc else strategy_id
            raise ValueError(
                "Instance is linked to strategy id=%s '%s'. Use force=true to delete anyway."
                % (strategy_id, strat_name)
            )
    r.db(DB_NAME).table("Instances").get(instance_id_actual).delete().run(conn)
    return {"deleted": True, "id": instance_id_actual}


def action_add_stock(conn, instance_id, symbol):
    if not instance_id or not symbol:
        raise ValueError("Instance ID and symbol required")
    instance_id = instance_id.strip()
    symbol = symbol.strip().upper()
    ensure_instances_table(conn)
    doc = _resolve_instance_doc(conn, instance_id)
    if doc is None:
        raise ValueError("Instance not found: %s" % instance_id)
    instance_id_actual = doc.get("id", instance_id)
    stocks = list(doc.get("stocks") or [])
    if symbol in stocks:
        return {"added": False, "message": "Symbol already in instance", "stocks_count": len(stocks)}
    stocks.append(symbol)
    r.db(DB_NAME).table("Instances").get(instance_id_actual).update({"stocks": stocks}).run(conn)
    ensure_live_prices_stocks_table(conn)
    if r.db(DB_NAME).table("LivePricesStocks").get(symbol).run(conn) is None:
        r.db(DB_NAME).table("LivePricesStocks").insert({"id": symbol, "ticker": symbol}).run(conn)
    return {"added": True, "symbol": symbol, "stocks_count": len(stocks)}


def action_remove_stock(conn, instance_id, symbol):
    if not instance_id or not symbol:
        raise ValueError("Instance ID and symbol required")
    instance_id = instance_id.strip()
    symbol = symbol.strip().upper()
    doc = _resolve_instance_doc(conn, instance_id)
    if doc is None:
        raise ValueError("Instance not found: %s" % instance_id)
    instance_id_actual = doc.get("id", instance_id)
    stocks = list(doc.get("stocks") or [])
    if symbol not in stocks:
        return {"removed": False, "message": "Symbol not in instance", "stocks_count": len(stocks)}
    stocks = [s for s in stocks if str(s).strip().upper() != symbol]
    r.db(DB_NAME).table("Instances").get(instance_id_actual).update({"stocks": stocks}).run(conn)
    return {"removed": True, "symbol": symbol, "stocks_count": len(stocks)}


def action_start_instance(conn, instance_id):
    if not instance_id or not str(instance_id).strip():
        raise ValueError("Instance ID required")
    instance_id = instance_id.strip()
    doc = _resolve_instance_doc(conn, instance_id)
    if doc is None:
        raise ValueError("Instance not found: %s" % instance_id)
    instance_id_actual = doc.get("id", instance_id)
    import datetime
    r.db(DB_NAME).table("Instances").get(instance_id_actual).update({
        "runCommand": True,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    }).run(conn)
    return {"started": True, "id": instance_id_actual}


def action_stop_instance(conn, instance_id):
    if not instance_id or not str(instance_id).strip():
        raise ValueError("Instance ID required")
    instance_id = instance_id.strip()
    doc = _resolve_instance_doc(conn, instance_id)
    if doc is None:
        raise ValueError("Instance not found: %s" % instance_id)
    instance_id_actual = doc.get("id", instance_id)
    r.db(DB_NAME).table("Instances").get(instance_id_actual).update({
        "runCommand": False,
        "started_at": None,
    }).run(conn)
    return {"stopped": True, "id": instance_id_actual}


def action_get_instance(conn, instance_id):
    """Return full instance detail: strategy, brokerage, backtests, uptime."""
    ensure_instances_table(conn)
    doc = _resolve_instance_doc(conn, instance_id)
    if doc is None:
        raise ValueError("Instance not found: %s" % instance_id)

    # Strategy details
    strategy = None
    sid = doc.get("strategy_id")
    if sid is not None:
        try:
            strategy = action_get_strategy(conn, sid)
        except Exception:
            pass

    # Brokerage details
    brokerage = None
    bid = doc.get("brokerage_id")
    if bid:
        try:
            _ensure_brokerage_accounts_table(conn)
            brok_doc = r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE).get(bid).run(conn)
            if brok_doc:
                brokerage = _mask_brokerage_doc(brok_doc)
        except Exception:
            pass

    # Optional market-data brokerage (separate Alpaca row for bars/news fetches).
    data_brokerage = None
    data_bid = doc.get("alpaca_data_brokerage_id")
    if data_bid:
        try:
            _ensure_brokerage_accounts_table(conn)
            data_doc = r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE).get(data_bid).run(conn)
            if data_doc:
                data_brokerage = _mask_brokerage_doc(data_doc)
        except Exception:
            pass

    # Backtests for this instance
    backtests = []
    try:
        ensure_backtest_instances_table(conn)
        tables = list(r.db(DB_NAME).table_list().run(conn))
        # Use the "instance" secondary index when present — same
        # optimisation as action_list_backtests. Falls back to filter()
        # when the index isn't ready (e.g. first deploy of that index).
        # Without this, the instance detail page scanned the entire
        # BacktestInstances table on every load, which gets slow once
        # an instance has dozens of historical backtests.
        _bt_instance_filter = str(doc.get("id", instance_id))
        try:
            _bt_idx = set(
                r.db(DB_NAME).table("BacktestInstances").index_list().run(conn)
            )
        except Exception:
            _bt_idx = set()
        if "instance" in _bt_idx:
            bt_rows = list(
                r.db(DB_NAME).table("BacktestInstances")
                .get_all(_bt_instance_filter, index="instance")
                .run(conn)
            )
        else:
            bt_rows = list(
                r.db(DB_NAME).table("BacktestInstances")
                .filter(r.row["instance"] == _bt_instance_filter)
                .run(conn)
            )
        has_results = "BacktestResults" in tables
        for row in bt_rows:
            rid = row.get("id")
            result_doc = None
            if has_results:
                result_doc = r.db(DB_NAME).table("BacktestResults").get(rid).run(conn)
            status = str(row.get("status", "queued"))
            pnl = pnl_pct = progress = None
            if result_doc:
                status = str(result_doc.get("status") or status)
                pnl = result_doc.get("pnl")
                pnl_pct = result_doc.get("pnl_percent")
                progress = result_doc.get("progress")
            backtests.append({
                "id": rid,
                "stocks": row.get("stocks") or [],
                "start_date": str(row.get("start-date", ""))[:10],
                "end_date": str(row.get("end-date", ""))[:10],
                "granularity": row.get("granularity_sec"),
                "initial_cash": row.get("initial_cash"),
                "status": status,
                "pnl": pnl,
                "pnl_percent": pnl_pct,
                "progress": progress,
            })
        backtests.sort(key=lambda x: x.get("id") or 0, reverse=True)
    except Exception:
        pass

    # Uptime
    started_at = doc.get("started_at")
    uptime_seconds = None
    if doc.get("runCommand"):
        try:
            import datetime as _dt

            dt = None
            if started_at:
                sa = started_at if isinstance(started_at, str) else str(started_at)
                dt = _dt.datetime.fromisoformat(sa.replace("Z", "+00:00"))
            else:
                # Legacy fallback from instance.py (`r.now()` stored in `uptimeStart`)
                uptime_start = doc.get("uptimeStart")
                if isinstance(uptime_start, dict):
                    epoch = uptime_start.get("epoch_time")
                    if epoch is not None:
                        dt = _dt.datetime.fromtimestamp(float(epoch), tz=_dt.timezone.utc)
                elif isinstance(uptime_start, str) and uptime_start.strip():
                    dt = _dt.datetime.fromisoformat(uptime_start.replace("Z", "+00:00"))

            if dt is not None:
                # Treat naive timestamps as UTC (how `started_at` is currently written).
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_dt.timezone.utc)
                now = _dt.datetime.now(_dt.timezone.utc)
                uptime_seconds = max(0, int((now - dt).total_seconds()))
        except Exception:
            pass

    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "runCommand": doc.get("runCommand", False),
        "stocks": doc.get("stocks") or [],
        "strategy_id": sid,
        "strategy": strategy,
        "brokerage_id": bid,
        "brokerage": brokerage,
        "alpaca_data_brokerage_id": data_bid,
        "alpaca_data_brokerage": data_brokerage,
        "max_usage": doc.get("max_usage"),
        "granularity_time_increment": doc.get("granularity_time_increment"),
        "created_by": doc.get("created_by", "user"),
        "started_at": started_at,
        "uptime_seconds": uptime_seconds,
        "backtests": backtests,
    }


def action_ensure_ai_alpaca_brokerage(conn):
    """Create (or find existing) Alpaca brokerage for the AI engine using env vars. Returns brokerage ID or None."""
    key = (os.environ.get("APCA_API_KEY_ID") or os.environ.get("KEY") or "").strip()
    secret = (os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("SECRET") or "").strip()
    if not key or not secret:
        return None
    _ensure_brokerage_accounts_table(conn)
    existing = list(
        r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE)
        .filter({"account_name": "AI Engine (Alpaca)"})
        .run(conn)
    )
    if existing:
        return existing[0].get("id")
    try:
        result = action_link_alpaca(conn, "AI Engine (Alpaca)", key, secret, paper=True)
        return (result.get("account") or {}).get("id")
    except Exception:
        return None


def action_terminate_price(conn):
    """Set EngineControl.price_engine.terminate=True so price engine exits. Keeps Config in sync for backward compat."""
    try:
        from engine_control import ENGINE_ID_PRICE, ensure_engine_control_table, update_engine_doc
        ensure_engine_control_table(conn)
        update_engine_doc(conn, ENGINE_ID_PRICE, {"terminate": True})
    except Exception:
        pass
    r.db(DB_NAME).table("Config").get("Config").update({"terminatePriceService": True}).run(conn)
    return {"ok": True}


def action_terminate_discover(conn):
    """Set EngineControl.discover_engine.terminate=True so discover engine exits."""
    try:
        from engine_control import ENGINE_ID_DISCOVER, ensure_engine_control_table, update_engine_doc
        ensure_engine_control_table(conn)
        update_engine_doc(conn, ENGINE_ID_DISCOVER, {"terminate": True, "running": False})
    except Exception:
        pass
    r.db(DB_NAME).table("Config").get("Config").update({"terminateDiscoverService": True}).run(conn)
    return {"ok": True}


def action_start_broker(conn):
    """Set EngineControl.price_engine.run_price_service=True so price engine starts broker."""
    try:
        from engine_control import ENGINE_ID_PRICE, ensure_engine_control_table, update_engine_doc
        ensure_engine_control_table(conn)
        update_engine_doc(conn, ENGINE_ID_PRICE, {"run_price_service": True})
    except Exception:
        pass
    r.db(DB_NAME).table("Config").get("Config").update({"runPriceService": True}).run(conn)
    return {"ok": True}


# --- Discover Engine (EngineControl.discover_engine) ---

try:
    from engine_control import ENGINE_ID_DISCOVER as DISCOVER_ENGINE_ID
except ImportError:
    DISCOVER_ENGINE_ID = "discover_engine"


def ensure_discover_control_table(conn):
    ensure_engine_control_table(conn)


def action_discover_control_get(conn):
    ensure_discover_control_table(conn)
    doc = get_engine_doc(conn, DISCOVER_ENGINE_ID)
    if doc is None:
        return {"running": False, "terminate": False}
    return {
        "running": bool(doc.get("running", False)),
        "terminate": bool(doc.get("terminate", False)),
    }


def action_discover_control_set(conn, running=None):
    """Start or stop the discover engine. Normal stop clears terminate."""
    ensure_discover_control_table(conn)
    update = {}
    if running is not None:
        update["running"] = bool(running)
        update["terminate"] = False
        try:
            r.db(DB_NAME).table("Config").get("Config").update({"terminateDiscoverService": False}).run(conn)
        except Exception:
            pass
    if update:
        update_engine_doc(conn, DISCOVER_ENGINE_ID, update)
    return action_discover_control_get(conn)


# --- AI Backtesting Agent (EngineControl.ai_backtest_engine) ---

try:
    from engine_control import (
        ENGINE_ID_AI_BACKTEST as AGENT_ENGINE_ID,
        ensure_engine_control_table,
        get_engine_doc,
        update_engine_doc,
    )
except ImportError:
    AGENT_ENGINE_ID = "ai_backtest_engine"
    def ensure_engine_control_table(conn): pass
    def get_engine_doc(conn, eid): return None
    def update_engine_doc(conn, eid, update): pass


def ensure_agent_control_table(conn):
    ensure_engine_control_table(conn)


def ensure_ai_backtesting_results_table(conn):
    dbs = list(r.db_list().run(conn))
    if DB_NAME not in dbs:
        r.db_create(DB_NAME).run(conn)
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "AIBacktestingResults" not in tables:
        r.db(DB_NAME).table_create("AIBacktestingResults").run(conn)


def action_agent_control_get(conn):
    ensure_agent_control_table(conn)
    doc = get_engine_doc(conn, AGENT_ENGINE_ID)
    if doc is None:
        return {"running": False, "last_run_date": None, "count_today": 0, "resume_at": None}
    return {
        "running": bool(doc.get("running")),
        "paused": bool(doc.get("paused")),
        "last_run_date": doc.get("last_run_date"),
        "count_today": int(doc.get("count_today") or 0),
        "resume_at": doc.get("resume_at"),
        "special_request": (doc.get("special_request") or "").strip() or None,
    }


def action_agent_control_set(conn, running=None, paused=None, special_request=None):
    """Update running and/or paused. Pass None to leave unchanged.
    special_request: optional instruction for strategy generation (e.g. "include nexus in each"); set when starting agent, cleared when stopping.
    When paused=True is set, ensures running=True (agent stays alive while paused).
    When resume_at is set, running is preserved as True.
    When paused=True, running cannot be set to False."""
    ensure_agent_control_table(conn)
    update = {}
    
    # Get current state to check resume_at and paused
    current = get_engine_doc(conn, AGENT_ENGINE_ID)
    has_resume_at = current and current.get("resume_at")
    is_paused = current and current.get("paused", False)
    resume_at_cleared_at_str = current and current.get("resume_at_cleared_at")
    
    # Check if resume_at was recently cleared (within last 60 seconds) - prevent running=False during this window
    resume_at_recently_cleared = False
    if resume_at_cleared_at_str:
        try:
            from datetime import datetime, timedelta, timezone
            cleared_at = datetime.fromisoformat(resume_at_cleared_at_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc).replace(tzinfo=cleared_at.tzinfo) if cleared_at.tzinfo else datetime.now(timezone.utc)
            if (now - cleared_at).total_seconds() < 60:  # Within 60 seconds
                resume_at_recently_cleared = True
        except Exception:
            pass
    
    if running is not None:
        if not running:
            # Explicit stop always wins — clear paused and resume_at too so the
            # agent process and watcher both see a clean stopped state.
            update["running"] = False
            update["paused"] = False
            update["resume_at"] = None
        else:
            update["running"] = True
            # If setting running=True, clear resume_at (no longer needed)
            update["resume_at"] = None

    if paused is not None:
        update["paused"] = bool(paused)
        # When setting paused=True, ensure running=True (agent stays alive while paused)
        if paused is True:
            update["running"] = True

    if special_request is not None:
        update["special_request"] = (special_request or "").strip() or None
    if update.get("running") is False:
        # When stopping agent, clear special_request so next start is fresh
        update["special_request"] = None

    if update:
        update_engine_doc(conn, AGENT_ENGINE_ID, update)

    # When agent is stopped, clean up all stale running state
    if update.get("running") is False:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat() + "Z"
        # Mark stale running cycle log entries as stopped
        try:
            ensure_agent_cycle_log_table(conn)
            r.db(DB_NAME).table(AGENT_CYCLE_LOG_TABLE).filter(
                r.row["status"].eq("running")
            ).update({"status": "stopped", "updated_at": now}).run(conn)
        except Exception:
            pass
        # Stop all running backtests and mark BacktestResults as stopped
        try:
            action_stop_all_backtests(conn)
        except Exception:
            pass

    return action_agent_control_get(conn)


def action_resume_timer(conn, days=0, hours=0, minutes=0, seconds=0):
    """Schedule agent to resume after specified time. Returns updated control state.
    Sets running=True so container stays alive; agent will check resume_at and wait."""
    from datetime import datetime, timedelta, timezone
    ensure_agent_control_table(conn)
    total_seconds = (days * 86400) + (hours * 3600) + (minutes * 60) + seconds
    if total_seconds <= 0:
        raise ValueError("Total time must be greater than 0")
    resume_at = datetime.now(timezone.utc) + timedelta(seconds=total_seconds)
    update_engine_doc(conn, AGENT_ENGINE_ID, {
        "resume_at": resume_at.isoformat() + "Z",
        "running": True,
        "paused": False,
    })
    return action_agent_control_get(conn)


def action_agent_restart(conn, special_request=None, delay_seconds=5):
    """Restart the agent: stop, wait, then start (with optional special_request).
    Returns dict with status info. The actual container stop/start is handled by the changefeed in server.py."""
    import time as _time
    ensure_agent_control_table(conn)
    # Stop
    action_agent_control_set(conn, running=False)
    # Wait for container to stop
    _time.sleep(delay_seconds)
    # Start with optional special_request
    action_agent_control_set(conn, running=True, special_request=special_request)
    result = action_agent_control_get(conn)
    result["restarted"] = True
    result["delay_seconds"] = delay_seconds
    return result


def action_agent_increment_backtest_count(conn):
    """Increment count_today; if last_run_date is not today (UTC), reset count_today to 0 first. Returns new count_today."""
    from datetime import datetime, timezone
    ensure_agent_control_table(conn)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    doc = get_engine_doc(conn, AGENT_ENGINE_ID)
    if not doc:
        return 0
    last = doc.get("last_run_date")
    count = int(doc.get("count_today") or 0)
    if last != today:
        count = 0
    count += 1
    update_engine_doc(conn, AGENT_ENGINE_ID, {"count_today": count, "last_run_date": today})
    return count


def action_list_ai_backtest_results(conn, limit=100):
    ensure_ai_backtesting_results_table(conn)
    cursor = r.db(DB_NAME).table("AIBacktestingResults").order_by(r.desc("created_at")).limit(limit).run(conn)
    rows = list(cursor)
    return {"results": rows}


def action_list_ai_backtest_results_since(conn, since_iso: str, limit=200):
    """List AI backtest results with created_at >= since_iso (ISO string). For digest summaries."""
    ensure_ai_backtesting_results_table(conn)
    cursor = (
        r.db(DB_NAME)
        .table("AIBacktestingResults")
        .filter(r.row["created_at"].ge(since_iso))
        .order_by(r.desc("created_at"))
        .limit(limit)
        .run(conn)
    )
    rows = list(cursor)
    return {"results": rows}


def action_insert_ai_backtest_result(
    conn,
    strategy_snapshot,
    backtest_id,
    instance_id,
    strategy_id,
    overall_profit,
    pnl_percent,
    pnl_per_stock,
    pnl_percent_per_stock,
    stock_price_change,
    start_date,
    end_date,
    stocks_used,
    status="passed",
    agent_notes=None,
):
    ensure_ai_backtesting_results_table(conn)
    import uuid
    from datetime import datetime, timezone
    doc = {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat() + "Z",
        "strategy_snapshot": strategy_snapshot,
        "backtest_id": backtest_id,
        "instance_id": instance_id,
        "strategy_id": strategy_id,
        "overall_profit": overall_profit,
        "pnl_percent": pnl_percent,
        "pnl_per_stock": pnl_per_stock or {},
        "pnl_percent_per_stock": pnl_percent_per_stock or {},
        "stock_price_change": stock_price_change or {},
        "start_date": start_date,
        "end_date": end_date,
        "stocks_used": stocks_used or [],
        "status": status,
        "agent_notes": agent_notes,
    }
    r.db(DB_NAME).table("AIBacktestingResults").insert(doc).run(conn)
    return doc


AGENT_BEST_ID = "best"
AGENT_TOP5_ID = "top5"
AGENT_TOP5_SIZE = 5


def ensure_agent_best_table(conn):
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "AgentBest" not in tables:
        r.db(DB_NAME).table_create("AgentBest").run(conn)


def _normalize_snapshot_to_strategies(strategy_snapshot):
    """Convert agent strategy_snapshot (name, strategies) to Strategies table format (list of sub-strategy dicts)."""
    raw = (strategy_snapshot or {}).get("strategies") or []
    normalized = []
    for s in raw:
        item = _normalize_strategy_payload_item(s, strict=True)
        if item is not None:
            normalized.append(item)
    return normalized


def action_agent_get_best(conn):
    """Return the current best strategy (from Strategies table with tag Best). Returns None if no best set."""
    ensure_agent_best_table(conn)
    ensure_strategies_table(conn)
    best_doc = r.db(DB_NAME).table("AgentBest").get(AGENT_BEST_ID).run(conn)
    if not best_doc:
        return None
    sid = best_doc.get("strategy_id")
    if sid is None:
        return None
    strat = r.db(DB_NAME).table("Strategies").get(sid).run(conn)
    if not strat:
        return None
    normalized_array = []
    for strategy_doc in (strat.get("strategies") or []):
        normalized_item = _normalize_strategy_payload_item(strategy_doc, strict=False)
        if normalized_item is not None:
            normalized_array.append(normalized_item)
    return {
        "id": strat.get("id"),
        "name": strat.get("name"),
        "strategies": normalized_array,
        "tags": strat.get("tags") or [],
        "strategy_snapshot": best_doc.get("strategy_snapshot"),
        "overall_profit": best_doc.get("overall_profit"),
        "pnl_percent": best_doc.get("pnl_percent"),
        "updated_at": best_doc.get("updated_at"),
        "results_summary": best_doc.get("results_summary"),
    }


def action_agent_set_best(conn, strategy_snapshot, overall_profit, pnl_percent, results_summary=None):
    """Persist strategy as best in Strategies with tag Best and update AgentBest. Caller decides when to set (e.g. after LLM best-selection)."""
    from datetime import datetime, timezone
    ensure_agent_best_table(conn)
    ensure_strategies_table(conn)
    overall_profit = float(overall_profit) if overall_profit is not None else 0.0
    pnl_percent = float(pnl_percent) if pnl_percent is not None else 0.0
    strategies_list = _normalize_snapshot_to_strategies(strategy_snapshot)
    if not strategies_list:
        return {"set": False, "reason": "invalid strategy_snapshot"}
    # Clear Best tag from any strategy that has it
    for row in r.db(DB_NAME).table("Strategies").run(conn):
        tags = row.get("tags") or []
        if isinstance(tags, list) and "Best" in tags:
            r.db(DB_NAME).table("Strategies").get(row["id"]).update({"tags": []}).run(conn)
    # Find or create "Agent Best" strategy
    agent_best_row = None
    for row in r.db(DB_NAME).table("Strategies").run(conn):
        if (row.get("name") or "").strip() == "Agent Best":
            agent_best_row = row
            break
    now = datetime.now(timezone.utc).isoformat() + "Z"
    if agent_best_row:
        sid = agent_best_row["id"]
        r.db(DB_NAME).table("Strategies").get(sid).update({
            "strategies": strategies_list,
            "tags": ["Best"],
        }).run(conn)
    else:
        next_id = next_strategy_id(conn)
        r.db(DB_NAME).table("Strategies").insert({
            "id": next_id,
            "name": "Agent Best",
            "strategies": strategies_list,
            "tags": ["Best"],
        }).run(conn)
        sid = next_id
    doc = {
        "id": AGENT_BEST_ID,
        "strategy_id": sid,
        "strategy_snapshot": strategy_snapshot,
        "overall_profit": overall_profit,
        "pnl_percent": pnl_percent,
        "updated_at": now,
    }
    if results_summary is not None:
        doc["results_summary"] = results_summary
    r.db(DB_NAME).table("AgentBest").insert(doc, conflict="replace").run(conn)
    return {"set": True, "strategy_id": sid, "overall_profit": overall_profit, "pnl_percent": pnl_percent}


# --- Agent Top-5 ---

def ensure_agent_top5_table(conn):
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "AgentTop5" not in tables:
        r.db(DB_NAME).table_create("AgentTop5").run(conn)


def action_agent_get_top5(conn):
    """Return the current top-5 strategies, sorted rank 1 (best) to 5 (worst).
    Auto-seeds from AIBacktestingResults if the table is empty."""
    from datetime import datetime, timezone

    ensure_agent_top5_table(conn)
    doc = r.db(DB_NAME).table("AgentTop5").get(AGENT_TOP5_ID).run(conn)
    entries = list(doc.get("entries") or []) if doc else []

    # Auto-seed from existing results if empty
    if not entries:
        ensure_ai_backtesting_results_table(conn)
        cursor = (
            r.db(DB_NAME)
            .table("AIBacktestingResults")
            .order_by(r.desc("pnl_percent"))
            .limit(AGENT_TOP5_SIZE)
            .run(conn)
        )
        rows = list(cursor)
        if rows:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            entries = []
            for i, row in enumerate(rows):
                snap = row.get("strategy_snapshot") or {}
                entries.append({
                    "strategy_snapshot": snap,
                    "overall_profit": row.get("overall_profit"),
                    "pnl_percent": row.get("pnl_percent"),
                    "strategy_id": row.get("strategy_id"),
                    "backtest_id": row.get("backtest_id"),
                    "results_summary": row.get("results_summary"),
                    "updated_at": row.get("created_at") or now,
                    "rank": i + 1,
                })
            r.db(DB_NAME).table("AgentTop5").insert(
                {"id": AGENT_TOP5_ID, "entries": entries, "updated_at": now},
                conflict="replace",
            ).run(conn)

    return {"top5": entries}


def action_agent_update_top5(
    conn,
    strategy_snapshot,
    overall_profit,
    pnl_percent,
    strategy_id=None,
    backtest_id=None,
    results_summary=None,
):
    """
    Compare candidate against current top-5 (ranked by pnl_percent desc).
    If candidate qualifies, insert it, re-sort, keep best AGENT_TOP5_SIZE.
    Returns: {"entered": bool, "rank": int|None, "dethroned": str|None, "top5": [...]}
    """
    from datetime import datetime, timezone

    ensure_agent_top5_table(conn)
    overall_profit = float(overall_profit) if overall_profit is not None else 0.0
    pnl_percent    = float(pnl_percent)    if pnl_percent    is not None else 0.0

    doc = r.db(DB_NAME).table("AgentTop5").get(AGENT_TOP5_ID).run(conn)
    entries = list(doc.get("entries") or []) if doc else []

    # Check if candidate qualifies (better than worst, or list not full)
    worst_pct = min(float(e.get("pnl_percent") or 0) for e in entries) if entries else None
    if len(entries) >= AGENT_TOP5_SIZE and (worst_pct is not None and pnl_percent <= worst_pct):
        return {"entered": False, "rank": None, "dethroned": None, "top5": entries}

    # Find entry that will be dethroned (the current worst)
    dethroned_name = None
    if len(entries) >= AGENT_TOP5_SIZE:
        worst_entry = min(entries, key=lambda e: float(e.get("pnl_percent") or 0))
        dethroned_name = (worst_entry.get("strategy_snapshot") or {}).get("name") or "Strategy #%s" % worst_entry.get("strategy_id", "?")
        entries = [e for e in entries if e is not worst_entry]

    now = datetime.now(timezone.utc).isoformat() + "Z"
    new_entry = {
        "strategy_snapshot": strategy_snapshot,
        "overall_profit": overall_profit,
        "pnl_percent": pnl_percent,
        "strategy_id": strategy_id,
        "backtest_id": backtest_id,
        "results_summary": results_summary,
        "updated_at": now,
    }
    entries.append(new_entry)
    # Sort by pnl_percent desc, assign ranks
    entries.sort(key=lambda e: float(e.get("pnl_percent") or 0), reverse=True)
    for i, e in enumerate(entries):
        e["rank"] = i + 1

    new_rank = next(i + 1 for i, e in enumerate(entries) if e is new_entry)

    r.db(DB_NAME).table("AgentTop5").insert(
        {"id": AGENT_TOP5_ID, "entries": entries, "updated_at": now},
        conflict="replace",
    ).run(conn)
    return {"entered": True, "rank": new_rank, "dethroned": dethroned_name, "top5": entries}


# --- Daily Digest Engine (EngineControl.daily_digest_engine) ---

try:
    from engine_control import ENGINE_ID_DAILY_DIGEST as DIGEST_ENGINE_ID
except ImportError:
    DIGEST_ENGINE_ID = "daily_digest_engine"

DIGEST_CONTROL_ID = DIGEST_ENGINE_ID  # alias for callers that use this name


def ensure_digest_control_table(conn):
    ensure_engine_control_table(conn)


def action_digest_control_get(conn):
    ensure_digest_control_table(conn)
    doc = get_engine_doc(conn, DIGEST_ENGINE_ID)
    if doc is None:
        return {"running": True, "send_now": False, "last_sent_at": None}
    return {
        "running": bool(doc.get("running", True)),
        "send_now": bool(doc.get("send_now", False)),
        "last_sent_at": doc.get("last_sent_at"),
        "last_morning_at": doc.get("last_morning_at"),
        "last_evening_at": doc.get("last_evening_at"),
    }


def action_digest_control_set(conn, running=None, send_now=None):
    """Update digest engine control. Pass None to leave unchanged. Returns current state."""
    ensure_digest_control_table(conn)
    update = {}
    if running is not None:
        update["running"] = bool(running)
    if send_now is not None:
        update["send_now"] = bool(send_now)
    if update:
        update_engine_doc(conn, DIGEST_ENGINE_ID, update)
    return action_digest_control_get(conn)


def action_digest_trigger_send_now(conn):
    """Set send_now=True and running=True so the digest engine sends a summary. Ensures digest container is started if not running."""
    return action_digest_control_set(conn, send_now=True, running=True)


def action_digest_mark_sent(conn, kind="morning"):
    """Called by digest engine after sending; clears send_now and sets last_sent_at, last_morning_at or last_evening_at."""
    from datetime import datetime, timezone
    ensure_digest_control_table(conn)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    update = {"send_now": False, "last_sent_at": now}
    if kind == "morning":
        update["last_morning_at"] = now
    elif kind == "evening":
        update["last_evening_at"] = now
    update_engine_doc(conn, DIGEST_ENGINE_ID, update)
    return action_digest_control_get(conn)


# --- Graph Nexus Engine (EngineControl.nexus_graph_engine) ---

try:
    from engine_control import ENGINE_ID_NEXUS_GRAPH as NEXUS_ENGINE_ID
except ImportError:
    NEXUS_ENGINE_ID = "nexus_graph_engine"

NEXUS_CONTROL_ID = NEXUS_ENGINE_ID  # alias
NEXUS_CONTAINER_NAME = "intellistock-graph-nexus"
NEXUS_CONTAINER_CACHE_ROOT = "/app/.cache"
_NEXUS_GRAPH_SUMMARY_CACHE = {"fetched_at": 0.0, "data": None}
_NEXUS_GRAPH_SUMMARY_TTL_SEC = max(5, int(os.environ.get("NEXUS_GRAPH_SUMMARY_TTL_SEC", "30")))
_NEXUS_RELATIONSHIP_LABELS = [
    ("IN_SECTOR", "In Sector"),
    ("SUPPLIER_OF", "Supplier Of"),
    ("STRATEGIC_PARTNER", "Strategic Partner"),
    ("COMPETES_WITH", "Competes With"),
    ("SUPPLIES_TO_SECTOR", "Supplies To Sector"),
    ("PARENT_OF", "Parent Of"),
    ("PARENT_OF_LEI", "Parent Of LEI"),
    ("HOLDS", "13F Holds"),
    ("CONTRACTS_WITH", "Contracts With"),
    ("CONTROLS", "Controls"),
    ("PATENT_PARTNER", "Patent Partner"),
    ("ETF_TRACKS_SECTOR", "ETF Tracks Sector"),
    ("ETF_TRACKS_THEME", "ETF Tracks Theme"),
    ("ETF_HOLDS", "ETF Holds"),
]
_NEXUS_PHASE3_DERIVED_CACHE_PATHS = [
    "parsed_edges",
    "supply_chain_sec_edgar.csv",
]


def _candidate_nexus_cache_roots():
    roots = []
    seen = set()
    for raw_root in (
        NEXUS_CONTAINER_CACHE_ROOT,
        os.environ.get("GRAPH_NEXUS_CACHE_DIR"),
    ):
        if not raw_root:
            continue
        root = os.path.realpath(str(raw_root))
        if root in seen:
            continue
        seen.add(root)
        roots.append(root)
    return roots


def _get_direct_nexus_cache_root():
    for root in _candidate_nexus_cache_roots():
        if os.path.isdir(root):
            return root
    return None


def _list_nexus_cache_entries_on_filesystem(root):
    entries = []
    if not root or not os.path.isdir(root):
        return entries
    for name in sorted(os.listdir(root), key=lambda value: value.lower()):
        path = os.path.join(root, name)
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            continue
        entries.append({
            "name": name,
            "path": name,
            "is_dir": os.path.isdir(path),
            "size_bytes": None if os.path.isdir(path) else int(stat.st_size),
            "modified_at": datetime.datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z",
        })
    return sorted(
        [entry for entry in entries if isinstance(entry, dict) and entry.get("path")],
        key=lambda entry: (not bool(entry.get("is_dir")), str(entry.get("path", "")).lower()),
    )


def _delete_nexus_cache_entries_on_filesystem(root, rel_paths):
    if not rel_paths:
        return []
    if not root or not os.path.isdir(root):
        raise ValueError("Nexus cache root is not available on the API filesystem.")
    real_root = os.path.realpath(root)
    deleted = []
    missing = []
    for rel in rel_paths:
        rel = str(rel or "").replace("\\", "/").strip().strip("/")
        if not rel:
            continue
        target = os.path.realpath(os.path.join(real_root, rel))
        if os.path.commonpath([real_root, target]) != real_root or target == real_root:
            raise ValueError("Invalid cache path: %s" % rel)
        if not os.path.exists(target):
            missing.append(rel)
            continue
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
        deleted.append(rel)
    if missing:
        raise ValueError("Some selected cache entries were not found: %s" % ", ".join(sorted(set(missing))))
    return deleted


def _get_docker_client_or_raise():
    try:
        import docker
        return docker.from_env()
    except Exception as exc:
        raise ValueError("Docker is unavailable: %s" % exc)


def _get_nexus_container(client=None):
    try:
        docker_client = client or _get_docker_client_or_raise()
    except ValueError:
        return None
    try:
        container = docker_client.containers.get(NEXUS_CONTAINER_NAME)
        try:
            container.reload()
        except Exception:
            pass
        return container
    except Exception:
        return None


def _get_running_nexus_container(client=None):
    container = _get_nexus_container(client)
    if container is None:
        return None
    try:
        status = str(getattr(container, "status", "") or "").strip().lower()
    except Exception:
        status = ""
    return container if status == "running" else None


def _docker_exec_result(result):
    exit_code = getattr(result, "exit_code", None)
    output = getattr(result, "output", None)
    if exit_code is None and isinstance(result, tuple) and len(result) == 2:
        exit_code, output = result
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    return int(exit_code or 0), output or ""


def _exec_python_in_nexus_container(container, script, *args):
    result = container.exec_run(
        ["python", "-c", script, *[str(arg) for arg in args]],
        user="root",
    )
    exit_code, output = _docker_exec_result(result)
    if exit_code != 0:
        raise ValueError(output.strip() or "Container command failed")
    return output


def _list_nexus_cache_entries_in_container(container):
    script = (
        "import datetime, json, os, sys\n"
        "root = sys.argv[1]\n"
        "entries = []\n"
        "if os.path.isdir(root):\n"
        "    for name in sorted(os.listdir(root), key=lambda value: value.lower()):\n"
        "        path = os.path.join(root, name)\n"
        "        try:\n"
        "            stat = os.stat(path)\n"
        "        except FileNotFoundError:\n"
        "            continue\n"
        "        entries.append({\n"
        "            'name': name,\n"
        "            'path': name,\n"
        "            'is_dir': os.path.isdir(path),\n"
        "            'size_bytes': None if os.path.isdir(path) else int(stat.st_size),\n"
        "            'modified_at': datetime.datetime.utcfromtimestamp(stat.st_mtime).isoformat() + 'Z',\n"
        "        })\n"
        "print(json.dumps(entries))\n"
    )
    raw = _exec_python_in_nexus_container(container, script, NEXUS_CONTAINER_CACHE_ROOT)
    try:
        entries = json.loads(raw or "[]")
    except Exception as exc:
        raise ValueError("Could not read Nexus cache listing: %s" % exc)
    return sorted(
        [entry for entry in entries if isinstance(entry, dict) and entry.get("path")],
        key=lambda entry: (not bool(entry.get("is_dir")), str(entry.get("path", "")).lower()),
    )


def _normalize_nexus_cache_delete_paths(requested_paths, available_entries):
    if not requested_paths:
        return []
    if not isinstance(requested_paths, list):
        raise ValueError("delete_cache_paths must be a list of cache entries.")
    allowed = {str(entry.get("path")).strip("/"): entry for entry in (available_entries or []) if entry.get("path")}
    normalized = []
    seen = set()
    for raw_path in requested_paths:
        if raw_path is None:
            continue
        rel_path = str(raw_path).strip().replace("\\", "/").strip("/")
        if not rel_path or rel_path in (".", ".."):
            raise ValueError("Invalid cache path: %s" % raw_path)
        if rel_path.startswith("../") or "/../" in rel_path or rel_path.startswith("/"):
            raise ValueError("Invalid cache path: %s" % raw_path)
        if rel_path not in allowed:
            raise ValueError("Unknown cache path selected for deletion: %s" % rel_path)
        if rel_path not in seen:
            normalized.append(rel_path)
            seen.add(rel_path)
    return normalized


def _delete_nexus_cache_entries_in_container(container, rel_paths):
    if not rel_paths:
        return []
    script = (
        "import json, os, shutil, sys\n"
        "root = os.path.realpath(sys.argv[1])\n"
        "requested = json.loads(sys.argv[2])\n"
        "deleted = []\n"
        "missing = []\n"
        "for rel in requested:\n"
        "    rel = str(rel or '').replace('\\\\', '/').strip().strip('/')\n"
        "    if not rel:\n"
        "        continue\n"
        "    target = os.path.realpath(os.path.join(root, rel))\n"
        "    if os.path.commonpath([root, target]) != root or target == root:\n"
        "        raise SystemExit('Invalid cache path: %s' % rel)\n"
        "    if not os.path.exists(target):\n"
        "        missing.append(rel)\n"
        "        continue\n"
        "    if os.path.isdir(target):\n"
        "        shutil.rmtree(target)\n"
        "    else:\n"
        "        os.remove(target)\n"
        "    deleted.append(rel)\n"
        "print(json.dumps({'deleted': deleted, 'missing': missing}))\n"
    )
    raw = _exec_python_in_nexus_container(
        container,
        script,
        NEXUS_CONTAINER_CACHE_ROOT,
        json.dumps(rel_paths),
    )
    try:
        payload = json.loads(raw or "{}")
    except Exception as exc:
        raise ValueError("Could not parse Nexus cache deletion response: %s" % exc)
    missing = payload.get("missing") or []
    if missing:
        raise ValueError("Some selected cache entries were not found: %s" % ", ".join(sorted(set(missing))))
    return payload.get("deleted") or []


def _restart_nexus_container(container):
    try:
        container.restart(timeout=10)
        try:
            container.reload()
        except Exception:
            pass
        return {"mode": "direct"}
    except Exception as exc:
        try:
            container.remove(force=True)
        except Exception as remove_exc:
            raise ValueError(
                "Nexus rebuild was queued, but the running container could not be restarted or removed: %s / %s"
                % (exc, remove_exc)
            )
        return {"mode": "server_recreate", "error": str(exc)}


def _remove_nexus_container(container):
    try:
        container.remove(force=True)
        return {"mode": "server_recreate"}
    except Exception as exc:
        raise ValueError("Could not remove the Nexus container for destructive rebuild: %s" % exc)


def _ensure_table(conn, table_name):
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if table_name not in tables:
        r.db(DB_NAME).table_create(table_name).run(conn)


def _upsert_nexus_progress_doc(conn, doc_id, updates):
    _ensure_table(conn, "GraphNexusProgress")
    payload = {"id": doc_id}
    payload.update(updates)
    r.db(DB_NAME).table("GraphNexusProgress").insert(payload, conflict="update").run(conn)


def _destructive_nexus_progress_reset_docs(queue_message):
    return (
        {
            "phase": 0,
            "last_completed_phase": 0,
            "progress_pct": 0.0,
            "status": "queued",
            "message": queue_message,
            "current_phase_label": "Queued rebuild",
            "current_phase_number": 1,
            "eta_remaining_sec": None,
            "stages": [],
            "phase_history": [],
            "started_at": None,
            "completed_at": None,
            "error": None,
        },
        {
            "last_ticker_index": 0,
            "progress_pct": 0.0,
            "status": "pending",
            "message": "Awaiting Phase 3 rebuild",
            "total_tickers": None,
            "edges_count": 0,
            "eta_remaining_sec": None,
            "current_phase_label": "",
            "current_phase_number": None,
            "error": None,
        },
    )


def _reset_nexus_rethink_status(conn, queue_message):
    graph_doc, scraper_doc = _destructive_nexus_progress_reset_docs(queue_message)
    graph_doc["last_updated"] = r.now()
    scraper_doc["last_updated"] = r.now()
    _upsert_nexus_progress_doc(conn, "graph_nexus", graph_doc)
    _upsert_nexus_progress_doc(conn, "sec_edgar_scraper", scraper_doc)


def _reset_nexus_bootstrap_progress_state(conn, queue_message=None, *, reset_progress_docs: bool):
    if reset_progress_docs:
        _reset_nexus_rethink_status(
            conn,
            queue_message or "Queued bootstrap rebuild from configured historical start date",
        )
    update_engine_doc(conn, NEXUS_ENGINE_ID, _nexus_bootstrap_reset_update(clear_start_date=False))


def _reset_nexus_graph_summary_cache():
    _NEXUS_GRAPH_SUMMARY_CACHE["fetched_at"] = 0.0
    _NEXUS_GRAPH_SUMMARY_CACHE["data"] = None


def _clear_nexus_graph_neo4j(progress_cb=None):
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "intellistock")
    batch_size = max(100, int(os.environ.get("NEXUS_NEO4J_CLEAR_BATCH_SIZE", "10000")))
    min_batch_size = max(50, int(os.environ.get("NEXUS_NEO4J_CLEAR_MIN_BATCH_SIZE", "500")))
    try:
        from neo4j import GraphDatabase
    except Exception as exc:
        raise ValueError("Neo4j driver is unavailable: %s" % exc)
    driver = None

    def _delete_relationship_batch(session, current_batch_size: int) -> int:
        result = session.run(
            """
            MATCH ()-[r]-()
            WITH r LIMIT $limit
            DELETE r
            RETURN count(*) AS deleted
            """,
            limit=current_batch_size,
        )
        record = result.single()
        return int((record or {}).get("deleted") or 0)

    def _delete_node_batch(session, current_batch_size: int) -> int:
        result = session.run(
            """
            MATCH (n)
            WITH n LIMIT $limit
            DETACH DELETE n
            RETURN count(*) AS deleted
            """,
            limit=current_batch_size,
        )
        record = result.single()
        return int((record or {}).get("deleted") or 0)

    def _run_batched_delete(session, delete_fn, label: str) -> None:
        current_batch_size = batch_size
        deleted_total = 0
        while True:
            try:
                deleted = delete_fn(session, current_batch_size)
            except Exception as exc:
                message = str(exc).lower()
                if (
                    current_batch_size > min_batch_size
                    and ("heap" in message or "memory" in message or "outofmemory" in message)
                ):
                    current_batch_size = max(min_batch_size, current_batch_size // 2)
                    if progress_cb is not None:
                        progress_cb(
                            label,
                            deleted_total,
                            None,
                            current_batch_size,
                            f"{label} hit memory pressure; retrying with smaller batch size {current_batch_size:,}.",
                        )
                    continue
                raise ValueError(f"Could not clear Neo4j graph data during {label}: {exc}") from exc
            if deleted <= 0:
                if progress_cb is not None:
                    progress_cb(label, deleted_total, 0, current_batch_size, f"{label} complete.")
                return
            deleted_total += deleted
            if progress_cb is not None:
                progress_cb(
                    label,
                    deleted_total,
                    deleted,
                    current_batch_size,
                    f"{label}: deleted {deleted_total:,} item(s) so far.",
                )

    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as session:
            _run_batched_delete(session, _delete_relationship_batch, "relationship delete")
            _run_batched_delete(session, _delete_node_batch, "node delete")
    except Exception as exc:
        raise ValueError("Could not clear Neo4j graph data: %s" % exc)
    finally:
        if driver is not None:
            with suppress(Exception):
                driver.close()


def action_nexus_cache_entries():
    """List top-level Nexus cache entries from a directly mounted cache root or the running Nexus container."""
    result = {
        "available": False,
        "container_running": False,
        "cache_root": NEXUS_CONTAINER_CACHE_ROOT,
        "entries": [],
        "error": None,
    }
    try:
        direct_root = _get_direct_nexus_cache_root()
        if direct_root:
            result["cache_root"] = direct_root
            result["entries"] = _list_nexus_cache_entries_on_filesystem(direct_root)
            result["available"] = True
            result["container_running"] = _get_running_nexus_container() is not None
            return result

        client = _get_docker_client_or_raise()
        container = _get_running_nexus_container(client)
        if container is None:
            result["error"] = "Cache selection is available when the Nexus container is running or the Nexus cache root is mounted into the API environment."
            return result
        result["container_running"] = True
        result["entries"] = _list_nexus_cache_entries_in_container(container)
        result["available"] = True
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _queue_nexus_rebuild(conn, message):
    import datetime as _dt

    update_engine_doc(
        conn,
        NEXUS_ENGINE_ID,
        {
            "running": True,
            "start_phase": 1,
            "end_phase": 14,
            "selected_phases": None,
            "rebuild_requested_at": _dt.datetime.utcnow().isoformat() + "Z",
        },
    )
    _upsert_nexus_progress_doc(
        conn,
        "graph_nexus",
        {
            "status": "queued",
            "message": message,
            "last_updated": r.now(),
        },
    )


def _set_nexus_rebuild_operation(
    conn,
    *,
    active: bool,
    destructive: bool | None = None,
    step: str | None = None,
    message: str | None = None,
    current: int | None = None,
    total: int | None = None,
    unit: str | None = None,
    started_at: str | None = None,
):
    import datetime as _dt

    update = {
        "rebuild_operation_active": bool(active),
        "rebuild_operation_step": step,
        "rebuild_operation_message": message,
        "rebuild_operation_current": current,
        "rebuild_operation_total": total,
        "rebuild_operation_unit": unit,
        "rebuild_operation_updated_at": _dt.datetime.utcnow().isoformat() + "Z",
    }
    if destructive is not None:
        update["rebuild_operation_destructive"] = bool(destructive)
    if active:
        update["rebuild_operation_started_at"] = started_at or _dt.datetime.utcnow().isoformat() + "Z"
    else:
        update["rebuild_operation_started_at"] = None
        update["rebuild_operation_destructive"] = False
    update_engine_doc(conn, NEXUS_ENGINE_ID, update)


def ensure_nexus_control_table(conn):
    ensure_engine_control_table(conn)
    doc = get_engine_doc(conn, NEXUS_ENGINE_ID) or {}
    schema_version = int(doc.get("phase_selector_schema_version") or 1)
    if schema_version >= 3:
        return

    def _migrate_legacy_phase_value(value, *, is_end_field: bool):
        if value in (None, ""):
            return None
        try:
            phase = int(value)
        except Exception:
            return value
        if phase < 1:
            return value
        if phase == 7 and is_end_field:
            return 8
        if phase >= 8:
            return min(14, phase + 1)
        return phase

    update_engine_doc(conn, NEXUS_ENGINE_ID, {
        "phase_selector_schema_version": 3,
        "start_phase": _migrate_legacy_phase_value(doc.get("start_phase"), is_end_field=False),
        "end_phase": _migrate_legacy_phase_value(doc.get("end_phase"), is_end_field=True),
        "selected_phases": None,
        "auto_update_start_phase": _migrate_legacy_phase_value(doc.get("auto_update_start_phase"), is_end_field=False),
        "auto_update_end_phase": _migrate_legacy_phase_value(doc.get("auto_update_end_phase"), is_end_field=True),
    })


def action_nexus_control_get(conn):
    """Get Graph Nexus engine control state and auto-update configuration."""
    ensure_nexus_control_table(conn)
    doc = get_engine_doc(conn, NEXUS_ENGINE_ID)
    if doc is None:
        return {
            "running": False,
            "start_phase": None,
            "end_phase": None,
            "selected_phases": None,
            "selected_phase_labels": [],
            "phase_selector_schema_version": 3,
            "force_bootstrap_rebuild": False,
            "auto_update_enabled": False,
            "auto_update_interval_hours": 168,
            "auto_update_start_phase": 3,
            "auto_update_end_phase": 14,
            "start_phase_label": None,
            "end_phase_label": None,
            "auto_update_start_phase_label": _nexus_phase_label_from_value(3),
            "auto_update_end_phase_label": _nexus_phase_label_from_value(14),
            "phase_options": list(NEXUS_PHASE_OPTIONS),
            "delete_phase_options": list(NEXUS_DELETE_PHASE_OPTIONS),
            "phase7_history_quarters": 1,
            "historical_mode_enabled": False,
            "historical_start_date": None,
            "historical_bootstrap_complete": False,
            "historical_coverage_end": None,
            "historical_phase_manifests": {},
            "last_historical_bootstrap_started_at": None,
            "last_historical_bootstrap_completed_at": None,
            "last_historical_bootstrap_duration_sec": None,
            "last_historical_bootstrap_status": None,
            "next_auto_update_at": None,
            "last_auto_update_at": None,
            "last_run_started_at": None,
            "last_run_completed_at": None,
            "last_run_status": None,
            "rebuild_requested_at": None,
            "rebuild_operation_active": False,
            "rebuild_operation_destructive": False,
            "rebuild_operation_step": None,
            "rebuild_operation_message": None,
            "rebuild_operation_current": None,
            "rebuild_operation_total": None,
            "rebuild_operation_unit": None,
            "rebuild_operation_started_at": None,
            "rebuild_operation_updated_at": None,
            "delete_operation_active": False,
            "delete_operation_step": None,
            "delete_operation_message": None,
            "delete_operation_current": None,
            "delete_operation_total": None,
            "delete_operation_unit": None,
            "delete_operation_started_at": None,
            "delete_operation_updated_at": None,
            "delete_operation_error": None,
            "delete_operation_selected_phases": None,
            "delete_operation_phase_rows": [],
        }
    return {
        "running": bool(doc.get("running", False)),
        "start_phase": doc.get("start_phase"),
        "end_phase": doc.get("end_phase"),
        "selected_phases": list(doc.get("selected_phases") or []) or None,
        "selected_phase_labels": [
            _nexus_phase_label_from_value(value)
            for value in (doc.get("selected_phases") or [])
            if _nexus_phase_label_from_value(value)
        ],
        "phase_selector_schema_version": int(doc.get("phase_selector_schema_version") or 3),
        "start_phase_label": _nexus_phase_label_from_value(doc.get("start_phase")),
        "end_phase_label": _nexus_phase_label_from_value(doc.get("end_phase")),
        "force_bootstrap_rebuild": bool(doc.get("force_bootstrap_rebuild", False)),
        "auto_update_enabled": bool(doc.get("auto_update_enabled", False)),
        "auto_update_interval_hours": int(doc.get("auto_update_interval_hours") or 168),
        "auto_update_start_phase": doc.get("auto_update_start_phase"),
        "auto_update_end_phase": doc.get("auto_update_end_phase"),
        "auto_update_start_phase_label": _nexus_phase_label_from_value(doc.get("auto_update_start_phase")),
        "auto_update_end_phase_label": _nexus_phase_label_from_value(doc.get("auto_update_end_phase")),
        "phase_options": list(NEXUS_PHASE_OPTIONS),
        "delete_phase_options": list(NEXUS_DELETE_PHASE_OPTIONS),
        "phase7_history_quarters": int(doc.get("phase7_history_quarters") or 1),
        "historical_mode_enabled": bool(doc.get("historical_mode_enabled", False)),
        "historical_start_date": doc.get("historical_start_date"),
        "historical_bootstrap_complete": bool(doc.get("historical_bootstrap_complete", False)),
        "historical_coverage_end": doc.get("historical_coverage_end"),
        "historical_phase_manifests": dict(doc.get("historical_phase_manifests") or {}),
        "last_historical_bootstrap_started_at": doc.get("last_historical_bootstrap_started_at"),
        "last_historical_bootstrap_completed_at": doc.get("last_historical_bootstrap_completed_at"),
        "last_historical_bootstrap_duration_sec": doc.get("last_historical_bootstrap_duration_sec"),
        "last_historical_bootstrap_status": doc.get("last_historical_bootstrap_status"),
        "next_auto_update_at": doc.get("next_auto_update_at"),
        "last_auto_update_at": doc.get("last_auto_update_at"),
        "last_run_started_at": doc.get("last_run_started_at"),
        "last_run_completed_at": doc.get("last_run_completed_at"),
        "last_run_status": doc.get("last_run_status"),
        "rebuild_requested_at": doc.get("rebuild_requested_at"),
        "rebuild_operation_active": bool(doc.get("rebuild_operation_active", False)),
        "rebuild_operation_destructive": bool(doc.get("rebuild_operation_destructive", False)),
        "rebuild_operation_step": doc.get("rebuild_operation_step"),
        "rebuild_operation_message": doc.get("rebuild_operation_message"),
        "rebuild_operation_current": doc.get("rebuild_operation_current"),
        "rebuild_operation_total": doc.get("rebuild_operation_total"),
        "rebuild_operation_unit": doc.get("rebuild_operation_unit"),
        "rebuild_operation_started_at": doc.get("rebuild_operation_started_at"),
        "rebuild_operation_updated_at": doc.get("rebuild_operation_updated_at"),
        "delete_operation_active": bool(doc.get("delete_operation_active", False)),
        "delete_operation_step": doc.get("delete_operation_step"),
        "delete_operation_message": doc.get("delete_operation_message"),
        "delete_operation_current": doc.get("delete_operation_current"),
        "delete_operation_total": doc.get("delete_operation_total"),
        "delete_operation_unit": doc.get("delete_operation_unit"),
        "delete_operation_started_at": doc.get("delete_operation_started_at"),
        "delete_operation_updated_at": doc.get("delete_operation_updated_at"),
        "delete_operation_error": doc.get("delete_operation_error"),
        "delete_operation_selected_phases": list(doc.get("delete_operation_selected_phases") or []) or None,
        "delete_operation_phase_rows": list(doc.get("delete_operation_phase_rows") or []),
    }


def _normalize_nexus_historical_start_date(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except Exception:
        raise ValueError("historical_start_date must be in YYYY-MM-DD format")
    if parsed > datetime.datetime.now(datetime.timezone.utc).date():
        raise ValueError("historical_start_date cannot be in the future")
    return parsed.isoformat()


def _nexus_bootstrap_reset_update(*, clear_start_date: bool = False):
    update = {
        "force_bootstrap_rebuild": False,
        "historical_bootstrap_complete": False,
        "historical_coverage_end": None,
        "historical_phase_manifests": {},
        "last_historical_bootstrap_started_at": None,
        "last_historical_bootstrap_completed_at": None,
        "last_historical_bootstrap_duration_sec": None,
        "last_historical_bootstrap_status": None,
    }
    if clear_start_date:
        update["historical_start_date"] = None
    return update


def _nexus_force_bootstrap_start_date(existing_doc: dict | None, update: dict | None = None) -> str | None:
    """Resolve the start date that a force-bootstrap rebuild should use."""
    existing_doc = existing_doc or {}
    update = update or {}
    start_date = update.get("historical_start_date", existing_doc.get("historical_start_date"))
    if start_date:
        return start_date
    manifests = update.get("historical_phase_manifests") or existing_doc.get("historical_phase_manifests") or {}
    coverage_starts = []
    for manifest in manifests.values():
        if not isinstance(manifest, dict):
            continue
        normalized = _normalize_nexus_iso_date(manifest.get("coverage_start"))
        if normalized:
            coverage_starts.append(normalized)
    return min(coverage_starts) if coverage_starts else None


def action_nexus_control_set(
    conn,
    running=None,
    start_phase=None,
    end_phase=None,
    force_bootstrap_rebuild=None,
    auto_update_enabled=None,
    auto_update_interval_hours=None,
    auto_update_start_phase=None,
    auto_update_end_phase=None,
    phase7_history_quarters=None,
    historical_mode_enabled=None,
    historical_start_date=None,
    selected_phases=None,
):
    """Set Graph Nexus engine control.
    running=True to start, False to stop.
    start_phase, end_phase: optional phase selectors. Accepts execution-order ints 1–14 or display selectors like 2B / 6B.
    selected_phases: optional explicit phase selectors for this manual run.
    Pass start_phase=None, end_phase=None, selected_phases=None when starting to run all phases.
    Returns current state."""
    ensure_nexus_control_table(conn)
    existing_doc = get_engine_doc(conn, NEXUS_ENGINE_ID) or {}
    update = {"phase_selector_schema_version": 3}
    queue_message = None
    if running is not None:
        update["running"] = bool(running)
        update["phase_selector_schema_version"] = 3
    if running is True or selected_phases is not None:
        update["selected_phases"] = _normalize_nexus_phase_values(selected_phases)
    # When starting (or when explicitly passing range), set phase range; None = run all
    if running is True or start_phase is not None or end_phase is not None:
        update["start_phase"] = _normalize_nexus_phase_value(start_phase)
        update["end_phase"] = _normalize_nexus_phase_value(end_phase)
    if update.get("selected_phases"):
        update["start_phase"] = None
        update["end_phase"] = None
    if auto_update_enabled is not None:
        update["auto_update_enabled"] = bool(auto_update_enabled)
        if bool(auto_update_enabled) and running is None:
            update["running"] = True
        update["next_auto_update_at"] = None
    if auto_update_interval_hours is not None:
        hours = int(auto_update_interval_hours)
        if hours < 1:
            raise ValueError("auto_update_interval_hours must be >= 1")
        update["auto_update_interval_hours"] = hours
        update["next_auto_update_at"] = None
    if auto_update_start_phase is not None or auto_update_end_phase is not None:
        auto_start = _normalize_nexus_phase_value(auto_update_start_phase)
        auto_end = _normalize_nexus_phase_value(auto_update_end_phase)
        if auto_start is not None:
            auto_start = max(1, min(14, auto_start))
        if auto_end is not None:
            auto_end = max(1, min(14, auto_end))
        if auto_start is not None and auto_end is None:
            auto_end = 14
        if auto_end is not None and auto_start is None:
            auto_start = 1
        if auto_start is not None and auto_end is not None and auto_start > auto_end:
            auto_start, auto_end = auto_end, auto_start
        update["auto_update_start_phase"] = auto_start
        update["auto_update_end_phase"] = auto_end
        update["next_auto_update_at"] = None
    if phase7_history_quarters is not None:
        quarters = int(phase7_history_quarters)
        if quarters < 1:
            raise ValueError("phase7_history_quarters must be >= 1")
        update["phase7_history_quarters"] = quarters
    if historical_mode_enabled is not None:
        enabled = bool(historical_mode_enabled)
        if enabled and not (historical_start_date or existing_doc.get("historical_start_date")):
            raise ValueError("historical_start_date is required when enabling historical_mode_enabled")
        update["historical_mode_enabled"] = enabled
        if not enabled:
            update.update(_nexus_bootstrap_reset_update(clear_start_date=False))
    if historical_start_date is not None:
        normalized_start = _normalize_nexus_historical_start_date(historical_start_date)
        update["historical_mode_enabled"] = True
        update["historical_start_date"] = normalized_start
        update.update(_nexus_bootstrap_reset_update(clear_start_date=False))
    if force_bootstrap_rebuild is not None:
        requested = bool(force_bootstrap_rebuild)
        effective_start_date = _nexus_force_bootstrap_start_date(existing_doc, update)
        effective_historical_enabled = bool(
            update.get("historical_mode_enabled", existing_doc.get("historical_mode_enabled", False))
        )
        if requested and not effective_start_date:
            raise ValueError("force_bootstrap_rebuild requires a configured historical bootstrap start date")
        update["force_bootstrap_rebuild"] = requested
        if requested:
            if not effective_historical_enabled:
                update["historical_mode_enabled"] = True
            if effective_start_date and "historical_start_date" not in update:
                update["historical_start_date"] = effective_start_date
            update.update(_nexus_bootstrap_reset_update(clear_start_date=False))
            update["force_bootstrap_rebuild"] = True
            if running is True or start_phase is not None or end_phase is not None:
                queue_message = "Queued bootstrap rebuild from configured historical start date"
                _reset_nexus_bootstrap_progress_state(
                    conn,
                    queue_message,
                    reset_progress_docs=True,
                )
    if update:
        update_engine_doc(conn, NEXUS_ENGINE_ID, update)
    return action_nexus_control_get(conn)


# Graph Nexus execution-order phase options. The EX-21 split is exposed as "Phase 6B"
# without renaming later cache folders or phase keys.
NEXUS_PHASE_OPTIONS = [
    {"value": 1, "selector": "1", "label": "Phase 1: Company universe"},
    {"value": 2, "selector": "2", "label": "Phase 2: Easy relationships"},
    {"value": 3, "selector": "2b", "label": "Phase 2B: SEC sector/industry"},
    {"value": 4, "selector": "3", "label": "Phase 3: Supply chain"},
    {"value": 5, "selector": "4", "label": "Phase 4: Competitive"},
    {"value": 6, "selector": "5", "label": "Phase 5: Macro/BEA"},
    {"value": 7, "selector": "6", "label": "Phase 6: GLEIF hierarchy"},
    {"value": 8, "selector": "6b", "label": "Phase 6B: SEC EX-21 hierarchy"},
    {"value": 9, "selector": "7", "label": "Phase 7: 13F ownership"},
    {"value": 10, "selector": "8", "label": "Phase 8: USASpending"},
    {"value": 11, "selector": "9", "label": "Phase 9: Wikidata"},
    {"value": 12, "selector": "10", "label": "Phase 10: PatentsView"},
    {"value": 13, "selector": "11", "label": "Phase 11: 8-K agreements"},
    {"value": 14, "selector": "12", "label": "Phase 12: ETF universe"},
]
_NEXUS_PHASE_OPTIONS_BY_VALUE = {int(row["value"]): dict(row) for row in NEXUS_PHASE_OPTIONS}
_NEXUS_PHASE_SELECTOR_TO_VALUE = {}
for _row in NEXUS_PHASE_OPTIONS:
    _selector = str(_row["selector"]).strip().lower()
    _value = int(_row["value"])
    _NEXUS_PHASE_SELECTOR_TO_VALUE[_selector] = _value
    _NEXUS_PHASE_SELECTOR_TO_VALUE[str(_value)] = _value
    _NEXUS_PHASE_SELECTOR_TO_VALUE[f"phase{_selector}"] = _value

NEXUS_DELETE_PHASE_VALUES = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
NEXUS_DELETE_PHASE_OPTIONS = [
    dict(_NEXUS_PHASE_OPTIONS_BY_VALUE[value])
    for value in NEXUS_DELETE_PHASE_VALUES
    if value in _NEXUS_PHASE_OPTIONS_BY_VALUE
]
_NEXUS_DELETE_PHASE_OPTION_VALUES = {int(row["value"]) for row in NEXUS_DELETE_PHASE_OPTIONS}
_NEXUS_DELETE_LOCK = threading.Lock()
_NEXUS_DELETE_THREAD = [None]


def _normalize_nexus_phase_value(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("phase selector must not be boolean")
    if isinstance(value, (int, float)):
        phase = int(value)
        if 1 <= phase <= 14:
            return phase
        raise ValueError("phase selector must be between 1 and 14")
    raw = str(value or "").strip().lower().replace(" ", "").replace("-", "")
    if raw.startswith("phase"):
        raw = raw[5:]
    normalized = _NEXUS_PHASE_SELECTOR_TO_VALUE.get(raw)
    if normalized is None:
        raise ValueError("unknown phase selector: %s" % value)
    return normalized


def _normalize_nexus_phase_values(values):
    if values in (None, ""):
        return None
    if not isinstance(values, (list, tuple, set)):
        raise ValueError("selected_phases must be a list of phase selectors")
    normalized = []
    for value in values:
        phase = _normalize_nexus_phase_value(value)
        if phase is None:
            continue
        normalized.append(int(phase))
    deduped = sorted(set(normalized))
    return deduped or None


def _nexus_phase_label_from_value(value):
    try:
        phase = int(value)
    except Exception:
        return None
    row = _NEXUS_PHASE_OPTIONS_BY_VALUE.get(phase)
    return row.get("label") if row else None


def _nexus_phase_selector_from_value(value):
    try:
        phase = int(value)
    except Exception:
        return None
    row = _NEXUS_PHASE_OPTIONS_BY_VALUE.get(phase)
    return row.get("selector") if row else None


def _default_nexus_delete_phase_rows(selected_phases):
    rows = []
    for value in list(selected_phases or []):
        phase = int(value)
        rows.append({
            "phase_value": phase,
            "phase_selector": _nexus_phase_selector_from_value(phase),
            "label": _nexus_phase_label_from_value(phase) or f"Phase {phase}",
            "status": "pending",
            "progress_pct": 0.0,
            "current": 0,
            "total": 0,
            "unit": "records",
            "deleted_count": 0,
            "message": "Queued",
        })
    return rows


def _set_nexus_delete_operation(
    conn,
    *,
    active,
    step=None,
    message=None,
    current=None,
    total=None,
    unit=None,
    started_at=None,
    error=None,
    selected_phases=None,
    phase_rows=None,
):
    import datetime as _dt

    update = {
        "delete_operation_active": bool(active),
        "delete_operation_step": step,
        "delete_operation_message": message,
        "delete_operation_current": current,
        "delete_operation_total": total,
        "delete_operation_unit": unit,
        "delete_operation_updated_at": _dt.datetime.utcnow().isoformat() + "Z",
        "delete_operation_error": error,
    }
    if selected_phases is not None:
        update["delete_operation_selected_phases"] = list(selected_phases or []) or None
    if phase_rows is not None:
        update["delete_operation_phase_rows"] = list(phase_rows or [])
    if active:
        update["delete_operation_started_at"] = started_at or _dt.datetime.utcnow().isoformat() + "Z"
    elif started_at is not None:
        update["delete_operation_started_at"] = started_at
    update_engine_doc(conn, NEXUS_ENGINE_ID, update)


def _nexus_delete_query_count(session, query, params=None):
    record = session.run(query, **dict(params or {})).single()
    if not record:
        return 0
    try:
        return int(record.get("count") or 0)
    except Exception:
        return 0


def _nexus_delete_query_batch(session, query, *, limit, params=None):
    query_params = dict(params or {})
    query_params["limit"] = int(limit)
    record = session.run(query, **query_params).single()
    if not record:
        return 0
    try:
        return int(record.get("deleted") or 0)
    except Exception:
        return 0


def _nexus_delete_phase_specs():
    return {
        3: {
            "label": _nexus_phase_label_from_value(3) or "Phase 2B",
            "operations": [
                {
                    "label": "SEC sector edges",
                    "batch_size": 10000,
                    "count_query": "MATCH ()-[r:IN_SECTOR {source_scope: 'SEC_SECTOR'}]->() RETURN count(r) AS count",
                    "delete_query": """
                        MATCH ()-[r:IN_SECTOR {source_scope: 'SEC_SECTOR'}]->()
                        WITH r LIMIT $limit
                        WITH collect(r) AS batch
                        FOREACH (rel IN batch | DELETE rel)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "SEC sector intervals",
                    "batch_size": 5000,
                    "count_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type = 'IN_SECTOR' AND h.source_scope = 'SEC_SECTOR'
                        RETURN count(h) AS count
                    """,
                    "delete_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type = 'IN_SECTOR' AND h.source_scope = 'SEC_SECTOR'
                        WITH h LIMIT $limit
                        WITH collect(h) AS batch
                        FOREACH (node IN batch | DETACH DELETE node)
                        RETURN size(batch) AS deleted
                    """,
                },
            ],
        },
        4: {
            "label": _nexus_phase_label_from_value(4) or "Phase 3",
            "operations": [
                {
                    "label": "supplier edges",
                    "batch_size": 10000,
                    "count_query": "MATCH ()-[r:SUPPLIER_OF {source_scope: 'SEC_10K_SUPPLIER'}]->() RETURN count(r) AS count",
                    "delete_query": """
                        MATCH ()-[r:SUPPLIER_OF {source_scope: 'SEC_10K_SUPPLIER'}]->()
                        WITH r LIMIT $limit
                        WITH collect(r) AS batch
                        FOREACH (rel IN batch | DELETE rel)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "10-K partner edges",
                    "batch_size": 5000,
                    "count_query": "MATCH ()-[r:STRATEGIC_PARTNER {source_scope: 'SEC_10K_STRATEGIC_PARTNER'}]->() RETURN count(r) AS count",
                    "delete_query": """
                        MATCH ()-[r:STRATEGIC_PARTNER {source_scope: 'SEC_10K_STRATEGIC_PARTNER'}]->()
                        WITH r LIMIT $limit
                        WITH collect(r) AS batch
                        FOREACH (rel IN batch | DELETE rel)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "supplier intervals",
                    "batch_size": 5000,
                    "count_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type = 'SUPPLIER_OF' AND h.source_scope = 'SEC_10K_SUPPLIER'
                        RETURN count(h) AS count
                    """,
                    "delete_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type = 'SUPPLIER_OF' AND h.source_scope = 'SEC_10K_SUPPLIER'
                        WITH h LIMIT $limit
                        WITH collect(h) AS batch
                        FOREACH (node IN batch | DETACH DELETE node)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "10-K partner intervals",
                    "batch_size": 5000,
                    "count_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type = 'STRATEGIC_PARTNER' AND h.source_scope = 'SEC_10K_STRATEGIC_PARTNER'
                        RETURN count(h) AS count
                    """,
                    "delete_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type = 'STRATEGIC_PARTNER' AND h.source_scope = 'SEC_10K_STRATEGIC_PARTNER'
                        WITH h LIMIT $limit
                        WITH collect(h) AS batch
                        FOREACH (node IN batch | DETACH DELETE node)
                        RETURN size(batch) AS deleted
                    """,
                },
            ],
        },
        5: {
            "label": _nexus_phase_label_from_value(5) or "Phase 4",
            "operations": [
                {
                    "label": "competitive edges",
                    "batch_size": 10000,
                    "count_query": "MATCH ()-[r:COMPETES_WITH {source_scope: 'SIC_COMPETITIVE'}]-() RETURN count(r) AS count",
                    "delete_query": """
                        MATCH ()-[r:COMPETES_WITH {source_scope: 'SIC_COMPETITIVE'}]-()
                        WITH DISTINCT r LIMIT $limit
                        WITH collect(r) AS batch
                        FOREACH (rel IN batch | DELETE rel)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "competitive intervals",
                    "batch_size": 5000,
                    "count_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type = 'COMPETES_WITH' AND h.source_scope = 'SIC_COMPETITIVE'
                        RETURN count(h) AS count
                    """,
                    "delete_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type = 'COMPETES_WITH' AND h.source_scope = 'SIC_COMPETITIVE'
                        WITH h LIMIT $limit
                        WITH collect(h) AS batch
                        FOREACH (node IN batch | DETACH DELETE node)
                        RETURN size(batch) AS deleted
                    """,
                },
            ],
        },
        6: {
            "label": _nexus_phase_label_from_value(6) or "Phase 5",
            "operations": [
                {
                    "label": "BEA sector edges",
                    "batch_size": 5000,
                    "count_query": "MATCH ()-[r:SUPPLIES_TO_SECTOR {source_scope: 'BEA_IO'}]->() RETURN count(r) AS count",
                    "delete_query": """
                        MATCH ()-[r:SUPPLIES_TO_SECTOR {source_scope: 'BEA_IO'}]->()
                        WITH r LIMIT $limit
                        WITH collect(r) AS batch
                        FOREACH (rel IN batch | DELETE rel)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "commodity exposure edges",
                    "batch_size": 5000,
                    "count_query": "MATCH ()-[r:EXPOSED_TO {source_scope: 'BEA_IO_COMMODITY'}]->() RETURN count(r) AS count",
                    "delete_query": """
                        MATCH ()-[r:EXPOSED_TO {source_scope: 'BEA_IO_COMMODITY'}]->()
                        WITH r LIMIT $limit
                        WITH collect(r) AS batch
                        FOREACH (rel IN batch | DELETE rel)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "BEA intervals",
                    "batch_size": 5000,
                    "count_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE (h.rel_type = 'SUPPLIES_TO_SECTOR' AND h.source_scope = 'BEA_IO')
                           OR (h.rel_type = 'EXPOSED_TO' AND h.source_scope = 'BEA_IO_COMMODITY')
                        RETURN count(h) AS count
                    """,
                    "delete_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE (h.rel_type = 'SUPPLIES_TO_SECTOR' AND h.source_scope = 'BEA_IO')
                           OR (h.rel_type = 'EXPOSED_TO' AND h.source_scope = 'BEA_IO_COMMODITY')
                        WITH h LIMIT $limit
                        WITH collect(h) AS batch
                        FOREACH (node IN batch | DETACH DELETE node)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "commodity nodes",
                    "batch_size": 2000,
                    "count_query": "MATCH (c:Commodity) RETURN count(c) AS count",
                    "delete_query": """
                        MATCH (c:Commodity)
                        WITH c LIMIT $limit
                        WITH collect(c) AS batch
                        FOREACH (node IN batch | DETACH DELETE node)
                        RETURN size(batch) AS deleted
                    """,
                },
            ],
        },
        7: {
            "label": _nexus_phase_label_from_value(7) or "Phase 6",
            "operations": [
                {
                    "label": "GLEIF hierarchy edges",
                    "batch_size": 5000,
                    "count_query": """
                        MATCH ()-[r:PARENT_OF_ENTITY {source_scope: 'CORPORATE_HIERARCHY'}]->()
                        WHERE coalesce(r.gleif_supported, false)
                           OR any(src IN coalesce(r.evidence_sources, []) WHERE src STARTS WITH 'GLEIF')
                        RETURN count(r) AS count
                    """,
                    "delete_query": """
                        MATCH ()-[r:PARENT_OF_ENTITY {source_scope: 'CORPORATE_HIERARCHY'}]->()
                        WHERE coalesce(r.gleif_supported, false)
                           OR any(src IN coalesce(r.evidence_sources, []) WHERE src STARTS WITH 'GLEIF')
                        WITH r LIMIT $limit
                        WITH collect(r) AS batch
                        FOREACH (rel IN batch | DELETE rel)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "GLEIF projection edges",
                    "batch_size": 5000,
                    "count_query": """
                        MATCH ()-[r:PARENT_OF {source_scope: 'CORPORATE_HIERARCHY_PROJECTION'}]->()
                        WHERE any(src IN coalesce(r.evidence_sources, []) WHERE src STARTS WITH 'GLEIF')
                        RETURN count(r) AS count
                    """,
                    "delete_query": """
                        MATCH ()-[r:PARENT_OF {source_scope: 'CORPORATE_HIERARCHY_PROJECTION'}]->()
                        WHERE any(src IN coalesce(r.evidence_sources, []) WHERE src STARTS WITH 'GLEIF')
                        WITH r LIMIT $limit
                        WITH collect(r) AS batch
                        FOREACH (rel IN batch | DELETE rel)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "legacy GLEIF parent edges",
                    "batch_size": 5000,
                    "count_query": "MATCH ()-[r:PARENT_OF {source_scope: 'GLEIF_PARENT'}]->() RETURN count(r) AS count",
                    "delete_query": """
                        MATCH ()-[r:PARENT_OF {source_scope: 'GLEIF_PARENT'}]->()
                        WITH r LIMIT $limit
                        WITH collect(r) AS batch
                        FOREACH (rel IN batch | DELETE rel)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "legacy GLEIF parent LEI edges",
                    "batch_size": 5000,
                    "count_query": "MATCH ()-[r:PARENT_OF_LEI {source_scope: 'GLEIF_PARENT_LEI'}]->() RETURN count(r) AS count",
                    "delete_query": """
                        MATCH ()-[r:PARENT_OF_LEI {source_scope: 'GLEIF_PARENT_LEI'}]->()
                        WITH r LIMIT $limit
                        WITH collect(r) AS batch
                        FOREACH (rel IN batch | DELETE rel)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "GLEIF hierarchy intervals",
                    "batch_size": 5000,
                    "count_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE (h.rel_type = 'PARENT_OF_ENTITY' AND h.source_scope = 'CORPORATE_HIERARCHY')
                           OR (h.rel_type = 'PARENT_OF' AND h.source_scope = 'CORPORATE_HIERARCHY_PROJECTION')
                           OR (h.rel_type = 'PARENT_OF' AND h.source_scope = 'GLEIF_PARENT')
                           OR (h.rel_type = 'PARENT_OF_LEI' AND h.source_scope = 'GLEIF_PARENT_LEI')
                        RETURN count(h) AS count
                    """,
                    "delete_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE (h.rel_type = 'PARENT_OF_ENTITY' AND h.source_scope = 'CORPORATE_HIERARCHY')
                           OR (h.rel_type = 'PARENT_OF' AND h.source_scope = 'CORPORATE_HIERARCHY_PROJECTION')
                           OR (h.rel_type = 'PARENT_OF' AND h.source_scope = 'GLEIF_PARENT')
                           OR (h.rel_type = 'PARENT_OF_LEI' AND h.source_scope = 'GLEIF_PARENT_LEI')
                        WITH h LIMIT $limit
                        WITH collect(h) AS batch
                        FOREACH (node IN batch | DETACH DELETE node)
                        RETURN size(batch) AS deleted
                    """,
                },
            ],
        },
        8: {
            "label": _nexus_phase_label_from_value(8) or "Phase 6B",
            "operations": [
                {
                    "label": "EX-21 hierarchy edges",
                    "batch_size": 5000,
                    "count_query": """
                        MATCH ()-[r:PARENT_OF_ENTITY {source_scope: 'CORPORATE_HIERARCHY'}]->()
                        WHERE coalesce(r.sec_ex21_supported, false)
                           OR 'SEC_EX21' IN coalesce(r.evidence_sources, [])
                        RETURN count(r) AS count
                    """,
                    "delete_query": """
                        MATCH ()-[r:PARENT_OF_ENTITY {source_scope: 'CORPORATE_HIERARCHY'}]->()
                        WHERE coalesce(r.sec_ex21_supported, false)
                           OR 'SEC_EX21' IN coalesce(r.evidence_sources, [])
                        WITH r LIMIT $limit
                        WITH collect(r) AS batch
                        FOREACH (rel IN batch | DELETE rel)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "EX-21 projection edges",
                    "batch_size": 5000,
                    "count_query": """
                        MATCH ()-[r:PARENT_OF {source_scope: 'CORPORATE_HIERARCHY_PROJECTION'}]->()
                        WHERE 'SEC_EX21' IN coalesce(r.evidence_sources, [])
                        RETURN count(r) AS count
                    """,
                    "delete_query": """
                        MATCH ()-[r:PARENT_OF {source_scope: 'CORPORATE_HIERARCHY_PROJECTION'}]->()
                        WHERE 'SEC_EX21' IN coalesce(r.evidence_sources, [])
                        WITH r LIMIT $limit
                        WITH collect(r) AS batch
                        FOREACH (rel IN batch | DELETE rel)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "EX-21 intervals",
                    "batch_size": 3000,
                    "count_query": """
                        MATCH (n:LegalEntity)<-[:EDGE_INTERVAL_SOURCE|EDGE_INTERVAL_TARGET]-(h:GraphEdgeInterval)
                        WHERE NOT n:Company
                          AND ('SEC_EX21' IN coalesce(n.source_systems, [])
                               OR coalesce(n.entity_key, '') STARTS WITH 'ex21:')
                          AND h.rel_type = 'PARENT_OF_ENTITY'
                          AND h.source_scope = 'CORPORATE_HIERARCHY'
                        RETURN count(DISTINCT h) AS count
                    """,
                    "delete_query": """
                        MATCH (n:LegalEntity)<-[:EDGE_INTERVAL_SOURCE|EDGE_INTERVAL_TARGET]-(h:GraphEdgeInterval)
                        WHERE NOT n:Company
                          AND ('SEC_EX21' IN coalesce(n.source_systems, [])
                               OR coalesce(n.entity_key, '') STARTS WITH 'ex21:')
                          AND h.rel_type = 'PARENT_OF_ENTITY'
                          AND h.source_scope = 'CORPORATE_HIERARCHY'
                        WITH DISTINCT h LIMIT $limit
                        WITH collect(h) AS batch
                        FOREACH (node IN batch | DETACH DELETE node)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "EX-21 legal entities",
                    "batch_size": 3000,
                    "count_query": """
                        MATCH (n:LegalEntity)
                        WHERE NOT n:Company
                          AND ('SEC_EX21' IN coalesce(n.source_systems, [])
                               OR coalesce(n.entity_key, '') STARTS WITH 'ex21:')
                        RETURN count(n) AS count
                    """,
                    "delete_query": """
                        MATCH (n:LegalEntity)
                        WHERE NOT n:Company
                          AND ('SEC_EX21' IN coalesce(n.source_systems, [])
                               OR coalesce(n.entity_key, '') STARTS WITH 'ex21:')
                        WITH n LIMIT $limit
                        WITH collect(n) AS batch
                        FOREACH (node IN batch | DETACH DELETE node)
                        RETURN size(batch) AS deleted
                    """,
                },
            ],
        },
        9: {
            "label": _nexus_phase_label_from_value(9) or "Phase 7",
            "operations": [
                {
                    "label": "13F holdings edges",
                    "batch_size": 15000,
                    "count_query": "MATCH ()-[r:HOLDS {source_scope: '13F_HR'}]->() RETURN count(r) AS count",
                    "delete_query": """
                        MATCH ()-[r:HOLDS {source_scope: '13F_HR'}]->()
                        WITH r LIMIT $limit
                        WITH collect(r) AS batch
                        FOREACH (rel IN batch | DELETE rel)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "13F intervals",
                    "batch_size": 8000,
                    "count_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type = 'HOLDS' AND h.source_scope = '13F_HR'
                        RETURN count(h) AS count
                    """,
                    "delete_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type = 'HOLDS' AND h.source_scope = '13F_HR'
                        WITH h LIMIT $limit
                        WITH collect(h) AS batch
                        FOREACH (node IN batch | DETACH DELETE node)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "orphan institutions",
                    "batch_size": 3000,
                    "count_query": """
                        MATCH (i:Institution)
                        WHERE NOT EXISTS { MATCH (i)-[:HOLDS]-() }
                        RETURN count(i) AS count
                    """,
                    "delete_query": """
                        MATCH (i:Institution)
                        WHERE NOT EXISTS { MATCH (i)-[:HOLDS]-() }
                        WITH i LIMIT $limit
                        WITH collect(i) AS batch
                        FOREACH (node IN batch | DETACH DELETE node)
                        RETURN size(batch) AS deleted
                    """,
                },
            ],
        },
        10: {
            "label": _nexus_phase_label_from_value(10) or "Phase 8",
            "operations": [
                {
                    "label": "USASpending edges",
                    "batch_size": 5000,
                    "count_query": "MATCH ()-[r:CONTRACTS_WITH {source_scope: 'USASPENDING_AWARD'}]->() RETURN count(r) AS count",
                    "delete_query": """
                        MATCH ()-[r:CONTRACTS_WITH {source_scope: 'USASPENDING_AWARD'}]->()
                        WITH r LIMIT $limit
                        WITH collect(r) AS batch
                        FOREACH (rel IN batch | DELETE rel)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "USASpending intervals",
                    "batch_size": 5000,
                    "count_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type = 'CONTRACTS_WITH' AND h.source_scope = 'USASPENDING_AWARD'
                        RETURN count(h) AS count
                    """,
                    "delete_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type = 'CONTRACTS_WITH' AND h.source_scope = 'USASPENDING_AWARD'
                        WITH h LIMIT $limit
                        WITH collect(h) AS batch
                        FOREACH (node IN batch | DETACH DELETE node)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "orphan agencies",
                    "batch_size": 1000,
                    "count_query": """
                        MATCH (a:GovAgency)
                        WHERE NOT EXISTS { MATCH (a)-[]-() }
                        RETURN count(a) AS count
                    """,
                    "delete_query": """
                        MATCH (a:GovAgency)
                        WHERE NOT EXISTS { MATCH (a)-[]-() }
                        WITH a LIMIT $limit
                        WITH collect(a) AS batch
                        FOREACH (node IN batch | DETACH DELETE node)
                        RETURN size(batch) AS deleted
                    """,
                },
            ],
        },
        11: {
            "label": _nexus_phase_label_from_value(11) or "Phase 9",
            "operations": [
                {
                    "label": "Wikidata control edges",
                    "batch_size": 5000,
                    "count_query": "MATCH ()-[r:CONTROLS {source_scope: 'WIKIDATA_CONTROLS'}]->() RETURN count(r) AS count",
                    "delete_query": """
                        MATCH ()-[r:CONTROLS {source_scope: 'WIKIDATA_CONTROLS'}]->()
                        WITH r LIMIT $limit
                        WITH collect(r) AS batch
                        FOREACH (rel IN batch | DELETE rel)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "Wikidata intervals",
                    "batch_size": 5000,
                    "count_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type = 'CONTROLS' AND h.source_scope = 'WIKIDATA_CONTROLS'
                        RETURN count(h) AS count
                    """,
                    "delete_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type = 'CONTROLS' AND h.source_scope = 'WIKIDATA_CONTROLS'
                        WITH h LIMIT $limit
                        WITH collect(h) AS batch
                        FOREACH (node IN batch | DETACH DELETE node)
                        RETURN size(batch) AS deleted
                    """,
                },
            ],
        },
        12: {
            "label": _nexus_phase_label_from_value(12) or "Phase 10",
            "operations": [
                {
                    "label": "PatentsView edges",
                    "batch_size": 5000,
                    "count_query": "MATCH ()-[r:PATENT_PARTNER {source_scope: 'PATENTSVIEW_COASSIGNEE'}]-() RETURN count(DISTINCT r) AS count",
                    "delete_query": """
                        MATCH ()-[r:PATENT_PARTNER {source_scope: 'PATENTSVIEW_COASSIGNEE'}]-()
                        WITH DISTINCT r LIMIT $limit
                        WITH collect(r) AS batch
                        FOREACH (rel IN batch | DELETE rel)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "PatentsView intervals",
                    "batch_size": 5000,
                    "count_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type = 'PATENT_PARTNER' AND h.source_scope = 'PATENTSVIEW_COASSIGNEE'
                        RETURN count(h) AS count
                    """,
                    "delete_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type = 'PATENT_PARTNER' AND h.source_scope = 'PATENTSVIEW_COASSIGNEE'
                        WITH h LIMIT $limit
                        WITH collect(h) AS batch
                        FOREACH (node IN batch | DETACH DELETE node)
                        RETURN size(batch) AS deleted
                    """,
                },
            ],
        },
        13: {
            "label": _nexus_phase_label_from_value(13) or "Phase 11",
            "operations": [
                {
                    "label": "8-K partner edges",
                    "batch_size": 5000,
                    "count_query": "MATCH ()-[r:STRATEGIC_PARTNER {source_scope: 'SEC_8K_ITEM_1_01'}]->() RETURN count(r) AS count",
                    "delete_query": """
                        MATCH ()-[r:STRATEGIC_PARTNER {source_scope: 'SEC_8K_ITEM_1_01'}]->()
                        WITH r LIMIT $limit
                        WITH collect(r) AS batch
                        FOREACH (rel IN batch | DELETE rel)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "8-K partner intervals",
                    "batch_size": 5000,
                    "count_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type = 'STRATEGIC_PARTNER' AND h.source_scope = 'SEC_8K_ITEM_1_01'
                        RETURN count(h) AS count
                    """,
                    "delete_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type = 'STRATEGIC_PARTNER' AND h.source_scope = 'SEC_8K_ITEM_1_01'
                        WITH h LIMIT $limit
                        WITH collect(h) AS batch
                        FOREACH (node IN batch | DETACH DELETE node)
                        RETURN size(batch) AS deleted
                    """,
                },
            ],
        },
        14: {
            "label": _nexus_phase_label_from_value(14) or "Phase 12",
            "operations": [
                {
                    "label": "ETF nodes",
                    "batch_size": 2000,
                    "count_query": "MATCH (e:ETF) RETURN count(e) AS count",
                    "delete_query": """
                        MATCH (e:ETF)
                        WITH e LIMIT $limit
                        WITH collect(e) AS batch
                        FOREACH (node IN batch | DETACH DELETE node)
                        RETURN size(batch) AS deleted
                    """,
                },
                {
                    "label": "ETF intervals",
                    "batch_size": 5000,
                    "count_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type IN ['ETF_TRACKS_SECTOR', 'ETF_TRACKS_THEME', 'ETF_HOLDS']
                          AND h.source_scope IN ['ETF_UNIVERSE_CLASSIFICATION', 'ETF_UNIVERSE_THEME', 'ETF_UNIVERSE_HOLDINGS']
                        RETURN count(h) AS count
                    """,
                    "delete_query": """
                        MATCH (h:GraphEdgeInterval)
                        WHERE h.rel_type IN ['ETF_TRACKS_SECTOR', 'ETF_TRACKS_THEME', 'ETF_HOLDS']
                          AND h.source_scope IN ['ETF_UNIVERSE_CLASSIFICATION', 'ETF_UNIVERSE_THEME', 'ETF_UNIVERSE_HOLDINGS']
                        WITH h LIMIT $limit
                        WITH collect(h) AS batch
                        FOREACH (node IN batch | DETACH DELETE node)
                        RETURN size(batch) AS deleted
                    """,
                },
            ],
        },
    }


def _run_nexus_delete_operation_in_background(selected_phases, started_at):
    current_thread = threading.current_thread()
    conn = None
    try:
        conn = get_conn()
        _execute_nexus_delete_operation(conn, selected_phases, started_at=started_at)
    except Exception:
        # Operation state is updated by _execute_nexus_delete_operation.
        pass
    finally:
        if conn is not None:
            with suppress(Exception):
                conn.close()
        with _NEXUS_DELETE_LOCK:
            if _NEXUS_DELETE_THREAD[0] is current_thread:
                _NEXUS_DELETE_THREAD[0] = None


def _start_nexus_delete_operation_async(selected_phases, *, started_at):
    with _NEXUS_DELETE_LOCK:
        existing = _NEXUS_DELETE_THREAD[0]
        if existing is not None and existing.is_alive():
            raise ValueError("A Nexus delete operation is already running")
        worker = threading.Thread(
            target=_run_nexus_delete_operation_in_background,
            args=(list(selected_phases or []), started_at),
            name="nexus-delete-edges",
            daemon=True,
        )
        _NEXUS_DELETE_THREAD[0] = worker
        worker.start()


def _execute_nexus_delete_operation(conn, selected_phases, *, started_at=None):
    from neo4j import GraphDatabase

    selected = [int(value) for value in list(selected_phases or [])]
    phase_specs = _nexus_delete_phase_specs()
    phase_rows = _default_nexus_delete_phase_rows(selected)
    started = started_at or datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    total_phases = len(selected)
    completed_phases = 0

    def _row_for(phase_value):
        for row in phase_rows:
            if int(row.get("phase_value") or 0) == int(phase_value):
                return row
        raise ValueError(f"Unknown delete phase row: {phase_value}")

    def _publish(step, message, *, active=True, error=None):
        _set_nexus_delete_operation(
            conn,
            active=active,
            step=step,
            message=message,
            current=completed_phases,
            total=total_phases,
            unit="phases",
            started_at=started,
            error=error,
            selected_phases=selected,
            phase_rows=phase_rows,
        )

    _publish("Queued delete", "Preparing selected phase cleanup...")

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "password")
    driver = None
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as session:
            for phase_value in selected:
                spec = phase_specs.get(int(phase_value))
                if spec is None:
                    raise ValueError(f"Delete is not supported for phase {phase_value}")
                row = _row_for(phase_value)
                row["status"] = "running"
                row["message"] = "Counting records..."
                row["progress_pct"] = 0.0
                row["current"] = 0
                row["deleted_count"] = 0
                row["unit"] = "records"
                _publish(row["label"], f"{row['label']}: counting records...")

                operations = list(spec.get("operations") or [])
                counts = []
                total_records = 0
                for operation in operations:
                    count = _nexus_delete_query_count(session, operation["count_query"], operation.get("params"))
                    counts.append(count)
                    total_records += count
                row["total"] = total_records

                if total_records <= 0:
                    row["status"] = "completed"
                    row["progress_pct"] = 100.0
                    row["message"] = "Nothing to delete."
                    completed_phases += 1
                    _publish(row["label"], f"{row['label']}: nothing to delete.")
                    continue

                deleted_total = 0
                for operation, operation_total in zip(operations, counts):
                    if operation_total <= 0:
                        continue
                    row["message"] = f"Deleting {operation['label']}..."
                    _publish(row["label"], row["message"])
                    while True:
                        deleted = _nexus_delete_query_batch(
                            session,
                            operation["delete_query"],
                            limit=operation.get("batch_size") or 5000,
                            params=operation.get("params"),
                        )
                        if deleted <= 0:
                            break
                        deleted_total += deleted
                        row["current"] = min(deleted_total, total_records)
                        row["deleted_count"] = deleted_total
                        row["progress_pct"] = round((row["current"] / total_records) * 100.0, 2)
                        _publish(row["label"], row["message"])

                row["status"] = "completed"
                row["current"] = total_records
                row["deleted_count"] = max(deleted_total, total_records)
                row["progress_pct"] = 100.0
                row["message"] = f"Deleted {int(row['deleted_count']):,} record(s)."
                completed_phases += 1
                _publish(row["label"], row["message"])
    except Exception as exc:
        failed_row = None
        for row in phase_rows:
            if row.get("status") == "running":
                failed_row = row
                break
        if failed_row is not None:
            failed_row["status"] = "failed"
            failed_row["message"] = str(exc)
        _set_nexus_delete_operation(
            conn,
            active=False,
            step="Delete failed",
            message=str(exc),
            current=completed_phases,
            total=total_phases,
            unit="phases",
            started_at=started,
            error=str(exc),
            selected_phases=selected,
            phase_rows=phase_rows,
        )
        raise
    finally:
        with suppress(Exception):
            driver.close()

    _set_nexus_delete_operation(
        conn,
        active=False,
        step="Delete complete",
        message="Selected phase cleanup complete.",
        current=total_phases,
        total=total_phases,
        unit="phases",
        started_at=started,
        error=None,
        selected_phases=selected,
        phase_rows=phase_rows,
    )


def action_nexus_delete_edges(conn, selected_phases):
    ensure_nexus_control_table(conn)
    control = get_engine_doc(conn, NEXUS_ENGINE_ID) or {}
    normalized = _normalize_nexus_phase_values(selected_phases)
    if not normalized:
        raise ValueError("Select at least one phase to delete.")
    unsupported = [value for value in normalized if int(value) not in _NEXUS_DELETE_PHASE_OPTION_VALUES]
    if unsupported:
        labels = ", ".join(str(value) for value in unsupported)
        raise ValueError(f"Edge delete is not supported for phase(s): {labels}")
    if bool(control.get("running")):
        raise ValueError("Stop Nexus before deleting edges.")
    if bool(control.get("rebuild_operation_active")):
        raise ValueError("Cannot delete edges while a rebuild is running.")
    if bool(control.get("delete_operation_active")):
        raise ValueError("A delete operation is already running.")

    started = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    phase_rows = _default_nexus_delete_phase_rows(normalized)
    _set_nexus_delete_operation(
        conn,
        active=True,
        step="Queued delete",
        message="Queued selected phase cleanup...",
        current=0,
        total=len(normalized),
        unit="phases",
        started_at=started,
        error=None,
        selected_phases=normalized,
        phase_rows=phase_rows,
    )
    try:
        _start_nexus_delete_operation_async(normalized, started_at=started)
    except Exception:
        _set_nexus_delete_operation(
            conn,
            active=False,
            step="Delete failed",
            message="Unable to start selected phase cleanup.",
            current=0,
            total=len(normalized),
            unit="phases",
            started_at=started,
            error="Unable to start selected phase cleanup.",
            selected_phases=normalized,
            phase_rows=phase_rows,
        )
        raise
    return {
        "success": True,
        "message": "Queued selected phase cleanup.",
        "selected_phases": list(normalized),
        "selected_phase_labels": [
            _nexus_phase_label_from_value(value) or f"Phase {value}"
            for value in normalized
        ],
    }


# Canonical Graph Nexus stage list (index, key, label, progress_pct) for pretty status (board interlocks phase removed)
NEXUS_STAGE_LABELS = [
    (0,  "init",       "Init Neo4j",                    5.0),
    (1,  "check",      "Check if built",               10.0),
    (2,  "phase1",     "Phase 1: Company universe",    16.0),
    (3,  "phase2",     "Phase 2: Easy relationships",  22.0),
    (4,  "phase2b",    "Phase 2b: SEC sector/industry", 28.0),
    (5,  "phase3",     "Phase 3: Supply chain",        34.0),
    (6,  "phase4",     "Phase 4: Competitive",         40.0),
    (7,  "phase5",     "Phase 5: Macro/BEA",           46.0),
    (8,  "phase6",     "Phase 6: GLEIF hierarchy",      52.0),
    (9,  "phase6_ex21","Phase 6B: SEC EX-21 hierarchy", 58.0),
    (10, "phase7",     "Phase 7: 13F ownership",        64.0),
    (11, "phase9",     "Phase 8: USASpending",          70.0),
    (12, "phase10",    "Phase 9: Wikidata",             76.0),
    (13, "phase11",    "Phase 10: PatentsView",         82.0),
    (14, "phase12",    "Phase 11: 8-K agreements",      88.0),
    (15, "phase_etf",  "Phase 12: ETF universe",        94.0),
    (16, "mark_built", "Mark built",                   100.0),
]
# Substeps per stage (same index order). Progress % for a stage = (substeps_completed / total) * 100.
NEXUS_STAGE_SUBSTEPS = [1, 1, 2, 2, 2, 3, 1, 3, 1, 1, 2, 2, 2, 2, 2, 1, 1]


def _nexus_merge_stages(graph_doc, graph_build_status):
    """Merge stored stages from graph_doc with canonical NEXUS_STAGE_LABELS; set graph_build_status['stages']."""
    if graph_build_status is None:
        return graph_build_status
    stored = graph_doc.get("stages") if graph_doc else None
    if not isinstance(stored, list):
        stored = []
    stored_by_idx = {}
    for s in stored:
        try:
            d = dict(s) if hasattr(s, "items") else s
            i = d.get("stage_index")
            if i is not None:
                stored_by_idx[int(i)] = d
        except Exception:
            pass
    stages = []
    for i, (idx, key, label, pct) in enumerate(NEXUS_STAGE_LABELS):
        total_substeps = NEXUS_STAGE_SUBSTEPS[i] if i < len(NEXUS_STAGE_SUBSTEPS) else 1
        entry = stored_by_idx.get(idx)
        if entry:
            e = dict(entry)
            if "label" not in e or not e["label"]:
                e["label"] = label
            if "progress_pct" not in e:
                e["progress_pct"] = pct
            if "key" not in e:
                e["key"] = key
            e["total_substeps"] = total_substeps
            if "substeps_completed" not in e or e.get("substeps_completed") is None:
                st = (e.get("status") or "").lower()
                e["substeps_completed"] = total_substeps if st in ("completed", "skipped") else (0 if st == "running" else 0)
            # Normalize Reql/datetime to ISO string for JSON (CLI/API/Discord)
            for k in ("started_at", "completed_at", "last_updated"):
                if k in e and e.get(k) is not None:
                    try:
                        v = e[k]
                        if hasattr(v, "isoformat"):
                            e[k] = v.isoformat()
                        elif not isinstance(v, str):
                            e[k] = str(v)
                    except Exception:
                        e[k] = str(e[k])
            stages.append(e)
        else:
            stages.append({
                "stage_index": idx,
                "key": key,
                "label": label,
                "progress_pct": pct,
                "status": "pending",
                "message": "",
                "total_substeps": total_substeps,
                "substeps_completed": 0,
            })
    graph_build_status["stages"] = stages
    return graph_build_status


def _nexus_stage_progress_bar(substeps_completed: int, total_substeps: int, width: int = 20) -> tuple[str, float]:
    """Return (bar_string, pct). pct is 0-100 based on substeps_completed/total_substeps."""
    if total_substeps is None or total_substeps <= 0:
        return "[" + "-" * width + "]", 0.0
    n = max(0, min(substeps_completed or 0, total_substeps))
    pct = (n / total_substeps) * 100.0
    filled = round((n / total_substeps) * width)
    filled = max(0, min(filled, width))
    bar = "[" + "#" * filled + "-" * (width - filled) + "]"
    return bar, pct


def format_nexus_status_for_cli(out):
    """Pretty-print Nexus status for CLI: control, graph build with per-stage progress bars (substep-based), scraper."""
    lines = []
    ctrl = out.get("control") or {}
    gb = out.get("graph_build")
    scr = out.get("scraper")
    built = out.get("graph_built")
    lines.append("  Nexus running: %s" % ctrl.get("running", False))
    lines.append("  Graph built:   %s" % built)
    if gb:
        lines.append("  Build status:  %s" % (gb.get("status_message") or gb.get("message") or gb.get("status") or "—"))
        if gb.get("eta_formatted"):
            lines.append("  ETA:           ~%s remaining" % gb.get("eta_formatted"))
        stages = gb.get("stages")
        if stages:
            lines.append("  ")
            lines.append("  Stages (progress = substeps completed within each stage):")
            w = max(len(s.get("label") or "") for s in stages)
            w = min(max(w, 28), 50)
            for s in stages:
                status = (s.get("status") or "pending").lower()
                if status == "completed":
                    icon = "✓"
                elif status == "running":
                    icon = "→"
                elif status == "skipped":
                    icon = "−"
                elif status == "failed":
                    icon = "✗"
                else:
                    icon = " "
                total = s.get("total_substeps") or 1
                done = s.get("substeps_completed")
                if done is None:
                    done = total if status in ("completed", "skipped") else 0
                done = max(0, min(int(done), total))
                bar, pct = _nexus_stage_progress_bar(done, total, width=20)
                sub_str = " %d/%d substeps %3.0f%%" % (done, total, pct)
                dur = s.get("duration_sec")
                dur_str = " (%.1fs)" % dur if dur is not None else ""
                msg = (s.get("message") or "").strip()
                if msg and len(msg) > 50:
                    msg = msg[:47] + "..."
                line = "    %s %s %s%s%s" % (icon, (s.get("label") or "").ljust(w), bar, sub_str, dur_str)
                if msg:
                    line += "  %s" % msg
                lines.append(line)
    if scr:
        lines.append("  Scraper:       status=%s, progress=%s%%, ticker %s, %s" % (
            scr.get("status"), scr.get("progress_pct"), scr.get("index"), scr.get("message") or "—"
        ))
    return "\n".join(lines)


def format_nexus_status_for_discord(out, max_field_chars=1024):
    """Build Discord embed dict for Nexus status: title, description, fields (stages in one code block)."""
    ctrl = out.get("control") or {}
    gb = out.get("graph_build")
    scr = out.get("scraper")
    built = out.get("graph_built")
    title = "Nexus status"
    parts = [
        "**Running:** %s" % ctrl.get("running", False),
        "**Graph built:** %s" % built,
    ]
    if gb:
        parts.append("**Build:** %s" % (gb.get("status_message") or gb.get("message") or gb.get("status") or "—"))
        if gb.get("eta_formatted"):
            parts.append("**ETA:** ~%s remaining" % gb.get("eta_formatted"))
    description = "\n".join(parts)
    fields = []
    stages = (gb or {}).get("stages")
    if stages:
        block_lines = []
        for s in stages:
            status = (s.get("status") or "pending").lower()
            icon = "✓" if status == "completed" else ("→" if status == "running" else ("−" if status == "skipped" else ("✗" if status == "failed" else "○")))
            total = s.get("total_substeps") or 1
            done = s.get("substeps_completed")
            if done is None:
                done = total if status in ("completed", "skipped") else 0
            done = max(0, min(int(done), total))
            bar, pct = _nexus_stage_progress_bar(done, total, width=10)
            sub_str = " %d/%d %3.0f%%" % (done, total, pct)
            dur = s.get("duration_sec")
            dur_str = " %.1fs" % dur if dur is not None else ""
            label = (s.get("label") or "").strip()[:28]
            block_lines.append("%s %s %s%s%s" % (icon, label.ljust(28), bar, sub_str, dur_str))
        body = "```\n%s\n```" % "\n".join(block_lines)
        if len(body) > max_field_chars:
            body = "```\n" + "\n".join(block_lines[:12]) + "\n... (%s more)\n```" % (len(block_lines) - 12)
        fields.append({"name": "Build stages", "value": body, "inline": False})
    if scr:
        fields.append({
            "name": "Scraper",
            "value": "status=%s, progress=%s%%, ticker %s, %s" % (
                scr.get("status"), scr.get("progress_pct"), scr.get("index"), (scr.get("message") or "—")[:200]
            ),
            "inline": False,
        })
    return {"title": title, "description": description, "color": 0x3498DB, "fields": fields}


def action_nexus_status(conn):
    """
    Comprehensive Graph Nexus status: control (running), graph build progress (GraphNexusProgress.graph_nexus),
    SEC EDGAR scraper progress (GraphNexusProgress.sec_edgar_scraper). For use by CLI, API, Discord.
    """
    out = {"control": action_nexus_control_get(conn)}
    out["bootstrap"] = _build_nexus_bootstrap_status(out["control"], None)
    out["graph_summary"] = _load_nexus_graph_summary(
        force_refresh=bool(
            out["control"].get("running")
            or out["control"].get("rebuild_operation_active")
            or out["control"].get("delete_operation_active")
        )
    )
    try:
        tables = list(r.db(DB_NAME).table_list().run(conn))
        if "GraphNexusProgress" not in tables:
            out["graph_build"] = None
            out["scraper"] = None
            out["graph_built"] = None
            out["bootstrap"] = _build_nexus_bootstrap_status(out["control"], None)
            return out
        tbl = r.db(DB_NAME).table("GraphNexusProgress")
        graph_doc = tbl.get("graph_nexus").run(conn)
        scraper_doc = tbl.get("sec_edgar_scraper").run(conn)
        out["graph_build"] = _nexus_progress_doc_to_status(graph_doc, "last_completed_phase", "phase")
        scr = _nexus_progress_doc_to_status(scraper_doc, "last_ticker_index", "ticker")
        if scraper_doc and scr:
            scr["total_tickers"] = scraper_doc.get("total_tickers")
            scr["edges_count"] = scraper_doc.get("edges_count")
        out["scraper"] = scr
        out["graph_built"] = bool(
            graph_doc and (graph_doc.get("last_completed_phase") == 14 or graph_doc.get("status") == "completed")
        )
        # Merge stored stages with canonical list so CLI/API/Discord get full per-stage progress
        out["graph_build"] = _nexus_merge_stages(graph_doc, out.get("graph_build"))
        out["graph_build"] = _nexus_apply_control_to_graph_build(out["control"], out.get("graph_build"))
        out["bootstrap"] = _build_nexus_bootstrap_status(out["control"], out.get("graph_build"))
    except Exception:
        out["graph_build"] = None
        out["scraper"] = None
        out["graph_built"] = None
        out["bootstrap"] = _build_nexus_bootstrap_status(out["control"], None)
    return out


def _format_eta_seconds(sec):
    """Format seconds as human-readable ETA e.g. '1h 20m' or '45m 30s'."""
    if sec is None or sec < 0:
        return None
    sec = int(round(sec))
    if sec < 60:
        return "%ds" % sec
    m, s = divmod(sec, 60)
    if m < 60:
        return "%dm %ds" % (m, s) if s else "%dm" % m
    h, m = divmod(m, 60)
    return "%dh %dm" % (h, m) if m else "%dh" % h


def _nexus_progress_doc_to_status(doc, index_key, index_label):
    """Turn a progress doc into a status dict for API/CLI/Discord. Includes phase label and ETA when present."""
    if not doc:
        return None
    eta_sec = doc.get("eta_remaining_sec")
    eta_formatted = _format_eta_seconds(eta_sec) if eta_sec is not None else None
    phase_label = doc.get("current_phase_label") or ""
    phase_num = doc.get("current_phase_number")
    progress_pct = doc.get("progress_pct")
    message = doc.get("message") or ""
    # Build a single status line for display
    status_parts = []
    if phase_label:
        status_parts.append(phase_label)
    if progress_pct is not None:
        status_parts.append("%.0f%%" % progress_pct)
    if eta_formatted:
        status_parts.append("ETA ~%s" % eta_formatted)
    status_message = " — ".join(status_parts) if status_parts else message
    return {
        "status": doc.get("status"),
        "message": message,
        "status_message": status_message,
        "progress_pct": progress_pct,
        "last_updated": doc.get("last_updated"),
        "index": doc.get(index_key),
        "index_label": index_label,
        "eta_remaining_sec": eta_sec,
        "eta_formatted": eta_formatted,
        "current_phase_label": phase_label,
        "current_phase_number": phase_num,
    }


def _nexus_apply_control_to_graph_build(control, graph_build):
    if not graph_build:
        return graph_build
    control = dict(control or {})
    normalized = dict(graph_build)
    status = str(normalized.get("status") or "").strip().lower()
    service_running = bool(control.get("running"))
    rebuild_active = bool(control.get("rebuild_operation_active"))
    current_phase_number = normalized.get("current_phase_number")
    current_phase_label = str(normalized.get("current_phase_label") or "").strip()
    updated_stages = []
    running_stage_indexes = []
    for stage in list(normalized.get("stages") or []):
        entry = dict(stage)
        updated_stages.append(entry)
        if str(entry.get("status") or "").strip().lower() == "running":
            running_stage_indexes.append(entry.get("stage_index"))
    if len(running_stage_indexes) > 1:
        preferred_running_index = None
        if current_phase_label:
            for stage in updated_stages:
                if str(stage.get("label") or "").strip() == current_phase_label:
                    preferred_running_index = stage.get("stage_index")
                    break
        if preferred_running_index is None and isinstance(current_phase_number, int):
            for stage in updated_stages:
                label = str(stage.get("label") or "")
                if label.startswith(f"Phase {current_phase_number}:"):
                    preferred_running_index = stage.get("stage_index")
                    break
        if preferred_running_index is None:
            preferred_running_index = max(
                (idx for idx in running_stage_indexes if isinstance(idx, int)),
                default=running_stage_indexes[-1],
            )
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        for entry in updated_stages:
            if str(entry.get("status") or "").strip().lower() != "running":
                continue
            if entry.get("stage_index") == preferred_running_index:
                continue
            entry_idx = entry.get("stage_index")
            if isinstance(entry_idx, int) and isinstance(preferred_running_index, int) and entry_idx < preferred_running_index:
                entry["status"] = "completed"
                total_substeps = entry.get("total_substeps") or 1
                entry["substeps_completed"] = total_substeps
                entry.setdefault("completed_at", now_iso)
            else:
                entry["status"] = "stopped"
                if not entry.get("message"):
                    entry["message"] = "Stopped"
        normalized["stages"] = updated_stages
    if status == "running" and not service_running and not rebuild_active:
        normalized["status"] = "stopped"
        normalized["message"] = "Stopped"
        normalized["status_message"] = "Stopped"
        stopped_stages = []
        for stage in list(normalized.get("stages") or []):
            entry = dict(stage)
            if str(entry.get("status") or "").strip().lower() == "running":
                entry["status"] = "stopped"
                if not entry.get("message"):
                    entry["message"] = "Stopped"
            stopped_stages.append(entry)
        if stopped_stages:
            normalized["stages"] = stopped_stages
    return normalized


def _load_nexus_graph_summary(force_refresh: bool = False):
    now = time.monotonic()
    cached = _NEXUS_GRAPH_SUMMARY_CACHE.get("data")
    fetched_at = float(_NEXUS_GRAPH_SUMMARY_CACHE.get("fetched_at") or 0.0)
    if not force_refresh and cached is not None and (now - fetched_at) < _NEXUS_GRAPH_SUMMARY_TTL_SEC:
        return dict(cached)

    summary = {
        "relationship_counts": [],
        "node_counts": {},
        "fetched_at": None,
    }
    driver = None
    try:
        from neo4j import GraphDatabase

        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "password")
        rel_types = [key for key, _ in _NEXUS_RELATIONSHIP_LABELS]
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as session:
            rel_rows = list(session.run("""
                MATCH ()-[r]->()
                WITH type(r) AS rel_type,
                     sum(CASE WHEN coalesce(r.edge_state, 'open') = 'open' THEN 1 ELSE 0 END) AS active_count,
                     count(r) AS total_count
                WHERE rel_type IN $rel_types
                RETURN rel_type, active_count, total_count
            """, rel_types=rel_types))
            rel_map = {
                str(row.get("rel_type") or ""): {
                    "active_count": int(row.get("active_count") or 0),
                    "total_count": int(row.get("total_count") or 0),
                }
                for row in rel_rows
                if row.get("rel_type")
            }
            summary["relationship_counts"] = [
                {
                    "key": key,
                    "label": label,
                    "active_count": rel_map.get(key, {}).get("active_count", 0),
                    "total_count": rel_map.get(key, {}).get("total_count", 0),
                }
                for key, label in _NEXUS_RELATIONSHIP_LABELS
            ]

            node_row = session.run("""
                CALL { MATCH (:Company) RETURN count(*) AS companies }
                CALL { MATCH (:ETF) RETURN count(*) AS etfs }
                CALL { MATCH (:Institution) RETURN count(*) AS institutions }
                CALL { MATCH (:GovAgency) RETURN count(*) AS agencies }
                CALL { MATCH (:GraphEdgeInterval) RETURN count(*) AS edge_intervals }
                RETURN companies, etfs, institutions, agencies, edge_intervals
            """).single() or {}
            summary["node_counts"] = {
                "companies": int(node_row.get("companies") or 0),
                "etfs": int(node_row.get("etfs") or 0),
                "institutions": int(node_row.get("institutions") or 0),
                "agencies": int(node_row.get("agencies") or 0),
                "edge_intervals": int(node_row.get("edge_intervals") or 0),
            }
            summary["fetched_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        pass
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass

    _NEXUS_GRAPH_SUMMARY_CACHE["fetched_at"] = now
    _NEXUS_GRAPH_SUMMARY_CACHE["data"] = dict(summary)
    return dict(summary)


_HISTORICAL_BOOTSTRAP_PHASES = [
    {"key": "phase3", "label": "Phase 3: Supply chain", "current_phase_number": 4},
    {"key": "phase7", "label": "Phase 7: 13F ownership", "current_phase_number": 9},
    {"key": "phase12", "label": "Phase 11: 8-K agreements", "current_phase_number": 13},
]


def _normalize_nexus_iso_date(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.datetime.strptime(raw[:10], "%Y-%m-%d").date().isoformat()
    except Exception:
        return None


def _parse_nexus_iso_datetime(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def _build_nexus_bootstrap_status(control, graph_build):
    control = dict(control or {})
    graph_build = dict(graph_build or {})
    enabled = bool(control.get("historical_mode_enabled") and control.get("historical_start_date"))
    manifests = dict(control.get("historical_phase_manifests") or {})
    current_phase_number = graph_build.get("current_phase_number")
    build_status = str(graph_build.get("status") or "").strip().lower()
    running = build_status == "running"
    started_at = control.get("last_historical_bootstrap_started_at")
    completed_at = control.get("last_historical_bootstrap_completed_at")
    rebuild_requested_at = control.get("rebuild_requested_at")
    last_bootstrap_status = str(control.get("last_historical_bootstrap_status") or "").strip().lower()
    start_dt = _parse_nexus_iso_datetime(started_at)
    end_dt = _parse_nexus_iso_datetime(completed_at)
    rebuild_dt = _parse_nexus_iso_datetime(rebuild_requested_at)
    coverage_end_control = _normalize_nexus_iso_date(control.get("historical_coverage_end"))
    fresh_bootstrap_rebuild = bool(
        enabled
        and running
        and (
            bool(control.get("force_bootstrap_rebuild"))
            or (rebuild_dt is not None and (end_dt is None or rebuild_dt > end_dt))
        )
    )
    stale_bootstrap_completion = bool(
        enabled
        and rebuild_dt is not None
        and (end_dt is None or rebuild_dt > end_dt)
        and not coverage_end_control
    )
    bootstrap_running = bool(
        enabled
        and (
            last_bootstrap_status == "running"
            or fresh_bootstrap_rebuild
            or (
                running
                and start_dt is not None
                and (end_dt is None or start_dt > end_dt)
            )
        )
    )
    mask_stale_completion = bool(bootstrap_running or stale_bootstrap_completion)
    rows = []
    completed_count = 0
    max_coverage_end = None if mask_stale_completion else coverage_end_control

    for phase in _HISTORICAL_BOOTSTRAP_PHASES:
        manifest = dict(manifests.get(phase["key"]) or {})
        phase_reached_this_run = bool(
            mask_stale_completion
            and isinstance(current_phase_number, int)
            and current_phase_number > int(phase["current_phase_number"])
        )
        allow_manifest_completion = bool(not mask_stale_completion or phase_reached_this_run)
        coverage_start = _normalize_nexus_iso_date(manifest.get("coverage_start")) if allow_manifest_completion else None
        coverage_end = _normalize_nexus_iso_date(manifest.get("coverage_end")) if allow_manifest_completion else None
        if (
            coverage_end
            and not mask_stale_completion
            and (not max_coverage_end or coverage_end > max_coverage_end)
        ):
            max_coverage_end = coverage_end
        status = "pending"
        manifest_complete = bool(manifest.get("bootstrap_complete")) if allow_manifest_completion else False
        if running and current_phase_number == phase["current_phase_number"] and not manifest_complete:
            status = "running"
        elif manifest_complete:
            status = "completed"
            completed_count += 1
        elif coverage_end:
            status = "partial"
        rows.append({
            "key": phase["key"],
            "label": phase["label"],
            "status": status,
            "mode": manifest.get("mode"),
            "coverage_start": coverage_start,
            "coverage_end": coverage_end,
            "last_incremental_start": _normalize_nexus_iso_date(manifest.get("last_incremental_start")),
            "last_incremental_end": _normalize_nexus_iso_date(manifest.get("last_incremental_end")),
            "bootstrap_complete": manifest_complete,
            "edge_count": manifest.get("edge_count"),
            "filings_processed": manifest.get("filings_processed"),
            "processed_snapshots": manifest.get("processed_snapshots"),
            "latest_event_date": _normalize_nexus_iso_date(manifest.get("latest_event_date")),
            "last_period_key": manifest.get("last_period_key"),
        })

    all_phases_complete = bool(rows) and completed_count == len(_HISTORICAL_BOOTSTRAP_PHASES)
    complete = bool(
        (not mask_stale_completion and control.get("historical_bootstrap_complete"))
        or (not mask_stale_completion and last_bootstrap_status == "completed")
        or all_phases_complete
    )
    duration_sec = control.get("last_historical_bootstrap_duration_sec")
    has_history = bool(
        max_coverage_end
        or started_at
        or completed_at
        or any(row["status"] in ("partial", "completed") for row in rows)
    )
    status = "never_built"
    if enabled:
        if bootstrap_running or (running and not complete):
            status = "running"
        elif stale_bootstrap_completion:
            status = "partial"
        elif complete:
            status = "completed"
        elif any(row["status"] in ("partial", "completed") for row in rows):
            status = "partial"
        else:
            status = "pending"
    elif has_history:
        status = "disabled"

    if duration_sec is None and started_at and completed_at:
        if start_dt and end_dt and end_dt >= start_dt:
            duration_sec = round((end_dt - start_dt).total_seconds(), 1)

    if status == "never_built":
        message = (
            "Historical bootstrap has never been built. Enable it with a start date to backfill "
            "Phase 3, Phase 7, and Phase 11 for backtests."
        )
    elif not enabled:
        message = "Historical bootstrap disabled."
    elif status == "completed":
        message = (
            f"Historical bootstrap complete for {control.get('historical_start_date')} -> "
            f"{max_coverage_end or control.get('historical_coverage_end') or 'today'}."
        )
    elif status == "running":
        current_label = graph_build.get("current_phase_label")
        message = (
            f"Historical bootstrap running from {control.get('historical_start_date')}"
            + (f" — {current_label}" if current_label else ".")
        )
    elif status == "partial":
        if stale_bootstrap_completion and str(graph_build.get("status") or "").strip().lower() == "stopped":
            message = "Historical bootstrap rebuild was stopped before completion."
        else:
            message = (
                f"Historical bootstrap partially built through {max_coverage_end or 'unknown date'}."
            )
    else:
        message = f"Historical bootstrap queued from {control.get('historical_start_date')}."

    return {
        "enabled": enabled,
        "status": status,
        "active": status == "running",
        "complete": complete,
        "start_date": control.get("historical_start_date"),
        "coverage_end": None if mask_stale_completion else (max_coverage_end or control.get("historical_coverage_end")),
        "completed_phases": completed_count,
        "total_phases": len(_HISTORICAL_BOOTSTRAP_PHASES),
        "message": message,
        "phases": rows,
        "current_phase_label": graph_build.get("current_phase_label"),
        "current_phase_number": current_phase_number,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_sec": duration_sec,
        "last_status": control.get("last_historical_bootstrap_status"),
    }


def action_nexus_rebuild(conn=None, delete_cache_paths=None, destructive=False, force_bootstrap_rebuild=False):
    """
    Queue a Nexus rebuild from phase 1.
    Non-destructive mode keeps existing graph data online while phases rerun in place.
    Destructive mode clears Neo4j data and resets Nexus progress before restarting the rebuild.
    If the Nexus container is running, selected cache entries are deleted inside the container and
    the container is restarted so the rebuild starts from a clean process state.
    """
    requested_cache_paths = delete_cache_paths or []
    destructive = bool(destructive)
    force_bootstrap_rebuild = bool(force_bootstrap_rebuild)
    result = {
        "success": True,
        "message": (
            "Nexus full rebuild requested. Existing graph data stays online while phases rerun in place."
            if not destructive
            else "Destructive Nexus rebuild requested."
        ),
        "neo4j_cleared": False,
        "rethinkdb_cleared": False,
        "error": None,
        "non_destructive": not destructive,
        "deleted_cache_paths": [],
        "container_running": False,
        "container_restarted": False,
        "container_restart_mode": None,
        "cache_root": NEXUS_CONTAINER_CACHE_ROOT,
        "force_bootstrap_rebuild": force_bootstrap_rebuild,
    }
    owns_conn = conn is None
    rconn = conn or get_conn()
    try:
        control_doc = get_engine_doc(rconn, NEXUS_ENGINE_ID) or {}
        bootstrap_start_date = _nexus_force_bootstrap_start_date(control_doc)
        _set_nexus_rebuild_operation(
            rconn,
            active=True,
            destructive=destructive,
            step="preparing",
            message="Preparing Nexus rebuild request...",
        )
        direct_cache_root = _get_direct_nexus_cache_root()
        existing_container = _get_nexus_container()
        running_container = _get_running_nexus_container()
        result["container_running"] = running_container is not None
        if direct_cache_root:
            result["cache_root"] = direct_cache_root
        auto_delete_paths = []
        if destructive or force_bootstrap_rebuild:
            auto_delete_paths.extend(_NEXUS_PHASE3_DERIVED_CACHE_PATHS)

        normalized_paths = []
        if requested_cache_paths or auto_delete_paths:
            if direct_cache_root:
                available_entries = _list_nexus_cache_entries_on_filesystem(direct_cache_root)
            else:
                if running_container is None:
                    if requested_cache_paths:
                        raise ValueError(
                            "Selected cache entries can only be deleted while the Nexus container is running or the Nexus cache root is mounted into the API environment."
                        )
                    available_entries = []
                else:
                    available_entries = _list_nexus_cache_entries_in_container(running_container)
            if requested_cache_paths:
                normalized_paths = _normalize_nexus_cache_delete_paths(requested_cache_paths, available_entries)
            available_paths = {str(entry.get("path") or "") for entry in available_entries}
            for rel in auto_delete_paths:
                if rel in available_paths and rel not in normalized_paths:
                    normalized_paths.append(rel)
            if normalized_paths:
                if direct_cache_root:
                    _set_nexus_rebuild_operation(
                        rconn,
                        active=True,
                        destructive=destructive,
                        step="cache_cleanup",
                        message="Deleting selected Nexus cache entries...",
                        unit="entries",
                    )
                    result["deleted_cache_paths"] = _delete_nexus_cache_entries_on_filesystem(direct_cache_root, normalized_paths)
                else:
                    _set_nexus_rebuild_operation(
                        rconn,
                        active=True,
                        destructive=destructive,
                        step="cache_cleanup",
                        message="Deleting selected Nexus cache entries in the running container...",
                        unit="entries",
                    )
                    result["deleted_cache_paths"] = _delete_nexus_cache_entries_in_container(running_container, normalized_paths)

        if destructive:
            queue_message = "Queued destructive rebuild from phase 1 after clearing Neo4j and Nexus progress"
            if result["deleted_cache_paths"]:
                queue_message += " and selected cache entries"
            if existing_container is not None:
                _set_nexus_rebuild_operation(
                    rconn,
                    active=True,
                    destructive=True,
                    step="container_stop",
                    message="Stopping the existing Nexus container before destructive rebuild...",
                )
                remove_result = _remove_nexus_container(existing_container)
                result["container_restarted"] = True
                result["container_restart_mode"] = remove_result.get("mode")
            def _neo4j_progress(step_label, current, _batch_deleted, batch_size_used, progress_message):
                _set_nexus_rebuild_operation(
                    rconn,
                    active=True,
                    destructive=True,
                    step=step_label,
                    message=progress_message,
                    current=current,
                    unit="graph items",
                    total=None,
                )

            _set_nexus_rebuild_operation(
                rconn,
                active=True,
                destructive=True,
                step="neo4j_clear",
                message="Clearing Neo4j graph data in batches...",
                current=0,
                unit="graph items",
            )
            _reset_nexus_graph_summary_cache()
            _clear_nexus_graph_neo4j(progress_cb=_neo4j_progress)
            _reset_nexus_graph_summary_cache()
            result["neo4j_cleared"] = True
            _set_nexus_rebuild_operation(
                rconn,
                active=True,
                destructive=True,
                step="rethink_reset",
                message="Resetting Nexus rebuild state in RethinkDB...",
            )
            _reset_nexus_bootstrap_progress_state(
                rconn,
                queue_message,
                reset_progress_docs=True,
            )
            result["rethinkdb_cleared"] = True
            _set_nexus_rebuild_operation(
                rconn,
                active=True,
                destructive=True,
                step="queue_rebuild",
                message="Queueing the destructive Nexus rebuild...",
            )
            _queue_nexus_rebuild(rconn, queue_message)
        else:
            queue_message = "Queued non-destructive rebuild from phase 1"
            if result["deleted_cache_paths"]:
                queue_message += " after cache cleanup"
            if force_bootstrap_rebuild:
                if not bootstrap_start_date:
                    raise ValueError("force_bootstrap_rebuild requires a configured historical bootstrap start date")
                queue_message += " with historical bootstrap reset"
                _reset_nexus_bootstrap_progress_state(
                    rconn,
                    queue_message,
                    reset_progress_docs=True,
                )
                update_engine_doc(rconn, NEXUS_ENGINE_ID, {
                    **_nexus_bootstrap_reset_update(clear_start_date=False),
                    "historical_mode_enabled": True,
                    "historical_start_date": bootstrap_start_date,
                    "force_bootstrap_rebuild": True,
                })
            _set_nexus_rebuild_operation(
                rconn,
                active=True,
                destructive=False,
                step="queue_rebuild",
                message="Queueing the Nexus rebuild...",
            )
            _queue_nexus_rebuild(rconn, queue_message)

            if running_container is not None:
                _set_nexus_rebuild_operation(
                    rconn,
                    active=True,
                    destructive=False,
                    step="container_restart",
                    message="Restarting the Nexus container so the rebuild starts cleanly...",
                )
                restart_result = _restart_nexus_container(running_container)
                result["container_restarted"] = True
                result["container_restart_mode"] = restart_result.get("mode")
                if restart_result.get("mode") == "server_recreate":
                    _queue_nexus_rebuild(rconn, queue_message + " (container recreated)")

        message_bits = ["Destructive Nexus rebuild requested." if destructive else "Nexus full rebuild requested."]
        if result["deleted_cache_paths"]:
            message_bits.append(
                "Deleted cache entries: %s." % ", ".join(result["deleted_cache_paths"])
            )
        if destructive:
            if result["neo4j_cleared"]:
                message_bits.append("Neo4j graph data was cleared.")
            if result["rethinkdb_cleared"]:
                message_bits.append("Nexus progress in RethinkDB was reset.")
        elif force_bootstrap_rebuild:
            message_bits.append("Historical bootstrap checkpoints were reset so temporal phases will rebuild from the configured bootstrap start date.")
        if result["container_restarted"]:
            if result["container_restart_mode"] == "server_recreate":
                message_bits.append("The running Nexus container was replaced and will be recreated automatically.")
            else:
                message_bits.append("The running Nexus container was restarted automatically.")
        else:
            if destructive:
                message_bits.append("No existing Nexus container was running, so a fresh rebuild start was queued.")
            else:
                message_bits.append("No running Nexus container was found, so the rebuild was queued without a container restart.")
        message_bits.append(
            "Existing graph data stays online while phases rerun in place."
            if not destructive
            else "The graph is intentionally cleared first, so strategy reads will be unavailable until the rebuild repopulates Neo4j."
        )
        result["message"] = " ".join(message_bits)
        _set_nexus_rebuild_operation(
            rconn,
            active=False,
            step="completed",
            message="Nexus rebuild request completed.",
        )
    except Exception as e:
        result["success"] = False
        result["error"] = str(e)
        result["message"] = "Could not queue Nexus rebuild: %s" % str(e)
        with suppress(Exception):
            _set_nexus_rebuild_operation(
                rconn,
                active=False,
                step="failed",
                message=result["message"],
            )
    finally:
        if owns_conn:
            try:
                rconn.close()
            except Exception:
                pass
    return result


# --- Strategies ---


def action_strategies(conn, instance_filter=None):
    ensure_strategies_table(conn)
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "Strategies" not in tables:
        return {"strategies": []}
    rows = list(r.db(DB_NAME).table("Strategies").order_by("id").run(conn))
    instances_by_strategy = {}
    if "Instances" in tables:
        instance_rows = list(
            r.db(DB_NAME)
            .table("Instances")
            .pluck("id", "strategy_id")
            .run(conn)
        )
        for inst in instance_rows:
            sid = inst.get("strategy_id")
            if sid is None:
                continue
            instances_by_strategy.setdefault(sid, []).append(str(inst.get("id")))
    strategies = []
    for row in rows:
        row_id = row.get("id")
        strategies_array = row.get("strategies", [])
        if not isinstance(strategies_array, list):
            strategies_array = []
        normalized_array = []
        for strategy_doc in strategies_array:
            normalized_item = _normalize_strategy_payload_item(strategy_doc, strict=False)
            if normalized_item is not None:
                normalized_array.append(normalized_item)
        inst_ids = instances_by_strategy.get(row_id, [])
        strategies.append({
            "id": row_id,
            "name": row.get("name", "id=%s" % row_id),
            "strategies": normalized_array,
            "instances_using": inst_ids,
        })
    return {"strategies": strategies}


def action_get_strategy(conn, strategy_id):
    try:
        sid = int(strategy_id)
    except (TypeError, ValueError):
        raise ValueError("Strategy ID must be an integer")
    ensure_strategies_table(conn)
    doc = r.db(DB_NAME).table("Strategies").get(sid).run(conn)
    if doc is None:
        raise ValueError("Strategy not found: %s" % sid)
    normalized_array = []
    for strategy_doc in (doc.get("strategies") or []):
        normalized_item = _normalize_strategy_payload_item(strategy_doc, strict=False)
        if normalized_item is not None:
            normalized_array.append(normalized_item)
    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "strategies": normalized_array,
    }


def action_create_strategy(conn, name, strategies):
    if not name or not str(name).strip():
        raise ValueError("Strategy name required")
    if not strategies or not isinstance(strategies, list):
        raise ValueError("At least one sub-strategy required")
    ensure_strategies_table(conn)
    normalized = []
    for s in strategies:
        item = _normalize_strategy_payload_item(s, strict=True)
        if item is not None:
            normalized.append(item)
    if not normalized:
        raise ValueError("No valid sub-strategies")
    # Retry loop to handle concurrent strategy creation (parallel threads may race on next_strategy_id)
    doc = {
        "name": str(name).strip(),
        "strategies": normalized,
    }
    max_attempts = 10
    for attempt in range(max_attempts):
        next_id = next_strategy_id(conn)
        doc["id"] = next_id
        result = r.db(DB_NAME).table("Strategies").insert(doc, conflict="error").run(conn)
        if result.get("inserted", 0) == 1:
            return {"id": next_id, "name": doc["name"], "strategies": normalized}
        # Duplicate key — another thread grabbed this ID; retry with a new one
    raise RuntimeError("Could not generate unique strategy id after %d attempts" % max_attempts)


def action_edit_strategy(conn, strategy_id, name=None, strategies=None):
    try:
        sid = int(strategy_id)
    except (TypeError, ValueError):
        raise ValueError("Strategy ID must be an integer")
    ensure_strategies_table(conn)
    doc = r.db(DB_NAME).table("Strategies").get(sid).run(conn)
    if doc is None:
        raise ValueError("Strategy not found: %s" % sid)
    update = {}
    if name is not None:
        update["name"] = str(name).strip()
    if strategies is not None:
        if not isinstance(strategies, list):
            raise ValueError("strategies must be a list")
        normalized = []
        for s in strategies:
            item = _normalize_strategy_payload_item(s, strict=True)
            if item is not None:
                normalized.append(item)
        update["strategies"] = normalized
    if not update:
        return {"updated": True, "id": sid}
    r.db(DB_NAME).table("Strategies").get(sid).update(update).run(conn)
    return {"updated": True, "id": sid}


def action_delete_strategy(conn, strategy_id, force=False):
    try:
        sid = int(strategy_id)
    except (TypeError, ValueError):
        raise ValueError("Strategy ID must be an integer")
    ensure_strategies_table(conn)
    doc = r.db(DB_NAME).table("Strategies").get(sid).run(conn)
    if doc is None:
        return {"deleted": False, "id": sid}
    instances_using = list(
        r.db(DB_NAME).table("Instances").filter(r.row["strategy_id"] == sid).run(conn)
    )
    if instances_using and not force:
        inst_ids = [str(i.get("id")) for i in instances_using]
        raise ValueError(
            "Strategy is used by instance(s): %s. Use force=true to delete anyway." % ", ".join(inst_ids)
        )
    r.db(DB_NAME).table("Strategies").get(sid).delete().run(conn)
    return {"deleted": True, "id": sid}


def action_unlink_strategy(conn, instance_id):
    ensure_instances_table(conn)
    doc = _resolve_instance_doc(conn, instance_id)
    if doc is None:
        raise ValueError("Instance not found: %s" % instance_id)
    instance_id_actual = doc.get("id", instance_id)
    r.db(DB_NAME).table("Instances").get(instance_id_actual).update({"strategy_id": None}).run(conn)
    return {"unlinked": True, "instance_id": instance_id_actual}


def action_link_strategy(conn, instance_id, strategy_id):
    try:
        strategy_id_int = int(strategy_id)
    except (TypeError, ValueError):
        raise ValueError("Strategy ID must be an integer")
    ensure_instances_table(conn)
    ensure_strategies_table(conn)
    doc = _resolve_instance_doc(conn, instance_id)
    if doc is None:
        raise ValueError("Instance not found: %s" % instance_id)
    instance_id_actual = doc.get("id", instance_id)
    strategy_doc = r.db(DB_NAME).table("Strategies").get(strategy_id_int).run(conn)
    if strategy_doc is None:
        raise ValueError("Strategy not found: %s" % strategy_id_int)
    current_strategy_id = doc.get("strategy_id")
    if current_strategy_id == strategy_id_int:
        strategy_name = strategy_doc.get("name", "id=%s" % strategy_id_int)
        raise ValueError(
            "Instance %s is already linked to strategy id=%s '%s'."
            % (instance_id_actual, strategy_id_int, strategy_name)
        )
    r.db(DB_NAME).table("Instances").get(instance_id_actual).update(
        {"strategy_id": strategy_id_int}
    ).run(conn)
    return {"linked": True, "instance_id": instance_id_actual, "strategy_id": strategy_id_int}


def action_link_brokerage_to_instance(conn, instance_id, brokerage_id):
    """Set or clear the brokerage on an instance. Pass brokerage_id=None to unlink."""
    ensure_instances_table(conn)
    doc = _resolve_instance_doc(conn, instance_id)
    if doc is None:
        raise ValueError("Instance not found: %s" % instance_id)
    instance_id_actual = doc.get("id", instance_id)
    if brokerage_id:
        brok = r.db(DB_NAME).table("BrokerageAccounts").get(brokerage_id).run(conn)
        if brok is None:
            raise ValueError("Brokerage not found: %s" % brokerage_id)
    r.db(DB_NAME).table("Instances").get(instance_id_actual).update({"brokerage_id": brokerage_id}).run(conn)
    return {"linked": True, "instance_id": instance_id_actual, "brokerage_id": brokerage_id}


def action_link_data_brokerage_to_instance(conn, instance_id, brokerage_id):
    """Set or clear the market-data Alpaca brokerage on an instance.

    Use when the trading brokerage is paper (no market-data subscription) and
    you want bars/news to come from a separate live Alpaca account.

    Pass brokerage_id=None to clear (strategy will fall back to trading creds
    for data fetches).
    """
    ensure_instances_table(conn)
    doc = _resolve_instance_doc(conn, instance_id)
    if doc is None:
        raise ValueError("Instance not found: %s" % instance_id)
    instance_id_actual = doc.get("id", instance_id)
    if brokerage_id:
        brok = r.db(DB_NAME).table("BrokerageAccounts").get(brokerage_id).run(conn)
        if brok is None:
            raise ValueError("Brokerage not found: %s" % brokerage_id)
        if (brok.get("brokerage_type") or "").strip().lower() != "alpaca":
            raise ValueError(
                "Data-source brokerage must be an Alpaca account "
                "(got brokerage_type=%r)" % brok.get("brokerage_type")
            )
    r.db(DB_NAME).table("Instances").get(instance_id_actual).update(
        {"alpaca_data_brokerage_id": brokerage_id}
    ).run(conn)
    return {
        "linked": True,
        "instance_id": instance_id_actual,
        "alpaca_data_brokerage_id": brokerage_id,
    }


# --- Backtests ---


def action_list_backtests(conn, instance_id=None, page=1, per_page=20, sort_by="completed_at", sort_order="desc"):
    ensure_backtest_instances_table(conn)
    tables = list(r.db(DB_NAME).table_list().run(conn))
    has_queue = "BacktestInstances" in tables
    has_results = "BacktestResults" in tables
    if not has_queue and not has_results:
        return {"backtests": [], "total": 0, "page": page, "per_page": per_page, "total_pages": 0}
    instance_filter = str(instance_id).strip() if instance_id is not None else None
    sort_by = (str(sort_by or "completed_at").strip().lower())
    if sort_by not in ("completed_at", "pnl", "pnl_percent"):
        sort_by = "completed_at"
    sort_order = "asc" if str(sort_order or "").strip().lower() == "asc" else "desc"

    queue_rows = []
    if has_queue:
        # 2026-04-26 perf: prefer the `instance` secondary index over .filter()
        # so per-instance list pages do an O(log N) range read instead of a
        # full-table scan. Falls back to .filter() if the index isn't ready
        # yet (e.g. first deploy after this commit, before index_wait succeeds).
        queue_query = None
        if instance_filter:
            try:
                _qidx = set(r.db(DB_NAME).table("BacktestInstances").index_list().run(conn))
            except Exception:
                _qidx = set()
            if "instance" in _qidx:
                queue_query = r.db(DB_NAME).table("BacktestInstances").get_all(instance_filter, index="instance")
            else:
                queue_query = r.db(DB_NAME).table("BacktestInstances").filter(
                    lambda row: row["instance"].default("").coerce_to("string") == instance_filter
                )
        else:
            queue_query = r.db(DB_NAME).table("BacktestInstances")
        queue_query = queue_query.pluck(
            "id",
            "instance",
            "stocks",
            "start-date",
            "end-date",
            "status",
            "paused",
            "created_at",
            "timestamp",
            "completed_at",
            "time_elapsed_seconds",
        )
        queue_rows = list(queue_query.run(conn))

    result_rows = []
    if has_results:
        # 2026-04-26 perf: same index-first pattern as the queue side.
        results_query = None
        if instance_filter:
            try:
                _ridx = set(r.db(DB_NAME).table("BacktestResults").index_list().run(conn))
            except Exception:
                _ridx = set()
            if "instance_or_instance_id" in _ridx:
                results_query = r.db(DB_NAME).table("BacktestResults").get_all(
                    instance_filter, index="instance_or_instance_id"
                )
            else:
                results_query = r.db(DB_NAME).table("BacktestResults").filter(
                    lambda row: row["instance_id"]
                    .default(row["instance"].default(""))
                    .coerce_to("string")
                    == instance_filter
                )
        else:
            results_query = r.db(DB_NAME).table("BacktestResults")
        # R22 (2026-04-25): list endpoint only reads ~15 summary fields per
        # row (id, status, pnl, pnl_percent, dates, time_elapsed, tickers).
        # Each BacktestResults row also carries backtest_decisions (7-15k
        # entries), backtest_prices (9-37k entries), backtest_trades, the
        # frozen strategy_schema (~20KB), per-symbol pnl dicts, and logs —
        # totalling 5-13MB per row. With 50+ backtests, list page used to
        # transfer >250MB. Pluck the needed fields server-side so the network
        # payload + Python deserialization stay bounded even if new heavy
        # fields are added later.
        # Detail / playback / graph endpoints fetch the full row separately.
        results_query = results_query.pluck(
            "id",
            "backtest_id",
            "instance_id",
            "instance",
            "tickers",
            "start_date",
            "end_date",
            "status",
            "progress",
            "pnl",
            "pnl_percent",
            "time_elapsed_seconds",
            "started_at",
            "created_at",
            "timestamp",
            "completed_at",
        )
        result_rows = list(results_query.run(conn))

    def _to_unix_ts(value):
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, dict):
            epoch = value.get("epoch_time")
            if epoch is not None:
                try:
                    return float(epoch)
                except (TypeError, ValueError):
                    return 0.0
            return 0.0
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return 0.0
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return float(dt.timestamp())
            except Exception:
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def _to_iso_string(value):
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            epoch = value.get("epoch_time")
            if epoch is not None:
                try:
                    return datetime.datetime.fromtimestamp(float(epoch), tz=datetime.timezone.utc).isoformat().replace("+00:00", "Z")
                except Exception:
                    return None
            return None
        if isinstance(value, (int, float)):
            try:
                return datetime.datetime.fromtimestamp(float(value), tz=datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            except Exception:
                return None
        return str(value)

    def _is_terminal_status(status):
        return str(status or "").strip().lower() in ("finished", "completed", "stopped", "failed", "error", "cancelled")

    def _normalize_status_for_display(status):
        """Collapse paused_llm_critical / paused_<anything> down to "paused"
        for UI consumption. Without this the listing keeps the raw DB
        status and the sort + filter logic in this function (and in the
        frontend) doesn't recognise the row as a paused backtest — so it
        sorts to the bottom of the page and falls off page-1 entirely
        when there are 100+ rows."""
        s = str(status or "").strip().lower()
        if s.startswith("paused"):
            return "paused"
        return s

    def _is_active_status(status):
        s = str(status or "").strip().lower()
        return s in ("running", "queued", "pending", "paused") or s.startswith("paused")

    def _to_seconds(value):
        try:
            if value is None:
                return None
            return max(0, int(float(value)))
        except (TypeError, ValueError):
            return None

    def _elapsed_for(status, queue_row=None, result_row=None):
        import time as _time

        stored = _to_seconds((result_row or {}).get("time_elapsed_seconds"))
        if stored is None:
            stored = _to_seconds((queue_row or {}).get("time_elapsed_seconds"))

        st = str(status or "").strip().lower()

        # For actively-running backtests, prefer live wall-clock elapsed time
        # computed from created_at, because the stored time_elapsed_seconds in
        # the DB only updates at the end of each broker loop iteration (which
        # can take many minutes for heavy strategies like Nexus with LLM calls).
        if st in ("running", "queued", "pending", "paused"):
            start_ts = _to_unix_ts(
                (result_row or {}).get("started_at")
                or (result_row or {}).get("created_at")
                or (queue_row or {}).get("created_at")
                or (queue_row or {}).get("timestamp")
            )
            if start_ts > 0:
                live_elapsed = max(0, int(_time.time() - start_ts))
                # Use whichever is larger: stored (last DB write) or live
                # (wall-clock).  This prevents the timer appearing to jump
                # backward if stored is slightly ahead due to broker-side
                # compute overhead not included in queue wait time.
                if stored is not None:
                    return max(stored, live_elapsed)
                return live_elapsed
            # No start timestamp — fall back to stored value
            if stored is not None:
                return stored
            return None

        # For finished/error/stopped backtests, use stored value (final)
        if stored is not None:
            return stored

        start_ts = _to_unix_ts(
            (result_row or {}).get("started_at")
            or (result_row or {}).get("created_at")
            or (queue_row or {}).get("created_at")
            or (queue_row or {}).get("timestamp")
        )
        end_ts = _to_unix_ts(
            (result_row or {}).get("completed_at")
            or (result_row or {}).get("timestamp")
        )

        if start_ts > 0 and end_ts > 0 and end_ts >= start_ts:
            return max(0, int(end_ts - start_ts))
        return None

    queue_rows_by_id = {}
    merged = {}
    for row in queue_rows:
        rid = row.get("id")
        if rid is None:
            continue
        queue_rows_by_id[rid] = row
        sort_ts = _to_unix_ts(row.get("created_at") or row.get("timestamp"))
        completed_at = _to_iso_string(row.get("completed_at"))
        completed_ts = _to_unix_ts(completed_at)
        merged[rid] = {
            "id": rid,
            "instance": row.get("instance"),
            "stocks": row.get("stocks") or [],
            "start_date": str(row.get("start-date", ""))[:10],
            "end_date": str(row.get("end-date", ""))[:10],
            "completed_at": completed_at,
            "status": str(row.get("status", "")),
            "progress": None,
            "pnl": None,
            "pnl_percent": None,
            "time_elapsed_seconds": _elapsed_for(row.get("status"), queue_row=row),
            "_sort_ts": sort_ts,
            "_completed_ts": completed_ts,
        }

    for row in result_rows:
        rid = row.get("backtest_id", row.get("id"))
        if rid is None:
            continue
        result_sort_ts = _to_unix_ts(
            row.get("started_at")
            or row.get("created_at")
            or row.get("timestamp")
            or row.get("completed_at")
        )
        current = merged.get(rid)
        if current is None:
            queue_row = queue_rows_by_id.get(rid)
            status = str(row.get("status") or "").strip() or ""
            if queue_row is not None and bool(queue_row.get("paused")) and status.lower() in ("running", "queued", "pending"):
                status = "paused"
            # Collapse paused_llm_critical / paused_<anything> → paused so
            # the listing's sort + the frontend's status switch both see
            # the row as a paused backtest (active rank, top of list).
            status = _normalize_status_for_display(status) if str(status).lower().startswith("paused") else status
            completed_at_raw = row.get("completed_at")
            if completed_at_raw is None and _is_terminal_status(status):
                completed_at_raw = row.get("timestamp")
            completed_at = _to_iso_string(completed_at_raw)
            completed_ts = _to_unix_ts(completed_at)
            current = {
                "id": rid,
                "instance": row.get("instance_id", row.get("instance")),
                "stocks": row.get("tickers") or [],
                "start_date": str(row.get("start_date", ""))[:10],
                "end_date": str(row.get("end_date", ""))[:10],
                "completed_at": completed_at,
                "status": status,
                "progress": None,
                "pnl": None,
                "pnl_percent": None,
                "time_elapsed_seconds": _elapsed_for(status, queue_row=queue_row, result_row=row),
                "_sort_ts": result_sort_ts,
                "_completed_ts": completed_ts,
            }
        else:
            current["_sort_ts"] = max(float(current.get("_sort_ts") or 0.0), result_sort_ts)
        queue_row = queue_rows_by_id.get(rid)
        status = str(row.get("status") or current.get("status") or "").strip() or ""
        if queue_row is not None and bool(queue_row.get("paused")) and status.lower() in ("running", "queued", "pending"):
            status = "paused"
        status = _normalize_status_for_display(status) if str(status).lower().startswith("paused") else status
        completed_at_raw = row.get("completed_at")
        if completed_at_raw is None and _is_terminal_status(status):
            completed_at_raw = row.get("timestamp")
        completed_at = _to_iso_string(completed_at_raw)
        completed_ts = _to_unix_ts(completed_at)
        current["status"] = status
        current["progress"] = row.get("progress")
        current["pnl"] = row.get("pnl")
        current["pnl_percent"] = row.get("pnl_percent")
        current["time_elapsed_seconds"] = _elapsed_for(status, queue_row=queue_row, result_row=row)
        current["completed_at"] = completed_at
        current["_completed_ts"] = max(float(current.get("_completed_ts") or 0.0), completed_ts)
        merged[rid] = current

    def _sort_key(item):
        def _num(value):
            try:
                if value is None:
                    return None
                return float(value)
            except (TypeError, ValueError):
                return None

        status = str(item.get("status") or "").strip().lower()
        # Active includes paused_llm_critical and any "paused_<tag>" variant
        # — without this an LLM-critical pause lands as active_rank=1 and
        # gets sorted to the bottom of the list, off page-1 entirely.
        is_active = _is_active_status(status)
        active_rank = 0 if is_active else 1

        if sort_by == "pnl":
            value = _num(item.get("pnl"))
        elif sort_by == "pnl_percent":
            value = _num(item.get("pnl_percent"))
        else:  # completed_at
            value = _num(item.get("_completed_ts"))
            if not value:
                value = None
            # Active backtests often have no completed_at; sort those by recent start/create time.
            if value is None and is_active:
                value = _num(item.get("_sort_ts"))

        missing_rank = 0 if (is_active and value is None) else (1 if value is None else 0)
        sortable_value = 0.0 if value is None else value
        rid = item.get("id")
        try:
            rid_sort = int(rid)
        except Exception:
            rid_sort = 0
        if sort_order == "asc":
            return (active_rank, missing_rank, sortable_value, rid_sort)
        return (active_rank, missing_rank, -sortable_value, -rid_sort)

    all_backtests = sorted(merged.values(), key=_sort_key)
    all_backtests = [{k: v for k, v in bt.items() if not str(k).startswith("_")} for bt in all_backtests]
    total = len(all_backtests)
    page = max(1, int(page))
    per_page = max(1, min(100, int(per_page)))
    offset = (page - 1) * per_page
    backtests = all_backtests[offset: offset + per_page]
    import math
    return {
        "backtests": backtests,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": math.ceil(total / per_page) if per_page else 1,
    }


def action_clear_instance_state(conn, instance_id: str, scope: str = "lookback_only",
                                 apply: bool = False) -> dict:
    """Dry-run / apply per-instance state wipe.

    Thin wrapper around ``clear_instance_state.execute``. ``apply=False``
    just counts what would be deleted; ``apply=True`` performs the
    deletes. Returns the same shape either way so the UI can render a
    "before" preview and an "after" confirmation with one code path.

    Raises ``ValueError`` on unknown scope (the API maps that to 400).
    """
    from clear_instance_state import execute as _execute
    if not (instance_id and str(instance_id).strip()):
        raise ValueError("instance_id is required")
    return _execute(
        conn,
        instance_id=str(instance_id).strip(),
        scope=str(scope or "lookback_only").strip(),
        apply=bool(apply),
    )


def action_create_backtest(
    conn,
    instance_id,
    stocks,
    start_date,
    end_date,
    granularity_sec=60,
    key=None,
    secret=None,
    initial_cash=100000.0,
):
    if not start_date or not end_date:
        raise ValueError("start_date, end_date required")  # V7.3: stocks can be empty for pure discovery
    ensure_backtest_instances_table(conn)
    instance_doc = _resolve_instance_doc(conn, instance_id) if instance_id else None
    default_key = (instance_doc.get("key") or "") if instance_doc else ""
    default_secret = (instance_doc.get("secret") or "") if instance_doc else ""
    stocks_list = [s.strip().upper() for s in (stocks if isinstance(stocks, list) else [stocks]) if s and str(s).strip()]
    # V7.3: stocks_list can be empty for pure discovery mode (Nexus discovers its own tickers)
    gran_sec = int(granularity_sec) if granularity_sec is not None else 60
    if initial_cash is None or float(initial_cash) <= 0:
        initial_cash = 100000.0
    key_val = (key if key is not None else default_key) or ""
    secret_val = (secret if secret is not None else default_secret) or ""
    if not key_val:
        key_val = (os.environ.get("APCA_API_KEY_ID") or os.environ.get("KEY") or "").strip()
    if not secret_val:
        secret_val = (os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("SECRET") or "").strip()
    doc = {
        "stocks": stocks_list,
        "start-date": str(start_date).strip()[:10],
        "end-date": str(end_date).strip()[:10],
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "granularity_sec": gran_sec,
        "status": "pending",
        "run": True,
        "paused": False,
        "key": key_val,
        "secret": secret_val,
        "initial_cash": float(initial_cash),
    }
    if instance_id:
        doc["instance"] = str(instance_id).strip()
    backtest_id = insert_backtest_with_unique_id(conn, doc)
    # Post "Queued" to #backtests so the same message can be edited with progress/P&L by broker/engine
    try:
        tickers_str = ", ".join((doc["stocks"] or [])[:8])
        if doc.get("stocks") and len(doc["stocks"]) > 8:
            tickers_str += " (+%d)" % (len(doc["stocks"]) - 8)
        period_str = "%s → %s" % (doc.get("start-date") or "—", doc.get("end-date") or "—")
        difficulty = get_instance_avg_difficulty(conn, doc.get("instance"))
        high_usage = get_instance_high_usage(conn, doc.get("instance"))
        diff_val = "%.1f" % difficulty
        if high_usage:
            diff_val += " (HIGH USAGE)"
        action_enqueue_discord_message(
            conn, "backtests", content=None,
            embed={
                "title": "Backtest Queued",
                "description": "A backtest was added to the queue.",
                "color": 0x3498DB,
                "fields": [
                    {"name": "ID", "value": str(backtest_id), "inline": True},
                    {"name": "Instance", "value": str(doc["instance"]), "inline": True},
                    {"name": "Status", "value": "Queued", "inline": True},
                    {"name": "Difficulty", "value": diff_val, "inline": True},
                    {"name": "Period", "value": period_str, "inline": False},
                    {"name": "Tickers", "value": tickers_str or "—", "inline": False},
                ],
            },
            message_key=str(backtest_id),
        )
    except Exception:
        pass
    return {
        "id": backtest_id,
        "instance": doc["instance"],
        "stocks": doc["stocks"],
        "start_date": doc["start-date"],
        "end_date": doc["end-date"],
    }


def action_delete_backtest(conn, backtest_id):
    try:
        bid = int(backtest_id)
    except (TypeError, ValueError):
        raise ValueError("Backtest ID must be an integer")
    tables = list(r.db(DB_NAME).table_list().run(conn))
    found = False
    if "BacktestInstances" in tables:
        doc = r.db(DB_NAME).table("BacktestInstances").get(bid).run(conn)
        if doc is not None:
            r.db(DB_NAME).table("BacktestInstances").get(bid).delete().run(conn)
            found = True
    if "BacktestResults" in tables:
        result_doc = r.db(DB_NAME).table("BacktestResults").get(bid).run(conn)
        if result_doc is not None:
            r.db(DB_NAME).table("BacktestResults").get(bid).delete().run(conn)
            found = True
    if not found:
        raise ValueError("Backtest not found: %s" % bid)
    return {"deleted": True, "id": bid}


def action_stop_backtest(conn, backtest_id):
    try:
        bid = int(backtest_id)
    except (TypeError, ValueError):
        raise ValueError("Backtest ID must be an integer")
    ensure_backtest_instances_table(conn)
    doc = r.db(DB_NAME).table("BacktestInstances").get(bid).run(conn)
    if doc is None:
        raise ValueError("Backtest not found: %s" % bid)
    if (doc.get("status") or "").strip().lower() != "running":
        raise ValueError("Backtest is not running (status=%s). Use delete to remove from queue." % doc.get("status"))
    r.db(DB_NAME).table("BacktestInstances").get(bid).update({"run": False}).run(conn)
    # Mark BacktestResults as stopped so UI doesn't show stale "running" state
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "BacktestResults" in tables:
        try:
            r.db(DB_NAME).table("BacktestResults").get(bid).update({"status": "stopped"}).run(conn)
        except Exception:
            pass
    return {"stop_requested": True, "id": bid}


def _stop_all_backtest_containers():
    """Stop and remove all Docker containers whose name contains 'backtest-instance' (running or orphaned). Returns count."""
    try:
        import docker
        client = docker.from_env()
        stopped = 0
        for c in client.containers.list(all=True):
            name = (c.name or "")
            if "backtest-instance" in name:
                try:
                    c.remove(force=True)
                    stopped += 1
                except Exception:
                    pass
        return stopped
    except Exception:
        return 0


def action_stop_all_backtests(conn):
    """Stop all backtests: set run=false for every row (so running brokers exit), delete all rows, and stop any backtest Docker containers (no orphans)."""
    ensure_backtest_instances_table(conn)
    rows = list(r.db(DB_NAME).table("BacktestInstances").run(conn))
    ids = [row["id"] for row in rows]
    for bid in ids:
        try:
            r.db(DB_NAME).table("BacktestInstances").get(bid).update({"run": False}).run(conn)
        except Exception:
            pass
    for bid in ids:
        try:
            r.db(DB_NAME).table("BacktestInstances").get(bid).delete().run(conn)
        except Exception:
            pass
    containers_stopped = _stop_all_backtest_containers()
    # Mark any stale running BacktestResults as stopped so the UI reflects reality
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "BacktestResults" in tables:
        try:
            r.db(DB_NAME).table("BacktestResults").filter(
                r.row["status"].eq("running")
            ).update({"status": "stopped"}).run(conn)
        except Exception:
            pass
    return {"stopped": len(ids), "ids": ids, "containers_stopped": containers_stopped}


def action_pause_backtest(conn, backtest_id):
    """Set paused=true on the backtest row so the broker stops advancing and waits for resume."""
    try:
        bid = int(backtest_id)
    except (TypeError, ValueError):
        raise ValueError("Backtest ID must be an integer")
    ensure_backtest_instances_table(conn)
    doc = r.db(DB_NAME).table("BacktestInstances").get(bid).run(conn)
    if doc is None:
        raise ValueError("Backtest not found: %s" % bid)
    r.db(DB_NAME).table("BacktestInstances").get(bid).update({"paused": True}).run(conn)
    return {"paused": True, "id": bid}


def action_resume_backtest(conn, backtest_id):
    """Set paused=false on the backtest row so the broker continues."""
    try:
        bid = int(backtest_id)
    except (TypeError, ValueError):
        raise ValueError("Backtest ID must be an integer")
    ensure_backtest_instances_table(conn)
    doc = r.db(DB_NAME).table("BacktestInstances").get(bid).run(conn)
    if doc is None:
        raise ValueError("Backtest not found: %s" % bid)
    r.db(DB_NAME).table("BacktestInstances").get(bid).update({"paused": False}).run(conn)
    return {"paused": False, "id": bid}


def action_get_backtest_status(conn, backtest_id):
    """Get status and progress for a backtest. Prefer BacktestResults (authoritative); fallback to BacktestInstances (queue)."""
    try:
        bid = int(backtest_id)
    except (TypeError, ValueError):
        raise ValueError("Backtest ID must be an integer")
    tables = list(r.db(DB_NAME).table_list().run(conn))
    queue_doc = None
    if "BacktestInstances" in tables:
        try:
            queue_doc = r.db(DB_NAME).table("BacktestInstances").get(bid).run(conn)
        except Exception:
            queue_doc = None

    def _to_unix_ts(value):
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, dict):
            epoch = value.get("epoch_time")
            if epoch is not None:
                try:
                    return float(epoch)
                except (TypeError, ValueError):
                    return 0.0
            return 0.0
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return 0.0
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return float(dt.timestamp())
            except Exception:
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def _to_seconds(value):
        try:
            if value is None:
                return None
            return max(0, int(float(value)))
        except (TypeError, ValueError):
            return None

    def _elapsed_for(status, queue_row=None, result_row=None):
        import time as _time

        stored = _to_seconds((result_row or {}).get("time_elapsed_seconds"))
        if stored is None:
            stored = _to_seconds((queue_row or {}).get("time_elapsed_seconds"))

        st = str(status or "").strip().lower()

        # For actively-running backtests, prefer live wall-clock elapsed time
        # computed from created_at, because the stored time_elapsed_seconds in
        # the DB only updates at the end of each broker loop iteration (which
        # can take many minutes for heavy strategies like Nexus with LLM calls).
        if st in ("running", "queued", "pending", "paused"):
            start_ts = _to_unix_ts(
                (result_row or {}).get("started_at")
                or (result_row or {}).get("created_at")
                or (queue_row or {}).get("created_at")
                or (queue_row or {}).get("timestamp")
            )
            if start_ts > 0:
                live_elapsed = max(0, int(_time.time() - start_ts))
                # Use whichever is larger: stored (last DB write) or live
                # (wall-clock).  This prevents the timer appearing to jump
                # backward if stored is slightly ahead due to broker-side
                # compute overhead not included in queue wait time.
                if stored is not None:
                    return max(stored, live_elapsed)
                return live_elapsed
            # No start timestamp — fall back to stored value
            if stored is not None:
                return stored
            return None

        # For finished/error/stopped backtests, use stored value (final)
        if stored is not None:
            return stored

        start_ts = _to_unix_ts(
            (result_row or {}).get("started_at")
            or (result_row or {}).get("created_at")
            or (queue_row or {}).get("created_at")
            or (queue_row or {}).get("timestamp")
        )
        end_ts = _to_unix_ts(
            (result_row or {}).get("completed_at")
            or (result_row or {}).get("timestamp")
        )

        if start_ts > 0 and end_ts > 0 and end_ts >= start_ts:
            return max(0, int(end_ts - start_ts))
        return None
    # Prefer BacktestResults (written by broker when backtest runs)
    if "BacktestResults" in tables:
        result_doc = r.db(DB_NAME).table("BacktestResults").get(bid).run(conn)
        if result_doc is not None:
            status = str(result_doc.get("status") or "running").strip() or "running"
            # If the queue row is paused, reflect that state in the API for UI controls.
            if queue_doc is not None and bool(queue_doc.get("paused")) and status.lower() in ("running", "queued", "pending"):
                status = "paused"
            elapsed = _elapsed_for(status, queue_row=queue_doc, result_row=result_doc)
            resp = {
                "id": bid,
                "status": status,
                "progress": result_doc.get("progress"),
                "time_elapsed_seconds": elapsed,
                "nexus_lookback": result_doc.get("nexus_lookback"),
            }
            # Include _last_active so the UI can show how stale the progress
            # data is (broker only writes progress at end of each loop iteration).
            last_active = result_doc.get("_last_active")
            if last_active is not None:
                resp["_last_active"] = str(last_active)
            return resp
    # Fallback: still in queue (BacktestInstances)
    ensure_backtest_instances_table(conn)
    if queue_doc is None:
        queue_doc = r.db(DB_NAME).table("BacktestInstances").get(bid).run(conn)
    if queue_doc is not None:
        status = (queue_doc.get("status") or "pending").strip() or "pending"
        if bool(queue_doc.get("paused")) and status.lower() in ("running", "queued", "pending"):
            status = "paused"
        elapsed = _elapsed_for(status, queue_row=queue_doc, result_row=None)
        return {
            "id": bid,
            "status": status,
            "progress": None,
            "time_elapsed_seconds": elapsed,
        }
    raise ValueError("Backtest not found: %s" % bid)


def action_summarize_backtest(conn, backtest_id):
    try:
        bid = int(backtest_id)
    except (TypeError, ValueError):
        raise ValueError("Backtest ID must be an integer")
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "BacktestResults" not in tables:
        raise ValueError("BacktestResults table not found")
    doc = r.db(DB_NAME).table("BacktestResults").get(bid).run(conn)
    if doc is None:
        raise ValueError("Backtest result not found: %s" % bid)

    def _to_unix_ts(value):
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, dict):
            epoch = value.get("epoch_time")
            if epoch is not None:
                try:
                    return float(epoch)
                except (TypeError, ValueError):
                    return 0.0
            return 0.0
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return 0.0
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return float(dt.timestamp())
            except Exception:
                return 0.0
        return 0.0

    time_elapsed_seconds = doc.get("time_elapsed_seconds")
    try:
        if time_elapsed_seconds is not None:
            time_elapsed_seconds = max(0, int(float(time_elapsed_seconds)))
    except (TypeError, ValueError):
        time_elapsed_seconds = None
    if time_elapsed_seconds is None:
        import time as _time
        status = str(doc.get("status") or "").strip().lower()
        start_ts = _to_unix_ts(doc.get("started_at") or doc.get("created_at") or doc.get("start_date"))
        end_ts = _to_unix_ts(doc.get("completed_at") or doc.get("timestamp"))
        if status in ("running", "queued", "pending", "paused"):
            if start_ts > 0:
                time_elapsed_seconds = max(0, int(_time.time() - start_ts))
        elif start_ts > 0 and end_ts > 0 and end_ts >= start_ts:
            time_elapsed_seconds = max(0, int(end_ts - start_ts))

    # Pull rerun fields from BacktestInstances
    queue_doc = None
    if "BacktestInstances" in tables:
        queue_doc = r.db(DB_NAME).table("BacktestInstances").get(bid).run(conn)

    backtest_trades = doc.get("backtest_trades") or []
    total_trades, total_buys, total_sells, cycle_pnls, winning, losing, breakeven = round_trip_stats(
        backtest_trades
    )
    portfolio_value_history = doc.get("portfolio_value_history") or []
    start_val = portfolio_value_history[0].get("value") if portfolio_value_history else None
    end_val = portfolio_value_history[-1].get("value") if len(portfolio_value_history) > 1 else start_val
    return {
        "id": bid,
        "status": doc.get("status"),
        "instance_id": (queue_doc.get("instance") if queue_doc else None) or doc.get("instance_id"),
        "granularity": (queue_doc.get("granularity_sec") if queue_doc else None) or doc.get("granularity_sec"),
        "initial_cash": (queue_doc.get("initial_cash") if queue_doc else None) or doc.get("initial_cash"),
        "start_date": doc.get("start_date"),
        "end_date": doc.get("end_date"),
        "tickers": doc.get("tickers") or [],
        "pnl": doc.get("pnl"),
        "pnl_percent": doc.get("pnl_percent"),
        "pnl_per_stock": doc.get("pnl_per_stock") or {},
        "pnl_percent_per_stock": doc.get("pnl_percent_per_stock") or {},
        "stock_price_change": doc.get("stock_price_change") or {},
        "time_elapsed_seconds": time_elapsed_seconds,
        "portfolio_start_value": start_val,
        "portfolio_end_value": end_val,
        "total_trades": total_trades,
        "total_buys": total_buys,
        "total_sells": total_sells,
        "round_trips": len(cycle_pnls),
        "winning_round_trips": winning,
        "losing_round_trips": losing,
        "breakeven_round_trips": breakeven,
        "win_rate_percent": (winning / len(cycle_pnls) * 100.0) if cycle_pnls else 0,
        "avg_winning_round_trip": sum(p for p in cycle_pnls if p > 0) / winning if winning else None,
        "avg_losing_round_trip": sum(p for p in cycle_pnls if p < 0) / losing if losing else None,
        "total_round_trip_pnl": sum(cycle_pnls) if cycle_pnls else None,
        "portfolio_value_high": max(float(h.get("value") or 0) for h in portfolio_value_history) if portfolio_value_history else None,
        "portfolio_value_low": min(float(h.get("value") or 0) for h in portfolio_value_history) if portfolio_value_history else None,
        "strategy_schema": doc.get("strategy_schema"),
        "strategy_id": doc.get("strategy_id"),
        "pause_reason_tag":   doc.get("pause_reason_tag"),
        "pause_reason_text":  doc.get("pause_reason_text"),
        "pause_provider":     doc.get("pause_provider"),
        "pause_model":        doc.get("pause_model"),
        "pause_call_site":    doc.get("pause_call_site"),
        "pause_attempts":     doc.get("pause_attempts"),
        "pause_bar_time":     doc.get("pause_bar_time"),
        "pause_sample":       doc.get("pause_sample"),
        "paused_at":          doc.get("paused_at"),
        "resumed_at":         doc.get("resumed_at"),
    }


def action_backtest_logs(conn, backtest_id):
    import os as _os
    try:
        bid = int(backtest_id)
    except (TypeError, ValueError):
        raise ValueError("Backtest ID must be an integer")
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "BacktestResults" not in tables:
        raise ValueError("BacktestResults table not found")
    doc = r.db(DB_NAME).table("BacktestResults").get(bid).run(conn)
    if doc is None:
        raise ValueError("Backtest result not found: %s" % bid)

    # Try to read from persistent log file on the shared volume (full, unlimited log)
    log_dir = _os.environ.get("BACKTEST_LOG_DIR", "/app/backtest_logs")
    log_file_path = _os.path.join(log_dir, f"{bid}.log")
    if _os.path.isfile(log_file_path):
        try:
            with open(log_file_path, "r", encoding="utf-8", errors="replace") as f:
                logs = [line.rstrip("\n") for line in f if line.strip()]
            return {
                "id": bid,
                "status": doc.get("status"),
                "error": doc.get("error"),
                "source": "file",
                "logs": logs,
            }
        except Exception:
            pass

    # Fall back to RethinkDB logs array (capped at 500 lines for older backtests)
    logs = doc.get("logs") or []
    if not isinstance(logs, list):
        logs = []
    return {
        "id": bid,
        "status": doc.get("status"),
        "error": doc.get("error"),
        "source": "db",
        "logs": logs,
    }


def action_list_nexus_graph_builds(conn, limit=50):
    """List recent nexus graph builds (newest first), minimal summary fields."""
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "NexusGraphBuilds" not in tables:
        return {"builds": []}
    rows = list(
        r.db(DB_NAME).table("NexusGraphBuilds")
        .order_by(r.desc("started_at"))
        .limit(int(limit) if limit else 50)
        .run(conn)
    )
    out = []
    for row in rows:
        totals = row.get("totals") or {}
        phases = row.get("phases_run") or []
        phase_summary = []
        for p in phases:
            phase_summary.append({
                "phase_key": p.get("phase_key"),
                "label": p.get("label"),
                "duration_sec": p.get("duration_sec"),
                "status": p.get("status"),
                "nodes_delta": p.get("nodes_delta"),
                "edges_delta": p.get("edges_delta"),
                "work_units": p.get("work_units"),
            })
        out.append({
            "id": row.get("id"),
            "started_at": row.get("started_at_iso"),
            "completed_at": row.get("completed_at_iso"),
            "duration_seconds": row.get("duration_seconds"),
            "mode": row.get("mode"),
            "final_status": row.get("final_status"),
            "selected_phases": row.get("selected_phases"),
            "new_nodes": totals.get("new_nodes"),
            "new_edges": totals.get("new_edges"),
            "nodes_after": totals.get("nodes_after"),
            "edges_after": totals.get("edges_after"),
            "phase_count": len(phases),
            "phases": phase_summary,
            "warnings": row.get("warnings") or [],
            "log_file_path": row.get("log_file_path"),
        })
    return {"builds": out}


def _resolve_latest_nexus_graph_build_id(conn):
    """Return the id of the most recent build (by started_at), or None if none exist."""
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "NexusGraphBuilds" not in tables:
        return None
    try:
        latest = list(
            r.db(DB_NAME).table("NexusGraphBuilds")
            .order_by(r.desc("started_at"))
            .limit(1)
            .run(conn)
        )
    except Exception:
        latest = []
    return (latest[0].get("id") if latest else None) or None


def action_get_nexus_graph_build(conn, build_id):
    """Return full build document including phases_run and log_tail.

    build_id="latest" resolves to the most recently started build.
    """
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "NexusGraphBuilds" not in tables:
        raise ValueError("NexusGraphBuilds table not found")
    if str(build_id).lower() == "latest":
        resolved = _resolve_latest_nexus_graph_build_id(conn)
        if not resolved:
            raise ValueError("No nexus graph builds found")
        build_id = resolved
    doc = r.db(DB_NAME).table("NexusGraphBuilds").get(str(build_id)).run(conn)
    if doc is None:
        raise ValueError("Nexus graph build not found: %s" % build_id)
    return doc


class NexusBuildNotFoundError(LookupError):
    """Raised when no nexus graph build can be resolved (maps to 404 at the API boundary)."""


# Max lines per /logs response. Protects worker memory + network on large builds.
# Client is expected to poll again immediately (no cadence wait) when truncated=True.
_NEXUS_LOGS_MAX_LINES_PER_RESPONSE = int(
    __import__("os").environ.get("NEXUS_LOGS_MAX_LINES_PER_RESPONSE", "5000")
)


def action_nexus_graph_build_logs(conn, build_id, since_line=0):
    """Read log file for a nexus graph build; fall back to log_tail in DB.

    build_id="latest" resolves to the most recently started build. Raises
    NexusBuildNotFoundError if no build exists or the id is unknown — the
    API layer turns that into a 404 so the UI can distinguish "no builds
    yet" from a real backend failure (which surfaces as 5xx).

    since_line: return only lines with 0-based index >= since_line. Default 0
    returns the full log (back-compat). Used by the UI's live-tail poll loop
    to fetch deltas instead of re-shipping the whole file.

    Returns a dict with:
      id            — build id
      final_status  — "running" | "completed" | "failed" | None
      source        — "file" (log dir) or "db" (log_tail fallback, capped 500)
      logs          — slice of lines starting at max(since_line, 0)
      next_line     — cursor client should echo on next poll (monotonic per build)
      total_lines   — total lines currently readable from source
      truncated     — True if the response was capped; client should poll again
                      immediately to catch up, no cadence wait
    """
    import os as _os
    try:
        since_line = max(0, int(since_line))
    except (TypeError, ValueError):
        since_line = 0
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "NexusGraphBuilds" not in tables:
        raise NexusBuildNotFoundError("NexusGraphBuilds table not found")
    if str(build_id).lower() == "latest":
        resolved = _resolve_latest_nexus_graph_build_id(conn)
        if not resolved:
            raise NexusBuildNotFoundError("No nexus graph builds found")
        build_id = resolved
    doc = r.db(DB_NAME).table("NexusGraphBuilds").get(str(build_id)).run(conn)
    if doc is None:
        raise NexusBuildNotFoundError("Nexus graph build not found: %s" % build_id)

    final_status = doc.get("final_status")
    log_dir = _os.environ.get("NEXUS_GRAPH_LOG_DIR", "/app/nexus_graph_logs")
    log_file_path = doc.get("log_file_path") or _os.path.join(log_dir, f"build_{build_id}.log")

    all_lines = None
    source = None
    if log_file_path and _os.path.isfile(log_file_path):
        try:
            with open(log_file_path, "r", encoding="utf-8", errors="replace") as f:
                # Only include COMPLETE lines (those ending in \n) so a writer
                # flushing mid-line doesn't ship a partial line that would be
                # superseded on the next poll at the same index. The final
                # unterminated line is deferred until it finishes.
                raw = f.read()
            file_lines = raw.splitlines(keepends=True)
            complete_stripped = []
            for line in file_lines:
                if not line.endswith("\n"):
                    continue  # partial trailing write — defer until next poll
                s = line.rstrip("\n")
                if s.strip():
                    complete_stripped.append(s)
            all_lines = complete_stripped
            source = "file"
        except Exception:
            all_lines = None

    if all_lines is None:
        # Fall back to log_tail (last 500 lines) stored in the row.
        tail = doc.get("log_tail") or []
        if not isinstance(tail, list):
            tail = []
        all_lines = list(tail)
        source = "db"

    total = len(all_lines)
    # Guard since_line > total (e.g. log file rotated, truncated, or source
    # swapped from db→file between polls — in all cases we want to return
    # zero lines, not wrap around to the end).
    start = since_line if since_line <= total else total
    sliced = all_lines[start:]

    # Cap response to keep payloads small and amortize first-load cost across
    # multiple polls. Client re-polls immediately when truncated=True.
    truncated = False
    if len(sliced) > _NEXUS_LOGS_MAX_LINES_PER_RESPONSE:
        sliced = sliced[:_NEXUS_LOGS_MAX_LINES_PER_RESPONSE]
        next_line = start + _NEXUS_LOGS_MAX_LINES_PER_RESPONSE
        truncated = True
    else:
        # Monotonic floor: never echo a cursor smaller than what the client
        # already sent. Protects against cursor "retreat" when the file
        # rotates, truncates, or the source swaps db→file mid-stream.
        next_line = max(since_line, total)

    return {
        "id": build_id,
        "final_status": final_status,
        "source": source,
        "logs": sliced,
        "next_line": next_line,
        "total_lines": total,
        "truncated": truncated,
    }


# ── Live trading: state read + command submit + log tail ────────────────────
# All actions here raise NexusBuildNotFoundError / ValueError. Mapped to 404
# / 400 by _run in api/main.py. Destructive commands (halt / close_position /
# submit_order) are admin-gated at the HTTP layer, not here.


class LiveInstanceNotFoundError(LookupError):
    """Raised when the instance has no LiveState row (never booted or broker
    already torn down). API layer returns 404; UI shows a 'not running'
    empty state."""


_LIVE_LOGS_MAX_LINES_PER_RESPONSE = int(os.environ.get("LIVE_LOGS_MAX_LINES_PER_RESPONSE", "5000"))


def action_get_live_state(conn, instance_id):
    """Return live state for this instance — broker-direct + LiveState merge.

    Pulls cash/equity/positions/recent_trades/portfolio_history live from the
    broker (Alpaca / Robinhood). The container-written LiveState row is now
    informational only — used for ``status`` / ``trading_active`` / ``lookback``
    / ``log_file_path`` / ``uptime_sec`` and as a fallback when the broker
    fetch fails.
    """
    import live_state as _ls_mod
    from live_broker_fetch import fetch_broker_live_state
    _inst = _resolve_instance_doc(conn, instance_id) if instance_id else None
    if _inst is None:
        raise ValueError("Instance not found: %s" % instance_id)
    real_id = str(_inst.get("id", instance_id))

    # Broker-direct fetch (never raises).
    try:
        broker_state = fetch_broker_live_state(conn, real_id)
    except Exception as _bf_e:
        broker_state = {
            "broker_fetch_error": "broker_fetch_unhandled: %s: %s" % (type(_bf_e).__name__, _bf_e),
            "broker_fetched_at_iso": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "broker_fetch_age_seconds": 0,
        }

    # Container-written LiveState row (may be None if container never booted).
    ls_row = _ls_mod.get_live_state(r, conn, real_id)
    container_seconds_since_update = None
    container_stale = False
    if isinstance(ls_row, dict):
        try:
            iso = ls_row.get("last_updated_iso")
            if iso:
                from datetime import datetime as _dt, timezone as _tz
                then = _dt.fromisoformat(iso.replace("Z", "+00:00"))
                if then.tzinfo is None:
                    then = then.replace(tzinfo=_tz.utc)
                container_seconds_since_update = int(
                    max(0, (_dt.now(_tz.utc) - then).total_seconds())
                )
                container_stale = container_seconds_since_update > 30
        except Exception:
            container_seconds_since_update = None
            container_stale = False

    broker_fetch_ok = not broker_state.get("broker_fetch_error")

    # Legacy contract: if neither path produced state, surface 404.
    if not broker_fetch_ok and not isinstance(ls_row, dict):
        raise LiveInstanceNotFoundError(
            "No live state for instance %s — broker fetch failed (%s) and "
            "container has never written a state row." %
            (real_id, broker_state.get("broker_fetch_error") or "unknown")
        )

    # Merge: start from broker fetch (authoritative), fall back to container row
    # for fields broker fetch couldn't produce.
    merged: dict = {}
    if broker_fetch_ok:
        merged.update(broker_state)
    if isinstance(ls_row, dict):
        # Only fill in fields we don't already have from broker fetch.
        for k, v in ls_row.items():
            if k in ("id", "last_updated", "last_updated_iso"):
                merged.setdefault(k, v)
                continue
            if not broker_fetch_ok or merged.get(k) in (None, [], {}):
                merged.setdefault(k, v)
        # Always carry these informational fields from the container row.
        for k in ("lookback", "log_file_path", "uptime_sec"):
            if ls_row.get(k) is not None:
                merged[k] = ls_row.get(k)
    # Always include broker_fetch_* metadata even if we merged onto ls_row.
    for k in ("broker_fetched_at_iso", "broker_fetch_error", "broker_fetch_age_seconds"):
        if k in broker_state:
            merged[k] = broker_state[k]

    # Status / trading_active: container row is the source of truth for whether
    # the broker loop is alive. Default to "halted" / False when row missing.
    if isinstance(ls_row, dict):
        if ls_row.get("status"):
            merged["status"] = ls_row["status"]
        elif broker_fetch_ok:
            merged["status"] = "running"
        else:
            merged["status"] = "halted"
        ls_active = bool(ls_row.get("trading_active", False))
    else:
        merged["status"] = "running" if broker_fetch_ok else "halted"
        ls_active = False
    merged["trading_active"] = bool(ls_active and broker_fetch_ok)

    # Container-staleness fields kept for diagnostic UI surfaces.
    merged["container_seconds_since_update"] = container_seconds_since_update
    merged["container_stale"] = container_stale

    # Redefined `stale`: the broker fetch itself is broken (NOT the container).
    merged["stale"] = bool(broker_state.get("broker_fetch_error"))
    merged["seconds_since_update"] = 0 if broker_fetch_ok else container_seconds_since_update

    return merged


def action_submit_live_command(conn, instance_id, command_type, payload, submitted_by=None):
    """Submit a command (halt / close_position / submit_order) to the broker
    for this instance. Returns {command_id}. The broker's command worker
    picks it up within ~1s and writes the result back to LiveCommands."""
    import live_state as _ls_mod
    inst = _resolve_instance_doc(conn, instance_id) if instance_id else None
    if inst is None:
        raise ValueError("Instance not found: %s" % instance_id)
    real_id = str(inst.get("id", instance_id))
    if not inst.get("runCommand", False):
        if command_type == "halt":
            # User explicitly asked to halt an already-stopped instance.
            # Return a synthetic completed command so the UI toast resolves.
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            cmd_id = _ls_mod.submit_command(
                r, conn,
                instance_id=real_id, type="halt",
                payload=dict(payload or {}),
                submitted_by=submitted_by,
            )
            _ls_mod.complete_command(r, conn, cmd_id, result={"already_halted": True, "noted_at": now_iso})
            return {"command_id": cmd_id, "status": "completed", "result": {"already_halted": True}}
        raise ValueError("Instance %s is not running; cannot %s" % (real_id, command_type))
    try:
        cmd_id = _ls_mod.submit_command(
            r, conn,
            instance_id=real_id,
            type=command_type,
            payload=payload or {},
            submitted_by=submitted_by,
        )
    except ValueError:
        raise
    except Exception as e:
        raise ValueError("submit_command failed: %s" % e)
    return {"command_id": cmd_id, "status": "pending"}


def action_get_live_command(conn, command_id):
    """Return the LiveCommands row (status + result) for a submitted command."""
    import live_state as _ls_mod
    doc = _ls_mod.get_command(r, conn, command_id)
    if doc is None:
        raise LiveInstanceNotFoundError("Live command not found: %s" % command_id)
    return doc


def action_live_trading_logs(conn, instance_id, since_line=0):
    """Live-tail of this instance's broker log file.

    Contract mirrors action_nexus_graph_build_logs exactly so the frontend
    can reuse the same cursor/reseed logic: returns {id, logs, next_line,
    total_lines, source, truncated, final_status}.
    """
    import live_state as _ls_mod
    try:
        since_line = max(0, int(since_line))
    except (TypeError, ValueError):
        since_line = 0
    inst = _resolve_instance_doc(conn, instance_id) if instance_id else None
    if inst is None:
        raise ValueError("Instance not found: %s" % instance_id)
    real_id = str(inst.get("id", instance_id))
    state = _ls_mod.get_live_state(r, conn, real_id)
    running = bool(inst.get("runCommand", False)) and state is not None
    # Try the log file first. Gracefully fall back to a DB log_tail if the
    # state row is present and carries one (live_state doesn't write log_tail
    # today — kept as a forward-compat hook).
    all_lines = None
    source = None
    log_dir = os.environ.get("LIVE_TRADING_LOG_DIR", "/app/live_trading_logs")
    # Reuse the state's log_file_path if available (accommodates custom dirs).
    path = None
    if isinstance(state, dict):
        path = state.get("log_file_path")
    if not path:
        path = _ls_mod.log_file_path_for(real_id, log_dir)
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
            file_lines = raw.splitlines(keepends=True)
            complete_stripped = []
            for line in file_lines:
                if not line.endswith("\n"):
                    continue
                s = line.rstrip("\n")
                if s.strip():
                    complete_stripped.append(s)
            all_lines = complete_stripped
            source = "file"
        except Exception:
            all_lines = None
    if all_lines is None:
        tail = (state or {}).get("log_tail") or []
        if not isinstance(tail, list):
            tail = []
        all_lines = list(tail)
        source = "db"

    total = len(all_lines)
    start = since_line if since_line <= total else total
    sliced = all_lines[start:]
    truncated = False
    if len(sliced) > _LIVE_LOGS_MAX_LINES_PER_RESPONSE:
        sliced = sliced[:_LIVE_LOGS_MAX_LINES_PER_RESPONSE]
        next_line = start + _LIVE_LOGS_MAX_LINES_PER_RESPONSE
        truncated = True
    else:
        next_line = max(since_line, total)

    # final_status mirrors the nexus endpoint's shape: "running" while alive,
    # "halted" once the state row is gone. The UI keys off this to decide
    # poll cadence (running = 5s, else = 15s).
    final_status = "running" if running else "halted"

    return {
        "id": real_id,
        "final_status": final_status,
        "source": source,
        "logs": sliced,
        "next_line": next_line,
        "total_lines": total,
        "truncated": truncated,
    }


def action_backtest_best_per_strategy(conn):
    """Return best backtest result (by pnl) per strategy_id, joining BacktestResults with Instances.
    Used by StrategiesView to show best backtest even for strategies with no AIBacktestingResults."""
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "BacktestResults" not in tables or "Instances" not in tables:
        return {"by_strategy": {}}
    # Build instance_id -> strategy_id map
    instances = list(r.db(DB_NAME).table("Instances").pluck("id", "strategy_id").run(conn))
    inst_to_strat = {}
    for inst in instances:
        iid = str(inst.get("id", ""))
        sid = inst.get("strategy_id")
        if iid and sid is not None:
            inst_to_strat[iid] = sid
    # Fetch all completed BacktestResults (skip running/queued/pending)
    skip_statuses = {"running", "queued", "pending"}
    results = list(
        r.db(DB_NAME).table("BacktestResults")
        .pluck("id", "instance_id", "pnl", "pnl_percent", "status")
        .run(conn)
    )
    best_by_strat = {}
    for row in results:
        if (row.get("status") or "").lower() in skip_statuses:
            continue
        inst_id = str(row.get("instance_id") or "")
        strat_id = inst_to_strat.get(inst_id)
        if strat_id is None:
            continue
        pnl = float(row.get("pnl") or 0)
        pct = float(row.get("pnl_percent") or 0)
        bid = row.get("id")
        existing = best_by_strat.get(strat_id)
        if existing is None or pnl > existing["best_pnl"]:
            best_by_strat[strat_id] = {"best_pnl": pnl, "best_pct": pct, "backtest_id": bid}
    return {"by_strategy": {str(k): v for k, v in best_by_strat.items()}}


def action_graph_backtest_data(conn, backtest_id):
    """Return graph/playback data for client-side plotting and decision traces."""
    try:
        bid = int(backtest_id)
    except (TypeError, ValueError):
        raise ValueError("Backtest ID must be an integer")
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "BacktestResults" not in tables:
        raise ValueError("BacktestResults table not found")
    doc = r.db(DB_NAME).table("BacktestResults").get(bid).run(conn)
    if doc is None:
        raise ValueError("Backtest result not found: %s" % bid)
    portfolio_value_history = doc.get("portfolio_value_history") or []
    if not portfolio_value_history:
        raise ValueError("No portfolio value history for this backtest")
    return {
        "id": bid,
        "portfolio_value_history": portfolio_value_history,
        "backtest_trades": doc.get("backtest_trades") or [],
        "backtest_prices": doc.get("backtest_prices") or [],
        "backtest_decisions": doc.get("backtest_decisions") or [],
    }


def action_get_backtest_playback_data(conn, backtest_id):
    """Transform BacktestResults into a time-ordered event stream for the playback UI.

    Returns:
        {
            events: [...],  # ordered list of {type: date|strategy|decision|portfolio, ...}
            metadata: {start_date, end_date, initial_cash, tickers, strategy_schema, pnl, pnl_percent}
        }
    """
    from datetime import datetime as _dt, timezone as _tz
    from collections import defaultdict

    try:
        bid = int(backtest_id)
    except (TypeError, ValueError):
        raise ValueError("Backtest ID must be an integer")
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "BacktestResults" not in tables:
        raise ValueError("BacktestResults table not found")
    doc = r.db(DB_NAME).table("BacktestResults").get(bid).run(conn)
    if doc is None:
        raise ValueError("Backtest result not found: %s" % bid)

    status = (doc.get("status") or "").strip().lower()
    if status in ("pending", "queued"):
        raise ValueError("Backtest has not started yet (status=%s)" % status)

    portfolio_history = doc.get("portfolio_value_history") or []
    trades = doc.get("backtest_trades") or []
    strategy_schema = doc.get("strategy_schema")
    initial_cash = doc.get("initial_cash") or 100000.0

    def _parse_ts(ts_val):
        """Parse a timestamp (ISO string or datetime) to a datetime object."""
        if ts_val is None:
            return None
        if isinstance(ts_val, _dt):
            return ts_val
        if isinstance(ts_val, str):
            raw = ts_val.strip()
            if not raw:
                return None
            try:
                dt = _dt.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is not None:
                    dt = dt.astimezone(_tz.utc).replace(tzinfo=None)
                return dt
            except Exception:
                return None
        return None

    def _date_key(dt_obj):
        """Return 'YYYY-MM-DD' for grouping."""
        return dt_obj.strftime("%Y-%m-%d") if dt_obj else None

    def _fmt_date_label(dt_obj):
        """e.g. 'March 5, 2026'"""
        return dt_obj.strftime("%B %-d, %Y") if dt_obj else "Unknown"

    def _fmt_time(dt_obj):
        """e.g. '09:30 AM'"""
        return dt_obj.strftime("%I:%M %p").lstrip("0") if dt_obj else ""

    # Use platform-safe strftime (Windows uses %#d instead of %-d)
    import platform
    _is_windows = platform.system() == "Windows"

    def _fmt_date_label_safe(dt_obj):
        if dt_obj is None:
            return "Unknown"
        if _is_windows:
            return dt_obj.strftime("%B %#d, %Y")
        return dt_obj.strftime("%B %-d, %Y")

    def _fmt_time_safe(dt_obj):
        if dt_obj is None:
            return ""
        raw = dt_obj.strftime("%I:%M %p")
        return raw.lstrip("0") if raw else ""

    # ── Group trades by date ──
    trades_by_date = defaultdict(lambda: {"buys": [], "sells": []})
    for t in trades:
        ts = _parse_ts(t.get("timestamp"))
        dk = _date_key(ts)
        if dk is None:
            continue
        entry = {
            "ticker": t.get("ticker"),
            "qty": t.get("shares"),
            "price": t.get("price"),
            "total": t.get("total"),
        }
        action = (t.get("action") or "").lower()
        if action == "buy":
            trades_by_date[dk]["buys"].append(entry)
        elif action == "sell":
            trades_by_date[dk]["sells"].append(entry)

    # ── Group portfolio snapshots by date (take last snapshot per date) ──
    snapshots_by_date = {}
    for snap in portfolio_history:
        ts = _parse_ts(snap.get("timestamp"))
        dk = _date_key(ts)
        if dk is None:
            continue
        # Keep last snapshot per date (most recent state)
        snapshots_by_date[dk] = snap

    # ── Group backtest_decisions by date ──
    decisions = doc.get("backtest_decisions") or []
    decisions_by_date = defaultdict(list)
    for d in decisions:
        ts = _parse_ts(d.get("timestamp"))
        dk = _date_key(ts)
        if dk is None:
            continue
        decisions_by_date[dk].append(d)

    # ── Collect all unique dates ──
    all_dates = sorted(set(
        list(trades_by_date.keys()) +
        list(snapshots_by_date.keys()) +
        list(decisions_by_date.keys())
    ))

    # ── Build event stream ──
    events = []
    event_id = 0

    # Compute cumulative position tracking for holdings display
    cumulative_positions = {}  # ticker -> {qty, total_cost}

    for dk in all_dates:
        dt_obj = _parse_ts(dk + "T00:00:00")

        # 1) Date marker
        event_id += 1
        snap = snapshots_by_date.get(dk)
        snap_ts = _parse_ts(snap.get("timestamp")) if snap else dt_obj
        events.append({
            "id": event_id,
            "type": "date",
            "label": _fmt_date_label_safe(dt_obj),
            "time": _fmt_time_safe(snap_ts) if snap_ts else "09:30 AM",
            "desc": "Market Open",
        })

        # 2) Sub-strategy evaluations from backtest_decisions
        day_decisions = decisions_by_date.get(dk, [])
        if day_decisions:
            # Group sub-strategy evaluations per symbol
            for dec in day_decisions:
                symbol = dec.get("symbol") or "?"
                strats = dec.get("strategies") or []
                action_str = dec.get("action") or "hold"
                norm_score = dec.get("normalized_score")

                # Emit one strategy event per sub-strategy that evaluated this symbol
                for sub in strats:
                    event_id += 1
                    sub_name = (sub.get("strategy") or "Unknown").strip()
                    sub_score = sub.get("decision", 0)
                    sub_reason = sub.get("reason") or ""
                    sub_weight = sub.get("weight")
                    score_label = {1: "BUY", 0: "HOLD", -1: "SELL"}.get(sub_score, "HOLD")
                    events.append({
                        "id": event_id,
                        "type": "strategy",
                        "name": sub_name,
                        "desc": "%s → %s" % (symbol, score_label),
                        "reason": sub_reason[:300] if sub_reason else ("Weight: %s" % sub_weight if sub_weight else ""),
                        "tickers": [symbol],
                    })

                # Emit outcome event with the aggregated result
                if strats:
                    event_id += 1
                    action_label = {1: "BUY", 0: "HOLD", -1: "SELL"}.get(dec.get("decision", 0), "HOLD")
                    score_str = "%.3f" % norm_score if norm_score is not None else "—"
                    events.append({
                        "id": event_id,
                        "type": "outcome",
                        "name": "%s → Final: %s" % (symbol, action_label),
                        "details": "Weighted score: %s (%d strategies)" % (score_str, len(strats)),
                    })

        elif strategy_schema and dk == all_dates[0]:
            # Fallback for older backtests without decisions data: emit static schema
            subs = strategy_schema.get("strategies") or []
            for sub in subs:
                event_id += 1
                sub_name = (sub.get("strategy") or "Unknown").strip()
                scope = (sub.get("execution_scope") or "per_symbol").strip()
                scope_label = "Run-once Strategy" if scope == "run_once" else "Per-ticker Strategy"
                events.append({
                    "id": event_id,
                    "type": "strategy",
                    "name": sub_name,
                    "desc": scope_label,
                    "reason": "Weight: %s" % sub.get("weight", "?"),
                })

        # 3) Decision events (trades)
        day_trades = trades_by_date.get(dk)
        if day_trades and (day_trades["buys"] or day_trades["sells"]):
            event_id += 1
            events.append({
                "id": event_id,
                "type": "decision",
                "buys": day_trades["buys"],
                "sells": day_trades["sells"],
            })

            # Update cumulative positions
            for buy in day_trades["buys"]:
                ticker = buy.get("ticker")
                if not ticker:
                    continue
                if ticker not in cumulative_positions:
                    cumulative_positions[ticker] = {"qty": 0.0, "total_cost": 0.0}
                cumulative_positions[ticker]["qty"] += float(buy.get("qty") or 0)
                cumulative_positions[ticker]["total_cost"] += float(buy.get("total") or 0)

            for sell in day_trades["sells"]:
                ticker = sell.get("ticker")
                if not ticker:
                    continue
                if ticker in cumulative_positions:
                    sell_qty = float(sell.get("qty") or 0)
                    pos = cumulative_positions[ticker]
                    avg = pos["total_cost"] / pos["qty"] if pos["qty"] > 0 else 0
                    pos["qty"] = max(0, pos["qty"] - sell_qty)
                    pos["total_cost"] = pos["qty"] * avg
                    if pos["qty"] <= 0:
                        del cumulative_positions[ticker]

        # 4) Portfolio snapshot
        if snap:
            positions_snap = snap.get("positions_snapshot") or {}
            prices_snap = snap.get("prices") or {}
            holdings = []
            for ticker, qty in positions_snap.items():
                if float(qty or 0) <= 0:
                    continue
                curr_price = prices_snap.get(ticker)
                pos = cumulative_positions.get(ticker)
                avg_cost = (pos["total_cost"] / pos["qty"]) if (pos and pos["qty"] > 0) else curr_price
                holdings.append({
                    "ticker": ticker,
                    "qty": float(qty),
                    "avg": round(float(avg_cost), 2) if avg_cost else None,
                    "curr": round(float(curr_price), 2) if curr_price else None,
                })

            event_id += 1
            events.append({
                "id": event_id,
                "type": "portfolio",
                "value": snap.get("value"),
                "cash": snap.get("cash"),
                "date": snap.get("timestamp") if isinstance(snap.get("timestamp"), str) else (snap.get("timestamp").isoformat() if hasattr(snap.get("timestamp"), "isoformat") else str(snap.get("timestamp"))),
                "holdings": holdings,
            })

    # ── Metadata ──
    metadata = {
        "start_date": doc.get("start_date"),
        "end_date": doc.get("end_date"),
        "initial_cash": initial_cash,
        "tickers": doc.get("tickers") or [],
        "strategy_schema": strategy_schema,
        "pnl": doc.get("pnl"),
        "pnl_percent": doc.get("pnl_percent"),
        "status": doc.get("status"),
        "granularity_sec": doc.get("granularity_sec"),
    }

    return {
        "id": bid,
        "events": events,
        "metadata": metadata,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Nexus Market Trends & Discovered Stocks
# ─────────────────────────────────────────────────────────────────────────────

_NEXUS_TRENDS_TABLE = "GraphNexusMarketTrends"
_NEXUS_DISCOVERED_TABLE = "GraphNexusDiscoveredStocks"


def _ensure_nexus_trends_tables(conn):
    """Ensure trends and discovered stocks tables exist."""
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if _NEXUS_TRENDS_TABLE not in tables:
        r.db(DB_NAME).table_create(_NEXUS_TRENDS_TABLE).run(conn)
    if _NEXUS_DISCOVERED_TABLE not in tables:
        r.db(DB_NAME).table_create(_NEXUS_DISCOVERED_TABLE).run(conn)


def action_list_trends(conn, instance_id=None, status=None):
    """List market trends, optionally filtered by instance_id and/or status."""
    _ensure_nexus_trends_tables(conn)
    query = r.db(DB_NAME).table(_NEXUS_TRENDS_TABLE)
    if instance_id:
        query = query.filter(lambda doc: doc["instance_id"] == instance_id)
    if status:
        query = query.filter(lambda doc: doc["status"] == status)
    cursor = query.order_by(r.desc("updated_at")).limit(100).run(conn)
    trends = list(cursor)
    return {"trends": trends, "count": len(trends)}


def action_get_trend(conn, trend_id):
    """Get a single trend by its full ID."""
    _ensure_nexus_trends_tables(conn)
    if not trend_id:
        raise ValueError("Trend ID required")
    doc = r.db(DB_NAME).table(_NEXUS_TRENDS_TABLE).get(str(trend_id)).run(conn)
    if doc is None:
        raise ValueError("Trend not found: %s" % trend_id)
    return {"trend": doc}


def action_end_trend(conn, trend_id, reason="manually ended"):
    """Manually end an active trend."""
    _ensure_nexus_trends_tables(conn)
    if not trend_id:
        raise ValueError("Trend ID required")
    doc = r.db(DB_NAME).table(_NEXUS_TRENDS_TABLE).get(str(trend_id)).run(conn)
    if doc is None:
        raise ValueError("Trend not found: %s" % trend_id)
    if doc.get("status") == "ended":
        return {"ended": False, "message": "Trend already ended"}
    from datetime import datetime
    r.db(DB_NAME).table(_NEXUS_TRENDS_TABLE).get(str(trend_id)).update({
        "status": "ended",
        "ended_at": datetime.utcnow().isoformat(),
        "end_reason": reason,
    }).run(conn)
    return {"ended": True, "id": trend_id}


def action_delete_trend(conn, trend_id):
    """Delete a trend entirely."""
    _ensure_nexus_trends_tables(conn)
    if not trend_id:
        raise ValueError("Trend ID required")
    doc = r.db(DB_NAME).table(_NEXUS_TRENDS_TABLE).get(str(trend_id)).run(conn)
    if doc is None:
        raise ValueError("Trend not found: %s" % trend_id)
    r.db(DB_NAME).table(_NEXUS_TRENDS_TABLE).get(str(trend_id)).delete().run(conn)
    return {"deleted": True, "id": trend_id}


def action_list_discovered_stocks(conn, instance_id=None, status=None):
    """List discovered stocks, optionally filtered by instance_id and/or status."""
    _ensure_nexus_trends_tables(conn)
    query = r.db(DB_NAME).table(_NEXUS_DISCOVERED_TABLE)
    if instance_id:
        query = query.filter(lambda doc: doc["instance_id"] == instance_id)
    if status:
        query = query.filter(lambda doc: doc["status"] == status)
    cursor = query.order_by(r.desc("discovered_at")).limit(100).run(conn)
    stocks = list(cursor)
    return {"stocks": stocks, "count": len(stocks)}


def action_remove_discovered_stock(conn, instance_id, ticker):
    """Remove a discovered stock (mark as removed)."""
    _ensure_nexus_trends_tables(conn)
    if not instance_id or not ticker:
        raise ValueError("Instance ID and ticker required")
    ticker = ticker.upper().strip()
    doc_id = f"{instance_id}:{ticker}"
    doc = r.db(DB_NAME).table(_NEXUS_DISCOVERED_TABLE).get(doc_id).run(conn)
    if doc is None:
        raise ValueError("Discovered stock not found: %s" % doc_id)
    from datetime import datetime
    r.db(DB_NAME).table(_NEXUS_DISCOVERED_TABLE).get(doc_id).update({
        "status": "removed",
        "removed_at": datetime.utcnow().isoformat(),
    }).run(conn)
    return {"removed": True, "ticker": ticker, "instance_id": instance_id}


def action_nexus_config_get(conn, instance_id):
    """Get nexus trend/discovery configuration for an instance."""
    if not instance_id:
        raise ValueError("Instance ID required")
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if "Instances" not in tables:
        return {"config": {}}
    doc = _resolve_instance_doc(conn, instance_id)
    if doc is None:
        raise ValueError("Instance not found: %s" % instance_id)
    # Extract nexus-related config from the instance's strategy configs
    strategies = doc.get("strategies") or []
    nexus_config = {}
    for s in strategies:
        if (s.get("strategy") or "").strip().lower() == "graph_nexus_analysis":
            nexus_config = dict(s.get("config") or {})
            break
    # Return only trend/discovery related keys
    trend_keys = [
        "trend_tracking_enabled", "stock_finder_enabled", "sell_enforcement_enabled",
        "max_discovered_stocks", "trend_min_strength_to_buy", "trend_max_age_days",
        "nexus_portfolio_pct", "google_news_enabled",
    ]
    filtered = {k: nexus_config.get(k) for k in trend_keys if k in nexus_config}
    # Provide defaults for missing keys
    defaults = {
        "trend_tracking_enabled": True,
        "stock_finder_enabled": True,
        "sell_enforcement_enabled": True,
        "max_discovered_stocks": 90,
        "trend_min_strength_to_buy": 0.5,
        "trend_max_age_days": 90,
        "nexus_portfolio_pct": 0.80,
        "google_news_enabled": True,
    }
    for k, v in defaults.items():
        if k not in filtered:
            filtered[k] = v
    return {"config": filtered, "instance_id": instance_id}


def action_nexus_config_set(conn, instance_id, updates):
    """Update nexus trend/discovery configuration for an instance."""
    if not instance_id:
        raise ValueError("Instance ID required")
    if not updates or not isinstance(updates, dict):
        raise ValueError("Updates dict required")
    doc = _resolve_instance_doc(conn, instance_id)
    if doc is None:
        raise ValueError("Instance not found: %s" % instance_id)
    instance_id_actual = doc.get("id", instance_id)
    strategies = list(doc.get("strategies") or [])
    nexus_idx = None
    for i, s in enumerate(strategies):
        if (s.get("strategy") or "").strip().lower() == "graph_nexus_analysis":
            nexus_idx = i
            break
    if nexus_idx is None:
        raise ValueError("No graph_nexus_analysis strategy found on instance %s" % instance_id)
    config = dict(strategies[nexus_idx].get("config") or {})
    # Only allow known trend/discovery keys
    allowed_keys = {
        "trend_tracking_enabled", "stock_finder_enabled", "sell_enforcement_enabled",
        "max_discovered_stocks", "trend_min_strength_to_buy", "trend_max_age_days",
        "nexus_portfolio_pct", "google_news_enabled",
    }
    applied = {}
    for k, v in updates.items():
        if k in allowed_keys:
            config[k] = v
            applied[k] = v
    strategies[nexus_idx]["config"] = config
    r.db(DB_NAME).table("Instances").get(instance_id_actual).update({
        "strategies": strategies,
    }).run(conn)
    return {"updated": True, "applied": applied, "instance_id": instance_id_actual}


# ── Brokerage Accounts ────────────────────────────────────────────────────────

BROKERAGE_ACCOUNTS_TABLE = "BrokerageAccounts"


def _ensure_brokerage_accounts_table(conn):
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if BROKERAGE_ACCOUNTS_TABLE not in tables:
        r.db(DB_NAME).table_create(BROKERAGE_ACCOUNTS_TABLE).run(conn)


def _mask_brokerage_doc(doc):
    """Return a copy with sensitive credentials masked for display.

    When the stored value is Fernet-encrypted (starts with "fernet:"), we mask
    it without exposing the tag - decrypt briefly for display-only masking
    of the original key's shape. Secrets always fully mask. Tokens fully mask.

    This function is also used for edit-form pre-population; the _looks_masked()
    helper lets the update action recognise echoed masks and treat them as
    'keep existing' rather than validating the mask against the broker.
    """
    d = dict(doc)
    try:
        from secret_store import decrypt as _decrypt, is_encrypted as _is_enc
    except Exception:
        _decrypt = lambda v: v
        _is_enc = lambda v: False

    def _mask_key(val):
        if not val:
            return val
        raw = _decrypt(val) if _is_enc(val) else val
        raw = raw or ""
        if len(raw) > 8:
            return raw[:4] + "****" + raw[-4:]
        return "****"

    if d.get("alpaca_key"):
        d["alpaca_key"] = _mask_key(d["alpaca_key"])
    if d.get("alpaca_secret"):
        d["alpaca_secret"] = "****"
    if d.get("robinhood_access_token"):
        d["robinhood_access_token"] = "****"
    if d.get("robinhood_refresh_token"):
        d["robinhood_refresh_token"] = "****"
    if d.get("robinhood_device_token"):
        d["robinhood_device_token"] = "****"
    return d


def _looks_masked(val: str) -> bool:
    """True if a submitted value looks like it was echoed from _mask_brokerage_doc.

    Users editing an account often leave the masked value in the form rather
    than blanking it; treating that submission as 'keep existing' matches the
    UI's intent ('leave blank to keep existing').
    """
    if not val or not isinstance(val, str):
        return False
    v = val.strip()
    # Exact full-mask or any value containing the **** sentinel.
    return v == "****" or "****" in v


def action_list_brokerages(conn):
    """List all linked brokerage accounts (credentials masked)."""
    _ensure_brokerage_accounts_table(conn)
    docs = list(r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE).run(conn))
    return {"accounts": [_mask_brokerage_doc(d) for d in docs]}


_ALPACA_DATA_FEED_CHOICES = ("iex", "sip")


def alpaca_run_diagnostic_suite(
    key: str,
    secret: str,
    feed: str = "iex",
    paper: bool = True,
) -> dict:
    """R16: multi-endpoint test suite with structured per-test results so the
    UI can show exactly which capabilities work BEFORE saving the brokerage.
    """
    import requests as _req
    feed = (feed or "iex").strip().lower()
    if feed not in _ALPACA_DATA_FEED_CHOICES:
        feed = "iex"
    headers = {"APCA-API-KEY-ID": key or "", "APCA-API-SECRET-KEY": secret or ""}
    trading_base = "https://paper-api.alpaca.markets" if bool(paper) else "https://api.alpaca.markets"
    data_base = "https://data.alpaca.markets"

    def _run(label: str, url: str, params: dict | None = None) -> dict:
        try:
            r = _req.get(url, headers=headers, params=params or {}, timeout=10)
        except Exception as e:
            return {
                "name": label,
                "ok": False,
                "status": 0,
                "message": f"transport error: {type(e).__name__}: {e}"[:240],
                "url": url,
            }
        ok = bool(r.ok)
        snippet = ""
        try:
            j = r.json()
            if isinstance(j, dict):
                snippet = (
                    j.get("message")
                    or j.get("error")
                    or j.get("code")
                    or (str(list(j.keys())[:5]) if j else "")
                )
        except Exception:
            snippet = (r.text or "")[:160]
        message = "ok" if ok else f"HTTP {r.status_code}: {snippet or r.reason}"[:240]
        return {
            "name": label,
            "ok": ok,
            "status": int(r.status_code),
            "message": message,
            "url": url,
        }

    tests: list[dict] = [
        _run("account", f"{trading_base}/v2/account"),
        _run("clock", f"{trading_base}/v2/clock"),
        _run(
            f"bars_{feed}",
            f"{data_base}/v2/stocks/AAPL/bars",
            {"timeframe": "1Day", "limit": 1, "feed": feed},
        ),
        _run(
            f"latest_quote_{feed}",
            f"{data_base}/v2/stocks/AAPL/quotes/latest",
            {"feed": feed},
        ),
        _run("news", f"{data_base}/v1beta1/news", {"symbols": "AAPL", "limit": 2}),
    ]

    passed = sum(1 for t in tests if t["ok"])
    failed = len(tests) - passed
    hints: list[str] = []
    bars_test = next((t for t in tests if t["name"].startswith("bars_")), None)
    if bars_test and not bars_test["ok"] and bars_test["status"] in (401, 403):
        if feed == "sip":
            hints.append(
                "SIP feed needs Alpaca 'Algo Trader Plus' ($99/mo) or equivalent. "
                "Subscribe on the Alpaca dashboard or switch this account to IEX."
            )
        else:
            hints.append(
                "IEX bars rejected — LIVE accounts must explicitly subscribe to "
                "'Basic Market Data' on the Alpaca dashboard. Paper accounts have "
                "it auto-enabled."
            )
    acct_test = next((t for t in tests if t["name"] == "account"), None)
    if acct_test and not acct_test["ok"] and acct_test["status"] in (401, 403):
        hints.append(
            "Trading API rejected the credentials. Check the key matches the "
            f"selected paper={bool(paper)} setting (paper keys only work on "
            "paper-api.alpaca.markets, live keys only work on api.alpaca.markets)."
        )
    if failed and not hints:
        hints.append(
            "One or more endpoints failed — check the per-test messages above for "
            "specific HTTP status and Alpaca error text."
        )

    return {
        "ok": failed == 0,
        "summary": {"passed": passed, "failed": failed, "total": len(tests)},
        "feed": feed,
        "paper": bool(paper),
        "tests": tests,
        "hints": hints,
    }


def _alpaca_validate_data_access(key: str, secret: str, feed: str) -> dict:
    """Validate the Alpaca bars endpoint accepts this feed/key combo BEFORE save.
    Returns ``{"ok": True, "bars": N, "feed": ...}`` on 2xx, a warning dict on
    transient 5xx/429, or raises ValueError on permanent 4xx so the UI shows
    why the feed choice won't work.
    """
    import requests as _req
    feed = (feed or "iex").strip().lower()
    if feed not in _ALPACA_DATA_FEED_CHOICES:
        raise ValueError(
            f"alpaca_data_feed must be one of {_ALPACA_DATA_FEED_CHOICES}, got {feed!r}"
        )
    try:
        resp = _req.get(
            "https://data.alpaca.markets/v2/stocks/AAPL/bars",
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            params={"timeframe": "1Day", "limit": 1, "feed": feed},
            timeout=10,
        )
    except Exception as e:
        raise ValueError(
            f"Could not reach data.alpaca.markets to test feed={feed!r}: "
            f"{type(e).__name__}: {e}"
        )
    if resp.status_code in (401, 403):
        detail = ""
        try:
            body = resp.json() or {}
            detail = body.get("message") or body.get("code") or resp.text[:160]
        except Exception:
            detail = resp.text[:160]
        if feed == "sip":
            hint = (
                "SIP feed requires Alpaca 'Algo Trader Plus' ($99/mo) or an "
                "equivalent paid plan. Subscribe on the Alpaca dashboard, "
                "or switch this account to IEX."
            )
        else:
            hint = (
                "IEX data is free but LIVE accounts must explicitly "
                "subscribe to 'Basic Market Data' on the Alpaca dashboard "
                "(paper accounts have it auto-enabled)."
            )
        raise ValueError(
            f"Alpaca data access validation failed for feed={feed} "
            f"(HTTP {resp.status_code}): {detail} — {hint}"
        )
    # Bug-sweep b682c5e: transient status codes shouldn't block saves —
    # the creds are probably fine, Alpaca just can't answer right now.
    if resp.status_code in (408, 425, 429, 500, 502, 503, 504):
        return {
            "ok": False,
            "warning": f"transient HTTP {resp.status_code} — feed unverified at save time",
            "feed": feed,
        }
    if not resp.ok:
        raise ValueError(
            f"Alpaca data test returned HTTP {resp.status_code}: {resp.text[:160]}"
        )
    try:
        bars = (resp.json() or {}).get("bars") or []
    except Exception:
        bars = []
    return {"ok": True, "bars": len(bars), "feed": feed}


def action_link_alpaca(conn, account_name, key, secret, paper=True, alpaca_data_feed="iex"):
    """Link an Alpaca account. Validates credentials via Alpaca API before saving."""
    account_name = (account_name or "").strip()
    key = (key or "").strip()
    secret = (secret or "").strip()
    alpaca_data_feed = (alpaca_data_feed or "iex").strip().lower()
    if alpaca_data_feed not in _ALPACA_DATA_FEED_CHOICES:
        raise ValueError(
            f"alpaca_data_feed must be one of {_ALPACA_DATA_FEED_CHOICES}"
        )
    if not account_name:
        raise ValueError("account_name is required")
    if not key or not secret:
        raise ValueError("key and secret are required for Alpaca")

    import requests as _req
    base_url = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
    try:
        resp = _req.get(
            f"{base_url}/v2/account",
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            timeout=10,
        )
        if not resp.ok:
            detail = ""
            try:
                detail = resp.json().get("message") or resp.json().get("code") or ""
            except Exception:
                pass
            raise ValueError(f"Alpaca validation failed (HTTP {resp.status_code}): {detail}")
        acct_data = resp.json()
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Could not connect to Alpaca to validate: {e}")

    # 2026-04-23: verify the bars endpoint accepts this feed choice BEFORE save
    # so the account row can never silently store a feed the credentials
    # won't authorize.
    data_access = _alpaca_validate_data_access(key, secret, alpaca_data_feed)

    _ensure_brokerage_accounts_table(conn)
    import datetime
    now = datetime.datetime.utcnow().isoformat()
    # Encrypt secrets at rest. If Fernet key is missing we fall back to plaintext
    # rather than block account linking - the credential_service logs a warning.
    try:
        from secret_store import encrypt as _encrypt
        _enc_key = _encrypt(key)
        _enc_secret = _encrypt(secret)
    except RuntimeError:
        _enc_key = key
        _enc_secret = secret
    doc = {
        "account_name": account_name,
        "brokerage_type": "alpaca",
        "alpaca_key": _enc_key,
        "alpaca_secret": _enc_secret,
        "alpaca_paper": bool(paper),
        "alpaca_base_url": base_url,
        "alpaca_account_number": acct_data.get("account_number", ""),
        "alpaca_data_feed": alpaca_data_feed,
        "status": "active",
        "last_error": None,
        "created_at": now,
        "updated_at": now,
        "last_refresh_at": now,
    }
    result = r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE).insert(doc).run(conn)
    inserted_id = (result.get("generated_keys") or [None])[0]
    doc["id"] = inserted_id
    return {"linked": True, "account": _mask_brokerage_doc(doc), "data_access": data_access}


def _rh_safe_float(v):
    """Parse a Robinhood string number to float, or None."""
    try:
        return round(float(v), 2) if v is not None else None
    except Exception:
        return None


def action_fetch_robinhood_accounts(access_token, refresh_token=None, device_token=None):
    """Validate Robinhood tokens and return available accounts with display info for account selection."""
    import time as _time
    import uuid as _uuid
    access_token = (access_token or "").strip()
    if access_token.lower().startswith("bearer "):
        access_token = access_token[7:].strip()
    if not access_token:
        raise ValueError("access_token is required")

    from robinhood_engine import RobinhoodClient, RobinhoodSessionState, RobinhoodAPIError as _RHErr
    state = RobinhoodSessionState(
        access_token=access_token,
        refresh_token=(refresh_token or "").strip(),
        token_type="Bearer",
        expires_in=86400,
        obtained_at_epoch=int(_time.time()),
        device_token=(device_token or "").strip() or str(_uuid.uuid4()),
    )
    client = RobinhoodClient(state=state)
    try:
        raw_accounts = client.get_accounts()
    except _RHErr as e:
        raise ValueError(f"Robinhood validation failed: {e.detail or str(e)}")
    except Exception as e:
        raise ValueError(f"Could not connect to Robinhood: {e}")

    if not raw_accounts:
        raise ValueError("No Robinhood accounts found with these credentials")

    result = []
    for a in raw_accounts:
        equity = _rh_safe_float(a.get("equity") or a.get("extended_hours_equity"))
        result.append({
            "account_number":  a.get("account_number", ""),
            "account_type":    a.get("type", ""),
            "display_name":    a.get("display_name", ""),
            "management_type": a.get("management_type", "self_directed"),
            "equity":          equity,
            "buying_power":    _rh_safe_float(a.get("buying_power")),
            "cash":            _rh_safe_float(a.get("cash")),
        })
    return {"accounts": result}


def _rh_expires_in_from_jwt(access_token, fallback=86400):
    """Decode the JWT's `exp` claim to derive a real expires_in (seconds from now).

    Robinhood now issues access_tokens with multi-week TTLs (e.g. 31 days = 2_713_454s),
    not the 24h default. Hardcoding 86400 makes _maybe_refresh_token attempt a refresh
    at the 12h mark of a 31-day token — way too early, and prone to hitting RH's
    session-rotation logic which surfaces as RobinhoodMFARequired.

    This decodes the JWT body (no signature check; we trust the token we just received
    from RH) and returns max(0, exp - now). Falls back to `fallback` on any parse error.
    """
    import base64
    import json
    import time as _time
    if not access_token or not isinstance(access_token, str):
        return fallback
    try:
        parts = access_token.split(".")
        if len(parts) < 2:
            return fallback
        payload_b64 = parts[1]
        # JWT base64url uses no padding; add it back.
        padding = "=" * (-len(payload_b64) % 4)
        decoded = base64.urlsafe_b64decode(payload_b64 + padding)
        claims = json.loads(decoded.decode("utf-8"))
        exp = claims.get("exp")
        if not exp:
            return fallback
        derived = int(exp) - int(_time.time())
        return derived if derived > 0 else fallback
    except Exception:
        return fallback


def action_link_robinhood_tokens(conn, account_name, access_token, refresh_token,
                                  device_token=None, expires_in=None, obtained_at_epoch=None,
                                  account_number=None):
    """Link a Robinhood account via tokens. Validates by calling /accounts/.
    If account_number is provided, selects that specific account; otherwise uses the first."""
    import time as _time
    import uuid as _uuid
    account_name = (account_name or "").strip()
    access_token = (access_token or "").strip()
    if access_token.lower().startswith("bearer "):
        access_token = access_token[7:].strip()
    refresh_token = (refresh_token or "").strip()
    if not account_name:
        raise ValueError("account_name is required")
    if not access_token:
        raise ValueError("access_token is required")
    if not refresh_token:
        raise ValueError("refresh_token is required")

    device_token = (device_token or "").strip() or str(_uuid.uuid4())
    if obtained_at_epoch is None:
        obtained_at_epoch = int(_time.time())
    # Derive real expires_in from the JWT exp claim; the 86400 hardcoded
    # default is wrong for tokens RH now issues with longer TTLs (~31 days).
    if expires_in is None or expires_in == 86400:
        expires_in = _rh_expires_in_from_jwt(access_token, fallback=expires_in or 86400)

    from robinhood_engine import RobinhoodClient, RobinhoodSessionState, RobinhoodAPIError as _RHErr
    state = RobinhoodSessionState(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="Bearer",
        expires_in=expires_in,
        obtained_at_epoch=obtained_at_epoch,
        device_token=device_token,
    )
    client = RobinhoodClient(state=state)
    try:
        acct = client.select_default_account(
            preferred_account_number=(account_number or "").strip() or None
        )
    except _RHErr as e:
        raise ValueError(f"Robinhood validation failed: {e.detail or str(e)}")
    except Exception as e:
        raise ValueError(f"Could not connect to Robinhood to validate: {e}")

    acct_number = client.state.account_number or acct.get("account_number", "")
    acct_url = client.state.account_url or acct.get("url", "")

    _ensure_brokerage_accounts_table(conn)
    import datetime
    now = datetime.datetime.utcnow().isoformat()
    # Encrypt bearer tokens at rest.
    try:
        from secret_store import encrypt as _encrypt
        _enc_access = _encrypt(access_token)
        _enc_refresh = _encrypt(refresh_token)
        _enc_device = _encrypt(device_token)
    except RuntimeError:
        _enc_access = access_token
        _enc_refresh = refresh_token
        _enc_device = device_token
    doc = {
        "account_name": account_name,
        "brokerage_type": "robinhood",
        "robinhood_access_token": _enc_access,
        "robinhood_refresh_token": _enc_refresh,
        "robinhood_device_token": _enc_device,
        "robinhood_expires_in": int(expires_in),
        "robinhood_obtained_at_epoch": int(obtained_at_epoch),
        "robinhood_account_number": acct_number,
        "robinhood_account_url": acct_url,
        "robinhood_token_type": "Bearer",
        "status": "active",
        "last_error": None,
        "created_at": now,
        "updated_at": now,
        "last_refresh_at": now,
    }
    result = r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE).insert(doc).run(conn)
    inserted_id = (result.get("generated_keys") or [None])[0]
    doc["id"] = inserted_id
    return {"linked": True, "account": _mask_brokerage_doc(doc)}


def action_delete_brokerage(conn, brokerage_id):
    """Delete a linked brokerage account."""
    brokerage_id = (brokerage_id or "").strip()
    if not brokerage_id:
        raise ValueError("brokerage_id is required")
    _ensure_brokerage_accounts_table(conn)
    doc = r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE).get(brokerage_id).run(conn)
    if doc is None:
        raise ValueError(f"Brokerage account not found: {brokerage_id}")
    r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE).get(brokerage_id).delete().run(conn)
    return {"deleted": True, "id": brokerage_id}


def action_update_brokerage(conn, brokerage_id, account_name=None, key=None, secret=None,
                             paper=None, access_token=None, refresh_token=None,
                             device_token=None, account_number=None, alpaca_data_feed=None):
    """Update a linked brokerage account's name and/or credentials.

    2026-04-23: accepts ``alpaca_data_feed`` (iex|sip). When the feed OR the
    key/secret changes, a live test call to data.alpaca.markets validates
    that the chosen feed is authorized before the row is updated.
    """
    brokerage_id = (brokerage_id or "").strip()
    if not brokerage_id:
        raise ValueError("brokerage_id is required")
    _ensure_brokerage_accounts_table(conn)
    doc = r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE).get(brokerage_id).run(conn)
    if doc is None:
        raise ValueError(f"Brokerage account not found: {brokerage_id}")

    btype = doc.get("brokerage_type")
    import datetime
    now = datetime.datetime.utcnow().isoformat()
    update = {"updated_at": now}
    data_access_result = None  # populated if we run the feed validation

    # Helper for encrypting secrets on update. If Fernet key missing, store plaintext
    # (caller may not have configured it yet; credential_service logs a warning).
    try:
        from secret_store import encrypt as _encrypt, decrypt as _decrypt
    except Exception:
        _encrypt = lambda v: v
        _decrypt = lambda v: v

    if btype == "alpaca":
        account_name = (account_name or "").strip()
        key = (key or "").strip()
        secret = (secret or "").strip()

        # Echoed masks (e.g. frontend re-submits the displayed "PKAB****WXYZ"
        # string) must be treated as "keep existing" rather than a new value.
        if _looks_masked(key):
            key = ""
        if _looks_masked(secret):
            secret = ""

        if account_name:
            update["account_name"] = account_name

        # 2026-04-23: accept alpaca_data_feed change even without new creds,
        # but ALWAYS validate the feed works with whatever creds we'll end up
        # storing (existing or new).
        _incoming_feed = (alpaca_data_feed or "").strip().lower() if alpaca_data_feed is not None else None
        if _incoming_feed and _incoming_feed not in _ALPACA_DATA_FEED_CHOICES:
            raise ValueError(
                f"alpaca_data_feed must be one of {_ALPACA_DATA_FEED_CHOICES}"
            )
        _effective_feed = _incoming_feed or (doc.get("alpaca_data_feed") or "iex")

        if key or secret:
            use_key = key or _decrypt(doc.get("alpaca_key", "")) or ""
            use_secret = secret or _decrypt(doc.get("alpaca_secret", "")) or ""
            use_paper = paper if paper is not None else doc.get("alpaca_paper", True)
            base_url = "https://paper-api.alpaca.markets" if use_paper else "https://api.alpaca.markets"

            import requests as _req
            try:
                resp = _req.get(
                    f"{base_url}/v2/account",
                    headers={"APCA-API-KEY-ID": use_key, "APCA-API-SECRET-KEY": use_secret},
                    timeout=10,
                )
                if not resp.ok:
                    detail = ""
                    try:
                        detail = resp.json().get("message") or resp.json().get("code") or ""
                    except Exception:
                        pass
                    raise ValueError(f"Alpaca validation failed (HTTP {resp.status_code}): {detail}")
                acct_data = resp.json()
            except ValueError:
                raise
            except Exception as e:
                raise ValueError(f"Could not connect to Alpaca to validate: {e}")

            # 2026-04-23: run the data-access validation alongside /v2/account
            # so stored feed never mismatches subscription state.
            data_access_result = _alpaca_validate_data_access(
                use_key, use_secret, _effective_feed,
            )

            if key:
                try:
                    update["alpaca_key"] = _encrypt(key)
                except RuntimeError:
                    update["alpaca_key"] = key
            if secret:
                try:
                    update["alpaca_secret"] = _encrypt(secret)
                except RuntimeError:
                    update["alpaca_secret"] = secret
            if paper is not None:
                update["alpaca_paper"] = bool(paper)
                update["alpaca_base_url"] = base_url
            update["alpaca_account_number"] = acct_data.get("account_number", "")
            update["status"] = "active"
            update["last_error"] = None
            update["last_refresh_at"] = now
        elif paper is not None:
            update["alpaca_paper"] = bool(paper)
            update["alpaca_base_url"] = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"

        # Feed-only change path: no new key/secret but caller flipped iex↔sip.
        # Validate against the existing stored creds so we don't silently
        # save a feed the account can't use.
        #
        # Bug-sweep 2026-04-23: short-circuit when the feed is unchanged to
        # avoid a wasteful data.alpaca.markets round-trip on every save that
        # just renames an account. Also: fail LOUD (not silent) when decrypt
        # fails on existing creds — previously we swallowed the exception
        # and persisted the feed change unvalidated, defeating the gate.
        if _incoming_feed and not (key or secret):
            _stored_feed = str(doc.get("alpaca_data_feed") or "").strip().lower()
            if _incoming_feed != _stored_feed:
                try:
                    _use_key = _decrypt(doc.get("alpaca_key", "")) or ""
                    _use_secret = _decrypt(doc.get("alpaca_secret", "")) or ""
                except Exception as _dec_e:
                    raise ValueError(
                        f"Cannot validate feed change: stored credentials could "
                        f"not be decrypted ({type(_dec_e).__name__}). Re-enter "
                        f"API key + secret along with the feed change, or fix "
                        f"INTELLISTOCK_CRED_KEY env."
                    )
                if not (_use_key and _use_secret):
                    raise ValueError(
                        "Cannot validate feed change: stored credentials are "
                        "empty or unreadable. Re-enter API key + secret."
                    )
                data_access_result = _alpaca_validate_data_access(
                    _use_key, _use_secret, _incoming_feed,
                )
        if _incoming_feed:
            update["alpaca_data_feed"] = _incoming_feed

    elif btype == "robinhood":
        account_name = (account_name or "").strip()
        access_token = (access_token or "").strip()
        if access_token.lower().startswith("bearer "):
            access_token = access_token[7:].strip()
        refresh_token = (refresh_token or "").strip()
        device_token = (device_token or "").strip()
        account_number = (account_number or "").strip()

        # Same mask-echo guard as alpaca: UI-displayed "****" must not be
        # re-submitted as a new token value.
        if _looks_masked(access_token):
            access_token = ""
        if _looks_masked(refresh_token):
            refresh_token = ""
        if _looks_masked(device_token):
            device_token = ""

        if account_name:
            update["account_name"] = account_name

        if access_token:
            import time as _time, uuid as _uuid
            from robinhood_engine import RobinhoodClient, RobinhoodSessionState, RobinhoodAPIError as _RHErr
            dtoken = device_token or _decrypt(doc.get("robinhood_device_token")) or str(_uuid.uuid4())
            _real_expires_in = _rh_expires_in_from_jwt(access_token, fallback=86400)
            state = RobinhoodSessionState(
                access_token=access_token,
                refresh_token=refresh_token or _decrypt(doc.get("robinhood_refresh_token", "")) or "",
                token_type="Bearer",
                expires_in=_real_expires_in,
                obtained_at_epoch=int(_time.time()),
                device_token=dtoken,
            )
            client = RobinhoodClient(state=state)
            try:
                acct = client.select_default_account(
                    preferred_account_number=account_number or doc.get("robinhood_account_number") or None
                )
            except _RHErr as e:
                raise ValueError(f"Robinhood validation failed: {e.detail or str(e)}")
            except Exception as e:
                raise ValueError(f"Could not connect to Robinhood to validate: {e}")

            acct_num = client.state.account_number or acct.get("account_number", "")
            # Encrypt tokens on write.
            try:
                _enc_access = _encrypt(access_token)
                _enc_refresh = _encrypt(refresh_token or _decrypt(doc.get("robinhood_refresh_token", "")) or "")
                _enc_device = _encrypt(dtoken)
            except RuntimeError:
                _enc_access = access_token
                _enc_refresh = refresh_token or doc.get("robinhood_refresh_token", "")
                _enc_device = dtoken
            update.update({
                "robinhood_access_token": _enc_access,
                "robinhood_refresh_token": _enc_refresh,
                "robinhood_device_token": _enc_device,
                "robinhood_obtained_at_epoch": int(_time.time()),
                "robinhood_expires_in": int(_real_expires_in),
                "robinhood_account_number": acct_num,
                "robinhood_account_url": client.state.account_url or acct.get("url", ""),
                "status": "active",
                "last_error": None,
                "last_refresh_at": now,
            })

    r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE).get(brokerage_id).update(update).run(conn)
    updated_doc = r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE).get(brokerage_id).run(conn)
    _out = {"updated": True, "account": _mask_brokerage_doc(updated_doc)}
    if data_access_result is not None:
        _out["data_access"] = data_access_result
    return _out


def action_get_portfolio_history(conn, brokerage_id, range_str="1M"):
    """
    Fetch portfolio value history for a linked brokerage account.
    Returns: {account_name, brokerage_type, range, timestamps (ms), values,
              current_value, open_value, change_abs, change_pct, currency}
    """
    brokerage_id = (brokerage_id or "").strip()
    range_str = (range_str or "1M").strip().upper()
    if range_str not in ("1D", "1W", "1M", "3M", "YTD", "1Y", "ALL"):
        raise ValueError(f"Invalid range: {range_str}. Must be one of 1D, 1W, 1M, 3M, YTD, 1Y, ALL")

    _ensure_brokerage_accounts_table(conn)
    doc = r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE).get(brokerage_id).run(conn)
    if doc is None:
        raise ValueError(f"Brokerage account not found: {brokerage_id}")

    btype = doc.get("brokerage_type", "")
    account_name = doc.get("account_name", "Account")

    # Decrypt Fernet-stored credentials before passing to broker APIs.
    try:
        from secret_store import decrypt as _decrypt
    except Exception:
        _decrypt = lambda v: v

    if btype == "alpaca":
        data = _fetch_alpaca_portfolio_history(
            key=_decrypt(doc.get("alpaca_key", "")) or "",
            secret=_decrypt(doc.get("alpaca_secret", "")) or "",
            base_url=doc.get("alpaca_base_url", "https://paper-api.alpaca.markets"),
            range_str=range_str,
        )
    elif btype == "robinhood":
        data = _fetch_robinhood_portfolio_history(
            access_token=_decrypt(doc.get("robinhood_access_token", "")) or "",
            token_type=doc.get("robinhood_token_type", "Bearer"),
            account_number=doc.get("robinhood_account_number", ""),
            range_str=range_str,
        )
    else:
        raise ValueError(f"Unsupported brokerage type: {btype}")

    # Self-heal: a successful history fetch proves the stored creds are valid,
    # so clear any stale "expired" status left over from a prior transient
    # failure (e.g. a credential_service refresh that ran before decryption
    # was wired in). Only touch the row if status actually needs changing.
    if doc.get("status") != "active" or doc.get("last_error"):
        try:
            import datetime as _dt
            r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE).get(brokerage_id).update({
                "status": "active",
                "last_error": None,
                "updated_at": _dt.datetime.utcnow().isoformat(),
            }).run(conn)
        except Exception:
            pass  # best-effort; don't fail the history fetch

    timestamps = data.get("timestamps") or []
    values = data.get("values") or []
    current_value = values[-1] if values else 0.0
    open_value = values[0] if values else 0.0
    change_abs = current_value - open_value
    change_pct = (change_abs / open_value * 100.0) if open_value else 0.0

    return {
        "account_name": account_name,
        "brokerage_type": btype,
        "brokerage_id": brokerage_id,
        "range": range_str,
        "timestamps": timestamps,
        "values": values,
        "current_value": round(current_value, 2),
        "open_value": round(open_value, 2),
        "change_abs": round(change_abs, 2),
        "change_pct": round(change_pct, 4),
        "currency": "USD",
    }


def _fetch_alpaca_portfolio_history(key, secret, base_url, range_str):
    """Call Alpaca /v2/account/portfolio/history and return normalized timestamps+values."""
    import requests as _req
    from datetime import datetime, timezone

    range_map = {
        "1D":  {"period": "1D",  "timeframe": "15Min"},
        "1W":  {"period": "1W",  "timeframe": "1H"},
        "1M":  {"period": "1M",  "timeframe": "1D"},
        "3M":  {"period": "3M",  "timeframe": "1D"},
        "YTD": {"period": "1A",  "timeframe": "1D"},
        "1Y":  {"period": "1A",  "timeframe": "1D"},
        "ALL": {"period": "all", "timeframe": "1W"},
    }
    params = dict(range_map.get(range_str, range_map["1M"]))
    params["intraday_reporting"] = "market_hours"
    params["extended_hours"] = "false"

    try:
        resp = _req.get(
            f"{base_url}/v2/account/portfolio/history",
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            params=params,
            timeout=15,
        )
        if not resp.ok:
            raise ValueError(f"Alpaca API error (HTTP {resp.status_code}): {resp.text[:200]}")
        raw = resp.json()
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Alpaca request failed: {e}")

    timestamps_s = raw.get("timestamp") or []
    equities = raw.get("equity") or []

    pairs = [
        (int(t) * 1000, float(v))
        for t, v in zip(timestamps_s, equities)
        if t is not None and v is not None
    ]

    if range_str == "YTD":
        now = datetime.now(tz=timezone.utc)
        ytd_start_ms = int(datetime(now.year, 1, 1, tzinfo=timezone.utc).timestamp()) * 1000
        pairs = [(t, v) for t, v in pairs if t >= ytd_start_ms]

    return {"timestamps": [p[0] for p in pairs], "values": [p[1] for p in pairs]}


def _fetch_robinhood_intraday_historicals(access_token, token_type, account_number):
    """
    1D fast-path: hit the legacy /portfolios/historicals/{account}/ endpoint
    that powers Robinhood's own web app intraday chart. Returns dense ~5-min
    equity bars across the trading day instead of Bonfire's sparse Bonfire
    cursor-points (which dedupe morning quiet periods into a single anchor).

    Returns {"timestamps": [ms], "values": [float]} or empty dict if RH
    returns no data.
    """
    import requests as _req
    from datetime import datetime, timezone

    if not account_number:
        return {}
    token = f"{(token_type or 'Bearer').strip()} {access_token}".strip()
    headers = {
        "Authorization": token,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    url = f"https://api.robinhood.com/portfolios/historicals/{account_number}/"
    # 5-minute bars across the full trading day with extended hours.
    params = {"interval": "5minute", "span": "day", "bounds": "regular"}
    try:
        resp = _req.get(url, headers=headers, params=params, timeout=15)
        if not resp.ok:
            return {}
        data = resp.json() or {}
    except Exception:
        return {}
    pairs = []
    for h in (data.get("equity_historicals") or []):
        ts_str = h.get("begins_at")
        # Prefer adjusted_close_equity for a rolled-forward equity number;
        # close_equity is the raw bar close.
        amount_str = h.get("adjusted_close_equity") or h.get("close_equity")
        if not ts_str or amount_str is None:
            continue
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            pairs.append((int(dt.timestamp() * 1000), float(amount_str)))
        except (TypeError, ValueError):
            continue
    return {"timestamps": [p[0] for p in pairs], "values": [p[1] for p in pairs]}


def _fetch_robinhood_portfolio_history(access_token, token_type, account_number, range_str):
    """
    Call the Robinhood Bonfire portfolio performance API and return normalized timestamps+values.
    URL: https://bonfire.robinhood.com/portfolio/performance/{account_number}
    Params: chart_style=PERFORMANCE&chart_type=historical_portfolio&display_span={span}&include_all_hours=true
    Response: lines[0].segments[0].points[] where each point has:
      cursor_data.label.value          -> date string "Mar 5, 2025"
      cursor_data.price_chart_data.dollar_value.amount -> portfolio value in USD

    For 1D specifically we first try /portfolios/historicals/ (the legacy
    intraday endpoint that powers RH's own live chart) — Bonfire's cursor-
    point format dedupes morning quiet periods so the 1D chart visually
    starts at the moment positions opened, not at market open.
    """
    import requests as _req
    from datetime import datetime, timezone

    # 1D fast path: try the dense intraday endpoint first.
    if range_str == "1D":
        intra = _fetch_robinhood_intraday_historicals(access_token, token_type, account_number)
        if intra.get("timestamps"):
            return intra
        # Fall through to Bonfire if /portfolios/historicals/ returned empty
        # (some new accounts get gated out of the legacy endpoint).

    # Spans to try in order. ALL tries multiple candidates since the exact
    # value for the max-history span is unknown ("all", "5year", etc.)
    span_candidates = {
        "1D":  ["day"],
        "1W":  ["week"],
        "1M":  ["month"],
        "3M":  ["3month"],
        "YTD": ["year"],
        "1Y":  ["year"],
        "ALL": ["all"],
    }
    spans_to_try = span_candidates.get(range_str, ["month"])

    if not account_number:
        raise ValueError("Robinhood account number is required for portfolio history")

    token = f"{(token_type or 'Bearer').strip()} {access_token}".strip()
    headers = {
        "Authorization": token,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    url = f"https://bonfire.robinhood.com/portfolio/performance/{account_number}"
    raw = None
    last_err = None
    for span in spans_to_try:
        params = {
            "chart_style": "PERFORMANCE",
            "chart_type": "historical_portfolio",
            "display_span": span,
            "include_all_hours": "true",
        }
        try:
            resp = _req.get(url, headers=headers, params=params, timeout=15)
            if not resp.ok:
                last_err = f"Robinhood Bonfire API error (HTTP {resp.status_code}): {resp.text[:300]}"
                continue
            candidate = resp.json()
            # Accept if it has actual point data (check across all segments)
            lines_c = candidate.get("lines") or []
            segs_c = (lines_c[0].get("segments") or []) if lines_c else []
            pts_c = [pt for seg in segs_c for pt in (seg.get("points") or [])]
            if pts_c:
                raw = candidate
                break
            last_err = f"No data returned for display_span={span}"
        except Exception as e:
            last_err = f"Robinhood request failed: {e}"

    if raw is None:
        raise ValueError(last_err or "Robinhood Bonfire API returned no data")

    # Navigate: lines[0].segments[0].points[]
    lines = raw.get("lines") or []
    segments = (lines[0].get("segments") or []) if lines else []
    # Collect points from ALL segments (week/month spans split by day into multiple segments)
    points = []
    for seg in segments:
        points.extend(seg.get("points") or [])

    # Bonfire labels use Eastern Time (US market timezone). We anchor "today"
    # in ET so a user on the West Coast late at night doesn't get UTC's next
    # calendar day stamped onto morning-of-today labels.
    try:
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo("America/New_York")
    except Exception:
        _ET = timezone.utc  # fallback if zoneinfo missing — closer to old behavior
    today_et = datetime.now(tz=_ET)
    # Date/time label formats returned by Bonfire for different spans:
    #   1D:       "9:30 AM", "10:15 AM"         — time only, pin to today (ET)
    #   1W / 1M:  "11:00 PM, Feb 25"            — time + comma + short date (NO year)
    #   3M / 1Y:  "Mar 5, 2025"                 — full date with year
    #   ALL:      "Mar 2025" or "2025"           — month+year or year only
    _DATE_FMTS = [
        ("%b %d, %Y",       False, False),  # "Mar 5, 2025"         — full date
        ("%I:%M %p, %b %d", False, True),   # "11:00 PM, Feb 25"   — time+date, no year (1W/1M)
        ("%b %d, %I:%M %p", False, True),   # "Mar 5, 9:30 AM"     — date+time, no year
        ("%b %d",           False, True),   # "Mar 4"               — short date, use current year
        ("%b %Y",           False, False),  # "Mar 2025"            — month + year
        ("%Y",              False, False),  # "2025"                — year only
        ("%I:%M %p",        True,  False),  # "9:30 AM"             — time only, pin to today (ET)
        ("%H:%M",           True,  False),  # "09:30"               — 24-h time only
    ]

    def _parse_label(label):
        for fmt, time_only, no_year in _DATE_FMTS:
            try:
                dt = datetime.strptime(label, fmt)
                if time_only:
                    # Time-only labels are ET market time. Stamp ET date+tz, convert to UTC.
                    dt = dt.replace(
                        year=today_et.year, month=today_et.month, day=today_et.day,
                        tzinfo=_ET,
                    )
                elif no_year or dt.year == 1900:
                    dt = dt.replace(year=today_et.year, tzinfo=_ET)
                else:
                    dt = dt.replace(tzinfo=_ET)
                return dt.astimezone(timezone.utc)
            except ValueError:
                continue
        return None

    import re as _re
    _DOLLAR_RE = _re.compile(r"[\$]?([\d,]+\.?\d*)")

    def _parse_amount(cursor):
        # Prefer precise amount from price_chart_data.dollar_value.amount
        price_chart = cursor.get("price_chart_data") or {}
        dollar_value = price_chart.get("dollar_value") or {}
        amount_str = dollar_value.get("amount")
        if amount_str is not None:
            try:
                return float(amount_str)
            except Exception:
                pass
        # Fallback: parse primary_value.value e.g. "$6,958.00"
        pv = (cursor.get("primary_value") or {}).get("value", "")
        m = _DOLLAR_RE.search(pv)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except Exception:
                pass
        return None

    pairs = []
    for pt in points:
        cursor = pt.get("cursor_data") or {}
        label = (cursor.get("label") or {}).get("value", "")
        if not label:
            continue
        dt = _parse_label(label)
        amount = _parse_amount(cursor)
        if dt is None or amount is None:
            continue
        pairs.append((int(dt.timestamp() * 1000), amount))

    if range_str == "YTD":
        now = datetime.now(tz=timezone.utc)
        ytd_start_ms = int(datetime(now.year, 1, 1, tzinfo=timezone.utc).timestamp()) * 1000
        pairs = [(t, v) for t, v in pairs if t >= ytd_start_ms]

    # Collapse consecutive flat-value runs caused by market-closed hours
    # (overnight, weekends, or idle pre-trading periods). Keep BOTH endpoints
    # of each flat run so the chart can draw the horizontal segment instead
    # of compressing it to a single point — otherwise an account that was
    # cash-only all morning shows a 1D chart that visually starts when the
    # first position was opened.
    # For 1D specifically we skip dedup entirely; intraday data density is
    # already low and every minute of the trading day matters visually.
    if len(pairs) > 1 and range_str != "1D":
        deduped = []
        n = len(pairs)
        for i, (t, v) in enumerate(pairs):
            is_first = (i == 0)
            is_last = (i == n - 1)
            prev_differs = is_first or abs(pairs[i - 1][1] - v) > 0.001
            next_differs = is_last or abs(pairs[i + 1][1] - v) > 0.001
            # Keep first, last, and any point where a neighbor's value differs.
            # In a flat run [a a a a b]: first a (kept, prev_diff), middle a's
            # dropped, last a (kept, next_diff), b (kept, prev_diff).
            if is_first or is_last or prev_differs or next_differs:
                deduped.append((t, v))
        pairs = deduped

    return {"timestamps": [p[0] for p in pairs], "values": [p[1] for p in pairs]}


def action_refresh_robinhood(conn, brokerage_id):
    """Manually refresh a Robinhood account's tokens."""
    brokerage_id = (brokerage_id or "").strip()
    if not brokerage_id:
        raise ValueError("brokerage_id is required")
    _ensure_brokerage_accounts_table(conn)
    doc = r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE).get(brokerage_id).run(conn)
    if doc is None:
        raise ValueError(f"Brokerage account not found: {brokerage_id}")
    if doc.get("brokerage_type") != "robinhood":
        raise ValueError("This account is not a Robinhood account")

    from robinhood_engine import RobinhoodClient, RobinhoodSessionState, RobinhoodAPIError as _RHErr
    import datetime
    # Decrypt stored tokens before giving to client; encrypt new ones on write.
    try:
        from secret_store import decrypt as _decrypt, encrypt as _encrypt
    except Exception:
        _decrypt = lambda v: v
        _encrypt = lambda v: v
    state = RobinhoodSessionState(
        access_token=_decrypt(doc.get("robinhood_access_token")),
        refresh_token=_decrypt(doc.get("robinhood_refresh_token")),
        token_type=doc.get("robinhood_token_type", "Bearer"),
        expires_in=doc.get("robinhood_expires_in"),
        obtained_at_epoch=doc.get("robinhood_obtained_at_epoch"),
        device_token=_decrypt(doc.get("robinhood_device_token")),
        account_number=doc.get("robinhood_account_number"),
        account_url=doc.get("robinhood_account_url"),
    )
    client = RobinhoodClient(state=state)
    try:
        new_state = client.refresh()
    except _RHErr as e:
        now = datetime.datetime.utcnow().isoformat()
        r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE).get(brokerage_id).update({
            "status": "expired",
            "last_error": str(e.detail or e),
            "updated_at": now,
        }).run(conn)
        raise ValueError(f"Refresh failed: {e.detail or str(e)}")

    now = datetime.datetime.utcnow().isoformat()
    try:
        _enc_access = _encrypt(new_state.access_token)
        _enc_refresh = _encrypt(new_state.refresh_token)
    except RuntimeError:
        _enc_access = new_state.access_token
        _enc_refresh = new_state.refresh_token
    r.db(DB_NAME).table(BROKERAGE_ACCOUNTS_TABLE).get(brokerage_id).update({
        "robinhood_access_token": _enc_access,
        "robinhood_refresh_token": _enc_refresh,
        "robinhood_obtained_at_epoch": new_state.obtained_at_epoch,
        "robinhood_expires_in": new_state.expires_in,
        "status": "active",
        "last_error": None,
        "last_refresh_at": now,
        "updated_at": now,
    }).run(conn)
    return {"refreshed": True, "id": brokerage_id}


# --- Agent Cycle Log ---

AGENT_CYCLE_LOG_TABLE = "AgentCycleLog"


def ensure_agent_cycle_log_table(conn):
    dbs = list(r.db_list().run(conn))
    if DB_NAME not in dbs:
        r.db_create(DB_NAME).run(conn)
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if AGENT_CYCLE_LOG_TABLE not in tables:
        r.db(DB_NAME).table_create(AGENT_CYCLE_LOG_TABLE).run(conn)


def action_agent_cycle_log_create(conn, cycle_id, name):
    ensure_agent_cycle_log_table(conn)
    import uuid
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat() + "Z"
    doc = {
        "id": str(uuid.uuid4()),
        "cycle_id": cycle_id,
        "name": name,
        "created_at": now,
        "updated_at": now,
        "status": "running",
        "stages": [],
        "final_result": None,
    }
    r.db(DB_NAME).table(AGENT_CYCLE_LOG_TABLE).insert(doc).run(conn)
    return {"id": doc["id"]}


def action_agent_cycle_log_update(conn, log_id, status=None, stages=None, final_result=None):
    ensure_agent_cycle_log_table(conn)
    from datetime import datetime, timezone
    update = {"updated_at": datetime.now(timezone.utc).isoformat() + "Z"}
    if status is not None:
        update["status"] = status
    if stages is not None:
        update["stages"] = stages
    if final_result is not None:
        update["final_result"] = final_result
    r.db(DB_NAME).table(AGENT_CYCLE_LOG_TABLE).get(log_id).update(update).run(conn)
    return {"ok": True}


def action_agent_run_force_stop(conn, log_id):
    """Mark a specific agent cycle log entry as stopped (manual cleanup for stale 'running' entries)."""
    from datetime import datetime, timezone
    ensure_agent_cycle_log_table(conn)
    now = datetime.now(timezone.utc).isoformat() + "Z"
    r.db(DB_NAME).table(AGENT_CYCLE_LOG_TABLE).get(log_id).update(
        {"status": "stopped", "updated_at": now, "final_result": "Manually marked as stopped."}
    ).run(conn)
    return {"ok": True, "id": log_id}


def action_list_agent_runs(conn, page=1, per_page=20):
    ensure_agent_cycle_log_table(conn)
    total = r.db(DB_NAME).table(AGENT_CYCLE_LOG_TABLE).count().run(conn)
    offset = (page - 1) * per_page
    cursor = (
        r.db(DB_NAME)
        .table(AGENT_CYCLE_LOG_TABLE)
        .order_by(r.desc("created_at"))
        .skip(offset)
        .limit(per_page)
        .run(conn)
    )
    rows = list(cursor)
    return {
        "runs": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    }


# ---------------------------------------------------------------------------
# Models CRUD  (centralized LLM model configurations)
# ---------------------------------------------------------------------------

MODELS_TABLE = "Models"


def _ensure_models_table(conn):
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if MODELS_TABLE not in tables:
        r.db(DB_NAME).table_create(MODELS_TABLE).run(conn)


def _mask_model_doc(doc):
    """Return a copy with api_key masked for display."""
    d = dict(doc)
    k = d.get("api_key") or ""
    if len(k) > 8:
        d["api_key"] = k[:4] + "****" + k[-4:]
    elif k:
        d["api_key"] = "****"
    return d


def action_list_models(conn):
    _ensure_models_table(conn)
    docs = list(r.db(DB_NAME).table(MODELS_TABLE).run(conn))
    return {"models": [_mask_model_doc(d) for d in docs]}


def action_get_model(conn, model_id):
    _ensure_models_table(conn)
    doc = r.db(DB_NAME).table(MODELS_TABLE).get(model_id).run(conn)
    if doc is None:
        raise ValueError(f"Model not found: {model_id}")
    return _mask_model_doc(doc)


def _validate_claude_cli_extra_args(extra_args):
    """Run the extra_args string through the provider's allowlist validator.
    Raised ValueError on disallowed flags so the API layer can return 400."""
    if extra_args is None:
        return ""
    s = str(extra_args).strip()
    if not s:
        return ""
    try:
        from chatbot.claude_cli_provider import validate_extra_args
    except Exception:
        # If the provider module isn't importable yet (e.g. during a partial
        # migration) we still want to reject obviously-dangerous flags.
        return s
    # Will raise ValueError on a hard-rejected or unknown flag.
    validate_extra_args(s)
    return s


def _validate_codex_cli_extra_args(extra_args):
    """Run codex-cli's extra_args string through the provider's allowlist.
    Mirrors _validate_claude_cli_extra_args but delegates to the codex
    provider's own (different) allowlist."""
    if extra_args is None:
        return ""
    s = str(extra_args).strip()
    if not s:
        return ""
    try:
        from chatbot.codex_cli_provider import validate_extra_args
    except Exception:
        return s
    # validate_extra_args raises CodexCliValidationError; let it propagate
    # (the API layer catches generic Exception and returns 400). Normalize
    # to ValueError so action callers can rely on a stable exception type.
    try:
        validate_extra_args(s)
    except Exception as e:
        raise ValueError(str(e)) from e
    return s


_PROVIDER_MODEL_INCOMPAT_PREFIXES: dict[str, tuple[str, ...]] = {
    "gemini": ("claude-", "gpt-", "o1-", "o3-", "o4-"),
    "openai": ("claude-", "gemini-"),
    "nvidia": ("claude-", "gemini-"),
    "anthropic": ("gpt-", "gemini-", "o1-", "o3-", "o4-"),
    "claude-cli": ("gpt-", "gemini-", "o1-", "o3-", "o4-"),
    "codex-cli": ("claude-", "gemini-"),
    # Azure deliberately omitted — deployment names are operator-defined
    # and can legitimately be any string.
}


def _validate_provider_model_compat(provider: str, model: str) -> None:
    """Reject obviously-incompatible provider+model combinations at save
    time so the operator can't ship a row like ``provider=gemini`` paired
    with ``model=claude-sonnet-4-6`` (which produces 404 NOT_FOUND on
    every call). See ``_PROVIDER_MODEL_INCOMPAT_PREFIXES`` for the per-
    provider blocklist.

    Skips validation for unrecognized providers (forward-compat) and for
    azure (deployment names are arbitrary). Empty values pass — callers
    have separate emptiness handling.

    Raises ValueError with a user-facing hint about the likely correct
    provider for the model.
    """
    p = (provider or "").strip().lower()
    m = (model or "").strip().lower()
    if not p or not m:
        return
    blocklist = _PROVIDER_MODEL_INCOMPAT_PREFIXES.get(p)
    if not blocklist:
        return
    for prefix in blocklist:
        if m.startswith(prefix):
            # Suggest the likely correct provider given the model prefix.
            suggestion_map = {
                "claude-": "'anthropic' or 'claude-cli'",
                "gpt-": "'openai' or 'azure'",
                "o1-": "'openai' or 'azure'",
                "o3-": "'openai' or 'azure'",
                "o4-": "'openai' or 'azure'",
                "gemini-": "'gemini'",
            }
            suggestion = suggestion_map.get(prefix, "the appropriate vendor")
            raise ValueError(
                f"Incompatible provider+model: provider={p!r} does not "
                f"serve models named {model!r} (prefix {prefix!r}). "
                f"Change provider to {suggestion}, or change the model "
                f"name to match {p}. (Azure deployments are exempt — "
                f"if you're using an Azure deployment named like a Claude "
                f"or GPT model, set provider='azure'.)"
            )


def action_create_model(conn, name, provider, model, api_key=None,
                        openai_base_url=None, nvidia_base_url=None,
                        azure_openai_endpoint=None, azure_openai_api_version=None,
                        reasoning_effort=None,
                        cli_path=None, extra_args=None,
                        ollama_base_url=None, ollama_keep_alive=None,
                        ollama_think=None,
                        bedrock_region=None, bedrock_reasoning=None,
                        model_cache_family=None,
                        input_cost_per_1m=None, output_cost_per_1m=None,
                        cache_creation_cost_per_1m=None,
                        cache_read_cost_per_1m=None):
    _ensure_models_table(conn)
    provider_n = (provider or "").strip().lower()
    _validate_provider_model_compat(provider_n, model)
    extra_args_clean = ""
    if provider_n == "claude-cli":
        extra_args_clean = _validate_claude_cli_extra_args(extra_args)
    elif provider_n == "codex-cli":
        extra_args_clean = _validate_codex_cli_extra_args(extra_args)
    now = datetime.datetime.utcnow().isoformat() + "Z"
    doc = {
        "name": (name or "").strip(),
        "provider": provider_n,
        "model": (model or "").strip(),
        "api_key": (api_key or "").strip(),
        "openai_base_url": (openai_base_url or "").strip(),
        "nvidia_base_url": (nvidia_base_url or "").strip(),
        "azure_openai_endpoint": (azure_openai_endpoint or "").strip(),
        "azure_openai_api_version": (azure_openai_api_version or "2024-10-21").strip(),
        "reasoning_effort": (reasoning_effort or "").strip(),
        "cli_path": (cli_path or "").strip(),
        "extra_args": extra_args_clean,
        # Ollama-specific (empty strings for non-ollama rows is fine; the
        # dispatcher only reads them when provider == "ollama").
        "ollama_base_url": (ollama_base_url or "").strip(),
        "ollama_keep_alive": (ollama_keep_alive or "").strip(),
        "ollama_think": (ollama_think or "").strip().lower(),
        # Bedrock-specific (empty for non-bedrock rows; the dispatcher only
        # reads them when provider == "bedrock"). region is required at call
        # time; reasoning is "off"/"low"/"medium"/"high".
        "bedrock_region": (bedrock_region or "").strip(),
        "bedrock_reasoning": (bedrock_reasoning or "").strip().lower(),
        # Cache-grouping tag (canonical_model_cache_key override).
        "model_cache_family": (model_cache_family or "").strip().lower(),
        # Optional per-model pricing override ($/1M tokens). None means
        # "use llm_pricing.yaml defaults" at cost-computation time.
        "input_cost_per_1m": input_cost_per_1m,
        "output_cost_per_1m": output_cost_per_1m,
        "cache_creation_cost_per_1m": cache_creation_cost_per_1m,
        "cache_read_cost_per_1m": cache_read_cost_per_1m,
        "created_at": now,
        "updated_at": now,
    }
    result = r.db(DB_NAME).table(MODELS_TABLE).insert(doc).run(conn)
    doc["id"] = result["generated_keys"][0]
    return {"created": True, "model": _mask_model_doc(doc)}


def action_edit_model(conn, model_id, **kwargs):
    _ensure_models_table(conn)
    doc = r.db(DB_NAME).table(MODELS_TABLE).get(model_id).run(conn)
    if doc is None:
        raise ValueError(f"Model not found: {model_id}")
    # If the resulting provider is claude-cli, validate extra_args before
    # persisting. We re-run the allowlist check against whichever value
    # would end up on disk: the incoming kwargs override, or the existing
    # value already in the doc. This catches a case where the user only
    # changes the provider (e.g. ``gemini`` → ``claude-cli``) and leaves a
    # stale ``extra_args`` in place that would never have been valid for
    # the new provider — saving that combination would have left a banned
    # flag persisted because validation only ran when ``extra_args`` itself
    # was in kwargs.
    new_provider = (kwargs.get("provider") or doc.get("provider") or "").strip().lower()
    # Validate the EFFECTIVE provider+model combination after applying
    # incoming kwargs over the existing doc. Without this, an operator
    # could edit just one of the two fields and leave the row in an
    # incompatible state (e.g. flip provider to 'gemini' while keeping
    # model='claude-sonnet-4-6'). Mirrors the extra_args validator
    # immediately below.
    effective_model = (
        kwargs["model"] if ("model" in kwargs and kwargs["model"] is not None)
        else doc.get("model")
    )
    _validate_provider_model_compat(new_provider, effective_model)
    if new_provider == "claude-cli":
        effective_extra = (
            kwargs["extra_args"] if ("extra_args" in kwargs and kwargs["extra_args"] is not None)
            else doc.get("extra_args")
        )
        if effective_extra:
            validated = _validate_claude_cli_extra_args(effective_extra)
            # Persist the canonical (validator-emitted) value so any
            # numeric/enum rewrites land in the doc.
            kwargs["extra_args"] = validated
    elif new_provider == "codex-cli":
        effective_extra = (
            kwargs["extra_args"] if ("extra_args" in kwargs and kwargs["extra_args"] is not None)
            else doc.get("extra_args")
        )
        if effective_extra:
            validated = _validate_codex_cli_extra_args(effective_extra)
            kwargs["extra_args"] = validated
    # The four cost-override fields special-case explicit None as "clear
    # the override" so a user can remove a previously-set pricing override
    # from the UI. All other fields treat None as "leave unchanged" (the
    # historical contract; callers omit fields they don't want to touch).
    _PRICING_FIELDS = (
        "input_cost_per_1m", "output_cost_per_1m",
        "cache_creation_cost_per_1m", "cache_read_cost_per_1m",
    )
    update = {"updated_at": datetime.datetime.utcnow().isoformat() + "Z"}
    for field in ("name", "provider", "model", "api_key", "openai_base_url",
                  "nvidia_base_url", "azure_openai_endpoint",
                  "azure_openai_api_version", "reasoning_effort",
                  "cli_path", "extra_args",
                  "ollama_base_url", "ollama_keep_alive", "ollama_think",
                  "bedrock_region", "bedrock_reasoning", "model_cache_family",
                  "input_cost_per_1m", "output_cost_per_1m",
                  "cache_creation_cost_per_1m", "cache_read_cost_per_1m"):
        if field not in kwargs:
            continue
        val = kwargs[field]
        if val is None:
            if field in _PRICING_FIELDS:
                # Clear the override.
                update[field] = None
            # Otherwise leave the doc field untouched.
            continue
        if isinstance(val, str):
            val = val.strip()
            if field == "provider":
                val = val.lower()
        update[field] = val
    r.db(DB_NAME).table(MODELS_TABLE).get(model_id).update(update).run(conn)
    updated = r.db(DB_NAME).table(MODELS_TABLE).get(model_id).run(conn)
    # If the conversation is using a claude-cli session with this model,
    # the session manager will close + respawn on the next turn when it
    # notices the model/system_prompt/extra_args signature changed.
    # Invalidate runtime cache (model_resolver doc cache + llm_utils
    # terminal-failure cache). Without the latter, a user who fixed a
    # misconfigured provider+model would keep hitting the suppression
    # short-circuit until the worker restarted — the very symptom we're
    # trying to avoid by surfacing terminal errors clearly.
    try:
        from model_resolver import invalidate_model_cache
        invalidate_model_cache(model_id)
    except Exception:
        pass
    try:
        from llm_utils import invalidate_terminal_failure_cache
        prev_provider = (doc.get("provider") or "").strip().lower()
        prev_model = (doc.get("model") or "").strip()
        invalidate_terminal_failure_cache(prev_provider, prev_model)
        # Also clear by the NEW values in case the user swapped to a
        # different combo that itself had been terminal previously.
        new_provider = (updated.get("provider") or "").strip().lower()
        new_model = (updated.get("model") or "").strip()
        if (new_provider, new_model) != (prev_provider, prev_model):
            invalidate_terminal_failure_cache(new_provider, new_model)
    except Exception:
        pass
    return {"updated": True, "model": _mask_model_doc(updated)}


def _find_strategies_referencing_model(conn, model_id):
    """Return list of strategies that reference the given model_id."""
    strategies = list(r.db(DB_NAME).table("Strategies").run(conn))
    result = []
    for strat in strategies:
        for sub in (strat.get("strategies") or []):
            cfg = sub.get("config") or {}
            for key, val in cfg.items():
                if key.endswith("llm_model_id") and val == model_id:
                    result.append(strat)
                    break
            else:
                continue
            break
    return result


def _restore_inline_from_model(conn, model_id, model_doc):
    """Before force-deleting a model, restore inline credentials into referencing strategies."""
    strategies = list(r.db(DB_NAME).table("Strategies").run(conn))
    for strat in strategies:
        changed = False
        for sub in (strat.get("strategies") or []):
            cfg = sub.get("config") or {}
            for key in list(cfg.keys()):
                if not key.endswith("llm_model_id") or cfg[key] != model_id:
                    continue
                prefix = key[: -len("llm_model_id")]
                cfg[f"{prefix}llm_provider"] = model_doc.get("provider", "")
                if prefix:
                    cfg[f"{prefix}llm_model"] = model_doc.get("model", "")
                else:
                    cfg["model_name"] = model_doc.get("model", "")
                cfg[f"{prefix}llm_api_key"] = model_doc.get("api_key", "")
                if (model_doc.get("provider") or "").lower() == "azure":
                    cfg[f"{prefix}azure_openai_api_key"] = model_doc.get("api_key", "")
                    cfg[f"{prefix}azure_openai_endpoint"] = model_doc.get("azure_openai_endpoint", "")
                    cfg[f"{prefix}azure_openai_api_version"] = model_doc.get("azure_openai_api_version", "2024-10-21")
                if (model_doc.get("provider") or "").lower() in ("claude-cli", "codex-cli"):
                    if model_doc.get("cli_path"):
                        cfg[f"{prefix}cli_path"] = model_doc["cli_path"]
                    if model_doc.get("extra_args"):
                        cfg[f"{prefix}extra_args"] = model_doc["extra_args"]
                if model_doc.get("openai_base_url"):
                    cfg[f"{prefix}openai_base_url"] = model_doc["openai_base_url"]
                if model_doc.get("nvidia_base_url"):
                    cfg[f"{prefix}nvidia_base_url"] = model_doc["nvidia_base_url"]
                if model_doc.get("reasoning_effort"):
                    cfg[f"{prefix}llm_reasoning_effort"] = model_doc["reasoning_effort"]
                del cfg[key]
                changed = True
            sub["config"] = cfg
        if changed:
            r.db(DB_NAME).table("Strategies").get(strat["id"]).update(
                {"strategies": strat["strategies"]}
            ).run(conn)


def action_delete_model(conn, model_id, force=False):
    _ensure_models_table(conn)
    doc = r.db(DB_NAME).table(MODELS_TABLE).get(model_id).run(conn)
    if doc is None:
        return {"deleted": False, "id": model_id}
    referencing = _find_strategies_referencing_model(conn, model_id)
    if referencing and not force:
        names = [s["name"] for s in referencing]
        raise ValueError(
            f"Model is used by strategy(ies): {', '.join(names)}. "
            "Pass force=true to delete anyway (inline credentials will be restored)."
        )
    if referencing and force:
        _restore_inline_from_model(conn, model_id, doc)
    r.db(DB_NAME).table(MODELS_TABLE).get(model_id).delete().run(conn)
    try:
        from model_resolver import invalidate_model_cache
        invalidate_model_cache(model_id)
    except Exception:
        pass
    try:
        from llm_utils import invalidate_terminal_failure_cache
        invalidate_terminal_failure_cache(
            (doc.get("provider") or "").strip().lower(),
            (doc.get("model") or "").strip(),
        )
    except Exception:
        pass
    return {"deleted": True, "id": model_id}


def action_resolve_model_for_runtime(conn, model_id):
    """Return full (unmasked) model doc for runtime use."""
    _ensure_models_table(conn)
    return r.db(DB_NAME).table(MODELS_TABLE).get(model_id).run(conn)


def action_model_strategies(conn, model_id):
    """Return strategies referencing this model."""
    _ensure_models_table(conn)
    refs = _find_strategies_referencing_model(conn, model_id)
    return {"strategies": [{"id": s["id"], "name": s["name"]} for s in refs]}
