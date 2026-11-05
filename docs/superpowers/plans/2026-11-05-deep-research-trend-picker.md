# Deep-Research Trend Picker (TR) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A backtestable weekly LLM trend-research stock picker: a point-in-time (PIT) data store, an offline research-ledger builder, an engine lane that replays the ledger, and a regime scorecard shown on web and iOS.

**Architecture:** New package `backend/trend_research/` holds the PIT store, ingesters, digest, LLM stages, ledger and pure book/scorecard logic. The engine lane `backend/strategies/strategy_trend_research.py` only reads ledger rows dated before each decision and turns them into target weights through `strategy_x.targets_to_orders`. The builder is a resumable CLI run on the server.

**Tech Stack:** Python 3 backend (pydantic, `from db import store`), Postgres via `backend/db`, Vue 3 web (`frontend/`), SwiftUI iOS (`ios/`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-05-deep-research-trend-picker-design.md` (read it first; section refs below are to it).

## Global Constraints

- Backtest only. Never modify doc 200, doc 201 or the `alpaca-main` instance. Never use live-account (`alpaca-main`) credentials; news ingest uses the paper instance `alpaca-paper-fwd` data credentials (resolve via `stock_credential_boundary.resolve_linked_alpaca_credentials`).
- DB access only through `from db import store`; every new table registered in `backend/db/schema.py` (`TABLES` tuple + `TableSpec`); no `CREATE INDEX` at call sites; `store.between` is `[lo, hi)`.
- Every record that can reach a prompt carries a tz-aware UTC `available_at`; reads gate on `available_at < decision_time` (strict).
- LLM calls: tools/web search OFF; provider + model from config (`claude-cli` and `anthropic` must work); prompts never contain portfolio state (C2).
- Universe rules (§5.3): stocks (any US-listed incl. ADRs) + commodity ETFs {GLD, SLV, USO, XLE, CPER, URA} + defensive {BIL, TLT, XLU, XLP} + inverse {SH, SQQQ} + leveraged {TQQQ, SOXL, plus any 2x/3x the builder adds to `LEVERAGED_ETFS`}. Other ETFs are rejected.
- Book rules: 8–12 holdings; single cap 0.25 NAV; leveraged+inverse total ≤ 0.20 NAV; BIL is the remainder; trailing stop at close ≤ 0.80 × highest close since purchase; swap margin 2.0 conviction points (0–10 scale); no profit-taking.
- Start cash $5,500; backtest granularity 900 s; decisions on the first tick of the week's first session using data available before 09:30 ET.
- Config defaults: `strategy_trend_research_enabled: false`; `prompt_version: "tr-v1"`; `max_llm_calls_per_build` (default 2000).
- No secrets in logs/outputs. Tests run from repo root: `python3 -m pytest backend/tests -q -p no:cacheprovider` (FakeStore without `PG_TEST_DSN`).
- Before editing any existing symbol run `gitnexus_impact` (upstream) and report risk; run `gitnexus_detect_changes()` before each commit (CLAUDE.md).

## Review Focus

1. A news article whose `available_at` equals the decision time or later must never reach a prompt (boundary `<`, not `<=`), including articles later updated (`updated_at`) — test in Task 1 and Task 5.
2. A ledger week missing or marked failed: the lane must hold its current book (no liquidation, no new buys) and still run the daily trailing stop — test in Task 9.
3. The LLM names a symbol with no bars at the decision date, a delisted/renamed ticker, a lowercase or exchange-suffixed ticker, or a forbidden theme ETF — it must be dropped before the book, with a logged reason — test in Task 8.
4. A claude-cli subscription-quota hit (`ClaudeCliRateLimitError` or "quota"/"session limit" text) mid-week must not write a partial week; the builder sleeps and resumes that week — test in Task 7.
5. Weekly latch at 900 s granularity: many ticks per session and a Monday holiday — the decision fires exactly once per week on the first tick of the week's first session — test in Task 9.

---

### Task 1: PIT tables and store helpers

**Files:**
- Modify: `backend/db/schema.py` (add to `TABLES` and `TableSpec` list beside the Swing tables)
- Create: `backend/trend_research/__init__.py`, `backend/trend_research/pit_store.py`
- Test: `backend/tests/trend_research/test_pit_store.py`

**Interfaces:**
- Produces tables: `TrendNewsPIT` (id = `"YYYY-MM-DDTHH:MM:SSZ|source|source_id"`, prefix_fields id), `TrendThemesPIT` (id = `"YYYY-MM-DD|theme"`), `TrendMacroPIT` (id = `"series|obs_date|vintage_date"`), `TrendFilingsPIT` (id = `"SYMBOL|acceptance_iso|accession"`), `TrendResearchLedger` (id = `"chain_id|YYYY-MM-DD|model|prompt_version"`, indexed_fields `chain_id`).
- `put_rows(table: str, rows: list[dict]) -> int` (upsert by id; each row must have `available_at` ISO UTC string; raises `ValueError` otherwise).
- `read_available(table: str, *, lo_id: str, hi_id: str, decision_time: datetime) -> list[dict]` — rows in `[lo_id, hi_id)` with `available_at < decision_time`, oldest first.

- [ ] **Step 1: Write failing tests** — `test_put_requires_available_at` (row without it → `ValueError`); `test_read_available_is_strict` (rows at T-1s, T, T+1s → only T-1s returned); `test_read_available_ignores_updated_at` (row with `available_at` < T but `updated_at` > T is returned with its original fields only; `put_rows` never overwrites an existing row's `available_at` with a later one).
- [ ] **Step 2: Run** `python3 -m pytest backend/tests/trend_research/test_pit_store.py -q -p no:cacheprovider` → FAIL (module missing).
- [ ] **Step 3: Implement** the tables and helpers (use `store.between` + Python filter on parsed `available_at`).
- [ ] **Step 4: Run** the tests → PASS; run `python3 -m pytest backend/tests -q -p no:cacheprovider -k schema` → PASS.
- [ ] **Step 5: Commit** `feat(tr): PIT tables and store helpers`.

### Task 2: Alpaca news backfill (Benzinga)

**Files:**
- Create: `backend/trend_research/ingest_alpaca_news.py`
- Test: `backend/tests/trend_research/test_ingest_alpaca_news.py`

**Interfaces:**
- Consumes: `put_rows("TrendNewsPIT", ...)`.
- Produces: `backfill_news(start: date, end: date, *, key: str, secret: str, http_get=None, sleep=time.sleep) -> int` and CLI `python3 -m trend_research.ingest_alpaca_news --start 2025-01-01 --end 2026-11-05` that resolves **paper** credentials from instance `alpaca-paper-fwd` and refuses any instance whose brokerage `alpaca_paper` is not true.
- Row fields: `source="alpaca"`, `source_id`, `published_at`=`created_at`, `available_at`=`created_at`, `headline`, `summary`, `content` (may be empty), `symbols` (list, uppercase), `url`, `author`.

- [ ] **Step 1: Failing tests** — `test_paginates_until_no_token` (fake http returns 3 pages via `next_page_token`; all 120 rows stored); `test_requests_ascending_and_limit_50` (params contain `sort=asc`, `limit=50`, `include_content=true`); `test_backs_off_on_429` (one 429 then 200 → sleep called, rows stored); `test_refuses_live_brokerage` (resolver returns paper=False → `RuntimeError`); `test_resume_from_latest_stored` (second run starts after the max stored `published_at` for that day range).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** day-by-day windows over `https://data.alpaca.markets/v1beta1/news`, ≤200 req/min.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(tr): Alpaca news PIT backfill`.

### Task 3: Global themes (GDELT), macro (ALFRED) and event calendar

**Files:**
- Create: `backend/trend_research/ingest_gdelt.py`, `backend/trend_research/ingest_macro.py`, `backend/trend_research/calendar_data.py`
- Test: `backend/tests/trend_research/test_ingest_global.py`

**Interfaces:**
- `backfill_themes(start: date, end: date, *, themes: list[str] = THEMES, http_get=None, sleep=time.sleep) -> int` — GDELT DOC 2.0 `mode=timelinevolraw` and `timelinetone` per theme, one `TrendThemesPIT` row per theme per day with `volume`, `tone`, `available_at` = end of that UTC day + 1h; spacing ≥ 15 s between requests; also stores up to 25 top article titles per theme-day (`mode=artlist`) with their `seendate` as `available_at`.
- `THEMES`: a module constant of ~25 GDELT queries covering §5.1 (AI/semiconductors, data centers/power, war/conflict, tariffs/sanctions, elections, oil/OPEC, inflation, central banks/rates, dollar, recession, GLP-1/obesity drugs, pandemic, consumer spending, housing, crypto, China, energy transition, metals/mining, defense).
- `backfill_macro(start: date, end: date, *, series: tuple = MACRO_SERIES, http_get=None) -> int` — ALFRED vintages (`realtime_start/realtime_end`); `available_at` = vintage date 08:30 ET (as UTC); series at least `CPIAUCSL, UNRATE, PAYEMS, FEDFUNDS, DGS10, DGS2, T10Y2Y, BAMLH0A0HYM2, DCOILWTICO, DTWEXBGS`.
- `event_calendar(start: date, end: date) -> list[dict]` — static FOMC decision dates (2020–2026) and CPI/jobs release dates; each `{date, kind, available_at}` (available 30 days before).

- [ ] **Step 1: Failing tests** — `test_gdelt_spacing` (fake sleep records ≥15 s gaps); `test_theme_row_available_next_day`; `test_macro_uses_first_release` (fake ALFRED returns two vintages for one obs; reading at a date between them returns the first); `test_calendar_contains_known_fomc` (2022-03-16 present).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(tr): GDELT themes, ALFRED macro and event calendar`.

### Task 4: EDGAR filings PIT excerpts

**Files:**
- Create: `backend/trend_research/filings.py`
- Test: `backend/tests/trend_research/test_filings.py`

**Interfaces:**
- Consumes: `edgar_fundamentals.resolve_cik`, `edgar_fundamentals.user_agent`, `edgar_factors.factor_set(symbol, as_of)`.
- Produces: `fetch_filings(symbol: str, start: date, end: date, *, http_get=None) -> int` — 10-K/10-Q/8-K from `data.sec.gov/submissions/CIK##########.json`; stores `TrendFilingsPIT` rows with `form`, `accession`, `available_at` = `acceptanceDateTime` (UTC), and `excerpt` (≤ 6,000 chars: MD&A + risk-factor changes for 10-K/10-Q, item text for 8-K) extracted from the primary document.
- `filing_dossier(symbol: str, decision_time: datetime, *, max_filings: int = 3) -> dict` — `{"factors": FactorSet-as-dict, "filings": [...]}` using only rows with `available_at < decision_time`.

- [ ] **Step 1: Failing tests** — `test_acceptance_time_gates` (filing accepted 2023-05-24T20:05Z hidden at 2023-05-24T20:00Z, visible at 2023-05-25T13:00Z); `test_excerpt_capped_6000`; `test_non_us_issuer_without_cik_returns_empty` (no exception).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (throttle ≤10 req/s via `edgar_fundamentals._throttle`). **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(tr): PIT EDGAR filing excerpts`.

### Task 5: Market tape and weekly digest

**Files:**
- Create: `backend/trend_research/market_tape.py`, `backend/trend_research/digest.py`
- Test: `backend/tests/trend_research/test_digest.py`

**Interfaces:**
- `market_tape(decision_time: datetime, *, bars_loader=None) -> dict` — 1w/1m/3m returns and distance from SMA50/SMA200 for SPY, QQQ, IWM, the 11 sector SPDRs, GLD, SLV, USO, CPER, TLT, UUP, plus VIX level (from `SwingMacroDaily` "VIX|"), using only sessions strictly before the decision session (reuse `swing_trader.backtest_bars` cache / `strategy_x.pit_daily_observations`).
- `symbol_trend_stats(symbol: str, decision_time: datetime, *, bars_loader=None) -> dict | None` — same stats plus 52-week high distance and max drawdown 3m; `None` if < 60 sessions of bars.
- `weekly_digest(decision_time: datetime, *, max_headlines: int = 250) -> dict` — `{"headlines": [{"id","t","headline","symbols"}], "themes": [...], "macro": [...], "calendar": [...], "tape": {...}}` from the previous 7 days. Headline selection is deterministic: cluster by normalized headline (lowercase, strip punctuation, first 12 tokens), score = cluster size × (1 + number of distinct symbols), break ties by earliest `available_at` then `id`; take top `max_headlines` clusters' earliest article.
- `digest_hash(digest: dict) -> str` (sha256 of canonical JSON).

- [ ] **Step 1: Failing tests** — `test_digest_deterministic` (same store → same `digest_hash` across two calls and shuffled insert order); `test_digest_excludes_future` (article at decision_time excluded); `test_tape_uses_prior_sessions_only` (bar for the decision session ignored); `test_symbol_stats_none_when_short_history`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(tr): market tape and deterministic weekly digest`.

### Task 6: Stage schemas, prompts and LLM wrapper

**Files:**
- Create: `backend/trend_research/schemas.py`, `backend/trend_research/prompts.py`, `backend/trend_research/llm.py`
- Test: `backend/tests/trend_research/test_llm_stage.py`

**Interfaces:**
- Pydantic models in `schemas.py`:
  - `Trend{name, category: Literal["tech","geopolitics","economy_rates","consumer_health"], state: Literal["emerging","strengthening","mature","fading","broken"], confidence: float(0..1), evidence_ids: list[str], beneficiaries: list[str], losers: list[str], summary: str}`
  - `TrendScan{trends: list[Trend], regime: Literal["risk_on","neutral","risk_off","crisis"], regime_reasons: str}`
  - `CandidateList{candidates: list[{symbol, trend_names: list[str], why: str}]}`
  - `ResearchNote{symbol, conviction: float(0..10), thesis: str, breaks_if: list[str], priced_in: Literal["little","partly","mostly","fully"], thesis_status: Literal["new","intact","weakened","broken"], risks: str}`
  - `BookProposal{picks: list[{symbol, conviction, target_weight: float(0..0.25), role: Literal["core","defensive","leveraged","inverse"]}], defensive_share: float(0..1), notes: str}`
- `prompts.PROMPT_VERSION = "tr-v1"`; functions `trend_scan_prompt(digest, prior_trends, as_of)`, `candidate_prompt(scan, prior_recommended, as_of)`, `research_prompt(symbol, dossier, as_of)`, `book_prompt(scan, notes, as_of)` → `str`. Each starts with: `"Today is {as_of:%A %Y-%m-%d} before the US market open. Use ONLY the material below; do not use knowledge of events after this date."`
- `llm.StageCaller(provider: str, model: str, api_key: str | None, *, max_calls: int, provider_config: dict | None = None)` with `.call(prompt: str, output_type, *, max_output_tokens: int) -> BaseModel` raising `QuotaExhausted(reset_at: datetime | None)` on claude-cli quota/session-limit or HTTP 429-with-retry-after, `BudgetExceeded` when `max_calls` reached, `StageFailed` on validation failure after retries; uses `llm_utils.call_structured_llm_by_provider(..., tools=None, use_prompt_cache=True)`.

- [ ] **Step 1: Failing tests** — `test_prompt_has_date_header`; `test_prompt_has_no_portfolio_words` (no "NAV", "cash", "position", "holding" in any prompt built from fixtures); `test_quota_maps_to_QuotaExhausted` (fake provider raises `ClaudeCliRateLimitError("… resets 11:30am")` → `QuotaExhausted` with parsed `reset_at`); `test_budget_exceeded_after_max_calls`; `test_none_result_raises_StageFailed`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(tr): stage schemas, prompts and quota-aware LLM wrapper`.

### Task 7: Research ledger and resumable builder

**Files:**
- Create: `backend/trend_research/ledger.py`, `backend/trend_research/build_ledger.py`
- Test: `backend/tests/trend_research/test_build_ledger.py`

**Interfaces:**
- Consumes: Tasks 1, 4, 5, 6, and `book.is_allowed_symbol` / `book.normalize_symbol` from Task 8 (implement Task 8 first if executing out of order).
- `ledger.week_id(chain_id, week_start: date, model, prompt_version) -> str`; `ledger.latest_row(chain_id, model, prompt_version, decision_time) -> dict | None` (newest row with `as_of < decision_time` and `status == "ok"`); `ledger.put_week(row: dict)`.
- Row: `{id, chain_id, week_start, as_of (ISO UTC, = week's first session 09:25 ET), model, provider, prompt_version, status: "ok"|"failed", digest_hash, scan: TrendScan, candidates: [...], notes: [ResearchNote], book: BookProposal, recommended: [symbols with thesis], calls_used, available_at (= as_of)}`.
- `build(chain_id: str, start: date, end: date, *, provider, model, api_key, max_calls, sleep=time.sleep, now=utcnow) -> dict` — iterates weeks forward; skips weeks already `ok`; for each: digest → scan (1 call, with prior week's trends from the same chain; cold start = none) → candidates (1 call) → filter (bars available via `symbol_trend_stats`, allowed universe) to ≤ 30 incl. prior recommended → one research call per candidate (dossier = `filing_dossier` + `symbol_trend_stats` + its PIT news last 8 weeks, ≤ 40 items) → book (1 call) → write row. On `QuotaExhausted`: discard the in-progress week, sleep until `reset_at` (or 30 min), retry the same week. On `StageFailed` after 2 attempts: write `status="failed"` row with the error text. On `BudgetExceeded`: stop cleanly, return summary.
- CLI: `python3 -m trend_research.build_ledger --chain honest-<model> --start 2025-08-04 --end 2026-11-02 --model-id <Models row id>` (resolves provider/model/key from the `Models` row; prints only counts).

- [ ] **Step 1: Failing tests** (fake `StageCaller`) — `test_writes_one_row_per_week`; `test_resume_skips_ok_weeks` (second run makes 0 calls); `test_quota_mid_week_no_partial_row` (quota on 3rd research call → no row for that week, sleep called with reset delta, week then completed); `test_cold_start_chain_has_no_prior_trends` (first week prompt contains no trends from another chain_id); `test_failed_stage_writes_failed_row`; `test_budget_stop`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(tr): research ledger and resumable builder`.

### Task 8: Book rules and trailing stop (pure)

**Files:**
- Create: `backend/trend_research/book.py`
- Test: `backend/tests/trend_research/test_book.py`

**Interfaces:**
- Constants: `COMMODITY_ETFS`, `DEFENSIVE_ETFS`, `INVERSE_ETFS`, `LEVERAGED_ETFS`, `ALL_ALLOWED_ETFS` per Global Constraints.
- `is_allowed_symbol(sym: str, *, is_etf: bool) -> bool` and `normalize_symbol(raw: str) -> str | None` (uppercase; strips exchange suffix like ".US"; class shares as "BRK.B").
- `target_book(row: dict, *, held: dict[str, float], has_bars: Callable[[str], bool], cfg: dict) -> tuple[dict[str, float], list[str]]` — returns weights (sum = 1.0 incl. "BIL") and reason log lines. Rules: drop disallowed/no-bar symbols; keep a holding unless its latest note says `thesis_status == "broken"` or it is absent from `row["notes"]` and `row["recommended"]`; fill to `max_positions` (default 12, min 8) by conviction; replace weakest holding only if a new pick's conviction ≥ weakest + `swap_margin` (2.0); weights = proposal target weights clipped to `max_single_weight` (0.25); scale leveraged+inverse total to ≤ `max_levered_inverse_weight` (0.20); apply `defensive_share` by routing that share to the proposal's defensive picks (or BIL if none); remainder BIL.
- `trailing_stop_hits(peaks: dict[str, float], closes: dict[str, float], *, stop_pct: float = 0.20) -> list[str]`; `update_peaks(peaks, closes, held) -> dict`.
- ETF detection for `is_etf`: membership in `ALL_ALLOWED_ETFS` or a static known-ETF list (`KNOWN_THEME_ETFS`, incl. ITA, SMH, XBI, ARKK, SOXX, XLK, etc.) used to reject other ETFs.

- [ ] **Step 1: Failing tests** — `test_caps_single_025`; `test_levered_plus_inverse_capped_020` (TQQQ 0.15 + SQQQ 0.15 → total 0.20 scaled pro-rata); `test_swap_requires_margin` (new 8.9 vs weakest 7.0 → no swap; 9.0 → swap); `test_broken_thesis_exits`; `test_disallowed_etf_dropped_with_reason` (ITA, SMH rejected; GLD allowed); `test_normalize_symbol` ("nvda"→"NVDA", "TSM.US"→"TSM", "brk-b"→"BRK.B"); `test_weights_sum_to_one_with_bil`; `test_trailing_stop_at_80pct` (peak 100, close 80.0 → hit; 80.01 → no).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(tr): pure book rules and trailing stop`.

### Task 9: Engine lane and broker wiring

**Files:**
- Create: `backend/strategies/strategy_trend_research.py` (first lines `# INTELLISTOCK_SCHEMA: {...}` with `strategy: "strategy_trend_research"`, `execution_scope: "run_once"`, `decision_phase: "pre"`, config defaults from Global Constraints, `honour_single_position_cap: true`, `broker_max_single_position_pct: 0.25`) and `# INTELLISTOCK_DESCRIPTION:` (plain-English summary).
- Modify: `backend/broker.py` — add `_trend_research_universe_symbols(cached_strategies)` beside the other `_<lane>_universe_symbols` helpers and include it at the backtest bar-fetch loop (recon: `broker.py:4348-4800`, `12791-12838`) returning the fixed ETF set plus every symbol in the selected chain's ledger rows for the run window.
- Test: `backend/tests/trend_research/test_lane.py`

**Interfaces:**
- Class `StrategyTrendResearch.run_once(self, symbols, prices, current_time, config, conditions, data=None, portfolio_emulator=None, strategy_cache=None, time_increment=None, mode=None) -> dict` (match `strategy_swing` signature).
- Cache keys `_tr_last_week`, `_tr_owned` (set), `_tr_peaks`, `_tr_stopped` (week-scoped set).
- Config keys: `strategy_trend_research_enabled`, `tr_chain_id`, `tr_model`, `tr_prompt_version`, `research_model_cutoff` (YYYY-MM-DD), `max_positions`, `swap_margin`, `max_single_weight`, `max_levered_inverse_weight`, `stop_pct`.
- Output via `strategy_x.targets_to_orders(targets, nav=..., positions=..., prices=..., cash=..., config=..., owned=cache["_tr_owned"])`, then EB-style `_emit` (`_cash_reserve_floor_pct=0.0`, sells stamped `action_intent="etf_sell"`).

- [ ] **Step 1: Failing tests** — `test_weekly_latch_once_at_900s` (Monday→Friday 15-minute ticks → one weekly decision; Monday holiday → fires Tuesday first tick); `test_missing_ledger_week_holds_book` (no row → no decisions except stops); `test_stop_runs_daily_and_blocks_rebuy_same_week`; `test_owned_symbols_are_sellable` (name dropped from book is sold); `test_replay_deterministic` (same ledger + bars twice → identical decisions dict); `test_disabled_flag_inert`; `test_uses_only_prior_bars` (bars at/after decision session ignored).
- [ ] **Step 2: Run** → FAIL. **Step 3: Run `gitnexus_impact` on the broker functions touched; implement.** **Step 4: Run** lane tests + `python3 -m pytest backend/tests -q -p no:cacheprovider` → PASS.
- [ ] **Step 5: Commit** `feat(tr): engine lane replaying the research ledger`.

### Task 10: Scorecard

**Files:**
- Create: `backend/trend_research/scorecard.py`, `backend/trend_research/regime_windows.py`
- Modify: `backend/broker.py` near the benchmark fields (recon: `~16421`) to add `tr_scorecard` to the result when the doc has an enabled `strategy_trend_research` lane.
- Test: `backend/tests/trend_research/test_scorecard.py`

**Interfaces:**
- `regime_windows.REGIME_WINDOWS` — a pure copy of the battery in `scripts/outlier_engine_test.py` (bull/chop/bear tags and dates); `classify_after(spy_closes, start: date) -> list[window]` — the pre-declared rule for dates after the battery: rolling 63-session SPY return > +8% bull, < −8% bear, else chop, merged into ≥ 21-session windows.
- `scorecard(nav: list[tuple[datetime, float]], spy_tr: list[tuple[date,float]], qqq: list[...], *, cutoff: date, candidate_weeks: list[dict], n_random: int = 1000, seed: int = 7) -> dict` → `{"overall": {...}, "optimistic": {...}, "honest": {...}}`, each with `cagr, spy_cagr, excess_pp, max_dd, spy_max_dd, roll12_beat_pct, windows: [{tag, kind, start, end, ret, spy_ret, beat}], random_pctile_candidates, random_pctile_universe, pass_all_regimes: True|False|"untested", pass_plus5: bool}`; a regime kind with no window in a segment → `"untested"`; `pass_all_regimes` is True only if every window beats SPY and all three kinds are present.

- [ ] **Step 1: Failing tests** — `test_split_at_cutoff`; `test_untested_when_no_bear_window`; `test_pass_requires_every_window`; `test_plus5_is_percentage_points` (strategy 20.0% vs SPY 15.2% → False; 20.3% → True); `test_random_baseline_deterministic_with_seed`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(tr): regime scorecard with honest/optimistic split`.

### Task 11: Show the scorecard on web and iOS

**Files:**
- Modify: `frontend/src/views/BacktestDetailView.vue` (a "vs S&P by market type" section under the summary grid, ~`:960`, rendered only when `tr_scorecard` exists: overall/honest/optimistic rows with CAGR vs SPY, excess, max DD, pass flags; window table).
- Modify: `ios/IntelliStock/Features/Backtests/Data/BacktestModels.swift` (optional `trScorecard` decodable), `ios/IntelliStock/Features/Backtests/Views/BacktestDetailSections.swift` (new Section with the same content).
- Test: web unit test beside existing view tests if present; iOS decoding test under `ios/IntelliStockTests/Features/Backtests/` with a fixture JSON containing and omitting `tr_scorecard`.

- [ ] **Step 1: Failing tests** — decode with/without field; view renders "Honest period" row and "untested" label.
- [ ] **Step 2: Run** (frontend test runner used by the repo; iOS `xcodebuild test` as the repo's scripts do) → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(tr): show TR scorecard on web and iOS backtest pages`.

### Task 12: Lab setup, backtest runner and journal scripts

**Files:**
- Create: `scripts/tr_lab_setup.py` (clone of `scripts/hx_lab_setup.py`: refuses docs 200/201; creates or updates doc "TR lab" with one enabled `strategy_trend_research` lane; creates backtest-only instance `strategy-tr-lab`, granularity "900", brokerage cloned from `strategy-eb`; idempotent), `scripts/tr_backtest.py` (POSTs one backtest `{instance_id: "strategy-tr-lab", start_date, end_date, granularity: "900", initial_cash: 5500}`, polls until finished, refuses if any backtest of this instance is running, prints the `tr_scorecard`), `scripts/tr_journal.py` (exports a chain's ledger to Markdown: per week trends, regime, picks with thesis/breaks-if, sells).
- Test: `backend/tests/trend_research/test_scripts.py` (refusal of docs 200/201; journal renders a fixture row).

- [ ] **Step 1: Failing tests.** **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(tr): lab setup, backtest runner and research journal scripts`.

---

## Execution notes (for the lead, not a task)

1. Order: 1 → 8 → 2, 3, 4 (independent) → 5 → 6 → 7 → 9 → 10 → 11 → 12.
2. After all tasks: full suite green, parallel bug sweep (separate reviewers: PIT/lookahead, broker wiring/live safety, LLM/quota/builder, book rules/scorecard math), fix findings, `gitnexus_detect_changes()`, open PR, merge **only outside market hours** (lane ships disabled; live doc 200 untouched).
3. On the server: verify deploy (`scripts/check_deployed_code.py`), run news/theme/macro/filings backfill for the honest window plus warm-up, build the honest chain with the operator-configurable model (default: the strongest available model whose published cutoff ≤ 2025-07; record `research_model_cutoff`), run `tr_lab_setup.py`, then backtests strictly one at a time, and deep-analyse each finished run.
