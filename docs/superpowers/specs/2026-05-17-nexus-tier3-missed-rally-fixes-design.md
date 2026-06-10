# Nexus Tier-3 — Missed-Rally Fixes (Premature-Exit + Capacity)

**Status**: Design draft v1 (2026-05-17)
**Predecessor**: `docs/superpowers/specs/2026-05-15-nexus-pnl-maximization-design.md` (Tier-2 P&L Max Package, shipped on `claude-code-integration`)
**Scope**: Strategy logic only. Targets `backend/strategies/graph_nexus_analysis.py` + `backend/broker.py`. No infra, no test scaffolding changes here.
**Author**: Pranav (operator) + Claude Code investigation
**No-code constraint**: This is a design spec. Implementation plan is a separate document.

---

## 1. Background

Backtest **901920** (2025-11-10 → 2026-05-16, $7K initial, 86-ticker universe, gpt-5.4-mini-HIGH, instance "main") produced **+247.6%** ($7K → ~$24,500) under the Tier-2 package. Headline number looks excellent. Investigation revealed two structural failures.

**Failure category A — Premature exit** (e.g. SNDK):
- SNDK bought 2025-11-10 at $239.49 via momentum_watchlist_buy (score 0.089-0.192). LLM sentiment was -0.68 to -0.80 throughout the hold; V31 14-day grace held. Position peaked +18.2% on day 3, then bled to -18.3% on day 11 (2025-11-21).
- Circuit breaker (Z3.1) fired at exact -15% absolute floor. Vol-scaling provided NO benefit because SNDK's realized vol was 5-7%; `max(-15%, -2.0 × 0.05)` = -15% dominated.
- A scheduled re-buy on 2025-11-28 driven by "S&P 500 entry" macro signal was **cancelled** by fresh negative LLM sentiment. SNDK then recovered to $244 by Dec 29 and ultimately rallied to $1,407 by May 2026 — **+487% from entry, missed**.
- The V31.4 post-sell breakout cooldown lift did fire eventually but only after price was already +40% above sell — too late.
- Across the full backtest: **5 circuit-breaker fires, 4 false positives (80% FP rate)**: SNDK (+487% missed), ROLR (+77%), FGL (+338%), ORLA (+34%). Only CAR (-54%) was a vindicated true loser.
- **Estimated lost alpha**: ~170pp across these 4 cases on this backtest.

