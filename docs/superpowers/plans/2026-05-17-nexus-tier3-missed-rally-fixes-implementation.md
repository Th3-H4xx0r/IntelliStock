# Nexus Tier-3 — Implementation Plan (Missed-Rally Fixes)

**Spec**: `docs/superpowers/specs/2026-05-17-nexus-tier3-missed-rally-fixes-design.md`
**Branch**: `claude-code-integration`
**Phases**: 4 (P1, P2a, P2b, P3), each in its own commit

---

## Phase 1 — Foundation (commit 1)

### A1: Conviction-aware loss floor matrix
**Files**: `backend/strategies/graph_nexus_analysis.py`

1. Add helpers (after existing `_realized_vol_20d`, ~line 5500):
   - `_resolve_position_market_cap(sym, strategy_cache, portfolio_emulator) -> float | None`
   - `_resolve_conviction_tier_at_exit(sym, config, strategy_cache, propagated) -> str` returns `"HIGH"`/`"MID"`/`"LOW"`
   - `_get_conviction_aware_floor(tier, config) -> tuple[float, float]` returns (floor_pct, vol_mult)
2. Modify `_evaluate_position_risk` circuit breaker block (line 13822-13849):
   - Replace `_max_open_loss_pct = float(config.get("max_open_loss_pct", -15.0))` with tier-resolved floor + vol_mult
   - Apply `_z31_vol_mult = 0` for HIGH tier (absolute floor only)
   - Apply low-vol disable: if `_z31_vol < threshold`, skip vol scaling
3. Add config knobs (read in 3 places per spec):
   - `circuit_breaker_floor_high_conviction_pct` default -25
   - `circuit_breaker_floor_mid_conviction_pct` default -20
   - `circuit_breaker_floor_low_conviction_pct` default -15
   - `circuit_breaker_high_conviction_mcap_threshold_usd` default 50e9
   - `circuit_breaker_high_conviction_raw_score_threshold` default 1.5
   - `circuit_breaker_mid_conviction_raw_score_threshold` default 0.5
   - `circuit_breaker_low_vol_disable_threshold_pct` default 0.08 (8%)

### B1: Account-size-scaled cash reserve
**Files**: `backend/strategies/graph_nexus_analysis.py` (line 7009)

1. Helper: `_get_scaled_cash_reserve_floor_pct(config, initial_value) -> float`
2. Replace constant `cash_reserve_floor_pct=0.10` with scaled formula:
   - equity < threshold_small → small_pct (0.05)
   - threshold_small <= equity < threshold_mid → mid_pct (0.075)
   - equity >= threshold_mid → large_pct (0.10)
3. Surface 5 new knobs in `_get_effective_nexus_config`

### B3: Relaxed rotation thresholds
**Files**: `backend/strategies/graph_nexus_analysis.py`

Modify defaults at:
- Line 5682 / 6883: `rotation_winner_lock_min_hold_days` 5 → 3
- Line 5683 / 6884: `rotation_winner_lock_min_pnl_pct` 3.0 → 2.0
- Line 6345 / 6879: `rotation_profitable_min_delta` 1.50 → 1.0
- Line 6351 / 6896: `rotation_break_glass_delta` 2.25 → 1.5

### Tests
**New file**: `backend/tests/test_nexus_tier3_phase1.py`

- `test_conviction_tier_resolution_mcap_high`
- `test_conviction_tier_resolution_raw_score_high`
- `test_conviction_tier_resolution_mid`
- `test_conviction_tier_resolution_low_default`
- `test_conviction_aware_floor_mapping`
- `test_cash_reserve_floor_small_account` (< $50K → 5%)
- `test_cash_reserve_floor_mid_account` ($50K-$200K → 7.5%)
- `test_cash_reserve_floor_large_account` (>= $200K → 10%)
- `test_rotation_defaults_relaxed`
- `test_circuit_breaker_high_conviction_holds_through_sndk_scenario` (regression: -18% on mega-cap held; -54% on low-conv still cut)
- `test_circuit_breaker_low_conviction_still_fires_at_15pct` (CAR regression)

### Commit 1 message:
`feat(nexus/tier3-phase1): conviction-aware floor + scaled cash reserve + relaxed rotation`

---

## Phase 2a — Allocation + Macro Override (commit 2)

### A3: Macro signals supersede sentiment veto
**File**: `backend/strategies/graph_nexus_analysis.py` (line 18820-18843)

1. Add module-level constant `_MACRO_OVERRIDE_REASON_KEYWORDS = ("S&P 500", "index", "buyout", "m&a", "merger", "insider", "Strong Benzinga")`
2. In the loop that cancels pending trades, before applying the cancellation, check if `trade.get("reason")` matches any keyword → skip cancellation, log "macro signal supersedes sentiment".

### B2: Raise `allocation_max_new_stock_buys`
**File**: `backend/strategies/graph_nexus_analysis.py` (line 6522, 6911)

1. Add helper `_get_scaled_max_new_stock_buys(config, initial_value) -> int`
2. Defaults: small (<$50K) → 6, mid ($50K-$500K) → 8, large (>=$500K) → 12

### B4: Tiered momentum budget
**File**: `backend/strategies/graph_nexus_analysis.py` (allocation logic)

1. Add config: `momentum_dedicated_slots_per_cycle` default 2; `momentum_dedicated_budget_pct` default 0.15
2. In allocation logic, reserve N slots that only momentum candidates can fill; flow back to propagation if no qualified momentum candidate

### B5: Extend backfill queue grace + size
**File**: `backend/strategies/graph_nexus_analysis.py` (line 6874, 6877)

