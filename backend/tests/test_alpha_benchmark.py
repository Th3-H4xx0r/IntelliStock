"""Task 9: inception truth, flow-aware TWR lenses, activity ingestion."""
import ast
import os
from pathlib import Path
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark_alpha.benchmark import (
    InceptionState,
    ingest_activities,
    reconcile_ledger,
    time_weighted_return,
    update_inception_state,
)

T0 = datetime(2026, 6, 4, tzinfo=timezone.utc)
SPY_MANIFEST = {
    "manifest_id": "spy-adjusted-2026-07-08--2026-07-10",
    "symbol": "SPY",
    "timeframe": "1Day",
    "adjustment": "all",
    "price_field": "c",
    "total_return": True,
    "feed": "iex",
    "start_date": "2026-07-08",
    "end_date": "2026-07-10",
    "valuation_rule": "xnys_session_close",
    "valuation_timestamps": [
        "2026-07-08T20:00:00Z",
        "2026-07-09T20:00:00Z",
        "2026-07-10T20:00:00Z",
    ],
    "content_hash": "spy-sha256-" + "0" * 64,
}


def _spy_manifest(values, *, start_date="2026-07-08",
                  end_date="2026-07-10", **patch):
    from backtest_summary import canonical_spy_content_hash

    return {
        **SPY_MANIFEST,
        "start_date": start_date,
        "end_date": end_date,
        "valuation_timestamps": sorted(values),
        "content_hash": canonical_spy_content_hash(values),
        **patch,
    }


def _snap(day, value):
    return {
        "timestamp": f"{day}T20:00:00Z",
        "value": value,
    }


def _extract_broker_functions(*names):
    path = Path(__file__).resolve().parents[1] / "broker.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    found = {node.name for node in nodes}
    assert found == set(names), f"missing broker helpers: {set(names) - found}"
    namespace = {}
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"),
        namespace,
    )
    return tuple(namespace[name] for name in names)


def test_restart_equity_never_overwrites_inception_or_high_water():
    state = InceptionState(inception_equity=6000.0, inception_at=T0,
                           high_water_equity=6243.15, source="first_full_funding")
    after_restart = update_inception_state(state, current_equity=5949.05)
    assert after_restart.inception_equity == 6000.0
    assert after_restart.high_water_equity == 6243.15
    new_high = update_inception_state(state, current_equity=6300.0)
    assert new_high.high_water_equity == 6300.0
    assert new_high.inception_equity == 6000.0
    with pytest.raises(ValueError):
        update_inception_state(None, current_equity=0.0)


def test_first_funding_twr_removes_the_june8_deposit():
    # Jun 4: $2,000 in. Jun 8 (pre-flow value 2,010): +$4,000 deposit.
    # Jul 17 end value 6,010. Naive return would be +200.5%; TWR links
    # sub-periods around the flow instead.
    values = [("2026-06-04", 2000.0), ("2026-06-08", 6010.0),
              ("2026-07-17", 6010.0)]
    flows = [("2026-06-08", 4000.0)]
    twr = time_weighted_return(values, flows)
    expected = (2010.0 / 2000.0) * (6010.0 / 6010.0) - 1
    assert twr == pytest.approx(expected, abs=1e-9)


def test_final_funded_window_reproduces_the_july18_numbers():
    values = [("2026-06-08", 6000.0), ("2026-06-15", 6243.15),
              ("2026-07-13", 5879.43), ("2026-07-17", 5979.38)]
    twr = time_weighted_return(values, [])
    assert round(twr * 100, 4) == -0.3437
    peak, trough = 6243.15, 5879.43
    assert round(1 - trough / peak, 6) == pytest.approx(0.058259, abs=1e-6)


def test_activity_ingestion_is_idempotent_by_activity_id():
    page1 = [{"id": "a1", "activity_type": "FILL", "net_amount": -100.0},
             {"id": "a2", "activity_type": "CSD", "net_amount": 4000.0}]
    page2 = [{"id": "a2", "activity_type": "CSD", "net_amount": 4000.0},
             {"id": "a3", "activity_type": "DIV", "net_amount": 7.91}]
    ledger = ingest_activities([page1, page2])
    assert set(ledger) == {"a1", "a2", "a3"}
    again = ingest_activities([page1, page2, page1])
    assert set(again) == {"a1", "a2", "a3"}  # no double counting


