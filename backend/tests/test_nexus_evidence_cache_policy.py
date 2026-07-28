from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend import llm_utils
from backend import _phase_alpha_helpers as phase_alpha
from backend.model_evidence import (
    ModelEvidenceCleanStartAudit,
    ModelEvidenceSession,
    activate_model_evidence_session,
    clear_model_evidence_session,
)
from backend.strategies import graph_nexus_analysis as gna
from backend.strategies import nexus_analyst_panel as panel


@pytest.fixture(autouse=True)
def _clear_evidence_session():
    clear_model_evidence_session()
    yield
    clear_model_evidence_session()


def _activate_mode(mode):
    kwargs = {"mode": mode, "arm_id": "arm"}
    if mode == "replay":
        kwargs["declared_occurrences"] = frozenset()
    session = ModelEvidenceSession(**kwargs)
    activate_model_evidence_session(session)
    return session


def _activate_preflight_mode(mode):
    kwargs = {
        "mode": mode,
        "arm_id": "arm",
        "backtest_id": "backtest",
        "build_id": "build",
    }
    if mode == "replay":
        kwargs["declared_occurrences"] = frozenset()
    session = ModelEvidenceSession(**kwargs)
    session.bind_clean_start_audit(
        ModelEvidenceCleanStartAudit(
            backtest_id="backtest",
            build_id="build",
            arm_id="arm",
            cleared_scope_identities={
                scope: "1" * 64
                for scope in phase_alpha._MODEL_EVIDENCE_CACHE_KINDS
            },
            before_state_hash="2" * 64,
            after_state_hash="3" * 64,
            verified_empty=True,
            remaining_entry_count=0,
            completed_at="2026-07-28T08:00:00+00:00",
        )
    )
    activate_model_evidence_session(session)
    return session


@pytest.mark.parametrize(
    "cache_kind",
    (
        "ordinary_prompt",
        "sentiment",
        "overlay_result",
        "active_event_maintenance",
        "analyst_panel",
        "learning",
        "macro_classification",
    ),
)
@pytest.mark.parametrize("mode", ("record", "record_extend", "replay"))
def test_evidence_modes_bypass_every_llm_derived_cache(cache_kind, mode):
    _activate_mode(mode)
    assert phase_alpha.evidence_cache_read_allowed(cache_kind) is False


def test_replay_rejects_mutable_cache_until_seal_verified():
    activate_model_evidence_session(
        ModelEvidenceSession(
            mode="replay",
            arm_id="arm",
            declared_occurrences=frozenset(),
        )
    )
    assert phase_alpha.evidence_cache_read_allowed("sentiment") is False


def test_off_mode_preserves_cache_reads():
    assert phase_alpha.evidence_cache_read_allowed("ordinary_prompt") is True
    assert phase_alpha.evidence_cache_read_allowed("sentiment") is True


def test_off_mode_ignores_inert_fixture_artifact_config():
    assert phase_alpha.evidence_cache_read_allowed(
        "sentiment", fixture_artifacts=["ordinary-json-list"]
    ) is True


def test_ordinary_prompt_reader_consults_policy_before_database(monkeypatch):
    activate_model_evidence_session(ModelEvidenceSession(mode="record", arm_id="arm"))
    touched = []
    monkeypatch.setattr(llm_utils, "_rethink", object())
    monkeypatch.setattr(llm_utils, "_prompt_cache_enabled", True)
    monkeypatch.setattr(llm_utils, "_prompt_cache_tbl_ok", True)
    monkeypatch.setattr(
        llm_utils,
        "_prompt_cache_new_conn",
        lambda: touched.append("database") or object(),
    )
    assert llm_utils._check_prompt_cache("prompt", "model", "") is None
    assert touched == []


@pytest.mark.parametrize("mode", ("record", "record_extend", "replay"))
def test_ordinary_prompt_writer_is_bypassed_in_every_evidence_mode(monkeypatch, mode):
    _activate_mode(mode)
    touched = []
    monkeypatch.setattr(llm_utils, "_rethink", object())
    monkeypatch.setattr(llm_utils, "_prompt_cache_enabled", True)
    monkeypatch.setattr(llm_utils, "_prompt_cache_tbl_ok", True)
    monkeypatch.setattr(
        llm_utils,
        "_prompt_cache_new_conn",
        lambda: touched.append("database") or object(),
    )
    llm_utils._store_prompt_cache("prompt", "model", "", "response long enough")
    assert touched == []


