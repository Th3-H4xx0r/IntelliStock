# Run-163943 exit-quality + C1 sector-cap pre-validation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline, chosen by user) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the two real, model-independent defects surfaced by the bt163943 forensics — (A) rotation sell-leg pre-validation missing the V31 sector-cap demotion, and (C) forced-exit paths cutting would-be-winners at local bottoms.

**Architecture:** Two small module-level helpers in `backend/strategies/graph_nexus_analysis.py`, each wired into existing decision sites. Track C is fully default-off (behavior-preserving); Track A defaults on but is conservative + fail-open. No existing signatures change.

**Tech Stack:** Python 3, pytest. Strategy file `backend/strategies/graph_nexus_analysis.py` (27k lines). Tests under `backend/tests/`.

## Global Constraints

- All new behavior gated by config; defaults preserve current live behavior EXCEPT `rotation_prevalidate_sector_cap_enabled` (default True — a conservative, fail-open correctness fix).
- Fail-open on any missing data (no sector cache, no price, degenerate inputs) — a helper may only ever *tighten* an exit/rotation, never loosen one, and never raise.
- Run `gitnexus_impact` before editing each symbol; `gitnexus_detect_changes` before each commit.
- Do NOT touch `rotation_winner_lock_*`, `fast_loser_cut_pct`, or break-glass score/delta gates (code-memory: winner-lock only shields positive-pnl holds; fix the cut thresholds, not the lock).
- Pre-existing baseline test failures (24, on base too): July-4 calendar `broker_session`, `profit_take_v25`, `live_calendar`, rethinkdb-localhost, `BfqWinnerLockBypass::test_disabled_by_default`. Branch must add zero NEW failures.
- Test file to create: `backend/tests/test_run163943_exit_c1_fix.py`. Models: `_Emu` (`_positions` dict) + `_base_config(**overrides)` from `test_nexus_evaluate_position_risk.py`; monkeypatch module global `_neo4j_stock_sector_cache`.

---

### Task 1: `_recent_runup_protect` helper + FLC refactor (Track C core)

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` (add helper near line 7099 after `_slot_min_notional`; refactor FLC block `:17150-17178`)
- Test: `backend/tests/test_run163943_exit_c1_fix.py`

**Interfaces:**
- Produces: `_recent_runup_protect(sym: str, price_history: dict | None, block_pct: float, lookback_bars: int) -> tuple[bool, float]` — returns `(protected, runup_pct)`. `protected` True iff `block_pct > 0` and the max-min close range over the last `lookback_bars` bars exceeds `block_pct` percent.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_run163943_exit_c1_fix.py
import os, sys
_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
import pytest
import strategies.graph_nexus_analysis as gna


def _ph(sym, closes):
    return {sym: [{"close": c} for c in closes]}


class TestRecentRunupProtect:
    def test_disabled_when_block_pct_zero(self):
        assert gna._recent_runup_protect("X", _ph("X", [10, 20, 15]), 0.0, 20) == (False, 0.0)

    def test_protects_when_runup_exceeds_threshold(self):
        # range 10->20 = +100% > 25% threshold
        protected, pct = gna._recent_runup_protect("X", _ph("X", [10, 20, 16]), 25.0, 20)
        assert protected is True
        assert pct == pytest.approx(100.0)

    def test_not_protected_when_runup_below_threshold(self):
        # range 100->110 = +10% < 25%
        protected, pct = gna._recent_runup_protect("X", _ph("X", [100, 110, 105]), 25.0, 20)
        assert protected is False
        assert pct == pytest.approx(10.0)

    def test_fail_open_on_missing_history(self):
        assert gna._recent_runup_protect("X", None, 25.0, 20) == (False, 0.0)
        assert gna._recent_runup_protect("X", {}, 25.0, 20) == (False, 0.0)
        assert gna._recent_runup_protect("X", _ph("X", [10]), 25.0, 20) == (False, 0.0)

    def test_respects_lookback_window(self):
        # old +100% runup falls outside a 2-bar lookback; recent 2 bars flat
        protected, pct = gna._recent_runup_protect("X", _ph("X", [10, 20, 30, 30]), 25.0, 2)
        assert protected is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_run163943_exit_c1_fix.py::TestRecentRunupProtect -v`
