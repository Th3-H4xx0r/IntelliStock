from __future__ import annotations

import pytest

from backend import llm_utils
from backend import _phase_alpha_helpers as phase_alpha
from backend.model_evidence import (
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
@pytest.mark.parametrize("mode", ("record", "record_extend"))
def test_record_modes_bypass_every_llm_derived_cache(cache_kind, mode):
    activate_model_evidence_session(ModelEvidenceSession(mode=mode, arm_id="arm"))
    assert phase_alpha.evidence_cache_read_allowed(cache_kind) is False


def test_replay_rejects_caller_claimed_fixture_artifacts_until_seal_verified():
    activate_model_evidence_session(
        ModelEvidenceSession(
            mode="replay",
            arm_id="arm",
            declared_occurrences=frozenset(),
        )
    )
    assert phase_alpha.evidence_cache_read_allowed("sentiment") is False
    assert (
        phase_alpha.evidence_cache_read_allowed(
            "sentiment", fixture_artifacts=frozenset({"sentiment"})
        )
        is False
    )


def test_off_mode_preserves_cache_reads():
    assert phase_alpha.evidence_cache_read_allowed("ordinary_prompt") is True
    assert phase_alpha.evidence_cache_read_allowed("sentiment") is True


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


def test_ordinary_prompt_writer_is_bypassed_during_recording(monkeypatch):
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
    llm_utils._store_prompt_cache("prompt", "model", "", "response long enough")
    assert touched == []


def test_legacy_prompt_cache_wrapper_bypasses_read_and_write(monkeypatch):
    activate_model_evidence_session(ModelEvidenceSession(mode="record", arm_id="arm"))
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
        lambda *args, **kwargs: "provider response",
    )

    assert llm_utils.call_llm_with_prompt_cache(
        "openai", "key", "model", "prompt", db_conn=object()
    ) == ("provider response", False)
    assert touched == []


def test_graph_nexus_llm_cache_readers_consult_central_policy(monkeypatch):
    activate_model_evidence_session(ModelEvidenceSession(mode="record", arm_id="arm"))
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


def test_google_macro_llm_cache_is_bypassed_before_database(monkeypatch):
    activate_model_evidence_session(ModelEvidenceSession(mode="record", arm_id="arm"))
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


def test_analyst_panel_cache_readers_are_bypassed_before_database(monkeypatch):
    activate_model_evidence_session(ModelEvidenceSession(mode="record", arm_id="arm"))
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


def test_analyst_panel_same_day_strategy_cache_is_bypassed(monkeypatch):
    activate_model_evidence_session(ModelEvidenceSession(mode="record", arm_id="arm"))
    monkeypatch.setattr(panel, "_get_nexus_db_conn", lambda: None)
    monkeypatch.setattr(panel, "_run_round1_independent", lambda *args, **kwargs: {})
    strategy_cache = {
        "_analyst_panel_last_run_date": "2026-01-05",
        "_analyst_panel_last_consensus": "stale",
        "_analyst_panel_last_adjustments": {"AAPL": 1.0},
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
