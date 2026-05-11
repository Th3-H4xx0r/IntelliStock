"""Live error taxonomy classification."""
import os
import sys

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from live_error_taxonomy import classify, is_nonfatal, KNOWN_NONFATAL


def test_rate_limit_benzinga():
    assert classify(None, "Benzinga: Rate limit exceeded") == "benzinga_quota_warn"


def test_yfinance_throttle():
    assert classify(None, "yfinance request failed") == "yfinance_throttle"


def test_neo4j_non_critical():
    assert classify(None, "Neo4j connection refused") == "nexus_graph_non_critical"


def test_stream_reconnect():
    assert classify(None, "WebSocket disconnected; reconnecting") == "alpaca_stream_reconnect"


def test_unknown_unclassified():
    assert classify(RuntimeError("kaboom"), "totally unexpected failure") == "UNCLASSIFIED"


def test_is_nonfatal_allowlist():
    assert is_nonfatal(None, "yfinance throttle") is True
    assert is_nonfatal(RuntimeError("kaboom"), "") is False


def test_all_known_nonfatal_in_set():
    expected = {
        "yfinance_throttle", "benzinga_quota_warn",
        "nexus_graph_non_critical", "llm_provider_retry",
        "alpaca_stream_reconnect",
    }
    assert expected == set(KNOWN_NONFATAL)
