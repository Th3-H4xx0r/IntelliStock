import os
import sys
import types
from datetime import datetime
from unittest.mock import patch

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as gna

from strategies.graph_nexus_analysis import (  # noqa: E402
    _apply_quality_filter,
    _build_position_health_snapshot,
    _build_nexus_candidate_meta,
    _classify_candidate_signal,
    _collect_backfill_queue_candidates,
    _compute_backfill_budget_partition,
    _compute_drawdown_halt_backfill_budget_pct,
    _apply_portfolio_drawdown_halt,
    _enqueue_backfill_candidate,
    _evaluate_trend_sell_enforcement,
    _finalize_scores,
    _get_active_watchlist_state,
    _get_effective_nexus_config,
    _normalize_llm_rows_and_traces,
    _normalize_overlay_worker_result,
    _plan_backfill_buy_allocation,
    _plan_executable_stock_buy_slate,
    _plan_winner_adds,
    _rotation_candidate_allowed,
    _rotation_winner_lock_active,
    _should_reject_backfill_candidate,
    _should_reserve_backfill_budget,
)
from nexus_broker_utils import build_nexus_buy_guard, get_nexus_buy_block_details, get_nexus_buy_block_reason  # noqa: E402


class MockPortfolioEmulator:
    def __init__(self, initial_cash: float = 5000.0):
        self._cash = float(initial_cash)
        self._initial_value = float(initial_cash)
        self._positions: dict[str, float] = {}
        self._trades: list[dict] = []

    def seed_position(self, ticker: str, shares: float, entry_price: float, *, buy_time: datetime):
        self._positions[ticker] = float(shares)
        self._trades.append(
            {
                "ticker": ticker,
                "action": "buy",
                "price": float(entry_price),
                "shares": float(shares),
                "timestamp": buy_time,
            }
        )

    def get_trade_history(self) -> list[dict]:
        return list(self._trades)

    def get_cash(self) -> float:
        return self._cash

    def get_portfolio_value(self, prices: dict) -> float:
        value = self._cash
        for ticker, shares in self._positions.items():
            value += shares * float((prices or {}).get(ticker, 0.0) or 0.0)
        return value

    def get_positions_value(self, prices: dict) -> float:
        value = 0.0
        for ticker, shares in self._positions.items():
            value += shares * float((prices or {}).get(ticker, 0.0) or 0.0)
        return value


def _bars(close: float, volume: float, count: int = 20) -> list[dict]:
    return [{"c": float(close), "v": float(volume)} for _ in range(count)]


def test_quality_filter_uses_bar_list_shape_for_avg_volume():
    scores = {
        "THIN": {
            "score": 1,
            "reason": "Direct trend_momentum sentiment=+1",
            "action_intent": "initial_buy",
            "quality_metadata": {"market_cap": 1_200_000_000},
        }
    }
    updated = _apply_quality_filter(
        scores,
        ["THIN"],
        {"THIN": 18.0},
        {"THIN": _bars(18.0, 75_000, 25)},
        None,
        {"min_avg_volume": 200_000, "quality_filter_missing_metadata_policy": "warn"},
        strategy_cache={},
    )
    assert updated["THIN"]["score"] == 0
    assert "avg_volume" in updated["THIN"]["reason"]


def test_quality_filter_can_block_on_missing_market_cap_metadata():
    scores = {
        "METALESS": {
            "score": 1,
            "reason": "Direct trend_momentum sentiment=+1",
            "action_intent": "initial_buy",
        }
    }
    updated = _apply_quality_filter(
        scores,
        ["METALESS"],
        {"METALESS": 22.0},
        {"METALESS": _bars(22.0, 400_000, 25)},
        None,
        {"quality_filter_missing_metadata_policy": "block"},
        strategy_cache={},
    )
    assert updated["METALESS"]["score"] == 0
    assert "missing market_cap metadata" in updated["METALESS"]["reason"]


def test_quality_filter_uses_structured_propagation_fields():
    scores = {
        "PROP": {
            "score": 1,
            "reason": "BUY candidate",
            "action_intent": "initial_buy",
            "raw_net_score": 0.12,
            "n_paths": 1,
            "signal_family": "propagation",
            "is_propagation_candidate": True,
            "quality_metadata": {"market_cap": 2_000_000_000, "avg_volume": 800_000},
        }
    }
    updated = _apply_quality_filter(
        scores,
        ["PROP"],
        {"PROP": 45.0},
        {"PROP": _bars(45.0, 800_000, 25)},
        None,
        {"propagation_min_paths": 2, "propagation_min_raw_score": 0.30},
        strategy_cache={},
    )
    assert updated["PROP"]["score"] == 0
    assert "propagation" in updated["PROP"]["reason"]


def test_classify_candidate_signal_falls_back_to_graph_reason_and_cache():
    family, is_prop = _classify_candidate_signal(
        "SNDK",
        {"reason": "Graph(13 paths, raw=+2.431): institutional(...)", "event": ""},
        strategy_cache={"_prop_expansion_set_alloc": {"SNDK"}},
    )
    assert family == "propagation"
    assert is_prop is True


def test_trend_sell_enforcement_blocks_early_sell():
    portfolio = MockPortfolioEmulator()
    portfolio.seed_position("CE", 10.0, 100.0, buy_time=datetime(2026, 3, 20, 13, 0, 0))
    result = _evaluate_trend_sell_enforcement(
        "CE",
        portfolio_emulator=portfolio,
        prices={"CE": 98.0},
        price_history={"CE": _bars(98.0, 500_000, 20)},
        propagated={"CE": {"raw_score": -0.8}},
        # V31: opt out of grace period to unit-test the legacy min_hold_days gate
        config={"sell_enforcement_min_hold_days": 5, "initial_grace_enabled": False},
        strategy_cache={},
        date_key="2026-03-23",
    )
    assert result["allow_force_sell"] is False
    assert "held 3d < 5d" in result["skip_reason"]


