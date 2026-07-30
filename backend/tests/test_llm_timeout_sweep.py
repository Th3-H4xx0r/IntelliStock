"""Regression net for the LLM timeout/retry sweep.

Every item here was found by a parallel audit of the whole LLM surface after
two production hangs (a sentiment call blocked 10+ min, an event-maintenance
batch 12+ min) where no timeout fired and no telemetry row was written.

These are source-level assertions on purpose: the defects were *missing*
arguments and *absent* guards, which a behavioural test cannot see without a
live provider.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)


def _src(rel):
    return open(os.path.join(_backend, rel)).read()


# --------------------------------------------------------------- thread pools
def test_no_unbounded_result_in_the_nexus_llm_pools():
    """`future.result()` with no timeout is how one wedged call pinned a stage."""
    src = _src("strategies/graph_nexus_analysis.py")
    for banned in ("_fut.result())", "future.result(), context="):
        assert banned not in src, f"unbounded join reintroduced: {banned}"


def test_llm_pools_use_per_call_windows():
    src = _src("strategies/graph_nexus_analysis.py")
    assert src.count("per_call_timeout=_llm_call_window(") >= 3
    assert "collect_bounded" in src


def test_pools_do_not_reblock_on_shutdown():
    """`with ThreadPoolExecutor` exits via shutdown(wait=True), which would
    re-block on the straggler the bound just abandoned."""
    src = _src("strategies/graph_nexus_analysis.py")
    assert "shutdown_bounded(_pool)" in src


# ------------------------------------------------------------- per-call limits
def test_overlay_etf_leg_sets_a_timeout():
    """The stock leg always passed one; the ETF leg silently inherited 180s."""
    src = _src("strategies/graph_nexus_analysis.py")
    assert 'timeout_sec=int((config or {}).get("overlay_llm_timeout_sec", 60) or 60)' in src


def test_live_trading_overlay_is_bounded():
    """540s per symbol, serially, on the live decision path."""
    src = _src("strategies/ai_trading_decision.py")
    assert "AI_TRADING_DECISION_LLM_TIMEOUT_SEC" in src and "retries=1" in src


def test_ai_backtest_engine_sets_a_timeout_with_its_retries():
    src = _src("engines/ai_backtest_engine.py")
    assert "timeout_sec=90" in src and "http_retries=1" in src


def test_llm_test_endpoint_is_actually_single_shot():
    """retries=0 alone did not do it — http_retries defaults to 2."""
    src = _src("api/main.py")
    assert "http_retries=0" in src


def test_kalshi_analyst_timeout_is_defaulted_not_opt_in():
    src = _src("kalshi/intelligence/analyst_panel.py")
    assert 'KALSHI_ANALYST_LLM_TIMEOUT_SEC", "30"' in src


# ------------------------------------------------------------------- security
def test_llm_outputs_write_is_not_open_to_the_network():
    src = _src("api/main.py")
    assert "_require_loopback(request)" in src
    assert "_LLM_OUTPUT_MAX_CHARS" in src, "unbounded write is a disk-fill primitive"


def test_llm_outputs_read_requires_auth():
    src = _src("api/main.py")
    idx = src.index('@app.get("/llm/outputs/{output_id}"')
    assert "Depends(get_current_user)" in src[idx:idx + 400]


def test_gemini_key_is_not_in_a_url():
    """As ?key=... it was written into every proxy and access log en route."""
    src = _src("engines/daily_digest_engine.py")
    assert "generateContent?key=" not in src
    assert "x-goog-api-key" in src


# -------------------------------------------------------------- local models
def test_finbert_load_cannot_hang_the_broker_thread():
    """It runs inline in run_once, and `except Exception` cannot catch a hang."""
    src = _src("strategies/ml_news.py")
    assert "HF_HUB_DOWNLOAD_TIMEOUT" in src
    assert "local_files_only=True" in src
