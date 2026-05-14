"""End-to-end claude-cli dispatch tests without the real binary.

Covers the gap the bug-sweep agents flagged: no test exercised the
strategy → llm_utils → claude-cli path before this. Production hit a
silent regression (backtest #336915 burning ~25s/cycle on Gemini 404s)
because graph_nexus_analysis._normalize_llm_provider had a hardcoded
whitelist that coerced 'claude-cli' to 'gemini'. These tests pin the
expected behavior so a future whitelist edit can't reintroduce it.

We mock the leaf function that spawns the ``claude`` subprocess so the
tests run anywhere without needing the binary, a login, or an internet
connection.
"""
from __future__ import annotations

import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


class _DummyOutput(BaseModel):
    text: str
    score: float


@pytest.fixture
def mock_claude_cli_structured():
    """Patch ``call_claude_cli_structured`` in claude_cli_provider so
    the test never spawns a subprocess. Returns the mock so individual
    tests can assert the call arguments."""
    with patch("chatbot.claude_cli_provider.call_claude_cli_structured") as m:
        m.return_value = _DummyOutput(text="ok", score=0.5)
        yield m


# ── 1) llm_utils dispatch ────────────────────────────────────────────────


def test_call_structured_llm_routes_claude_cli_branch(mock_claude_cli_structured):
    """call_structured_llm_by_provider must hand claude-cli off to the
    bespoke branch — NOT through PydanticAI / Gemini / OpenAI."""
    from llm_utils import call_structured_llm_by_provider

    out = call_structured_llm_by_provider(
        "claude-cli",
        "ignored-api-key",
        "claude-sonnet-4-6",
        prompt="classify this article",
        output_type=_DummyOutput,
        provider_config={"cli_path": "claude", "extra_args": ""},
    )

    assert out is not None
    assert out.text == "ok"
    # Subprocess-spawning function was called exactly once.
    assert mock_claude_cli_structured.call_count == 1
    kwargs = mock_claude_cli_structured.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs.get("cli_path") in ("claude", None)


def test_claude_cli_branch_records_terminal_on_not_logged_in():
    """When the bespoke branch surfaces ClaudeCliNotLoggedInError, the
    metadata must flag is_terminal=True so callers can skip retry/split
    loops."""
    from chatbot.claude_cli_provider import ClaudeCliNotLoggedInError
    from llm_utils import (
        call_structured_llm_by_provider,
        get_last_structured_llm_call_metadata,
    )

    with patch("chatbot.claude_cli_provider.call_claude_cli_structured") as m:
        m.side_effect = ClaudeCliNotLoggedInError("not logged in")
        out = call_structured_llm_by_provider(
            "claude-cli",
            "ignored",
            "claude-sonnet-4-6",
            prompt="…",
            output_type=_DummyOutput,
            provider_config={"cli_path": "claude"},
        )

    assert out is None
    meta = get_last_structured_llm_call_metadata()
    assert meta.get("is_terminal") is True
    assert meta.get("ok") is False


# ── 2) graph_nexus_analysis normalizer ───────────────────────────────────


@pytest.mark.parametrize(
    "provider,expected",
    [
        ("claude-cli", "claude-cli"),
        ("anthropic", "anthropic"),
        ("CLAUDE-CLI", "claude-cli"),
        ("gemini", "gemini"),
        ("azure", "azure"),
        ("openai", "openai"),
        ("nvidia", "nvidia"),
        ("deepseek", "deepseek"),
        # Unknown providers still coerce to gemini — that's the
        # intentional safe-default.
        ("unknown-provider", "gemini"),
        ("", "gemini"),
    ],
)
def test_graph_nexus_normalize_provider_preserves_claude_cli(provider, expected):
    """REGRESSION: silent coercion of 'claude-cli' → 'gemini' caused the
    backtest #336915 thrash. Pin the normalizer's contract."""
    from strategies.graph_nexus_analysis import _normalize_llm_provider

    assert _normalize_llm_provider(provider) == expected


def test_graph_nexus_default_api_key_returns_sentinel_for_claude_cli():
    """``if not api_key: return`` short-circuits in role callers would
    skip the pipeline if we returned empty for claude-cli. The sentinel
    string keeps the truthiness check passing."""
    from strategies.graph_nexus_analysis import _default_api_key_for_provider

    out = _default_api_key_for_provider("claude-cli")
    assert out  # non-empty
    assert out == "claude-cli-no-api-key"