@pytest.mark.parametrize("mode", ("record", "record_extend", "replay"))
def test_legacy_prompt_cache_wrapper_fails_closed_before_provider(monkeypatch, mode):
    session_kwargs = {"mode": mode, "arm_id": "arm"}
    if mode == "replay":
        session_kwargs["declared_occurrences"] = frozenset()
    activate_model_evidence_session(ModelEvidenceSession(**session_kwargs))
    touched = []
    monkeypatch.setattr(llm_utils, "_rethink", object())
    monkeypatch.setattr(
        llm_utils,
        "_prompt_cache_get",
        lambda *args, **kwargs: touched.append("read") or {},
    )
    monkeypatch.setattr(
        llm_utils,
        "_prompt_cache_save",
        lambda *args, **kwargs: touched.append("write"),
    )
    monkeypatch.setattr(
        llm_utils,
        "call_llm_by_provider",
        lambda *args, **kwargs: touched.append("provider") or "provider response",
    )

    with pytest.raises(Exception, match="guarded.*ModelEvidenceContext"):
        llm_utils.call_llm_with_prompt_cache(
            "openai", "key", "model", "prompt", db_conn=object()
        )
    assert touched == []


def test_legacy_prompt_cache_wrapper_off_path_is_unchanged(monkeypatch):
    touched = []
    monkeypatch.setattr(llm_utils, "_rethink", None)
    monkeypatch.setattr(
        llm_utils,
        "call_llm_by_provider",
        lambda *args, **kwargs: touched.append("provider") or "provider response",
    )

    assert llm_utils.call_llm_with_prompt_cache(
        "openai", "key", "model", "prompt"
    ) == ("provider response", False)
    assert touched == ["provider"]


@pytest.mark.parametrize("mode", ("record", "record_extend", "replay"))
def test_graph_nexus_llm_cache_readers_consult_central_policy(monkeypatch, mode):
    _activate_mode(mode)
    touched = []
    monkeypatch.setattr(gna, "_ensure_learning_cache_table", lambda conn: touched.append("learning"))
    monkeypatch.setattr(gna, "_ensure_nexus_history_table", lambda *args: touched.append("history"))
    monkeypatch.setattr(gna, "_r", object())

    assert gna._get_learning_cache(object(), "instance", 12.0, config={}) is None
    assert (
        gna._load_active_event_maintenance_cache_doc(
            object(), "scope", "2026-01-05", config={}
        )
        is None
    )
    assert gna._get_cached_articles(object(), "2026-01-05", config={}) == (None, None)
    assert (
        gna._check_overlay_result_cache(
            object(), "AAPL", "2026-01-05", 0.2, [], {"overlay_result_cache_enabled": True}
        )
        is None
    )
    assert gna._load_llm_cache_rows(object(), "llm-cache", ["row"]) == {}
    assert touched == []


@pytest.mark.parametrize("mode", ("record", "record_extend", "replay"))
def test_google_macro_llm_cache_is_bypassed_before_database(monkeypatch, mode):
    _activate_mode(mode)
    touched = []
    monkeypatch.setattr(gna, "_nexus_db_available", True)
    monkeypatch.setattr(
        gna, "_ensure_google_macro_table", lambda conn: touched.append("database")
    )
    monkeypatch.setattr(
        gna,
        "_classify_macro_news_via_llm",
        lambda *args, **kwargs: [{"source": "provider"}],
    )

    result = gna._classify_macro_news_via_llm_cached(
        [{"id": "article-1", "headline": "Macro news"}],
        "openai",
        "key",
        "model",
        "2026-01-05",
        ["Technology"],
        ["Federal Reserve"],
        conn=object(),
    )

    assert result == [{"source": "provider"}]
    assert touched == []


@pytest.mark.parametrize("mode", ("record", "record_extend", "replay"))
def test_analyst_panel_cache_readers_are_bypassed_before_database(monkeypatch, mode):
    _activate_mode(mode)
    touched = []
    monkeypatch.setattr(panel, "_r", object())
    monkeypatch.setattr(
        panel, "_get_panel_db_conn", lambda: touched.append("database") or object()
    )
    agents = [{"role": "bull"}]

    assert panel._load_agent_memory(object(), "instance", "bull", "2026-01-05") == ""
    assert panel._compute_agent_weights(
        object(), "instance", "2026-01-05", agents
    ) == {"bull": 1.0}
    panel.fill_analyst_panel_outcomes(
        object(), "instance", "2026-01-05", {"AAPL": 100.0}
    )
    assert touched == []