def test_ledger_reconciles_the_july18_economics_within_5_cents():
    result = reconcile_ledger(
        fifo_realized_pnl=-71.3420, unrealized_pnl=43.3449,
        dividends=7.91, fees=0.52, account_pnl=-20.62)
    assert result["residual"] == pytest.approx(-0.0129, abs=1e-4)
    assert result["reconciled"] is True
    bad = reconcile_ledger(fifo_realized_pnl=-71.34, unrealized_pnl=43.34,
                           dividends=7.91, fees=0.52, account_pnl=-25.00)
    assert bad["reconciled"] is False


def test_backtest_summary_merges_benchmark_fields_without_replacing_pnl():
    from backtest_summary import compute_backtest_summary
    snapshots = [
        _snap("2026-07-08", 100.0),
        _snap("2026-07-09", 102.0),
        _snap("2026-07-10", 101.0),
    ]
    base = compute_backtest_summary(None, snapshots, 100.0)
    assert base["pnl"] == pytest.approx(1.0)
    assert "benchmark_return" not in base or base["benchmark_return"] is None
    benchmark_values = {
        "2026-07-10T20:00:00Z": 101.0,
        "2026-07-08T20:00:00Z": 100.0,
        "2026-07-09T20:00:00Z": 101.0,
    }
    with_benchmark = compute_backtest_summary(
        None,
        snapshots,
        100.0,
        benchmark_values=benchmark_values,
        benchmark_manifest=_spy_manifest(
            benchmark_values,
            valuation_timestamps=[
                "2026-07-08T20:00:00Z",
                "2026-07-09T20:00:00Z",
                "2026-07-10T20:00:00Z",
            ],
        ),
        trials=7,
    )
    assert with_benchmark["pnl"] == pytest.approx(1.0)  # merged, not replaced
    assert with_benchmark["benchmark_return"] == pytest.approx(0.01)
    assert with_benchmark["active_return"] == pytest.approx(0.0)
    assert with_benchmark["max_drawdown_magnitude"] >= 0
    assert "information_ratio" in with_benchmark
    assert with_benchmark["benchmark_complete"] is True
    assert with_benchmark["benchmark_observations"] == 2
    assert with_benchmark["trial_count"] == 7


def test_backtest_summary_aligns_benchmark_by_timestamp_not_mapping_order():
    from backtest_summary import compute_backtest_summary

    snapshots = [
        _snap("2026-07-08", 100.0),
        _snap("2026-07-09", 110.0),
        _snap("2026-07-10", 121.0),
    ]
    benchmark_out_of_order = {
        "2026-07-10T20:00:00Z": 104.0,
        "2026-07-08T20:00:00Z": 100.0,
        "2026-07-09T20:00:00Z": 102.0,
    }

    summary = compute_backtest_summary(
        None,
        snapshots,
        100.0,
        benchmark_values=benchmark_out_of_order,
        benchmark_manifest=_spy_manifest(benchmark_out_of_order),
        trials=11,
    )

    assert summary["benchmark_return"] == pytest.approx(0.04)
    assert summary["active_return"] == pytest.approx(0.17)
    assert summary["benchmark_complete"] is True


def test_intraday_snapshots_use_the_last_daily_valuation_against_spy_close():
    from backtest_summary import compute_backtest_summary

    snapshots = [
        {"timestamp": "2026-07-08T14:30:00Z", "value": 100.0},
        {"timestamp": "2026-07-08T20:00:00Z", "value": 101.0},
        {"timestamp": "2026-07-09T14:30:00Z", "value": 102.0},
        {"timestamp": "2026-07-09T20:00:00Z", "value": 103.0},
    ]
    benchmark_values = {
        "2026-07-08T20:00:00Z": 500.0,
        "2026-07-09T20:00:00Z": 500.0,
    }
    summary = compute_backtest_summary(
        None,
        snapshots,
        100.0,
        benchmark_values=benchmark_values,
        benchmark_manifest=_spy_manifest(
            benchmark_values,
            end_date="2026-07-09",
        ),
        trials=5,
    )

    assert summary["benchmark_complete"] is True
    assert summary["active_return"] == pytest.approx(103.0 / 101.0 - 1.0)
    assert summary["benchmark_observations"] == 1