def test_trend_sell_enforcement_requires_consecutive_neutral_days():
    portfolio = MockPortfolioEmulator()
    portfolio.seed_position("AAOI", 10.0, 100.0, buy_time=datetime(2026, 3, 10, 13, 0, 0))
    cache = {}
    cfg = {
        "sell_enforcement_min_hold_days": 0,
        "sell_enforcement_consecutive_neutral_days": 3,
        # V31: opt out of grace period to unit-test the legacy consecutive-neutral-days gate
        "initial_grace_enabled": False,
    }
    day1 = _evaluate_trend_sell_enforcement(
        "AAOI",
        portfolio_emulator=portfolio,
        prices={"AAOI": 97.0},
        price_history={"AAOI": _bars(97.0, 700_000, 20)},
        propagated={"AAOI": {"raw_score": -0.1}},
        config=cfg,
        strategy_cache=cache,
        date_key="2026-03-20",
    )
    day2 = _evaluate_trend_sell_enforcement(
        "AAOI",
        portfolio_emulator=portfolio,
        prices={"AAOI": 96.0},
        price_history={"AAOI": _bars(96.0, 700_000, 20)},
        propagated={"AAOI": {"raw_score": -0.2}},
        config=cfg,
        strategy_cache=cache,
        date_key="2026-03-21",
    )
    day3 = _evaluate_trend_sell_enforcement(
        "AAOI",
        portfolio_emulator=portfolio,
        prices={"AAOI": 95.0},
        price_history={"AAOI": _bars(95.0, 700_000, 20)},
        propagated={"AAOI": {"raw_score": -0.25}},
        config=cfg,
        strategy_cache=cache,
        date_key="2026-03-22",
    )
    assert day1["allow_force_sell"] is False
    assert day2["allow_force_sell"] is False
    assert day3["allow_force_sell"] is True


def test_event_maintenance_prompt_limits_auto_reduce_gpt_oss_lookback():
    limits = gna._get_event_maintenance_prompt_limits(
        {
            "historical_lookback_mode": True,
            "event_maintenance_llm_provider": "azure",
            "event_maintenance_llm_model": "gpt-oss-120b",
            "max_events_in_prompt": 200,
            "event_maintenance_candidate_batch_size": 8,
        }
    )
    assert limits["is_gpt_oss"] is True
    assert limits["max_events_in_prompt"] == 35
    assert limits["candidate_batch_size"] == 4
    assert limits["auto_reduced"] is True


def test_sentiment_prompt_limits_auto_reduce_gpt_oss_lookback():
    limits = gna._get_sentiment_prompt_limits(
        {
            "historical_lookback_mode": True,
            "sentiment_llm_provider": "azure",
            "sentiment_llm_model": "gpt-oss-120b",
            "num_articles_for_llm": 30,
        }
    )
    assert limits["is_gpt_oss"] is True
    assert limits["max_articles"] == 12
    assert limits["max_pending_trades"] == 8
    assert limits["max_history_tickers"] == 4
    assert limits["max_history_entries"] == 4
    assert limits["max_output_tokens"] == 0
    assert limits["auto_reduced"] is True


def test_enhanced_sentiment_retry_shrinks_prompt_for_gpt_oss():
    articles = [{"headline": f"Headline {i}"} for i in range(30)]
    pending = [
        {"symbol": f"T{i}", "signal": 1, "target_date": "2025-10-30", "reason": "test", "source_date": "2025-10-22"}
        for i in range(10)
    ]
    ticker_history = {
        f"T{i}": [{"d": "2025-10-21", "h": f"Past headline {i}-{j}"} for j in range(6)]
        for i in range(6)
    }
    prompts: list[str] = []
    logs: list[str] = []

    def _fake_call(*args, **kwargs):
        prompts.append(str(args[3] or ""))
        if len(prompts) < 3:
            return None
        return gna._EnhancedSentimentResponse(sentiment=[])

    cfg = {
        "historical_lookback_mode": True,
        "sentiment_llm_provider": "azure",
        "sentiment_llm_api_key": "azure-key",
        "sentiment_llm_model": "gpt-oss-120b",
        "num_articles_for_llm": 30,
    }

    with patch.object(gna, "_log", side_effect=lambda msg, *_args, **_kwargs: logs.append(str(msg))), \
         patch.object(gna, "_build_trends_context", return_value="\nACTIVE TRENDS:\n- Example trend"), \
         patch.object(gna, "call_structured_llm_by_provider", side_effect=_fake_call):
        result, future, cancellations, trend_updates = gna._enhanced_sentiment_from_llm(
            articles,
            "azure",
            "azure-key",
            "gpt-oss-120b",
            num_articles=30,
            today_str="2025-10-22",
            pending_trades=pending,
            ticker_history=ticker_history,
            active_trends=[{"id": "semis", "name": "Semis", "direction": "bullish"}],
            additional_context="ANALYST RATINGS\n" + ("X" * 4000),
            provider_config={"reasoning_effort": "medium"},
            config=cfg,
        )

    assert result == {}
    assert future == []
    assert cancellations == []
    assert isinstance(trend_updates, dict)
    assert len(prompts) == 3
    assert len(prompts[1]) < len(prompts[0])
    assert len(prompts[2]) < len(prompts[1])
    assert any("auto-reduced prompt context" in msg for msg in logs)
    assert any("retry 1/2: shrinking prompt context" in msg for msg in logs)
    assert any("retry 2/2: shrinking prompt context" in msg for msg in logs)


def test_active_event_maintenance_retry_shrinks_prompt_context_for_gpt_oss():
    current_events = [
        {
            "event_cluster_key": f"evt-{i}",
            "event_name": f"Event {i}",
            "event_type": "government",
            "government_action_type": "none",
            "status": "live",
            "last_confirmed_date": "2025-10-21",
            "confidence": 0.9,
        }
        for i in range(80)
    ]
    candidates = [
        {
            "event_cluster_key": f"cand-{i}",
            "event_name": f"Candidate {i}",
            "event_type": "government",
            "government_action_type": "none",
            "status": "live",
            "start_date": "2025-10-22",
            "supporting_article_hashes": [f"hash-{i}"],
        }
        for i in range(4)
    ]
    prompts: list[str] = []

    def _fake_call(*args, **kwargs):
        prompts.append(str(args[3] or ""))
        if len(prompts) == 1:
            return None
        return gna._ActiveEventMaintenanceResponse(updates=[])

    cfg = {
        "historical_lookback_mode": True,
        "event_maintenance_llm_provider": "azure",
        "event_maintenance_llm_api_key": "azure-key",
        "event_maintenance_llm_model": "gpt-oss-120b",
        "max_events_in_prompt": 200,
        "event_maintenance_candidate_batch_size": 8,
    }

    captured_logs: list[str] = []
    with patch.object(gna, "_log", side_effect=lambda msg, *_args, **_kwargs: captured_logs.append(str(msg))), \
         patch.object(gna, "_derive_event_candidates_from_macro_rows", return_value=candidates), \
         patch.object(gna, "_load_active_events_as_of", side_effect=[current_events, current_events]), \
         patch.object(gna, "_load_active_event_maintenance_cache_doc", return_value=None), \
         patch.object(gna, "_store_active_event_maintenance_cache_doc"), \
         patch.object(gna, "_ensure_nexus_history_table"), \
         patch.object(gna, "_r", None), \
         patch.object(gna, "call_structured_llm_by_provider", side_effect=_fake_call), \
         patch.object(gna, "get_last_structured_llm_call_metadata", return_value={"provider": "azure", "effective_model": "gpt-oss-120b", "ok": False, "usage": {}}):
        gna._maintain_active_events(
            object(),
            cfg,
            instance_id="test-run",
            date_key="2025-10-22",
            macro_rows=[{"article_hash": "macro-a"}],
        )

    assert len(prompts) == 2
    assert len(prompts[1]) < len(prompts[0])
    assert any("auto-reduced prompt context" in msg for msg in captured_logs)
    assert any("shrinking current-event context" in msg for msg in captured_logs)


