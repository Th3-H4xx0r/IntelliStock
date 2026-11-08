# Recon: engine, LLM and data plumbing for a "deep research trend picker" lane

Agent: recon · 2026-11-05 · read-only. Repo at `3d2e9249` on branch `fix/eb-cold-start-targets`. I edited no code, posted no backtests, and changed no docs or instances. I ran read-only Postgres queries (`default_transaction_read_only=on`) against server7.
Builds on `orch/research/07_intellistock_architecture.md` (cited as **[07]**) and does not repeat it.

## 0. Bottom line

1. **The lane slots into existing machinery.** It is one `execution_scope: run_once` file, `backend/strategies/<snake>.py` with class `PascalCase`, that returns `{sym: 1|-1}` plus `_nexus_position_sizes`, built by `strategy_x.targets_to_orders`.
   - **The closest template is `strategy_swing`, not EB.** Swing already does the four things this lane needs:
     - an S&P-500-sized point-in-time universe
     - its own daily bars, cached in `SwingDailyBars`
     - an LLM gate that runs *inside* backtests under a call budget
     - next-open fills
2. **Running the LLM inside the engine is the wrong shape.** It works mechanically, but measured claude-cli throughput and subscription quota make it painfully slow, and every A/B arm would pay again. Build a **point-in-time research ledger offline** (a resumable job that writes one row per week, keyed by week, prompt version and model). The engine lane then *replays* that ledger with zero LLM calls, so stop, sizing and cadence A/Bs cost what an EB run costs.
3. **The binding validity problem is LLM memorization, not plumbing.** Engine price history starts **2020-07-27**: Alpaca IEX returns `[]` before that date (verified in `AlpacaBarsCache`). Every claude model available through the CLI has a training cutoff inside that window. So any engine backtest of an LLM picker before the model's cutoff is contaminated (`research2/03_llm_deep_research_picking.md` §2b). The engine can validate the *mechanics*. Only a forward paper test can validate the *edge*.

---

## 1. How target-book lanes are registered, configured and called

### 1.1 Registration (no registry table; it is name-based)
- **Name to module/class.** `broker.py:2063-2077` `_strategy_name_to_module_and_class` maps `strategy_eb` to `strategies/strategy_eb.py::StrategyEb`. The loader is `broker.py:2079-2121` (it checks the flat path first, then `strategies.crypto.`).
  - The class name is load-bearing. A miss logs "has no run_once method; skipping" and the run is inert (`broker.py:7509-7512`; comment at `strategies/strategy_eb.py:207-211`).
- **UI and editor metadata.** The first-line header `# INTELLISTOCK_SCHEMA: {json}` plus `# INTELLISTOCK_DESCRIPTION:` is parsed by `backend/strategies_meta.py:16-63`. The schema *is* the default lane spec: `strategy`, `weight`, `execution_position`, `decision_phase: "pre"`, `execution_scope: "run_once"`, `conditions`, `config` (see `strategies/strategy_eb.py:1`).
- **Doc shape.** A `Strategies` row is `{name, strategies: [lane_spec,...]}`. Current lab docs (read-only DB):

  | Doc | Name | Lanes |
  |---|---|---|
  | 200 | EB | EB live |
  | 201 | "EB + Outlier Sleeve" | — |
  | 202 | HX lab | — |
  | 203 | EB frontier | — |
  | 204 | Swing paper | `strategy_swing` + `strategy_wheel` |

### 1.2 Per-tick call path (backtest and live share it)
- **Spec selection.** At boot, `broker.py:12764` filters the run-once specs by `execution_scope == "run_once"`.
- **The driver is `run_run_once_strategies`** (`broker.py:7313`). For each spec it:
  - merges `conditions ∪ config` (`7516-7522`)
  - injects the Alpaca creds, the data feed (`iex`/`sip`), `instance_id` and `_nexus_is_live_mode`
  - injects `nexus_backtest_deterministic`, which is on in backtests (`7524-7590`)
  - takes a per-lane cache, `cache_store.setdefault(name, {})` (`7591`)
  - calls `instance.run_once(symbols, prices, current_time, config, conditions, data=, portfolio_emulator=, strategy_cache=, time_increment=, mode=)` (`7583-7601`)
