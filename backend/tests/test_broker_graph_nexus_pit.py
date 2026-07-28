from __future__ import annotations

import ast
from contextlib import nullcontext
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from point_in_time_data import PointInTimeDataError
import point_in_time_registry


UTC = timezone.utc
BROKER_PATH = Path(__file__).resolve().parents[1] / "broker.py"


def _extract_broker_functions(*names: str):
    tree = ast.parse(BROKER_PATH.read_text())
    # The strict-historical dispatch binds each resolved PIT manifest to its
    # decision through this helper, so it must come along or the dispatch
    # NameErrors before it ever reaches run_historical.
    wanted = set(names) | {"_record_evidence_pit"}
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    found = {node.name for node in nodes}
    missing = wanted - found
    if missing:
        pytest.fail(f"broker.py is missing PIT dispatch functions: {sorted(missing)}")

    namespace = {
        "MODE_BACKTEST": "backtest",
        "MODE_LIVE": "live",
        "mode": "backtest",
        "os": __import__("os"),
        "_strategy_cache": {},
        "_strategy_class_cache": {},
        "_log": lambda *args, **kwargs: None,
        "_load_strategy_class": lambda name: None,
        "_resolve_nexus_runtime_identity": lambda base, settings: (
            base,
            "history-scope",
            f"{base}|history-scope",
            {},
        ),
        "_instance_kind_and_crypto_config": lambda: ("stock", {}),
        "instance_id": "main",
        "backtest_row_id": "bt-1",
        "telemetry_llm_call_context": lambda **kwargs: nullcontext(),
        "get_conn": lambda: pytest.fail("model resolution should not run"),
        "resolve_model_refs_in_config": lambda conn, config: config,
        "_partial_trim_syms": lambda sizes: set(),
    }
    for node in nodes:
        module = ast.Module(body=[node], type_ignores=[])
        exec(compile(module, str(BROKER_PATH), "exec"), namespace)
    return namespace


def _manifest_mapping():
    return {
        "manifest_id": "broker-pit-manifest",
        "source_hashes": {
            "graph": "sha256:graph",
            "fundamentals": "sha256:fundamentals",
            "universe": "sha256:universe",
        },
        "created_at": "2026-03-03T00:00:00Z",
    }


def _snapshot_mapping():
    available = datetime(2026, 3, 1, 1, tzinfo=UTC)
    effective = datetime(2026, 3, 1, tzinfo=UTC)
    return {
        "graph": [
            {
                "manifest_id": "broker-pit-manifest",
                "effective_at": effective,
                "available_at": available,
                "payload": object(),
            }
        ],
        "fundamentals": [
            {
                "manifest_id": "broker-pit-manifest",
                "effective_at": effective,
                "available_at": available,
                "payload": {"AAPL": {"market_cap": 1_500_000_000_000}},
            }
        ],
        "universe": [
            {
                "manifest_id": "broker-pit-manifest",
                "effective_at": effective,
                "available_at": available,
                "payload": {"symbols": ["AAPL"]},
            }
        ],
    }


def _session_close(session_date: date) -> datetime:
    return datetime(
        session_date.year,
        session_date.month,
        session_date.day,
        21,
        tzinfo=UTC,
    )


@pytest.fixture(autouse=True)
def _fail_closed_default_registry(monkeypatch):
    def unavailable(_as_of):
        raise PointInTimeDataError("no finalized point-in-time manifest exists")

    monkeypatch.setattr(
        point_in_time_registry,
        "resolve_default_bundle",
        unavailable,
    )


def test_broker_routes_graph_backtest_through_strict_historical_entrypoint():
    namespace = _extract_broker_functions(
        "_run_graph_nexus_with_point_in_time",
        "run_run_once_strategies",
    )
    captured = {}

    class Strategy:
        def run_historical(self, *args, **kwargs):
            captured["historical"] = kwargs
            return {"AAPL": {"score": 0}}

        def run_once(self, *args, **kwargs):
            pytest.fail("backtest must not call Graph Nexus run_once directly")

    namespace["_strategy_class_cache"]["graph_nexus_analysis"] = Strategy
    config = {
        "point_in_time_manifest": _manifest_mapping(),
        "point_in_time_store": _snapshot_mapping(),
        "point_in_time_session_close_resolver": _session_close,
    }
    result = namespace["run_run_once_strategies"](
        [
            {
                "strategy": "graph_nexus_analysis",
                "weight": 1.0,
                "config": config,
            }
        ],
        ["AAPL"],
        {"AAPL": 100.0},
        datetime(2026, 3, 2, 14),
        data={"AAPL": []},
        strategy_caches={},
    )

    context = captured["historical"]["context"]
    assert context.strict is True
    assert context.is_live is False
    assert context.as_of == datetime(2026, 3, 2, 14, tzinfo=UTC)
    assert type(captured["historical"]["point_in_time_store"]).__name__ == (
        "ImmutableSnapshotStore"
    )
    assert captured["historical"]["session_close_resolver"] is _session_close
    assert result[0][1] == {"AAPL": 0}