def test_backfill_budget_partition_and_priority_allocation():
    primary, reserve = _compute_backfill_budget_partition(
        1000.0,
        True,
        {"backfill_budget_reserve_pct": 0.20},
    )
    assert primary == 800.0
    assert reserve == 200.0
    alloc, bucket = _plan_backfill_buy_allocation(
        {"ticker": "SNDK", "raw_net_score": 2.431, "signal_source": "watchlist_priority", "is_watchlist_priority": True},
        2.431,
        priority_budget=100.0,
        standard_budget=0.0,
        min_position_size=50.0,
        headroom=1,
        config={"priority_min_position_size": 100.0, "priority_budget_can_bypass_regular_min": True},
    )
    assert alloc == 100.0
    assert bucket == "priority"


def test_enqueue_backfill_candidate_protects_high_score_entries():
    queue = [
        {"ticker": "SNDK", "raw_net_score": 2.431, "signal_source": "watchlist_priority", "is_watchlist_priority": True},
        {"ticker": "MU", "raw_net_score": 1.200, "signal_source": "propagation_expansion", "is_propagation_expansion": True},
    ]
    status, replaced = _enqueue_backfill_candidate(
        queue,
        {"ticker": "WEAK", "raw_net_score": 0.450, "signal_source": "propagation"},
        max_size=2,
        reserved_priority_slots=1,
    )
    assert status == "full_general_blocked"
    assert replaced is None
    assert {item["ticker"] for item in queue} == {"SNDK", "MU"}


def test_enqueue_backfill_candidate_priority_evicts_general_when_reserved_slots_open():
    queue = [
        {"ticker": "GEN1", "raw_net_score": 0.51, "signal_source": "direct"},
        {"ticker": "GEN2", "raw_net_score": 0.42, "signal_source": "propagation"},
    ]
    status, replaced = _enqueue_backfill_candidate(
        queue,
        {"ticker": "LITE", "raw_net_score": 0.77, "signal_source": "watchlist_priority", "is_watchlist_priority": True},
        max_size=2,
        reserved_priority_slots=1,
    )
    assert status == "priority_evicted_general"
    assert replaced == "GEN2"
    assert {item["ticker"] for item in queue} == {"GEN1", "LITE"}


def test_drawdown_halt_backfill_budget_uses_graduated_thresholds():
    cfg = {
        "portfolio_drawdown_halt_pct": 15.0,
        "portfolio_drawdown_halt_backfill_budget_pct": 0.50,
        "portfolio_drawdown_halt_backfill_reduce_pct": 20.0,
        "portfolio_drawdown_halt_backfill_reduce_budget_pct": 0.25,
        "portfolio_drawdown_halt_backfill_stop_pct": 25.0,
    }
    assert _compute_drawdown_halt_backfill_budget_pct(10.0, cfg) == 1.0
    assert _compute_drawdown_halt_backfill_budget_pct(15.0, cfg) == 0.50
    assert _compute_drawdown_halt_backfill_budget_pct(22.0, cfg) == 0.25
    assert _compute_drawdown_halt_backfill_budget_pct(25.0, cfg) == 0.0


def test_quality_filter_blocks_single_path_propagation_even_with_high_raw_score():
    scores = {
        "TTGT": {
            "score": 1,
            "reason": "BUY candidate",
            "action_intent": "initial_buy",
            "raw_net_score": 1.0,
            "n_paths": 1,
            "signal_family": "propagation",
            "is_propagation_candidate": True,
            "quality_metadata": {"market_cap": 3_000_000_000, "avg_volume": 900_000},
        }
    }
    updated = _apply_quality_filter(
        scores,
        ["TTGT"],
        {"TTGT": 45.0},
        {"TTGT": _bars(45.0, 900_000, 25)},
        None,
        {"propagation_min_paths": 2, "propagation_min_raw_score": 0.30},
        strategy_cache={},
    )
    assert updated["TTGT"]["score"] == 0
    assert "blocked regardless of propagation floor" in updated["TTGT"]["reason"]


def test_backfill_reserve_requested_before_queue_exists():
    assert _should_reserve_backfill_budget(False, ["MU"], []) is True
    assert _should_reserve_backfill_budget(False, [], [{"ticker": "MU"}]) is True
    assert _should_reserve_backfill_budget(False, [], []) is False


def test_backfill_queue_grace_preserves_broker_skipped_name():
    reject, threshold, reason = _should_reject_backfill_candidate(
        {
            "ticker": "MU",
            "signal_source": "broker_skipped",
            "bars_in_queue": 2,
            "raw_net_score": 0.496,
        },
        current_score=0.0,
        buy_threshold=0.15,
        config={"backfill_queue_grace_bars": 3},
    )
    assert reject is False
    assert threshold == 0.15
    assert reason == "grace"


def test_backfill_queue_rejects_flat_name_after_grace_window():
    # V20: With queued-score carry-forward, a stock queued at 0.496 stays fillable
    # because fill_score = max(0.0, 0.496 * 0.50) = 0.248 >= effective_threshold.
    # Use a VERY low queued score to test actual rejection.
    reject, threshold, reason = _should_reject_backfill_candidate(
        {
            "ticker": "MU",
            "signal_source": "broker_skipped",
            "bars_in_queue": 5,
            "raw_net_score": 0.10,  # V20: lowered to test real rejection
        },
        current_score=0.0,
        buy_threshold=0.15,
        config={"backfill_queue_grace_bars": 3},
    )
    assert reject is True
    assert reason == "decayed_score"


