# Regime Safety Implementation Plan (spec: 2026-07-19-bear-neutral-regime-safety-design.md)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline execution this session — no subagents per user usage constraint). Steps use checkbox syntax.

**Goal:** Make the nexus engine regime-aware for real (fix the data-starved classifier), regime-safe (hysteresis + profiles + circuit), and validate bear>0 / neutral≥0 / bull≥SPY.

**Architecture:** All fixes land in `backend/strategies/graph_nexus_analysis.py` (cache layer, detector, hysteresis, profiles, circuit tiers) and `backend/broker.py` (sleeve). Config-gated with safe defaults. One prod-DB cache purge + one config revert (doc-179) via scripts.

**Tech Stack:** Python 3 backend, RethinkDB (direct via .env creds), pytest (`backend/tests/`, run with `python3 -m pytest`), faithful validation via the deployed backtest API.

## Global Constraints
- Every symbol edit preceded by `gitnexus_impact` (CLAUDE.md); `detect_changes` before each commit.
- No Workflow/subagent spend. Real-money/prod-DB actions: doc-179 revert + cache purge (user pre-authorized "fix everything").
- New behavior default-ON only where the spec says (fail-safe chop, cache hardening); aggressive tiers config-gated.
- Detector fail-safe default: `regime_blind_fallback` = "chop" (config-overridable to "bull" for live cold-start preference).

---

### Task 0: Phase 0 — revert doc-179 to bear-safe baseline
- [ ] `python3 scripts/apply_doc179_bull_participation_levers.py --revert scripts/doc179_backup_phase2_keys_20260719T070606Z.json`
- [ ] `python3 scripts/apply_doc179_bull_participation_levers.py --revert scripts/doc179_backup_bull_levers_20260719T070025Z.json`
- [ ] Verify: read doc 179 config; expect `max_positions=8`, no `max_positions_*`, `etf_portfolio_pct=0.05`, `deployment_bar1_cap_pct=0.8`, `fast_loser_cut_recent_runup_block_pct=40`, `quality_filter_missing_metadata_policy="warn"`, and NO `entry_extension_*` / `fast_loser_cut_min_hold_days` / `residual_sleeve_*` keys.

### Task 1: Phase 1a — purge poisoned overlay-bars rows
- [ ] Create `scripts/purge_overlay_bars_cache.py`: connects like apply-script `_conn()`; deletes rows from `GraphNexusOverlayBarsCache` where `bars` is empty OR first bar date > "2026-01-01" (truncated rows); `--dry-run` default, `--apply` to run; prints per-row (id, n_bars, first, last).
- [ ] Dry-run, review list (expect SPY + the 53-bar benchmark rows; VOO survives), then `--apply`.
- [ ] Commit script.

### Task 2: Phase 1b — cache layer hardening (`_overlay_bars_cache_set/get`, `_ensure_overlay_bars_cached`)
**Files:** Modify `backend/strategies/graph_nexus_analysis.py:17873-18013`. Test: `backend/tests/test_regime_data_layer.py` (new).
**Produces:** `_overlay_bars_cache_get(conn, symbol) -> dict|None` now returns the whole doc (bars + coverage) — update its two call sites; `_ensure_overlay_bars_cached` gains start-coverage refetch.
- [ ] Write failing tests (monkeypatched `_r`/conn stubs + in-memory table dict):
  - `test_cache_set_skips_empty_bars` — set with `[]` writes nothing.
  - `test_cache_set_stores_coverage` — doc carries `fetch_start`/`fetch_end`.
  - `test_ensure_refetches_when_row_starts_too_late` — cached row `fetch_start="2026-04-30"`, requested range starts `2025-12-14` → symbol lands in refetch batch.
  - `test_ensure_treats_empty_row_as_miss`.
- [ ] Run: `python3 -m pytest backend/tests/test_regime_data_layer.py -v` → FAIL.
- [ ] Implement: set() early-returns on empty bars and stamps `fetch_start`/`fetch_end` (accepts optional args, default from row bars); get() returns full doc; ensure(): `rdb_bars=doc.get("bars")`, empty→miss; start check: `doc_start = doc.get("fetch_start") or first-bar-date`; if `doc_start > fetch_start + 7d grace` → still_missing.
- [ ] Tests PASS → commit.

