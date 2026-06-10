# BT294837 Phase ε — Execution-throughput unlock (post 12-agent + adversarial review)

**Branch target:** `claude-code-integration` (do NOT merge to `main`).

**Continues from:**
- `docs/superpowers/plans/2026-05-18-bt232179-phase-gamma-fix-plan.md` (Phase γ — A1 silent-inertness fix; SHIPPED in commits `365fde2` + `697c6aa`)
- 12-agent parallel investigation across BT294837 (post-γ+δ) + BT136708 (+171% baseline) + BT901920 (+206% ceiling)
- Adversarial review of the original 9-component Phase ε draft

**Investigation session date:** 2026-05-19

---

## 1. Headline reframing — what the data actually shows

| Claim before this investigation | Claim after this investigation |
|---|---|
| BT901920 hit +247% | **BT901920 actual broker-reported P&L = +206.02%.** The +247 figure was the per-stock-summary sum, not the cash-aware total. |
| The +247% recipe is reproducible with the right fixes | **+206% is ~80% luck**: top 2 stocks (XNDU +285%, NRGD +344%) = 81% of P&L. Without either, total collapses to ~+39%. |
| BT901920 captured SNDK/LITE/MU successfully | **NONE of SNDK/LITE/MU were captured in BT901920**: SNDK lost -$87, LITE never bought, MU never bought. The operator's must-buy premise is unmet across every historical run. |
| The +227pp gap from BT294837 to BT901920 is universe-bandwidth | **REFUTED by data**: BT294837 has 152 scored symbols/bar vs BT901920 127. BT294837's top candidates (BAC 2.10, MS/GS/JPM 1.90, PLTR 1.87) outscore BT901920's (GLW 1.5, BKNG 1.5, SRPT 1.8). The 8-ticker seed reaches 421 Neo4j graph nodes — wider than BT901920's 330. |
| The bottleneck is discovery / propagation | **The bottleneck is execution-throughput**: BT294837 = 0.7 buys/bar; BT901920 = 3.3 buys/bar. 92.2% of allowed rotations get SKIPPED at V28.8.1 cap downstream. |
| Phase γ's mcap pre-seed (γ.1) is silently inert | **γ.1 + γ.5 work**: 64.7% HIGH / 6.6% MID / 28.7% LOW resolutions in BT294837 (vs 0/0/100% in BT232179). SNDK correctly resolves as HIGH (mcap=84502M). The silent-inertness regression is FIXED. |
| The sell-side floor uses the resolved conviction tier | **Possibly NOT — under investigation (ε.B.1)**: SNDK was tagged HIGH at buy-time but circuit_breaker fired at `floor=-15.0% (default=-15%)` — the LOW default. Either the tier resolver isn't called at sell-time, OR mcap was empty at sell-time, OR the floor mapping doesn't honor HIGH. |

---

## 2. Root-cause hierarchy (per 12-agent findings)

| Rank | Failure | Owner | Magnitude estimate (uplift if fixed) |
|---|---|---|---|
| **R0** | V28.8.1 SKIP-at-cap kills 92% of allowed rotations downstream | rotation pair eval / cap gate | +10-20pp (the dominant throughput limiter) |
| **R0** | `profitable_min_hold` blocks 52% of rotation rejections (4,444 events in BT294837) — 20-day floor on winning holds | `_rotation_candidate_allowed` | +5-15pp |
| **R0** | `min_hold` blocks 26% of rotation rejections — 10-day floor on losing/marginal | `_rotation_candidate_allowed` | +5-10pp |
| **R1** | Sell_enforcement BUY→SELL flip from stale negative-sentiment propagation. BT901920 missed SNDK rebuy ($274→$1407 = -$87 instead of +$420) precisely because `Nexus sell enforcement` list contained SNDK from stale `top raw: SNDK=-1.000(12p)` | `_evaluate_trend_sell_enforcement` | +3-8pp (per missed-rebuy event) |
| **R1** | SNDK rebuy fully blocked across 25,000+ log lines after force-sell (specific gate TBD by ε.B.5) | TBD by investigation | +3-8pp (generalized to any post-sell rebuy attempt) |
| **R1** | Cap-leakage past max_positions: BT294837 hits current=14 vs max=8 (BT901920 never exceeded 10) | TBD by ε.B.2 (winner_add? momentum_amp? BFQ direct-reserve?) | depends on source |
| **R2** | Sell-side tier disconnect (SNDK HIGH cut at LOW -15% default floor) | TBD by ε.B.1 | +1-3pp |
| **R2** | ML SELL_BLOCK fires for phantom positions (position=0) | `_apply_ml_overlay_to_scores` | $0 (observability) — but may have downstream side-effects |
| **R2** | γ.1 BFQ source contributes 0 tickers (`universe_sources=symbols:0/held:N/bfq:0`) | `_preseed_mcap_cache_from_universe` BFQ read | minor |
| **R3** | Trend signals not threaded into raw_score (MU at trend_strength=0.92 stays at raw=0.650) | trend → scoring pipeline | architectural (Phase ζ) |
| **R3** | Configuration drift since BT901920 (Tier-2/Tier-3 features defaulting ON) | TBD by ε.B.4 | depends on which knobs |