def test_backfill_queue_priority_grace_extends_watchlist_survival():
    reject, threshold, reason = _should_reject_backfill_candidate(
        {
            "ticker": "SNDK",
            "signal_source": "watchlist_priority",
            "is_watchlist_priority": True,
            "is_watchlist_match": True,
            "grace_eligible": True,
            "bars_in_queue": 4,
            "raw_net_score": 2.431,
        },
        current_score=0.0,
        buy_threshold=0.15,
        config={"backfill_queue_grace_bars": 3, "backfill_queue_priority_grace_bars": 8},
    )
    assert reject is False
    assert threshold == 0.15
    assert reason == "grace"


def test_rotation_candidate_blocks_early_rotation():
    allow, delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=-1.0,
        held_rotation_score=-0.2,
        held_days=1,
        incoming_raw_score=1.2,
        incoming_rotation_score=1.4,
        config={"rotation_min_hold_days": 10, "rotation_min_delta": 0.15},
    )
    assert allow is False
    assert delta > 0.0
    assert reason == "min_hold"


def test_rotation_candidate_requires_stronger_delta_for_profitable_hold():
    allow, delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=1.5,
        held_rotation_score=0.4,
        held_days=6,
        incoming_raw_score=1.4,
        incoming_rotation_score=1.1,
        config={
            "rotation_min_hold_days": 10,
            "rotation_min_delta": 0.15,
            "rotation_profitable_min_delta": 1.5,
        },
    )
    assert allow is False
    assert round(delta, 3) == 0.7
    assert reason == "profitable_min_hold"


def test_rotation_winner_lock_blocks_healthy_profitable_winner():
    allow, delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=4.3,
        held_rotation_score=0.444,
        held_days=6,
        held_raw_score=0.31,
        drop_from_peak_pct=2.0,
        is_equity=True,
        incoming_raw_score=2.264,
        incoming_rotation_score=2.266,
        config={},
    )
    assert allow is False
    assert round(delta, 3) == 1.822
    assert reason == "winner_lock"


def test_rotation_candidate_break_glass_allows_partial_trim():
    allow, delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=12.0,
        held_rotation_score=0.3,
        held_days=12,
        held_raw_score=0.45,
        drop_from_peak_pct=1.5,
        is_equity=True,
        incoming_raw_score=3.1,
        incoming_rotation_score=2.8,
        config={},
    )
    assert allow is True
    assert round(delta, 3) == 2.5
    assert reason == "break_glass_trim"


def test_rotation_winner_lock_helper_flags_healthy_winner():
    locked, reason = _rotation_winner_lock_active(
        held_pnl_pct=8.2,
        held_days=7,
        held_raw_score=0.4,
        drop_from_peak_pct=3.0,
        is_equity=True,
        config={},
    )
    assert locked is True
    assert reason == "winner_lock"


def test_executable_stock_slate_balances_top_candidates():
    funded, queued, meta = _plan_executable_stock_buy_slate(
        [
            {"ticker": "MU", "raw_net_score": 2.4},
            {"ticker": "SNDK", "raw_net_score": 2.1},
            {"ticker": "LITE", "raw_net_score": 1.8},
            {"ticker": "NVDA", "raw_net_score": 1.6},
            {"ticker": "AMD", "raw_net_score": 0.48},
            {"ticker": "INTC", "raw_net_score": 0.20},
        ],
        1000.0,
        min_position_size=100.0,
        config={},
    )
    assert [item["ticker"] for item in funded] == ["MU", "SNDK", "LITE", "NVDA"]
    assert [item["buy_cash"] for item in funded] == [400.0, 250.0, 200.0, 150.0]
    assert queued == ["AMD", "INTC"]
    assert meta["profile"] == "balanced"


def test_executable_stock_slate_queues_when_cash_not_executable():
    funded, queued, meta = _plan_executable_stock_buy_slate(
        [{"ticker": "MU", "raw_net_score": 1.2}],
        26.60,
        min_position_size=50.0,
        config={},
    )
    assert funded == []
    assert queued == ["MU"]
    assert meta["selected"] == []


def test_winner_add_funds_one_follow_on_add():
    funded, remaining = _plan_winner_adds(
        [
            {
                "ticker": "MU",
                "is_equity": True,
                "held_days": 6,
                "unrealized_pct": 9.0,
                "raw_net_score": 0.60,
                "drop_from_peak_pct": 2.0,
                "existing_add_count": 0,
                "entry_notional": 1000.0,
            }
        ],
        1000.0,
        min_position_size=50.0,
        config={},
    )
    assert len(funded) == 1
    assert funded[0]["ticker"] == "MU"
    assert funded[0]["buy_cash"] == 500.0
    assert remaining == 500.0


def test_winner_add_respects_max_count():
    funded, remaining = _plan_winner_adds(
        [
            {
                "ticker": "SNDK",
                "is_equity": True,
                "held_days": 9,
                "unrealized_pct": 12.0,
                "raw_net_score": 0.70,
                "drop_from_peak_pct": 1.0,
                "existing_add_count": 1,
                "entry_notional": 1200.0,
            }
        ],
        1000.0,
        min_position_size=50.0,
        config={},
    )
    assert funded == []
    assert remaining == 1000.0


def test_position_health_snapshot_ignores_stale_peak_from_prior_entry():
    portfolio = MockPortfolioEmulator()
    portfolio._trades.extend(
        [
            {
                "ticker": "ABC",
                "action": "buy",
                "price": 50.0,
                "shares": 10.0,
                "timestamp": datetime(2026, 3, 1, 13, 0, 0),
            },
            {
                "ticker": "ABC",
                "action": "sell",
                "price": 200.0,
                "shares": 10.0,
                "timestamp": datetime(2026, 3, 8, 13, 0, 0),
            },
            {
                "ticker": "ABC",
                "action": "buy",
                "price": 100.0,
                "shares": 10.0,
                "timestamp": datetime(2026, 3, 10, 13, 0, 0),
            },
        ]
    )
    portfolio._positions["ABC"] = 10.0
    cache = {"_peak_ABC": 200.0}
    snapshot = _build_position_health_snapshot(
        "ABC",
        portfolio_emulator=portfolio,
        prices={"ABC": 110.0},
        price_history={"ABC": _bars(110.0, 700_000, 20)},
        strategy_cache=cache,
        date_key="2026-03-20",
        raw_score=0.4,
    )
    assert snapshot["entry_key"] == "2026-03-10T13:00:00"
    assert snapshot["drop_from_peak_pct"] == 0.0
    assert "_peak_ABC" not in cache


