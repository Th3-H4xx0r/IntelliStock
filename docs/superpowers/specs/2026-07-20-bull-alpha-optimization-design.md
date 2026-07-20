# Bull-Alpha Optimization — Design (2026-07-20)

**Goal:** raise the bull-window return (bt 148462: +6.60%, 2026-03-30→04-27, SPY +13.6%) well above SPY while preserving the bear-window result (bt 726941: +2.29%, 2026-03-02→03-30, SPY −8%).

**Targets:** bull ≥ +16% (SPY + ~2.5pp or better); bear stays positive and within 0.5pp of its re-validated baseline.

## 1. Evidence base

Five parallel forensic passes over the full logs of bt 148462 (bull final), bt 726941 (bear win), and bt 643754 (pre-fix bull, −11.47%). Logs archived in the session job dir; line-level citations live in the investigation transcripts.

### 1.1 Gap decomposition (bull window, $6k book, gap vs SPY = $421 / 7.0pp)

Per-dollar long stock selection was NOT the problem: return on deployed capital ex-SQQQ was **13.8% — it matched SPY**. The gap is mechanics:

| Drag source | $ impact | Nature |
|---|---|---|
| False bear episode, week 1 (stale day-1 trigger: ret20 −8.18% inherited from March, QQQ proxy) | SQQQ −$236 + delayed winner-cohort entry ≈ −$555 + forced concentration in TEXU/TTXD −$141 | Regime detector staleness |
| CAR blow-up | −$154 realized + it tripped the −17.3% drawdown KILL that flattened all 14 positions on 04-23 into the closing rally (≈ −$60–120 more) | Entry gate fail-open + exit non-execution |
| Backfill-queue starvation (200 × `ALLOC=0 headroom=0`) | −$250–500 (INTC #1-scored from 04-08, +97% window move, unfunded 13 sessions; MSTR +40.6% never funded) | No displacement mechanism |
| Conviction-tail under-sizing (halving ladder + $100 min_pos) | −$310 (AMD $100 slot, +66% move → +$170 forfeit; RKLB +$142) | Sizing ladder |
| Avg deployment only 69–74% | ~$214–250 of the gap (largely overlaps rows 1–2) | Bear lockout + KILL |

(Buckets overlap; recoverable total is capped by run dynamics, not by the sum.)

### 1.2 Root causes confirmed in code

1. **Extension gate fails OPEN on missing bars** (`_v32_momentum_ath_or_mcap_block`, graph_nexus_analysis.py:4971–5061; helper `_recent_runup_protect` :7397). All momentum-lane call sites pass the broker `data` dict, which is EMPTY for fresh discoveries — CAR's bars were fetched only *after* the buy was emitted. The momentum scorer that picked CAR (score 1.839) reads `strategy_cache["_overlay_bars_raw"]`, which HAD the bars. The picker sees prices; the gate doesn't. This is why the gate has fired **zero times across all three runs** (resolves handoff open item #1 — it's not "unobserved", it's unreachable for discovery entries).
2. **`entry_extension_lookback_bars=20` is not granularity-scaled** — at 1h bars that's ~3 trading days, not the intended ~month.
3. **Profit-take/trailing sells can trigger without executing.** CAR: +21.4%→33% and +48.7%→50% tiers TRIGGERED, "ML overlay PRESERVE … partial trim retained", never reached the broker; trailing stop fired at +72% and slid two sim-days (after-hours bars → pre-market deferral → 14:00 broker tick) to fill at −18.5%. Exit at first trigger = +$601 instead of −$154. Caution: the same non-execution SAVED FLY (+$107) — double-edged.
4. **Bear regime trigger was stale on day 1** of the bull window: −8.18% ret20 was March's drawdown, mostly already played out; threshold changes don't help (reading was below any sane threshold; raising to 5.0 also delays the real bear-month protection by ~2.5 weeks — REFUTED as a lever).
5. **The bear +2.29% has never been reproduced under HEAD + the current patch.** bt 726941 ran with `regime_upgrade_confirm_bars=3` (patch: 2), `max_positions_chop=8` (patch: 10), `deployment_ramp_chop_scale=0.6` (patch: 0.85), and a build predating the sleeve-invisibility fixes. The chop-scoped patch keys are live, unvalidated changes to bear behavior.

### 1.3 DO-NOT-TOUCH inventory (fired in the bear win)

`regime_bear_spy_drawdown_pct=3.0` (bear days classified at −3.5..−4.0 — the 5.0 default would erase the win), `max_positions_bear=2` (43 hard blocks), SQQQ bear leg 0.35 alloc / 10% stop (+$285 = 208% of the bear month's P&L), Bear RS entry gate (750 blocks), rotation-lanes regime gate, bear-mode fast-loser cut, one-stop-per-episode latch (+$247 vs pre-latch run in the bull window; no-op in bear).

**Leak list (shared state — any change here requires paired-window validation):** `regime_upgrade_confirm_bars` (gates BOTH bear→chop and chop→bull), `max_positions_chop` + `deployment_ramp_chop_scale` (chop days occur inside bear months), sleeve `min_park_hours`/`release_cash_pct`/`buffer_pct`/`min_deploy_pct` (shared by SPY and SQQQ legs), episode-latch re-arm (couples to regime flicker), drawdown-circuit thresholds (regime-agnostic), `regime_bear_spy_drawdown_pct` (defines everything).

## 2. Approaches considered

**A. Plumbing-first (RECOMMENDED).** Fix the mechanics that lost money without moving any threshold that fired in the bear win: give the entry gates real bars, fund starved conviction names, floor conviction sizing, and add a *staleness* guard (not a threshold change) to the bear trigger. Highest expected value per unit of bear risk; every change is entry-side or bull/chop-scoped.

**B. Config-only levers (REJECTED).** The handoff's two candidates are refuted by forensics: `regime_bear_spy_drawdown_pct` 3.0→5.0 does NOT prevent the bull-month SQQQ loss (day-1 reading −8.18%) and delays the bear-month protective cap ~2.5 weeks; `residual_sleeve_bear_alloc_pct` 0.35→0.25 saves ~$67 in bull but costs ~$81 in bear. Small, symmetric, not worth runs.

**C. Aggressive (= A + exit-execution fix + bull stop widening).** Adds the profit-take non-execution fix (worth +$300–750 on CAR alone) and `circuit_breaker_regime_adjustment_bull_pp`. Higher ceiling, but the exit fix is double-edged (it saved FLY) and stop widening increases CAR-class tolerance. Run as a **separate graduation experiment** after A lands, not bundled.

## 3. The plan (Approach A, phased; each phase = paired bear+bull backtests)

Execution mechanics for every phase: apply config via `scripts/apply_doc179_config_patch_api.py`, **verify the patch stuck (GET /strategies/179) before POST /backtests** (a failed apply silently runs baseline), granularity "3600", $6k, BACKTEST_SEED=0, alpaca-main. Windows: BEAR 2026-03-02→03-30, BULL 2026-03-30→04-27. Maintain the run ledger. doc-179 reverted to bear-safe baseline after every session; live wake stays OFF.

### Phase 0 — Re-validate the bear baseline under HEAD + patch (MANDATORY, blocking)
Run the BEAR window under the exact fullstack patch. This is the missing experiment: the +2.29% predates confirm=2/chop=10/ramp=0.85 and the sleeve-invisibility fixes.
- **Gate:** bear stays positive. If it degrades, first repair bear under the patch (likely suspects per leak list: confirm=2 → try 3, chop cap 10 → 8) before ANY bull work. The repaired config becomes baseline **B0**; the bull window must be re-run under B0 if it changed.

### Phase 1 — Entry-gate plumbing (code, ~half day)
1. `_v32_momentum_ath_or_mcap_block` + the `_apply_quality_filter` extension site: fall back to `strategy_cache["_overlay_bars_raw"]` when `data` has <2 bars for the symbol; if a *discovery-lane* entry still has no bars, **fail closed** (mirror the Bear RS gate posture).
2. Scale `entry_extension_lookback_bars` with `_scale_bars` (like `momentum_min_history_bars`).
3. Unit tests: gate fires on synthetic >25% runup via overlay-bars fallback; fail-closed on empty history; scaled lookback at 1h vs 1d.
- **Expected:** blocks CAR (−$154 + KILL knock-on), TEXU (−$66), likely TTXD (−$74). Bear-window check: ANAB/FCG/LNGX runups were 12–15.5% < 25% — should pass; the paired bear run proves it.
- **Gate:** bull ≥ +1pp vs B0-bull; bear within 0.5pp of B0.

### Phase 2 — Fund conviction (code, bull/chop-scoped, ~1 day)
1. **Backfill-queue displacement:** when the queue's top name has high conviction (score ≥ ~1.3) and a held position is a loser, allow the existing V28.8.1 rotation pair to displace it (it evaluated exactly this — `USL(pnl=-1.7%)→INTC` — and SKIPPED). New config key, active only where `rotation_lanes_regime_gated` already permits rotation (off in bear by construction).
2. **Conviction sizing floor:** in the bull/chop sizing ladder, floor high-conviction rungs at ~7% NAV (~$430) instead of the $100 min_pos tail. Regime-scoped to bull/chop branches only.
- **Expected:** +$250–500 (queue) +$200–310 (sizing) class recoveries.
- **Gate:** same paired-run criteria.

### Phase 3 — Stale-bear-trigger guard (code, touches the detector — most sensitive, ~half day + careful A/B)
Suppress a *bear* classification when the drawdown that produced it has already recovered ≥ R% off its trailing low (start R=50%). Additive condition only; in the real bear month every reading was fresh (still falling), so the guard should be silent there — the paired bear run is the proof.
- **Expected:** shortens the bull window's false-bear week (faster cap lift; SQQQ entry may still occur on day 1 — treat residual SQQQ cost as genuine cycle-boundary insurance).
- **Gate:** bear within 0.5pp of B0 with the guard demonstrably silent in bear logs; bull improves.

### Phase 4 (optional graduation, = Approach C) — Exit-execution experiment
Investigate the ML-overlay PRESERVE path swallowing triggered profit-take tiers, and the after-hours/pre-market deferral slide. A/B as its own run-pair; FLY/CAR asymmetry means no prior on the sign.

### Stop rule
After Phases 1–3, if bull < +16% but > SPY, present results and stop; further levers (Phase 4, bull cap, rotation cadence `_bull` keys) are user calls. Any phase that degrades bear beyond the gate is reverted immediately (backup json per apply, as before).

## 4. Risks

- **Phase 0 may reveal the bear win doesn't survive the current patch** — that's the point of running it first; bull results mean nothing against a broken bear baseline.
- Extension gate now actually firing could block bear-month RS winners or future bull winners with >25% runups (momentum lanes are momentum-seeking by design). Mitigation: paired runs + the 2.50-conviction bypass stays only where it exists today (scored lane), never momentum lanes.
- CAR's price series looks glitchy in the log (entry $311 vs movement summary $148→187). Add a bar-sanity check on CAR before attributing; if bad bars, the CAR-specific $ estimates shrink but the gate fix stays correct.
- Backtest-vs-live divergence: all of this is the fast in-engine path on real bars; the 2nd-exit=50 lesson says execution mechanics need prod A/B before live re-apply. Live re-apply of any winning profile remains a user decision.

## 5. Success criteria

- BEAR (03-02→03-30): positive, within 0.5pp of re-validated B0, protective inventory (cap=2 blocks, SQQQ parks, RS-gate blocks) intact in logs.
- BULL (03-30→04-27): ≥ +16% (vs SPY +13.6%), with the extension gate observed firing (or correctly passing with bars visible) in the log.
- Full cycle: compounded ≥ +18% vs SPY ≈ +4.5%.
- 2,688-test suite green; new unit tests for gate fallback/fail-closed/scaling.
