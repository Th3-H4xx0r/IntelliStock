"""Tests for scripts/migrate_llm_cache_to_canonical.py id re-keying."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import migrate_llm_cache_to_canonical as mig  # noqa: E402


def test_rekey_azure_with_effort():
    # azure model_ref carried a -MEDIUM suffix; canonical folds it into @medium.
    assert mig._canonical_from_old_id("company|H1|azure|gpt-oss-120b-MEDIUM|v3") == "company|H1|gpt-oss-120b@medium|v3"


def test_rekey_bedrock_no_effort_suffix():
    # old bedrock rows had no effort in the key -> canonical with no @effort.
    assert mig._canonical_from_old_id("company|H1|bedrock|openai.gpt-oss-120b-1:0|v3") == "company|H1|gpt-oss-120b|v3"


def test_rekey_azure_no_effort():
    assert mig._canonical_from_old_id("macro|H2|azure|gpt-oss-120b|v3") == "macro|H2|gpt-oss-120b|v3"


def test_already_canonical_skipped():
    # canonical ids have 4 pipe-parts (no provider segment) -> not old scheme.
    assert mig._canonical_from_old_id("company|H1|gpt-oss-120b@medium|v3") is None


def test_non_string_skipped():
    assert mig._canonical_from_old_id(None) is None