def test_finalize_scores_uses_entry_scoped_peak_after_rebuy():
    portfolio = MockPortfolioEmulator()
    portfolio._trades.extend(
        [
            {
                "ticker": "ABC",
                "action": "buy",
                "price": 50.0,
                "shares": 10.0,
                "timestamp": datetime(2026, 3, 1, 13, 0, 0),
            },
            {
                "ticker": "ABC",
                "action": "sell",
                "price": 200.0,
                "shares": 10.0,
                "timestamp": datetime(2026, 3, 8, 13, 0, 0),
            },
            {
                "ticker": "ABC",
                "action": "buy",
                "price": 100.0,
                "shares": 10.0,
                "timestamp": datetime(2026, 3, 10, 13, 0, 0),
            },
        ]
    )
    portfolio._positions["ABC"] = 10.0
    cache = {"_peak_ABC": 200.0}
    scores = _finalize_scores(
        ["ABC"],
        {},
        {},
        {"trailing_stop_activation_pct": 10.0, "trailing_stop_pct": 8.0, "max_hold_days": 45},
        portfolio_emulator=portfolio,
        date_key="2026-03-20",
        prices={"ABC": 110.0},
        strategy_cache=cache,
        price_history={"ABC": _bars(110.0, 700_000, 20)},
    )
    assert scores["ABC"]["score"] == 0
    assert "Trailing stop" not in scores["ABC"]["reason"]
    assert "_peak_ABC" not in cache
    assert cache["_peak_ABC_2026-03-10T13:00:00"] == 110.0


def test_trend_sell_enforcement_migrates_legacy_list_tracker_to_consecutive_count():
    portfolio = MockPortfolioEmulator()
    portfolio.seed_position("AAOI", 10.0, 100.0, buy_time=datetime(2026, 3, 10, 13, 0, 0))
    cache = {"_sell_enforcement_neutral_tracker": {"AAOI": [1, 1]}}
    result = _evaluate_trend_sell_enforcement(
        "AAOI",
        portfolio_emulator=portfolio,
        prices={"AAOI": 95.0},
        price_history={"AAOI": _bars(95.0, 700_000, 20)},
        propagated={"AAOI": {"raw_score": -0.2}},
        config={
            "sell_enforcement_min_hold_days": 0,
            "sell_enforcement_consecutive_neutral_days": 3,
            # V31: opt out of grace period to unit-test the legacy migration path
            "initial_grace_enabled": False,
        },
        strategy_cache=cache,
        date_key="2026-03-20",
    )
    assert result["allow_force_sell"] is True
    assert result["neutral_days"] == 3
    assert cache["_sell_enforcement_neutral_tracker"]["AAOI"] == 3


def test_nexus_buy_guard_blocks_unsized_nexus_only_buy():
    guard = build_nexus_buy_guard(
        [{"strategy": "graph_nexus_analysis", "decision": 1, "weight": 1.0}],
        "MU",
        1,
        [],
        {"_buy_price_floor": 5.0},
    )
    assert guard["is_nexus_only_buy"] is True
    assert get_nexus_buy_block_reason("MU", 240.0, guard) == "Nexus execution gate: MU not in executable buy whitelist"
    details = get_nexus_buy_block_details("MU", 240.0, guard)
    assert details == {
        "code": "not_whitelisted",
        "message": "Nexus execution gate: MU not in executable buy whitelist",
    }


def test_nexus_buy_guard_blocks_sub_five_execution_price_for_stock():
    guard = build_nexus_buy_guard(
        [{"strategy": "graph_nexus_analysis", "decision": 1, "weight": 1.0}],
        "APVO",
        1,
        ["APVO"],
        {"APVO": {"buy_cash": 250.0, "asset_class": "stock"}, "_buy_price_floor": 5.0},
    )
    assert get_nexus_buy_block_reason("APVO", 1.49, guard) == "Nexus execution price floor: APVO at $1.49 is below $5.00"


def test_build_nexus_candidate_meta_distinguishes_member_from_active_priority():
    meta = _build_nexus_candidate_meta(
        "SNDK",
        {"raw_net_score": 2.431, "reason": "Graph(13 paths, raw=+2.431): institutional(...)"},
        {"sector_watchlist": {"Semiconductors": ["SNDK", "MU"]}},
        strategy_cache={"_prop_expansion_set_alloc": {"SNDK"}},
    )
    assert meta["signal_source"] == "propagation_expansion"
    assert meta["is_watchlist_member"] is True
    assert meta["is_watchlist_priority"] is False
    assert meta["is_watchlist_match"] is True
    assert meta["is_propagation_expansion"] is True
    assert meta["grace_eligible"] is True


def test_build_nexus_candidate_meta_uses_active_watchlist_priority_context():
    meta = _build_nexus_candidate_meta(
        "MU",
        {"raw_net_score": 0.747, "reason": "Direct earnings sentiment=+1"},
        {"sector_watchlist": {"Semiconductors": ["SNDK", "MU"]}},
        strategy_cache={"_active_watchlist_priority_tickers": {"MU"}},
    )
    assert meta["is_watchlist_member"] is True
    assert meta["is_watchlist_priority"] is True
    assert meta["signal_source"] == "watchlist_priority"


def test_build_nexus_candidate_meta_legacy_watchlist_match_marks_member_not_priority():
    meta = _build_nexus_candidate_meta(
        "LITE",
        {"raw_net_score": 0.62, "reason": "Direct earnings sentiment=+1"},
        {"sector_watchlist": {"Semiconductors": ["SNDK", "MU", "LITE"]}},
        strategy_cache={},
        is_watchlist_match=True,
    )
    assert meta["is_watchlist_member"] is True
    assert meta["is_watchlist_priority"] is False


def test_get_active_watchlist_state_marks_only_active_matching_sectors():
    state = _get_active_watchlist_state(
        [
            {
                "status": "active",
                "direction": "bullish",
                "strength": 0.8,
                "affected_sectors": ["Semiconductors"],
                "name": "Semiconductors leadership",
            },
            {
                "status": "active",
                "direction": "bearish",
                "strength": 0.9,
                "affected_sectors": ["Banks"],
                "name": "Banks weakness",
            },
        ],
        {"sector_watchlist": {"Semiconductors": ["MU", "SNDK"], "Banks": ["JPM"]}},
    )
    assert state["matched_keys"] == ["Semiconductors"]
    assert state["priority_tickers"] == {"MU", "SNDK"}


