# Nexus Strategy — P&L Maximization Design (Tier 2, revised post-adversarial-review)

**Date:** 2026-05-15
**Status:** v2 design, addresses 5 BLOCKERS and 2 SERIOUS findings from adversarial review. Pending operator sign-off before plan/implementation.
**Source backtest:** `BacktestResults.id = 299903` (RethinkDB `<RETHINKDB_HOST>/IntelliStock`)
**Strategy:** `graph_nexus_analysis` (`backend/strategies/graph_nexus_analysis.py`, ~22.5K LOC)

## 1. Problem statement

Backtest 299903 (2026-04-30 → 2026-05-12, 9 trading days, $10K initial):
- Strategy P&L: -1.22% (-$121.76)
- SPY same window: +3.74%
- Alpha gap: **-4.96pp**
- Naive equal-weight buy-and-hold of strategy's own 11 picks: +6.5%

Discovery layer produced +7.19pp passive alpha; trading layer converted it into -4.96pp realized — **-12.15pp self-inflicted destruction.**

## 2. What changed between v1 and v2

The v1 design (pre-adversarial-review) is preserved in git history (commit `d7ce2c9`). v2 addresses:

| Finding | v1 position | v2 resolution |
|---------|-------------|---------------|
| BLOCKER 1: Overfit constants (50%/150%, -10%, 0.10/0.50, -0.45) | Hardcoded round numbers | Converted to market-stationary expressions (vol-scaled, percentile-based, ATR-scaled). Numeric defaults stated explicitly for audit but every knob is a function of measurable distribution properties. |
| BLOCKER 2: Regime dependence | "Walk-forward 3 regimes, positive in 2 of 3" | Package is gated by regime detector. In bear/crash regimes the strategy reverts to a defensive mode (smaller positions, no momentum bias). v2 also adds an explicit kill-switch criterion. |
| BLOCKER 3: Z5 Trojan-horse momentum bias | Included as biggest contributor | **Deferred to a separate Tier-3 spec.** Root cause of `Base=0.00` mass-zeroing must be investigated at the graph layer first. |
| BLOCKER 4: Unjustified SPY+6-12pp/yr | Published as target | **Removed.** Replaced with a window-relative claim and an explicit validation-volume gate. |
| BLOCKER 5: LLM-recovery scope contradiction | Non-goal that gated the target | Two parallel targets published: "LLM-on" (contingent) and "LLM-off" (committable). LLM-off is the primary deliverable. |
| SERIOUS 6: Z2.1 could break legitimate exits | Enforcement gate from day 1 | **Mandatory log-only mode for phase 1** + explicit whitelist of legitimate sell pathways before enforcement. |
| SERIOUS 7: Intraday-low unverified | Acknowledged but unfixed | **Verified:** strategy code never reads `low`/`high`/`open` fields (grep confirms zero matches). Z3.1 downgraded to use rolling close-based drawdown signal. |

## 3. Goals and non-goals

### Goals (unchanged)

1. Convert existing discovery alpha into realized P&L without compromising regime robustness
2. Block parabolic / FOMO buys at the discovery gate
3. Stop "ghost sells" of winners
4. Cut held losers faster
5. Deploy idle cash that compounds-down in rising markets
6. Open meaningful headroom for the backfill queue to drain

### Non-goals (with explicit cost acknowledgement)

- **LLM safety net recovery** (Claude CLI quota, gpt-5-mini reliability). Operator handles. Cost: caps "LLM-off" target at SPY+2-4pp/yr published; "LLM-on" target requires operator restoring nets.
- **Fixing the Base=0.00 mass-zeroing root cause.** Out of scope for this spec; tracked separately. Cost: the +$800–1,800 Z5 contribution is removed from the package estimate.
- ML model retraining (constant 0.50/0.50)
- Discovery layer redesign
- Live-trading rollout

## 4. Design anchors (operator decisions, 2026-05-15)

1. Tier 2 scope, ~10–12 changes after adversarial revisions (was 15 in v1)
2. Asymmetric stop discipline — cut losers fast, let winners run
3. `max_positions` 8→12 with soft sector cap
4. ~~Include scoring fallback~~ — **deferred to separate Tier-3 spec**

## 5. Regime gating (new in v2 — addresses Blocker 2)

The entire package is conditional on regime. Use the existing `V31 market regime` detector (already in `gna.py`, logged as "V31 market regime: bull|bear|chop").

