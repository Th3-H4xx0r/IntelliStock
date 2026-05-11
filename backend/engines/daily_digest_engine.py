"""
Daily Digest Engine: sends scheduled briefs to Discord (#briefs channel).
- 6:00 Los Angeles time: morning digest (AI backtesting agent summary; AI-generated newsletter via LLM).
- 18:00 Los Angeles time: evening digest (end-of-day recap of AI backtesting activity).

Controlled via DigestControl in RethinkDB: running (start/stop scheduled sends), send_now (trigger immediate send).
API: GET/POST /digest/control, POST /digest/send-now. CLI: digest start | stop | send-now | status. Discord: !digest start | stop | send-now | status.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

# Run from backend/engines/; backend is parent for imports and cwd (must be before any backend imports)
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
os.chdir(_backend_dir)

import pytz
from rethinkdb import RethinkDB

r = RethinkDB()
DB_NAME = 'IntelliStock'

try:
    from intellistock_logger import intellistock_logger
    def _log(msg, color="white"):
        intellistock_logger.log(msg, color, service="DIGEST_ENGINE")
except Exception:
    def _log(msg, color="white"):
        print(f"[DIGEST_ENGINE] {msg}")

from interactive_utils import (
    get_conn,
    ensure_digest_control_table,
    action_digest_control_get,
    action_digest_mark_sent,
    DIGEST_CONTROL_ID,
    ensure_agent_control_table,
    action_agent_control_get,
    action_list_ai_backtest_results_since,
    action_agent_get_best,
)
from discord_sender import enqueue_discord_message

DIGEST_CHANNEL = "briefs"
LA_TIMEZONE = pytz.timezone("America/Los_Angeles")
MORNING_HOUR_LA = 6
EVENING_HOUR_LA = 18
POLL_INTERVAL_SEC = 60
EMBED_DESCRIPTION_MAX = 4096

# Threading lock to prevent concurrent digest sends (main loop + changefeed thread)
_send_lock = threading.Lock()

# In-memory guard: tracks which digest types have been sent today to prevent repeat sends
# even if the DB timestamp check fails (e.g. due to format issues).
_sent_today: dict[str, str] = {}  # kind -> date string "YYYY-MM-DD"


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _now_la() -> datetime:
    """Get current time in LA timezone (always timezone-aware)."""
    return datetime.now(timezone.utc).astimezone(LA_TIMEZONE)


def _get_digest_llm_config() -> dict:
    """Build LLM config for digest from env: AI_DIGEST_PROVIDER, AI_DIGEST_MODEL, AI_DIGEST_API_KEY. Fallback to GEMINI/DEEPSEEK."""
    provider = (_env("AI_DIGEST_PROVIDER") or "gemini").strip().lower()
    if provider not in ("gemini", "deepseek"):
        provider = "gemini"
    model = _env("AI_DIGEST_MODEL") or ("gemini-2.0-flash" if provider == "gemini" else "deepseek-chat")
    api_key = _env("AI_DIGEST_API_KEY") or (_env("GEMINI_API_KEY") if provider == "gemini" else _env("DEEPSEEK_API_KEY"))
    return {"provider": provider, "model": model, "api_key": api_key}


def _call_llm(prompt: str, config: dict) -> str | None:
    """Call LLM (Gemini or DeepSeek). Returns response text or None."""
    provider = (config.get("provider") or "gemini").strip().lower()
    model = (config.get("model") or "").strip()
    api_key = (config.get("api_key") or "").strip()
    if not api_key or not model:
        return None
    if provider == "deepseek":
        return _call_llm_deepseek(prompt, model, api_key)
    return _call_llm_gemini(prompt, model, api_key)


def _call_llm_gemini(prompt: str, model: str, api_key: str) -> str | None:
    try:
        url = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s" % (model, api_key)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.5, "maxOutputTokens": 2048},
        }
        req_module = __import__("urllib.request", fromlist=["request"])
        req = req_module.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        with req_module.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for c in data.get("candidates", []):
                for p in c.get("content", {}).get("parts", []):
                    if "text" in p:
                        return (p["text"] or "").strip()
        return None
    except Exception as e:
        _log("Gemini digest LLM failed: %s" % e, "red")
        return None


def _call_llm_deepseek(prompt: str, model: str, api_key: str) -> str | None:
    try:
        url = "https://api.deepseek.com/v1/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2048,
            "temperature": 0.5,
        }
        req_module = __import__("urllib.request", fromlist=["request"])
        req = req_module.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer %s" % api_key},
            method="POST",
        )
        with req_module.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for choice in data.get("choices", []):
                msg = choice.get("message") or {}
                if isinstance(msg.get("content"), str):
                    return msg["content"].strip()
        return None
    except Exception as e:
        _log("DeepSeek digest LLM failed: %s" % e, "red")
        return None


# ── Data gathering ────────────────────────────────────────────────────────────

def _gather_digest_data(conn, since_hours: int = 24) -> dict:
    """Gather raw stats for a digest. Used for both morning and evening briefs."""
    ensure_agent_control_table(conn)
    agent = action_agent_control_get(conn)
    count_today = int(agent.get("count_today") or 0)
    last_run_date = agent.get("last_run_date") or "—"

    since_iso = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    since_resp = action_list_ai_backtest_results_since(conn, since_iso, limit=200)
    results = since_resp.get("results") or []
    kept = len(results)

    # Top 5 results by profit
    sorted_results = sorted(
        [r for r in results if r.get("overall_profit") is not None],
        key=lambda x: float(x.get("overall_profit", 0)),
        reverse=True,
    )[:5]
    top_results = []
    for r in sorted_results:
        snap = r.get("strategy_snapshot") or {}
        name = snap.get("name") or "?"
        subs = snap.get("strategies") or []
        sub_names = [s.get("strategy", "?") for s in subs[:5]]
        top_results.append({
            "name": name,
            "profit": float(r.get("overall_profit", 0)),
            "pnl_percent": float(r.get("pnl_percent", 0)),
            "sub_strategies": sub_names,
        })

    # Worst results (bottom 3)
    worst_results = sorted(
        [r for r in results if r.get("overall_profit") is not None],
        key=lambda x: float(x.get("overall_profit", 0)),
    )[:3]
    bottom_results = []
    for r in worst_results:
        name = (r.get("strategy_snapshot") or {}).get("name") or "?"
        bottom_results.append({
            "name": name,
            "profit": float(r.get("overall_profit", 0)),
            "pnl_percent": float(r.get("pnl_percent", 0)),
        })

    # Current best strategy
    best_doc = action_agent_get_best(conn)
    best_name = (best_doc.get("strategy_snapshot") or {}).get("name") if best_doc else None
    best_profit = float(best_doc.get("overall_profit") or 0) if best_doc else 0
    best_pnl_pct = float(best_doc.get("pnl_percent") or 0) if best_doc else 0
    best_updated = best_doc.get("updated_at") if best_doc else None
    new_best = False
    if best_updated:
        try:
            best_dt = datetime.fromisoformat(str(best_updated).replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - best_dt).total_seconds() < since_hours * 3600:
                new_best = True
        except Exception:
            pass

    # Strategy composition counts (which sub-strategies appear most)
    strat_counts: dict[str, int] = {}
    for r in results:
        subs = (r.get("strategy_snapshot") or {}).get("strategies") or []
        for s in subs:
            sid = (s.get("strategy") or "").strip()
            if sid:
                strat_counts[sid] = strat_counts.get(sid, 0) + 1
    most_used = sorted(strat_counts.items(), key=lambda x: -x[1])[:5]

    # Average profit across all results
    profits = [float(r.get("overall_profit", 0)) for r in results if r.get("overall_profit") is not None]
    avg_profit = sum(profits) / len(profits) if profits else 0
    profitable_count = sum(1 for p in profits if p > 0)

    now_la = _now_la()
    return {
        "period_hours": since_hours,
        "count_today": count_today,
        "last_run_date": last_run_date,
        "total_kept": kept,
        "profitable_count": profitable_count,
        "avg_profit": round(avg_profit, 2),
        "top_results": top_results,
        "bottom_results": bottom_results,
        "best_name": best_name,
        "best_profit": best_profit,
        "best_pnl_pct": best_pnl_pct,
        "new_best": new_best,
        "most_used_strategies": most_used,
        "time_la": now_la.strftime("%Y-%m-%d %I:%M %p PT"),
        "time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# ── LLM newsletter generation ────────────────────────────────────────────────

def _generate_newsletter_llm(data: dict, kind: str, llm_config: dict) -> str | None:
    """Ask the LLM to write a newsletter-style digest. kind is 'morning' or 'evening'."""
    if kind == "morning":
        time_label = "Morning"
        intro = "You are writing the **Morning Newsletter** for IntelliStock, a trading platform's Discord channel. This is the start-of-day brief summarizing overnight AI backtesting activity."
    else:
        time_label = "Evening"
        intro = "You are writing the **Evening Newsletter** for IntelliStock, a trading platform's Discord channel. This is the end-of-day recap wrapping up today's AI backtesting activity."

    prompt = f"""{intro}