- **Backtest vs live difference.** `data` is the dict of bars for the whole window in backtest, and `None` live. Live, EB is special-cased: `_eb_decision_outside_rth` (`broker.py:7266`) holds decisions until RTH, and `_eb_live_portfolio_view` (`7228`) wraps pending orders.
- **Lookahead hazard.** In a backtest **`data` holds future bars**, so a lane must cut by time itself. The canonical cut is `strategy_x.pit_daily_observations` (`strategy_x.py:527`). It infers whether bars are daily and, for daily bars, demands a *strictly earlier* NY session.
- **Output channel** (`broker.py:7602-7640`, merged at `~17918-17965` per [07]): `_nexus_position_sizes`, `_nexus_discovered`, `_nexus_executable_buys`, `_nexus_sell_enforcement`, `_nexus_action_intents`, `_nexus_max_positions`. EB's `_emit` (`strategies/strategy_eb.py:137-158`) is the minimal correct payload:
  - set `_cash_reserve_floor_pct=0.0`, or a default 10% cash floor blocks buys
  - stamp sells with `action_intent="etf_sell"`, or the Z2.1 check flags them

### 1.3 `targets_to_orders` (`strategy_x.py:1140-1243`)
`targets_to_orders(targets, nav, positions, prices, cash, config, owned)` returns `(decisions, sizes)`.

**Scope**
- Only symbols in `owned ∪ targets` can be sold (`1166-1171`). That rule stops one lane from liquidating another lane's book.

**Sells** (processed first, in sorted order)
- A held symbol absent from targets is a full exit.
- The `core_band_pct` no-churn band applies only around nonzero targets (`1184-1192`).
- `min_order_usd` applies to trims.

**Buys** (largest weight first, `1203`)
- An *open* from flat ignores the band (`1209-1222`).
- Buys are sized off **settled cash only**: `spend = min(delta, cash*(1-cost_haircut_pct))` (`1223-1243`).
- So a rotation leaves one bar in cash unless the doc sets `backtest_credit_pending_sell_proceeds` (`broker.py:3647`) or the lane re-sweeps idle cash on the next session the way EB does (`strategies/strategy_eb.py:_sweep`, `PENDING_TARGETS`).

**Config keys it reads:** `core_band_pct`, `min_order_usd`, `cost_haircut_pct`.

**Implication for this lane:** pass `targets = {sym: weight}` with weights ≤ 0.25, the sum ≤ 1, and BIL as remainder. Pass `owned` = every symbol the lane has ever held, persisted in the cache. Otherwise a name dropped from this week's 8-12 picks is never sold, because it is outside `targets`.

### 1.4 Cadence: daily, weekly, pre-open
- **There is no framework-level weekly hook.** Cadence is the lane's own job. EB evaluates its state machine only when `session_weekday(session_id) in rebalance_weekdays(cfg)` (`backend/strategy_eb.py:550,563`; applied at `strategies/strategy_eb.py:281-298`). Its latch is `LAST_REBALANCE_KEY == session_id` (`eb_should_trade`, `strategy_eb.py:786`). Copy this. The research step runs weekly, but the **20% trailing stop must be checked every session**: track the peak close per symbol in the cache and exit when `close ≤ 0.8·peak`.
- **Backtest clock.** `_advance_backtest_time` (`broker.py:21826-21836`) steps by the granularity in seconds and skips to the next market open outside the session. So at `86400` there is one tick per session, at the open.
- **Order fills.**
  - **Ordinary orders are next-bar** (`NextEventExecutionSimulator`, `simulated_execution.py:633`).
  - **`fill_at_next_open`** exists for swing (`SimulationOrder.fill_at_next_open`, `simulated_execution.py:317,384`; due rule `_due_next_open`, `1101-1108`; stamping at `portfolio_emulator.py:1755-1777`; 5-session expiry at `portfolio_emulator.py:132,1361`). The lane passes it as a hint in `_nexus_position_sizes[sym]`: `{"buy_cash":…, "fill_at_next_open": True, "whole_shares": …}` (`strategies/strategy_swing.py:455,1061,1652`). Swing's spec says this "fills at the OPEN of the first bar whose session starts after the decision time" (`docs/superpowers/specs/2026-09-24-swing-trader-port-design.md:240`).