def test_analyst_panel_propagates_context_and_evidence_errors(monkeypatch):
    from backend.model_evidence import ModelEvidenceError

    activate_model_evidence_session(ModelEvidenceSession(mode="record", arm_id="arm"))
    captured = []

    def fail_with_context(*args, **kwargs):
        captured.append(kwargs["evidence_context"])
        raise ModelEvidenceError("replay audit failure")

    monkeypatch.setattr(panel, "_scl_guarded", fail_with_context)
    with pytest.raises(ModelEvidenceError, match="replay audit failure"):
        panel._run_single_agent(
            "system",
            "prompt",
            panel._AnalystPanelResponse,
            {"_date_key": "2026-01-05"},
            round_num=2,
            agent_role="bull_analyst",
        )

    assert captured[0].decision_at == "2026-01-05"
    assert captured[0].call_site == "nexus_analyst_panel.round2"
    assert captured[0].subject == "bull_analyst"


@pytest.mark.parametrize("mode", ("record", "record_extend", "replay"))
def test_analyst_panel_strategy_cache_is_not_read_or_mutated(monkeypatch, mode):
    _activate_mode(mode)
    monkeypatch.setattr(panel, "_get_nexus_db_conn", lambda: None)
    monkeypatch.setattr(panel, "_run_round1_independent", lambda *args, **kwargs: {})
    strategy_cache = {
        "_analyst_panel_last_run_date": "2026-01-05",
        "_analyst_panel_last_consensus": "stale",
        "_analyst_panel_last_adjustments": {"AAPL": 1.0},
    }
    original = {
        key: value.copy() if isinstance(value, dict) else value
        for key, value in strategy_cache.items()
    }

    context, adjustments = panel.run_analyst_panel(
        {
            "analyst_panel_enabled": True,
            "analyst_panel_rounds": 1,
            "analyst_panel_agents": [],
        },
        "",
        [],
        strategy_cache,
        "instance",
        "2026-01-05",
    )

    assert context != "stale"
    assert adjustments == {}
    assert strategy_cache == original


@pytest.mark.parametrize("mode", ("record", "record_extend", "replay"))
def test_evidence_modes_bypass_all_llm_cache_database_writes(monkeypatch, mode):
    _activate_mode(mode)
    touched = []
    db = object()
    monkeypatch.setattr(
        gna, "_ensure_learning_cache_table", lambda conn: touched.append("learning")
    )
    monkeypatch.setattr(
        gna, "_ensure_nexus_history_table", lambda *args: touched.append("history")
    )
    monkeypatch.setattr(gna, "_r", object())
    monkeypatch.setattr(panel, "_r", object())
    monkeypatch.setattr(
        panel, "_get_panel_db_conn", lambda: touched.append("panel") or db
    )

    gna._save_learning_cache(db, "instance", "summary", config={})
    gna._store_llm_cache_rows(db, "classification", [{"id": "row"}])
    gna._store_active_event_maintenance_cache_doc(db, {"id": "event"})
    gna._save_cached_sentiment(
        db,
        "2026-01-05",
        {"AAPL": {"sentiment": 1}},
        [{"id": "article"}],
    )
    gna._store_overlay_result_cache(
        db,
        "AAPL",
        "2026-01-05",
        0.2,
        [],
        {"overlay_result_cache_enabled": True},
        {"delta_score": 0.1},
    )
    panel._save_round_results(
        db,
        "instance",
        "2026-01-05",
        "bull",
        round1={"stocks": []},
    )

    assert touched == []


