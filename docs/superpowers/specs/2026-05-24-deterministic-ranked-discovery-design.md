# Design: Deterministic + Rank-Based Stock Discovery (and exit/sizing tuning)

- Date: 2026-05-24
- Branch: `claude-code-integration`
- Author: operator + Claude
- Status: Phase 1+2 implemented (2026-05-24); exit/sizing (Phase 3) pending operator-run backtests

## Implementation Note (2026-05-24) — what was actually built vs the design above

Reading the code changed three things in the original plan:

- **Determinism was mostly an operational gap, not new code.** A multi-phase determinism
  program already exists: α.1 (forced sentiment cache), α.2 (Neo4j weekly snapshot), α.3
  (`derive_backtest_seed` RNG, wired in broker.py), α.4 (mcap pre-seed). The remaining gap:
  the engine spawned the backtest broker **without `PYTHONHASHSEED`**, so Python set-of-string
  iteration varied run-to-run during discovery. Fix: `backtest_engine` now forwards
  `PYTHONHASHSEED` (default `"0"`) into the broker via the unit-tested
  `backtest_determinism_env_vars` helper; also added to `docker-compose.yml` + `.env.example`.
  Operators should verify `PYTHONHASHSEED` in their prod (Dockploy) backtest-engine env.
- **Phase 1a (temperature=0) is infeasible:** `_omit_temperature` returns True for all gpt-5.x
  models — they reject a custom temperature (that is *why* α.1 caches instead). Dropped.
- **Phase 1c (global composite top-N) deferred:** discovery is a 7-source quota/eviction system,
  and most sources already sort deterministically (propagation, momentum). Instead of a risky
  rewrite, added explicit deterministic per-source ordering at the two order-dependent points
  (trend `_discover_stocks`, Benzinga) via `_ordered_discovery_candidates` (strongest-first,
  ticker tiebreak). This satisfies the stated goal ("no dict-order luck, strongest make the cut,
  reproducible") at low risk. A full cross-source global ranker remains an optional,
  separately-validated follow-up.
- **Headline for the original comparison:** #357345 (Azure gpt-5.4-mini) vs #616531 (Codex-CLI +
  Bedrock kimi-k2.5) used **different models**, so their baskets differ by design. Determinism
  guarantees apply only to **same-config** re-runs (same model, PYTHONHASHSEED=0, warm cache).
- **Phase 2 (clear stale `pause_*` on resume)** implemented via `cleared_pause_fields()`.

Operator validation: re-run the SAME config twice (with `PYTHONHASHSEED=0`) and confirm identical
`tickers[]` + `pnl_percent`. Then proceed to Phase-3 exit/sizing knob tuning on the now-stable basket.

## Motivation

Comparing two backtests of the **same** Graph Nexus strategy (`strategy_id 179`), same
~$7,000 start, same start date, ~same end date:

| | #357345 | #616531 |
|---|---|---|
| Return | **+266.3%** | **+130.5%** |
| Models | Azure gpt-5.4-mini | Codex-CLI gpt-5.4-mini + Bedrock kimi-k2.5 |
| Tickers | 105 | 80 (only **35 shared**) |
| LLM errors | 2,088 (57%) | 48 (2.5%) |

Diagnosis (see investigation notes): the gap is **not** a bug and **not** the 48 errors
(the baseline had 43× more errors and did better — failures degrade gracefully to neutral).
It decomposes as:

- **~56% ($5,330): different discovered basket.** The universe is dynamically discovered at
  runtime and is **non-reproducible** — the two runs surfaced largely different baskets.
- **~44% ($4,177): the strategy leaked gains on shared winners** via premature exits
  (INTC sold +6.5% then ran +214%; SNDK cut −15% + blacklisted then ran +517%) and capital
  starvation (`deferred_unfunded_buy`, `max_positions=8`).

The backtest replays byte-identical historical price/news data every run, so discovery
*should* converge on the same basket. It does not, because the computation is not pure.

## Goals

1. **Deterministic discovery** — re-running the same historical backtest produces the
   **identical** basket every time. Discovery stays **fully dynamic** (re-derived from scratch
   each run from the same data); **no** hardcoded list, **no** pinned/frozen basket, **no**
   cross-run state carryover.
2. **Rank-based selection** — the basket is the **top-N by a stable composite score**, so the
   strongest candidates always make the cut instead of dict/set-iteration luck.
3. **Exit/sizing tuning** — reduce the premature-exit + capital-starvation leak by tuning
   **safe structural config knobs**, validated across **multiple periods** to avoid overfitting.
4. **Bugfix** — clear stale `pause_*` fields on backtest resume.

## Non-Goals

- No hardcoded ticker lists or operator-pinned baskets (explicitly rejected by operator).
- No rewrite of the exit-decision pipeline in this pass (knob-tuning only; deeper redesign deferred).
- Not changing **live** trading determinism (live sees new data daily; determinism is a
  backtest concept). Determinism mechanics are scoped to backtest mode. Selection improvements
  may apply to both but are validated separately before any live impact.