def test_broker_resolves_registered_bundle_without_serialized_config(
    monkeypatch,
):
    from point_in_time_registry import InMemoryPointInTimeRegistry

    registry = InMemoryPointInTimeRegistry()
    registry.finalize_bundle(
        as_of=datetime(2026, 3, 2, 13, tzinfo=UTC),
        datasets={
            "graph": {"recording_version": 1, "queries": {}},
            "fundamentals": {"AAPL": {"market_cap": 1_500_000_000_000}},
            "universe": {"symbols": ["AAPL"]},
            "news": {"alpaca": [], "google": [], "benzinga": {}},
        },
        code_revision="test",
    )
    monkeypatch.setattr(
        point_in_time_registry,
        "resolve_default_bundle",
        registry.resolve_bundle,
    )
    namespace = _extract_broker_functions(
        "_run_graph_nexus_with_point_in_time",
        "run_run_once_strategies",
    )
    captured = {}

    class Strategy:
        def run_historical(self, *args, **kwargs):
            captured.update(kwargs)
            return {"AAPL": {"score": 0}}

        def run_once(self, *args, **kwargs):
            pytest.fail("backtest must not call Graph Nexus run_once directly")

    namespace["_strategy_class_cache"]["graph_nexus_analysis"] = Strategy
    config = {}
    result = namespace["run_run_once_strategies"](
        [{"strategy": "graph_nexus_analysis", "weight": 1.0, "config": config}],
        ["AAPL"],
        {"AAPL": 100.0},
        datetime(2026, 3, 2, 14, tzinfo=UTC),
        data={"AAPL": []},
        strategy_caches={},
    )

    assert config == {}
    assert captured["context"].manifest.manifest_id.startswith("pit-")
    assert captured["context"].provenance == "strict_verified"
    assert captured["session_close_resolver"].__name__ == (
        "resolve_nyse_session_close"
    )
    assert result[0][1] == {"AAPL": 0}


def test_broker_supplies_explicit_live_context_to_graph_nexus():
    namespace = _extract_broker_functions(
        "_run_graph_nexus_with_point_in_time",
        "run_run_once_strategies",
    )
    namespace["mode"] = "live"
    captured = {}

    class Strategy:
        def run_historical(self, *args, **kwargs):
            pytest.fail("live execution must not enter run_historical")

        def run_once(self, *args, **kwargs):
            captured.update(kwargs)
            return {"AAPL": {"score": 0}}

    namespace["_strategy_class_cache"]["graph_nexus_analysis"] = Strategy
    result = namespace["run_run_once_strategies"](
        [{"strategy": "graph_nexus_analysis", "weight": 1.0, "config": {}}],
        ["AAPL"],
        {"AAPL": 100.0},
        datetime(2026, 3, 2, 14, tzinfo=UTC),
        strategy_caches={},
        mode="FULL",
    )

    context = captured["point_in_time_context"]
    assert context.is_live is True
    assert context.strict is False
    assert result[0][1] == {"AAPL": 0}


def test_broker_graph_backtest_fails_closed_without_snapshot_inputs():
    namespace = _extract_broker_functions(
        "_run_graph_nexus_with_point_in_time",
        "run_run_once_strategies",
    )

    class Strategy:
        def run_historical(self, *args, **kwargs):
            pytest.fail("missing snapshots must fail before strategy execution")

        def run_once(self, *args, **kwargs):
            pytest.fail("missing snapshots must fail before strategy execution")

    namespace["_strategy_class_cache"]["graph_nexus_analysis"] = Strategy

    with pytest.raises(PointInTimeDataError, match="manifest|snapshot"):
        namespace["run_run_once_strategies"](
            [{"strategy": "graph_nexus_analysis", "weight": 1.0, "config": {}}],
            ["AAPL"],
            {"AAPL": 100.0},
            datetime(2026, 3, 2, 14),
            data={"AAPL": []},
            strategy_caches={},
        )
