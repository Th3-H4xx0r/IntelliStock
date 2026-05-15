# Nexus Strategy — P&L Maximization Design (Tier 2 + Scoring Fallback)

**Date:** 2026-05-15
**Status:** Design approved by operator (2026-05-15). Pending adversarial review and spec sign-off before plan/implementation.
**Source backtest:** `BacktestResults.id = 299903` (RethinkDB `REDACTED-IP/IntelliStock`)
**Strategy:** `graph_nexus_analysis` (`backend/strategies/graph_nexus_analysis.py`, ~22.5K LOC)
**Benchmark:** SPY (operator-defined: strategy must outperform SPY substantially in both short and long run)

---

## 1. Problem statement

Backtest 299903 ran 2026-04-30 to 2026-05-12 (9 trading days, dual-cadence) with $10,000 initial cash.

- **Strategy final P&L: -1.22% (-$121.76)**
- **SPY same window: +3.74%**
- **Alpha gap vs SPY: -4.96 percentage points**
- **Naive equal-weight buy-and-hold of the strategy's own 11 picks: +6.5% (+$650)**

The discovery layer surfaced a universe that would have beaten SPY by +7.19pp passively. The trading layer converted that into -4.96pp realized — a **-12.15pp self-inflicted alpha destruction.**

## 2. Goals and non-goals

### Goals

1. Convert the existing discovery alpha into realized P&L
2. Block parabolic / FOMO buys at the discovery gate (AIOS-class)
3. Stop "ghost sells" of winners (AMD, SOXL pattern)
4. Cut held losers faster (AORT pattern)
5. Deploy idle cash that is currently held back by compounding rules in rising markets
6. Open meaningful headroom for the backfill queue to drain
7. Restore granularity to the scoring layer so non-graph-touched names can compete for slots

### Non-goals (out of scope for this spec)

- LLM safety net recovery (Claude CLI quota, gpt-5-mini reliability) — operator handles model/effort/provider decisions
- ML model retraining — currently emits constant 0.50/0.50 in 607/607 decisions, operator decides when/whether to retrain
- Discovery-layer redesign (the universe is already producing alpha)
- Active-event maintenance overhaul beyond the work already done in this session
- Live-trading rollout / portfolio migration

## 3. Diagnosed failure modes from the 7-agent forensic audit

Ranked by alpha cost.

| # | Failure | Window cost | Mechanism (file:line) |
|---|---------|-------------|-----------------------|
| 1 | Bimodal scoring — 81 of 92 evaluated symbols sat at `Base=0.00` all run; CHPS +21%, FTXL +20%, TECL +13%, COPZ +25%, TTXU +36%, ATEX +23%, MARS +23% all locked out as "No graph signal" | ~$1,500–3,000 vs ideal top-8 | Scoring produces 1.0 or 0.0; no granular ranking. `graph_nexus_analysis.py:15537` |
| 2 | AIOS FOMO buy at $22.33 after +5,200% YTD parabolic | -$400 | `momentum_watchlist_rotation` lane at `:20979` ignores `raw_net_score=-1.0`; V32 ATH gate at `:4494` defaults OFF; `quality_filter_missing_metadata_policy="warn"` allowed missing market_cap; no parabolic ceiling in `_discover_stocks_from_momentum:9139` |
| 3 | "Ghost sells" of AMD ($354 ⇒ missed $95/share) and SOXL ($130 ⇒ missed $42/share) — `action=sell` while `primary_action_intent=hold`, `override_applied=false` | ~$300–500 opportunity | Out-of-band sell channel (trend-sell queue at `logs.txt:54`) bypasses V31 grace period and override accounting |
| 4 | AORT held through -33% drawdown, sold at -27% | -$198 | Only 2 decision records for AORT in 9 days (held positions not re-scored daily); stop check uses mid-day close not intraday low; `initial_grace_bars=14` too generous |
| 5 | Cash drag (avg 42% idle in +3.74% market) | -$157 | `_compute_macro_risk_scale:7020` haircut fired 20% every bar because `max(0.5, confidence)` floor at `:7038` creates false-bearish on a +3.74% tape; `max_positions=8` with 41 queued names → headroom=1/bar; queue TTL collapsed to 2 bars under dual-cadence (`_scale_bars` at `:16550`); V31 anchor reserve holds 40% of stock budget for candidates that can't qualify in <14d |
| 6 | LLM safety nets OFFLINE the entire run | Unknown but large | Trade-overlay LLM 100% failed (Claude quota), active-event maintenance 83% failed, ML returns 0.50/0.50 in 607/607 records, macro classification stored 0 |
| 7 | Graph peer-inversion bug | ATEX +22.67% missed | `COMPETES_WITH [inv]` produced `raw=-0.488` on stocks that actually rose. Inversion logic systematically miscalibrated |
| 8 | Sector cap blocking positive signals | ALAB, CRWD, NET stuck in queue despite `Base=+1.0` / 22 paths | 40% sector cap hard-blocks even high-conviction names; no priority override |

