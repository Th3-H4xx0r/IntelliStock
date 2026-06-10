# BT136708 Fix Implementation Plan

**Companion design doc:** `docs/superpowers/specs/2026-05-18-bt136708-investigation-design.md` (TBD — this doc embeds the design rationale for now).

**Branch target:** `claude-code-integration` (do NOT merge to `main` without operator approval, per Tier-3 handoff convention).

**Backtest baseline:** BT136708, 2025-11-10 → 2026-05-17, 118-ticker universe, $7K → $18,993 = **+$11,993 P&L / +171.3%**. Prior baseline BT901920 = +247.6%. **Gap: -76pp / -$5,337 on $7K**.

**Strategy version observed:** `V32-PHASE3-PATH12-BLACKLIST-HCFLOOR-CASHGATEDIAG-MCAPPREFILTER200M` (post-Tier-3, post-V32 ML overlay).

---

## 1. Investigation summary (8-agent parallel sweep)

| Agent | Headline finding |
|---|---|
| **SNDK lifecycle** | Bought $239 → cut at $195 by circuit_breaker LOW tier on day 11 (-18.3%). Re-bought $274 via momentum 47d later, rotated OUT at $377 on Jan 12. Missed +619% recovery to $1407. **~$750 P&L missed.** |
| **LITE non-buy** | Blocked 3× by V28.8.1 position-breach gate (10/8). raw_score=1.500 was high enough but partial-trim rotations rejected at cap. Demoted to hold-only watchlist each time. |
| **MU non-buy** | Expired from BFQ at bar 11 of 10. raw_score=0.650 (mid-tier). Same V28.8.1 breach as LITE blocked the BFQ drain. |
| **Discovery throughput** | 35 discovered/day → 184 scored/day → 20.6 pre-sizing → **2.0 executed**. $8 price floor blocks 65% of post-quality (912 buys). MCAPPREFILTER200M is warn-only; the **price floor is the actual hard block**. Confirmed rejected: STRO ($0.81→$39, +4731%), FGL ($0.44→$1.91, +334%), MLEC ($0.65→$8.34, +1146%). |
| **Forced-exit audit** | 7 forced exits, ALL circuit_breaker, ALL conviction tier=LOW. **86% false-positive rate**: SNDK, ROLR, EGO, ORLA, SEDG all recovered after exit; only NOW was a true positive. |
| **Tier-3 telemetry** | A1 fired 18× — **all 18 returned LOW**. A2 regime gating live (bull=10, bear=3, chop=4). A3 **0 hits** (dead code). A4 silent (backtest-mode suppression + no `run_once` callsite). A5 fired 8× (= BT901920's 7, no uplift). Phase 3 telemetry collected in-memory but never logged. B1-B5 mostly silent / static defaults. |
| **Sentiment veto** | 0 sentiment vetoes — sentiment is for scoring only, not blocking. **292 ML overlay BUY_BLOCKs** (V32 GPT-5.4-mini classifier) cost ~$600-$1,000, mostly hit ETFs (AIQ, BOTZ, ROBT, WTAI). |
| **Slot scarcity** | **THE structural finding**: max_positions=8 cap caused V28.8.1 breach for ~95% of run. BFQ stuck 48-60/60 for 175 days. Avg 1.98 buys/day vs 6+ capacity. Top deferred-never-bought: COOK, STRO, MLEC, WDC, STM. |

---

## 2. Root-cause hierarchy

| Rank | Failure | Owner | Magnitude |
|---|---|---|---|
| 1 | **Position-count ceiling (max_positions=8)** blocks BFQ drain → 60-ticker graveyard | Capacity layer | ~$2,000-$3,500 missed P&L |
| 2 | **Tier-3 A1 conviction-tier returns LOW for everyone** → -15% default floor → 86% FP rate on circuit_breaker | A1 + mcap cache | ~$1,000-$1,500 missed P&L |
| 3 | **Re-entry mechanisms (A4 unwired, A5 too late)** → no recovery from false-positive exits | A4 wiring + A5 timing | ~$500-$1,000 missed P&L |
| 4 | **$8 price floor** excludes micro-cap winners | Discovery filter | ~$500-$2,000 missed P&L |
| 5 | **ML overlay over-blocks** ETFs | V32 overlay | ~$600-$1,000 missed P&L |

**Note on P&L estimates:** Numbers are order-of-magnitude. The 8-agent sweep produced one estimate of $210K-$300K which is mathematically impossible on a $7K portfolio — corrected here with realistic position-size assumptions ($300-$500 avg position, capture rate 30-50% for re-allocated capital).

---

## 3. Adversarial review findings

Run after Phase 1 draft. Verdict: **plan is sound for items 1-6 but items 7-10 are speculative**. Specific findings:

- **1 BLOCKER**: A4 wiring (Item 5) requires *adding a callsite* to `run_once`, not just calling existing helpers. Confirmed by grep — `_get_post_sell_watch_candidates` has zero invocations outside tests.
- **3 SERIOUS**: (a) Item 3 (raw_score threshold lower) depends on Item 6 (mcap pre-seed) landing first; (b) Items 7-10 conflict with Items 1-3 — Item 7 (break-glass) duplicates Item 1, Item 8 (separate momentum BFQ) may starve main queue, Item 9 (conviction decay -20%/day) has no spec; (c) Item 10 (auto-rotate at breach) has an unresolved ambiguity about conviction-tier resolution for held positions.
- **MINOR**: Item 2 ($2-$4 vague — pin to $3.50 with tiering), Item 4 (BFQ grace may already be 15 — verify before changing), Item 1 (clarify which cap is enforced).

**Decision**: Defer Items 7-10 to a separate spec. Ship Items 1-6 only. Add 3 missing fixes the reviewer surfaced.

---

## 4. Phased plan

### Phase 1 — Low risk, ship as single PR (~5 days est.)

#### P1.1 — Pre-seed `_yf_market_cap_cache` in backtest harness *(prereq for P1.5)*

**File:** `backend/broker.py` or `backend/backtest_runner.py` (locate via grep for backtest universe load)

**Change:** On backtest universe load, query yfinance / cached metadata for each ticker's market cap once, populate `_yf_market_cap_cache` before the first `run_once` call. Single batch call, no per-bar cost.

**Why first:** Without this, A1 HIGH-tier resolution falls back to raw_score-only, which is the failure mode that drove the 76pp gap. Every other A1-related fix is conditional on this.

**P&L estimate (standalone):** $0 (prereq, no direct effect).
**P&L estimate (unlocked):** Enables ~$1,000-$1,500 from P1.5.

**Test:** Add unit test asserting `_yf_market_cap_cache` is non-empty after backtest universe load.

**Risk:** Low. Only adds data. If yfinance is rate-limited, fall back gracefully.

---

#### P1.2 — Raise `max_positions` from 8 → 12

**File:** `backend/strategies/graph_nexus_analysis.py`

**Locate:** Search for `max_positions` config key + the cap site (logs show V28.8.1 breach checks at lines ~21003). Adversarial reviewer flagged confusion — confirm whether the enforced cap is `max_positions=8`, pool totals (10+4=14), or `max=15` at line 20871.

**Change:** Set default `max_positions = 12` for portfolios under $50K. Add explicit logging on the enforced cap value to remove the ambiguity. Update `_get_effective_nexus_config`.

**P&L estimate:** **+$1,500-$3,500.** Unblocks BFQ drain. Captures 2-3 of: COOK, STRO, MLEC, WDC, STM. Realistic per-name capture: $300-$500 position × 100-1000% = $300-$5,000 each, weighted by 30-50% capture probability.

**Test:** Backtest re-run on same universe + window. Assert avg buys/day ≥ 4.0 (vs current 1.98). Assert BFQ end-state size < 30 (vs current 48-60).

**Risk:** Low. Higher position count means smaller per-position sizing; verify slate planner doesn't over-fragment.

---

#### P1.3 — Lower price floor from $8 → $3.50 with discovery-quality tiering

**File:** `backend/strategies/graph_nexus_analysis.py` or `backend/engines/nexus_graph_engine.py` (locate the price-floor gate that logged "Price floor: blocked N sub-$8 buy(s)")

**Change:**
- Primary discovery (Benzinga/Alpaca direct): floor = $8 (unchanged for blue-chip rotations)
- Propagation-discovered: floor = $3.50 if parent ≥ $1B mcap
- Sub-$3.50: still hard-blocked (avoid pure penny-stock dumpster)

**P&L estimate:** **+$1,000-$2,500.** Captures 1-2 of STRO/FGL/MLEC/COOK. STRO at $0.81 → $39 = 48x means even a $50 position becomes $2,400.

**Test:** Unit test on the tiered gate. Backtest re-run: assert at least 1 sub-$8 buy executed (vs current 0).

**Risk:** Medium. Penny stocks are noisier; pair with Item P1.5 conviction floor to widen exit floors for these names.

---

#### P1.4 — Verify BFQ grace bars match intent (no change if already 15)

**File:** `backend/strategies/graph_nexus_analysis.py`

**Change:** Adversarial reviewer flagged that logs show `bfq=10%/1g/15pg` (15 priority grace already in effect). Audit current values vs the Tier-3 Phase 2a spec (`backfill_queue_grace=7, max_size=50`). If already at 15, no change. If less, raise to 15 for priority candidates.

**P&L estimate:** **+$0-$200.** Marginal — MU expired at bar 11 of 10, so 15 would have saved MU specifically. Other deferred names were blocked by the cap, not grace exhaustion.

**Test:** Read config + log assertion.

**Risk:** Trivial.

---

#### P1.5 — Lower HIGH-tier raw_score threshold + auto-promote mega-caps

**File:** `backend/strategies/graph_nexus_analysis.py` — `_resolve_conviction_tier_at_exit` helper.

**Change:**
- HIGH tier: raw_score ≥ **1.0** (was 1.5) OR mcap ≥ **$30B** (was $50B; SNDK ~$36B would qualify)
- MID tier: raw_score ≥ **0.6** (was 1.0) OR mcap ≥ **$10B**
- LOW: everything else

**Why these numbers:** BT136708's highest raw_score was ~1.3 (below the 1.5 HIGH threshold). The 1.0 threshold captures roughly the top 10% of evals (was 0%). The $30B mcap captures SNDK and large semi-caps that are structurally lower-vol.

**P&L estimate:** **+$400-$800.** Saves SNDK ($750 missed alone, partially mitigated by momentum re-buy) and 1-2 of EGO/ORLA/SEDG. ROLR stays LOW (no path to HIGH), still cut.

**Test:** Unit test: assert SNDK with mcap=$36B resolves to HIGH. Backtest: assert at least 5 HIGH-tier resolutions per run (vs current 0). Assert circuit_breaker FP rate drops below 50%.

**Risk:** Medium. A/B against raw_score=1.2 threshold in a second backtest to check overfit — BT136708 max ~1.3 may not generalize.

**Prereq:** P1.1 must land first or this fires only on the raw_score fork (mcap path stays broken).

---

#### P1.6 — Audit / remove Tier-3 A3 macro-override (dead code)

**File:** `backend/strategies/graph_nexus_analysis.py` — `_macro_event_supersedes_sentiment` + `_MACRO_OVERRIDE_REASON_KEYWORDS`.

**Change:** Adversarial reviewer + sentiment-veto agent both confirmed 0 hits in BT136708. Either:
- (a) Wire it to the actual sentiment-blocking path (if one exists), OR
- (b) Remove it and the 14-keyword constant.

**Decision:** Option (b) — remove. Sentiment-veto agent confirmed sentiment is passive (used for scoring, not blocking). A3 was designed against a veto path that doesn't exist in V32. Removing simplifies and avoids "wiring it later" tech debt.

**P&L estimate:** $0 (cleanup).

**Test:** Delete the dead code, confirm tests still pass, update Tier-3 spec to reflect removal.

**Risk:** Trivial.

---

### Phase 1b — Medium risk, feature-flagged (~3 days est.)

#### P1.7 — Wire A4 `post_sell_watch` re-entry into `run_once` daily loop

**File:** `backend/strategies/graph_nexus_analysis.py` — main `run_once` body.

**Change (per Tier-3 handoff deferred item):**

1. Add a new phase in `run_once` (between sell loop and buy loop): "post_sell_watch re-entry check".
2. Call `_get_post_sell_watch_candidates(conn, instance_id, date_key)` to get rows where status='post_sell_watch'.
3. For each candidate, call `_is_post_sell_reentry_eligible(...)` — checks (cur ≥ exit×1.05 OR 10d resistance break) AND fresh raw_score ≥ 0.40.
4. Eligible candidates get added to the buy slate at **50% of standard position size** (per Tier-3 spec).
5. On execution, call `_mark_discovered_stock_re_entered(...)`.
6. After 60-day window expiry (no re-entry), call `_mark_discovered_stock_forgotten(...)`.

**Feature flag:** `enable_post_sell_watch_reentry` config key, **default OFF**. Adversarial reviewer mandated this — A4 has unit tests but zero real-backtest exercise.

**Mode-safety:** A4 DB writes are already gated on `_GN_LIVE_MODE_FLAG + not historical_lookback_mode`. **CRITICAL: for the A4 backtest exercise, we need a different gate** — either (a) temporarily route writes to an in-memory dict during backtest, or (b) flip the gate to allow writes in backtest with an `_is_temporary_backtest_a4_test=True` strategy_cache key. Option (a) is preferred.

**P&L estimate:** **+$300-$700.** Catches SNDK-pattern: re-entry triggered when SNDK rebounded 5% off the $195 exit (around $205) instead of 47 days later at $274. Similar for ROLR ($2.46→$2.58 re-entry). Limited by which names trigger forced exits.

**Test:** Unit test the new run_once phase. Backtest with flag ON: assert ≥ 3 post_sell_watch → re_entered transitions (per Tier-3 spec §8 V2.3).

**Risk:** Medium-high. First real-backtest exercise of A4. Feature flag mitigates.

---

#### P1.8 — Document A4↔sentiment + A4↔rotation interactions

**File:** `docs/superpowers/specs/2026-05-17-nexus-tier3-missed-rally-fixes-design.md` (amend) and inline code comments.

**Change:** Adversarial reviewer flagged that the SNDK 11/28 re-buy was previously vetoed by sentiment. Document explicitly:
- A4 re-entry: respects sentiment veto? (Current: yes, since A3 whitelist is being removed.) Need to decide whether to add a narrow A4-specific bypass.
- A4 re-entry: respects rotation? (Eligible re-entries should compete for slots in slate planner like any other buy.)
- A4 re-entry: subject to max_positions cap? (Yes, but P1.2 raised it to 12.)

**P&L estimate:** $0 (clarification).

**Risk:** Trivial.

---

### Phase 2 — DEFERRED to separate spec (do NOT bundle with Phase 1)

The following items from the original 10-item plan are deferred per adversarial-review verdict:

- **Original Item 7** — Break-glass max_positions override for HIGH-conviction inbound. *Duplicates P1.2's intent; if P1.2 isn't enough, revisit.*
- **Original Item 8** — Separate 5-slot momentum BFQ with no breach gate. *Risk of starving main queue; design needs an isolation proof.*
- **Original Item 9** — BFQ conviction decay (-20% raw_score/day after age=5d). *No spec, no validation; tunable that needs sensitivity analysis.*
- **Original Item 10** — Auto-rotate at breach: HIGH inbound force-rotates lowest-conviction held. *Unresolved: how is held-position conviction tier resolved at rotation-eval time? Spec ambiguity.*

If Phase 1 backtest closes <50pp of the 76pp gap, write a Phase 2 design spec covering these items with explicit hypotheses + A/B test gates.

---

## 5. Aggregate P&L estimate

| Phase | Items | Realistic P&L lift |
|---|---|---|
| **Phase 1 (P1.1-P1.6)** | mcap prefill + max_pos 12 + price floor $3.50 + grace verify + HIGH-tier loosened + A3 removal | **+$2,900-$7,000** (+41-100pp on $7K base) |
| **Phase 1b (P1.7-P1.8)** | A4 wiring + interactions doc | **+$300-$700** (+4-10pp) |
| **Phase 1 + 1b total** | All shipped together (P1.7 feature-flagged OFF first run, ON second run) | **+$3,200-$7,700 = +46-110pp** |
| **76pp gap to close** | Required to match BT901920 baseline | **+$5,337 = +76pp** |

**Verdict:** Phase 1 mid-point estimate (+~$5,000 / +71pp) brackets the gap. **Likely sufficient.** Upper end (+110pp) would push BT136708 past BT901920's +247.6%, which is plausible if max_positions=12 captures the BFQ graveyard properly.

**Caveats on these estimates:**
- Order-of-magnitude only. Real backtest will have interaction effects.
- Capture rate for deferred winners is uncertain — fixing max_positions doesn't guarantee STRO gets picked; depends on scoring + sequencing.
- BT136708 vs BT901920 have different universes — some of the 76pp may be structural (universe-driven), not fixable.
- ML overlay (V32) wasn't in BT901920 — the 292 BUY_BLOCKs are net-new headwind in BT136708 that no Phase 1 fix addresses. ML overlay tuning is implicitly deferred.

---

## 6. Sequencing + A/B test design

### Single-PR ship (recommended)

Ship P1.1 → P1.2 → P1.3 → P1.4 → P1.5 → P1.6 → P1.7 (flagged OFF) → P1.8 as **one commit chain** on `claude-code-integration`.

### Backtest A/B sequence

1. **Run A (Phase 1 only, A4 flag OFF):** Same universe + window as BT136708. Expected: +30-60pp lift. Confirms P1.1-P1.6 work.
2. **Run B (Phase 1 + A4 flag ON):** Same universe + window. Expected: incremental +5-15pp from A4 re-entries. Confirms A4 wiring is safe.
3. **Run C (Out-of-sample window):** Different 6-month window (e.g., 2025-06-01 → 2025-11-30) to check overfit. Expected: P&L should be directionally similar (Phase 1 should help, not hurt).

### Validation gates (mirroring Tier-3 spec §8 style)

| Gate | Threshold | Source agent |
|---|---|---|
| V1.1 (Run A) | total P&L ≥ +210% (closes ≥45pp of gap) | Phase 1 ship gate |
| V1.2 (Run A) | avg buys/day ≥ 4.0 (currently 1.98) | Slot-scarcity agent |
| V1.3 (Run A) | BFQ end-state size < 30 (currently 48-60) | Slot-scarcity agent |
| V1.4 (Run A) | At least 5 A1 HIGH-tier resolutions (currently 0) | Tier-3 telemetry agent |
| V1.5 (Run A) | Circuit_breaker FP rate < 50% (currently 86%) | Forced-exit agent |
| V1.6 (Run A) | At least 1 sub-$8 buy executed (currently 0) | Discovery agent |
| V2.1 (Run B) | total P&L ≥ Run A + 5pp | A4 increment gate |
| V2.2 (Run B) | ≥ 3 post_sell_watch → re_entered transitions | Tier-3 §8 V2.3 |
| V3.1 (Run C) | total P&L positive AND ≥ baseline OOS P&L | Overfit gate |

**Failure response:**
- If V1.1 fails: profile per-item attribution; consider Phase 2 items.
- If V3.1 fails: revert overfit-risky items (P1.3, P1.5 thresholds); narrow to P1.1, P1.2, P1.7.

---

## 7. Risks + mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Pre-seed mcap cache (P1.1) hits yfinance rate limit on first run | Med | Cache results to disk; reuse on subsequent runs. |
| max_positions=12 over-fragments slate planner | Low | Verify min_position_size_pct still respected; tweak if needed. |
| Lower price floor admits true penny-stock losers (e.g., VCIG -22%) | Med | Pair with P1.5 (HIGH-tier floor widening for surviving names) + monitor FP rate. |
| HIGH-tier threshold 1.0 is overfit to BT136708 | Med | Run C OOS gate catches this. A/B against 1.2 threshold if Run A passes but Run C fails. |
| A4 re-entry conflicts with rotation logic | Med | Feature flag OFF on Run A; turn ON for Run B isolated test. |
| Removing A3 (P1.6) regresses some unobserved live-mode behavior | Low | A3 had 0 fires in BT136708; unlikely active. Document removal in CHANGELOG. |
| Phase 1 lift exceeds expectations, masks ML overlay headwind | Low | ML overlay is separate concern; track 292 BUY_BLOCK count post-Phase-1. |

---

## 8. Out of scope

- **ML overlay tuning** (292 BUY_BLOCKs, ~$600-$1K cost): deferred. Needs its own spec on whether the GPT-5.4-mini classifier is over-blocking ETFs.
- **Sentiment-veto path implementation**: deferred. If we want sentiment to actually block buys (not just score them), that's a new feature, not a fix.
- **Phase 3 telemetry export**: Phase 3's `_nexus_conviction_telemetry` data is collected but never logged or serialized to backtest output. Nice-to-have for post-hoc analysis; not blocking.
- **Universe-divergence analysis** between BT136708 and BT901920: how much of the 76pp gap is universe-specific (structural) vs strategy-specific (fixable)? Could be a follow-up data-only task.
- **Phase 2 architectural items** (auto-rotate, momentum BFQ, conviction decay, break-glass): documented as deferred in §4 Phase 2.

---

## 9. Open questions for operator

1. **Confirm `max_positions` change is acceptable** (8 → 12 for <$50K accounts). Live broker constraints OK with 12 positions on a $7K account?
2. **Price floor decision**: $3.50 with tiering, or pure $3.50 for everything, or different value (e.g., $2.00)?
3. **A4 backtest exercise gate**: Option (a) in-memory writes during backtest, or option (b) `_is_temporary_backtest_a4_test` flag? Operator preference?
4. **Out-of-sample window for Run C**: 2025-06-01 → 2025-11-30 OK, or different range?
5. **Should Phase 2 be designed now in parallel**, or wait for Phase 1 results?

---

## 10. References

- Investigation logs: `.tmp_bt136708/logs_via_api.log` (75,638 lines)
- Baseline comparison: `.tmp_bt901920/logs_via_api.log` (84,241 lines)
- Tier-3 spec (parent context): `docs/superpowers/specs/2026-05-17-nexus-tier3-missed-rally-fixes-design.md`
- Tier-3 plan (parent context): `docs/superpowers/plans/2026-05-17-nexus-tier3-missed-rally-fixes-implementation.md`
- Tier-3 handoff: `.sessions/2026-05-17-220000-tier3-missed-rally-fixes-and-deferred-live-mode-spec.md`
- Strategy file: `backend/strategies/graph_nexus_analysis.py`

---

## 11. Addendum: BT109429 findings + reversed sequencing (2026-05-18)

After commit `6c16c70` was deployed, the operator ran backtest 109429 (8-ticker seed: ADBE/AIQ/ARKQ/BOTZ/JNJ/NKE/UBOT/WTAI, same 2025-11-10 → 2026-05-17 window). It ended at **+51.88%** vs BT136708's +171.3% — a **120pp gap** that initially looked like a regression caused by these fixes. A 7-parallel-agent investigation + adversarial review of the proposed recovery plan produced findings that invalidate the original Phase 1+1b sequencing and replace it with a variance-first approach.

### Findings (1-line each)

- **Most P1 fixes ran but were INERT in BT109429.** P1.1 `_preseed_mcap_cache_from_universe` silently populated 0 tickers (its log was gated on `if populated:`, so the failure was invisible). Without mcap data, P1.5's conviction-tier resolver fell back to the raw_score path; SNDK's exit-time raw_score < 0.6 → still resolves to LOW → still cut at -18.3% (identical pre-fix behavior).
- **P1.3 price floor tiering was the only fix that fired visibly**, but the propagation-discovered names it was meant to admit (STRO $0.81, FGL $0.44, MLEC $0.67) are all sub-$3.50 anyway — they got blocked under the new tier just like before. Zero new admits.
- **P1.2 chop cap 8→12 has no visible Z4.1 log evidence** in BT109429. The capacity agent flagged BT109429 was MORE constrained than BT136708 (mean BFQ 54.3 vs 47.1; queue=60 cap hit 2.3× more often), suggesting either the regime was bull (cap not engaged), or the change didn't take effect.
- **The 120pp gap is variance, not regression.** Variance/robustness agent's decomposition: LLM non-determinism 40-60%, graph-propagation seed sensitivity 25-35%, article volume 10-20%, portfolio mechanics 5-10%. Same-code spread across BT901920 / BT136708 / BT109429: +247.6% / +171.3% / +51.9% = **4.8× spread**.
- **Adversarial review verdict on the original "re-run BT136708 with flag ON to validate" plan: scientifically unsound.** Variance floor (±100pp) exceeds the signal floor (+30-50pp target). A single re-run produces an unreadable result.

### Reversed sequencing: Phase α (variance) → Phase β (validated Phase 1)

The original §6 Phase 1 sequencing is **superseded** by this two-phase approach:

#### Phase α — Variance containment (must complete first)

| # | Item | Description | Variance reduction |
|---|---|---|---|
| α.1 | Mandatory sentiment cache in backtest | Flip `use_sentiment_cache` from optional to mandatory when `historical_lookback_mode=True` or `_nexus_is_live_mode=False`. LLM calls only on cache miss; cache fills deterministically by `(date, ticker, article_source)` key. | 40-60% |
| α.2 | Frozen Neo4j graph state in backtest | Snapshot graph (Companies + relationships) at backtest start_date; propagate against frozen snapshot; no per-bar Neo4j refresh. | 25-35% |
| α.3 | Deterministic RNG seeding | Seed Python's random + any tie-breaking with `hash(backtest_id + start_date + universe_hash)`. | 5% |
| α.4 | P1.1 visibility patch | Replace `if populated: _log(...)` with always-on log `mcap pre-seed: {neo4j_hits}/{n} from neo4j, {yf_hits}/{n} from yfinance, {yf_failures} failures`. Add 3-retry loop with backoff for yfinance failures. | (diagnostic — exposes the silent-fail) |

**Validation gate Φ.1**: run BT136708 **3 times** with Phase α applied. Same seed universe, same window. Target: P&L spread across the 3 runs < 1.5× (i.e., all within ±15pp of median). If variance compresses, Phase β is valid. If not, dig deeper — there's another non-determinism source.

#### Phase β — Validated Phase 1 (conditional on Phase α passing)

Critical change from original Phase 1 sequencing: **per-fix flags, not a grouped flag**. Adversarial reviewer's serious finding: grouping P1.2 + P1.3 + P1.5 under one flag means future regressions can't be attributed back to a single fix.

| # | Item | Config flag | Default |
|---|---|---|---|
| β.1 | P1.1 (mcap pre-seed) | (no flag — bug fix, always on) | ON |
| β.2 | P1.4 (BFQ TTL floor 15) | (no flag — config knob, already operator-tunable) | 15 bars |
| β.3 | P1.5 (conviction-tier thresholds 30B/10B/1.0/0.6) | `enable_conviction_tier_recalibration` | OFF |
| β.4 | P1.2 (max_positions chop 12 / bear 8) | `enable_regime_capacity_scaling` | OFF |
| β.5 | P1.3 (price floor tiering) | `enable_price_floor_tiering` | OFF |
| β.6 | P1.7 (A4 re-entry) | `post_sell_watch_reentry_execution_enabled` (existing) | OFF |

**Validation gate Φ.2** (per-flag): run BT136708 with each flag ON individually (4 runs). Measure isolated P&L lift per flag. Reject any flag whose lift is < 5pp (within noise).

**Validation gate Φ.3** (combined): run BT136708 with all surviving flags ON. Expected: sum-of-individuals ≈ combined lift (interactions small). If combined < sum-of-individuals by > 20pp, there's a destructive interaction → investigate.

**Validation gate Φ.4** (out-of-sample): run BT136708 + Phase α + all flags ON on a second universe (10-15 different seed tickers) to check overfit.

Only after Φ.4 passes, ship as defaults via a new commit.

### What's NOT changing

- **Commit `6c16c70` stays in place.** Rolling it back loses 1,291 LOC + 24 tests + Tier-3 §13 addendum for no proven benefit. The fixes aren't actively harming; they're inert.
- **All current defaults that are pre-fix-equivalent stay.** P1.5 and P1.2 are inert by design until their flag is flipped (Phase β).

### Open issues surfaced but deferred

1. **Audit other gated-log antipatterns** (`if <count>: _log(...)`) across the codebase. P1.1's silent failure mode may exist elsewhere.
2. **Z4.1 regime classification**: did the chop cap actually fire in BT109429? If logs show bull dominated, the cap didn't engage; that's data, not fix inertness. Side-by-side log diff against BT136708 is the next investigation.
3. **Robust live-deployment readiness**: variance agent estimates 6-month live range -20% to +115% with no buffer. Phase α + β alone may not be enough; consider position sizing throttle based on signal-count variance.

### P&L impact estimates (revised)

| Phase | Items | Estimated lift on BT136708 | Variance after |
|---|---|---|---|
| Phase α alone | α.1-α.4 | $0 (no behavior change) | 1.0-1.5× spread (was 4.8×) |
| Phase α + β (P1.1 + P1.5 only) | β.1 + β.3 | +$400-$800 (SNDK + ROLR saved) | 1.0-1.5× |
| Phase α + β (all flags ON) | β.1-β.6 | **+$3,200-$7,700 (+46-110pp)** — same as the original target, now actually validatable | 1.0-1.5× |

### References (BT109429 investigation)

- BT109429 logs: `.tmp_bt109429/logs_via_api.log` (~67K lines at 88% progress)
- Adversarial review of Path A: see this session's task-notification record (agent ID `ac2109fc4c1de2029`)
- Variance agent decomposition: see task-notification record (agent ID `a84ea44de1fe69198`)