1. Defaults: `backfill_queue_grace_bars` 3 → 7; `backfill_queue_max_size` 30 → 50

### Tests
**New file**: `backend/tests/test_nexus_tier3_phase2a.py`

- `test_macro_override_keyword_match`
- `test_macro_override_skips_sentiment_cancel`
- `test_macro_override_non_macro_reason_still_cancels`
- `test_scaled_max_new_stock_buys_small_account`
- `test_backfill_queue_extended_grace`

### Commit 2 message:
`feat(nexus/tier3-phase2a): macro override + scaled allocation + tiered momentum + extended queue`

---

## Phase 2b — Schema-extending: post_sell_watch + regime floors (commit 3)

### A4: Post-sell active re-entry monitoring
**Files**: `backend/strategies/graph_nexus_analysis.py`

1. Extend `_mark_discovered_stock_sold` (line 9707): accept `forced_exit: bool` kwarg; if True, also write `exit_price`, `exit_date`, `entry_conviction_tier`, and set status to `"post_sell_watch"` (else status="sold" as before).
2. New helper: `_get_post_sell_watch_candidates(conn, instance_id, date_key, window_days=60)` returns list of (ticker, exit_price, exit_date, entry_conviction_tier).
3. New helper: `_evaluate_post_sell_reentry(ticker, current_price, exit_price, propagated, config)` returns (re_entry: bool, reason: str).
4. New helper: `_mark_discovered_stock_re_entered(conn, instance_id, ticker, re_entry_date)`.
5. New helper: `_mark_discovered_stock_forgotten(conn, instance_id, ticker)` for TTL cleanup.
6. In the main `run_once` (around the FULL cycle), add a section that queries post_sell_watch candidates and triggers re-entry events (write to `_pending_buys` or scoring metadata).
7. Update `_get_recently_sold_discovered_tickers` filter to include `status in ("sold", "post_sell_watch")` so cooldown still applies during watch window.

Config knobs (defaults):
- `post_sell_watch_enabled` default True
- `post_sell_watch_window_days` default 60
- `post_sell_recovery_threshold_pct` default 0.05
- `post_sell_resistance_break_pct` default 0.005
- `post_sell_resistance_lookback_days` default 10
- `post_sell_reentry_min_raw_score` default 0.40
- `post_sell_reentry_size_fraction` default 0.50

### A2: Regime-gated floor adjustment
**Files**: `backend/strategies/graph_nexus_analysis.py` (combine with A1)

1. In `_get_conviction_aware_floor`, accept `regime: str` parameter and add adjustment:
   - bull: +5pp
   - chop: 0
   - bear: -5pp
   - crash: emergency-exit (return -1.0 effectively forcing sell)
2. Config knobs:
   - `circuit_breaker_regime_adjustment_bull_pp` default 5
   - `circuit_breaker_regime_adjustment_chop_pp` default 0
   - `circuit_breaker_regime_adjustment_bear_pp` default -5

### Tests
**New file**: `backend/tests/test_nexus_tier3_phase2b.py`

- `test_post_sell_watch_status_written_on_forced_exit`
- `test_post_sell_watch_not_written_on_normal_exit`
- `test_post_sell_recovery_threshold_triggers_reentry`
- `test_post_sell_resistance_break_triggers_reentry`
- `test_post_sell_reentry_requires_fresh_score`
- `test_post_sell_window_ttl_marks_forgotten`
- `test_regime_floor_bull_widens`
- `test_regime_floor_bear_tightens`
- `test_regime_floor_crash_emergency_exit`

### Commit 3 message:
`feat(nexus/tier3-phase2b): post_sell_watch re-entry + regime-gated floors`

---

## Phase 3 — Telemetry + Threshold tweak (commit 4)

### A5: Lower V31.4 post-sell breakout cooldown lift threshold
1. Find the V31.4 code (search for "V31.4 post-sell breakout" or "lift cooldown")
2. Replace threshold (currently ~40% above sell price) with config `post_sell_cooldown_lift_threshold_pct` default 0.10 (10%)

### Conviction-score telemetry
1. In `_evaluate_position_risk`, compute and log `conviction_score` at exit-time alongside tier resolution
2. Add to position metadata in cache `_nexus_conviction_telemetry`: per-symbol score history
3. Surface in `_get_effective_nexus_config`

### Tests
**New file**: `backend/tests/test_nexus_tier3_phase3.py`

- `test_v31_4_threshold_lower_default`
- `test_conviction_score_telemetry_logged`

### Commit 4 message:
`feat(nexus/tier3-phase3): V31.4 threshold lower + conviction telemetry`

---

## Bug sweep (parallel agents)

After commit 4, dispatch 5+ parallel agents to audit:
1. **AST + import** — verify no syntax errors, all helpers properly defined
2. **Test coverage** — verify each phase has the unit tests claimed in this plan
3. **`_get_effective_nexus_config` completeness** — verify all 20+ new knobs are surfaced
4. **Mode safety** — verify all new logic respects `mode == MODE_LIVE` vs `MODE_BACKTEST` where relevant
5. **Migration-reset list** — flag any new cache keys (e.g. `_nexus_conviction_telemetry`) that should be added to broker.py's `_migration_reset_keys`
6. **Edge cases** — sym not in `_yf_market_cap_cache`, propagated empty, strategy_cache None, regime missing, post_sell_watch row has missing fields

Address findings in a fixup commit before push.

---

## Push

1. `git log --oneline` to verify commits stacked correctly
2. `git push origin claude-code-integration` (no force, no merge to main)
3. Run `npx gitnexus analyze --embeddings` in background to refresh index
4. Report URLs / commit SHAs in final summary