## 4. Design anchors (operator decisions, 2026-05-15)

1. **Tier 2 scope** — Standard package, 10–15 changes, mix of config flips and targeted code edits
2. **Asymmetric stop discipline** — cut losers fast (intraday-low aware -10% floor), let winners run (12% trailing stop from peak, activated at +5% gain)
3. **Capacity** — `max_positions` 8→12 with soft sector cap (`raw_net_score ≥ 0.8` overrides 40% cap)
4. **Include minimal scoring fallback** — price-momentum baseline (0.10–0.50 percentile-clipped) when `Base=0.00` and `graph_paths=0`, so non-graph-touched names rank above zero

## 5. The 15 interventions

### Zone 1 — Discovery hardening (4 changes)

| # | Change | Type | File:line | Mechanism |
|---|--------|------|-----------|-----------|
| Z1.1 | Add `momentum_discovery_max_20d_return = 50.0` and `momentum_discovery_max_60d_return = 150.0` ceilings to `_discover_stocks_from_momentum`. Mirror existing logic in `_rediscover_momentum_comebacks:9320-9325` | code | `gna.py:9176-9219` | Blocks parabolic candidates at discovery time. AIOS (+5,200% 60d) blocked. |
| Z1.2 | Set `portfolio_swap_ath_gate_enabled = true`, `portfolio_swap_ath_gate_max_pct = 0.05`, `portfolio_swap_ath_gate_bypass_raw = 2.5` | config | `gna.py:4494, :4496` | Hard-blocks buys within 5% of ATH unless extraordinary conviction |
| Z1.3 | Set `momentum_watchlist_mcap_prefilter_enabled = true`, `momentum_watchlist_min_market_cap = 2_000_000_000` | config | `gna.py:4522` | Filters sub-$2B caps from momentum-watchlist lane |
| Z1.4 | Flip `quality_filter_missing_metadata_policy` from `"warn"` → `"block"` in operator config | config | `gna.py:6644, :15686` | Reject buys with missing market_cap metadata instead of logging |

### Zone 2 — Decision plumbing (3 changes)

| # | Change | Type | File:line | Mechanism |
|---|--------|------|-----------|-----------|
| Z2.1 | **Ghost-sell block.** In trade-execution path, refuse to execute a sell when `primary_action_intent != "sell"` AND `override_applied=false`. Force the trend-sell queue to route through the same decision pipeline. | code | trade-execution path (find via grep `primary_action_intent` and the trend-sell-list at `logs.txt:54`) | Stops AMD/SOXL/AORT ghost sells |
| Z2.2 | **Daily re-eval of held positions.** Every held symbol must generate a non-empty decision record each day. Inject held-position list before scoring. | code | `_score_symbols` / `_finalize_scores` near `gna.py:15537` | Held losers (AORT) get continuous evaluation; intraday-low stops can fire |
| Z2.3 | **Fix macro-haircut false-positive.** Remove `max(0.5, confidence)` floor at `:7038, :7045`. Add price-confirmation gate: only apply `scale < 1.0` when `net_score < 0` AND SPY 20-day return < 0. | code | `_compute_macro_risk_scale:7020`, `:7038`, `:7045` | 20% haircut stops firing on +3.74% markets. Recovers ~$859/bar of buy budget |

### Zone 3 — Asymmetric stop discipline (3 changes)