- **"Decide pre-open, fill at that session's open" is therefore approximate** (inference from `_due_next_open`, not run):
  - **At `86400`:** a decision on session S's first tick (closes ≤ S-1, news < S 09:30) probably fills at **S+1's open**, a one-session lag. Daily bars are stamped before the 09:30 decision.
  - **At `900`:** an ordinary market order fills on the 09:45 bar of S, which is the closest available approximation of "pre-open decide, open fill".
  - Verify on a real swing backtest log before relying on either.

### 1.5 What a new lane needs (checklist)
1. **`backend/<lane>.py` (pure).**
   - Universe, target-book construction (8-12 names, 25% cap, leveraged ≤ 20% total, inverse allowed), trailing-stop rule, `DEFAULTS`.
   - Use `pit_daily_observations` for every price read.
2. **`backend/strategies/<lane>.py` wrapper.**
   - `INTELLISTOCK_SCHEMA` header, with `<lane>_enabled: false` by default.
   - Class `PascalCase` with `run_once`.
   - `_log` via `intellistock_logger`. stdout is discarded (`strategies/strategy_eb.py:42-55`).
   - `_log_once` refusals. Fail closed (`{}`) on blind data or unreadable NAV.
   - Cache keys prefixed `_<lane>_`.
3. **`broker.py` fetch-site wiring.**
   - Add a `_<lane>_universe_symbols(cached_strategies)` beside the others (`broker.py:4348-4800`) and loop over it at the backtest bar fetch (`12791-12838`). Otherwise the fixed ETF legs have no bars.
   - **Dynamic picks** load on demand: any symbol in `_nexus_discovered` / `_nexus_executable_buys` is fetched by `_ensure_backtest_history_for_symbols` (`broker.py:1812`; call sites `18007/18057/18118/18176/18233`) at the run's timeframe. The candidate *research* bars should come from the lane's own daily-bar loader (swing pattern, §3.1), not from this.
4. **Single-position cap and live envelope.**
   - Set `honour_single_position_cap: true` and `broker_max_single_position_pct: 0.25` in config. `_strategy_eb_single_position_pct` (`broker.py:4447`) honours the cap in backtest for any lane.
   - **Live honours it only for lanes listed in `_LANE_ENABLE_FLAGS`** (`broker.py:4427-4440`), and each listed lane also needs a `defaults_by_lane` row ([07] §2.2). Unregistered, live trims every buy to the 15% failsafe. That is how XS and HX shipped inert.
5. **Live one-lane rule.** `live_equity_bars.other_enabled_run_once_lanes` (`live_equity_bars.py:48`) means the new lane cannot share a live doc with EB without changing that rule ([07] §2.4). Use a dedicated doc.
6. **Live LLM work stays out of the tick.** Swing budgets the tick (`swing_trader/clock.py:25-30`: 1500 s default, 55 s per-candidate reserve, and a resumable scan). A 40-call weekly research pass does not fit in a tick. Run it as a separate scheduled job that writes the ledger (§2.5); the live lane reads the newest row ≤ now.

---

## 2. LLM calls in backtest mode

### 2.1 Entry points (`backend/llm_utils.py`, 7308 lines)
- **Structured:** `call_structured_llm_by_provider` (`llm_utils.py:2832`) dispatches `claude-cli` to `_call_claude_cli_structured_from_strategy` (`2464`). That calls `chatbot/claude_cli_provider.py::call_claude_cli_structured` (`2333`): one `claude -p --output-format json --json-schema … --tools "" --strict-mcp-config --no-session-persistence` subprocess per call (`_SAFETY_FLAGS`, `claude_cli_provider.py:348-353`).
  - **All tools are off**, so the model cannot browse or fetch filings. Deep research means the lane assembles the PIT dossier into the prompt, which is good for PIT safety.
  - The output is validated with Pydantic.