def test_graph_nexus_default_model_for_claude_cli():
    from strategies.graph_nexus_analysis import _default_model_for_provider

    assert _default_model_for_provider("claude-cli") == "claude-sonnet-4-6"
    assert _default_model_for_provider("anthropic") == "claude-sonnet-4-6"


# ── 3) Same checks for the four other strategies/engines that had the bug


@pytest.mark.parametrize(
    "module_path",
    [
        "strategies.earnings",
        "strategies.ml_news",
        "engines.ai_backtest_engine",
    ],
)
def test_other_modules_normalize_claude_cli(module_path):
    """earnings, ml_news, and ai_backtest_engine each had their own
    copy of the silent-coerce bug. Make sure each module's normalizer
    preserves claude-cli."""
    import importlib

    mod = importlib.import_module(module_path)
    assert hasattr(mod, "_normalize_llm_provider"), f"{module_path} missing _normalize_llm_provider"
    assert mod._normalize_llm_provider("claude-cli") == "claude-cli"
    assert mod._normalize_llm_provider("anthropic") == "anthropic"
    # API-key sentinel for claude-cli — keeps the ``if not api_key`` short-circuits from firing.
    assert mod._default_api_key_for_provider("claude-cli") == "claude-cli-no-api-key"


# ── 4) Provider+model save-time compatibility check ──────────────────────


@pytest.mark.parametrize(
    "provider,model",
    [
        ("gemini", "claude-sonnet-4-6"),
        ("gemini", "gpt-4o"),
        ("gemini", "o3-mini"),
        ("openai", "claude-3-5-sonnet"),
        ("openai", "gemini-1.5-pro"),
        ("nvidia", "claude-opus-4"),
        ("anthropic", "gpt-4o"),
        ("anthropic", "gemini-pro"),
        ("claude-cli", "o1-preview"),
        # case-insensitive
        ("gemini", "Claude-Sonnet-4-6"),
        ("CLAUDE-CLI", "GPT-4o"),
    ],
)
def test_validate_provider_model_compat_rejects_mismatches(provider, model):
    from interactive_utils import _validate_provider_model_compat

    with pytest.raises(ValueError, match="Incompatible provider"):
        _validate_provider_model_compat(provider, model)


@pytest.mark.parametrize(
    "provider,model",
    [
        # Correct combos
        ("gemini", "gemini-2.0-flash"),
        ("openai", "gpt-4o"),
        ("openai", "o4-mini"),
        ("anthropic", "claude-sonnet-4-6"),
        ("claude-cli", "claude-opus-4-1"),
        ("nvidia", "meta/llama-3.3-70b-instruct"),
        # Azure exempt — deployment names are operator-defined
        ("azure", "claude-sonnet-4-6"),
        ("azure", "anything-goes"),
        # Unknown providers pass through (forward-compat)
        ("future-provider", "anything"),
        # Empty values pass — separate emptiness handling
        ("", "claude-sonnet-4-6"),
        ("gemini", ""),
    ],
)
def test_validate_provider_model_compat_passes_valid(provider, model):
    from interactive_utils import _validate_provider_model_compat

    # Should not raise
    _validate_provider_model_compat(provider, model)


# ── 5) Test-endpoint reasoning_effort plumbing ───────────────────────────


def test_build_llm_test_provider_config_includes_reasoning_effort_for_claude_cli():
    """The ``/test-llm-config`` endpoint's provider-config builder was
    silently dropping reasoning_effort for claude-cli before this fix.
    Make sure 'high' flows through."""
    pytest.importorskip("fastapi")  # api.main needs FastAPI; skip locally if not installed
    from api.main import _build_llm_test_provider_config

    body = types.SimpleNamespace(
        provider="claude-cli",
        api_key="",
        openai_base_url=None,
        nvidia_base_url=None,
        azure_openai_endpoint=None,
        azure_openai_api_version=None,
        reasoning_effort="high",
        cli_path="claude",
        extra_args="",
    )
    cfg = _build_llm_test_provider_config(body)
    assert cfg.get("reasoning_effort") == "high"


# ── 6) Output-repair pipeline ────────────────────────────────────────────


class _Wrapper(BaseModel):
    items: list[_DummyOutput]