def test_missing_benchmark_date_is_explicitly_incomplete_not_dropped():
    from backtest_summary import compute_backtest_summary

    snapshots = [
        _snap("2026-07-08", 100.0),
        _snap("2026-07-09", 101.0),
        _snap("2026-07-10", 102.0),
    ]
    benchmark_values = {
        "2026-07-08T20:00:00Z": 500.0,
        "2026-07-10T20:00:00Z": 505.0,
    }
    summary = compute_backtest_summary(
        None,
        snapshots,
        100.0,
        benchmark_values=benchmark_values,
        benchmark_manifest=_spy_manifest(
            benchmark_values,
            valuation_timestamps=[
                "2026-07-08T20:00:00Z",
                "2026-07-09T20:00:00Z",
                "2026-07-10T20:00:00Z",
            ],
        ),
        trials=3,
    )

    assert summary["pnl"] == pytest.approx(2.0)
    assert summary["benchmark_complete"] is False
    assert "missing benchmark=1" in summary["benchmark_incomplete_reason"]
    assert "active_return" not in summary


def test_missing_registry_trial_count_is_explicitly_incomplete():
    from backtest_summary import compute_backtest_summary

    benchmark_values = {
        "2026-07-08T20:00:00Z": 500.0,
        "2026-07-09T20:00:00Z": 501.0,
    }
    summary = compute_backtest_summary(
        None,
        [_snap("2026-07-08", 100.0), _snap("2026-07-09", 101.0)],
        100.0,
        benchmark_values=benchmark_values,
        benchmark_manifest=_spy_manifest(
            benchmark_values,
            end_date="2026-07-09",
        ),
        trials=None,
    )

    assert summary["benchmark_complete"] is False
    assert summary["trial_count"] is None
    assert "actual experiment-registry count" in (
        summary["benchmark_incomplete_reason"]
    )


@pytest.mark.parametrize(
    ("manifest_patch", "reason"),
    [
        ({"adjustment": "raw"}, "adjustment"),
        ({"timeframe": "1Hour"}, "timeframe"),
        ({"symbol": "QQQ"}, "symbol"),
        ({"price_field": "vw"}, "price_field"),
        ({"total_return": False}, "total_return"),
    ],
)
def test_wrong_spy_price_convention_is_explicitly_incomplete(
    manifest_patch, reason
):
    from backtest_summary import compute_backtest_summary

    benchmark_values = {
        "2026-07-08T20:00:00Z": 500.0,
        "2026-07-09T20:00:00Z": 501.0,
    }
    manifest = _spy_manifest(
        benchmark_values,
        end_date="2026-07-09",
        **manifest_patch,
    )
    summary = compute_backtest_summary(
        None,
        [_snap("2026-07-08", 100.0), _snap("2026-07-09", 101.0)],
        100.0,
        benchmark_values=benchmark_values,
        benchmark_manifest=manifest,
        trials=3,
    )

    assert summary["benchmark_complete"] is False
    assert reason in summary["benchmark_incomplete_reason"]


def test_adjusted_spy_close_series_uses_exact_window_and_close_field():
    from backtest_summary import build_adjusted_spy_close_series

    values = build_adjusted_spy_close_series(
        [
            {"t": "2026-07-07T04:00:00Z", "c": 499.0},
            {"t": "2026-07-08T04:00:00Z", "c": 500.0, "vw": 999.0},
            {"t": "2026-07-09T04:00:00Z", "c": 502.0, "vw": 998.0},
            {"t": "2026-07-10T04:00:00Z", "c": 503.0, "vw": 997.0},
            {"t": "2026-07-11T04:00:00Z", "c": 504.0},
        ],
        start_date="2026-07-08",
        end_date="2026-07-10",
        session_close_resolver=lambda day: datetime(
            day.year,
            day.month,
            day.day,
            20,
            tzinfo=timezone.utc,
        ),
    )

    assert list(values.values()) == [500.0, 502.0, 503.0]
    assert [timestamp.date().isoformat() for timestamp in values] == [
        "2026-07-08",
        "2026-07-09",
        "2026-07-10",
    ]


