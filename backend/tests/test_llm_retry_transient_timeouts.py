"""LLM calls must treat client-side timeouts as transient and retry up to 3x."""
import os, sys
_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
import importlib
import llm_utils


class TestTimeoutIsTransient:
    def test_client_timeouts_are_retryable(self):
        for t in [
            "httpx.ReadTimeout", "The read operation timed out",
            "openai.APITimeoutError: Request timed out.",
            "ConnectTimeout", "botocore ReadTimeoutError: read timeout",
            "Request timed out", "TimeoutException",
        ]:
            assert llm_utils._is_transient_http_error(t) is True, t

    def test_server_transients_still_retryable(self):
        for t in ["status_code: 503", "status code 429", "bad gateway", "connection reset"]:
            assert llm_utils._is_transient_http_error(t) is True, t

    def test_non_transient_not_retryable(self):
        for t in ["invalid_api_key", "model_not_found", "content_policy_violation", ""]:
            assert llm_utils._is_transient_http_error(t) is False, t


class TestDefaultRetries:
    def test_default_is_two_attempts_plus_one(self):
        os.environ.pop("LLM_MAX_RETRIES", None)
        assert llm_utils._default_llm_retries() == 2  # 2 retries => 3 attempts

    def test_env_override(self):
        os.environ["LLM_MAX_RETRIES"] = "4"
        try:
            assert llm_utils._default_llm_retries() == 4
        finally:
            os.environ.pop("LLM_MAX_RETRIES", None)

    def test_bad_env_falls_back(self):
        os.environ["LLM_MAX_RETRIES"] = "not-an-int"
        try:
            assert llm_utils._default_llm_retries() == 2
        finally:
            os.environ.pop("LLM_MAX_RETRIES", None)