def test_get_active_watchlist_state_matches_semiconductor_variants():
    state = _get_active_watchlist_state(
        [
            {
                "status": "active",
                "direction": "bullish",
                "strength": 0.75,
                "affected_sectors": ["Semiconductor Equipment"],
                "name": "Semis leadership",
            }
        ],
        {"sector_watchlist": {"Semiconductors": ["MU", "SNDK", "LITE"]}},
    )
    assert state["matched_keys"] == ["Semiconductors"]
    assert state["priority_tickers"] == {"MU", "SNDK", "LITE"}


def test_collect_backfill_queue_candidates_recovers_priority_buys_from_final_state():
    scores = {
        "MU": {
            "score": 1,
            "raw_net_score": 0.747,
            "is_watchlist_priority": True,
            "is_watchlist_member": True,
            "is_propagation_expansion": True,
        },
        "LITE": {
            "score": 1,
            "raw_net_score": 0.621,
            "is_watchlist_member": True,
            "is_propagation_expansion": True,
        },
        "SMH": {"score": 1, "raw_net_score": 1.0},
    }
    queue_syms = _collect_backfill_queue_candidates(
        pre_queue_candidates=[],
        final_buy_symbols=["SMH"],
        etf_set={"SMH"},
        scores=scores,
        prop_expansion_buys=["MU", "LITE"],
    )
    assert queue_syms == ["MU", "LITE"]


def test_nexus_buy_guard_does_not_block_buy_when_other_strategy_also_votes_buy():
    guard = build_nexus_buy_guard(
        [
            {"strategy": "graph_nexus_analysis", "decision": 1, "weight": 1.0},
            {"strategy": "mean_reversion", "decision": 1, "weight": 1.0},
        ],
        "MU",
        1,
        [],
        {"_buy_price_floor": 5.0},
    )
    assert guard["is_nexus_only_buy"] is False
    assert get_nexus_buy_block_reason("MU", 240.0, guard) is None


def test_effective_config_reports_v9_defaults():
    cfg = _get_effective_nexus_config({})
    assert cfg["pool_a_base"] == 8
    assert cfg["pool_b_base"] == 4
    assert cfg["max_stock_buys_per_day"] == 10
    assert cfg["portfolio_drawdown_halt_pct"] == 15.0
    assert cfg["portfolio_drawdown_resume_up_days"] == 2
    # 2026-05-07: default flipped from "warn" → "block" (fail-closed). Empty
    # market_cap was letting micro-caps slip through silently.
    assert cfg["quality_filter_missing_metadata_policy"] == "block"
    # 2026-05-07: derived label flips with the underlying policy.
    # `quality_market_cap_mode` = "warn-only" if policy=="warn" else policy.
    assert cfg["quality_market_cap_mode"] == "block"
    assert cfg["backfill_queue_grace_bars"] == 3
    assert cfg["backfill_queue_priority_grace_bars"] == 8
    assert cfg["backfill_queue_reserved_priority_slots"] == 10
    assert cfg["backfill_queue_max_size"] == 30
    assert cfg["rotation_min_hold_days"] == 10
    assert cfg["rotation_profitable_min_delta"] == 1.5
    assert cfg["rotation_profitable_full_exit_min_hold_days"] == 20
    assert cfg["rotation_profitable_min_incoming_raw_score"] == 2.0
    assert cfg["rotation_winner_lock_enabled"] is True
    assert cfg["rotation_winner_lock_min_hold_days"] == 5
    assert cfg["rotation_winner_lock_min_pnl_pct"] == 3.0
    assert cfg["rotation_winner_lock_min_raw_score"] == -0.10
    assert cfg["rotation_winner_lock_max_peak_drawdown_pct"] == 8.0
    assert cfg["rotation_break_glass_raw_score"] == 2.75
    assert cfg["rotation_break_glass_delta"] == 2.25
    assert cfg["rotation_break_glass_sell_fraction"] == 0.50
    assert cfg["propagation_floor_requires_min_paths"] is True
    assert cfg["sector_watchlist_reserved_slots"] == 0
    assert cfg["sector_watchlist_max_per_sector"] == 0
    assert cfg["watchlist_sector_protected_slots"] == 0
    assert cfg["watchlist_priority_requires_active_sector"] is False
    assert cfg["watchlist_priority_slots"] == 0
    assert cfg["propagation_expansion_reserved_slots"] == 4
    assert cfg["priority_min_position_size"] == 100.0
    assert cfg["priority_budget_can_bypass_regular_min"] is True
    assert cfg["allocation_profile"] == "balanced"
    assert cfg["allocation_max_new_stock_buys"] == 4
    assert cfg["allocation_execute_min_raw_score"] == 0.35
    assert cfg["allocation_top2_min_raw_score"] == 0.50
    assert cfg["winner_add_enabled"] is True
    assert cfg["winner_add_min_hold_days"] == 5
    assert cfg["winner_add_min_pnl_pct"] == 8.0
    assert cfg["winner_add_min_raw_score"] == 0.25
    assert cfg["winner_add_max_drawdown_from_peak_pct"] == 5.0
    assert cfg["winner_add_fraction_of_initial"] == 0.50
    assert cfg["winner_add_max_count"] == 1
    assert cfg["max_sector_peer_discoveries_per_day"] == 3
    assert cfg["max_competitor_discoveries_per_day"] == 3
    assert cfg["momentum_discovery_max_per_day"] == 3
    assert cfg["sector_fill_max_per_sector"] == 6
    assert cfg["max_propagated_scoring_slots"] == 15
    assert cfg["sector_watchlist_keys"] == []


def test_normalize_llm_rows_and_traces_drops_malformed_worker_payloads():
    assert _normalize_llm_rows_and_traces({"bad": "shape"}) == ([], [])
    assert _normalize_llm_rows_and_traces(({"doc": "bad"}, {"trace": "bad"})) == ([], [])
    rows, traces = _normalize_llm_rows_and_traces(([{"id": 1}, "bad"], [{"t": 1}, "bad"]))
    assert rows == [{"id": 1}]
    assert traces == [{"t": 1}]


def test_normalize_overlay_worker_result_drops_malformed_payloads():
    assert _normalize_overlay_worker_result({"delta_score": 1.0}) == ({}, {})
    assert _normalize_overlay_worker_result((["bad"], ["bad"])) == ({}, {})
    overlay, trace = _normalize_overlay_worker_result(({"delta_score": 0.25}, {"source": "llm"}))
    assert overlay == {"delta_score": 0.25}
    assert trace == {"source": "llm"}


