"""Chatbot turn orchestration.

The user posts a message; we run the LLM↔tool loop until either a final
assistant text answer is produced or a non-safe tool needs confirmation,
at which point we persist the partial state and return to the client so
the UI can prompt the user.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from rethinkdb import RethinkDB

_log = logging.getLogger(__name__)

from chatbot import conversations as conv_store
from chatbot.llm import call_chat_with_tools
from chatbot.system_prompt import build_system_message
from chatbot.tools import all_tools, get_tool, openai_tool_definitions
from interactive_utils import action_list_brokerages, action_list_models, action_strategies, action_instances, action_list_backtests, action_get_model


r = RethinkDB()
DB_NAME = os.environ.get("INTELLISTOCK_DB_NAME", "IntelliStock")
MODELS_TABLE = "Models"


def _fetch_model_doc(conn, model_id: str):
    """Fetch the raw model doc (with the unmasked api_key) from RethinkDB."""
    if not model_id:
        return None
    try:
        return r.db(DB_NAME).table(MODELS_TABLE).get(model_id).run(conn)
    except Exception:
        return None

# Maximum LLM↔tool round-trips per user turn before we force a stop and
# return whatever text the assistant has produced so far. Keeps a runaway
# tool-call loop from spinning forever.
MAX_TOOL_ROUNDS = 6
LLM_TIMEOUT_SEC = 120
# Reasoning-capable models on OpenAI / Azure (o-series, gpt-5 family) burn
# their token budget on hidden reasoning before emitting any visible content
# OR a tool call. With reasoning_effort=high on a small model, 2048 tokens
# can be entirely consumed by reasoning and leave 0 for the user-facing
# response — the chat shows an empty assistant bubble. Default headroom is
# now 8192 so reasoning + output both fit.
MAX_OUTPUT_TOKENS = 8192


# ── Workspace counts (cheap, used in the system prompt) ───────────────────


def _gather_counts(conn) -> Dict[str, int]:
    out: Dict[str, int] = {}
    try:
        out["models"] = len(action_list_models(conn) or [])
    except Exception:
        pass
    try:
        out["brokerages"] = len(action_list_brokerages(conn) or [])
    except Exception:
        pass
    try:
        out["instances"] = len(action_instances(conn) or [])
    except Exception:
        pass
    try:
        out["strategies"] = len(action_strategies(conn) or [])
    except Exception:
        pass
    try:
        bt = action_list_backtests(conn, page=1, per_page=1)
        out["backtests"] = int((bt or {}).get("total") or 0)
    except Exception:
        pass
    return out


# ── Model resolution ──────────────────────────────────────────────────────


def _resolve_model(conn, model_id: Optional[str]) -> Dict[str, Any]:
    """Return a dict with provider / model / api_key / base_url / etc.

    Raises ValueError if the model can't be resolved.
    """
    if not model_id:
        raise ValueError("This conversation has no model selected — pick one in the chatbot settings.")
    doc = _fetch_model_doc(conn, model_id)
    if not doc:
        raise ValueError(f"Model {model_id} not found. Open the chatbot settings to pick another.")
    provider = (doc.get("provider") or "gemini").strip().lower()
    if provider == "claude-cli":
        # claude-cli uses the locally-installed `claude` binary; there's no
        # API key to resolve, just a cli_path + optional extra_args.
        from chatbot.claude_cli_provider import validate_extra_args
        try:
            extra_args = validate_extra_args(doc.get("extra_args") or "")
        except ValueError as e:
            raise ValueError(
                f"Model has invalid extra_args: {e}. Edit it on the Models page."
            ) from e
        return {
            "provider": "claude-cli",
            "model": doc.get("model") or "",
            "model_name": doc.get("name") or "",
            "api_key": "",  # not used
            "cli_path": (doc.get("cli_path") or "claude").strip() or "claude",
            "extra_args": extra_args,
            "base_url": None,
            "azure_endpoint": None,
            "azure_api_version": None,
            "reasoning_effort": doc.get("reasoning_effort"),
        }
    api_key = doc.get("api_key") or ""
    if api_key:
        from secret_store import decrypt_required
        api_key = decrypt_required(api_key, field="Models.api_key")
    if not api_key:
        # Fallback to env vars via resolver pattern; we keep this simple here
        # because the chatbot can't accept env-based keys silently.
        from llm_utils import resolve_api_key_for_provider  # local import to avoid cycle on cold start
        api_key = resolve_api_key_for_provider(doc.get("provider") or "", "")
    if not api_key:
        raise ValueError("This model has no API key configured. Edit it on the Models page.")
    return {
        "provider": provider,
        "model": doc.get("model") or "",
        "model_name": doc.get("name") or "",
        "api_key": api_key,
        "base_url": doc.get("openai_base_url") or doc.get("nvidia_base_url") or doc.get("openrouter_base_url"),
        "azure_endpoint": doc.get("azure_openai_endpoint"),
        "azure_api_version": doc.get("azure_openai_api_version"),
        "reasoning_effort": doc.get("reasoning_effort"),
    }


# ── Tool execution ────────────────────────────────────────────────────────


def _execute_tool(conn, user: Dict[str, Any], name: str, args: Dict[str, Any]) -> Tuple[bool, Any]:
    """Returns (ok, result). result is a JSON-serialisable value."""
    tool = get_tool(name)
    if not tool:
        return False, {"error": f"unknown tool: {name}"}
    try:
        result = tool.executor({"conn": conn, "user": user}, args or {})
        return True, result
    except Exception as e:
        return False, {"error": str(e)}


def _summarise_tool_result(value: Any, max_len: int = 4000) -> str:
    """Turn a tool result into a string the LLM can read. Long arrays are
    truncated heavily — the LLM only needs to know what shape came back so
    it can pass it on to a render tool, not memorize every datapoint.

    For ``get_price_history`` / ``get_portfolio_history``-shaped payloads
    we keep the metadata and only the first/last few points, so the LLM
    has enough context to write a summary line ("here's SNDK's 1M chart")
    without burning thousands of tokens on raw OHLC data.
    """
    if isinstance(value, dict):
        # Compact known time-series shapes so the LLM doesn't see hundreds
        # of points it doesn't need to render anyway.
        if isinstance(value.get("results"), dict) and value.get("range") is not None:
            slim_results = {}
            for sym, points in value["results"].items():
                if isinstance(points, list) and len(points) > 8:
                    slim_results[sym] = (
                        list(points[:3]) + [f"... ({len(points) - 6} more points omitted)"] + list(points[-3:])
                    )
                else:
                    slim_results[sym] = points
            value = {**value, "results": slim_results}
        elif isinstance(value.get("timestamps"), list) and isinstance(value.get("values"), list):
            n = len(value["values"])
            if n > 8:
                value = {
                    **value,
                    "timestamps": value["timestamps"][:3] + ["... omitted ..."] + value["timestamps"][-3:],
                    "values": value["values"][:3] + ["... omitted ..."] + value["values"][-3:],
                    "_total_points": n,
                }

    try:
        text = json.dumps(value, default=str)
    except Exception:
        text = str(value)
    if len(text) > max_len:
        text = text[:max_len] + "... (truncated)"
    return text


_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
_API_KEY_LIKE = re.compile(r"\b(?:sk|nvapi|pk|gsk|gho)[_-][A-Za-z0-9]{12,}\b")


def _sanitise_provider_error(err: Exception) -> str:
    """Strip URLs / api keys before persisting an LLM error message into the
    user-visible assistant content. Provider error bodies sometimes echo
    the request, including the model id and (rarely) auth headers."""
    raw = str(err)
    safe = _URL_RE.sub("[redacted-url]", raw)
    safe = _API_KEY_LIKE.sub("[redacted-key]", safe)
    if len(safe) > 240:
        safe = safe[:240] + "..."
    return safe


# ── LLM call loop ─────────────────────────────────────────────────────────


def _llm_messages_for_call(history: List[Dict[str, Any]], system: str) -> List[Dict[str, Any]]:
    """Build the LLM-facing messages list. Strips chatbot-only metadata,
    expands tool messages, and defensively scrubs any orphan tool_calls /
    tool messages.

    OpenAI / Azure 400 if an assistant message has tool_calls without
    matching tool messages immediately following (or vice-versa). A pre-
    existing corrupt conversation (e.g., a tool result that failed to
    persist) would dead-end every subsequent turn — so we drop the orphan
    tool_calls (downgrading the assistant message to a plain text turn)
    and drop any tool message whose tool_call_id has no parent.
    """
    # First pass: keep only roles we care about and skip pending state.
    raw: List[Dict[str, Any]] = []
    for m in history:
        role = m.get("role")
        if role not in ("user", "assistant", "tool"):
            continue
        if m.get("status") == "pending_confirmation":
            continue
        raw.append(m)

    # Index every tool message by tool_call_id so we can verify each
    # assistant.tool_calls[].id is followed by a matching tool message.
    # Build a forward map from tool_call_id → index of the assistant message
    # that owns it; the matching tool message must come AFTER that index.
    pending_call_ids: Dict[str, int] = {}  # tool_call_id → assistant index
    valid_call_ids: set = set()
    for i, m in enumerate(raw):
        if m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                cid = tc.get("id")
                if cid:
                    pending_call_ids[cid] = i
        elif m.get("role") == "tool":
            cid = m.get("tool_call_id")
            if cid and cid in pending_call_ids and pending_call_ids[cid] < i:
                valid_call_ids.add(cid)

    out: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    for m in raw:
        role = m.get("role")
        if role == "assistant":
            tcs = m.get("tool_calls") or []
            if tcs:
                kept = [tc for tc in tcs if tc.get("id") in valid_call_ids]
                if not kept:
                    # All tool_calls are orphaned — downgrade to plain text.
                    out.append({"role": "assistant", "content": m.get("content") or ""})
                    continue
                out.append({
                    "role": "assistant",
                    "content": m.get("content") or "",
                    "tool_calls": kept,
                })
                continue
            out.append({"role": "assistant", "content": m.get("content") or ""})
            continue
        if role == "tool":
            cid = m.get("tool_call_id")
            if cid not in valid_call_ids:
                # Orphan tool result — drop it so OpenAI/Azure doesn't 400.
                continue
            out.append({
                "role": "tool",
                "tool_call_id": cid,
                "name": m.get("name") or "",
                "content": m.get("content") or "",
            })
            continue
        out.append({"role": role, "content": m.get("content") or ""})
    return out


_CLAUDE_CLI_MCP_HINT = (
    "\n\n=== Tool calling via Claude Code (MCP bridge) ===\n"
    "IntelliStock's chatbot tools are exposed to you through an MCP "
    "server. Their names appear in your tool list prefixed with "
    "``mcp__intellistock__`` (e.g. ``mcp__intellistock__fetch_quote``). "
    "Call them like any other tool when the user's request needs live "
    "data, charts, navigation, or backend state.\n\n"
    "Read-only and render tools (fetch_quote, render_price_chart, "
    "list_*, get_*) execute immediately and the result returns "
    "synchronously. Action tools (write, destructive — placing trades, "
    "starting instances, deleting backtests, etc.) may be queued for "
    "user confirmation. You can tell the difference by reading the "
    "tool error message:\n"
    "  * **ONLY** if the error contains the exact phrase \"queued for "
    "user confirmation\", reply by acknowledging that you've requested "
    "the action and the user needs to approve or decline it in the UI. "
    "Do NOT retry in the same turn.\n"
    "  * For ANY OTHER tool error (\"unknown tool\", \"user not "
    "found\", connection refused, etc.), the tool simply failed. "
    "Report the error to the user honestly — do NOT pretend the action "
    "was queued or that the user needs to approve anything.\n"
)


def _call_llm(
    model_cfg: Dict[str, Any],
    history: List[Dict[str, Any]],
    system: str,
    with_tools: bool = True,
    *,
    conversation_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    provider = (model_cfg.get("provider") or "").strip().lower()
    if provider == "claude-cli":
        # claude-cli runs as a persistent subprocess per conversation
        # with the IntelliStock MCP server attached, so CC can call our
        # tools via ``mcp__intellistock__*`` and get IntelliStock to
        # dispatch them. We append a short hint to the system prompt
        # describing the queue-on-confirm semantics — without it, the
        # model retries non-safe tools in a loop on every "tool was
        # queued" error.
        from chatbot.claude_cli_provider import call_claude_cli_chat
        return call_claude_cli_chat(
            conversation_id=conversation_id or "",
            messages=_llm_messages_for_call(history, system),
            system_prompt=(system or "") + _CLAUDE_CLI_MCP_HINT,
            model=model_cfg["model"],
            user_id=user_id or "",
            cli_path=model_cfg.get("cli_path") or "claude",
            extra_args=model_cfg.get("extra_args") or [],
            reasoning_effort=model_cfg.get("reasoning_effort"),
            timeout_sec=LLM_TIMEOUT_SEC,
        )
    return call_chat_with_tools(
        provider=model_cfg["provider"],
        api_key=model_cfg["api_key"],
        model=model_cfg["model"],
        messages=_llm_messages_for_call(history, system),
        tools_openai=openai_tool_definitions() if with_tools else None,
        base_url=model_cfg.get("base_url"),
        azure_endpoint=model_cfg.get("azure_endpoint"),
        azure_api_version=model_cfg.get("azure_api_version"),
        reasoning_effort=model_cfg.get("reasoning_effort"),
        timeout_sec=LLM_TIMEOUT_SEC,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )


# ── Public entry: run a turn ──────────────────────────────────────────────


def _run_loop(
    conn,
    user: Dict[str, Any],
    convo: Dict[str, Any],
    history: List[Dict[str, Any]],
    appended: List[Dict[str, Any]],
    model_cfg: Dict[str, Any],
    system: str,
    auto_confirm_safe: bool,
    *,
    conv_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Drive the LLM↔tool loop. Mutates ``history`` and ``appended`` in place
    and returns ``appended``. Used by both run_turn and resume-after-tool."""
    # The claude-cli provider needs the conversation_id to route turns to
    # the right persistent subprocess. Fall back to the doc id if the
    # caller didn't pass it explicitly.
    conv_id_eff = conv_id or convo.get("id") or ""
    user_id_eff = str(user.get("id") or "")
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            result = _call_llm(
                model_cfg, history, system, with_tools=True,
                conversation_id=conv_id_eff, user_id=user_id_eff,
            )
        except Exception as e:
            _log.warning("chatbot LLM call failed: %s", e, exc_info=False)
            err_msg = conv_store.new_message(
                "assistant",
                content=f"⚠️ The model call failed: {_sanitise_provider_error(e)}",
            )
            appended.append(err_msg)
            history.append(err_msg)
            break

        tool_calls = result.get("tool_calls") or []
        text = (result.get("content") or "").strip()
        finish_reason = (result.get("finish_reason") or "").strip().lower()
        # Render blocks (charts, tables, navigate, ...) populated by the
        # claude-cli MCP path. The OpenAI-style provider returns no blocks
        # here (tools get a dedicated round of orchestration); for CC,
        # MCP tool execution happens inside the provider's persistent
        # subprocess, so the blocks come back attached to the final result.
        provider_blocks = list(result.get("blocks") or [])

        if not tool_calls:
            # Empty content + no tool calls is almost always a token-budget
            # exhaustion on a reasoning model (the hidden reasoning tokens
            # ate the whole max_completion_tokens). Make the silent failure
            # visible to the user and the LLM (next round can recover).
            if not text:
                hint = (
                    " (model returned no output — likely ran out of tokens; "
                    "try a smaller range, fewer symbols, or lower reasoning effort)"
                    if finish_reason in ("length", "max_tokens", "max_completion_tokens", "")
                    else f" (finish_reason: {finish_reason})"
                )
                asst = conv_store.new_message(
                    "assistant",
                    content="⚠️ I couldn't generate a reply for this turn." + hint,
                )
            else:
                asst = conv_store.new_message("assistant", content=text)
            if provider_blocks:
                asst["blocks"] = provider_blocks
            appended.append(asst)
            history.append(asst)
            break

        # Identify which calls need user confirmation.
        non_safe: List[Tuple[Dict[str, Any], Any]] = []
        for tc in tool_calls:
            tool = get_tool(tc.get("name") or "")
            if not tool:
                continue
            if tool.safety != "safe" or (not auto_confirm_safe and not tool.render_only):
                non_safe.append((tc, tool))

        if non_safe:
            # Surface only the FIRST non-safe call. Drop sibling calls so we
            # don't end up with assistant.tool_calls with N ids but only 1
            # tool result (which OpenAI 400s on). The LLM can re-issue the
            # other calls on the next turn after the user resolves this one.
            tc, tool = non_safe[0]
            pending = {
                "tool_call_id": tc["id"],
                "name": tc["name"],
                "arguments": tc.get("arguments") or {},
                "safety": tool.safety,
                "description": tool.description,
            }
            asst = conv_store.new_message(
                "assistant",
                content=text,
                tool_calls=[{
                    "id": tc["id"],
                    "name": tc["name"],
                    "arguments": tc.get("arguments") or {},
                }],
                pending_tool=pending,
                status="pending_confirmation",
            )
            appended.append(asst)
            break

        # All calls are safe → execute them all in this round.
        asst = conv_store.new_message(
            "assistant",
            content=text,
            tool_calls=[
                {"id": tc["id"], "name": tc["name"], "arguments": tc.get("arguments") or {}}
                for tc in tool_calls
            ],
        )
        appended.append(asst)
        history.append(asst)

        for tc in tool_calls:
            ok, value = _execute_tool(conn, user, tc.get("name") or "", tc.get("arguments") or {})
            tool_msg, blocks = _build_tool_result_message(tc, ok, value)
            if blocks:
                tool_msg["blocks"] = blocks
            appended.append(tool_msg)
            history.append(tool_msg)
    else:
        appended.append(conv_store.new_message(
            "assistant",
            content="(stopped: reached max tool rounds for this turn — try asking again with a tighter scope)",
        ))

    return appended