def test_broker_requests_adjusted_daily_spy_for_exact_user_window():
    (fetch_spy,) = _extract_broker_functions("_fetch_adjusted_spy_benchmark")
    calls = []
    start = datetime(2026, 7, 8)
    end = datetime(2026, 7, 10, 23, 59, 59)
    benchmark_values = {
        "2026-07-08T20:00:00Z": 500.0,
        "2026-07-09T20:00:00Z": 502.0,
        "2026-07-10T20:00:00Z": 503.0,
    }

    def fake_fetch(symbols, start_date, end_date, **kwargs):
        calls.append((symbols, start_date, end_date, kwargs))
        return {
            "SPY": [
                {"t": "2026-07-08T04:00:00Z", "c": 500.0},
                {"t": "2026-07-09T04:00:00Z", "c": 502.0},
                {"t": "2026-07-10T04:00:00Z", "c": 503.0},
            ]
        }

    bundle = fetch_spy(
        fake_fetch,
        start_date=start,
        end_date=end,
        key="test-key",
        secret="test-secret",
        feed="iex",
        registered_manifest=_spy_manifest(benchmark_values),
        session_close_resolver=lambda day: datetime(
            day.year,
            day.month,
            day.day,
            20,
            tzinfo=timezone.utc,
        ),
    )

    symbols, called_start, called_end, kwargs = calls[0]
    assert symbols == ["SPY"]
    assert called_start is start
    assert called_end is end
    assert kwargs == {
        "key": "test-key",
        "secret": "test-secret",
        "timeframe": "1Day",
        "db_conn": None,
        "feed": "iex",
        "adjustment": "all",
    }
    assert bundle["manifest"]["price_field"] == "c"
    assert bundle["manifest"]["total_return"] is True
    assert list(bundle["values"].values()) == [500.0, 502.0, 503.0]