### Task 3: Phase 1c — detector fix (`_detect_market_regime`)
**Files:** Modify `graph_nexus_analysis.py:6029-6099` + caller `~22380` (pass `data`). Test: `backend/tests/test_regime_detector.py` (new).
**Produces:** `_detect_market_regime(strategy_cache, config, date_key, data=None) -> str`; stores `strategy_cache["_market_regime_diag"] = {"proxy","closes","ret20","raw"}`.
- [ ] Failing tests:
  - `test_proxy_skips_unusable_bars` — QQQ bars all AFTER date_key, VOO has 60 closes before → uses VOO (returns non-fallback regime).
  - `test_blind_falls_back_to_chop_not_bull` — empty cache, no data → "chop" (default config).
  - `test_blind_fallback_config_override` — `regime_blind_fallback="bull"` honored.
  - `test_data_param_fallback` — cache empty but `data={"SPY": {"bars":[...60 daily...]}}` → computes real regime.
  - `test_bear_on_20d_drawdown` — closes engineered ret20=−6% → "bear".
- [ ] Run → FAIL. Implement:
  - Usable-proxy selection: for each of (SPY, QQQ, VOO): filter ≤ date_key, count valid closes; pick first with ≥21.
  - If none: build daily closes from `data` bars for the same proxies (last close per date ≤ date_key); ≥21 → use.
  - Else: `fb = str(config.get("regime_blind_fallback", "chop"))`; loud `_log(..., "red")` once per date_key (throttle key in strategy_cache).
  - Always stash `_market_regime_diag`; extend the caller's regime log line with proxy/ret20.
- [ ] Tests PASS; also run `python3 -m pytest backend/tests/test_nexus_fixes.py -k regime -v` (existing suite guard). Commit.

### Task 4: Phase 2 — asymmetric hysteresis at the call site (~22380)
**Files:** Modify caller block; new helper `_apply_regime_hysteresis(strategy_cache, raw, config) -> str`. Test: `backend/tests/test_regime_hysteresis.py` (new).
**Produces:** `strategy_cache["_market_regime"]` = smoothed regime; raw kept in diag.
- [ ] Failing tests: rank bull=3, chop=2, bear=1, crash=0. Downgrade applies immediately; upgrade needs K consecutive raws (`regime_upgrade_confirm_bars`, default 3); first-ever call seeds directly (cold start); flapping seq `bull,chop,bull,chop` yields `bull,chop,chop,chop`; `bear,chop,chop,chop` yields `bear,bear,bear,chop`.
- [ ] Implement state in `strategy_cache["_regime_hyst"] = {"cur","pend","n"}`; wire: `_v31_regime = _apply_regime_hysteresis(strategy_cache, _detect_market_regime(...), config)`.
- [ ] Tests PASS → commit.

### Task 5: Phase 1d — sweep `or "bull"` fallbacks
**Files:** `graph_nexus_analysis.py` ~5964, 7362, 17560 (grace-period regime reads) → `or "chop"`.
- [ ] `gitnexus_impact` on `_in_initial_grace_period` consumers; edit the three sites; run `python3 -m pytest backend/tests/test_nexus_fixes.py -v` (full file) → PASS → commit.

### Task 6: Phase 3 — regime profiles
**Files:** `graph_nexus_analysis.py` momentum lanes ~25739/25953 (+ backfill_rotation ~26932), ramp use site, Z4.1 (crash cap already 0). Test: `backend/tests/test_regime_profiles.py` (new, targeted helper-level).
- [ ] New helper `_rotation_lane_allowed(strategy_cache, config) -> bool`: False when smoothed regime in ("bear","crash") and `rotation_lanes_bull_only_outside` (default True... name: `rotation_lanes_regime_gated`, default True); in "chop" lanes allowed only if `held < cap` (no swap-in over cap): implement as `_rotation_lane_allowed(regime, held, cap, config)`.
- [ ] Wire into the three lane gates (they already have "lane gate" blocks — add regime/cap condition, log `ROTATION lane blocked: regime=<r>`).
- [ ] Ramp regime-scale: where `_get_deployment_ramp_caps` is consumed, if regime=="chop" multiply caps by `deployment_ramp_chop_scale` (default 0.6, min bar1 ≤0.6); bear/crash: new-entry paths already capped by Z4.1 (bear entries allowed within cap 8 — baseline behavior preserved).
- [ ] Tests (helper-level truth table) PASS → commit.