def run_turn(
    conn,
    user: Dict[str, Any],
    conv_id: str,
    user_message: str,
) -> List[Dict[str, Any]]:
    """Append the user's message and drive the LLM↔tool loop. Returns the
    list of newly-appended messages (so the client can render the delta)."""
    user_id = user["id"]
    convo = conv_store.get_conversation(conn, user_id, conv_id)
    if not convo:
        raise ValueError("conversation not found")

    model_cfg = _resolve_model(conn, convo.get("model_id"))
    counts = _gather_counts(conn)
    system = build_system_message(
        user=user,
        counts=counts,
        extra_settings=convo.get("settings") or {},
    )

    user_msg = conv_store.new_message("user", content=user_message)
    appended: List[Dict[str, Any]] = [user_msg]
    history = list(convo.get("messages") or []) + [user_msg]
    auto_confirm_safe = bool(((convo.get("settings") or {}).get("auto_confirm_safe_tools", True)))

    _run_loop(conn, user, convo, history, appended, model_cfg, system, auto_confirm_safe, conv_id=conv_id)

    conv_store.append_messages(conn, user_id, conv_id, appended)
    # Auto-title brand-new conversations.
    if (convo.get("title") or "New conversation") == "New conversation" and user_message:
        title = user_message.strip().splitlines()[0][:80]
        if title:
            conv_store.update_conversation(conn, user_id, conv_id, title=title)
    return appended