def test_finalize_scores_tolerates_malformed_fast_loser_and_profit_state_cache():
    pe = MockPortfolioEmulator(initial_cash=0.0)
    pe.seed_position("ABC", 1.0, 100.0, buy_time=datetime(2026, 1, 1))
    out = _finalize_scores(
        ["ABC"],
        {},
        {"ABC": {"raw_score": 0.0, "reasons": ["x"], "n_paths": 1}},
        {
            "buy_threshold": 0.15,
            "sell_threshold": -0.15,
            "fast_loser_cut_pct": -5.0,
            "profit_take_enabled": True,  # V31: opt in for legacy unit test
            "profit_take_gain_pct": 40.0,
            "profit_take_sell_fraction": 0.5,
            "trailing_stop_pct": 8.0,
            "trailing_stop_activation_pct": 10.0,
            # V31: opt out of grace period to unit-test legacy fast-loser behavior
            "initial_grace_enabled": False,
        },
        pending_by_symbol={},
        portfolio_emulator=pe,
        date_key="2026-01-10",
        prices={"ABC": 94.0},
        strategy_cache={"_fast_loser_blacklist": [], "_nexus_profit_take_state": []},
        price_history={"ABC": _bars(94.0, 250000, 5)},
    )
    assert out["ABC"]["score"] == -1
    assert "Fast loser cut" in out["ABC"]["reason"]


def test_finalize_scores_fast_loser_sets_forced_exit_flag():
    """Fast loser cut MUST set _forced_exit=True so the downstream ML overlay propagation
    floor cannot override the forced sell back to a buy (Bug #1 round-5 regression)."""
    pe = MockPortfolioEmulator(initial_cash=0.0)
    pe.seed_position("LOSS", 1.0, 100.0, buy_time=datetime(2026, 1, 1))
    out = _finalize_scores(
        ["LOSS"],
        {},
        # Propagation raw score is very high (0.90) — would trigger propagation floor
        {"LOSS": {"raw_score": 0.90, "reasons": ["strong graph"], "n_paths": 5}},
        {
            "buy_threshold": 0.15,
            "sell_threshold": -0.15,
            "fast_loser_cut_pct": -5.0,
            "profit_take_gain_pct": 100.0,
            "trailing_stop_pct": 8.0,
            "trailing_stop_activation_pct": 10.0,
            # V31: opt out of grace period to unit-test the legacy _forced_exit flag behavior
            "initial_grace_enabled": False,
        },
        pending_by_symbol={},
        portfolio_emulator=pe,
        date_key="2026-01-10",
        prices={"LOSS": 94.0},  # -6% loss, triggers fast_loser_cut_pct=-5%
        strategy_cache={},
        price_history={"LOSS": _bars(94.0, 250000, 5)},
    )
    # Fast loser must fire and set _forced_exit so ML overlay can't override it
    assert out["LOSS"]["score"] == -1, "fast loser cut must produce sell"
    assert out["LOSS"].get("_forced_exit") is True, "_forced_exit must be True for fast loser"
    assert "Fast loser cut" in out["LOSS"]["reason"]


def test_v31_grace_period_does_not_mask_circuit_breaker():
    """V31 Codex review HIGH-1: grace period must NOT suppress circuit breaker
    sells when max_open_loss_pct is tighter than initial_grace_catastrophic_loss_pct.
    A user setting max_open_loss_pct=-8 must still see early -10% positions cut
    even though initial_grace_catastrophic_loss_pct=-15 wouldn't fire."""
    pe = MockPortfolioEmulator(initial_cash=0.0)
    pe.seed_position("CB", 1.0, 100.0, buy_time=datetime(2026, 1, 1))
    out = _finalize_scores(
        ["CB"],
        {},
        {"CB": {"raw_score": 0.0, "reasons": ["x"], "n_paths": 1}},
        {
            "buy_threshold": 0.15,
            "sell_threshold": -0.15,
            "fast_loser_cut_pct": -50.0,           # disable fast loser
            "max_open_loss_pct": -8.0,             # tighter than grace catastrophic
            "trailing_stop_pct": 50.0,             # disable trailing
            "trailing_stop_activation_pct": 50.0,  # ditto
            # V31 grace ENABLED with default catastrophic of -15
            "initial_grace_enabled": True,
            "initial_grace_bars": 14,
            "initial_grace_catastrophic_loss_pct": -15.0,
        },
        pending_by_symbol={},
        portfolio_emulator=pe,
        date_key="2026-01-05",  # day 4 of grace, well within window
        prices={"CB": 90.0},  # -10% loss — between fast loser and catastrophic, but past circuit breaker
        strategy_cache={},
        price_history={"CB": _bars(90.0, 250000, 5)},
    )
    # Circuit breaker fires at -10% < -8%; grace MUST NOT mask it
    assert out["CB"]["score"] == -1, "circuit breaker must fire even inside grace period"
    assert "Circuit breaker" in out["CB"]["reason"], "sell reason must remain Circuit breaker"


def test_v31_detect_market_regime_tolerates_malformed_overlay_bars():
    """V31 Codex review HIGH-2: _detect_market_regime must NOT crash on
    malformed _overlay_bars_raw entries. A bad cache entry should fall back
    to 'bull' instead of aborting the bar."""
    cache = {
        "_overlay_bars_raw": {
            "SPY": [
                None,                                    # not a dict
                {"t": "2026-01-01", "c": "not-a-number"},  # bad numeric
                {"t": "2026-01-02", "c": 100.0},        # valid
                "junk",                                  # not a dict
                {"t": "2026-01-03"},                     # missing close
                {"t": "2026-01-04", "c": 101.0},
            ]
        }
    }
    cfg = {"regime_detector_enabled": True, "regime_bear_spy_drawdown_pct": 5}
    # Should not crash; falls back to "bull" because closes < 21
    result = gna._detect_market_regime(cache, cfg, "2026-01-10")
    assert result == "bull"
    # Empty cache fallback
    assert gna._detect_market_regime({}, cfg, "2026-01-10") == "bull"
    # None cache fallback
    assert gna._detect_market_regime(None, cfg, "2026-01-10") == "bull"
    # Disabled flag fallback
    assert gna._detect_market_regime(cache, {"regime_detector_enabled": False}, "2026-01-10") == "bull"