| # | Change | Type | File:line | Mechanism |
|---|--------|------|-----------|-----------|
| Z3.1 | Tighten loss floor from -15% → -10%, evaluate against intraday daily-low rather than mid-day close | code | `gna.py:13575` (`_unrealized_pct` calc + caller) | AORT at intraday low -33% triggers cut, saves ~$98 |
| Z3.2 | Earlier trailing-stop activation: `trailing_stop_activation_pct` 15.0 → 5.0 | config | `gna.py:13658, :13536` | Trailing stop active once winner gains 5%; locks gains without selling on first wobble |
| Z3.3 | Tighten `sell_threshold` -0.30 → -0.45 AND raise `winner_sell_protection_min_pnl_pct` to 5.0 (already coded path, requires explicit setting) | config | `gna.py:15447, :11409` | Once at +5% gain, winners only sold by trailing stop or `raw ≤ -0.45`. AMD/SOXL hold through full runs |

### Zone 4 — Capacity expansion (4 changes)

| # | Change | Type | File:line | Mechanism |
|---|--------|------|-----------|-----------|
| Z4.1 | `max_positions` 8 → 12; `max_stock_buys_per_day` 8 → 10 | config | `schema.json:282`, `gna.py:6655` | Headroom 1/bar → 4/bar. Queue drains. |
| Z4.2 | **Soft sector cap with priority override.** 40% sector cap bypass when `raw_net_score ≥ 0.8 AND graph_paths ≥ 5` | code | sector-cap log emitter (grep `SECTOR_CAP exceeded`) | ALAB / CRWD / NET execute when high-conviction |
| Z4.3 | **Fix queue TTL collapse under dual-cadence.** Remove `_scale_bars` from `_bfq_max_bars` OR set explicit `backfill_queue_max_bars >= 5` | code | `gna.py:16550, :16554` | Queue items survive >2 bars |
| Z4.4 | **Release dead reserves.** When V31 anchor reinforcement has 0 qualifying candidates, return 40% reserve to `_stock_budget_available`. Same for backfill reserve when queue empty post-expiry-sweep | code | `gna.py:20429-20455`, `:5844-5876` | Adds $1,100–1,800/bar of newly-spendable budget |

### Zone 5 — Minimal scoring fallback (1 change)

| # | Change | Type | File:line | Mechanism |
|---|--------|------|-----------|-----------|
| Z5.1 | **Price-momentum baseline when graph-signal absent.** In `_finalize_scores`: when `Base==0.0` and `graph_paths==0`, set `score = clip(20d_return_percentile_within_universe, 0.10, 0.50)`. Floor keeps non-momentum names from going to zero; cap keeps them below any genuine graph-signal name. | code | `gna.py:15537` (~50–100 LOC change) | CHPS, FTXL, TECL, COPZ, TTXU, ATEX rank above zero and can win slots. Largest single P&L lever. |

### Total: 15 changes, ~250–400 LOC, roughly 60% config / 40% code

## 6. P&L estimate band

### Per-zone in-window contribution ($10K base, 2026-04-30 → 2026-05-12)

| Zone | Low | Expected | High | Confidence |
|------|----:|---------:|-----:|:----------:|
| Z1 — Discovery hardening | +$150 | **+$400** | +$550 | High |
| Z2 — Decision plumbing | +$100 | **+$350** | +$700 | Medium-High |
| Z3 — Asymmetric stops | +$50 | **+$300** | +$650 | Medium (depends on intraday bar availability) |
| Z4 — Capacity | +$50 | **+$200** | +$500 | Medium (depends on Z5 working) |
| Z5 — Scoring fallback | +$200 | **+$800** | +$1,800 | Low-Medium (largest expected contributor, largest uncertainty) |
| **Sum (additive, naive)** | **+$550** | **+$2,050** | **+$4,200** | |
| **After -25% non-linearity haircut** | **+$410** | **+$1,540** | **+$3,150** | |

### Alpha vs SPY (window)

| Scenario | Strategy return | Alpha vs SPY (+3.74%) |
|----------|----------------:|----------------------:|
| Current 299903 (baseline) | -1.22% | -4.96pp |
| Low band post-package | +2.88% | -0.86pp |
| Expected | +14.18% | +10.44pp |
| High band | +30.28% | +26.54pp |

### Annualized target

A 9-day window cannot be naively annualized. After regime mixing and conservative haircuts:

**Published target: SPY + 6 to 12 percentage points per year.**

This assumes:
- LLM safety nets recover to ≥80% success rate
- Sensitivity sweep does not reveal overfitting in Z1.1, Z3.1, Z5.1 numeric parameters
- Simulator feeds intraday bars to make Z3.1 effective
- Strategy is run across mixed regimes (bull, chop, drawdown), not cherry-picked windows

