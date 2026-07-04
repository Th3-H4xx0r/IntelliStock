"""Run-185254 finding: cost_source=models_override rows dropped reasoning-token
cost entirely (~$0.33 across macro/sentiment call sites). OpenRouter bills
reasoning at the output rate."""
from backend.llm_telemetry import compute_cost


def test_reasoning_tokens_billed_at_output_rate_by_default():
    res = compute_cost(
        model="nvidia/nemotron-3-ultra-550b-a55b",
        usage={"input_tokens": 0, "output_tokens": 1_000_000, "reasoning_tokens": 1_000_000},
        pricing_yaml={},
        models_override={"input_cost_per_1m": 0.5, "output_cost_per_1m": 2.0},
    )
    assert res["total_cost_usd"] == 4.0          # 1M output + 1M reasoning @ $2/1M
    assert res["reasoning_cost_usd"] == 2.0
    assert res["cost_source"] == "models_override"


def test_explicit_reasoning_price_wins():
    res = compute_cost(
        model="m",
        usage={"reasoning_tokens": 1_000_000},
        pricing_yaml={},
        models_override={"output_cost_per_1m": 2.0, "reasoning_cost_per_1m": 1.0},
    )
    assert res["reasoning_cost_usd"] == 1.0


def test_envelope_still_authoritative():
    res = compute_cost(
        model="m",
        usage={"reasoning_tokens": 999},
        pricing_yaml={},
        models_override=None,
        cost_usd_override=1.23,
    )
    assert res["total_cost_usd"] == 1.23
    assert res["cost_source"] == "envelope"


# --- Part 2: native structured OpenRouter path opts into the cost envelope ---
import backend.llm_utils as llm_utils


def test_openrouter_native_settings_request_cost_envelope():
    s = llm_utils._build_structured_model_settings(
        "openrouter", 256, 30, 0.2, model="nvidia/nemotron-3-ultra-550b-a55b"
    )
    assert s.get("extra_body") == {"usage": {"include": True}}
    # Only OpenRouter opts in; other providers are untouched.
    s2 = llm_utils._build_structured_model_settings("openai", 256, 30, 0.2, model="gpt-4o")
    assert s2.get("extra_body") is None


def test_structured_cost_override_extracts_positive_cost():
    assert llm_utils._structured_cost_override({"detail_cost": 0.7}) == 0.7
    assert llm_utils._structured_cost_override({"cost": 0}) is None
    assert llm_utils._structured_cost_override({"input_tokens": 10}) is None
    assert llm_utils._structured_cost_override(None) is None


# --- Part 3: HTTP-200 whose body fails JSON parsing still salvages usage/cost ---
def test_salvage_usage_block_from_truncated_200():
    raw = (
        '{"choices": [truncated garbage}}} '
        '"usage": {"prompt_tokens": 10, "completion_tokens": 20, "cost": 0.5, '
        '"completion_tokens_details": {"reasoning_tokens": 5}}'
    )
    block = llm_utils._salvage_usage_block(raw)
    assert block is not None
    usage, cost = llm_utils._extract_openrouter_usage(block)
    assert usage == {"input_tokens": 10, "output_tokens": 20, "reasoning_tokens": 5}
    assert cost == 0.5


def test_salvage_usage_block_none_when_absent():
    assert llm_utils._salvage_usage_block("total junk no usage here") is None
    assert llm_utils._salvage_usage_block("") is None