Expected: FAIL with `AttributeError: module ... has no attribute '_recent_runup_protect'`

- [ ] **Step 3: Add the helper** (insert after `_slot_min_notional`, ~line 7106)

```python
def _recent_runup_protect(sym, price_history, block_pct, lookback_bars) -> tuple[bool, float]:
    """True when a position's recent close range ran up more than block_pct
    over the last lookback_bars bars (volatile momentum on a hot entry).
    Used to spare such a name from a forced exit at a local dip (run-163943:
    UAL/DELL cut at local bottoms before large rebounds). Returns
    (protected, runup_pct). block_pct <= 0 or missing history disables it."""
    try:
        _bp = float(block_pct or 0.0)
    except (TypeError, ValueError):
        return False, 0.0
    if _bp <= 0 or not isinstance(price_history, dict):
        return False, 0.0
    bars = (price_history.get(sym) or [])[-max(2, int(lookback_bars or 0)):]
    if len(bars) < 2:
        return False, 0.0
    closes = [float(b.get("close") or b.get("c") or 0.0) for b in bars if isinstance(b, dict)]
    closes = [c for c in closes if c > 0.0]
    if len(closes) < 2:
        return False, 0.0
    lo = min(closes)
    hi = max(closes)
    runup_pct = ((hi - lo) / lo) * 100.0 if lo > 0 else 0.0
    return (runup_pct > _bp), runup_pct
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_run163943_exit_c1_fix.py::TestRecentRunupProtect -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Refactor the FLC block to use the helper (behavior-equivalent)**

Replace `backend/strategies/graph_nexus_analysis.py:17151-17176` (the inline `_flc_runup_block_pct` … `_flc_block_runup = True` … log block) with:

```python
                    _flc_runup_block_pct = float(config.get("fast_loser_cut_recent_runup_block_pct", 0.0) or 0.0)
                    _flc_runup_lookback = int(config.get("fast_loser_cut_recent_runup_lookback_bars", 20) or 20)
                    _flc_block_runup, _flc_runup_pct = _recent_runup_protect(
                        sym, price_history, _flc_runup_block_pct, _flc_runup_lookback,
                    )
                    if _flc_block_runup:
                        _log(
                            f"V28.7 FLC recent-runup block: {sym} recent "
                            f"{_flc_runup_lookback}-bar range ran +{_flc_runup_pct:.1f}% > "
                            f"{_flc_runup_block_pct:.0f}% threshold — not cutting "
                            f"(volatility on hot entry)",
                            "yellow",
                        )
```

(The following `if _flc_block_runup: pass else: fresh_score = -1 …` block is unchanged.)

- [ ] **Step 6: Verify FLC equivalence via the existing risk suite**

Run: `cd backend && python -m pytest tests/test_nexus_evaluate_position_risk.py -v`
Expected: PASS (same set as base — the refactor is behavior-preserving)

- [ ] **Step 7: Commit**

```bash
git add backend/strategies/graph_nexus_analysis.py backend/tests/test_run163943_exit_c1_fix.py
git commit -m "feat(nexus): extract _recent_runup_protect; refactor FLC runup-block to use it"
```

---

### Task 2: Wire runup-guard into the V28.9 losing break-glass (Track C, default-off)

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` (insert after the η.D refusal block, ~line 24542, inside `_apply_sector_concentration_limit`; price-history local here is `data`)
- Test: `backend/tests/test_run163943_exit_c1_fix.py`

**Interfaces:**
- Consumes: `_recent_runup_protect` (Task 1).
- New config: `rotation_break_glass_recent_runup_block_pct` (default 0.0 = off), `rotation_break_glass_recent_runup_lookback_bars` (default 20).

