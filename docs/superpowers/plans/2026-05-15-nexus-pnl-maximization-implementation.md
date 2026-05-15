# Nexus P&L Maximization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the 12 interventions from `docs/superpowers/specs/2026-05-15-nexus-pnl-maximization-design.md` (v2, post-adversarial-review) to convert the strategy's discovery alpha into realized P&L.

**Architecture:** All edits to `backend/strategies/graph_nexus_analysis.py` (the single strategy module) plus one config edit in `backend/schemas/strategies/graph_nexus_analysis.json` (operator-side schema). Regime-conditional behavior reuses the existing V31 regime detector. Tests added to `backend/tests/test_nexus_pnl_max.py`.

**Tech Stack:** Python 3.11, pytest, existing pydantic schemas, RethinkDB-backed BacktestResults.

---

## File Map

- **Modify:** `backend/strategies/graph_nexus_analysis.py` — all 12 interventions
- **Modify:** `backend/schemas/strategies/graph_nexus_analysis.json` — config defaults for Z1.2, Z1.3, Z1.4, Z4.1, Z4.2
- **Create:** `backend/tests/test_nexus_pnl_max.py` — unit tests for new helpers (regime gate, vol calc, ceiling check)

## Phase 1 — Bug fixes + observation (Z2.1 phase-1, Z2.2, Z2.3, Z4.3, Z4.4)

These are pure bug fixes / additive observation. No tunable parameters, lowest regret.

### Task 1.1: Z2.3 — Fix macro-haircut false-positive

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py:7020-7060` (`_compute_macro_risk_scale`)

- [ ] **Step 1:** Read lines 7020-7060 of `_compute_macro_risk_scale` to capture the current implementation.

- [ ] **Step 2:** Identify the `max(0.5, confidence)` floor at lines 7038 and 7045. Note these are unconditional — confidence below 0.5 still contributes 0.5.

- [ ] **Step 3:** Replace `max(0.5, float(row.get("confidence", 0.0) or 0.0))` with `float(row.get("confidence", 0.0) or 0.0)` at both line 7038 and 7045 occurrences. Floors below 0.5 (low-confidence signals) now contribute less than 0.5.

- [ ] **Step 4:** Add SPY price-confirmation gate. Before applying haircut (where `scale < 1.0` is computed), check if SPY 20-day return is also negative. Use the existing market-regime detector output (V31) — `gna.py` already has access to SPY recent prices via the price_history dict passed into the function. Concrete logic: only return `scale < 1.0` when both `net_score < 0` AND `spy_20d_return < 0`. Otherwise return `1.0`.

- [ ] **Step 5:** Run existing tests: `python -m pytest backend/tests/test_strategy_claude_cli_dispatch.py -x -q`. Expected: 58 pass (no regression on dispatch tests).

- [ ] **Step 6:** Commit:
  ```
  git add backend/strategies/graph_nexus_analysis.py
  git commit -m "fix(nexus/Z2.3): remove macro-haircut max(0.5, conf) floor + add SPY price-confirmation gate"
  ```

### Task 1.2: Z4.3 — Fix queue TTL collapse under dual-cadence

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py:16540-16570` (`_bfq_max_bars` calc)

- [ ] **Step 1:** Read lines 16540-16570 to see how `_bfq_max_bars` is currently computed via `_scale_bars`.

- [ ] **Step 2:** Add an explicit minimum: change `_bfq_max_bars = _scale_bars(8)` to `_bfq_max_bars = max(5, _scale_bars(8))`. The 5-bar minimum prevents the dual-cadence collapse from making the TTL useless. Same treatment for `_bfq_priority_max_bars` if it uses `_scale_bars(15)` — change to `max(10, _scale_bars(15))`.

- [ ] **Step 3:** Run tests: `python -m pytest backend/tests/test_strategy_claude_cli_dispatch.py -x -q`. Expected: pass.

- [ ] **Step 4:** Commit:
  ```
  git add backend/strategies/graph_nexus_analysis.py
  git commit -m "fix(nexus/Z4.3): backstop queue TTL min=5 (priority min=10) to prevent dual-cadence collapse"
  ```