- **Plain:** `_call_claude_cli_plain` (`llm_utils.py:1895`).
- **Web search:** `call_llm_with_web_search` (`llm_utils.py:7091`) supports claude-cli via `--tools WebSearch`. **It is lookahead in a backtest** because it searches today's web. Swing's backtest gate explicitly turns news and web search off (`strategies/strategy_swing.py:643-652`, `point_in_time=True`).
- **Provider selection.** Each role is a Models row referenced by `<role>_llm_model_id` and resolved per tick into `<role>_llm_provider/model/api_key` (`broker.py:7413-7436`). claude-cli needs no key: `resolve_api_key_for_provider` (`llm_utils.py:239`) and `ai_analyst._KEYLESS_PROVIDERS` (`swing_trader/ai_analyst.py:58`).
  - In backtest, a model-resolution failure is **fatal by design** (`broker.py:7437-7461`): no silent LLM-off runs.
- **Auth inside backtest containers.** `engines/backtest_engine.py:1033-1051` bind-mounts the host `~/.claude` read-only (via `CLAUDE_HOST_HOME`/`CLAUDE_HOST_CONFIG`), so backtests bill the **operator's Pro/Max subscription**. That is the same quota as the operator's interactive Claude Code use.

### 2.2 Concurrency, timeouts, retries
| Knob | Value | Where |
|---|---|---|
| Global spawn cap per process | `CLAUDE_CLI_MAX_CONCURRENT=10` (BoundedSemaphore, acquire timeout 30 s, else `ClaudeCliError`) | `claude_cli_provider.py:65,72,2410` |
| Structured turn timeout | `CLAUDE_CLI_TURN_TIMEOUT_SEC=120` unless the caller passes `timeout_sec` | `claude_cli_provider.py:68,2388` |
| Plain timeout | `LLM_REQUEST_TIMEOUT` (default 180 s) | `llm_utils.py:376-384` |
| Retries | `retries`/`output_retries` (max of the two); exponential backoff 2·2^n s, cap 60 s | `llm_utils.py:2630-2632, 397` |
| Validation retry | retried, then terminal | `llm_utils.py:2721-2752` |
| Subscription quota | `ClaudeCliRateLimitError` is **terminal immediately, no retry** | `llm_utils.py:2718-2724` |
| Not logged in | terminal | `llm_utils.py:2714` |
| Daemon reuse | `CLAUDE_CLI_DAEMON_FOR_STRUCTURED=1`, opt-in, default off | `llm_utils.py:2594-2626` |

- **Gap:** the critical guard (`llm_critical_guard.py:68-122`) pauses a backtest on auth failures, 402 credits, *codex* quota and persistent 5xx. It does **not** catch a claude-cli quota hit, so a quota hit becomes silent per-call failures and the run keeps going. Bt 331350 did exactly that, with 138 failed calls (below). The lane must fail closed: hold the book and log when a research stage fails. Or the guard must gain a claude-cli quota class.

### 2.3 Prompt-hash caching
- **Table:** `GraphNexusLLMPromptCache`. Key `sha256(model|effort|prompt)[:48]` (`llm_utils.py:5798, 5869-5873`); read at `5875`, write at `5927`.
  - Poison responses ("rate limit", "429", "i cannot", …) and malformed JSON are never stored (`5816-5820, 5951-5960`).
  - The cache disables itself after 5 consecutive DB failures.
- **claude-cli structured** consults the cache only with `use_prompt_cache=True` (`llm_utils.py:2530-2575, 2413-2421`). Deterministic backtest mode forces the structured path through the cache (`configure_llm_prompt_cache(structured=True)`, `5840-5859`; the flag is set at `broker.py:7562-7590`).
- **Under a model-evidence session** (strict evidence runs) every cache read and write is refused (`_phase_alpha_helpers.py:35-70`).
- **Practical consequence.** A cache hit needs a *byte-identical prompt*. If the prompt contains anything portfolio-dependent (holdings, NAV, cash), each A/B arm misses and pays full price. Make the research prompts depend only on PIT world state (date, news, filings, prices), never on the book.