## Background / Current Mechanics (from code mapping)

Discovery lives **inside** `backend/strategies/graph_nexus_analysis.py::run_once`, **not** in
`backend/discover.py` (which is an unrelated offline screener). Pipeline:

```
run_once(symbols)                                  graph_nexus_analysis.py:19349
  [bootstrap] import prior snapshot (cross-run dep) :19956 -> _bootstrap_discoveries_from_snapshot :9776
  fetch articles (cached by date_key)              :19570 -> _fetch_articles_cached :12873
  LLM sentiment classify (TEMPERATURE 1.0)         :20913 -> _enhanced_sentiment_from_llm
  discover from: trend signals / Neo4j propagation / sector-peers / momentum
                                                    :21158/21701/21724/21774 (append to DISCOVERED_TABLE)
  select up to max_discovered_stocks               (per-source, some unsorted at cap)
  snapshot write once/run                          :26008 -> _save_discovery_snapshot
```

### Sources of run-to-run nondeterminism (ranked)
1. **LLM sentiment classification at temperature 1.0** (`:20913`, default temp via `:19414`),
   which seeds trends → discovery. A forced backtest sentiment cache exists keyed on
   `(date_key, article_set_fingerprint, scope_id)` (`:12627`, `:12655`) — **deterministic once
   warm**, but the LLM fires at temp 1.0 on cache miss, and `scope_id` may not be shared across
   runs (to be confirmed in implementation).
2. **Cross-run candidate-pool carryover** — `DISCOVERED_TABLE` persists across runs; the TTL
   prune (`:20006`) only runs live, not in pure backtest (`:20019`); snapshot bootstrap (`:19956`)
   imports a prior scope's basket.
3. **Order-dependent selection at the `max_discovered_stocks` cap** — `_discover_stocks`
   (`:10300`, trend path) and the Benzinga path (`:21010`) iterate dict/`set` insertion order
   with no sort/tiebreak at the cap boundary. (Propagation `:10379` and momentum `:10984`
   already sort with a ticker tiebreak.)
4. Wall-clock `date_key` when `current_time is None` — **live only**; backtest passes a fixed bar time.

No `random()`/`.sample()` exists in the strategy file (confirmed).

### Available ranking signals
- **Propagation `raw_score`** (graph centrality/contagion) — the de-facto rank today
  (`:10371`, persisted in snapshot `:9739`).
- **Momentum 20d/60d returns** (`:10933`) — clean, deterministic, already fully sorted.
- `_finalize_scores` (`:16576`) blends sentiment + propagation + ML — but it ranks the
  *already-selected* universe (it is the buy-ranker, not the discovery selector).
- Trend-buy signal score is **binary** (`=1`, `:11608`) — not usable for ranking.

### Exit / sizing knobs (the leak)
All are **global strategy-schema params** (affect every backtest **and live**):
- Trailing stop arms at `trailing_stop_activation_pct=5.0` (`:16393`), triggers at
  `trailing_stop_pct=8.0` peak-drop (`:16404`); PnL-scaling only widens at ≥25% (`:16431`).
- `fast_loser_cut_pct=-10` + `fast_loser_blacklist_days=20` (`:16337/16383`) → cut + can't re-enter.
- Winner protection needs ≥10% gain (`:16188`); peak/mega protection ≥30/50% (`:16309/16438`).
- `max_positions` = **code default 15**, run used **8** (`:23015`); `allocation_max_new_stock_buys=4`
  (`:7809`); `cash_reserve_floor_pct=0.10` hard (`:8523`).
- `winner_lock` (`:6846`, gate `:7609`) locks any held position ≥2% gain / ≥3 days, blocking
  rotation to fund repeat high-conviction signals (the SNDK starvation cause).
- ⚠️ `live_mode_overrides.py:37` sets `max_positions_breach_auto_rotate=False` for live —
  rotation behaves differently live vs backtest.

## Design

### Phase 1 — Deterministic + rank-based discovery (the feature)

**1a. Purify the LLM classification (the root).**
- Set **temperature 0 + a fixed seed** on the discovery-driving classification calls
  (sentiment/trend). This minimizes cache-miss divergence.
- Ensure the backtest classification cache is **cross-run stable**: the cache key must depend
  only on `(date, article-set fingerprint, prompt/version, canonical model)` — **not** on a
  per-run/instance salt. Confirm what `scope_id` contains; if run-specific, derive a
  run-independent scope for backtest so re-runs hit the same cache entries.
- *Determinism vs "dynamic":* the cache memoizes the LLM's reading of **identical historical
  articles** — a deterministic sub-computation. Discovery still dynamically re-derives the
  basket from those classifications each run. This is **not** a frozen basket.

**1b. Clean-slate, no cross-run carryover.**
- Each backtest run starts from an **empty discovered-state scope** (clear/scope
  `DISCOVERED_TABLE` per run) so discovery re-derives from scratch — deterministic given
  identical inputs, and matching the operator's "rediscover each time" intent.