def test_broker_result_summary_passes_timestamp_keyed_spy_and_trials():
    (summarize,) = _extract_broker_functions(
        "_compute_backtest_summary_with_benchmark"
    )
    captured = {}
    bundle = {
        "values": {
            "2026-07-10T20:00:00Z": 503.0,
            "2026-07-08T20:00:00Z": 500.0,
            "2026-07-09T20:00:00Z": 502.0,
        },
        "manifest": SPY_MANIFEST,
    }

    def fake_compute(emulator, snapshots, initial_cash, **kwargs):
        captured.update(kwargs)
        return {"pnl": 1.0}

    result = summarize(
        fake_compute,
        emulator=object(),
        snapshots=[_snap("2026-07-08", 100.0)],
        initial_cash=100.0,
        benchmark_bundle=bundle,
        trials=13,
    )

    assert result == {"pnl": 1.0}
    assert captured == {
        "benchmark_values": bundle["values"],
        "benchmark_manifest": SPY_MANIFEST,
        "trials": 13,
    }

    broker_tree = ast.parse(
        (Path(__file__).resolve().parents[1] / "broker.py").read_text(
            encoding="utf-8"
        )
    )
    writer_calls = [
        node
        for node in ast.walk(broker_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_compute_backtest_summary_with_benchmark"
    ]
    assert writer_calls, "broker result writer bypasses adjusted-SPY summary wiring"


def test_broker_request_params_leave_crypto_and_default_stock_paths_unchanged():
    (build_params,) = _extract_broker_functions(
        "_historical_bars_request_params"
    )

    stock_default = build_params(
        is_crypto=False,
        symbol="SPY",
        start_iso="2026-07-08T00:00:00Z",
        end_iso="2026-07-11T00:00:00Z",
        timeframe="1Day",
        feed="iex",
        adjustment=None,
    )
    stock_adjusted = build_params(
        is_crypto=False,
        symbol="SPY",
        start_iso="2026-07-08T00:00:00Z",
        end_iso="2026-07-11T00:00:00Z",
        timeframe="1Day",
        feed="iex",
        adjustment="all",
    )
    crypto = build_params(
        is_crypto=True,
        symbol="BTC/USD",
        start_iso="2026-07-08T00:00:00Z",
        end_iso="2026-07-11T00:00:00Z",
        timeframe="1Day",
        feed="iex",
        adjustment="all",
    )

    assert "adjustment" not in stock_default
    assert stock_adjusted["adjustment"] == "all"
    assert "adjustment" not in crypto
    assert "feed" not in crypto


def test_crypto_backtest_schema_never_requests_the_equity_benchmark():
    (uses_equity_benchmark,) = _extract_broker_functions(
        "_backtest_uses_equity_benchmark"
    )

    registered = SimpleNamespace(
        spec=SimpleNamespace(benchmark_manifest=SPY_MANIFEST)
    )
    assert uses_equity_benchmark(
        {"name": "crypto:momentum"},
        is_non_equity_runtime=False,
        registered_experiment=registered,
    ) is False
    assert uses_equity_benchmark(
        {"name": "equity-alpha"},
        is_non_equity_runtime=True,
        registered_experiment=registered,
    ) is False
    assert uses_equity_benchmark(
        {"name": "equity-alpha"},
        is_non_equity_runtime=False,
        registered_experiment=None,
    ) is False
    assert uses_equity_benchmark(
        {"name": "equity-alpha"},
        is_non_equity_runtime=False,
        registered_experiment=registered,
    ) is True


def test_broker_reads_the_registered_count_for_one_declared_search_scope():
    from backtest_experiments import BacktestExperimentContext

    schema = {
        "name": "equity-alpha",
        "experiment_search_scope": "untrusted-arbitrary-scope",
    }
    calls = []

    class Registry:
        def trial_count(self, *, scope):
            calls.append(scope)
            return 17

    context = BacktestExperimentContext(
        registry=Registry(),
        registration=SimpleNamespace(
            experiment_id="attempt-1",
            fingerprint="expfp-test",
            search_scope="stored-registered-scope",
        ),
    )

    assert context.trial_count() == 17
    assert calls == ["stored-registered-scope"]
    assert schema["experiment_search_scope"] != context.search_scope


def test_live_broker_fetch_inception_fallback_is_removed():
    from live_broker_fetch import resolve_inception
    initial, unavailable = resolve_inception(
        [{"value": 6000.0}, {"value": 5979.38}])
    assert initial == 6000.0 and unavailable is False
    initial, unavailable = resolve_inception([])
    assert initial is None
    assert unavailable is True  # NEVER current equity; P&L must not read 0


def test_twr_refuses_a_flow_without_a_matching_valuation_point():
    """Bug sweep 2026-07-18: a deposit dated between valuation points was
    silently ignored, reporting +200.5% instead of removing the deposit."""
    with pytest.raises(ValueError):
        time_weighted_return([("2026-06-04", 2000.0), ("2026-07-17", 6010.0)],
                             [("2026-06-08", 4000.0)])


def test_twr_collapses_duplicate_valuation_dates_before_flows():
    """Audit: a flow on a duplicated date was applied to every sub-period
    ending that date — a flat account reported -10%."""
    twr = time_weighted_return(
        [("2026-01-02", 110.0), ("2026-01-02", 110.0), ("2026-01-03", 110.0)],
        [("2026-01-02", 10.0)])
    # Only points strictly before the flow date can anchor; with none, the
    # measurable growth from the flow-adjusted base is flat.
    assert twr == pytest.approx(0.0)


def test_twr_rejects_non_positive_valuations():
    with pytest.raises(ValueError):
        time_weighted_return([("2026-01-02", 0.0), ("2026-01-03", 100.0)], [])


def test_backtest_summary_skips_unusable_snapshots_and_never_emits_nan():
    """Audit: a None snapshot value became 0.0, poisoning metrics with NaN
    and a false 100% drawdown, then breaking JSON serialization."""
    import json as _json
    import math as _math
    from backtest_summary import compute_backtest_summary
    snapshots = [
        _snap("2026-07-07", 100.0),
        _snap("2026-07-08", None),
        _snap("2026-07-09", 110.0),
        _snap("2026-07-10", 120.0),
    ]
    benchmark_values = {
        "2026-07-07T20:00:00Z": 100.0,
        "2026-07-08T20:00:00Z": 101.0,
        "2026-07-09T20:00:00Z": 102.0,
        "2026-07-10T20:00:00Z": 103.0,
    }
    summary = compute_backtest_summary(
        None,
        snapshots,
        100.0,
        benchmark_values=benchmark_values,
        benchmark_manifest=_spy_manifest(
            benchmark_values,
            start_date="2026-07-07",
        ),
        trials=4,
    )
    for key, value in summary.items():
        if isinstance(value, float):
            assert _math.isfinite(value), key
    _json.dumps(summary, allow_nan=False)  # must serialize
    assert summary["benchmark_complete"] is False
    assert "finite positive" in summary["benchmark_incomplete_reason"]