### 2.4 Point-in-time guards that exist
- **`pit_mode` = `strict | research`** (`backtest_evidence_options.py:68-75, 161`). It is honoured **only for Graph Nexus** (`broker.py:7114-7135`). `research` runs GNA on current-state data, is stamped `legacy_unverified`, and is promotion-ineligible. It does not touch a new lane.
- **Generic PIT primitives:**
  - `point_in_time_data.PointInTimeContext` (`point_in_time_data.py:232`)
  - `filter_available(records, context, available_at)` (`320`), which keeps a record only if its availability ≤ as_of and raises in strict mode for records with no timestamp
  - `ticker_research.py`: an adapter pipeline in which every record must carry a timezone-aware `available_at`, and sources with no verifiable as-of are banned
- **Swing's in-backtest LLM gate is the working precedent** (`strategies/strategy_swing.py:643-718`):
  - it is opt-in (`ai_gate_in_backtest: false` by default)
  - it has a hard per-run budget (`ai_backtest_max_calls: 300`, counted in the cache under `_swing_ai_bt_calls`)
  - inputs are PIT-only: no news, web search or earnings lookup; the sector RSI comes from bars before the session
  - a failed or missing model means skip, never enter unscored
  - it prints a banner that the model's training data may know outcomes (`strategy_swing.py:106-107`)

### 2.5 Measured claude-cli throughput (`LLMUsage`, read-only)
All-time, 930 claude-cli calls, all `haiku`, all in backtests (2026-07-21 to 07-27):
- **Latency:** mean 50.4 s, p50 51.2 s, p90 80.2 s. Mean tokens per call: 2.7k input, 4.4k output.
- **Bt 331350 (GNA):** 798 calls in 5.77 h, which is **138 calls/h average with ~1.9 in flight**.
  - Short bursts in other runs reached **604-654 calls/h with ~10 in flight**, the semaphore cap.
  - **The subscription session limit hit after 655 calls (~5.25 h, ~5.5M in+out tokens);** the remaining 138 calls failed with "Claude Code subscription quota hit … session limit · resets 11:30am".
  - For comparison: openrouter 230k calls (mean 5.5 s), bedrock 61k (6.8 s).
  - The plan tier at the time is unknown.

**Estimate for 260 weekly cycles × ~40 calls = 10,400 calls** (my assumptions, not measured):
- **Compute-bound.** At 50-90 s per call (Sonnet/Opus with 15-40k-token dossiers will be slower than the Haiku sample), serial execution takes ~145-260 h.
  - Each week has ~3 dependent stages (trend scan, then ~30 parallel candidate dossiers, then portfolio construction). At the 10-wide cap that is ~4-6 min per week, so **~18-26 h wall clock** if quota were unlimited.
- **Quota-bound, the real limit.** The observed window held ~5.5M Haiku tokens per 5-hour session. One research week is ~40 × (25k in + 3k out) ≈ 1.1M tokens, on Sonnet/Opus, which draw the limit several times faster than Haiku.
  - That works out to roughly **0.5-2 simulated weeks per 5-hour window**, so 260 weeks needs ~130-520 windows: **about 1-4 months of continuous wall clock**, before weekly caps.
  - It also starves the operator's own Claude Code use, because it is the same subscription.
- **Every additional engine run of the same configuration costs the same again** unless prompts are cache-stable (§2.3).
- **Conclusion:** generate a **PIT research ledger once, offline**.
  - **Build job:** a resumable script that tolerates quota resets and sleeps until the reset time. It writes one row per `week|prompt_version|model` with the full inputs digest, the trend list, ~30 candidate verdicts and the target book.
  - **Storage:** a table registered in `backend/db/schema.py` and accessed through `from db import store`, per CLAUDE.md.
  - **Replay:** the engine lane only replays rows with `as_of < decision time`. The 260-week ledger is built once (months of quota). After that, every engine run costs zero LLM calls and is deterministic.
  - **Live:** the same job runs live on Sunday or Monday pre-open.

---

## 3. Data available at simulated time T

### 3.1 Prices
- **Engine bars** come from `fetch_alpaca_historical_bars` (`broker.py:997`). `adjustment="split"` means **price-only**: no dividend total return (comment at `1006-1012`). The SPY benchmark separately uses `"all"`.
  - The feed is the brokerage's `alpaca_data_feed` (`iex` default; `broker.py:7484-7491`).
  - The cache table is `AlpacaBarsCache`.