def _build_tool_result_message(tc: Dict[str, Any], ok: bool, value: Any) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Build a 'tool' role message that the LLM can read, plus any UI blocks
    extracted from render-only tools."""
    blocks: List[Dict[str, Any]] = []
    payload: Any = value
    if isinstance(value, dict) and value.get("type") == "block":
        blocks.append(value["block"])
        payload = {"ok": True, "rendered": value["block"].get("type")}
    if isinstance(value, dict) and value.get("type") == "navigate":
        blocks.append({"type": "navigate", "route": value.get("route")})
        payload = {"ok": True, "navigated_to": value.get("route")}
    msg = conv_store.new_message(
        "tool",
        content=_summarise_tool_result(payload if ok else {"error": value}),
        tool_call_id=tc.get("id"),
        name=tc.get("name") or "",
    )
    return msg, blocks


# ── Public entry: confirm a pending tool ──────────────────────────────────


def confirm_pending_tool(
    conn,
    user: Dict[str, Any],
    conv_id: str,
    pending_message_id: str,
    approved: bool,
) -> List[Dict[str, Any]]:
    user_id = user["id"]
    convo = conv_store.get_conversation(conn, user_id, conv_id)
    if not convo:
        raise ValueError("conversation not found")
    pending_msg = conv_store.find_message(convo, pending_message_id)
    if not pending_msg or pending_msg.get("status") != "pending_confirmation":
        raise ValueError("no pending tool found on that message")
    pending = pending_msg.get("pending_tool") or {}
    tc_id = pending.get("tool_call_id")
    name = pending.get("name") or ""
    args = pending.get("arguments") or {}

    appended: List[Dict[str, Any]] = []

    if not approved:
        # Replace the pending message with a final assistant message + a tool
        # message that tells the LLM the user declined, so subsequent calls
        # have context.
        resolved_msg = {
            **pending_msg,
            "status": "complete",
            "pending_tool": None,
            "tool_calls": pending_msg.get("tool_calls") or [
                {"id": tc_id, "name": name, "arguments": args}
            ],
        }
        conv_store.replace_message(conn, user_id, conv_id, pending_message_id, resolved_msg)
        decline_msg = conv_store.new_message(
            "tool",
            content=json.dumps({"error": "user_declined", "tool": name}),
            tool_call_id=tc_id,
            name=name,
        )
        appended.append(decline_msg)
        # Resume the conversation: ask the LLM for a follow-up text answer.
        return _resume_after_tool_results(conn, user, conv_id, appended)

    # Approved → execute now
    ok, value = _execute_tool(conn, user, name, args)
    tool_msg, blocks = _build_tool_result_message(
        {"id": tc_id, "name": name},
        ok,
        value,
    )
    if blocks:
        tool_msg["blocks"] = blocks
    # Promote pending message to complete (carry tool_calls for context)
    resolved_msg = {
        **pending_msg,
        "status": "complete",
        "pending_tool": None,
        "tool_calls": pending_msg.get("tool_calls") or [
            {"id": tc_id, "name": name, "arguments": args}
        ],
    }
    conv_store.replace_message(conn, user_id, conv_id, pending_message_id, resolved_msg)
    appended.append(tool_msg)
    return _resume_after_tool_results(conn, user, conv_id, appended)


def _resume_after_tool_results(
    conn,
    user: Dict[str, Any],
    conv_id: str,
    appended_so_far: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    user_id = user["id"]
    convo = conv_store.get_conversation(conn, user_id, conv_id)
    if not convo:
        return appended_so_far
    model_cfg = _resolve_model(conn, convo.get("model_id"))
    counts = _gather_counts(conn)
    system = build_system_message(user=user, counts=counts, extra_settings=convo.get("settings") or {})
    history = list(convo.get("messages") or []) + appended_so_far
    auto_confirm_safe = bool(((convo.get("settings") or {}).get("auto_confirm_safe_tools", True)))

    new_appends = list(appended_so_far)
    _run_loop(conn, user, convo, history, new_appends, model_cfg, system, auto_confirm_safe, conv_id=conv_id)
    # Persist the full new_appends — confirm_pending_tool only persisted the
    # pending→complete promotion via replace_message, so the tool result
    # message it passed in `appended_so_far` is NOT yet in the DB. Saving
    # only the suffix (the previous behaviour) left the assistant.tool_calls
    # with no matching tool result on disk, which Azure / OpenAI 400 on the
    # next turn ("tool_call_ids did not have response messages").
    conv_store.append_messages(conn, user_id, conv_id, new_appends)
    return new_appends