def test_repair_unwraps_single_key_inner_json():
    """CC sometimes wraps the schema-conforming JSON inside a single-key
    envelope like ``{"final": "{\"text\":...}"}``. The repair pipeline
    should unwrap it before raising Pydantic validation error."""
    from chatbot.claude_cli_provider import _try_repair_payload_for_schema

    payload = {"final": '{"text": "hello", "score": 0.42}'}
    out = _try_repair_payload_for_schema(_DummyOutput, payload)
    assert out is not None
    assert out.text == "hello"
    assert out.score == 0.42


def test_repair_coerces_bare_list_into_wrapper_schema():
    """``_coerce_structured_output_shape`` wraps a bare list into the
    expected single-field wrapper schema."""
    from chatbot.claude_cli_provider import _try_repair_payload_for_schema

    raw_list = [{"text": "a", "score": 1.0}, {"text": "b", "score": 2.0}]
    out = _try_repair_payload_for_schema(_Wrapper, raw_list)
    assert out is not None
    assert len(out.items) == 2
    assert out.items[0].text == "a"


def test_repair_returns_none_when_no_strategy_works():
    """Genuinely broken payloads should return None so the caller raises
    the original ClaudeCliValidationError with the payload preview."""
    from chatbot.claude_cli_provider import _try_repair_payload_for_schema

    out = _try_repair_payload_for_schema(_DummyOutput, {"completely": "wrong"})
    assert out is None


# ── 7) Rate-limit detection + Discord alert dedupe ───────────────────────


def test_classify_envelope_with_429_status_returns_ratelimit_even_without_needle():
    """REGRESSION: CC's actual rate-limit body is
    ``"You've hit your limit · resets 11:50pm (UTC)"`` and none of the old
    text-only needles matched it. Backtest #336915 burned ~25 s per cycle
    on misclassified 429s. Status-code-first classification fixes this."""
    from chatbot.claude_cli_provider import (
        ClaudeCliRateLimitError,
        _classify_error_envelope,
    )

    envelope = {
        "is_error": True,
        "api_error_status": 429,
        # Deliberately a phrase that has none of the legacy needles —
        # if the classifier still picks RateLimitError, the fix works.
        "result": "Service unavailable, try again later",
    }
    err = _classify_error_envelope(envelope)
    assert isinstance(err, ClaudeCliRateLimitError)


def test_classify_envelope_with_hit_your_limit_text_returns_ratelimit():
    """Even without ``api_error_status``, the new text needle
    ``hit your limit`` must catch CC's actual phrasing."""
    from chatbot.claude_cli_provider import (
        ClaudeCliRateLimitError,
        _classify_error_envelope,
    )

    envelope = {
        "is_error": True,
        "result": "You've hit your limit · resets 11:50pm (UTC)",
    }
    err = _classify_error_envelope(envelope)
    assert isinstance(err, ClaudeCliRateLimitError)


def test_classify_envelope_status_as_string_429_still_classifies_correctly():
    """Some serializers emit ``api_error_status`` as a string. Coerce
    before comparing — otherwise ``"429" == 429`` is False in Python."""
    from chatbot.claude_cli_provider import (
        ClaudeCliRateLimitError,
        _classify_error_envelope,
    )

    envelope = {"is_error": True, "api_error_status": "429", "result": "nope"}
    err = _classify_error_envelope(envelope)
    assert isinstance(err, ClaudeCliRateLimitError)


def test_classify_envelope_non_429_falls_back_to_text_classifier():
    """500s and other non-429 statuses must fall through to the existing
    text-based classifier so we don't mis-classify generic CC failures
    as rate limits."""
    from chatbot.claude_cli_provider import (
        ClaudeCliError,
        ClaudeCliRateLimitError,
        _classify_error_envelope,
    )

    envelope = {
        "is_error": True,
        "api_error_status": 500,
        "result": "internal server error",
    }
    err = _classify_error_envelope(envelope)
    assert isinstance(err, ClaudeCliError)
    assert not isinstance(err, ClaudeCliRateLimitError)


