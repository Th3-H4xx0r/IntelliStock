# Bull-Alpha Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Raise the bull-window backtest above SPY by a wide margin without degrading the validated bear-window mechanics, by fixing the mechanics that lost money (unreachable entry gate, false-bear staleness, min-position under-sizing).

**Architecture:** All new behavior is config-gated with defaults that reproduce today's behavior exactly, so deploying the code is a no-op until the doc-179 patch turns keys on. Verification is paired bear+bull backtests on alpaca-main after deploy. Design: `docs/superpowers/specs/2026-07-20-bull-alpha-optimization-design.md`.

**Tech Stack:** Python 3, pytest, backend/strategies/graph_nexus_analysis.py, backend/broker.py.

## Global Constraints

- Every new config key defaults to a value that reproduces current behavior (0/off/neutral). No behavior change without an explicit doc-179 config value.
- No key in the DO-NOT-TOUCH list changes: `regime_bear_spy_drawdown_pct`, `max_positions_bear`, SQQQ bear leg keys, `bear_entry_rs_filter_enabled`, `rotation_lanes_regime_gated`, episode-latch.
- No lookahead: any bars fallback must be filtered to `<= date_key` (overlay cache holds full/future history).
- Run `mcp__gitnexus__impact` (upstream) on each edited symbol before editing; warn on HIGH/CRITICAL.
- `python3` for tests; the suite is 2,688 tests and must stay green.

---

### Task 1: Extension-gate reachability (Phase 1)

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` — add `_resolve_asof_bars` helper near `_recent_runup_protect` (:7397); extend `_v32_momentum_ath_or_mcap_block` (:4971) signature with `date_key`; update 3 callers (:24593, :26144, :26366); extend `_apply_quality_filter` (:19977) signature with `date_key` and its extension site (:20034); scale lookback via `_scale_bars` at both gate sites.
- Test: `backend/tests/test_extension_gate_reachability.py` (new).

**Interfaces:**
- Produces: `_resolve_asof_bars(sym, price_history, strategy_cache, date_key=None, min_bars=2) -> list[dict]` — returns as-of-correct bars, preferring `price_history[sym]`, else `strategy_cache["_overlay_bars_raw"][sym]` filtered to `<= date_key`.
- `_v32_momentum_ath_or_mcap_block(..., lane="momentum", date_key=None)` — new trailing kw-arg (backward-compatible).

- [ ] **Step 1: Write failing tests** — gate fires via overlay fallback when broker `data` empty; no-lookahead (future bars filtered by date_key); fail-closed when `entry_extension_require_bars` set and no bars; lookback scaled at 1h.
- [ ] **Step 2: Run to verify fail** (`_resolve_asof_bars` undefined).
- [ ] **Step 3: Implement** `_resolve_asof_bars`; use it in both gate sites; `_scale_bars(entry_extension_lookback_bars)`; add `entry_extension_require_bars` (default False) fail-closed branch.
- [ ] **Step 4: Run tests → pass.**
- [ ] **Step 5: Commit.**

### Task 2: Stale-bear-trigger recovery guard (Phase 3)

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` — `_detect_market_regime` ret20-bear branch (:6165-6169).
- Test: `backend/tests/test_regime_stale_bear_guard.py` (new).

**Interfaces:**
- Produces: config key `regime_bear_stale_recovery_pct` (default 0.0 = off). When >0 and the recent window's recovery fraction `(current - lo)/(hi - lo)` over the 21-bar window ≥ the key (as a fraction, e.g. 0.5), the **ret20-based** bear is suppressed (falls through to bull/chop). The `current < ma_200` structural-bear branch is UNCHANGED.

- [ ] **Step 1: Write failing tests** — still-falling series (current≈low) → bear preserved (guard silent); recovered series (current near high, ret20 still <−pct) → bear suppressed → chop/bull; key=0 → identical to today.
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** the additive guard on the ret20 branch only.
- [ ] **Step 4: Run tests → pass.**
- [ ] **Step 5: Commit.**

### Task 3: Config patch + conviction sizing (Phase 2, config-only)

**Files:**
- Create: `scripts/doc179_patch_bull_alpha_v1.json` — fullstack-validation keys + new keys: `entry_extension_require_bars: true`, `regime_bear_stale_recovery_pct: 0.5`, `slot_min_notional_pct` (conviction floor ≈7%), `priority_min_position_size` raised, `backfill_queue_reserved_priority_slots` tuned.

- [ ] **Step 1:** Copy `doc179_patch_fullstack_validation.json`, add the new keys.
- [ ] **Step 2:** Commit.

### Task 4: Verify suite + bug-sweep

- [ ] Run the crypto/strategy/regime test subsets touching the edited functions, then the full suite; confirm green.
- [ ] Self bug-sweep the diff (lookahead, None-handling, backward-compat of new kw-args).
- [ ] Commit any fixes.

### Task 5: Push + deploy + paired backtests

- [ ] Push branch `feat/controlled-benchmark-alpha`.
- [ ] Wait 3 minutes for deploy.
- [ ] Apply `doc179_patch_bull_alpha_v1.json` via `scripts/apply_doc179_config_patch_api.py`; **verify it stuck (GET /strategies/179)**.
- [ ] POST bear backtest (2026-03-02→03-30) and bull backtest (2026-03-30→04-27), granularity 3600, $6000, seed 0.
- [ ] Poll to completion; pull logs; confirm bear ≥ +2.29%−0.5pp (protective mechanisms visible) and bull > +6.60% (extension gate observed firing).
- [ ] Revert doc-179 to bear-safe baseline; confirm live wake stays OFF.

## Self-Review

- Spec coverage: Phase 1 → Task 1; Phase 3 → Task 2; Phase 2 (sizing, config-scoped) → Task 3; validation → Tasks 4–5. Phase 2 BFQ-displacement deliberately descoped to config-tuning (bear-leak risk on chop-in-bear rotation path) — documented deviation.
- No placeholders; exact line anchors given.
- Type consistency: `_resolve_asof_bars` signature identical across tasks; `date_key` kw-arg default None everywhere.
