# Design: Winner-depth + propagation-hygiene + basis-aware re-entry (cold-start P&L fix v2)

Date: 2026-05-26
Branch: claude-code-integration
Supersedes the emphasis of: 2026-05-26-discovery-expansion-fix-design.md (which concentrated the buy funnel — shown inert here)

## Problem

Cold-start validation backtest **522929** (kimi-k2.5, 2025-11-10→2026-05-25, $7k) returned **+130.8%** vs the **+152.2% floor** (404780) — the concentrated config *underperformed* by ~21 pts. An 8-agent forensic sweep established the comparison is model-fair (identical resolved LLMs + prompt hashes, same dates/capital), so the gap is config/code.

### Root causes (evidence-ranked)
1. **Under-captured mega-winner SNDK = 86% of the gap (−$1,277).** SanDisk ran $239→$1478 (+517%). Both cold runs were force-sold in mid-Nov by the *same* trend-reversal enforcement (shared, not the differentiator). 522929 then **re-bought at $693** (after the stock tripled) with 57% of its SNDK dollars → blended basis so high it captured only +18.7%, vs the +266% run's +106.7% (94% of dollars in at $227-274).
2. **Reactive late entry → churn.** ~55% of buys are `backfill_rotation` refills; winners (INTC, SIMO, CNTA) entered late/underwater, fall outside the ≥+2% lock, get rotated out. 14 churned-at-a-loss names later ran +40-294%.
3. **Propagation flood.** One HOOD earnings item injects ~93 `COMPETES_WITH` competitors at raw ≈0.54 (the `("COMPETES_WITH","out/in")=(0.40, True)` invert edge, graph_nexus_analysis.py:13709), clearing the 0.50 expansion gate with no per-seed cap → floods `max_propagated_scoring_slots` + the backfill queue; high-conviction names (CELH/MNST/VST) expire unbought. Worse for the narrow config (940 vs 851 `headroom=0`).
4. **Idle-cash drag.** 522929 ran ~75% deployed in the back half vs ~93% for the floor → compounded less. `cash_reserve_floor_pct=0.01`, so NOT a reserve-floor issue — most plausibly a downstream symptom of #3 (queue full of non-converting propagation names).

### Meta-finding (reframes the prior session)
The concentration knobs were **inert**: both runs executed *exactly* 161 buys (~1.2/day, far under either the 8 or 14 daily cap) with near-identical tickets. `max_stock_buys_per_day` / `pool_*` / `allocation_max_new_stock_buys` never bound. The real levers are **winner depth, entry/re-entry quality, and propagation-queue hygiene** — not buy-funnel width.

### ETF / discovery are NOT drivers
ETF exclusion was net −$23; the mom 12→6 cut cost ~$460 (mostly leveraged ETFs). Both are non-causal.

## Authoritative current config (prod doc 179 "Nexus Only", read 2026-05-26)
`winner_add_max_multiple_of_entry=2` (a **notional** size cap = 2× entry $, NOT a price gate — confirmed at graph_nexus_analysis.py:8109-8114), `winner_add_max_drawdown_from_peak_pct=8`, `winner_add_min_pnl_pct=5`, `winner_add_max_count=8`, `winner_add_fraction_of_initial=0.6`, `winner_add_min_notional=400`, `propagation_min_raw_score=0.4`, `propagation_expansion_min_raw_score=0.5`, `max_propagated_scoring_slots=40`, `momentum_discovery_max_per_day=6`, `cash_reserve_floor_pct=0.01`, `backfill_budget_reserve_pct=0.1`.

## Change-set

### A — Config knobs (doc 179 + schema default line 1 + backend/cli.py), low-risk, reversible
| Knob | From | To | Why |
|------|------|----|-----|
| `winner_add_max_multiple_of_entry` | 2 | 3 | let an add reach 3× entry notional → deeper winners |
| `winner_add_max_drawdown_from_peak_pct` | 8 | 10 | volatile winners pull back >8% mid-run; widen the add window modestly |
| `propagation_expansion_min_raw_score` | 0.5 | 0.56 | the HOOD cluster sits at 0.54 — drop the whole band |
| `max_propagated_scoring_slots` | 40 | 20 | matches the code's "realistic fan-out cone" (:8369); shrinks queue pressure |
| `momentum_discovery_max_per_day` | 6 | 12 | cheap discovery insurance (recovers the ~$460) |

### B — Per-seed propagation fan-out cap (code), moderate
New knob `propagation_max_per_seed` (default 8). In the propagation scoring path (`_PROPAGATION` usage, graph_nexus_analysis.py:15777-15918, feeding `propagated`), cap the number of neighbors a single seed (source ticker / news item) contributes to the scored set. Structural fix for the 93-name HOOD flood independent of the score threshold.

### C1 — Basis-aware re-entry guard (code), higher-risk
New knob `reentry_max_premium_over_exit_pct` (default 40). When re-buying a ticker that was previously sold, block/down-weight the buy if current price > (1 + pct/100) × last-exit price. Directly prevents the SNDK-at-$693 re-entry.
- **Design constraint:** the existing re-entry blacklist (:7450) is a *short recent-sell window*; SNDK was re-bought ~5 months later, beyond it. So C1 requires **persistent per-ticker last-exit-price memory** in `strategy_cache`. If that memory is absent on a path, C1 must **fail-open** (allow the buy) — never block a legitimate fresh entry due to missing history.

### C2 — Idle-cash deployment, investigate-then-fix
C2 is NOT a pre-specified code edit. Root-cause the back-half idle cash via the 522929 logs/code (`deployment_ramp_*`, `backfill_budget_reserve_pct`, queue-slot occupancy). Hypothesis: it is downstream of #3, so largely resolved by A+B. Only make a targeted deployment change if a specific gate is identified; otherwise document that A+B covers it. **No speculative change to cash logic.**

## Test plan (TDD, unit-level — backtest deferred per operator choice)
New file `backend/tests/test_winner_depth_propagation_reentry_fix.py`:
- A: effective-config resolves the new defaults; schema/cli/effective-config agree.
- B: a seed with N>cap neighbors contributes exactly `propagation_max_per_seed`; a seed with ≤cap is unchanged; cap disabled (0 / very large) = no-op.
- C1: re-buy blocked when premium > threshold; allowed when ≤ threshold; **fail-open** when no last-exit memory; never affects a never-held ticker.
- Regression guard: existing winner-add gating unchanged for normal adds; `_plan_winner_adds` still honors `max_count`/`min_pnl`/`drop_from_peak`.

## Risk & rollback
- Real-money: doc 179 is shared with nexus-live; changes apply live (not in `live_mode_overrides`). Validation is unit-tests only (no backtest) — explicit operator choice.
- C1/B touch the live buy/propagation path → highest regression risk; both fail-open and are gated by new knobs that can disable them (`propagation_max_per_seed`=very large, `reentry_max_premium_over_exit_pct`=very large → no-op).
- **Rollback (doc 179):** restore `winner_add_max_multiple_of_entry=2`, `winner_add_max_drawdown_from_peak_pct=8`, `propagation_expansion_min_raw_score=0.5`, `max_propagated_scoring_slots=40`, `momentum_discovery_max_per_day=6`, and delete `propagation_max_per_seed` + `reentry_max_premium_over_exit_pct` (code falls back to no-op defaults).
- Baseline pytest = 21 pre-existing failures; success = 0 NEW failures + the new test file green.