---

## 3. Phase ε — two-stage rollout

Adversarial review demanded sequencing: ship Tier A + run Tier B investigations in Stage 1, then ship Tier C based on B findings. **Tiers cannot ship together** — Tier C changes are structural and depend on B confirmation. Shipping blindly re-creates the silent-inertness pattern that bit Phase α/γ.

### **STAGE 1 — Tier A code + Tier B investigations (THIS DOC)**

#### Tier A — Quick wins, low risk (ship today)

**ε.A.1 — Configuration regression audit (deliverable from ε.B.4)**
- Pure investigation, no code
- Side-by-side `_get_effective_nexus_config()` diff between BT294837 and BT901920
- Operator decides which knobs to revert
- Output: a config-diff markdown file

**ε.A.2 — ML SELL_BLOCK position-existence gate**
- File: `backend/strategies/graph_nexus_analysis.py` (around the `ML overlay SELL_BLOCK` log emission)
- Add `if portfolio_emulator._positions.get(sym, 0) <= 0: skip` before the SELL_BLOCK
- Scope: ~5 LOC
- Risk: zero (pure suppression of phantom log spam, no behavior change for real positions)
- Telemetry: counter of suppressed events per run

**ε.A.3 — Sell_enforcement trend-reversal BUY→SELL flip gate**
- File: `backend/strategies/graph_nexus_analysis.py` (only the trend-reversal path inside `_evaluate_trend_sell_enforcement`, NOT the 7 other `nexus_sell_enforcement.add()` sites which use it as a block-list)
- Add `if float(portfolio_emulator._positions.get(ticker, 0)) <= 0: skip` before the auto-add
- Scope: ~10 LOC + tests
- Risk: low (single-path gate; explicitly excludes block-list callers)
- Telemetry: emit `[sell-flip-guard] {ticker} skipped — pos=0 (no holding to sell)` once per skip
- **Generalization**: works for any stock with stale negative-sentiment propagation, not just SNDK

#### Tier B — Read-only investigations (run in parallel with Tier A)

**ε.B.1 — γ.1 mcap pre-seed sufficiency at SNDK exit bar**
- Decide whether ε.C.0 (sell-side tier wiring) is needed
- If γ.1 had populated SNDK's mcap at the exit bar AND the tier resolver returned HIGH, then the sell gate IS reading the wrong source → ε.C.0 ships
- If γ.1 had NOT populated SNDK at the exit bar, then ε.C.0 (BFQ source fix in γ.1) ships instead

**ε.B.2 — Cap-leakage source identification**
- Identifies which path produces current=14 overflows
- Decides whether ε.C.4 (hard cap upper bound) is needed
- If winner_add is the source AND BT901920 used winner_add successfully, "leakage" is intentional — no fix

**ε.B.3 — V32 convert-vs-skip ratio audit**
- Quantifies how many of the 190 SKIPped rotations would have CONVERTed at min_loss=-4.0 / -6.0 / -8.0
- Decides whether ε.C.1 (v32_convert_min_loss widening) is high-leverage

**ε.B.4 — Config diff BT294837 vs BT901920**
- Drives ε.A.1 deliverable above

**ε.B.5 — SNDK rebuy gate audit**
- Identifies the dominant gate blocking SNDK rebuy across 25,000+ log lines after force-sell
- Candidates: `_evict_cooldown`, `_sold_cooldown`, `discovery_cooldown`, persistent negative propagation (`SNDK=-1.000`), ML SELL_BLOCK side-effects, rotation eval blockers
- **Generalization**: which OTHER tickers in BT294837 would also benefit from the same fix?
- Drives the design of ε.C.5 (rebuy-gate relaxation for post-sell winners) if a clear binding gate is identified

