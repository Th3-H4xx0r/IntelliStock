"""Verify the retry loops skip non-retryable filter responses (content_filter
and Azure temp-block) so we don't burn into Azure's abuse-monitor temp-block.

Background: when a backtest sends a prompt that hits Azure's content filter
(HTTP 400 with code=content_filter), or hits an already-in-effect temp-block
(HTTP 403 "temporarily blocked"), blindly retrying the same prompt is what
Azure's abuse monitor flags as a bypass attempt — and that extends the
temp-block to 24-48h on the entire resource. Two real backtests (bt357345
and bt437583) died this way. This module verifies that
``_is_non_retryable_filter_response`` correctly classifies those signatures
so the surrounding retry loops break instead of re-fire.
"""
import pytest

from backend import llm_utils


def test_is_non_retryable_content_filter_400():
    """HTTP 400 + body containing 'content_filter' → must not retry."""
    result, tag = llm_utils._is_non_retryable_filter_response(
        status=400,
        body='{"error":{"code":"content_filter","message":"The response was filtered"}}',
    )
    assert result is True
    assert tag == "content_filter"


def test_is_non_retryable_azure_temp_block_403():
    """HTTP 403 + body indicating temp-block → must not retry."""
    result, tag = llm_utils._is_non_retryable_filter_response(
        status=403,
        body="{'error': {'code': 'Forbidden', 'message': 'Your resource has been temporarily blocked because we detected unusual behavior.'}}",
    )
    assert result is True
    assert tag == "azure_temp_blocked"


def test_is_non_retryable_normal_503_returns_false():
    """503 is a transient infra error — IS retryable, not a filter signature."""
    result, tag = llm_utils._is_non_retryable_filter_response(
        status=503, body="Service unavailable",
    )
    assert result is False
    assert tag == ""


def test_is_non_retryable_normal_429_returns_false():
    """429 rate-limit IS retryable (transient throttling, not filter/block)."""
    result, tag = llm_utils._is_non_retryable_filter_response(
        status=429, body="Too many requests",
    )
    assert result is False


def test_is_non_retryable_exception_with_response():
    """Accepts an exception that has a .response attribute (requests pattern)."""
    class _FakeResp:
        status_code = 400
        text = '{"error":{"code":"content_filter"}}'

    class _FakeExc(Exception):
        response = _FakeResp()

    result, tag = llm_utils._is_non_retryable_filter_response(exc=_FakeExc("HTTP 400"))
    assert result is True
    assert tag == "content_filter"


def test_is_non_retryable_no_info_returns_false():
    """No status, no body, no exception → safe default (retry allowed)."""
    result, tag = llm_utils._is_non_retryable_filter_response()
    assert result is False


def test_is_non_retryable_content_filter_case_insensitive():
    """Variations in casing must still match the content_filter pattern."""
    for body in (
        '{"error":{"code":"CONTENT_FILTER"}}',
        '{"error":{"message":"Content Filter triggered"}}',
        '{"error":{"code":"Content_Filter"}}',
    ):
        result, tag = llm_utils._is_non_retryable_filter_response(status=400, body=body)
        assert result is True, f"failed to match: {body!r}"
        assert tag == "content_filter"


def test_is_non_retryable_temp_block_variants():
    """Both 'temporarily blocked' and 'unusual behavior' map to azure_temp_blocked."""
    for body in (
        "Your resource has been temporarily blocked.",
        "We detected unusual behavior on this resource.",
        "TEMPORARILY BLOCKED",
    ):
        result, tag = llm_utils._is_non_retryable_filter_response(status=403, body=body)
        assert result is True, f"failed to match: {body!r}"
        assert tag == "azure_temp_blocked"


def test_is_non_retryable_content_filter_match_without_status():
    """When status is missing but body has content_filter, still non-retryable.

    This covers the common case where a higher layer wraps the response as
    RuntimeError(f"HTTP 400: {body}") and we only see the stringified form
    in the inner ``except Exception``.
    """
    result, tag = llm_utils._is_non_retryable_filter_response(
        status=None,
        body='HTTP 400: {"error":{"code":"content_filter"}}',
    )
    assert result is True
    assert tag == "content_filter"


def test_is_non_retryable_exception_only_runtime_error():
    """A bare RuntimeError carrying the wrapped HTTP message is still classified."""
    exc = RuntimeError("HTTP 403: Your resource has been temporarily blocked.")
    result, tag = llm_utils._is_non_retryable_filter_response(exc=exc)
    assert result is True
    assert tag == "azure_temp_blocked"