- **Disable snapshot-bootstrap** (`_bootstrap_discoveries_from_snapshot`) for deterministic
  backtest runs (it is a soft cross-run pin). Snapshot *write* can remain (harmless) or be
  gated off; bootstrap must not import a prior basket.

**1c. Deterministic top-N by composite score.**
- Define a **composite discovery score** per candidate, combining the already-available signals:
  `composite = w_prop * norm(propagation_raw_score) + w_mom * norm(momentum_return) + w_sent * norm(sentiment)`.
  Initial weights favor propagation (the current de-facto rank); weights are config knobs.
- **Pool all candidates across sources** into one list, then select
  `sort(key=(-composite, ticker))[:max_discovered_stocks]`. This replaces the per-source-quota +
  dict/set-order selection at the two unsorted points (`:10300`, `:21010`) and makes selection
  reproducible and rank-principled.
- Preserve per-source provenance for logging/telemetry; only the **final selection** changes to
  global top-N.

**1d. Stable iteration everywhere.** Any `set`/`dict` that feeds ordered output gets a fully
specified sort key with a `ticker` tiebreak.

**Scoping:** 1a–1b are gated to **backtest mode** (live wants adaptive behavior + intended
bootstrap). 1c–1d (better selection) apply in both code paths but their **live** impact is
validated separately before deploy.

**Verification:**
- Unit tests: selection is a pure deterministic top-N (same input → same output, ordering
  invariant to input permutation); composite-score computation is deterministic; cache key is
  run-independent; discovery-driving LLM calls request temp 0 + seed.
- End-to-end: run the **same** backtest **twice** → assert **identical** `tickers[]` and identical
  `pnl_percent`. (Mechanism for running backtests TBD during implementation — local engine vs
  enqueue on prod.)

### Phase 2 — Clear stale `pause_*` on resume (bugfix)

`backtest_critical_abort.py` writes `pause_reason_tag/text/provider/model/call_site/attempts/...`
+ `status='paused_*'`; the broker resume path (`broker.py` ~`:2508-2534`) flips status back to
running but **does not clear the `pause_*` fields**, so a finished run carries misleading pause
metadata. Fix: on resume, null out the `pause_*` fields (and `paused_at`) in the same update that
restores `status`. Unit test the resume path clears them.

### Phase 3 — Exit/sizing knob tuning (data-driven, validated)

**Method (depends on Phase 1 determinism for clean A/Bs):**
1. With deterministic discovery, fix the basket/period and vary **one knob group at a time**.
2. Candidate **safe, structural** knobs (low overfit risk; avoid the hardcoded protection ladder
   and per-ticker constants):
   - `max_positions` 8 → ~12–15 (matches code default; the single biggest starvation lever)
   - `allocation_max_new_stock_buys` 4 → higher
   - `trailing_stop_activation_pct` / `trailing_stop_pct` (let small winners arm later / run further)
   - `cash_reserve_floor_pct` (free deployable cash)
   - `winner_lock` thresholds / rotation aggressiveness (free slots for repeat high-conviction signals)
3. **Validate across multiple date ranges** (not just the #357345 window) + report
   robustness (return, max drawdown, win rate, trade count) before committing values.
4. Consult prior art: `docs/superpowers/specs/2026-05-17-nexus-tier3-missed-rally-fixes-design.md`
   and the bt232179 phase-gamma plan — the early-exit problem has been tuned before; avoid
   re-introducing regressions.

Knob **values are intentionally not fixed in this spec** — they are the output of Phase-3
validation. Phase 3 changes affect **live trading**; final values + any deploy are surfaced to
the operator before merge/deploy.

## Risks & Mitigations

- **LLM determinism is not bit-guaranteed** across model/provider versions (MoE routing, batching).
  *Mitigation:* the cross-run cache makes a given period exactly reproducible once warm;
  temp 0 + seed minimizes cold-run divergence. Residual: a cold first run of a brand-new period
  may differ slightly until the cache is warmed (operator can pre-warm).
- **Live blast radius** — exit/sizing knobs and selection logic are global.
  *Mitigation:* determinism gated to backtest; selection change validated for live separately;
  no deploy without operator sign-off.
- **Composite-score weights** could shift the basket in unintended ways.
  *Mitigation:* default weights reproduce the current propagation-led ranking; tune deliberately.
- **GitNexus MCP not connected** — CLAUDE.md mandates impact analysis via GitNexus tools.
  *Mitigation:* used thorough agent-based file:line touchpoint maps as the substitute (per the
  precedent set last session). Flagged to operator.

## Open Questions

- Exact contents of the sentiment cache `scope_id` (run-independent?) — resolve in implementation.
- How to execute backtests for E2E verification from this environment (local engine vs prod enqueue).
- Whether the deterministic top-N selection should also become the live default (deferred to
  Phase-3 validation).