#### Tier A — Implementation status

| Item | Status | Commit (pending) |
|------|--------|------|
| ε.A.1 Config audit | Investigation deliverable from ε.B.4 | n/a (markdown report) |
| ε.A.2 ML SELL_BLOCK phantom gate | TODO | Stage 1 |
| ε.A.3 Sell_enforcement flip gate | TODO | Stage 1 |

### **STAGE 2 — Tier C structural changes (DO NOT FORGET — required after Stage 1)**

**This stage MUST happen after Stage 1 results land. Skipping it leaves the largest uplift unrealized.**

Tier C is the high-leverage structural work. It depends on Tier B findings to ship correctly. Each component below has a conditional gate on a Tier B finding.

#### **ε.C.0 — Sell-side tier wiring** (conditional on ε.B.1)
- If ε.B.1 confirms that γ.1 populated SNDK's mcap at exit but the sell-side circuit_breaker still used LOW default floor: thread the live resolver into the circuit_breaker floor selection
- File: TBD by investigation
- Scope: ~10-20 LOC
- Risk: medium (touches sell-side critical path)
- **Generalization**: works for any HIGH/MID tier stock with current circuit_breaker floor mismatch

#### **ε.C.1 — V32 convert_min_loss widening** (conditional on ε.B.3)
- If ε.B.3 shows that widening v32_convert_min_loss_pct from -2.0 to -4.0 (or -6.0) would CONVERT ≥30 additional rotations:
  - Change `v32_convert_min_loss_pct` default to -4.0
  - Add an explicit config knob `nexus_v32_convert_min_loss_pct_chop` / `_bull` / `_bear` for per-regime tuning
- Scope: ~5-10 LOC
- Risk: medium (could over-aggressively convert marginal-loser holds during chop)
- Test: paired-rerun expectation: +5-10pp uplift

#### **ε.C.2 — Regime-aware time-floor decay** (UNCONDITIONAL — ship in Stage 2 regardless)
- The dominant rejection cluster (52% pmh + 26% min_hold = 78%) is time-based, not conviction-based
- New per-regime knobs:
  - `rotation_min_hold_days_chop` (default 5, was 10)
  - `rotation_min_hold_days_bull` (default 10 — unchanged)
  - `rotation_min_hold_days_bear` (default 10 — unchanged)
  - `rotation_profitable_full_exit_min_hold_days_chop` (default 10, was 20)
  - `rotation_profitable_full_exit_min_hold_days_bull` (default 20 — unchanged)
  - `rotation_profitable_full_exit_min_hold_days_bear` (default 20 — unchanged)
- Loosens chop-regime rotation throughput by ~50% on the time-floor axis
- Scope: ~30 LOC + tests
- Risk: medium-high (chop-regime overtrading risk)
- **Generalization**: structural mechanism, no ticker-specific code
- Expected uplift: +10-20pp on BT294837

#### **ε.C.3 — γ.1 BFQ-source fix** (conditional on ε.B.1)
- If ε.B.1 confirms BFQ contributes 0 tickers to γ.1's mcap pre-seed (per existing 12-agent telemetry):
  - Investigate why `strategy_cache.get("_backfill_queue", {})` is empty at γ.1's call site
  - Fix: ensure BFQ tickers are pre-seeded for mcap before A1 tier resolution evaluates them
- Scope: ~15 LOC
- Risk: low
- **Generalization**: works for any high-conviction BFQ candidate that needs HIGH tier protection at execution

#### **ε.C.4 — Cap-leakage hard upper bound** (conditional on ε.B.2)
- If ε.B.2 identifies a TRUE BUG (not intentional winner_add):
  - Add hard cap check at the leaking path
- Scope: ~10 LOC
- Risk: medium (could break winner_add semantics if scope wrong)
- **Skip if** ε.B.2 shows the leakage IS winner_add and BT901920 also leaked similarly

#### **ε.C.5 — Rebuy-gate relaxation for post-sell HIGH-conviction recovery** (conditional on ε.B.5)
- Per ε.B.5 findings, design the specific fix for whatever binding gate blocks SNDK rebuy
- Likely candidates:
  - Reduce `_evict_cooldown_bars` from current default to chop=3 / bull=5
  - Add an override: post-sell HIGH-conviction recovery (price > entry_price * 1.5) lifts cooldown
  - Decay persistent negative propagation faster