def test_emit_rate_limit_discord_alert_dedupes_within_process():
    """Chunk-fallback can fire 14+ doomed spawns inside a few seconds.
    The Discord alert must dedupe by the parsed reset time so the
    operator gets a single notification per quota window."""
    from chatbot import claude_cli_provider as ccp

    ccp._reset_rate_limit_alert_dedupe_for_tests()

    enqueue_calls = []

    def _fake_enqueue(conn, channel, content, embed=None, **_kw):
        enqueue_calls.append((channel, embed))
        return {"id": "fake", "channel": channel, "status": "pending"}

    fake_conn = MagicMock()
    fake_conn.close = MagicMock()

    with patch("interactive_utils.get_conn", return_value=fake_conn), patch(
        "discord_sender.enqueue_discord_message", side_effect=_fake_enqueue
    ):
        msg = "You've hit your limit · resets 11:50pm (UTC)"
        ccp._emit_rate_limit_discord_alert(msg)
        ccp._emit_rate_limit_discord_alert(msg)  # same reset → skip
        ccp._emit_rate_limit_discord_alert(msg)  # same reset → skip

    assert len(enqueue_calls) == 1, (
        f"Expected exactly one Discord enqueue for the same reset window, "
        f"got {len(enqueue_calls)}"
    )
    channel, embed = enqueue_calls[0]
    assert channel == "notifications"
    assert "rate limit" in (embed.get("title") or "").lower()
    # Reset time must surface in the embed so the operator knows when to
    # expect normal operation to resume.
    embed_text = json.dumps(embed)
    assert "11:50pm" in embed_text


def test_emit_rate_limit_discord_alert_sends_again_for_different_reset_time():
    """Each fresh reset window deserves its own alert — otherwise the
    operator gets stuck on a stale one if quota refreshes and then hits
    the wall again."""
    from chatbot import claude_cli_provider as ccp

    ccp._reset_rate_limit_alert_dedupe_for_tests()

    enqueue_calls = []

    def _fake_enqueue(conn, channel, content, embed=None, **_kw):
        enqueue_calls.append((channel, embed))
        return {"id": "fake", "channel": channel, "status": "pending"}

    fake_conn = MagicMock()
    fake_conn.close = MagicMock()

    with patch("interactive_utils.get_conn", return_value=fake_conn), patch(
        "discord_sender.enqueue_discord_message", side_effect=_fake_enqueue
    ):
        ccp._emit_rate_limit_discord_alert(
            "You've hit your limit · resets 11:50pm (UTC)"
        )
        ccp._emit_rate_limit_discord_alert(
            "You've hit your limit · resets 4:30am (UTC)"
        )

    assert len(enqueue_calls) == 2


def test_emit_rate_limit_discord_alert_swallows_db_failures():
    """If RethinkDB is down, the alert helper must NOT raise — a Discord
    outage cannot be allowed to break a backtest."""
    from chatbot import claude_cli_provider as ccp

    ccp._reset_rate_limit_alert_dedupe_for_tests()

    with patch(
        "interactive_utils.get_conn", side_effect=RuntimeError("rdb down")
    ):
        # Must not raise.
        ccp._emit_rate_limit_discord_alert(
            "You've hit your limit · resets 11:50pm (UTC)"
        )


def test_llm_utils_marks_ratelimit_as_terminal_and_does_not_retry():
    """If the dispatcher hits a rate-limit, retrying immediately just
    burns more 429s before reset. Must record ``is_terminal=True`` and
    issue only ONE underlying call no matter the retry budget."""
    from chatbot.claude_cli_provider import ClaudeCliRateLimitError
    from llm_utils import (
        call_structured_llm_by_provider,
        get_last_structured_llm_call_metadata,
    )

    with patch("chatbot.claude_cli_provider.call_claude_cli_structured") as m:
        m.side_effect = ClaudeCliRateLimitError(
            "You've hit your limit · resets 11:50pm (UTC)"
        )
        out = call_structured_llm_by_provider(
            "claude-cli",
            "ignored",
            "claude-sonnet-4-6",
            prompt="…",
            output_type=_DummyOutput,
            provider_config={"cli_path": "claude"},
            retries=3,
            output_retries=3,
        )

    assert out is None
    # Single call — no retry burst.
    assert m.call_count == 1
    meta = get_last_structured_llm_call_metadata()
    assert meta.get("is_terminal") is True
    assert meta.get("ok") is False


# ── 8) End-to-end: envelope → classify → raise → alert ────────────────────


def test_envelope_error_path_emits_discord_alert():
    """Integration check: a structured-call envelope with is_error=True
    and api_error_status=429 should raise RateLimitError AND fire exactly
    one Discord alert."""
    from chatbot import claude_cli_provider as ccp

    ccp._reset_rate_limit_alert_dedupe_for_tests()

    enqueue_calls = []

    def _fake_enqueue(conn, channel, content, embed=None, **_kw):
        enqueue_calls.append((channel, embed))
        return {"id": "fake", "channel": channel, "status": "pending"}

    fake_conn = MagicMock()
    fake_conn.close = MagicMock()

    envelope = {
        "is_error": True,
        "api_error_status": 429,
        "result": "You've hit your limit · resets 11:50pm (UTC)",
    }
    err = ccp._classify_error_envelope(envelope)
    assert isinstance(err, ccp.ClaudeCliRateLimitError)

    with patch("interactive_utils.get_conn", return_value=fake_conn), patch(
        "discord_sender.enqueue_discord_message", side_effect=_fake_enqueue
    ):
        ccp._emit_rate_limit_discord_alert(envelope["result"])

    assert len(enqueue_calls) == 1