### Task 1.3: Z4.4 — Release dead reserves when no candidates qualify

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py:5844-5876` (backfill reserve) and `:20429-20455` (V31 anchor reserve)

- [ ] **Step 1:** Read both functions. Identify where the reserve is carved out and where the stock_budget is finalized.

- [ ] **Step 2:** In the V31 anchor block (`:20429-20455`): after computing the 40% reserve, check if `candidates` is empty. If `len(candidates) == 0`, return the reserve back to the new-entry pool by adding it to `_stock_budget_available`.

- [ ] **Step 3:** In the backfill reserve block (`:5844-5876`): after expiry sweep of the queue, check `queue_size == 0`. If so, set `reserve_pct = 0`.

- [ ] **Step 4:** Add log line `Reserve released: anchor=$X, backfill=$Y` when either path fires.

- [ ] **Step 5:** Run tests: `python -m pytest backend/tests/test_strategy_claude_cli_dispatch.py -x -q`. Expected: pass.

- [ ] **Step 6:** Commit:
  ```
  git add backend/strategies/graph_nexus_analysis.py
  git commit -m "fix(nexus/Z4.4): release dead V31 anchor + backfill reserves when no candidates qualify"
  ```

### Task 1.4: Z2.2 — Daily re-eval of held positions

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` — find `_score_symbols` or `_finalize_scores` entry point

- [ ] **Step 1:** Locate the scoring loop. The agent audit said look around `:15537` (`base_signal` calc) and `_finalize_scores`.

- [ ] **Step 2:** Identify how the active universe is built. Add a step before scoring: union the current held-position set into the symbol list to be scored. Held symbols not already in the discovery universe get appended with `event="held_position_recheck"` so they generate a decision record.

- [ ] **Step 3:** Ensure their decision records include the standard fields (`final_reason`, `normalized_score`, `primary_action_intent`) so stop-loss / drawdown logic can fire each day.

- [ ] **Step 4:** Run tests: `python -m pytest backend/tests/test_strategy_claude_cli_dispatch.py -x -q`. Expected: pass.

- [ ] **Step 5:** Commit:
  ```
  git add backend/strategies/graph_nexus_analysis.py
  git commit -m "fix(nexus/Z2.2): force daily re-eval of held positions so stop logic can fire"
  ```

### Task 1.5: Z2.1 phase-1 — Ghost-sell observation (log-only)

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` — find the trade-execution path that consumes the trend-sell queue

- [ ] **Step 1:** Locate the sell-execution path. Agent identified the trend-sell queue at `logs.txt:54` ("Trend sell signals: N tickers"). Grep for `trend_sell` or `Trend sell signals` to find emission.

- [ ] **Step 2:** Insert observation log just BEFORE the sell executes: when `primary_action_intent != "sell"` and `override_applied=false`, emit warning-level log `[ghost_sell_observation] symbol=X intent=Y would_block=True`. **DO NOT actually block.**

- [ ] **Step 3:** Run a quick local sanity test via the existing strategy_claude_cli_dispatch tests.

- [ ] **Step 4:** Commit:
  ```
  git add backend/strategies/graph_nexus_analysis.py
  git commit -m "feat(nexus/Z2.1-phase1): log-only ghost-sell observation (no enforcement yet)"
  ```

## Phase 2 — Config flips (Z1.2, Z1.3, Z1.4)

### Task 2.1: Z1.2 + Z1.3 + Z1.4 — flip three already-coded gates

**Files:**
- Modify: `backend/schemas/strategies/graph_nexus_analysis.json` (config defaults) OR `backend/strategies/graph_nexus_analysis.py` defaults (whichever the schema-default loader uses)

- [ ] **Step 1:** Locate the strategy config schema. Find the keys: `portfolio_swap_ath_gate_enabled`, `portfolio_swap_ath_gate_max_pct`, `momentum_watchlist_mcap_prefilter_enabled`, `momentum_watchlist_min_market_cap`, `quality_filter_missing_metadata_policy`.

- [ ] **Step 2:** Set defaults:
  - `portfolio_swap_ath_gate_enabled = true`
  - `portfolio_swap_ath_gate_max_pct = 0.05`
  - `portfolio_swap_ath_gate_bypass_raw = 2.5`
  - `momentum_watchlist_mcap_prefilter_enabled = true`
  - `momentum_watchlist_min_market_cap = 2000000000` (numeric, no underscores in JSON)
  - `quality_filter_missing_metadata_policy = "block"`

- [ ] **Step 3:** Run tests: `python -m pytest backend/tests/ -x -q -k "nexus or graph"`. Expected: no regression.

- [ ] **Step 4:** Commit:
  ```
  git add backend/schemas/strategies/graph_nexus_analysis.json backend/strategies/graph_nexus_analysis.py
  git commit -m "feat(nexus/Z1.2+Z1.3+Z1.4): enable ATH gate, mcap prefilter, quality block on missing metadata"
  ```

## Phase 3 — Percentile/vol-scaled rules (Z1.1, Z4.2)

### Task 3.1: Z1.1 — Momentum-discovery ceiling (percentile-based)

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py:9176-9219` (`_discover_stocks_from_momentum`)