- Scope: ~10-30 LOC depending on which gate
- Risk: medium (relaxing cooldowns could re-buy quickly into a continuing downtrend)
- **Generalization**: must verify the fix would help N other tickers in BT294837 too (per ε.B.5 generalizability check)
- Expected uplift: +3-8pp per missed-rebuy event captured

---

## 4. Validation gates

### Φ.ε.0 — Stage 1 fast smoke (after Tier A code ships, ~20min)

Run BT294837 once. Assert:
- ε.A.2: SELL_BLOCK phantom count goes from 4+ to 0 (or N to 0 generally)
- ε.A.3: `[sell-flip-guard]` log line appears at least once if any stock matches the trigger pattern (a stock with negative propagation AND no position)
- No regression in any existing Phase γ/δ test
- No code crash or new error spam

If any fail: triage, do NOT proceed to Stage 2.

### Φ.ε.1 — Stage 1 P&L gate (~6h backtest)

Compare BT294837 post-Tier-A to current trajectory:
- Target: P&L improvement of +3-10pp (modest but real)
- If NEGATIVE: the Tier A changes regressed something; investigate before Stage 2

### Φ.ε.2 — Stage 2 component gates (after each Tier C ship, ~1h each)

Per component: re-run BT294837, measure specific metric:
- ε.C.0: circuit_breaker floor distribution shifts from 100% LOW default to >10% HIGH/MID
- ε.C.1: V28.9 CONVERTED count rises proportional to the threshold widening
- ε.C.2: profitable_min_hold rejection count drops by 30%+ in chop regime
- ε.C.3: mcap pre-seed `universe_sources=bfq:>0` (currently 0)
- ε.C.4: max breach magnitude drops from current=14 to current<=10
- ε.C.5: SNDK (or generalized class of post-sell recovery candidates) successfully rebought

### Φ.ε.3 — Stage 2 final P&L gate (full BT294837, ~6h)