- [ ] **Step 1: Write the failing test** (append to the test file)

```python
class TestBreakGlassRunupGuardConfig:
    """The guard is opt-in and defaults off (behavior-preserving)."""
    def test_default_off_means_helper_returns_not_protected(self):
        # With the default block_pct=0, a huge run-up is NOT protected — so
        # the break-glass path behaves exactly as before this change.
        cfg_pct = 0.0
        protected, _ = gna._recent_runup_protect("UAL", {"UAL": [{"close": 90}, {"close": 120}]}, cfg_pct, 20)
        assert protected is False

    def test_enabled_protects_recent_runup_name(self):
        protected, pct = gna._recent_runup_protect("UAL", {"UAL": [{"close": 90}, {"close": 120}, {"close": 105}]}, 25.0, 20)
        assert protected is True  # +33% range > 25%
```

- [ ] **Step 2: Run test to verify it passes the config-semantics assertions**

Run: `cd backend && python -m pytest tests/test_run163943_exit_c1_fix.py::TestBreakGlassRunupGuardConfig -v`
Expected: PASS (these lock the default-off semantics that Step 3's wiring relies on)

- [ ] **Step 3: Insert the guard** after the η.D refusal block (after line 24542, before the `# V28.6: v28_hc_profitable_break_glass remains FULL EXIT` comment at ~24543)

```python
                                        # Track C (run-163943): recent-runup momentum-protect.
                                        # A losing position that recently ran up (volatile hot
                                        # entry that dipped) is not evicted at a local bottom via
                                        # the losing break-glass (UAL was cut here before a +27%
                                        # move). Default-off; winner-lock partial-trim modes are
                                        # unaffected (they already protect genuine winners).
                                        if _rotation_mode == "v28_hc_losing_break_glass":
                                            _bg_runup_pct_cfg = float(
                                                config.get("rotation_break_glass_recent_runup_block_pct", 0.0) or 0.0
                                            )
                                            _bg_runup_lookback = int(
                                                config.get("rotation_break_glass_recent_runup_lookback_bars", 20) or 20
                                            )
                                            _bg_protected, _bg_runup_pct = _recent_runup_protect(
                                                _wt, data, _bg_runup_pct_cfg, _bg_runup_lookback,
                                            )
                                            if _bg_protected:
                                                _log(
                                                    f"break-glass recent-runup block: {_wt} recent "
                                                    f"{_bg_runup_lookback}-bar range ran +{_bg_runup_pct:.1f}% > "
                                                    f"{_bg_runup_pct_cfg:.0f}% — refusing losing break-glass "
                                                    f"eviction (momentum on hot entry); trying next pair",
                                                    "yellow",
                                                )
                                                continue
```

- [ ] **Step 4: Verify the rotation/risk suites still pass (default-off = no behavior change)**

Run: `cd backend && python -m pytest tests/test_rotation_graph_gate.py tests/test_rotation_override_recalibration.py tests/test_nexus_evaluate_position_risk.py -v`
Expected: PASS (same as base — guard is inert at default 0.0)

- [ ] **Step 5: Commit**

```bash
git add backend/strategies/graph_nexus_analysis.py backend/tests/test_run163943_exit_c1_fix.py
git commit -m "feat(nexus): runup momentum-protect guard on V28.9 losing break-glass (default off)"
```

---

### Task 3: `_rotation_incoming_sector_cap_ok` helper (Track A core)

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` (add helper after `_rotation_incoming_executable`, ~line 7044)
- Test: `backend/tests/test_run163943_exit_c1_fix.py`

**Interfaces:**
- Produces: `_rotation_incoming_sector_cap_ok(sym, buy_cash, portfolio_emulator, portfolio_total, config, prices=None, price_history=None, selling_sym=None) -> tuple[bool, str]`. Returns `(True, "")` = allow; `(False, reason)` = would be sector-cap demoted → block.
- Uses module globals `_neo4j_stock_sector_cache` and `_resolve_symbol_price`.

- [ ] **Step 1: Write the failing test** (append)

```python
class TestRotationIncomingSectorCapOk:
    def _emu(self, positions):
        class _E:
            _positions = {}
        e = _E(); e._positions = dict(positions); return e

    def setup_method(self):
        self._saved = gna._neo4j_stock_sector_cache
        gna._neo4j_stock_sector_cache = {"tech": ["AMD", "SMCI", "LRCX", "NVDA"], "travel": ["VIK", "UAL"]}

    def teardown_method(self):
        gna._neo4j_stock_sector_cache = self._saved

    def _cfg(self, **ov):
        c = {"max_sector_portfolio_enabled": True, "max_sector_portfolio_pct": 0.40,
             "rotation_prevalidate_sector_cap_enabled": True}
        c.update(ov); return c

    def test_blocks_buy_into_already_over_cap_sector(self):
        # tech already $45k of $100k (>40% cap); buying more tech (AMD) blocked
        emu = self._emu({"NVDA": 100})  # 100 * 450 = 45k
        ok, reason = gna._rotation_incoming_sector_cap_ok(
            "AMD", 5000, emu, 100000, self._cfg(),
            prices={"NVDA": 450.0, "AMD": 150.0}, selling_sym="VIK")
        assert ok is False
        assert "cap" in reason

    def test_allows_buy_when_sector_under_cap(self):
        emu = self._emu({"NVDA": 20})  # 20*450 = 9k tech
        ok, _ = gna._rotation_incoming_sector_cap_ok(
            "AMD", 5000, emu, 100000, self._cfg(),
            prices={"NVDA": 450.0, "AMD": 150.0}, selling_sym="VIK")
        assert ok is True  # 9k + 5k = 14k < 40k

    def test_same_sector_sell_is_excluded_from_exposure(self):
        # tech = NVDA 45k, but we are SELLING NVDA (same sector) to buy AMD:
        # exposure after removing NVDA = 0, +5k buy < cap -> allowed
        emu = self._emu({"NVDA": 100})
        ok, _ = gna._rotation_incoming_sector_cap_ok(
            "AMD", 5000, emu, 100000, self._cfg(),
            prices={"NVDA": 450.0, "AMD": 150.0}, selling_sym="NVDA")
        assert ok is True

    def test_fail_open_when_flag_disabled(self):
        emu = self._emu({"NVDA": 100})
        ok, _ = gna._rotation_incoming_sector_cap_ok(
            "AMD", 5000, emu, 100000, self._cfg(rotation_prevalidate_sector_cap_enabled=False),
            prices={"NVDA": 450.0, "AMD": 150.0})
        assert ok is True

    def test_fail_open_when_no_sector_cache(self):
        gna._neo4j_stock_sector_cache = {}
        emu = self._emu({"NVDA": 100})
        ok, _ = gna._rotation_incoming_sector_cap_ok(
            "AMD", 5000, emu, 100000, self._cfg(), prices={"NVDA": 450.0})
        assert ok is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_run163943_exit_c1_fix.py::TestRotationIncomingSectorCapOk -v`
Expected: FAIL with `AttributeError: ... '_rotation_incoming_sector_cap_ok'`

- [ ] **Step 3: Add the helper** (after `_rotation_incoming_executable`, ~line 7044)

```python
def _rotation_incoming_sector_cap_ok(
    sym, buy_cash, portfolio_emulator, portfolio_total, config,
    prices=None, price_history=None, selling_sym=None,
) -> tuple[bool, str]:
    """Pre-validate a rotation's incoming BUY against the V31 sector-portfolio
    cap BEFORE the sell leg commits (run-163943: rotations executed the sell
    leg, then the buy leg was sector-cap demoted, stranding proceeds — same
    sell-leg-only leak class as run-185254 leak #1 via a new gate).

    Conservative + fail-open: returns (True, "") whenever the cap is disabled,
    the sector cache/price is unavailable, or inputs are degenerate. Only
    returns (False, reason) when the buy's sector exposure (after removing
    selling_sym if it is in the same sector) plus buy_cash provably exceeds
    the cap. Blocking keeps the held position — no capital at risk."""
    if not bool(config.get("rotation_prevalidate_sector_cap_enabled", True)):
        return True, ""
    if not bool(config.get("max_sector_portfolio_enabled", True)):
        return True, ""
    try:
        cap_pct = float(config.get("max_sector_portfolio_pct", 0.40) or 0.40)
        _pt = float(portfolio_total or 0.0)
        _bc = float(buy_cash or 0.0)
    except (TypeError, ValueError):
        return True, ""
    if cap_pct <= 0 or _pt <= 0 or _bc <= 0:
        return True, ""
    cache = _neo4j_stock_sector_cache
    if not cache:
        return True, ""
    ticker_to_sector: dict[str, str] = {}
    for sector_kw, tickers in cache.items():
        for t in tickers:
            ticker_to_sector.setdefault(str(t).strip().upper(), sector_kw)
    buy_sector = ticker_to_sector.get(str(sym or "").strip().upper(), "unknown")
    selling_u = str(selling_sym or "").strip().upper()
    sector_dollars = 0.0
    if portfolio_emulator is not None:
        positions = getattr(portfolio_emulator, "_positions", {}) or {}
        for hs, qty in positions.items():
            try:
                qty_f = float(qty)
            except (TypeError, ValueError):
                continue
            if qty_f <= 0:
                continue
            hs_u = str(hs).strip().upper()
            if ticker_to_sector.get(hs_u, "unknown") != buy_sector:
                continue
            if hs_u == selling_u:
                continue  # this position is being sold in the rotation
            price = _resolve_symbol_price(
                hs_u, prices, price_history, portfolio_emulator=portfolio_emulator
            ) or 0.0
            sector_dollars += qty_f * float(price or 0.0)
    cap_dollars = _pt * cap_pct
    if sector_dollars + _bc > cap_dollars:
        return False, (
            f"sector '{buy_sector}' ${sector_dollars + _bc:,.0f} "
            f"> {cap_pct * 100:.0f}% cap ${cap_dollars:,.0f}"
        )
    return True, ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_run163943_exit_c1_fix.py::TestRotationIncomingSectorCapOk -v`
Expected: PASS (5 tests). If `_resolve_symbol_price` needs bar-shaped prices instead of floats, pass `price_history={"NVDA":[{"close":450.0}]}` in the tests instead of `prices={...}` — adjust to whatever `_resolve_symbol_price` accepts (verify by reading it).

- [ ] **Step 5: Commit**

```bash
git add backend/strategies/graph_nexus_analysis.py backend/tests/test_run163943_exit_c1_fix.py
git commit -m "feat(nexus): _rotation_incoming_sector_cap_ok pre-validation helper (fail-open)"
```

---

### Task 4: Wire sector-cap pre-check into the two rotation lanes (Track A)

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` — mw_rotation lane (`:25476-25479`) and mw_pf swap lane (`:25670-25671`)

**Interfaces:**
- Consumes: `_rotation_incoming_sector_cap_ok` (Task 3). Price-history local in this scope is `data`.

- [ ] **Step 1: Edit mw_rotation lane.** Replace `:25476-25479`:

```python
                    _mw_buy_alloc = max(_mw_min_pos, _mw_freed + _mw_extra)
                    if isinstance(strategy_cache, dict):
                        strategy_cache["_mw_free_cash_spent_this_bar"] = _mw_fc_spent_sofar + _mw_extra
                    if _mw_buy_alloc >= _mw_min_pos:
```

with:

```python
                    _mw_buy_alloc = max(_mw_min_pos, _mw_freed + _mw_extra)
                    _mw_sc_ok, _mw_sc_block = _rotation_incoming_sector_cap_ok(
                        _mw_buy, _mw_buy_alloc, portfolio_emulator, portfolio_total,
                        config, prices, data, selling_sym=_mw_sell,
                    )
                    if not _mw_sc_ok:
                        _log(
                            f"ROTATION PREVALIDATE sector-cap: skip incoming {_mw_buy} "
                            f"({_mw_sc_block}) — rotation skipped, keeping {_mw_sell}",
                            "yellow",
                        )
                    if isinstance(strategy_cache, dict) and _mw_sc_ok:
                        strategy_cache["_mw_free_cash_spent_this_bar"] = _mw_fc_spent_sofar + _mw_extra
                    if _mw_buy_alloc >= _mw_min_pos and _mw_sc_ok:
```

- [ ] **Step 2: Edit mw_pf swap lane.** Replace `:25670-25671`:

```python
                        _mw_pf_alloc = max(_mw_pf_min_pos, _mw_pf_freed + _mw_pf_extra)
                        if _mw_pf_alloc >= _mw_pf_min_pos:
```

with:

```python
                        _mw_pf_alloc = max(_mw_pf_min_pos, _mw_pf_freed + _mw_pf_extra)
                        _mw_pf_sc_ok, _mw_pf_sc_block = _rotation_incoming_sector_cap_ok(
                            _mw_pf_buy, _mw_pf_alloc, portfolio_emulator, portfolio_total,
                            config, prices, data, selling_sym=_mw_pf_sell,
                        )
                        if not _mw_pf_sc_ok:
                            _log(
                                f"ROTATION PREVALIDATE sector-cap: skip incoming {_mw_pf_buy} "
                                f"({_mw_pf_sc_block}) — swap skipped, keeping {_mw_pf_sell}",
                                "yellow",
                            )
                        if _mw_pf_alloc >= _mw_pf_min_pos and _mw_pf_sc_ok:
```

- [ ] **Step 3: Syntax + import sanity check**

Run: `cd backend && python -c "import strategies.graph_nexus_analysis"`
Expected: no error (module imports; indentation/paren balance OK).

- [ ] **Step 4: Run rotation + allocation suites**

Run: `cd backend && python -m pytest tests/test_rotation_graph_gate.py tests/test_rotation_override_recalibration.py tests/test_nexus_pnl_max.py -v`
Expected: PASS — same set as base (default cap behavior unchanged; the pre-check only *adds* a skip when a rotation buy would breach the 40% cap, which these tests don't hit).

- [ ] **Step 5: Commit**

```bash
git add backend/strategies/graph_nexus_analysis.py
git commit -m "feat(nexus): sector-cap pre-validation on mw_rotation + mw_pf rotation lanes"
```

---

### Task 5: Full-suite bisect verification

**Files:** none (verification only).

- [ ] **Step 1: Detect changes**

Run `gitnexus_detect_changes()` and confirm only the intended symbols/flows are affected. Warn if HIGH/CRITICAL.

- [ ] **Step 2: Run the full backend suite on the branch head**

Run: `cd backend && python -m pytest tests/ -q 2>&1 | tail -30`
Capture the failure count + names.

- [ ] **Step 3: Compare against base**

The branch must add ZERO new failures vs base `5abc86c` (24 known pre-existing). If any NEW failure appears, return to the relevant task — do not proceed.

- [ ] **Step 4: Commit any test-list notes to the SDD ledger** (`.superpowers/sdd/progress.md`), then proceed to bug sweep.

## Self-review notes
- Spec coverage: Track A = Tasks 3-4; Track C = Tasks 1-2; measurement protocol = spec only (not code, runs later); documented non-goals = no tasks (correct).
- Types consistent: `_recent_runup_protect` → `(bool, float)` used identically in Tasks 1/2; `_rotation_incoming_sector_cap_ok` → `(bool, str)` used identically in Tasks 3/4.
- No placeholders: all steps carry real code + exact commands.