| Regime | Package behavior |
|--------|------------------|
| **Bull** (V31 = bull AND SPY 20d > +2%) | Full package enabled |
| **Neutral / chop** (V31 = neutral OR \|SPY 20d\| ≤ 2%) | Z3.2 trailing-stop activation widened to 1.5× vol (reduces whipsaw); Z4.1 max_positions stays at 8 (don't over-deploy in chop); Z2.3 macro-haircut bug fix still applies but does not allow re-deploying past 70% of capital |
| **Bear** (V31 = bear OR SPY 20d < -5%) | **Defensive mode:** strategy holds maximum 4 positions (down from 8); raises `min_market_cap` to $5B; disables all momentum-driven discovery lanes; tightens Z3.1 loss-floor to 1× vol; treats Z4.4 reserve release as suspended (keep cash) |
| **Crash** (SPY -8% intraday or VIX > 40) | **Kill switch:** halt all new buys for ≥3 trading days; existing stops still execute; revert to bull-mode parameters only after VIX < 30 AND SPY 20d > 0 |

The crash kill switch is the explicit answer to "what breaks in a flash crash."

## 6. The interventions (v2, 12 changes)

### Zone 1 — Discovery hardening (4 changes, all retained)

| # | Change | Type | File:line | Mechanism (v2 revisions in bold) |
|---|--------|------|-----------|----------------------------------|
| Z1.1 | Add momentum-ceiling on `_discover_stocks_from_momentum`. **v2: ceiling = max(historical_3yr_p95_60d_return, configured_default). Configured default raised to 200% for safety margin. Re-evaluated annually against rolling 3yr cross-sectional distribution.** Mirrors `_rediscover_momentum_comebacks:9320-9325` ceiling logic | code | `gna.py:9176-9219` | Blocks parabolic candidates without curve-fitting to AIOS specifically. AIOS at +5,200% would be blocked by any reasonable percentile. |
| Z1.2 | `portfolio_swap_ath_gate_enabled = true`, `portfolio_swap_ath_gate_max_pct = 0.05`, `portfolio_swap_ath_gate_bypass_raw = 2.5` | config | `gna.py:4494, :4496` | Already-coded gate, no overfitting risk |
| Z1.3 | `momentum_watchlist_mcap_prefilter_enabled = true`, `momentum_watchlist_min_market_cap = 2_000_000_000` | config | `gna.py:4522` | Already-coded gate |
| Z1.4 | `quality_filter_missing_metadata_policy` → `"block"` | config | `gna.py:6644, :15686` | Bug fix; current code default is already block |

### Zone 2 — Decision plumbing (3 changes, all retained, Z2.1 revised)

| # | Change | Type | File:line | Mechanism (v2 revisions in bold) |
|---|--------|------|-----------|----------------------------------|
| Z2.1 | Ghost-sell observation. **v2: PHASE 1 is log-only — emit "would-block" telemetry without actually blocking. PHASE 2 (only after audit of telemetry) converts to enforcement with an explicit whitelist of legitimate sell intents: `sell`, `sell_override`, `stop_loss`, `circuit_breaker`, `v11_deep_loser_protect`, `trend_reversal_forced`. Anything outside the whitelist with `intent != "sell"` is blocked.** | code | trade-execution path | Catches ghost sells without breaking AORT-class legitimate forced exits. Mandatory observation phase confirms which paths fire. |
| Z2.2 | Daily re-eval of held positions | code | `_score_symbols` / `_finalize_scores` | Held losers get continuous evaluation. No tunable parameter — pure data-completeness fix. |
| Z2.3 | Fix macro-haircut false-positive: remove `max(0.5, confidence)` floor at `:7038, :7045`; add price-confirmation gate (only haircut when `net_score < 0` AND SPY 20d < 0) | code | `_compute_macro_risk_scale:7020`, `:7038`, `:7045` | Bug fix. No overfitting — the `max(0.5, confidence)` floor is an unambiguous bug. |

### Zone 3 — Asymmetric stop discipline (3 changes, all revised)

| # | Change | Type | File:line | Mechanism (v2 revisions in bold) |
|---|--------|------|-----------|----------------------------------|
| Z3.1 | **v2: NO intraday-low check.** Strategy doesn't read `low` field (grep verified). Replace with: rolling 5-day close-based drawdown stop, where the **floor = max(absolute -15% default, 2× position's 20-day realized volatility)**. Each position gets its own vol-scaled floor; the absolute -15% floor is a sanity backstop. | code | `gna.py:13575` (`_unrealized_pct` + caller) | Per-symbol vol-aware floor — AORT (higher vol) gets tighter floor than AZN (lower vol). Cuts AORT faster while not whipsawing AZN. |
| Z3.2 | Earlier trailing-stop activation. **v2: activation threshold = max(absolute 5% default, 0.75× position's 20-day realized volatility).** Vol-scaled per-symbol; absolute 5% as floor. | config + code | `gna.py:13658, :13536` | High-vol names (SOXL) need bigger gain before trail activates to avoid whipsaw; low-vol names trail tightly. |
| Z3.3 | `sell_threshold` stays at -0.30 default; `winner_sell_protection_min_pnl_pct` raised to 5.0%. **v2: NO change to sell_threshold value — the -0.45 in v1 was a fitted number with no rationale. Just enable the existing winner-protection gate.** | config | `gna.py:15447, :11409` | Only the documented protective gate is changed; no fitted threshold added. |

### Zone 4 — Capacity expansion (3 changes — Z4.2 revised, Z4.3 + Z4.4 retained as bug fixes)

| # | Change | Type | File:line | Mechanism (v2 revisions in bold) |
|---|--------|------|-----------|----------------------------------|
| Z4.1 | `max_positions` 8 → 12 (bull regime only; chop=8, bear=4 per Section 5). | config + regime gate | `schema.json:282`, `gna.py:6655` | Regime-conditional capacity. |
| Z4.2 | Soft sector cap. **v2: bypass threshold = max(absolute 0.8 default, top-decile of `raw_net_score` distribution across today's universe). Plus `graph_paths ≥ 5`. Sector cap bypass only available when both conditions met.** | code | sector-cap emitter (grep `SECTOR_CAP exceeded`) | Threshold is rank-based (top decile) not fitted, so it generalizes across universes. |
| Z4.3 | Fix queue TTL collapse under dual-cadence | code | `gna.py:16550, :16554` | Bug fix |
| Z4.4 | Release dead reserves when V31 anchor / backfill reserves have no qualifying candidates | code | `gna.py:20429-20455`, `:5844-5876` | Bug fix |

### Zone 5 — DEFERRED

Z5.1 (scoring fallback) moved to a separate spec (`2026-05-15-base-zero-root-cause-investigation.md`, to be drafted). Investigation must establish:
1. Why are 88% of evaluations stuck at `Base=0.00`?
2. Is it a discovery-side issue (paths not propagating) or scoring-side (signal aggregation collapsing to zero)?
3. Can the root cause be fixed without introducing momentum-factor exposure?

## 7. P&L estimate band (revised — addresses Blocker 4)

**v1's annualized target (SPY+6-12pp/yr) is withdrawn.** Insufficient validation volume to publish an annualized number.

### Window-relative estimate (backtest 299903 re-run only)

| Zone | Low ($) | Expected ($) | High ($) | Note |
|------|--------:|-------------:|---------:|------|
| Z1 | +100 | +350 | +500 | AIOS prevented; other parabolic blocks marginal |
| Z2 | +50 | +250 | +500 | Ghost-sell fix is uncertain until phase-2 enforcement |
| Z3 | +50 | +200 | +450 | Vol-scaled stops; less aggressive than v1's intraday-low |
| Z4 | +50 | +150 | +350 | Capacity helps only if discovery surfaces good names |
| **Sum (naive)** | **+250** | **+950** | **+1,800** | |
| **After -30% interaction haircut** (joint-effect penalty larger in v2) | **+175** | **+665** | **+1,260** | |

In-window scenarios:

| Scenario | Strategy return | Alpha vs SPY |
|----------|----------------:|-------------:|
| Current 299903 baseline | -1.22% | -4.96pp |
| Low band | +0.53% | -3.21pp |
| Expected | +5.43% | +1.69pp |
| High band | +11.38% | +7.64pp |

### Window-frequency claim (replaces annualized target)

**v2 deliverable:** "After full validation completes, the package is expected to **beat SPY in 55–65% of randomly-sampled 30-day windows** in mixed-regime backtest history. No annualized point estimate published until 2,000+ backtest-days completed."

### Conditions for publishing an annualized number (Blocker 4 gate)

Before any annualized SPY+Xpp/yr claim is made:
1. Minimum 2,000 backtest-days of validation history
2. ≥4 distinct regime epochs (bull, bear, chop, crash)
3. ≥12 out-of-sample 30-day windows
4. Parameters fit on first half of history, tested on disjoint second half — no re-fitting
5. Sensitivity sweep at ±100% on every numeric default (not just ±25%)
6. Worst-window drawdown and time-to-recovery reported

## 8. Validation plan (revised — addresses Blockers 1, 2, 4)

| Phase | Test | Pass criterion |
|-------|------|----------------|
| V0 | **Intraday plumbing verification (DONE)** | Verified: strategy reads `close` only. Z3.1 already adjusted. |
| V1 | Re-run 299903 with each zone implemented | Z1+Z2 (bug fixes + observations) alone: P&L > -$50; full package: P&L ≥ +$400 |
| V2 | Z2.1 phase-1 observation: emit "would-block" log entries only | Operator audits which sell-pathways trip the block; whitelist updated; only then phase 2 enforcement |
| V3 | **Sensitivity sweep ±100% on each numeric default** in Z1.1, Z3.1, Z3.2, Z4.2, regime thresholds | P&L doesn't degrade by >50% under any single perturbation; monotonicity check on directional sensitivity |
| V4 | OOS 30-day windows, ≥6 from 2026-Q1/Q2 not used during design | Beats SPY in ≥55% (4 of 6) |
| V5 | Multi-regime walk-forward — bull, chop, drawdown across 2024–2026 | Positive alpha vs SPY in bull AND chop. Bear can be flat (defensive mode). Crash kill-switch fires at expected points without manual intervention. |
| V6 | LLM-OFF stress (force trade-overlay LLM = 0% success) | Phase V1 P&L reproducible within $50 |
| V7 | **Annualized-target gate:** all conditions in Section 7 met | Only then publish SPY+Xpp/yr |

V0-V6 are mandatory before implementation. V7 is the gate to claim an annualized number.

## 9. Implementation order

Phased to allow per-zone validation; rollback after any regression.

1. **Z2.1 phase-1 (log-only) + Z2.2 + Z2.3 + Z4.3 + Z4.4** — pure bug fixes + observation, no parameter tuning. Lowest-regret.
2. **Z1.2 + Z1.3 + Z1.4** — config flips to already-coded gates.
3. **Z1.1 + Z4.2** — percentile/vol-scaled rules; verify monotonicity in V3 before merge.
4. **Z3.1 + Z3.2 + Z3.3** — stop discipline; requires V3 sensitivity sweep to pass first.
5. **Z4.1** — capacity raise; only after Z1-Z3 demonstrate working.
6. **Z2.1 phase-2 (enforcement)** — only after V2 telemetry audit completes.

Re-run 299903 after each phase. Hard-stop and re-evaluate if P&L regresses between phases.

## 10. Open questions remaining

1. What "configured_default" for Z1.1 ceiling is acceptable while waiting for the 3yr cross-sectional distribution to be computed? Proposal: 200% 60d (more conservative than v1's 150%).
2. Z3.1 vol-scaled floor — should "vol" be ATR, close-to-close stddev, or downside-only realized vol? Proposal: 20-day downside realized vol (penalizes losers more than upside vol).
3. Regime detector hysteresis — V31 already exists but how often does it flip? If it flips daily we whipsaw between bull/chop modes. Need to verify flip-frequency in historical data.
4. Bull-mode `max_positions=12` — does the per-position $-size become too small to be meaningful after slippage? At $833 per position with avg liquid spread, slippage could eat 50bps per round-trip.
5. Z2.1 whitelist coverage — initial proposed whitelist: `sell`, `sell_override`, `stop_loss`, `circuit_breaker`, `v11_deep_loser_protect`, `trend_reversal_forced`. Any missing?

## 11. Decision log

| Decision | Resolution | Date |
|----------|-----------|------|
| Tier 2 vs Tier 1/Tier 3 scope | Tier 2 + minimal scoring fallback | 2026-05-15 |
| Asymmetric vs symmetric vs loose stops | Asymmetric | 2026-05-15 |
| max_positions: 8 vs 12 vs 15 | 12 with soft sector cap | 2026-05-15 |
| Include Z5 scoring fallback? | v1: yes; **v2: deferred after adversarial review** | 2026-05-15 |
| Address adversarial blockers before plan? | Yes — Tier 2 scope retained, blockers addressed via vol-scaling + regime gating + deferring Z5 | 2026-05-15 |