def test_daemon_conversation_id_differs_per_schema(monkeypatch):
    """Bug #2 fix: two output schemas with the same model+system_prompt+thread
    must NOT collide on conversation_id. Otherwise the second schema reuses
    the first schema's frozen system prompt inside the daemon subprocess."""
    from pydantic import BaseModel

    class SchemaA(BaseModel):
        a: str

    class SchemaB(BaseModel):
        b: int
        c: int

    monkeypatch.setenv("CLAUDE_CLI_DAEMON_FOR_STRUCTURED", "1")
    seen_ids: list[str] = []

    def fake_chat_structured(**kwargs):
        seen_ids.append(kwargs["conversation_id"])
        return kwargs["output_schema"](
            **({"a": "x"} if kwargs["output_schema"] is SchemaA else {"b": 1, "c": 2})
        )

    from llm_utils import call_structured_llm_by_provider
    with patch(
        "chatbot.claude_cli_provider.call_claude_cli_chat_structured",
        side_effect=fake_chat_structured,
    ):
        call_structured_llm_by_provider(
            "claude-cli", "k", "claude-sonnet-4-6",
            prompt="p", output_type=SchemaA,
            provider_config={"cli_path": "claude"},
        )
        call_structured_llm_by_provider(
            "claude-cli", "k", "claude-sonnet-4-6",
            prompt="p", output_type=SchemaB,
            provider_config={"cli_path": "claude"},
        )

    assert len(seen_ids) == 2
    assert seen_ids[0] != seen_ids[1], (
        f"Schema A and B must yield different conversation_ids; got {seen_ids}"
    )
    assert "schema" in seen_ids[0] and "schema" in seen_ids[1]


def test_daemon_history_accumulates_across_calls():
    """Bug #1 fix: the chat-path session manager slices messages by
    sess.messages_sent, so the scaffold must pass cumulative history,
    not a single user turn. After two structured calls on the same
    conversation_id, the second invocation of call_claude_cli_chat
    must see at least 3 messages (user1, assistant1, user2)."""
    from chatbot.claude_cli_provider import (
        call_claude_cli_chat_structured,
        _clear_structured_history,
    )

    seen_message_lists: list[list] = []

    def fake_chat(**kwargs):
        seen_message_lists.append(list(kwargs.get("messages", [])))
        return {"content": json.dumps({"text": "ok", "score": 0.5})}

    _clear_structured_history()  # ensure clean slate
    with patch("chatbot.claude_cli_provider.call_claude_cli_chat", side_effect=fake_chat):
        for _ in range(2):
            call_claude_cli_chat_structured(
                conversation_id="probe-1",
                model="claude-sonnet-4-6",
                system_prompt="sys",
                user_prompt="u",
                output_schema=_DummyOutput,
            )

    assert len(seen_message_lists) == 2
    assert len(seen_message_lists[0]) == 1
    assert seen_message_lists[0][0]["role"] == "user"
    assert len(seen_message_lists[1]) >= 3
    assert seen_message_lists[1][0]["role"] == "user"
    assert seen_message_lists[1][1]["role"] == "assistant"
    assert seen_message_lists[1][2]["role"] == "user"