- **History depth (verified).** IEX daily chunks for SPY from 2008-2019 store `[]`. The first real bar for SPY and TSM is **2020-07-27**. `1Day` chunks cover 1,675 symbols, `15Min` only from 2025-08-12, and `1Hour` from 2025-01-07.
  - **That is why every engine window starts in 2021.** IEX starts 2020-07-27, and lanes need 70-250 bars of warm-up (EB `min_history_bars` 70; swing SMA200 plus `LIVE_WINDOW_DAYS`).
  - 260 weeks (≈2021-11 to 2026-10) fits, just.
  - *Unverified option:* Alpaca's free plan reportedly allows historical **SIP** (not just IEX) older than 15 minutes. Setting the data brokerage's feed to `sip` might unlock 2016+ history. No SIP row exists in the cache today; test this before believing it.
- **Universe coverage.** Any US-listed symbol Alpaca carries can be fetched on demand, including ADRs and ETFs. Verified 2020-07 starts for TSM, ASML, NVDA, SMCI, SLV, USO, CPER, SOXL, SH, XLP and XLU. URA is cached only for 2024-05 onward, so check its fetch.
  - **IEX volume** is a small slice of consolidated volume. Volume filters and small-cap liquidity read wrong.
  - **Delisted names:** not checked. An LLM naming only survivors is a separate survivorship and lookahead channel.
- **Lane-owned daily bars (template):** `swing_trader/backtest_bars.py`.
  - `fetch_daily_bars` (`154`) issues batched multi-symbol requests (≤100 symbols), caches one row per symbol per year in `SwingDailyBars`, detects splits, and backs off on 429.
  - `OwnBars` hands out only the sessions strictly before the decision.
  - `SwingDailyBars` today holds 611 symbols, 2020-2026, IEX only.
- **PIT S&P 500 membership:** `SwingIndexMembership`, 132 rows from `SPX|2018-12-24` to `SPX|2026-08-18`. There is no mid-cap, small-cap or ADR universe table; the lane's candidate universe is whatever the LLM names, filtered by "has bars at T".

### 3.2 Fundamentals
- **SEC EDGAR XBRL with true filing dates:**
  - `backend/edgar_fundamentals.py`: companyfacts API, every vintage kept, `filed` date gating, CIK map, throttle, required User-Agent. It has **no DB cache and no decision-path call sites** (its own docstring, `:44-46`).
  - `backend/edgar_factors.py`: `factor_set(symbol, as_of)` returns profitability, accruals and similar, admitting only facts with `filed ≤ as_of`.
  - `backend/company_research.py:99`: `research_company(symbol, as_of)` builds a PIT viability dossier.
  - `backend/ticker_research.py:156`: `research_ticker`, with the PIT-filtered adapter registry.
- **Banned source:** yfinance fundamentals (no filing date; `factor_profitability.py` header). Swing's `days_until_earnings` is yfinance-live (`swing_trader/ai_analyst.py:198-217`), which makes it lookahead in a backtest; swing disables it there.
- **Missing:** filing text (10-K/10-Q/8-K narrative, MD&A) and earnings-call transcripts. `sec_edgar_supply_chain.py` downloads 10-Ks with `sec-edgar-downloader` for graph edges only. A "deep research" dossier would need a new PIT filing-text fetcher keyed on acceptance datetime.

### 3.3 News
**What is stored** (counts verified)

| Table | Contents |
|---|---|
| `GraphNexusNewsRaw` | 51,527 rows: alpaca 34,575, google_news 16,952. By year: 2010 60, 2021 100, 2023 207, 2024 847, **2025 28,445, 2026 21,868**. 461 distinct days. Mean excerpt 61 chars, mean summary 47 chars: headlines, not articles. |
| `NewsRaw` | 5,076 rows, 2025-11 to 2026-02 |
| `GoogleNewsCache` | 442 day-keys |

**Stored news cannot drive a 2021-2025 backtest.**