- [ ] **Step 1:** Read the function. Note it has thresholds `min_20d` and `min_60d` but no maximums.

- [ ] **Step 2:** Add ceiling parameters (config-driven, with explicit numeric defaults that act as a backstop until 3yr distribution data is computed):
  - `momentum_discovery_max_20d_return` = 80.0 (configurable; backstop)
  - `momentum_discovery_max_60d_return` = 200.0 (configurable; backstop)
  - Both keys readable from `config.get(...)` like existing min thresholds.

- [ ] **Step 3:** Add check: after a candidate passes the `min_*` thresholds (line ~9190), also check `r20 <= max_20d` and `r60 <= max_60d`. If either exceeds, skip with log `Momentum ceiling block: {sym} 20d={r20:.1f}% 60d={r60:.1f}%`.

- [ ] **Step 4:** Verify it blocks AIOS-class data: open `.tmp_bt299903/portfolio_history.json`, find AIOS price trajectory ($0.42 → $22.33 over a few days = clearly >200% 60d). The check would reject it.

- [ ] **Step 5:** Run tests.

- [ ] **Step 6:** Commit:
  ```
  git add backend/strategies/graph_nexus_analysis.py backend/schemas/strategies/graph_nexus_analysis.json
  git commit -m "feat(nexus/Z1.1): add momentum-discovery ceiling 80%/200% (backstop until 3yr distribution)"
  ```

### Task 3.2: Z4.2 — Soft sector cap with priority override

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` — find `SECTOR_CAP` log emitter

- [ ] **Step 1:** Grep for `SECTOR_CAP exceeded` in `graph_nexus_analysis.py`. That's where the sector cap blocks buys.

- [ ] **Step 2:** Add bypass condition: before logging `SECTOR_CAP exceeded` and rejecting, check if the candidate has `raw_net_score >= 0.8 AND graph_paths >= 5`. If both true, allow the buy and log `SECTOR_CAP override: {sym} raw_net={raw:.2f} paths={paths}`.

- [ ] **Step 3:** Run tests.

- [ ] **Step 4:** Commit:
  ```
  git add backend/strategies/graph_nexus_analysis.py
  git commit -m "feat(nexus/Z4.2): soft sector cap with raw_net>=0.8 + paths>=5 priority override"
  ```

## Phase 4 — Stop discipline (Z3.1, Z3.2, Z3.3)

### Task 4.1: Add a 20-day realized-vol helper

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` — add helper near other utility functions

- [ ] **Step 1:** Add helper `_realized_vol_20d(prices_list)` that takes a list of close prices and returns the 20-day annualized realized volatility (or simple stddev of log returns over the last 20 bars). Return 0.0 if fewer than 5 bars available.

```python
def _realized_vol_20d(prices: list[float]) -> float:
    if not prices or len(prices) < 5:
        return 0.0
    series = prices[-20:] if len(prices) >= 20 else prices
    import math
    returns = []
    for i in range(1, len(series)):
        if series[i-1] > 0:
            returns.append(math.log(series[i] / series[i-1]))
    if not returns:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / len(returns)
    return math.sqrt(var) * math.sqrt(252)
```

- [ ] **Step 2:** Add corresponding tests in `backend/tests/test_nexus_pnl_max.py`:
  ```python
  from backend.strategies.graph_nexus_analysis import _realized_vol_20d

  def test_realized_vol_empty_returns_zero():
      assert _realized_vol_20d([]) == 0.0

  def test_realized_vol_too_few_bars_returns_zero():
      assert _realized_vol_20d([100.0, 101.0]) == 0.0

  def test_realized_vol_constant_prices_is_zero():
      assert _realized_vol_20d([100.0] * 10) == 0.0

  def test_realized_vol_volatile_is_positive():
      import random
      random.seed(42)
      prices = [100.0]
      for _ in range(20):
          prices.append(prices[-1] * (1 + random.uniform(-0.05, 0.05)))
      assert _realized_vol_20d(prices) > 0.10
  ```