Write a polished, newsletter-style summary using Discord markdown formatting. Structure it as follows:

1. **Opening line**: A one-sentence overview of the day's activity (e.g. "The AI agent ran X backtests today, keeping Y strategies...")
2. **Performance Highlights**: Top performing strategies with their P&L. Mention sub-strategy composition if interesting.
3. **Trends**: Which sub-strategies appeared most frequently? Any patterns?
4. **Current Best Strategy**: Name, P&L, and whether it was newly set.
5. **Quick Stats**: A compact line with key numbers (total kept, profitable %, avg profit).

Use bold (**text**) for emphasis, bullet points (•) for lists. Keep it concise but informative — 3-5 short paragraphs max. Write in a professional but engaging tone, like a financial newsletter. Do NOT use markdown headers (#). Do NOT invent data — use ONLY what's provided below.

Data:
{json.dumps(data, indent=2)}

Output only the newsletter text, no preamble or labels."""

    _log("Generating %s newsletter with LLM (%s, %s)..." % (kind, llm_config.get("provider"), llm_config.get("model")), "cyan")
    out = _call_llm(prompt, llm_config)
    if out:
        return out.strip()[:EMBED_DESCRIPTION_MAX]
    return None


def _fallback_description(data: dict, kind: str) -> str:
    """Template fallback when LLM is unavailable."""
    label = "Morning" if kind == "morning" else "Evening"
    desc = (
        "**%s Recap** — The AI backtesting agent ran **%d** backtests, keeping **%d** strategies (%d profitable). "
        "Average P&L: **$%.2f**."
    ) % (label, data["count_today"], data["total_kept"], data["profitable_count"], data["avg_profit"])
    if data.get("new_best") and data.get("best_name"):
        desc += " A **new best strategy** was set: %s ($%.2f, %.2f%%)." % (
            data["best_name"], data["best_profit"], data["best_pnl_pct"])
    elif data.get("best_name"):
        desc += " Current best: %s ($%.2f)." % (data["best_name"], data["best_profit"])
    return desc


# ── Build digest embeds ───────────────────────────────────────────────────────

def _build_digest(conn, kind: str) -> dict:
    """Build a digest embed (morning or evening). Always tries LLM first for newsletter format."""
    since_hours = 24 if kind == "morning" else 12  # evening covers since morning
    data = _gather_digest_data(conn, since_hours=since_hours)

    llm_config = _get_digest_llm_config()
    description = None
    if llm_config.get("api_key") and llm_config.get("model"):
        description = _generate_newsletter_llm(data, kind, llm_config)
        if description:
            _log("LLM newsletter generated successfully (%d chars)" % len(description), "green")
        else:
            _log("LLM newsletter generation failed; using template fallback", "yellow")
    else:
        _log("No LLM API key configured for digest; using template fallback", "yellow")

    if not description:
        description = _fallback_description(data, kind)

    # Build embed fields
    top_results = data.get("top_results") or []
    highlight_lines = []
    for r in top_results:
        subs = r.get("sub_strategies") or []
        sub_str = " (%s)" % ", ".join(subs) if subs else ""
        highlight_lines.append("• **%s**%s — $%.2f (%.2f%%)" % (r["name"][:40], sub_str, r["profit"], r["pnl_percent"]))

    fields = []
    if highlight_lines:
        fields.append({
            "name": "Top Results",
            "value": "\n".join(highlight_lines),
            "inline": False,
        })
    if data.get("best_name"):
        best_val = "**%s** — $%.2f (%.2f%%)" % (data["best_name"][:50], data["best_profit"], data["best_pnl_pct"])
        if data.get("new_best"):
            best_val += " ✨ **NEW**"
        fields.append({"name": "Current Best Strategy", "value": best_val, "inline": False})

    # Quick stats
    most_used = data.get("most_used_strategies") or []
    mu_str = ", ".join("%s (%d)" % (s, c) for s, c in most_used[:4]) if most_used else "—"
    fields.append({"name": "Most Used Sub-Strategies", "value": mu_str, "inline": False})
    fields.append({"name": "Backtests Today", "value": str(data["count_today"]), "inline": True})
    fields.append({"name": "Profitable", "value": "%d/%d" % (data["profitable_count"], data["total_kept"]), "inline": True})
    fields.append({"name": "Avg P&L", "value": "$%.2f" % data["avg_profit"], "inline": True})

    if kind == "morning":
        title = "☀️ Morning Brief — AI Backtesting"
        color = 0x3498DB
    else:
        title = "🌙 Evening Recap — AI Backtesting"
        color = 0x9B59B6

    return {
        "title": title,
        "description": description[:EMBED_DESCRIPTION_MAX],
        "color": color,
        "fields": fields,
        "footer": {"text": data["time_la"]},
    }


# ── Send functions ────────────────────────────────────────────────────────────

def _send_digest(conn, kind: str) -> bool:
    """Build and enqueue a digest to Discord. Thread-safe via _send_lock. Returns True if sent."""
    if not _send_lock.acquire(blocking=False):
        _log("%s digest send skipped — another send is already in progress" % kind.capitalize(), "yellow")
        return False
    try:
        embed = _build_digest(conn, kind)
        enqueue_discord_message(conn, DIGEST_CHANNEL, "", embed=embed)
        action_digest_mark_sent(conn, kind)
        # Set in-memory guard so we don't re-send within the same day
        _sent_today[kind] = _now_la().strftime("%Y-%m-%d")
        _log("%s digest sent to #%s" % (kind.capitalize(), DIGEST_CHANNEL), "green")
        return True
    except Exception as e:
        _log("Failed to send %s digest: %s" % (kind, e), "red")
        return False
    finally:
        _send_lock.release()


def _send_morning_digest(conn) -> bool:
    return _send_digest(conn, "morning")


def _send_evening_digest(conn) -> bool:
    return _send_digest(conn, "evening")


# ── Schedule checks ───────────────────────────────────────────────────────────

def _should_send(control: dict, kind: str, target_hour: int) -> bool:
    """Check if a digest should be sent. Uses timezone-aware datetime throughout."""
    now_la = _now_la()
    if now_la.hour != target_hour or now_la.minute >= 15:
        return False
    # In-memory guard: if we already sent this kind today, skip
    today_str = now_la.strftime("%Y-%m-%d")
    if _sent_today.get(kind) == today_str:
        return False
    last_key = "last_%s_at" % kind
    last = control.get(last_key)
    if not last:
        return True
    try:
        last_str = str(last)
        # Handle both "...Z" and "...+00:00" formats
        if last_str.endswith("Z"):
            last_str = last_str[:-1] + "+00:00"
        last_dt = datetime.fromisoformat(last_str)
        # Compare in LA timezone to handle day boundaries correctly
        last_la = last_dt.astimezone(LA_TIMEZONE)
        return last_la.date() < now_la.date()
    except Exception:
        _log("Warning: could not parse last_%s_at=%r; allowing send" % (kind, last), "yellow")
        return True


def _should_send_morning(control: dict) -> bool:
    return _should_send(control, "morning", MORNING_HOUR_LA)


def _should_send_evening(control: dict) -> bool:
    return _should_send(control, "evening", EVENING_HOUR_LA)


def run_digest_send_now_change(change, conn):
    """Handle EngineControl.daily_digest_engine changefeed changes, specifically for send_now flag."""
    try:
        new_val = change.get('new_val')
        if new_val is None:
            return

        send_now = new_val.get('send_now', False)
        old_send_now = change.get('old_val', {}).get('send_now', False) if change.get('old_val') else False

        # Only send when send_now changes from false to true
        if send_now and not old_send_now:
            _log("Send-now triggered via changefeed — sending digest immediately.", "green")
            try:
                _send_morning_digest(conn)
            except Exception as e:
                _log("Error sending digest via changefeed: %s" % e, "red")
    except Exception as e:
        _log("Error in digest send_now changefeed handler: %s" % e, "red")


def run_digest_send_now_changefeed():
    """Run changefeed on EngineControl.daily_digest_engine to listen for send_now triggers."""
    max_attempts = 30
    delay = 2
    for attempt in range(1, max_attempts + 1):
        conn = None
        try:
            conn = get_conn()
            _log("Starting DigestControl changefeed for send_now monitoring.", "cyan")

            from interactive_utils import ensure_digest_control_table
            ensure_digest_control_table(conn)

            from engine_control import ENGINE_CONTROL_TABLE
            _log("Listening for changes on EngineControl document ID: %s" % DIGEST_CONTROL_ID, "white")
            doc = r.db(DB_NAME).table(ENGINE_CONTROL_TABLE).get(DIGEST_CONTROL_ID).run(conn)
            if doc and doc.get('send_now'):
                _log("Send-now already set on startup — sending digest immediately.", "green")
                try:
                    _send_morning_digest(conn)
                except Exception as e:
                    _log("Error sending digest on startup: %s" % e, "red")
            _log("EngineControl (digest) changefeed started — waiting for send_now changes.", "green")
            for change in r.db(DB_NAME).table(ENGINE_CONTROL_TABLE).get(DIGEST_CONTROL_ID).changes().run(conn):
                run_digest_send_now_change(change, conn)
            break
        except Exception as e:
            err_str = str(e).lower()
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            if "primary replica" in err_str or "not available" in err_str:
                if attempt < max_attempts:
                    _log("EngineControl digest changefeed failed (attempt %d/%d): %s. Retrying in %ds..." % (
                        attempt, max_attempts, e, delay), "yellow")
                    time.sleep(delay)
                    delay = min(delay * 1.5, 30)
                else:
                    _log("EngineControl digest changefeed failed after %d attempts. Giving up." % max_attempts, "red")
                    raise
            else:
                _log("EngineControl digest changefeed error: %s" % e, "red")
                raise
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass


def main():
    _log("Daily digest engine started. Channel: #%s. Schedule: %d:00 and %d:00 Los Angeles time." % (DIGEST_CHANNEL, MORNING_HOUR_LA, EVENING_HOUR_LA), "cyan")

    # Start changefeed thread for immediate send_now response
    send_now_feed_thread = threading.Thread(target=run_digest_send_now_changefeed, daemon=True)
    send_now_feed_thread.start()
    _log("Send-now changefeed thread started for immediate responsiveness.", "cyan")

    conn = None
    while True:
        try:
            if conn is None:
                conn = get_conn()
            ensure_digest_control_table(conn)
            control = action_digest_control_get(conn)

            if not control.get("running", True):
                _log("Digest engine stopped by control (running=false). Exiting.", "yellow")
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass
                sys.exit(0)

            # Scheduled digest checks (send_now is handled by changefeed thread)
            if _should_send_morning(control):
                _log("Sending scheduled morning digest", "green")
                _send_morning_digest(conn)
                # Re-fetch control immediately to update last_morning_at before next check
                control = action_digest_control_get(conn)
            if _should_send_evening(control):
                _log("Sending scheduled evening digest", "green")
                _send_evening_digest(conn)
                control = action_digest_control_get(conn)

        except Exception as e:
            _log("Loop error: %s" % e, "red")
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
            conn = None
            time.sleep(POLL_INTERVAL_SEC)
            continue

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
