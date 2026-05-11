"""Classified error taxonomy for live mode.

Known non-fatal exceptions get a tag; everything else classifies as UNCLASSIFIED,
which the go/no-go gate treats as a regression. Adding a tag is a deliberate
operator decision, not a drive-by fix.
"""

from __future__ import annotations


KNOWN_NONFATAL = frozenset({
    "yfinance_throttle",
    "benzinga_quota_warn",
    "nexus_graph_non_critical",
    "llm_provider_retry",
    "alpaca_stream_reconnect",
})


def classify(exc: BaseException | None, msg: str = "") -> str:
    """Return taxonomy tag for logging/alerting. Unknown -> 'UNCLASSIFIED'."""
    text = (
        f"{type(exc).__name__ if exc else ''} "
        f"{exc if exc else ''} "
        f"{msg}"
    ).lower()
    if "rate limit" in text or "429" in text:
        if "benzinga" in text:
            return "benzinga_quota_warn"
        if "alpaca" in text:
            return "alpaca_stream_reconnect"
        return "llm_provider_retry"
    if "quota" in text and "benzinga" in text:
        return "benzinga_quota_warn"
    if "yfinance" in text or "yf " in text:
        return "yfinance_throttle"
    if "neo4j" in text or "nexus_graph" in text:
        return "nexus_graph_non_critical"
    if ("stream" in text or "websocket" in text) and ("disconnect" in text or "reconnect" in text or "closed" in text):
        return "alpaca_stream_reconnect"
    if "timeout" in text and ("llm" in text or "openai" in text or "anthropic" in text):
        return "llm_provider_retry"
    return "UNCLASSIFIED"


def is_nonfatal(exc: BaseException | None, msg: str = "") -> bool:
    return classify(exc, msg) in KNOWN_NONFATAL