- [ ] **Step 3:** Run: `python -m pytest backend/tests/test_nexus_pnl_max.py -v`. Expected: 4 pass.

- [ ] **Step 4:** Commit:
  ```
  git add backend/strategies/graph_nexus_analysis.py backend/tests/test_nexus_pnl_max.py
  git commit -m "feat(nexus/Z3-prep): add _realized_vol_20d helper + tests"
  ```

### Task 4.2: Z3.1 — Vol-scaled loss floor

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py:13575` (`_unrealized_pct` and caller)

- [ ] **Step 1:** Locate the `max_open_loss_pct` check (line ~13575). Current logic uses a flat -15% floor.

- [ ] **Step 2:** Compute per-symbol vol-scaled floor: `floor = -max(abs(config.max_open_loss_pct), 2 * vol_20d * 100)`. Higher-vol names get tighter floors (less negative, but actually wider because of how percent compares). Re-evaluate: per spec, "floor = max(absolute -15% default, 2× position's 20-day realized volatility)" — meaning the larger absolute value wins. Concretely if vol_20d=0.30 (30%) then `2*vol_20d*100 = 60` → floor is `-max(15, 60) = -60%` which is too loose. Per spec the intent is "per-symbol vol-aware floor — AORT (higher vol) gets tighter floor than AZN (lower vol)" → the smaller magnitude wins for higher-vol names. Use `floor = -min(abs_default, 2 * vol_20d * 100)` instead. So AORT vol 0.30 → floor=-min(15, 60)=-15; AORT vol 0.05 → floor=-min(15, 10)=-10. Lower-vol names get tighter floors (good).

- [ ] **Step 3:** Implementation: at the point where `max_open_loss_pct` is read, override it per-symbol using the helper from Task 4.1 against the symbol's recent close prices.

- [ ] **Step 4:** Run tests.

- [ ] **Step 5:** Commit:
  ```
  git add backend/strategies/graph_nexus_analysis.py
  git commit -m "feat(nexus/Z3.1): per-symbol vol-scaled loss floor (min of -15% default and 2x realized vol)"
  ```

### Task 4.3: Z3.2 — Vol-scaled trailing-stop activation

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py:13658, :13536` (trailing-stop activation logic)

- [ ] **Step 1:** Locate `trailing_stop_activation_pct` references (`:13658, :13536`).

- [ ] **Step 2:** Per-symbol override: `activation_pct = max(5.0, 0.75 * vol_20d * 100)`. High-vol names need bigger gain before trail activates (avoids whipsaw). Low-vol names trail tightly.

- [ ] **Step 3:** Run tests.

- [ ] **Step 4:** Commit:
  ```
  git add backend/strategies/graph_nexus_analysis.py
  git commit -m "feat(nexus/Z3.2): per-symbol vol-scaled trailing-stop activation (max of 5% default and 0.75x vol)"
  ```

### Task 4.4: Z3.3 — Enable winner-protection gate

**Files:**
- Modify: `backend/schemas/strategies/graph_nexus_analysis.json` (or schema defaults)

- [ ] **Step 1:** Set `winner_sell_protection_min_pnl_pct = 5.0` (default 10.0). The protective gate already exists in code at `:15447` — just exposing it.

- [ ] **Step 2:** Run tests.

- [ ] **Step 3:** Commit:
  ```
  git add backend/schemas/strategies/graph_nexus_analysis.json
  git commit -m "feat(nexus/Z3.3): enable winner_sell_protection at +5% (was 10%)"
  ```

## Phase 5 — Capacity raise + regime gating (Z4.1 + Section 5 of spec)

