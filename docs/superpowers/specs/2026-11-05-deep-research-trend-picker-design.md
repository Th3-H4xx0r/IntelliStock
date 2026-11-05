# Deep-Research Trend Picker — design

Date: 2026-11-05 · Status: DRAFT, awaiting operator approval · Scope: backtest only (no live or paper trading in this project)

## 1. Intent

The operator wants a strategy that does what their colleagues did with NVDA: notice big global trends early, work out
which companies benefit, research those companies deeply, and hold the best of them for months. It must be
backtestable in IntelliStock so the operator can judge it on numbers rather than anecdotes.

Research context (docs/superpowers/research/2026-11-04-orchestrator-and-stock-picking/, including
`recon_engine_llm_lane.md` and `recon_news_sources.md`): 16 agents found no systematic or AI stock-picking method that
beat SPY out of sample after costs, and an LLM backtest over dates the model saw in training is contaminated by memory.
The operator has read this and wants to build and measure it anyway. This design therefore puts **honest measurement**
first: every result is labelled by whether the model could have known the outcome.

## 2. Goals

- G1. A new strategy lane that, each week, (a) reads point-in-time global and company news plus market and macro data,
  (b) identifies and tracks trends, (c) maps trends to beneficiary instruments, (d) researches ~30 candidates deeply,
  (e) outputs a target book of 8–12 positions, and that sells on story break, stop-loss or a clearly better idea.
- G2. Backtestable on the IntelliStock engine from 2021 to the present, with zero lookahead from data (news, filings,
  prices, macro) — every input carries an availability timestamp strictly before the decision.
