from __future__ import annotations

from datetime import datetime

from strategies import graph_nexus_analysis as graph


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class _RecordingDriver:
    def export(self):
        return {
            "recording_version": 1,
            "queries": {
                "query-hash": {
                    "query": "MATCH (n) RETURN n",
                    "parameters": {},
                    "occurrences": [[{"n": "AAPL"}]],
                }
            },
        }


class _Registry:
    def __init__(self):
        self.call = None

    def finalize_bundle(self, **kwargs):
        self.call = kwargs
        return "manifest"


def test_capture_finalizes_all_required_sources(monkeypatch):
    registry = _Registry()
    monkeypatch.setattr(
        graph,
        "_neo4j_market_cap_cache",
        {"AAPL": 3_000_000_000_000},
    )
    monkeypatch.setattr(
        "ticker_universe.snapshot_current_universe",
        lambda: {
            "symbols": ["AAPL"],
            "rows": [
                {
                    "sym": "AAPL",
                    "price": 200.0,
                    "volume": 10_000_000,
                    "mcap": 3_000_000_000_000,
                }
            ],
        },
    )

    result = graph._capture_point_in_time_bundle(
        as_of=_ts("2026-07-28T20:00:00Z"),
        recording_driver=_RecordingDriver(),
        strategy_cache={
            "_yf_market_cap_cache": {"MSFT": 2_500_000_000_000}
        },
        alpaca_articles=[
            {
                "id": "alpaca-1",
                "created_at": "2026-07-28T19:59:00Z",
            }
        ],
        google_articles=[
            {
                "id": "google-1",
                "published_date": "2026-07-28T19:58:00Z",
            }
        ],
        benzinga_data={
            "ratings": [
                {
                    "ticker": "AAPL",
                    "published_at": "2026-07-28T19:57:00Z",
                }
            ]
        },
        registry=registry,
        code_revision="abc123",
    )

    assert result == "manifest"
    assert registry.call["code_revision"] == "abc123"
    assert set(registry.call["datasets"]) == {
        "graph",
        "fundamentals",
        "universe",
        "news",
    }
    assert registry.call["datasets"]["fundamentals"] == {
        "AAPL": {"market_cap": 3_000_000_000_000.0},
        "MSFT": {"market_cap": 2_500_000_000_000.0},
    }
    assert registry.call["datasets"]["news"]["alpaca"][0]["id"] == "alpaca-1"


def test_capture_failure_never_escapes_into_trading(monkeypatch):
    def fail_capture(**kwargs):
        raise RuntimeError("provider response contained sensitive details")

    logs = []
    monkeypatch.setattr(graph, "_capture_point_in_time_bundle", fail_capture)
    monkeypatch.setattr(
        graph,
        "_log",
        lambda message, color="white": logs.append(str(message)),
    )

    result = graph._finalize_pit_capture_safely(
        enabled=True,
        as_of=_ts("2026-07-28T20:00:00Z"),
        recording_driver=object(),
        strategy_cache={},
        alpaca_articles=[],
        google_articles=[],
        benzinga_data={},
    )

    assert result is None
    assert "RuntimeError" in logs[-1]
    assert "sensitive details" not in logs[-1]


class _Ctx:
    """Minimal stand-in for the strategy run context."""

    def __init__(self, is_live: bool):
        self.is_live = is_live


# ── the capture GATE ──────────────────────────────────────────────────────
#
# `_capture_point_in_time_bundle` is covered above, but nothing exercised the
# predicate that decides whether it is ever CALLED. That gate is the single
# point of failure between "a paper instance is running" and "there is
# certifiable point-in-time data to validate against": if it silently returns
# False, `PointInTimeManifests` stays empty and the only way to notice is that
# weeks of paper trading produced nothing. As of 2026-08-03 both PIT tables
# have 0 rows and this code has never executed in production, so its contract
# is pinned here rather than discovered later.


def test_capture_gate_requires_live_full_and_the_flag():
    cfg = {"pit_capture_enabled": True}
    assert graph._pit_capture_enabled(cfg, _Ctx(is_live=True), "FULL") is True
    # Case-insensitive on the mode, since callers stamp it from several places.
    assert graph._pit_capture_enabled(cfg, _Ctx(is_live=True), "full") is True


def test_capture_never_fires_outside_a_live_full_tick():
    """Backtests must NEVER write manifests.

    A backtest replays the CURRENT graph (`PIT RESEARCH MODE ... running the
    legacy current-state path`), so a bundle captured there would certify
    lookahead-contaminated data as point-in-time — worse than having no data,
    because the contamination becomes invisible downstream.
    """
    cfg = {"pit_capture_enabled": True}
    assert graph._pit_capture_enabled(cfg, _Ctx(is_live=False), "FULL") is False
    assert graph._pit_capture_enabled(cfg, None, "FULL") is False
    # MONITOR/IDLE ticks skip the expensive work a bundle is meant to describe.
    for mode in ("MONITOR", "IDLE"):
        assert graph._pit_capture_enabled(cfg, _Ctx(is_live=True), mode) is False


def test_capture_gate_is_off_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("PIT_CAPTURE_ENABLED", raising=False)
    for cfg in ({}, None, {"pit_capture_enabled": False},
                {"pit_capture_enabled": ""}):
        assert graph._pit_capture_enabled(cfg, _Ctx(is_live=True), "FULL") is False


def test_config_overrides_the_environment_in_both_directions(monkeypatch):
    """An explicit config key wins over the env var, including when it says NO.

    Deployments set PIT_CAPTURE_ENABLED image-wide, so a single instance has to
    be able to opt OUT without a redeploy — and an instance that was switched
    off must not be silently re-enabled by the image default.
    """
    monkeypatch.setenv("PIT_CAPTURE_ENABLED", "true")
    ctx = _Ctx(is_live=True)
    assert graph._pit_capture_enabled({}, ctx, "FULL") is True          # env
    assert graph._pit_capture_enabled({"pit_capture_enabled": False},
                                      ctx, "FULL") is False             # config NO wins
    monkeypatch.setenv("PIT_CAPTURE_ENABLED", "0")
    assert graph._pit_capture_enabled({"pit_capture_enabled": True},
                                      ctx, "FULL") is True              # config YES wins


def test_truthy_spellings_all_enable_capture(monkeypatch):
    monkeypatch.delenv("PIT_CAPTURE_ENABLED", raising=False)
    ctx = _Ctx(is_live=True)
    for raw in (True, "1", "true", "TRUE", "yes", "on", " on "):
        assert graph._pit_capture_enabled(
            {"pit_capture_enabled": raw}, ctx, "FULL") is True, raw
    for raw in (False, "0", "false", "no", "off", "maybe", None):
        assert graph._pit_capture_enabled(
            {"pit_capture_enabled": raw}, ctx, "FULL") is False, raw