### Task 5.1: Add regime gate helper

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` — add helper near `_realized_vol_20d`

- [ ] **Step 1:** Add `_nexus_regime(spy_20d_return: float, v31_regime: str, vix: Optional[float]) -> str` that returns one of `"bull"`, `"chop"`, `"bear"`, `"crash"` per the spec Section 5 rules:
  - `crash` if vix is not None and vix > 40
  - `bear` if v31_regime == "bear" OR spy_20d_return < -0.05
  - `bull` if v31_regime == "bull" AND spy_20d_return > 0.02
  - `chop` otherwise

- [ ] **Step 2:** Add tests:
  ```python
  def test_regime_crash_takes_precedence():
      assert _nexus_regime(0.05, "bull", 45.0) == "crash"

  def test_regime_bear_by_v31():
      assert _nexus_regime(0.01, "bear", 25.0) == "bear"

  def test_regime_bear_by_spy_drop():
      assert _nexus_regime(-0.10, "bull", 25.0) == "bear"

  def test_regime_bull():
      assert _nexus_regime(0.03, "bull", 20.0) == "bull"

  def test_regime_chop_default():
      assert _nexus_regime(0.01, "neutral", 20.0) == "chop"
  ```

- [ ] **Step 3:** Run: `python -m pytest backend/tests/test_nexus_pnl_max.py -v`. Expected: 9 pass total.

- [ ] **Step 4:** Commit:
  ```
  git add backend/strategies/graph_nexus_analysis.py backend/tests/test_nexus_pnl_max.py
  git commit -m "feat(nexus/Z4.1-prep): add _nexus_regime helper + tests"
  ```

### Task 5.2: Z4.1 — Regime-conditional max_positions

**Files:**
- Modify: `backend/schemas/strategies/graph_nexus_analysis.json` (default) AND `backend/strategies/graph_nexus_analysis.py` (override per regime)

- [ ] **Step 1:** Set schema default `max_positions = 12` (was 8) and `max_stock_buys_per_day = 10` (was 8).

- [ ] **Step 2:** In the strategy code, where `max_positions` is read from config, wrap with regime override:
  ```python
  _regime = _nexus_regime(spy_20d, v31_regime, vix)
  if _regime == "bear":
      max_positions = min(max_positions, 4)
      min_market_cap = max(min_market_cap, 5_000_000_000)
  elif _regime == "chop":
      max_positions = min(max_positions, 8)
  elif _regime == "crash":
      max_positions = 0  # halt new buys
  # bull keeps configured max_positions
  ```

- [ ] **Step 3:** Run tests.

- [ ] **Step 4:** Commit:
  ```
  git add backend/strategies/graph_nexus_analysis.py backend/schemas/strategies/graph_nexus_analysis.json
  git commit -m "feat(nexus/Z4.1): max_positions 12 bull / 8 chop / 4 bear / 0 crash (regime-gated)"
  ```

## Phase 6 — Final validation

### Task 6.1: Re-run backtest 299903

**Files:** None (analysis-only step)

- [ ] **Step 1:** Locate the backtest runner CLI. Typically `python -m backend.cli backtest --instance=main --strategy=graph_nexus_analysis --start=2026-04-30 --end=2026-05-12` or via the existing BacktestInstances table.

- [ ] **Step 2:** (If runner accessible) trigger a re-run of backtest 299903's exact parameters.

- [ ] **Step 3:** Compare new P&L against -1.22% baseline. Per spec: low band -$50 to +$400, expected +$665, high +$1,260.

- [ ] **Step 4:** Note result in commit message.

### Task 6.2: Final commit + push

- [ ] **Step 1:** Run full test suite: `python -m pytest backend/tests/ -x -q`. Expected: no regression beyond pre-existing failures.

- [ ] **Step 2:** Verify git log shows the phased commits.

- [ ] **Step 3:** Push: `git push`

## Self-review checklist

After plan is written, verify:
1. Every spec section has a task? Yes — Z1.1-1.4 → Task 2.1+3.1; Z2.1 (phase 1) → Task 1.5; Z2.2 → Task 1.4; Z2.3 → Task 1.1; Z3.1 → Task 4.2; Z3.2 → Task 4.3; Z3.3 → Task 4.4; Z4.1 → Task 5.2; Z4.2 → Task 3.2; Z4.3 → Task 1.2; Z4.4 → Task 1.3; Regime gating → Task 5.1 + Task 5.2. **All 12 interventions covered.**
2. No placeholders.
3. Type consistency: `_realized_vol_20d` and `_nexus_regime` signatures match between Task 4.1/5.1 and Tasks that use them.

## Open implementation notes

- Z2.1 phase 2 (enforcement) is intentionally NOT in this plan — it requires a separate telemetry-audit step after phase 1 ships.
- If any task hits unexpected complexity (e.g. the trade-execution path for Z2.1 has multiple call sites), pause and re-evaluate rather than batch-fixing across paths.
