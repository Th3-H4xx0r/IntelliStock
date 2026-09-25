"""Web-searching model calls for the swing-trader news line (spec §8)."""
import json
import os
import subprocess
import sys
import types

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import llm_utils  # noqa: E402


def _capture_cli(monkeypatch, result="Upgraded by two brokers this week.", is_error=False):
    seen = {}
    import chatbot.claude_cli_provider as ccp
    monkeypatch.setattr(ccp, "_resolve_cli_path", lambda path: "/usr/local/bin/claude")

    def fake_run(argv, **kwargs):
        seen["argv"] = list(argv)
        seen["input"] = kwargs.get("input")
        return types.SimpleNamespace(
            stdout=json.dumps({"result": result, "is_error": is_error}),
            stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_gemini_goes_through_search_grounding(monkeypatch):
    seen = {}

    def fake(api_key, model, prompt, max_output_tokens=1024, timeout_sec=None):
        seen.update(api_key=api_key, model=model, prompt=prompt,
                    tokens=max_output_tokens, timeout=timeout_sec)
        return "grounded summary"

    monkeypatch.setattr(llm_utils, "call_gemini_with_grounding", fake)
    out = llm_utils.call_llm_with_web_search(
        "gemini", "k", "gemini-2.5-flash", "news?", max_output_tokens=300, timeout_sec=25)
    assert out == "grounded summary"
    assert seen == {"api_key": "k", "model": "gemini-2.5-flash", "prompt": "news?",
                    "tokens": 300, "timeout": 25}


def test_claude_cli_enables_only_web_search_and_caps_the_turns(monkeypatch):
    seen = _capture_cli(monkeypatch)
    out = llm_utils.call_llm_with_web_search(
        "claude-cli", "", "claude-sonnet-4-6", "news?", max_uses=2, timeout_sec=25)
    assert out == "Upgraded by two brokers this week."
    argv = seen["argv"]
    assert argv[argv.index("--tools") + 1] == "WebSearch"
    assert argv[argv.index("--allowedTools") + 1] == "WebSearch"
    assert argv[argv.index("--max-turns") + 1] == "3"
    assert seen["input"] == "news?"


def test_a_plain_claude_cli_call_keeps_every_tool_off(monkeypatch):
    seen = _capture_cli(monkeypatch, result="ok")
    assert llm_utils.call_llm_by_provider("claude-cli", "", "claude-sonnet-4-6", "hi") == "ok"
    argv = seen["argv"]
    assert argv[argv.index("--tools") + 1] == ""
    assert "--allowedTools" not in argv and "--max-turns" not in argv


def test_a_cli_error_is_an_empty_string(monkeypatch):
    _capture_cli(monkeypatch, result="boom", is_error=True)
    assert llm_utils.call_llm_with_web_search("claude-cli", "", "m", "q") == ""


def test_other_providers_skip_news_with_one_log_line(monkeypatch, capsys):
    monkeypatch.setattr(llm_utils, "_WEB_SEARCH_SKIP_LOGGED", set())
    for provider in ("openai", "openai", "bedrock"):
        assert llm_utils.call_llm_with_web_search(provider, "k", "m", "q") == ""
    err = capsys.readouterr().err
    assert err.count("'openai'") == 1 and err.count("'bedrock'") == 1