### Task 7: Phase 4 — drawdown circuit tiers (extend existing `portfolio_drawdown_halt`)
**Files:** `graph_nexus_analysis.py` (halt logic ~7596 + consumers). Test: `backend/tests/test_drawdown_circuit.py` (new).
**Design:** rolling 20-session NAV high in `strategy_cache["_dd_high"]`; tiers: soft `portfolio_dd_soft_pct=5` (block NEW buys incl. lanes; requires `spy_20d<0` corroboration), hard `portfolio_dd_hard_pct=9` (soft + tighten fast-cut floor to `portfolio_dd_hard_cut_floor_pct=-7`), kill `portfolio_dd_kill_pct=12` (emit sell for all held + halt; loud log; live also fires the existing notification path if present, else log-only). Enabled via `drawdown_circuit_enabled` (default True — validated in backtests before any live redeploy; doc-179 live config reverted to baseline anyway).
- [ ] Failing tests: tier resolution from (nav_series, spy20) truth table incl. no-corroboration soft suppress; hard floor override propagates to the sell-gate floor helper; kill emits sells.
- [ ] Implement helper `_drawdown_circuit_tier(strategy_cache, nav, spy_20d, config) -> str|None` + wire: buy-block flag read where Z4.1 breach blocks buys; floor override read at circuit-breaker floor computation; kill loop marks held symbols sell (reason "Drawdown circuit kill").
- [ ] Tests PASS → commit.

### Task 8: Phase 5 — sleeve hysteresis fix (`backend/broker.py:2632-2708`)
Test: `backend/tests/test_residual_sleeve.py` (extend if exists, else new; stub emulator).
- [ ] Failing tests: park floor = `max(buffer_pct, release_cash_pct + buffer_pct)` (post-park cash ≥ release threshold → no immediate release); release is PARTIAL (`needed = release_pct*nav − cash`, sells `min(qty, needed/px)`); min-park duration `residual_sleeve_min_park_bars` (default 8) blocks non-protective release; protective (bear/crash) release stays full + unconditional.
- [ ] Implement in `_residual_sleeve_deploy` / `_residual_sleeve_release` (+ `_residual_sleeve_config` keys; stamp `strategy_cache`-independent `_sleeve_last_park_bar` in a broker-side dict or on the emulator wrapper — use module-level `_sleeve_state = {"last_park_key": None}` keyed by bar timestamp).
- [ ] Tests PASS → commit.

### Task 9: Verify + ship
- [ ] Full test sweep: `python3 -m pytest backend/tests/ -k "regime or sleeve or drawdown or nexus_fixes" -x -q` → PASS.
- [ ] `gitnexus_detect_changes` (expect: cache/detector/hysteresis/lane/ramp/circuit/sleeve symbols only) + `npx gitnexus analyze` refresh.
- [ ] Push branch (Dokploy auto-deploys feat/controlled-benchmark-alpha backend).

### Task 10: Phase 6 — validation matrix (deployed API, faithful)
- [ ] Confirm deploy picked up new code (start a 1-day probe backtest, check log for the new regime diag line; delete probe).
- [ ] Launch BEAR_F4 (03-02..03-30), NEUTRAL_F4 (06-03..07-07), BULL_F4 (03-30..04-27) — granularity "3600", $6k, alpaca-main/doc-179-equivalent config with the full fix stack expressed as backtest config overrides (levers + profiles + circuit), NOT by touching doc-179.
- [ ] Pull logs (`scripts/pull_backtest_logs.py`), verify in-log: regime flips match VOO replay (chop 03-02 start, bear ~03-20; chop until 04-09 in bull window), caps track regime, lanes blocked in bear, no sleeve deploys outside bull, circuit tiers as expected.
- [ ] Gates: BEAR_F4 > 0%, NEUTRAL_F4 ≥ 0%, BULL_F4 ≥ SPY(+13.61%). If BULL_F4 < SPY: raise `deployment_ramp_chop_scale` / chop cap and rerun BULL only.
- [ ] Report results; doc-179 re-apply remains a user decision (Phase 7).

## Self-Review
- Spec coverage: Phase 0→T0, 1→T1-3+T5, 2→T4, 3→T6, 4→T7, 5→T8, 6→T10, 7 explicitly user-gated. ✓
- No placeholders; signatures consistent (`_detect_market_regime(..., data=None)`, `_apply_regime_hysteresis`, `_rotation_lane_allowed`, `_drawdown_circuit_tier`). ✓
- Risk note: Task 7 default-ON is justified because live doc-179 is reverted to baseline (circuit can't harm live until re-apply) and backtests must exercise it.