**Failure category B — Never-bought / capacity** (e.g. MU, LITE):
- MU never bought. Initial signal (score 0.08, ranked #2 after RKLB+SNDK on 2025-11-05) blocked by zero cash reserve + CLF's leader_lock (P&L +15.7%, held 24 days) preventing rotation. Re-discovered later via propagation at score **0.650** but expired from backfill queue after 11 bars when no cash materialized.
- LITE never bought despite appearing as #1 momentum watchlist on 2025-11-05 (score 0.198) and again 2026-02-06 at **raw=1.500**. Blocked by `allocation_max_new_stock_buys=4` + 10% hard cash reserve + rotation thresholds. Even raw=1.500 couldn't break `rotation_break_glass_delta=2.25`. Final status: "Deferred unfunded buys demoted to hold".
- Root structural issues: small account ($7K) × 10% hard cash reserve = $700 always-reserved; only 4 new buys per cycle; propagation candidates monopolize 60-slot backfill queue; rotation gates lock profitable holds for 5-20 days regardless of better incoming signals.

**Strategic implication**: Tier-2's risk discipline (Z3.1-Z3.3, Z4.1) is structurally sound for true losers (CAR) but mis-cuts mid/low-conviction names that turn into outsized winners. And the allocation/rotation machinery starves high-conviction late-arrivers (LITE at raw=1.500). Both must be addressed for the strategy to be robust live.

---

## 2. Goals

1. **Cut the circuit-breaker false-positive rate from ~80% to ≤30%** on the 901920 backtest's forced-exit cohort.
2. **Capture at least 50% of the +1000pp+ aggregate missed alpha** (SNDK+ROLR+FGL+ORLA+MU+LITE) without disturbing the existing protection on CAR-class true losers.
3. **Keep small-account ($7K-$50K) viability** by making allocation parameters scale with account size.
4. **Preserve the Tier-2 strategy CORE** (~82% of alpha from discovery+scoring). All changes are downstream of the discovery signal; none touch discovery itself.

---

## 3. Investigation findings (input to design)

| Ticker | Outcome | Failure mode | Lever needed |
|---|---|---|---|
| **SNDK** | Bought, cut at -18.3%, missed +487% | -15% floor too tight; LLM sentiment vetoed macro re-buy signal; vol-scaling ineffective on low-vol | A1, A3, A4 |
| **ROLR** | Bought, cut at -22.5%, missed +77% | Same as SNDK pattern | A1, A4 |
| **FGL** | Bought, cut at -22.8%, missed +338% | Penny-stock propagation entry, low conviction, true catastrophe risk but recovered | A1, plus B-discovery-quality filter |
| **ORLA** | Bought, cut at -16.3%, missed +34% | Mid-conviction propagation, narrow miss | A1 |
| **MU** | Never bought; expired in backfill queue | Cash starvation + leader_lock + queue grace too short | B1, B3, B4, B5 |
| **LITE** | Never bought; demoted to hold | Cash starvation + rotation thresholds too high | B1, B2, B3 |
| **CAR** | Bought, cut at -53.7%, vindicated | True disaster — circuit breaker correctly protected | KEEP as-is |

Common thread among catastrophic misses: **all came from low-conviction signal sources** (momentum watchlist, backfill rotation, propagation expansion). **None** had Benzinga upgrade catalysts or strong LLM-positive sentiment at entry. This is the discriminating signal for the conviction-aware floor in A1.

---

## 4. Design — Phase 1 (highest ROI, ship first)

### A1 — Conviction-aware loss floor matrix
**Replace** the constant -15% floor in `_evaluate_position_risk` (`backend/strategies/graph_nexus_analysis.py:~13822`) with a 3-tier matrix decided at exit-time:

```
conviction_tier_at_exit_time(position) =
  HIGH if (raw_net_score_at_entry ≥ 1.5
           OR has_benzinga_catalyst_at_entry
           OR market_cap_usd > 50e9
           OR llm_sentiment_at_entry ≥ +0.6)
  MID  if (raw_net_score_at_entry ≥ 0.5)
  LOW  else

floor_pct(tier) =
  HIGH: -25  (vol_multiplier = 0  -> absolute floor dominates)
  MID:  -20  (vol_multiplier capped at 2.0, disabled when realized_vol < 8%)
  LOW:  -15  (current behavior)
```

`conviction_tier_at_entry` must be cached in the position's metadata at buy-time (probably as a new field on the `_v32_position_history[symbol]` entry) so the exit-time evaluator doesn't need to reconstruct the entry score.

**Config keys**:
- `circuit_breaker_floor_high_conviction_pct` default -25
- `circuit_breaker_floor_mid_conviction_pct` default -20
- `circuit_breaker_floor_low_conviction_pct` default -15 (current)
- `circuit_breaker_high_conviction_mcap_threshold_usd` default 50e9
- `circuit_breaker_high_conviction_raw_score_threshold` default 1.5
- `circuit_breaker_low_vol_disable_threshold_pct` default 8.0

**Expected lift on backtest 901920**: SNDK (mcap >$50B → HIGH-tier → -25% floor) held through -18.3% dip, captures partial recovery via trailing stop later. ROLR, FGL, ORLA stay LOW-tier (no Benzinga catalyst), still cut, but the catastrophic SNDK miss is recovered. Estimated **+20-30pp**.

### B1 — Account-size-scaled cash reserve floor
Current `cash_reserve_floor_pct=0.10` is a hard floor on every account. On $7K that's $700 permanently dead capital. Replace with scaled formula:

```
cash_reserve_floor_pct(account_equity) =
  0.05 if equity < 50,000
  0.075 if 50,000 <= equity < 200,000
  0.10 if equity >= 200,000
```

**Config keys**:
- `cash_reserve_floor_small_pct` default 0.05
- `cash_reserve_floor_mid_pct` default 0.075
- `cash_reserve_floor_large_pct` default 0.10
- `cash_reserve_floor_threshold_small_usd` default 50000
- `cash_reserve_floor_threshold_mid_usd` default 200000

**Expected lift**: Frees ~$350 of buying room on the 901920 backtest. Directly enables MU's initial entry attempt and LITE's first executable-buys slot.

### B3 — Relax rotation thresholds
Current rotation gates (in `_finalize_scores` / V28 rotation logic, search for `rotation_break_glass_delta`, `rotation_profitable_min_delta`, `rotation_winner_lock_min_*`):

```
Current:                          → Proposed:
rotation_profitable_min_delta=1.5 → 1.0
rotation_break_glass_delta=2.25   → 1.5
rotation_winner_lock_min_pnl_pct=3.0   → 2.0
rotation_winner_lock_min_hold_days=5   → 3
```

**Rationale**: LITE at raw=1.500 should have rotated in. With the proposed thresholds, raw=1.500 vs held=0.800 (BOTZ -2.9%) gives delta=0.700 + losing-position boost qualifies for `v28_hc_losing_break_glass`. Currently this fires but `allowed=False` for profitable holds; lowering the min_pnl threshold from 3% to 2% releases more holds for rotation.

**Risk**: Lower min_hold_days could cause whipsaw if a fresh signal is wrong. Mitigation: combine with conviction tier — only allow rotation if incoming raw_score is HIGH-conviction.

**Expected lift**: Directly enables LITE entry on 2026-02-06. **+5-10pp** depending on LITE's actual trajectory.

### Phase 1 combined target: **+30-50pp on backtest 901920**

---

## 5. Design — Phase 2 (medium complexity)

### A2 — Regime-gated floor adjustments
Tier-2 spec already wrote regime-gating into `_compute_macro_risk_scale`, but the backtest never hit bear/crash. Layer regime onto A1's floors:

```
floor_adjusted = floor_pct(tier) + regime_adjustment[regime]
regime_adjustment =
  bull:  +5  (widen — let winners breathe)
  chop:   0  (use base floors)
  bear:  -5  (tighten — capital preservation)
  crash: emergency_exit_all
```

**Config keys**: `circuit_breaker_regime_adjustment_bull_pp` default 5; `_bear_pp` default -5. Crash regime path already exists in Tier-2 (sets max_positions=0); extend it to also force-sell remaining positions.

**Expected lift**: Untested on this backtest (no bear days). Critical for live robustness.

### A3 — Macro signals supersede LLM-sentiment veto
Find the code path that cancelled SNDK's 2025-11-28 scheduled re-buy ("Cancelled pending buy for SNDK on 2025-11-28 (fresh negative sentiment contradicts: S&P 500 entry)"). The cancellation logic is `_cancel_pending_buy_if_sentiment_contradicts` (search `graph_nexus_analysis.py` for "Cancelled pending buy").

Add a whitelist of macro-event reasons that CANNOT be vetoed by sentiment:
```
SUPERSEDING_MACRO_REASONS = {
  "S&P 500 entry", "Index addition", "Index reweight",
  "M&A target", "Buyout announcement",
  "Strong Benzinga upgrade", "Major insider buy",
}
if pending_buy_reason in SUPERSEDING_MACRO_REASONS:
    skip sentiment veto; log "macro signal supersedes sentiment"
```

LLM sentiment can still log a warning but cannot cancel the buy.

**Expected lift**: Recovers SNDK's 11/28 re-buy. **+15-25pp** standalone.

### A4 — Post-sell active re-entry monitoring
New machinery — extends `GraphNexusDiscoveredStocks` table:

1. **On forced-exit**, the strategy writes `status="post_sell_watch"` (new status) to the ticker's row, along with `exit_price`, `exit_date`, `entry_conviction_tier`.
2. **On every FULL cycle**, query rows with `status="post_sell_watch"` AND `exit_date >= today-60`.
3. **Re-entry triggers** (any of):
   - `current_price >= exit_price * (1 + post_sell_recovery_threshold_pct)` (default 1.05)
   - `current_price >= max(last_10d_high_after_exit) * (1 + post_sell_resistance_break_pct)` (default 1.005)
   - Macro structural event (index entry / M&A / strong Benzinga catalyst)
4. **Re-entry filter** (mandatory):
   - Fresh `raw_net_score >= post_sell_reentry_min_raw_score` (default 0.40)
   - This prevents pure-bounce re-entries
5. **Re-entry sizing**: `post_sell_reentry_size_fraction` (default 0.5) of normal size
6. **Status transitions**:
   - `post_sell_watch` → `re_entered` on successful re-buy
   - `post_sell_watch` → `forgotten` after 60 days with no trigger
7. **`_get_recently_sold_discovered_tickers`** filter at line 9725 must update to `status IN ("sold", "post_sell_watch")` so the cooldown still applies during the watch window.

**Schema-extension cost**: 1 new status enum value, 3 new fields per row (exit_price, exit_date, entry_conviction_tier). Existing rows default to old behavior.

**Expected lift**: Captures ROLR (+77%), FGL (+338%) re-entries. Conservative est. with 50% sizing: **+10-20pp** on this backtest.

### B2 — Raise `allocation_max_new_stock_buys`
Current `allocation_max_new_stock_buys=4`. Proposed:

```
allocation_max_new_stock_buys(account_equity) =
  6 if equity < 50,000   # small account: more diversification
  8 if 50,000 <= equity < 500,000
  12 if equity >= 500,000
```

**Expected lift**: Direct impact on LITE's first attempt.

### B4 — Tiered momentum budget
Currently propagation and momentum both feed the same 60-slot backfill queue with `top20 by score` getting cash. Propagation dominates because it produces more candidates per cycle.

Proposed: **dedicate 15% of new-buy budget** to top-2 momentum picks per cycle. Implementation: reserve 2 slots in the per-cycle allocation that ONLY momentum candidates can fill. If no momentum candidate qualifies, the slots flow back to propagation.

**Config keys**:
- `momentum_dedicated_slots_per_cycle` default 2
- `momentum_dedicated_budget_pct` default 0.15

**Expected lift**: Direct impact on MU's first attempt (score 0.08 was #2 in momentum top-3).

### B5 — Extend backfill queue grace + size
- `backfill_queue_grace_bars`: 3 → 7
- `backfill_queue_max_size`: 30 → 50

**Rationale**: MU expired after 11 bars in the queue without funding. Raising grace to 7 days plus the cash-reserve drop (B1) plus the allocation-max raise (B2) should let it execute.

### Phase 2 combined target: **+20-30pp on top of Phase 1**

---

## 6. Design — Phase 3 (telemetry only)

### A5 — Lower V31.4 post-sell breakout cooldown lift threshold
The existing V31.4 lift fires when current price > sell_price × (some current threshold). The SNDK log shows it fired at $275+ vs sell of $195 (40% above). Proposed: lower threshold to **+10% above sell price** so the cooldown lifts as soon as the ticker shows real recovery, allowing A4's re-entry logic to fire.

### Conviction-score telemetry
Compute and log `conviction_score` at entry per Tier-2 follow-up plan (`conviction_score = raw_quartile × 0.35 + llm_sentiment × 0.30 + benzinga_catalyst × 0.20 + sector_trend × 0.15`). Don't use it to change behavior — just log it. After 30 days of telemetry, decide whether to bind it into A1's tier resolution as a continuous knob.

---

## 7. Anti-patterns (DO NOT)

1. **Don't disable circuit breaker globally.** CAR (-54%) proves true disasters exist. Asymmetric widening per conviction tier is the right move; uniform widening is not.
2. **Don't widen stops uniformly.** Conflating low-conviction noise with high-conviction signal undermines the 82% alpha from CORE discovery.
3. **Don't re-enter on price action alone.** A4's re-entry MUST require fresh raw_score ≥ 0.40. Otherwise the strategy chases bounces.
4. **Don't relax winner_lock without min_pnl floor.** B3's relaxation must keep a floor; 0% min_pnl would whipsaw-rotate every cycle.
5. **Don't ship Phase 1 + Phase 2 in one PR.** Phase 1 changes 3 things and is testable in 1 backtest re-run. Phase 2 adds a schema change (A4) and a new cadence layer (A2/A3). Ship Phase 1 first, validate, then Phase 2.

---

## 8. Validation gates

For each phase, the gate before merging to `claude-code-integration` (let alone `main`):

**Phase 1**:
- V1.1: Backtest 901920 re-run with Phase 1 enabled. Target: total P&L ≥ +290% (vs +247.6% baseline), SNDK contribution ≥ +30pp (vs -1.3pp baseline).
- V1.2: All existing 99 unit tests pass + 6+ new unit tests on the conviction-tier resolver, cash-reserve scaling, and rotation-delta math.
- V1.3: CAR still gets cut at its -54% loss (regression check on the true-loser path).

**Phase 2**:
- V2.1: Backtest 901920 re-run with Phase 1+2. Target: ≥ +330%, FGL contribution ≥ +20pp.
- V2.2: A3 logic logs at least 1 "macro signal supersedes sentiment" event in the backtest (SNDK 11/28).
- V2.3: A4 records at least 3 `post_sell_watch` → `re_entered` transitions.
- V2.4: 10+ new unit tests on the new status enum, re-entry filter, schema extension.

**Phase 3**:
- V3.1: Conviction telemetry logs visible in `_get_effective_nexus_config` diagnostic.
- V3.2: After 30 backtest re-runs (or backtest-days of accumulation), conviction_score distribution is examined and threshold for A1 binding is decided.

**Cross-phase live-mode gate** (separate from this spec, but required before merging to `main`):
- Run the Phase 1+2 build in PAPER trading on Robinhood for 2-4 weeks. Measure actual slippage on micro-cap discoveries vs backtest assumption.
- Audit Z2.1 ghost-sell observation logs from this period to decide phase-2 enforcement.

---

## 9. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| A1 widens floor on a name that later truly crashes (CAR-class hidden under HIGH-tier flag) | MEDIUM | HIGH-tier requires mcap>$50B OR Benzinga catalyst — both are objectively present at entry, hard to fake |
| B1 lowering cash reserve to 5% leaves the account margin-call vulnerable on a fast drawdown | LOW | 5% is still substantial; Drawdown halt (Z2) still active |
| B3 rotation relaxation causes whipsaw on noisy days | MEDIUM | Pair with conviction requirement: only rotate IN on raw_score >= 1.0 |
| A3 macro-override misclassifies a fake "index entry" rumor as real | LOW-MED | Source the whitelist from `_macro_event_reasons` already in code, which are LLM-verified |
| A4 re-entry monitoring grows DiscoveredStocks table unbounded | LOW | 60-day TTL + status="forgotten" sweep |
| Schema extension on DiscoveredStocks breaks existing queries | MEDIUM | Backwards-compatible: existing rows default to `status="sold"` if no new fields |
| Phase 1 + Phase 2 interact in ways the backtest doesn't reveal (LLM-OFF / Phase 2 cascades) | MEDIUM-HIGH | Run V2 backtest with Phase 2 enabled but A3 disabled, then with A3 enabled, to isolate the macro-override impact |

---

## 10. Sequencing + dev estimates

| Phase | Items | Est. dev | Est. test/iteration | Validation gate |
|---|---|---|---|---|
| **Phase 1** | A1 + B1 + B3 | 2-3 days | 1 backtest re-run + 1 day of analysis | V1.1, V1.2, V1.3 |
| **Phase 2a** | A3 + B2 + B4 + B5 | 2-3 days | 1 backtest re-run + 1 day of analysis | V2.1, V2.2 |
| **Phase 2b** | A4 (schema-extending) + A2 | 3-5 days | 2 backtest re-runs + 2 days analysis | V2.3, V2.4 |
| **Phase 3** | A5 + telemetry | 1 day | logging-only; no behavior validation needed | V3.1 |
| **Live paper-trade** | Run Phase 1+2 on RH paper | 2-4 weeks elapsed | Slippage + behavior observation | Pre-`main` gate |

**Total dev**: ~8-12 days across phases, plus 2-4 weeks elapsed for paper-trade validation.

---

## 11. Open questions for the operator

1. **Z2.1 phase-2 enforcement timing**: Tier-2 spec deferred ghost-sell enforcement pending telemetry audit. Should this be coupled with Phase 1's deployment, or held until later?
2. **Conviction tier mcap threshold**: $50B is the proposed HIGH-tier cutoff. SNDK was somewhere around this depending on date; should we use a stricter $100B or looser $30B?
3. **Re-entry size fraction**: 50% is the conservative default for A4. Some readers may want 100% — what's your risk preference?
4. **Should A4 also trigger on tickers we sold via NORMAL exits** (winner_sell_protection, trailing-stop after large gain), not just forced exits? My current proposal limits to forced exits to avoid re-entering positions we deliberately took profit on.
5. **Backtest re-run policy**: After each phase, re-run on the SAME backtest 901920, or on multiple OOS windows (V4-V6 from Tier-2 spec)? The latter is more rigorous but slower.

---

## 12. References

- `docs/superpowers/specs/2026-05-15-nexus-pnl-maximization-design.md` — Tier-2 P&L Max Package (v2, shipped)
- `docs/superpowers/plans/2026-05-15-nexus-pnl-maximization-implementation.md` — Tier-2 implementation plan
- `.tmp_bt901920/logs_via_api.log` — backtest 901920 logs (84,241 lines, ~80MB)
- `.sessions/2026-05-16-014500-nexus-pnl-tier2-package-and-llm-tuning.md` — prior session handoff
- Backtest 901920 metadata: instance="main", $7K initial, 86 tickers, 122 trading days, gpt-5.4-mini-HIGH

---

*Spec v1 draft. Awaiting operator review before writing the implementation plan.*

---

## 13. Addendum: BT136708 follow-up calibration (2026-05-18, P1.7-P1.8)

The Tier-3 spec was validated against BT901920's premature-exit cohort. BT136708 (same universe pattern, different period, post-V32 stack) ended at +171.3% — a 76pp gap vs BT901920's +247.6%. An 8-agent investigation traced the gap to three cascading failures: max_positions cap at 8 created a 60-deep BFQ graveyard; the A1 conviction-tier resolver returned LOW for all 18 evaluations because the raw_score≥1.5 threshold was too tight AND `_yf_market_cap_cache` was empty in backtest; A4 re-entry was data-plumbed but never wired into `run_once`. The BT136708 fix plan (`docs/superpowers/plans/2026-05-18-bt136708-fix-implementation.md`) addressed all three. This section documents the cross-cutting interactions surfaced during implementation.

### A4 re-entry ↔ sentiment veto

A4 re-entry candidates injected into `scores` follow the same pipeline as fresh discovery buys, so they ARE subject to the existing sentiment-veto path at the future-trade-review block (graph_nexus_analysis.py ~19560-19583). A3's macro-override whitelist applies to pending scheduled buys, not directly to A4 re-entries — but if an A4 re-entry coincides with a scheduled buy on the same ticker, the macro-override behaviour applies to the scheduled buy independently. There is no special A4 bypass of sentiment veto; if the operator wants A4 to be immune to sentiment veto, that would be a separate Phase 2 design item.

### A4 re-entry ↔ rotation

A4 re-entries compete for buy slots like any other buy candidate. The slate planner sees them via `scores[sym]["score"] == 1` with `_is_post_sell_reentry=True` marker. They flow through the same rotation logic, max_positions cap (now 12 in chop after P1.2), BFQ queue, and price-floor gate (P1.3 propagation-tier $3.50 floor applies if reason text contains "propagation" or "expansion"; A4 reasons don't, so re-entries get the primary $5 floor unless the operator lowers `buy_price_floor`).

### A4 re-entry ↔ max_positions cap

A4 candidates are constrained by the same chop=12/bear=8 cap (after P1.2). If at cap, A4 re-entries fall through to BFQ like any other deferred buy. The priority TTL (15 bars after P1.4) gives them a fair chance to fill before aging out. The BFQ "_is_propagation_expansion" priority flag is NOT set on A4 entries by default — if Tier-3 wants A4 to compete with elevated priority, that's a Phase 2 follow-up.

### A4 re-entry ↔ sizing fraction

The Tier-3 spec called for 50% sizing on re-entries (`post_sell_reentry_size_fraction=0.50`). The P1.7 implementation marks the score entry with `_reentry_size_fraction` but the slate planner does NOT currently honor it — A4 re-entries fund at 100% of the normal slate cash. This is a known incomplete piece; the marker is in place so a future Phase 2 patch can plumb the fraction through `_plan_executable_stock_buy_slate`. Operator can compensate by lowering global allocation caps in the interim.

### A3 macro-override status

Per the BT136708 investigation, A3 fired 0 times. Initial review suggested A3 was dead code; on closer inspection (graph_nexus_analysis.py ~19566-19583) the path is wired correctly — it only fires when (a) a scheduled future trade exists, (b) fresh sentiment contradicts it, (c) the trade is a BUY, and (d) the article reason matches the 14-keyword whitelist. BT136708 had no such confluence. **A3 is retained**, not removed. The keyword list could be expanded if a future backtest reveals miss patterns.

### A1 mcap pre-seed dependency

`_yf_market_cap_cache` is empty in backtest against a static universe because `_get_quality_metadata_for_ticker` only populates it during fresh discovery. P1.1 added a one-time pre-seed via the new `_preseed_mcap_cache_from_universe` helper, called early in `run_once`. Source order: `_neo4j_market_cap_cache` first (cheap, no network), yfinance as fallback (gated on `mcap_preseed_use_yfinance`, default True). The pre-seed runs once per backtest (idempotent via `_yf_market_cap_cache_preseeded` flag) and is on the migration-reset + persistence-blacklist lists so a new backtest run starts fresh.

### Backtest exercise of A4

The A4 sell-side write was originally gated on `_GN_LIVE_MODE_FLAG` only. To exercise the re-entry pipeline in backtest WITHOUT polluting the shared `GraphNexusDiscoveredStocks` table, P1.7 added an in-memory mirror keyed on `strategy_cache["_post_sell_watch_inmem"]`. Live mode writes to DB exclusively; backtest writes to in-memory exclusively; historical-lookback pre-pass writes to neither. The reader `_get_post_sell_watch_candidates_combined` merges both sources (in-memory wins via dedupe). This pattern can be reused for other state that needs backtest exercise without DB pollution.