def test_v31_recent_sell_block_skips_already_rebought():
    """V31 Codex review MED-3: _recent_sell_block_active must honor live_qty>0
    and report the symbol as NOT blocked once it has been rebought, mirroring
    the batched _build_recent_sell_block_set behavior."""
    pe = MockPortfolioEmulator(initial_cash=0.0)
    # Seed a buy + sell to populate the trade history
    pe.seed_position("RB", 1.0, 100.0, buy_time=datetime(2026, 1, 1))
    pe._trades.append({
        "ticker": "RB",
        "action": "sell",
        "price": 95.0,
        "shares": 1.0,
        "timestamp": datetime(2026, 1, 5, 13, 0, 0),
    })
    # Now simulate a re-buy: live qty back to 1.0
    pe._positions["RB"] = 1.0
    pe._trades.append({
        "ticker": "RB",
        "action": "buy",
        "price": 92.0,
        "shares": 1.0,
        "timestamp": datetime(2026, 1, 6, 13, 0, 0),
    })
    cfg = {
        "recent_sell_block_enabled": True,
        "recent_sell_block_bars": 30,
        "recent_sell_block_recovery_pct": 10.0,
    }
    blocked, reason = gna._recent_sell_block_active(
        "RB",
        current_price=93.0,
        current_date="2026-01-08",
        portfolio_emulator=pe,
        config=cfg,
    )
    assert blocked is False, "rebought symbol must not be reported as blocked"


def test_drawdown_halt_tolerates_malformed_cached_state():
    pe = MockPortfolioEmulator(initial_cash=0.0)
    pe.seed_position("ABC", 1.0, 1000.0, buy_time=datetime(2026, 1, 1))
    pe._initial_value = 1000.0
    scores = {"ABC": {"score": 1, "raw_net_score": 1.0, "reason": "x"}}
    out = _apply_portfolio_drawdown_halt(
        scores,
        ["ABC"],
        pe,
        {"portfolio_drawdown_halt_enabled": True, "portfolio_drawdown_halt_pct": 5.0},
        {"_portfolio_drawdown_state": []},
        {"ABC": 100.0},
        {"ABC": _bars(100.0, 250000, 5)},
        "2026-01-10",
    )
    assert out["ABC"]["score"] == 0
    assert "drawdown_halt" in out["ABC"]["reason"].lower()


def test_sell_enforcement_tolerates_malformed_tracker_cache():
    pe = MockPortfolioEmulator(initial_cash=0.0)
    pe.seed_position("ABC", 1.0, 100.0, buy_time=datetime(2026, 1, 1))
    result = _evaluate_trend_sell_enforcement(
        "ABC",
        portfolio_emulator=pe,
        prices={"ABC": 100.0},
        price_history={"ABC": _bars(100.0, 250000, 5)},
        propagated={"ABC": {"raw_score": -0.1}},
        config={
            "sell_enforcement_min_hold_days": 5,
            "sell_enforcement_consecutive_neutral_days": 3,
            # V31: opt out of grace period to unit-test the legacy malformed-cache tolerance
            "initial_grace_enabled": False,
        },
        strategy_cache={"_sell_enforcement_neutral_tracker": []},
        date_key="2026-01-10",
    )
    assert result["allow_force_sell"] is False
    assert result["neutral_days"] == 1


def test_build_candidate_meta_tolerates_malformed_watchlist_priority_cache():
    meta = _build_nexus_candidate_meta(
        "ABC",
        {"signal_source": "direct"},
        {"sector_watchlist": {}},
        strategy_cache={"_active_watchlist_priority_tickers": 1},
    )
    assert meta["ticker"] == "ABC"
    assert meta["is_watchlist_priority"] is False


def test_log_falls_back_when_logger_backend_raises(monkeypatch):
    class BrokenLogger:
        def log(self, *args, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(gna, "intellistock_logger", BrokenLogger(), raising=False)
    gna._log({"msg": "hello"}, "cyan")


def test_fetch_all_benzinga_skips_empty_scoped_fetch_for_broad_discovery_types(monkeypatch):
    calls = []

    fake_bz = types.ModuleType("benzinga_client")

    def fake_bulk(data_type, _key, _from, _to, tickers=None, max_results=2000):
        calls.append((data_type, None if tickers is None else tuple(tickers)))
        return []

    fake_bz.fetch_benzinga_bulk = fake_bulk
    fake_bz._strip_future_actuals = lambda data_type, rows, date_key: rows
    fake_bz._FORWARD_LOOKING_TYPES = {"earnings"}
    fake_bz.set_benzinga_backtest_mode = lambda enabled: None

    monkeypatch.setitem(sys.modules, "benzinga_client", fake_bz)
    monkeypatch.setenv("BENZINGA_API_KEY", "test-key")

    out = gna._fetch_all_benzinga(
        {
            "benzinga_discovery_enabled": True,
            "historical_lookback_mode": True,
        },
        "2026-01-10",
        [],
        strategy_cache={},
    )
    assert out == {}
    assert ("ratings", None) in calls
    assert ("earnings", None) in calls
    assert calls.count(("ratings", None)) == 1
    assert calls.count(("earnings", None)) == 1


def test_fetch_google_news_cached_background_logs_background_cache_context(monkeypatch):
    fake_gn = types.ModuleType("google_news")
    fake_gn.fetch_google_news = lambda **kwargs: []
    fake_gn.fetch_google_news_by_topic = lambda **kwargs: []
    fake_gn.load_cached_articles = lambda conn, date_key, kw_hash: [{"url": "u1", "title": "x"}]
    fake_gn.cache_articles = lambda conn, date_key, articles, kw_hash: None
    fake_gn.compute_keywords_hash = lambda keywords, topics: "kw"
    fake_gn.DEFAULT_KEYWORDS = ["alpha"]
    fake_gn.DEFAULT_TOPICS = ["market"]
    monkeypatch.setitem(sys.modules, "google_news", fake_gn)

    logs = []
    monkeypatch.setattr(gna, "_log", lambda msg, color="cyan": logs.append(str(msg)))
    monkeypatch.setattr(gna, "_filter_low_signal_alpaca_articles", lambda articles, **kwargs: list(articles))

    rows = gna._fetch_google_news_cached(
        "2026-01-10",
        datetime(2026, 1, 10),
        datetime(2026, 1, 10),
        {"google_news_enabled": True},
        conn=object(),
        log_context="background",
    )
    assert len(rows) == 1
    assert any("Google News background: 1 articles from cache for 2026-01-10" in line for line in logs)
    assert not any("Google News: 1 articles from cache for 2026-01-10" in line for line in logs)
