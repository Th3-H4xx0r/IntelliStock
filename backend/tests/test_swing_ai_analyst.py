"""AI analyst parity with ST (prompts byte for byte, result post-processing),
ST's own prompt and sector tests, and the failure modes of fix 5."""
import importlib.util
import inspect
import json
import os
import sys
import types

import pandas as pd
import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import ai_analyst as aa  # noqa: E402

_ST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "swing_trader_st")
ROLE = {"provider": "claude-cli", "model": "claude-sonnet-4-6", "api_key": "",
        "provider_config": {"cli_path": "claude"}}


def _st(name):
    spec = importlib.util.spec_from_file_location(
        f"_swing_st_{name}", os.path.join(_ST_DIR, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Anthropic:
    """ST's client.messages.create, answering with fixed text."""

    def __init__(self, text):
        self.text = text
        self.prompts = []

    @property
    def messages(self):
        return self

    def create(self, **kw):
        self.prompts.append(kw["messages"][0]["content"])
        return types.SimpleNamespace(content=[types.SimpleNamespace(text=self.text)])


def _llm(payload, seen):
    def call(provider, api_key, model, prompt, output_type, **kw):
        seen.append({"provider": provider, "model": model, "prompt": prompt,
                     "output_type": output_type, **kw})
        return None if payload is None else output_type(**payload)
    return call


SIGNAL = {"symbol": "nflx", "rsi": 38.2, "rsi_prev": 35.7, "macd_hist": 0.142,
          "macd_hist_prev": 0.089, "entry_price": 950.0, "shares": 13,
          "stop_price": 893.0, "target_price": 1035.5}
PAYLOAD = {"conviction_score": 80, "recommendation": "review",
           "reasoning": "Clean pullback in an uptrend.",
           "position_size_adjustment": 0.5, "key_risks": ["Earnings in 4 days"]}


def _st_analyse(payload):
    st = _st("ai_analyst_pure")
    client = _Anthropic(json.dumps(payload))
    st._get_client = lambda: client
    st.days_until_earnings = lambda s: 4
    st.get_symbol_sector = lambda s: "communication_services"
    st.sector_etf_rsi = lambda s: 55.5
    st.fetch_news_summary = lambda s: "NEWS"
    return st, client, st.analyse(dict(SIGNAL))


def _our_analyse(payload, monkeypatch, **kw):
    seen = []
    monkeypatch.setattr(aa, "sector_etf_rsi", lambda s, **k: 55.5)
    result = aa.analyse(dict(SIGNAL), role=ROLE,
                        sector_of=lambda s: "communication_services",
                        earnings_fn=lambda s: 4, news_fn=lambda s, r: "NEWS",
                        llm=_llm(payload, seen), **kw)
    return seen, result


COMPARED = ("conviction_score", "recommendation", "reasoning",
            "position_size_adjustment", "key_risks", "symbol", "entry_price",
            "shares", "stop_price", "target_price", "risk_pct", "reward_pct",
            "rr_ratio", "earnings_days", "sector_etf", "sector_rsi", "news_summary")


def test_the_swing_prompt_and_result_match_st(monkeypatch):
    _, client, theirs = _st_analyse(PAYLOAD)
    seen, ours = _our_analyse(PAYLOAD, monkeypatch)
    assert seen[0]["prompt"] == client.prompts[0]
    assert seen[0]["output_type"] is aa.SwingConviction
    assert seen[0]["max_output_tokens"] == 600 and seen[0]["retries"] == 0
    assert seen[0]["timeout_sec"] == aa.SCORE_TIMEOUT_S
    for key in COMPARED:
        assert ours[key] == theirs[key], key
    assert ours["recommendation"] == "approve"     # thresholds re-applied in code


def test_a_review_with_valid_prices_matches_st(monkeypatch):
    payload = dict(PAYLOAD, conviction_score=62, position_size_adjustment=0.3)
    st, _, theirs = _st_analyse(payload)
    _, ours = _our_analyse(payload, monkeypatch)
    assert theirs["recommendation"] == ours["recommendation"] == "review"
    assert ours["position_size_adjustment"] == theirs["position_size_adjustment"] == 1.0
    assert st.pending and st.pending[0]["symbol"] == "NFLX"


def test_a_review_with_bad_prices_raises_like_st(monkeypatch):
    payload = dict(PAYLOAD, conviction_score=60)
    bad = dict(SIGNAL, stop_price=949.0)
    st = _st("ai_analyst_pure")
    st._get_client = lambda: _Anthropic(json.dumps(payload))
    st.days_until_earnings = lambda s: None
    st.sector_etf_rsi = lambda s: None
    st.fetch_news_summary = lambda s: "NEWS"
    with pytest.raises(ValueError, match="Price validation failed"):
        st.analyse(dict(bad))
    monkeypatch.setattr(aa, "sector_etf_rsi", lambda s, **k: None)
    with pytest.raises(ValueError, match="Price validation failed"):
        aa.analyse(dict(bad), role=ROLE, sector_of=lambda s: "unknown",
                   earnings_fn=lambda s: None, news_fn=lambda s, r: "NEWS",
                   llm=_llm(payload, []))


def test_malformed_json_raises(monkeypatch):
    monkeypatch.setattr(aa, "sector_etf_rsi", lambda s, **k: None)
    with pytest.raises(ValueError, match="no valid JSON"):
        aa.analyse(dict(SIGNAL), role=ROLE, sector_of=lambda s: "unknown",
                   earnings_fn=lambda s: None, news_fn=lambda s, r: "NEWS",
                   llm=_llm(None, []))


def test_out_of_range_score_raises(monkeypatch):
    monkeypatch.setattr(aa, "sector_etf_rsi", lambda s, **k: None)
    for score in (150, -5):
        with pytest.raises(ValueError, match="outside 0-100"):
            aa.analyse(dict(SIGNAL), role=ROLE, sector_of=lambda s: "unknown",
                       earnings_fn=lambda s: None, news_fn=lambda s, r: "NEWS",
                       llm=_llm(dict(PAYLOAD, conviction_score=score), []))


def test_no_role_raises():
    with pytest.raises(RuntimeError, match="no conviction model"):
        aa._score("prompt", aa.SwingConviction, None, None)


def test_config_thresholds_are_reapplied(monkeypatch):
    _, ours = _our_analyse(dict(PAYLOAD, conviction_score=78), monkeypatch,
                           approve_threshold=80, review_threshold=50)
    assert ours["recommendation"] == "review"


# -- the wheel scorer (wheel_trader.py:958-1072) ------------------------------

CAND = {"symbol": "APH", "stock_price": 131.2, "strike_price": 127.8, "otm_pct": 2.59,
        "expiry": "2026-06-12", "est_premium": 1.7, "est_premium_pct": 1.296,
        "rsi": 48.3, "sma50": 125.0, "atr": 6.8, "atr_pct": 5.18, "earnings_days": None}
WHEEL_PAYLOAD = {"conviction_score": 81, "recommendation": "reject",
                 "reasoning": "Good premium.", "position_size_contracts": 5,
                 "key_risks": ["Sector weak"]}


def test_the_wheel_prompt_and_result_match_st():
    st = _st("wheel_trader_pure")
    client = _Anthropic(json.dumps(WHEEL_PAYLOAD))
    st._get_client = lambda: client
    st._fetch_news = lambda s: "NEWS"
    theirs = st.score_candidate(dict(CAND))
    seen = []
    ours = aa.score_candidate(dict(CAND), role=ROLE, news_fn=lambda s, r: "NEWS",
                              llm=_llm(WHEEL_PAYLOAD, seen))
    assert seen[0]["prompt"] == client.prompts[0]
    assert seen[0]["output_type"] is aa.WheelConviction
    for key in ("conviction_score", "recommendation", "reasoning",
                "position_size_contracts", "key_risks", "strike_price", "expiry"):
        assert ours[key] == theirs[key], key
    assert ours["recommendation"] == "approve" and ours["position_size_contracts"] == 3


def test_wheel_scoring_failure_is_a_reject_not_a_crash():
    for payload in (None, dict(WHEEL_PAYLOAD, conviction_score=250)):
        out = aa.score_candidate(dict(CAND), role=ROLE, news_fn=lambda s, r: "NEWS",
                                 llm=_llm(payload, []))
        assert out["recommendation"] == "reject" and out["conviction_score"] == 0
        assert out["key_risks"] == ["AI scoring error"]
        assert out["reasoning"].startswith("AI scoring failed:")
    out = aa.score_candidate(dict(CAND), role=None, news_fn=lambda s, r: "NEWS")
    assert out["recommendation"] == "reject"


# -- ST tests/test_ai_analyst_prompt.py, on the prompt builder ---------------

SRC = inspect.getsource(aa.build_swing_prompt)


def test_rr_ratio_not_listed_as_risk_factor():
    assert "Risk/reward ratio < 1.5:1" not in SRC


def test_prompt_forbids_structural_constants_in_key_risks():
    assert "NEVER cite the R/R ratio" in SRC
    assert "do NOT treat its absence as a risk" in SRC


def test_prompt_requires_trade_specific_risks():
    assert "specific to THIS trade" in SRC


# -- ST tests/test_ai_analyst_sector.py, with the sector lookup injected -------

def test_sector_etf_resolution():
    assert aa.resolve_sector_etf("APD", lambda s: "basic_materials") == "XLB"
    assert aa.resolve_sector_etf("AVGO", lambda s: "technology") == "XLK"
    assert aa.resolve_sector_etf("PLD", lambda s: "real_estate") == "XLRE"
    assert aa.resolve_sector_etf("ZZZZ", lambda s: "unknown") is None

    def must_not_call(symbol):
        raise AssertionError("should not be called")

    assert aa.resolve_sector_etf("SPY", must_not_call) == "SPY"

    def boom(symbol):
        raise Exception("down")

    assert aa.resolve_sector_etf("APD", boom) is None


def _oscillating_df(n=40):
    closes = [100.0]
    for i in range(1, n):
        closes.append(closes[-1] + (1.5 if i % 2 else -1.0))
    return pd.DataFrame({"Close": closes})


def test_sector_etf_rsi(monkeypatch):
    calls = []
    monkeypatch.setattr(aa.market_data, "get_daily_bars",
                        lambda syms, days, client: calls.append((syms, days)) or {"XLB": _oscillating_df()})
    val = aa.sector_etf_rsi("CF", sector_of=lambda s: "basic_materials", client=object())
    assert isinstance(val, float) and calls == [(["XLB"], 90)]
    calls.clear()
    assert aa.sector_etf_rsi("ZZZZ", sector_of=lambda s: "unknown", client=object()) is None
    assert calls == []
    monkeypatch.setattr(aa.market_data, "get_daily_bars", lambda syms, days, client: {})
    assert aa.sector_etf_rsi("AVGO", sector_of=lambda s: "technology", client=object()) is None


# -- the models framework ----------------------------------------------------

def test_llm_role_from_config():
    cfg = {"conviction_llm_provider": "claude-cli", "conviction_llm_model": "claude-sonnet-4-6",
           "conviction_llm_api_key": "", "conviction_cli_path": "claude",
           "conviction_llm_reasoning_effort": "high", "conviction_llm_model_id": "7"}
    role = aa.llm_role_from_config(cfg)
    assert role["provider"] == "claude-cli" and role["model"] == "claude-sonnet-4-6"
    assert role["provider_config"]["cli_path"] == "claude"
    assert role["provider_config"]["reasoning_effort"] == "high"
    assert "llm_model_id" not in role["provider_config"]
    assert aa.llm_role_from_config({"conviction_llm_provider": "openai",
                                    "conviction_llm_model": "gpt-5"}) is None
    assert aa.llm_role_from_config({"conviction_llm_model_id": ""}) is None


def test_news_never_raises_and_uses_sts_prompt():
    seen = []

    def search(provider, api_key, model, prompt, **kw):
        seen.append((provider, prompt, kw["max_uses"], kw["max_output_tokens"]))
        return "  Upgraded twice.  "

    assert aa.fetch_news_summary("AAPL", ROLE, web_search=search) == "Upgraded twice."
    assert seen[0][1].startswith("Search for 'AAPL stock news this week'")
    assert seen[0][2:] == (2, 300)
    assert aa.fetch_news_summary("AAPL", ROLE, web_search=lambda *a, **k: "") == \
        "No news summary available."

    def boom(*a, **k):
        raise RuntimeError("timeout")

    assert aa.fetch_news_summary("AAPL", ROLE, web_search=boom) == "News fetch failed: timeout"
    assert aa.fetch_news_summary("AAPL", None).startswith("News unavailable")


# -- beyond the brief: failure modes found while porting ----------------------

def test_an_empty_key_risks_reply_is_not_a_skeleton(monkeypatch):
    """ST's prompt asks for an empty key_risks list when a trade has no
    specific risk. llm_utils rejects a reply whose only list is empty as a
    skeleton (the raw-JSON path gpt-5/gpt-oss/kimi models always take), which
    would skip exactly the cleanest setups. The list is a tuple on the wire
    model, so that check never sees it, and a plain list in the result."""
    import llm_utils

    for model, extra in ((aa.SwingConviction, {"position_size_adjustment": 1.0}),
                         (aa.WheelConviction, {"position_size_contracts": 2})):
        raw = json.dumps({"conviction_score": 82, "recommendation": "approve",
                          "reasoning": "Clean.", "key_risks": [], **extra})
        out = llm_utils._validate_structured_output_from_raw_text(model, raw)
        assert llm_utils._is_skeleton_structured_output(out) is False
    _, ours = _our_analyse(dict(PAYLOAD, key_risks=[]), monkeypatch)
    assert ours["key_risks"] == [] and isinstance(ours["key_risks"], list)
    _, ours = _our_analyse(PAYLOAD, monkeypatch)
    assert isinstance(ours["key_risks"], list)
    out = aa.score_candidate(dict(CAND), role=ROLE, news_fn=lambda s, r: "NEWS",
                             llm=_llm(dict(WHEEL_PAYLOAD, key_risks=[]), []))
    assert out["key_risks"] == [] and out["conviction_score"] == 81


def test_llm_role_maps_endpoint_keys_to_the_names_llm_utils_reads():
    """model_resolver injects azure_openai_endpoint / openai_base_url; the
    dispatcher reads azure_endpoint / api_version / base_url (the names
    graph_nexus_analysis maps them to)."""
    azure = aa.llm_role_from_config({
        "conviction_llm_provider": "azure", "conviction_llm_model": "gpt-5",
        "conviction_llm_api_key": "k", "conviction_azure_openai_api_key": "k",
        "conviction_azure_openai_endpoint": "https://x.openai.azure.com",
        "conviction_azure_openai_api_version": "2024-10-21"})
    assert azure["api_key"] == "k"
    assert azure["provider_config"]["azure_endpoint"] == "https://x.openai.azure.com"
    assert azure["provider_config"]["api_version"] == "2024-10-21"
    assert not any(k.endswith("api_key") for k in azure["provider_config"])
    openai = aa.llm_role_from_config({
        "conviction_llm_provider": "openai", "conviction_llm_model": "gpt-5",
        "conviction_llm_api_key": "k", "conviction_openai_base_url": "https://proxy/v1"})
    assert openai["provider_config"]["base_url"] == "https://proxy/v1"
    nvidia = aa.llm_role_from_config({
        "conviction_llm_provider": "nvidia", "conviction_llm_model": "m",
        "conviction_llm_api_key": "k", "conviction_nvidia_base_url": "https://nv/v1"})
    assert nvidia["provider_config"]["base_url"] == "https://nv/v1"


def test_sector_rsi_without_a_bars_client_is_unavailable(monkeypatch):
    # No client, no fetch: market_data would retry the doomed call with a sleep.
    calls = []
    monkeypatch.setattr(aa.market_data, "get_daily_bars",
                        lambda syms, days, client: calls.append(syms) or {})
    assert aa.sector_etf_rsi("AVGO", sector_of=lambda s: "technology", client=None) is None
    assert calls == []


def test_analyse_with_no_linked_model_fails_before_any_enrichment():
    def must_not_run(*a, **k):
        raise AssertionError("enrichment ran without a linked model")

    with pytest.raises(RuntimeError, match="no conviction model"):
        aa.analyse(dict(SIGNAL), role=None, sector_of=must_not_run,
                   earnings_fn=must_not_run, news_fn=must_not_run, llm=must_not_run)


def test_point_in_time_analyse_reads_no_live_input(monkeypatch):
    """ai_gate_in_backtest: no earnings, news, web search or live bars client;
    the sector RSI comes only from the caller's point-in-time reader, and the
    prompt says what is missing. The live prompt is untouched (ST parity
    above)."""
    def must_not_run(*a, **k):
        raise AssertionError("a live-only input was read in point-in-time mode")

    monkeypatch.setattr(aa, "sector_etf_rsi", must_not_run)
    monkeypatch.setattr(aa, "days_until_earnings", must_not_run)
    monkeypatch.setattr(aa, "fetch_news_summary", must_not_run)
    seen, asked = [], []
    result = aa.analyse(dict(SIGNAL), role=ROLE, sector_of=lambda s: "communication_services",
                        bars_client=object(), earnings_fn=must_not_run, news_fn=must_not_run,
                        llm=_llm(PAYLOAD, seen), point_in_time=True,
                        sector_rsi_fn=lambda etf: asked.append(etf) or 61.25)
    assert asked == ["XLC"]
    prompt = seen[0]["prompt"]
    assert "Sector RSI(14): 61.2" in prompt or "Sector RSI(14): 61.3" in prompt
    assert "Days to earnings: unknown" in prompt
    assert "Recent news:     unavailable (point-in-time backtest" in prompt
    assert "earnings block is not applied" in prompt
    assert result["earnings_days"] is None and result["sector_etf"] == "XLC"
    assert result["recommendation"] == "approve"
    # Without a reader the sector RSI is unavailable, never fetched.
    seen.clear()
    aa.analyse(dict(SIGNAL), role=ROLE, sector_of=lambda s: "technology",
               llm=_llm(PAYLOAD, seen), point_in_time=True)
    assert "Sector RSI(14): unavailable" in seen[0]["prompt"]