Cumulative target: BT294837 final P&L of +85-160% (per adversarial reviewer's quantification).

**Realistic ceiling per adversarial reviewer:** +120%. +250% reliably requires Phase ζ (seed curation × LLM determinism × trend-boost architecture).

---

## 5. Expected P&L impact (adversarial-review-discounted)

| Phase | Expected uplift on BT294837 |
|---|---|
| Current trajectory (post-γ+δ) | ~+60-110% (single-run, high variance per LLM jitter) |
| + Stage 1 Tier A | **+3-10pp uplift** → ~+65-120% |
| + Stage 2 Tier C (full set) | **+25-50pp total uplift** → ~+85-160% |
| + Phase ζ (deferred) | up to **+150-200%** with curated 50-100 ticker seed in a bull window |

**+250% reliably each time is structurally unachievable** because:
1. BT901920's +206% was 80% luck (two outlier picks + bull window)
2. LLM non-determinism produces ~4.8× spread on same-code paired re-runs
3. SNDK/LITE/MU have NEVER been captured by propagation in any backtest — they must be operator-seeded into the universe (data decision, not code)

---

## 6. Phase ζ candidates (deferred — outside Phase ε scope)

| ID | Description | Required for | Estimated LOC |
|---|---|---|---|
| ζ.1 | Trend → raw_score signal wiring (MU at trend_strength=0.92 should boost raw_score) | Surfacing trend-confirmed HC stocks without ticker hardcoding | ~50 LOC + spec |
| ζ.2 | LLM temperature=0 model swap | Variance compression < 1.5× (Phase α target) | Operator-owned |
| ζ.3 | Operator-curated seed expansion to 50-100 tickers | Replicating BT901920's seed-quality alpha | Data decision, not code |
| ζ.4 | Coordinated rotation-gate relaxation (winner_lock + pmh + min_hold + V28.8.1 cap as an ensemble) | If Stage 2 Tier C doesn't close the gap | ~100+ LOC + spec |

---

## 7. No-ticker-hardcoding constraint (operator-mandated)

**Every component above is checked against the constraint: no fix may bake in SNDK/LITE/MU (or any specific ticker) as literal strings.** All mechanisms must generalize.

Confirmed clean components:
- ε.A.2 (SELL_BLOCK phantom gate): generic position-existence check
- ε.A.3 (sell_enforcement flip): generic "you can only sell what you hold"
- ε.B.5 (SNDK rebuy audit): must report GENERALIZABILITY — which OTHER tickers would benefit
- ε.C.0 (sell-side tier wiring): generic — applies to any HIGH/MID tier resolution
- ε.C.1 (v32_convert widening): generic numeric threshold
- ε.C.2 (regime-aware time-floor decay): generic — applies to all stocks per regime
- ε.C.3 (γ.1 BFQ-source fix): generic BFQ read path
- ε.C.4 (cap-leakage bound): generic cap enforcement
- ε.C.5 (rebuy-gate relaxation): generic post-sell HIGH-conviction recovery class

---

## 8. References

- BT294837 logs: `backtests/294837_20260519-043948Z.log` (37,700 lines, RUNNING)
- BT136708 logs: `.tmp_bt136708/logs_via_api.log` (+171%, 75,638 lines)
- BT901920 logs: `.tmp_bt901920/logs_via_api.log` (+206.02%, 84,240 lines)
- Phase γ plan: `docs/superpowers/plans/2026-05-18-bt232179-phase-gamma-fix-plan.md`
- Phase γ commits: `365fde2` (γ.1-γ.5 impl), `697c6aa` (δ observability + scope audit)
- 12-agent investigation report: synthesized inline in this doc (no separate artifact)
- Adversarial review: synthesized inline in this doc

---

## 9. Operator decisions required

1. **Approve Tier A scope?** ε.A.2 + ε.A.3 = ~15 LOC, ship in Stage 1
2. **Approve Tier B investigations?** 5 read-only agents running in background
3. **Acknowledge realistic ceiling**: +85-160% on BT294837, not +250% reliably
4. **Phase ζ.3 (seed curation)**: willing to operator-curate a 50-100 ticker seed list? This is the ONLY known mechanism for reliable +200%+
5. **Phase ζ.2 (temp=0 model)**: willing to swap LLM model for variance compression?

---

## 10. **DO NOT FORGET — Stage 2 is required**

This doc captures the full Phase ε plan. **Stage 1 ships ~10% of the total uplift; Stage 2 ships the remaining 90%.** If only Stage 1 lands, the operator will see ~+5-15pp improvement and reasonably ask "why isn't this closing the gap" — the answer is "Stage 2 wasn't shipped yet."

Stage 2 trigger: all 5 Tier B investigation reports must complete and be reviewed. Then ε.C.0 through ε.C.5 ship in sequence with per-component Φ.ε.2 gates.

**This plan doc will be committed alongside Stage 1 code. The next session MUST reference this doc and execute Stage 2 — don't let it slide.**

---

## 11. Tier B investigation findings (all 5 reports landed during Stage 1)

The 5 Tier B investigation reports landed during the Stage 1 work. Stage 2 Tier C components are reshaped accordingly. Summary below; raw agent outputs are in `.claude/tasks/` JSONL transcripts for the audit trail.

### ε.B.1 — γ.1 mcap pre-seed at SNDK exit bar — **finding: ε.C.0 was MISDIAGNOSED**

Original premise: SNDK was force-sold by circuit_breaker with LOW default -15% floor (because the conviction tier disconnect). Investigation REFUTES this:

- **circuit_breaker NEVER FIRED for SNDK.** No `[sell-gate] SNDK | gate=circuit_breaker | ...` line exists in the entire 37,699-line log.
- γ.1 + γ.5 are working **perfectly**: SNDK resolved `tier=HIGH mcap=84502M raw_score=-1.000 path=mcap_high` at every bar including the exit bar (lines 1208, 2070, 2974, 3813, 4576, 5226, 5762, 6322, 6853, 7052).
- circuit_breaker code at `graph_nexus_analysis.py:15402-15404` uses LIVE `_resolve_conviction_tier_at_exit` (γ.5 wiring) — NOT a stored entry_conviction_tier. The wire IS correct.
- The actual exit mechanism: **V31 grace `escape_A_catastrophic`** at L4922 with hard-coded `initial_grace_catastrophic_loss_pct=-15.0`. This threshold is TIER-BLIND.

**Reshape ε.C.0** → **ε.C.0' (V31 grace tier-aware catastrophic threshold)**:
- File: `graph_nexus_analysis.py` `_in_initial_grace_period` (~L4920)
- Make `initial_grace_catastrophic_loss_pct` tier-aware:
  - HIGH tier: -25% (matches HIGH conviction CB floor)
  - MID tier: -20%
  - LOW tier: -15% (unchanged, current default)
- Scope: ~15 LOC
- **Generalization**: works for any HIGH/MID conviction stock; not ticker-specific

### ε.B.2 — Cap-leakage source identification — **finding: ETF allocation bypasses cap**

The `current=14 > max=8` overflow is caused by **ETF allocation at L23075-23089** which writes `nexus_position_sizes[sym] = {"asset_class": "etf", ...}` WITHOUT consulting `_current_positions`, `_max_positions`, or `_v28_7_position_breach_active`. The breach check at L22038 counts ALL positions (stocks + ETFs), but the cap is enforced only on `_new_stock_candidates`.

- Bar 1: 4-6 trend ETFs (BOTZ, ROBT, WTAI, ARKQ, UBOT, AIQ) + 8 stocks seated immediately → 12-14 positions
- ETFs ride along through entire backtest protected by `profitable_min_hold`/`winner_lock`
- Portfolio floor = 8 stocks + 6 ETFs = 14, occasionally trimmed to 13/12

**Reshape ε.C.4** → **ε.C.4 (ETF allocation cap enforcement)**:
- File: `graph_nexus_analysis.py:23075-23089`
- Add cap check: ETF count + stock count <= `max_positions` (or introduce `max_positions_etf` separate budget)
- Scope: ~10-20 LOC
- **Highest-leverage fix** per ε.B.5's generalization finding — fixing this unblocks dozens of HIGH-conviction tickers, not just SNDK

### ε.B.3 — V32 convert-vs-skip ratio — **finding: ε.C.1 was BACKWARDS**

Original premise: widen `v32_convert_min_loss_pct` from -2.0 to -4.0 to CONVERT more rotations. Investigation REFUTES this:
- 0 SKIP events have held_pnl ≤ -4% in BT294837
- 89 SKIP events ALREADY pass the -2.0 threshold but don't convert (blocked by `break_glass_fresh_shield` L22539, `same_ticker_cooldown` L22559, or held outside `at_cap`)
- 84 SKIP events are winners (pnl ≥ 0%) — ineligible for convert by mode

**Reshape ε.C.1** → **ε.C.1' (V32 convert downstream-gate audit)**:
- Investigate why 89 events at -2% to -3.9% with `_sell_fraction < 0.999` are not converting given they pass the loss-pct gate
- Candidate fix: relax `break_glass_fresh_shield` time-window or `same_ticker_cooldown` for HIGH-conviction incoming
- Scope: TBD by additional investigation (~10-30 LOC)

### ε.B.4 — Config diff BT294837 vs BT901920 — **finding: CONFIGS ARE BYTE-IDENTICAL**

**Critical reframing:** A programmatic key-by-key diff of all 526 config keys shows **ZERO drift** between BT294837 and BT901920. Same strategy_id (179), same instance_id, same start date, same initial_cash, same every knob.

The gap is NOT configuration drift. It is:
1. **Code build drift**: BT901920 ran on a pre-Phase-α/γ/δ build. BT294837 has variance-containment (`55d7a52`) + Phase γ + δ. Variance pinning may have suppressed a stochastic alpha source.
2. **Environment drift**: BT294837 hit the BT109429 silent-fail mode 3 times at run start (`mcap pre-seed: 0 tickers populated`). BT901920 has zero such warnings.
3. **Run state**: **BT294837 is at 43.44% progress with 11.5% P&L. BT901920 is finished at 100% with 247.6% P&L.** You are not comparing finished-vs-finished. Half the gap is just run-progress.

**Operator decision points (urgent):**
- **DO NOT spend time hunting config reverts** — there are no drifted knobs.
- **Wait or kill BT294837?** It is at 43.44% — finishing it gives the only apples-to-apples top-line P&L number. Current 11.5% is NOT comparable to BT901920's 247.6%.
- **Triage mcap pre-seed silent-fail at start**: 3 occurrences of `populated=0` per ε.B.4 finding. Per ε.B.1, γ.1 IS working for resolution, but the initial cold-start cohort still hits the silent-fail warning. May explain early-bars conviction misses.
- **Re-run BT901920 on current code SHA** to quantify pure code-drift effect. If new code produces +150% vs old code's +247%, variance-containment IS the culprit and Phase ζ.2 (LLM determinism via temp=0) becomes urgent.

This finding RESHAPES the entire premise of Phase ε. **The strategy isn't "regressed via knob drift"; it's a different binary running on a partial run.** Phase ε's structural fixes are still valuable (the bugs ε.B.1/ε.B.2/ε.B.5 identified are real), but the +250% target requires the operator to finish BT294837 AND re-run BT901920 on current code to isolate code-drift vs run-progress.

### ε.B.5 — SNDK rebuy gate audit — **finding: V28.8.1 BREACH is the dominant blocker**

Comprehensive trace of every SNDK post-sell event reveals:

- **SNDK had ONE legitimate rebuy opportunity at bar 2026-01-06 (L25667, price=$349.59)**. Momentum watchlist scored SNDK 0.323 (#2), V31.4 lifted cooldown via `20d_high_breakout` (cur=$349.59 > $275.29 × 1.05).
- **Immediate blocker: `V28.8.1 max_positions BREACH] current=12 > max=8 (auto-heal freed 0)`**.
- V28 ROT EVAL paired SNDK against 12 holds — only NFLX (v28_hc_losing_break_glass, pnl=-2.5%) was theoretically allowed, but V28.8.1 SKIPPED it (partial trim + CONVERT-blocked).
- BFQ FORCE-ADD at L25909 also gated by same breach flag.
- 5 other cooldown lifts (V31.4) fired but failed to translate to executable buys because (a) momentum watchlist slots went to higher-scoring tickers (e.g. LITE/TYRA at L7499), and (b) breach gate persisted.

**Generalization**: at every breach bar in BT294837 (52 bars total), 10-16 HIGH-conviction tickers are unfunded. Top unfunded names recur: **ATR (1.800), CRH (1.800), MMS (1.800), OR (1.800), PLUG (1.800), AIR (1.503), PASG (1.500), SNDK, UAL (1.218), MS, WFC, GM, NBIS, GS, PNFP**. Easing the breach gate would unblock dozens of HIGH-conviction tickers — not ticker-specific.

**Root cause chain** (ranked by severity):
1. **ETF allocation cap-leak** (ε.B.2) → portfolio sits at 12-14 positions vs cap=8
2. **V28.8.1 BREACH blocks BFQ + partial-trim** rotations
3. **Breach auto-heal effectively dead** (`auto-heal freed 0` on most breach bars due to winner_lock>15%, in-grace, held<3d filters)
4. **Stale Neo4j sentiment** (`SNDK=-1.000(12p)`) never decays — keeps SNDK in propagation_expansion SELL rows even when momentum_watchlist scores it positive

**Reshape ε.C.5** → **ε.C.5 (Stale Neo4j sentiment decay)**:
- File: TBD by code inspection. Need to find where `_neo4j_market_cap_cache` or propagation sentiment is cached.
- Add time-based decay (e.g., propagation sentiment older than 14 days drops to 0 unless refreshed)
- Scope: ~20 LOC
- **Generalization**: works for any post-sell HIGH-conviction recovery candidate

**Also identified bug**: V31.4 recovery_shortcut lifts the cooldown but doesn't PROMOTE the ticker to a buy candidate — it just unmasks `_mw_cooldown_set`. If the slot allocation favors other higher-scoring tickers that bar, the lifted ticker is dropped.

**Reshape Phase ε** → **ε.C.6 (V31.4 cooldown-lift force-promotion)**:
- File: `graph_nexus_analysis.py:21788-21889` (V31.4 logic)
- When a V31.4 lift fires, RESERVE a momentum_watchlist new_buys slot for the lifted ticker
- Scope: ~15 LOC
- **Generalization**: applies to any post-sell HIGH-conviction recovery candidate

---

## 12. Stage 2 — REVISED Tier C plan (post Tier B findings)

| Component | Status | Action |
|---|---|---|
| ε.C.0 (sell-side tier wiring) | **REJECTED — misdiagnosed by ε.B.1** | Replaced by ε.C.0' below |
| **ε.C.0' (V31 grace tier-aware catastrophic threshold)** | **NEW — ship** | Make `initial_grace_catastrophic_loss_pct` tier-aware: HIGH=-25%, MID=-20%, LOW=-15%. ~15 LOC. |
| ε.C.1 (v32_convert widening) | **REJECTED — backwards by ε.B.3** | Replaced by ε.C.1' below |
| **ε.C.1' (V32 convert downstream-gate audit + relax)** | **NEW — investigate then ship** | Audit `break_glass_fresh_shield` + `same_ticker_cooldown` blocking 89 events that pass loss-pct gate. ~10-30 LOC. |
| ε.C.2 (regime-aware time-floor decay) | **UNCONDITIONAL — ship** | `rotation_*_min_hold_days_chop/bull/bear` per-regime knobs. ~30 LOC. |
| ε.C.3 (γ.1 BFQ-source fix) | **DEFERRED — ε.B.1 confirms γ.1 working for resolution** | Not blocking |
| **ε.C.4 (ETF allocation cap enforcement)** | **HIGHEST LEVERAGE — ship FIRST** | ETF channel bypasses cap at L23075-23089. ~10-20 LOC. **Fixes the breach permanent-state root cause.** |
| **ε.C.5 (stale Neo4j sentiment decay)** | **NEW — ship** | Time-based decay on propagation sentiment cache. ~20 LOC. |
| **ε.C.6 (V31.4 cooldown-lift force-promotion)** | **NEW — ship** | Reserve momentum_watchlist new_buys slot for lifted tickers. ~15 LOC. |

**Sequencing**: ε.C.4 ships FIRST (highest leverage, root-cause). Then ε.C.0' + ε.C.2 + ε.C.5 + ε.C.6 in parallel (independent). Then ε.C.1' last (depends on additional investigation).

**Expected uplift (revised post Tier B):**

| Component | Expected uplift on BT294837 |
|---|---|
| ε.C.4 (ETF cap fix) | +15-30pp (eliminates the breach permanent-state) |
| ε.C.0' (V31 tier-aware catastrophic) | +5-10pp (HIGH-tier stocks survive deeper drawdowns) |
| ε.C.2 (regime-aware time-floor) | +5-15pp (chop-regime rotation throughput) |
| ε.C.5 (stale sentiment decay) | +3-8pp (post-sell recovery candidates re-promoted) |
| ε.C.6 (V31.4 lift force-promotion) | +2-5pp (per missed-rebuy event captured) |
| ε.C.1' (V32 downstream-gate relax) | +3-8pp (89 stuck events potentially admit) |
| **Stage 2 total realistic uplift** | **+33-76pp** |

Combined with Stage 1 Tier A (~+5-15pp) → **+38-91pp on top of current trajectory**, taking BT294837 to a realistic finished range of **+100-200%** depending on which fixes hit + bar timing.

**The +250% reliably each time target remains structurally unachievable** without:
- Phase ζ.2 (LLM determinism via temp=0 model swap) — operator-owned
- Phase ζ.3 (operator-curated seed expansion) — data decision, not code

Both Phase ζ items remain outside Phase ε scope.

---

## 13. Operator decisions — UPDATED post Tier B

The Tier B findings change the operator's decision matrix:

1. **CRITICAL: Wait for BT294837 to finish or kill it.** Currently at 43.44% / +11.5%. Comparing this to BT901920's 247.6% (finished) is apples-to-oranges. Decide: wait ~6h, or stop and re-launch with Stage 1 patches.

2. **Approve revised Stage 2 scope?** ε.C.4 (ETF cap fix) is the single highest-leverage change. Approving it alone is reasonable; bundling ε.C.0' + ε.C.2 + ε.C.5 + ε.C.6 multiplies the impact.

3. **Re-run BT901920 on current code SHA?** This isolates code-drift effect. Required to know if Phase α's variance pinning suppressed an alpha source vs reproducible improvement.

4. **Phase ζ.2 (LLM temperature=0)?** Required for variance compression. Without it, +200%+ won't reliably reproduce.

5. **Phase ζ.3 (operator-curated 50-100 ticker seed)?** Required for any +200%+ ceiling.

---

## 14. **DO NOT FORGET — Stage 2 is required**

This doc is the authoritative reference. **Stage 1 (Tier A) ships ~+5-15pp uplift; Stage 2 (Tier C reshape per Tier B findings) ships ~+33-76pp.** If only Stage 1 lands, the operator will see modest improvement and the gap to BT901920 will not close.

**Stage 2 trigger:** all 5 Tier B reports are now in this doc. Stage 2 can proceed in the next session. **The next session MUST execute Stage 2 ε.C.4 + ε.C.0' + ε.C.2 + ε.C.5 + ε.C.6 (skip ε.C.1' for now or batch with the others), with per-component Φ.ε.2 measurement gates.**