### Conditions that invalidate the estimate

1. LLM signal quality stays depressed (Claude quota / ML stays untrained) — alpha caps at SPY +2–4pp/yr
2. 299903's bull-tape regime is unrepresentative — alpha collapses or goes negative in chop / drawdown
3. Z5 scoring fallback overfits the 0.10/0.50 percentile bounds — alpha shrinks toward strict Tier 2 (+3–5pp)
4. Z4 capacity expansion fills slots with mediocre names in thin markets
5. Simulator only feeds mid-day closes — Z3.1 becomes a no-op

## 7. Validation plan

| Phase | Test | Pass criterion |
|-------|------|----------------|
| 1 | Re-run backtest 299903 (same window) after each zone implemented | P&L > -$50 after Z1+Z2; > +$500 after all 5 zones |
| 2 | Out-of-sample window from 2026-Q1 (operator-selected, not used during design) | Beats SPY by ≥ +1pp in window |
| 3 | Walk-forward across 3 regimes (bull, chop, drawdown) | Positive alpha vs SPY in ≥ 2 of 3; max drawdown ≤ 15% in worst regime |
| 4 | Configuration sensitivity sweep — vary new numeric knobs by ±25% | P&L doesn't degrade > 30% from any single perturbation. Catches overfitting in Z1.1, Z3.1, Z5.1 |
| 5 | LLM-OFF stress test (force trade-overlay LLM to 0% success) | P&L from Phase 1 reproducible with LLM unavailable |

## 8. Adversarial review hooks (must answer before implementation)

1. **Overfitting risk** — Z1.1 ceilings (50%/150%), Z3.1 floor (-10%), Z5.1 bounds (0.10/0.50): were these cherry-picked? Rationale that generalizes?
2. **Regime dependence** — package built off one favorable bull-tape (SPY +3.74% / 9d). What breaks in -20% drawdown, sideways chop, 2022-style bear?
3. **Lookahead / simulator artifacts** — Z3.1 daily-low intraday requires the simulator to feed intraday bars. Does it?
4. **Plumbing assumptions** — Z2.1 assumes a single trade-execution path where the intent-vs-action gate can be inserted. What if there are multiple paths or legitimate stop-loss sells use a different code path?
5. **Capacity inflation** — Z4.1 raises `max_positions`. Each position drops from ~12% of capital to ~8%. Does the strategy still have edge at lower per-position size?
6. **Score fallback bias** — Z5.1 momentum baseline is essentially retail "buy what's going up." Does it introduce momentum-crash exposure?
7. **The "fix would have prevented X" trap** — every per-trade savings number assumes the rest of the trades are unchanged. Combined effects can be non-linear: blocking AIOS frees $1,220 → that money goes somewhere else → has its own outcome (positive or negative).

## 9. Implementation order (recommended)

Phased to allow per-zone validation:

1. **Z2.1 + Z2.2** first — the ghost-sell block and held-position re-eval are bug fixes, no parameter tuning involved. Lowest-regret changes.
2. **Z1.1–Z1.4** — discovery hardening, all config flips or single-rule additions
3. **Z2.3** — macro-haircut bug fix (single-function edit)
4. **Z3.1 + Z3.2 + Z3.3** — stop discipline, requires intraday-bar plumbing check first
5. **Z4.1–Z4.4** — capacity changes, validate Z1-Z3 working before opening capacity
6. **Z5.1** — scoring fallback last, highest design risk, largest potential reward. Run sensitivity sweep before merging.

Re-run 299903 after each phase. Hard-stop and re-evaluate if P&L regresses between phases.

## 10. Open questions for adversarial review

- What's the rationale for picking the specific 50%/150% / -10% / 0.10–0.50 numeric values in Z1.1, Z3.1, Z5.1? Are they tied to a market property or just fitted to 299903?
- Should Z2.1 ghost-sell block log instead of refuse, for one round of validation, so we can audit what gets blocked before turning it into an enforcement gate?
- Should Z5.1 momentum baseline use 20d return or a longer/shorter lookback? Is 20d a free parameter that should also be in the sensitivity sweep?
- Should the per-position sizing scale up when `max_positions` is raised (to keep dollar exposure constant) or stay at current per-position allocation logic?
- Should Z4.2 soft sector cap apply across ALL sectors uniformly, or should some sectors (energy, materials) keep harder caps due to higher correlation risk?