- G3. A scorecard that reports, per run: CAGR and max drawdown vs SPY total return and QQQ; results in each bull, chop and
  bear window; a random-portfolio baseline; and a split into **"optimistic" (before the model's training cutoff)** and
  **"honest" (after it)**.
- G4. The operator can change the AI model and provider in the strategy config (Claude subscription via claude-cli,
  Anthropic API credits, or other configured providers) without code changes.
- G5. Re-running a backtest with different trading rules (stop level, position size, swap threshold) costs no AI calls.

## 3. Success bar (operator's words)

"Beat the S&P in all market regimes — bull, chop and bear — and make at least 5% higher than the S&P" (per year).
The scorecard reports this as pass/fail on the **honest** period and, separately, on the optimistic period. Passing
only on the optimistic period is not a pass.

## 4. Non-goals

- No live or paper trading in this project (a later project, only with the operator's explicit go). Never touch doc 200
  or the `alpaca-main` instance; do not use doc 201.
- No intraday or day trading. No options. No short selling (bearish exposure only via inverse ETFs).
- No theme ETFs other than the commodity/materials and defensive sets listed in §5.3.
- Not a replacement for Graph Nexus; it does not reuse Graph Nexus scoring.

## 5. Decisions the operator made

5.1 Trend types: tech waves; geopolitics (wars, tariffs, sanctions, elections); economy and rates (rates, inflation, oil,
dollar); consumer and health (spending shifts, drugs, demographics).

5.2 Holding style: ride trends for ~1–6 months; research re-checked weekly; trade only when the story changes.

5.3 Universe:
- US large caps (S&P 500), US mid and small caps, and foreign companies listed in the US (ADRs).
- Commodity/materials ETFs: e.g. GLD, SLV, USO, XLE, CPER, URA (builder may add close equivalents).
- Defensive: cash in BIL; defensive assets such as gold, Treasuries (TLT), utilities (XLU), staples (XLP).
- Inverse ETFs for bearish bets: SH, SQQQ (and similar).
- Leveraged ETFs (2x–3x, e.g. TQQQ, SOXL) on high-conviction trends only.
- Excluded: other theme ETFs (e.g. ITA, SMH, XBI).

5.4 Book: 8–12 positions (~10% each).

5.5 Bear defense: may move to cash, shift to defensive assets, and buy inverse ETFs.

5.6 Leverage: a small slice — at most ~20% of the account in leveraged ETFs, high-conviction trends only.

5.7 Sell rules: (a) story breaks (the thesis no longer holds); (b) stop-loss at **20% below the highest close since
purchase**; (c) swap for a clearly better idea. No automatic profit-taking.

5.8 Backtest start cash: **$5,500** (matches the real account).

5.9 Research depth: **Deep** — ~40 AI calls per weekly cycle (one full research note per candidate).

5.10 Model: configurable by the operator; likely an older strong model (longer honest window), possibly via Anthropic
API credits rather than the Claude Code subscription.

5.11 Honest-test policy: run the full 2021→present period to develop and debug, labelled "optimistic"; judge the
strategy on the period after the configured model's training cutoff, then on forward paper trading (later project).

5.12 Build scope: **honest period first.** This project builds the research ledger only for the weeks after the
configured model's cutoff, then stops for review with the operator. Building 2021→cutoff (≈10k calls at Deep depth,
≈$1k+ on API credits or 1–4 months of subscription quota) is a separate decision after the honest results are seen.
The builder and lane must still support any date range, so the backfill needs no new code.

5.13 Granularity: **15-minute steps (900 s)**, per the operator's standing backtest rule, so Monday decisions fill on
Monday morning. 15-minute price history for the tested period must be available (cached only from 2025-08 today; fetch
as needed).

5.14 Success "+5%" means **5 percentage points per year** over SPY total return (played back to and accepted by the
operator).

## 6. Designer's calls (operator may override; marked as mine)

- C1. **Research ledger, replayed by the engine.** The AI research runs offline in a resumable job and is stored as one
  ledger row per week. The engine lane only reads ledger rows dated before each decision. (Reason: measured claude-cli
  throughput ≈138 calls/h and a subscription session limit after ~655 calls make in-engine research take months per
  run, and every A/B would pay again.)
- C2. **The ledger is path-independent.** Research outputs depend only on world state at the date (news, filings,
  prices, macro) and on the ledger's own prior weeks (trend list, previously recommended names and their theses) — never
  on the engine's simulated portfolio. The weekly output is a *ranked list* with conviction, thesis and "breaks-if"
  conditions, plus a regime call. Turning that into trades (hysteresis, swaps, stops, caps) is deterministic code in the
  lane. This is what makes G5 possible.
- C3. Inverse ETFs count toward the same 20% cap as leveraged ETFs ("leveraged + inverse ≤ 20% of NAV").
- C4. Single position cap 25% of NAV (safety limit, not profit-taking); target weights ~8–12% each.
- C5. A stopped-out name goes to cash (BIL) until the next weekly cycle and may be re-bought only if that week's research
  ranks it again.
- C6. "Clearly better idea": a new candidate replaces the weakest holding only if its conviction exceeds the holding's by
  a configurable margin (default: 2 points on a 10-point scale) — to avoid churn.
- C7. Decision time: Monday pre-open (or first session of the week) using data available before 09:30 ET that day.
- C8. Benchmarks: SPY total return (primary), QQQ, and a random baseline = many random equal-weight portfolios of the
  same size drawn each week from **that week's AI candidate list** (isolates selection skill from trend/universe choice),
  plus random draws from the full tradable universe.
- C9. The lane is named `strategy_trend_research` (class `StrategyTrendResearch`); "TR" in the UI.
- C10. **Ledger chains.** Every ledger row carries a `chain_id`. The honest chain starts **cold** at the model cutoff
  (no inherited trend list or picks from contaminated weeks). A later 2021→cutoff backfill is a separate chain; a run
  that spans both shows the join at the cutoff explicitly. Key: `chain_id | week | model | prompt_version`.
- C11. `research_model_cutoff` = the **later** of the dates the model vendor publishes for that model (conservative).
  Operator sets the model; the builder supports the `claude-cli` and `anthropic` (API credits) providers at minimum.
- C12. **Strict regime scoring.** "Beat the S&P in all regimes" = every bull, chop and bear window in the scored period
  beats SPY-TR. Windows come from the battery in `scripts/outlier_engine_test.py`; dates after the battery's last window
  are classified by a fixed, pre-declared rule (builder writes it before looking at results). A regime with no window
  in the honest period is reported **"untested"**, never "pass". Note: the battery's bear windows are 2022 H1,
  2025-02-15→04-15 and 2026-02-01→04-01, so a model with a 2026 cutoff leaves no honest bear window.
- C13. A dev-only **budget cap** in config (max AI calls per build run) so a mistake can't burn quota; the honest-period
  build itself uses Deep depth (5.9).

## 7. How it works

### 7.1 Point-in-time data store (new)
One store for everything the research reads, keyed by `published_at` and `available_at` (availability is what gates
use). Backfilled once for 2020-07 → present, appended weekly afterwards. Reuse the PIT primitives already in the repo
(the adapter rules in `backend/ticker_research.py`,
spec `2026-08-05-ticker-research-pipeline-design.md`). Do NOT use `point_in_time_data.filter_available` for gating: it admits records at `as_of` (`<=`); TR gates strictly (`<`).

| Source | Use | Notes from recon |
|---|---|---|
| Alpaca News (Benzinga) | company + market news | history from 2015; `created_at` to the second; 50/request, paginate; 200 req/min; ~1.8M articles 2021–26 ≈ 3 h; 28–40% have full content. **Use paper-account data credentials only.** |
| GDELT DOC 2.0 (and optionally Events) | global/geopolitical themes, volume and tone | 2021+; strict rate limit (~15–20 s spacing); pre-aggregate weekly theme timelines |
| SEC EDGAR | filings and XBRL facts | gate on `acceptanceDateTime` (filings) and `filed` (facts); reuse `backend/edgar_fundamentals.py`, `edgar_factors.py`; add PIT filing-text excerpts (MD&A, risk factors, 8-K items) |
| ALFRED (FRED vintages) | macro data as first released | no key; real revisions exist |
| Static calendar | FOMC, CPI, jobs report dates | for context only |
| Engine/Alpaca daily bars | prices, trend stats | IEX daily from 2020-07-27; reuse `swing_trader/backtest_bars.py` pattern |

The existing `GraphNexusNewsRaw` / Google News tables are **not** used (they hold only the newest 50 articles per
fetch, and Google rows are date-only).

### 7.2 Weekly research cycle (offline ledger builder)
Stage 1 — Weekly digest (deterministic, no AI): compress the week's ~4–6k articles into ~150–300 representative
headlines, plus GDELT theme deltas, macro releases, and a market tape (index, sector, commodity, rates and dollar moves).

Stage 2 — Trend scan (1 call): update the running trend list. Each trend: name, category (5.1), state (emerging /
strengthening / mature / fading / broken), confidence, evidence (digest item IDs), beneficiaries and losers. Also a
market regime call (risk-on / neutral / risk-off / crisis) with reasons.

Stage 3 — Candidate mapping (1–2 calls + deterministic filter): name instruments exposed to active trends, plus all
names currently in the ledger's recommended list. Keep those with daily bars at the date and minimum price/liquidity
(builder sets thresholds; IEX volume understates real volume). Target ~30 candidates.

Stage 4 — Deep research (1 call per candidate, ~30): a point-in-time dossier per candidate — PIT fundamentals and
growth trend, latest filing excerpts, the last 4–8 weeks of its news, price trend stats (returns, distance from moving
averages, relative strength, drawdown), next earnings date if known from filings. Output: conviction (0–10), thesis,
explicit "breaks-if" conditions, "how much is priced in", and for previously recommended names, whether the thesis is
intact or broken.

Stage 5 — Book proposal (1 call): ranked list of 8–12 picks with conviction, target weights, defensive/cash share
implied by the regime call, any leveraged or inverse slice with justification.

Every prompt states the simulated date and that only the supplied material may be used; web search and tools stay off.
Prompts and rules are versioned (`prompt_version`); a ledger row records model, provider, prompt version, input digests
and outputs.

Builder job requirements: resumable; never writes a partial week; on quota or rate-limit errors sleeps until reset and
resumes (a claude-cli quota hit is currently a silent per-call failure — it must be detected); on any stage failure
records the week as failed (the lane then holds its book for that week — fail closed). Builds run forward in time
within a chain (C10); this project builds only the honest chain (5.12).

### 7.3 Engine lane (replay)
- New `run_once` lane following the existing target-book pattern (template: `strategy_swing`; output via
  `strategy_x.targets_to_orders`, passing every symbol the lane has held as `owned`).
- Weekly (C7): read the newest ledger row with `as_of` before the decision; apply hysteresis, swaps (C6), caps (C3, C4),
  regime-driven defensive share; emit target weights with BIL as the remainder.
- Daily: trailing stop — exit any position whose close is ≤ 80% of its highest close since purchase (C5).
- Uses only bars strictly before the decision session (`pit_daily_observations`).
- Config (strategy doc): ledger selector (model + prompt version), all rule parameters, and `research_model_cutoff`
  (date) used by the scorecard split.
- Runs on a **new lab doc + new backtest-only instance** (pattern: `scripts/hx_lab_setup.py`), granularity 900 (5.13);
  the weekly decision fires on the first tick of the week's first session, the stop check once per session.

### 7.4 Scorecard
Computed from one long run's NAV path (not 25 separate runs):
- Totals vs SPY-TR and QQQ: CAGR, excess per year, max drawdown, % of rolling 12-month windows beating SPY.
- Regime windows (reuse the bull/chop/bear window battery in `scripts/outlier_engine_test.py`): return vs SPY per window.
- Random baselines (C8): percentile of the strategy among random portfolios.
- Split at `research_model_cutoff`: optimistic vs honest, with the §3 pass/fail on each.
- Persisted with the backtest result, and shown on the web and iOS backtest detail pages (neither shows benchmark
  results today).
- A readable research journal per week (trends, picks, theses, why sold) exported from the ledger.

## 8. Constraints

- Database access only through `backend/db` (`from db import store`); new tables registered in `backend/db/schema.py`.
- Engine backtests are posted strictly one at a time; granularity passed in seconds.
- Engine bars are price-only (no dividends): the strategy starts ~1–2%/yr behind SPY total return; report this.
- Engine cost: 23.2 bps one-way for most symbols (conservative for large caps); do not change existing cost presets.
- No secrets in logs or outputs. Never use the live account's credentials for news.
- Work on a new branch from `main`. No merging and no live changes without the operator's explicit go.

## 9. Known limits (to be stated in every report)

- The model may remember 2021-to-cutoff outcomes; only the honest period and later paper trading judge the edge.
- Survivorship: the AI tends to name companies that still exist; delisted names may lack bars.
- Small-cap liquidity is mis-measured by IEX volume.
- The honest period may be short, depending on the configured model.

## 10. Left to the builder

Digest ranking method (must be deterministic and point-in-time); storage layout, partitioning and retention; liquidity
thresholds; exact prompt wording and output schemas; how GDELT is aggregated; whether to add an ETF cost tier
(as a new opt-in preset); test design (must include: no record after `as_of` reaches a prompt; trailing stop; caps;
replay determinism — the same ledger gives byte-identical trades).