def test_daemon_history_cleared_between_retries(monkeypatch):
    """Bug #1 follow-up: the llm_utils retry-loop must clear scaffold history
    between attempts so a respawned daemon doesn't replay stale messages.

    This test patches the TRANSPORT (call_claude_cli_chat) rather than the
    scaffold so the real call_claude_cli_chat_structured runs end-to-end,
    populating _structured_history. On attempt 1 the transport returns
    bad JSON to trigger ClaudeCliValidationError -> the retry-loop runs
    cleanup. On attempt 2 we assert:

      (a) the transport sees a clean 1-message slate (proving the
          observable end-state is clean), AND
      (b) ``_clear_structured_history`` was called by the retry-loop with
          a non-empty conversation_id matching the active dispatch
          (proving the llm_utils cleanup branch ACTUALLY EXECUTED — this
          is the load-bearing assertion that fails if the cleanup block
          is removed).

    Assertion (b) is needed because the scaffold ALSO performs its own
    rollback on the bad-JSON path, so (a) alone passes even without the
    retry-loop cleanup. (b) directly observes the retry-loop's call into
    the cleanup helper, which is what this test exists to protect.
    """
    from chatbot import claude_cli_provider as ccp
    from chatbot.claude_cli_provider import (
        _clear_structured_history,
    )

    monkeypatch.setenv("CLAUDE_CLI_DAEMON_FOR_STRUCTURED", "1")
    _clear_structured_history()

    seen_message_lists: list[list] = []
    call_n = {"n": 0}

    def fake_chat(**kwargs):
        call_n["n"] += 1
        seen_message_lists.append(list(kwargs.get("messages", [])))
        if call_n["n"] == 1:
            return {"content": "not json at all"}  # forces validation failure
        return {"content": json.dumps({"text": "ok", "score": 0.5})}

    # Spy on the cleanup helper so we can verify the retry-loop in
    # llm_utils invokes it with the active conversation_id. We wrap the
    # real impl so behavior is unchanged.
    cleanup_calls: list = []
    real_clear = ccp._clear_structured_history

    def spy_clear(conversation_id=None):
        cleanup_calls.append(conversation_id)
        return real_clear(conversation_id)

    monkeypatch.setattr(ccp, "_clear_structured_history", spy_clear)

    from llm_utils import call_structured_llm_by_provider
    with patch("chatbot.claude_cli_provider.call_claude_cli_chat", side_effect=fake_chat):
        out = call_structured_llm_by_provider(
            "claude-cli", "k", "claude-sonnet-4-6",
            prompt="p", output_type=_DummyOutput,
            provider_config={"cli_path": "claude"},
            output_retries=1,
        )

    assert out is not None
    assert call_n["n"] == 2
    # Attempt #1: just one user turn.
    assert len(seen_message_lists[0]) == 1
    assert seen_message_lists[0][0]["role"] == "user"
    # Attempt #2: with cleanup it must be exactly 1: the retry's fresh
    # user turn. (The scaffold's own rollback also clears state on the
    # bad-JSON path, so this assertion alone is necessary but not
    # sufficient — see the load-bearing assertion below.)
    assert len(seen_message_lists[1]) == 1, (
        f"Retry should see clean slate; got {seen_message_lists[1]!r}"
    )
    assert seen_message_lists[1][0]["role"] == "user"

    # LOAD-BEARING: the retry-loop in llm_utils MUST have invoked the
    # cleanup helper between attempts 1 and 2 with a non-empty
    # conversation_id (the daemon-path conv_id built from model/sys/schema).
    # If the cleanup block is deleted, this list will be empty.
    assert len(cleanup_calls) >= 1, (
        "Retry-loop never called _clear_structured_history between "
        "attempts; the cleanup block in llm_utils may have regressed."
    )
    assert any(
        isinstance(cid, str) and cid.startswith("nexus-structured-")
        for cid in cleanup_calls
    ), (
        f"Cleanup was called but not with the daemon-path conversation_id: "
        f"{cleanup_calls!r}"
    )


def test_daemon_history_rolls_back_on_validation_failure():
    """Bug #1 follow-up: if call_claude_cli_chat succeeds but JSON
    parsing or Pydantic validation fails inside the scaffold, the
    optimistic user message must be rolled back so retries see a
    clean slate."""
    from chatbot.claude_cli_provider import (
        ClaudeCliValidationError,
        _clear_structured_history,
        _structured_history,
        call_claude_cli_chat_structured,
    )

    _clear_structured_history()

    def fake_chat(**kwargs):
        return {"content": "not json at all"}

    with patch("chatbot.claude_cli_provider.call_claude_cli_chat", side_effect=fake_chat):
        try:
            call_claude_cli_chat_structured(
                conversation_id="rollback-1",
                model="claude-sonnet-4-6",
                system_prompt="sys",
                user_prompt="u",
                output_schema=_DummyOutput,
            )
        except ClaudeCliValidationError:
            pass
        else:
            raise AssertionError("expected ClaudeCliValidationError")

    assert "rollback-1" not in _structured_history, (
        f"Expected rollback-1 removed after validation failure; "
        f"history={_structured_history.get('rollback-1')!r}"
    )