@pytest.mark.parametrize("mode", ("record", "record_extend", "replay"))
def test_run_once_does_not_read_or_mutate_llm_strategy_caches(monkeypatch, mode):
    _activate_preflight_mode(mode)
    panel_stocks = []
    monkeypatch.setattr(gna, "_get_nexus_db_conn", lambda *args, **kwargs: None)
    monkeypatch.setattr(gna, "_nexus_db_available", False)
    monkeypatch.setattr(gna, "_r", None)
    monkeypatch.setattr(
        gna, "_NEXUS_BACKTEST_CLEANED_INSTANCES", {"evidence-test"}
    )
    monkeypatch.setattr(gna, "_fetch_alpaca_news_all", lambda *args, **kwargs: [])
    monkeypatch.setattr(gna, "_build_learning_context", lambda *args, **kwargs: "fresh")
    monkeypatch.setattr(
        gna,
        "_enhanced_sentiment_from_llm",
        lambda *args, **kwargs: ({}, [], [], {}),
    )
    monkeypatch.setattr(gna, "_create_nexus_graph_driver", lambda **kwargs: _FakeGraphDriver())
    monkeypatch.setattr(gna, "_load_neo4j_etf_mappings", lambda *args, **kwargs: None)
    monkeypatch.setattr(gna, "_load_neo4j_stock_sector_mappings", lambda *args, **kwargs: None)
    monkeypatch.setattr(gna, "_load_neo4j_market_cap_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(gna, "_load_analyst_panel", lambda: None)
    monkeypatch.setattr(gna, "_ANALYST_PANEL_AVAILABLE", True)
    monkeypatch.setattr(gna, "fill_analyst_panel_outcomes", None)

    def capture_panel(**kwargs):
        panel_stocks.extend(kwargs["stock_candidates"])
        return "", {}

    monkeypatch.setattr(gna, "run_analyst_panel", capture_panel)
    strategy_cache = {
        "_nexus_learning_context_scope": {"stale": True},
        "_nexus_learning_context_built": True,
        "_nexus_learning_context": "stale-learning",
        "_nexus_learning_last_attempt_date": "2026-01-04",
        "_last_sentiment_data": {"AAPL": {"sentiment": 1, "event": "stale"}},
    }
    protected = {
        key: value.copy() if isinstance(value, dict) else value
        for key, value in strategy_cache.items()
    }
    config = {
        "_nexus_is_live_mode": False,
        "runtime_instance_id": "evidence-test",
        "llm_api_key": "key",
        "llm_provider": "openai",
        "llm_model": "model",
        "learning_stage_enabled": True,
        "analyst_panel_enabled": True,
        "analyst_panel_skip_lookback": False,
        "private_entity_bridge_enabled": False,
        "google_news_enabled": False,
        "outcome_tracking_enabled": False,
        "trend_tracking_enabled": False,
        "sector_price_context_enabled": False,
        "etf_allocation_enabled": False,
        "nexus_discovery_bootstrap_enabled": False,
        "use_llm_sentiment": False,
        "use_sentiment_cache": False,
        "nexus_sentiment_cache_force_in_backtest": False,
        "nexus_fast_mode": False,
        "overlay_result_cache_enabled": False,
    }

    gna.GraphNexusAnalysis().run_once(
        ["AAPL"],
        {"AAPL": 100.0},
        datetime(2026, 1, 5, tzinfo=timezone.utc),
        config,
        {},
        strategy_cache=strategy_cache,
        time_increment=86400,
    )

    assert {key: strategy_cache[key] for key in protected} == protected
    assert panel_stocks == [{"ticker": "AAPL", "score": 0.0}]


class _FakeGraphSession:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FakeGraphDriver:
    def session(self):
        return _FakeGraphSession()

    def close(self):
        return None


@pytest.mark.parametrize(
    "lane",
    (
        "learning",
        "analyst_panel",
        "private_hierarchy",
        "background_macro",
        "google_macro",
        "event_maintenance",
    ),
)
def test_run_once_never_swallows_model_evidence_error(monkeypatch, lane):
    from backend.model_evidence import ModelEvidenceError

    injected = ModelEvidenceError(f"injected {lane}")
    needs_alpaca = lane == "private_hierarchy"
    needs_google = lane in {
        "background_macro",
        "google_macro",
        "event_maintenance",
    }
    alpaca_articles = (
        [{"id": "a1", "headline": "Private company event", "created_at": "2026-01-05"}]
        if needs_alpaca
        else []
    )
    google_articles = (
        [{"id": "g1", "headline": "Macro event", "created_at": "2026-01-05"}]
        if needs_google
        else []
    )

    monkeypatch.setattr(gna, "_get_nexus_db_conn", lambda *args, **kwargs: None)
    monkeypatch.setattr(gna, "_nexus_db_available", False)
    monkeypatch.setattr(gna, "_r", None)
    monkeypatch.setattr(
        gna, "_get_cached_articles", lambda *args, **kwargs: (alpaca_articles, None)
    )
    monkeypatch.setattr(
        gna, "_fetch_alpaca_news_all", lambda *args, **kwargs: list(alpaca_articles)
    )
    monkeypatch.setattr(gna, "_create_nexus_graph_driver", lambda **kwargs: _FakeGraphDriver())
    monkeypatch.setattr(gna, "_load_neo4j_etf_mappings", lambda *args, **kwargs: None)
    monkeypatch.setattr(gna, "_load_neo4j_stock_sector_mappings", lambda *args, **kwargs: None)
    monkeypatch.setattr(gna, "_load_neo4j_market_cap_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        gna,
        "_fetch_google_news_cached",
        lambda *args, **kwargs: list(google_articles),
    )
    monkeypatch.setattr(
        gna,
        "_store_nexus_news_raw",
        lambda conn, date_key, source, articles, **kwargs: [
            {
                **article,
                "article_hash": article.get("id", "hash"),
                "date_key": date_key,
                "source": source,
                "published_at": article.get("created_at", date_key),
                "summary": "",
                "content_excerpt": "",
            }
            for article in articles
        ],
    )
    monkeypatch.setattr(
        gna, "_classify_macro_article_records", lambda *args, **kwargs: ([], [])
    )
    monkeypatch.setattr(gna, "_get_available_sectors", lambda session: [])
    monkeypatch.setattr(gna, "_get_available_gov_agencies", lambda session: [])
    monkeypatch.setattr(
        gna, "_classify_macro_news_via_llm_cached", lambda *args, **kwargs: []
    )
    monkeypatch.setattr(
        gna, "_maintain_active_events", lambda *args, **kwargs: ([], [])
    )

    if lane == "learning":
        monkeypatch.setattr(
            gna, "_build_learning_context", lambda *args, **kwargs: (_ for _ in ()).throw(injected)
        )
    elif lane == "analyst_panel":
        monkeypatch.setattr(gna, "_load_analyst_panel", lambda: None)
        monkeypatch.setattr(gna, "_ANALYST_PANEL_AVAILABLE", True)
        monkeypatch.setattr(
            gna, "run_analyst_panel", lambda *args, **kwargs: (_ for _ in ()).throw(injected)
        )
        monkeypatch.setattr(gna, "fill_analyst_panel_outcomes", None)
    elif lane == "private_hierarchy":
        monkeypatch.setattr(
            gna,
            "_resolve_private_entity_news_matches",
            lambda *args, **kwargs: (_ for _ in ()).throw(injected),
        )
    elif lane == "background_macro":
        monkeypatch.setattr(
            gna,
            "_classify_macro_article_records",
            lambda *args, **kwargs: (_ for _ in ()).throw(injected),
        )
    elif lane == "google_macro":
        monkeypatch.setattr(
            gna,
            "_classify_macro_news_via_llm_cached",
            lambda *args, **kwargs: (_ for _ in ()).throw(injected),
        )
    else:
        monkeypatch.setattr(
            gna,
            "_maintain_active_events",
            lambda *args, **kwargs: (_ for _ in ()).throw(injected),
        )

    config = {
        "_nexus_is_live_mode": False,
        "runtime_instance_id": "evidence-test",
        "llm_api_key": "key",
        "llm_provider": "openai",
        "llm_model": "model",
        "learning_stage_enabled": lane == "learning",
        "analyst_panel_enabled": lane == "analyst_panel",
        "analyst_panel_skip_lookback": False,
        "private_entity_bridge_enabled": lane == "private_hierarchy",
        "google_news_enabled": needs_google,
        "outcome_tracking_enabled": False,
        "trend_tracking_enabled": False,
        "sector_price_context_enabled": False,
        "etf_allocation_enabled": False,
        "nexus_discovery_bootstrap_enabled": False,
        "use_llm_sentiment": False,
        "use_sentiment_cache": False,
        "nexus_sentiment_cache_force_in_backtest": False,
    }

    with pytest.raises(ModelEvidenceError, match=f"injected {lane}"):
        gna.GraphNexusAnalysis().run_once(
            ["AAPL"],
            {"AAPL": 100.0},
            datetime(2026, 1, 5, tzinfo=timezone.utc),
            config,
            {},
            strategy_cache={},
            time_increment=86400,
        )


def test_graph_nexus_preflight_fails_before_strategy_mutation():
    activate_model_evidence_session(ModelEvidenceSession(mode="record", arm_id="arm"))
    config = {
        "model_evidence_clean_start": False,
        "nexus_sentiment_cache_force_in_backtest": False,
        "use_sentiment_cache": False,
        "nexus_fast_mode": False,
        "overlay_result_cache_enabled": False,
    }
    strategy_cache = {}

    with pytest.raises(Exception, match="clean-start"):
        gna.GraphNexusAnalysis().run_once(
            [],
            {},
            None,
            config,
            {},
            strategy_cache=strategy_cache,
        )
    assert "_nexus_last_tick_mode" not in strategy_cache
    assert "_resolved_time_increment_sec" not in config