**Fetchable history** (the lane would build its own PIT news corpus into the ledger)
- **Alpaca news API**, `/v1beta1/news` with `start/end` (`strategies/graph_nexus_analysis.py:551, 16703-16749`; GNA pulls one page of ≤50 with no pagination). It is Benzinga-sourced and historical, with a `created_at` timestamp. Use pagination and `include_content`.
- **Google News RSS** with `after:`/`before:` date operators (`strategies/google_news.py:145-153`). The published date is parsed, but RSS search returns only a capped top-N per query.

**How the engine filters by time**
- Nothing filters generically. Each consumer must filter: GNA through its strict snapshot sources (`graph_nexus_analysis.py:2305`), the new lane through `filter_available` on `published_at < decision time`.

---

## 4. Cost, dividends, fractional shares

**Cost tiers** (`simulated_execution.py`)
- **Default:** `equity-measured-v3-nbbo23`, **23.2 bps one-way** (spread 45.6/2 + 0.1 slippage + 0.3 fees, measured on 61 real fills; `:92-122`). The nominal `equity-next-event-v1` is 5 + 10 + 0.3 (`:84-90`).
- **`etf-liquid` tier:** 4.4 bps (`:130-145`), covering only the closed list `{SPY, QQQ, TQQQ, QLD, SQQQ, BIL, GLD, IWM}`. It is opted in per POST with `equity_cost_tiers: "etf-liquid"` (`backtest_evidence_options.py:56`; `TieredExecutionCostModel`, `:147`, preset `:242`).
- **`equity_total_cost_bps`** on the POST overrides the cost flatly.
- **Effect on this lane's universe:** SLV, USO, XLE, CPER, URA, TLT, XLU, XLP, SH and SOXL all pay 23.2 bps unless the tier list grows. Large caps at 23.2 bps are over-charged; per the brief, ~5-20 bps is realistic. Small caps are roughly right.

**Dividends**
- Bars are price-only. The emulator accrues dividends only for symbols in `DEFAULT_EQUITY_DIVIDEND_YIELDS`, with a default of 0.0 (`portfolio_emulator.py:134-160`). It can be overridden per run with `dividend_yields=`.
- The SPY benchmark is total return. A stock picker therefore starts ~1.3%/yr behind SPY-TR plus its holdings' yield. This is the "dividend accrual mistaken for a ledger bug" trap in memory.

**Fractional shares and order minimums**
- Allowed. `MIN_EQUITY_ORDER_NOTIONAL = $1` (`portfolio_emulator.py:109-115`).
- `whole_shares` is opt-in per order (`simulated_execution.py:313,380`). A $5.5k book with 8-12 names (≈$450-690 per name) works fractionally.
- `min_order_usd` (EB 25) in `targets_to_orders` prevents dust trades.

**Shorting**
- Not needed. The book is long-only, inverse exposure comes through SH/SQQQ, and the live gate is reduce-only on sells ([07] §2.2).

---

## 5. Result reporting, and where regime scoring plugs in

**What a run persists**
- The terminal result dict is built at `broker.py:16300-16441`. It includes `portfolio_value_history`, `backtest_trades`, `backtest_decisions`, `backtest_refusals`, `logs` (last 500) and `dividend_summary`.
- It also includes **benchmark metrics** from `_compute_backtest_summary_with_benchmark` (`broker.py:1414`, called at `16329`): `benchmark_return`, `active_return`, `beta`, `tracking_error`, `information_ratio`, `deflated_sharpe_probability`, `max_drawdown_magnitude`, bootstrap bounds (`16421-16441`).
- Per-step `pv` and `trade` rows live in `BacktestSteps` (`backtest_result_store.py:111-170`).

**What the UIs display**
- **Web** (`frontend/src/views/BacktestDetailView.vue:925-960`): Total P&L, P&L %, start→end value, trades, elapsed, win rate, high/low, then the portfolio chart (`1315`), per-stock P&L (`1336`) and playback.
- **iOS:** `ios/IntelliStock/Features/Backtests/Data/BacktestModels.swift:70-114` (`BacktestSummary`) and `Views/BacktestDetailSections.swift` (Results `:87`, Strategy `:254`, Logs `:319`, Round trips `:674`).
- **Neither UI shows the benchmark, active return or max drawdown**. A grep finds no hits in `frontend/src` or `ios/IntelliStock`.

