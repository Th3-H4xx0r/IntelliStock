"""Local backtest harness — persistent LLM disk cache (scripts/harness/llm_disk_cache.py).

The cache sits AFTER the strategy's RethinkDB cache checks and BEFORE the live
Claude call, so re-tests of the same window replay from disk. It must: be a
pure passthrough when disabled (zero effect on live/chatbot), hit on repeats,
reconstruct the validated model exactly, and never break a call on error.
"""
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_HARNESS = os.path.join(_REPO, "scripts", "harness")
if _HARNESS not in sys.path:
    sys.path.insert(0, _HARNESS)

import llm_disk_cache as c  # noqa: E402


class FakeModel:
    """Minimal pydantic-like stand-in: model_dump / model_validate roundtrip."""
    def __init__(self, val):
        self.val = val

    def model_dump(self):
        return {"val": self.val}

    @classmethod
    def model_validate(cls, d):
        return cls(d["val"])

    def __eq__(self, other):
        return isinstance(other, FakeModel) and other.val == self.val


def _clear_env():
    os.environ.pop("NEXUS_LOCAL_LLM_CACHE_DIR", None)


def test_key_stability_and_variation():
    k1 = c.make_key("claude-cli", "haiku", "hello", FakeModel, None, {"reasoning_effort": "medium"})
    k2 = c.make_key("claude-cli", "haiku", "hello", FakeModel, None, {"reasoning_effort": "medium"})
    assert k1 == k2, "same inputs -> same key"
    assert k1 != c.make_key("claude-cli", "haiku", "HELLO", FakeModel, None, {}), "prompt changes key"
    assert k1 != c.make_key("claude-cli", "sonnet", "hello", FakeModel, None, {"reasoning_effort": "medium"}), "model changes key"
    assert k1 != c.make_key("claude-cli", "haiku", "hello", FakeModel, None, {"reasoning_effort": "high"}), "effort changes key"


def test_passthrough_when_disabled(monkeypatch, tmp_path):
    _clear_env()
    calls = {"n": 0}

    def orig(provider, api_key, model, prompt, output_type, **kw):
        calls["n"] += 1
        return FakeModel("R")

    wrapped = c.wrap_structured(orig)
    assert wrapped("claude-cli", "", "haiku", "p", FakeModel) == FakeModel("R")
    assert wrapped("claude-cli", "", "haiku", "p", FakeModel) == FakeModel("R")
    assert calls["n"] == 2, "disabled cache must call through every time (no caching)"


def test_structured_hit_miss_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_LOCAL_LLM_CACHE_DIR", str(tmp_path))
    calls = {"n": 0}

    def orig(provider, api_key, model, prompt, output_type, **kw):
        calls["n"] += 1
        return FakeModel(f"resp-{calls['n']}")

    wrapped = c.wrap_structured(orig)
    r1 = wrapped("claude-cli", "", "haiku", "p", FakeModel)      # miss -> call + store
    assert r1 == FakeModel("resp-1") and calls["n"] == 1
    r2 = wrapped("claude-cli", "", "haiku", "p", FakeModel)      # hit -> no call
    assert r2 == FakeModel("resp-1"), "hit reconstructs the exact cached model"
    assert calls["n"] == 1, "cache hit must NOT call through"
    s = c.stats()
    assert s["struct_hit"] >= 1 and s["struct_miss"] >= 1


def test_different_prompt_is_a_miss(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_LOCAL_LLM_CACHE_DIR", str(tmp_path))
    calls = {"n": 0}

    def orig(provider, api_key, model, prompt, output_type, **kw):
        calls["n"] += 1
        return FakeModel(prompt)

    wrapped = c.wrap_structured(orig)
    wrapped("claude-cli", "", "haiku", "A", FakeModel)
    wrapped("claude-cli", "", "haiku", "B", FakeModel)
    assert calls["n"] == 2, "distinct prompts are distinct cache entries"


def test_none_result_not_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_LOCAL_LLM_CACHE_DIR", str(tmp_path))
    calls = {"n": 0}

    def orig(provider, api_key, model, prompt, output_type, **kw):
        calls["n"] += 1
        return None  # LLM failure

    wrapped = c.wrap_structured(orig)
    wrapped("claude-cli", "", "haiku", "p", FakeModel)
    wrapped("claude-cli", "", "haiku", "p", FakeModel)
    assert calls["n"] == 2, "a None (failed) result must not be cached"


def test_corrupt_cache_falls_through(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_LOCAL_LLM_CACHE_DIR", str(tmp_path))
    key = c.make_key("claude-cli", "haiku", "p", FakeModel, None, None, kind="struct")
    sub = tmp_path / key[:2]
    sub.mkdir(parents=True)
    (sub / (key + ".json")).write_text("{ this is not valid json")
    calls = {"n": 0}

    def orig(provider, api_key, model, prompt, output_type, **kw):
        calls["n"] += 1
        return FakeModel("ok")

    wrapped = c.wrap_structured(orig)
    assert wrapped("claude-cli", "", "haiku", "p", FakeModel) == FakeModel("ok")
    assert calls["n"] == 1, "corrupt cache entry must fall through to the real call"


def test_plain_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_LOCAL_LLM_CACHE_DIR", str(tmp_path))
    calls = {"n": 0}

    def orig(provider, api_key, model, prompt, **kw):
        calls["n"] += 1
        return f"text-{calls['n']}"

    wrapped = c.wrap_plain(orig)
    assert wrapped("claude-cli", "", "haiku", "p") == "text-1"
    assert wrapped("claude-cli", "", "haiku", "p") == "text-1"
    assert calls["n"] == 1, "plain cache hit must not call through"
