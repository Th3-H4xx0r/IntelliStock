"""Bedrock error-shape classification in backend/llm_critical_guard.py."""
import llm_critical_guard as g


def setup_function(_):
    g.reset_state()


def test_bedrock_access_denied_is_auth_failure():
    tag, crit = g.classify(status=403, body="AccessDeniedException: not authorized", provider="bedrock", model="m")
    assert (tag, crit) == ("auth_failure", True)


def test_bedrock_expired_token_is_auth_failure():
    tag, crit = g.classify(status=400, body="ExpiredTokenException: token expired", provider="bedrock", model="m")
    assert crit is True and tag == "auth_failure"


def test_bedrock_unrecognized_client_is_auth_failure():
    tag, crit = g.classify(status=403, body="UnrecognizedClientException: invalid token", provider="bedrock", model="m")
    assert crit is True and tag == "auth_failure"


def test_bedrock_validation_error_not_critical():
    # Bad model id / missing inference profile is a config error, not a retry storm.
    tag, crit = g.classify(status=400, body="ValidationException: model not found", provider="bedrock", model="m")
    assert crit is False


def test_bedrock_5xx_persists_after_three():
    for _ in range(3):
        g.update_consecutive_state(tag="x", status=500, provider="bedrock", model="m")
    tag, crit = g.classify(status=500, body="InternalServerException", provider="bedrock", model="m")
    assert (tag, crit) == ("provider_5xx_persistent", True)


def test_bedrock_single_5xx_not_critical():
    g.update_consecutive_state(tag="x", status=500, provider="bedrock", model="m")
    tag, crit = g.classify(status=500, body="InternalServerException", provider="bedrock", model="m")
    assert crit is False