**Where regime scoring happens today**
- Only offline. `scripts/outlier_engine_test.py:29-47` holds the 25-window battery (bear/bull/chop/handoff/years/multi) with pinned baselines. `:68-89` rebuilds the NAV and SPY-TR path from `BacktestSteps` kind `pv`, and computes maxDD and attribution.

**Plug-in points**
1. **Pure module:** `regime_windows(snapshots, spy_tr) -> [{tag, kind, start, end, ret, spy_ret, active, max_dd}]`, computed at `broker.py:~16421` beside the benchmark fields and persisted as one new field.
2. **Web:** a "vs SPY by regime" table under the summary grid in `BacktestDetailView.vue` (~`:960`).
3. **iOS:** the field in `BacktestSummary` plus a Section in `BacktestDetailSections.swift`.

**For an LLM lane specifically**
- Run **one** long cycle and **slice** the 25 windows from its `pv` path, rather than posting 25 runs. Each separate run would redo research, or need the ledger anyway.
- Caveat: slices inherit open positions instead of starting from cash. Report both views where it matters.

---

## 6. Lab doc and instance pattern

**Existing lab instances** (read-only DB)

| Instance | Doc | Stored granularity |
|---|---|---|
| `strategy-eb-lab` | 201 | 60 |
| `strategy-hx-lab` | 202 | 86400 |
| `swing-paper` | 204 | 86400 |
| `alpaca-main` | 200 | 86400 |

`strategy-eb-lab`'s stored granularity of 60 is harmless only because every POST passes its own granularity.

**Rules for doc 201**
- Do not use it for this lane. It has been contaminated by a killed runner before (memory `reference_lab-doc-contamination`).
- `scripts/hx_lab_setup.py:27,47-53` hard-refuses docs 200 and 201.

**The right pattern is a new lab doc and a new backtest-only instance**, cloned from `scripts/hx_lab_setup.py` (idempotent):
1. Build the doc payload `{name, strategies:[lane(cfg)]}` with `<lane>_enabled: true` (`:56-64`).
2. `GET /strategies`; then `PUT /strategies/{id}` if the name exists, else `POST /strategies` (`:80-92`).
3. `POST /instances` with `{id, name, strategy_id, granularity: "86400", brokerage_id (cloned from strategy-eb), stocks}` (`:93-105`).
   - The field is `granularity`, *not* `granularity_time_increment`, or the instance defaults to 60 s.
4. An existing instance is re-linked with `POST /instances/{id}/link-strategy` (`:106-113`). `PATCH` silently drops `strategy_id`.
5. `POST /instances/{id}/stocks` per symbol.

`scripts/swing_lab_setup.py` is the variant for a dynamic-universe lab: it sets the same-tick funding flags and never links a live brokerage.

**Posting backtests**
- `POST /backtests {instance_id, stocks, start_date, end_date, granularity, initial_cash, equity_cost_tiers}` (`outlier_engine_test.py:124-127`).
- Strictly sequential (user veto), with `granularity` in seconds.
- Do not edit the lab doc mid-run: config is read live, and a live broker restarts on any doc edit (`broker.py:6448`).

**Live path later:** a separate paper doc and instance (swing-paper is the precedent), then the registrations in §1.5 items 4-5 before any real-money doc.

---

## 7. Verified vs inferred
- **Verified (code read or DB query today):**
  - all file:line references
  - the claude-cli latency, throughput and quota-hit numbers from `LLMUsage`
  - IEX history starting 2020-07-27, and the bar cache coverage
  - news table counts by year
  - lab doc and instance state
  - the cost tiers
  - the critical guard not covering claude-cli quota
  - the UIs not showing benchmark fields
- **Inferred or estimated, not tested:**
  - fill timing at 86400 vs 900 for `fill_at_next_open` (§1.4)
  - the 10,400-call wall-clock and quota estimates (assumed token sizes and model weights; plan tier unknown)
  - SIP historical availability on the free plan
  - Alpaca bars for delisted symbols
  - URA's full history
