# Tiered partial profit-take — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let a winning position scale out in stages (trim ⅓ at +20%, another ⅓ at +40%, keep ⅓ running) instead of the current fire-once profit-take.

**Architecture:** A pure helper `_profit_take_next_tier` selects the next un-fired tier; the existing profit-take block in `_evaluate_position_risk` calls it and threads the fired tier's `sell_fraction` through `extras` into `_finalize_scores`. Fully default-preserving (no `profit_take_tiers` ⇒ current single-fire behaviour).

**Tech Stack:** Python 3, pytest. `backend/strategies/graph_nexus_analysis.py`.

## Global Constraints

- Behaviour-preserving: whole feature gated by `profit_take_enabled` (default false); tier path active only when `profit_take_tiers` is a non-empty list. Empty/absent ⇒ existing single-fire path unchanged.
- `sell_fraction` is a fraction of the *current* remaining holding (existing semantics — do not change).
- Fire the **lowest** un-fired crossed tier, **one tier per bar** (progressive scale-out).
- GitNexus `impact` before editing; `detect_changes` before commit; full-suite bisect head-vs-base = zero new failures (21 pre-existing: 19 fails + 2 `ModuleNotFound: backend` collection errors).
- New config keys registered in the `INTELLISTOCK_SCHEMA` header (line 1), matching PR-92 convention.
- Test file: `backend/tests/test_tiered_profit_take.py`. Integration tests reuse `_Emu` + `_base_config` from `test_nexus_evaluate_position_risk.py`.

---

### Task 1: `_profit_take_next_tier` pure helper

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` (add helper just above `_evaluate_position_risk`, ~line 16888)
- Test: `backend/tests/test_tiered_profit_take.py`

**Interfaces:**
- Produces: `_profit_take_next_tier(config: dict, unrealized_pct: float, entry_key: str, prior_marker) -> dict | None`. Returns `{"gain": float, "fraction": float, "new_marker": {"entry": entry_key, "fired": [floats]}}` for the lowest un-fired crossed tier, else `None`. `prior_marker` may be the new dict form, a legacy entry-key string, or None.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_tiered_profit_take.py
import os, sys
_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
import pytest  # noqa: E402
import strategies.graph_nexus_analysis as gna  # noqa: E402

TIERS = {"profit_take_tiers": [[20, 0.33], [40, 0.5]]}


class TestProfitTakeNextTier:
    def test_no_tiers_returns_none(self):
        assert gna._profit_take_next_tier({}, 50.0, "e1", None) is None
        assert gna._profit_take_next_tier({"profit_take_tiers": []}, 50.0, "e1", None) is None

    def test_fires_lowest_crossed_tier_first(self):
        r = gna._profit_take_next_tier(TIERS, 25.0, "e1", None)
        assert r["gain"] == 20 and r["fraction"] == 0.33
        assert r["new_marker"] == {"entry": "e1", "fired": [20.0]}

    def test_below_first_tier_returns_none(self):
        assert gna._profit_take_next_tier(TIERS, 15.0, "e1", None) is None

    def test_one_tier_per_bar_even_when_both_crossed(self):
        # gain 45 clears both tiers; with tier 20 already fired, fire tier 40 next
        prior = {"entry": "e1", "fired": [20.0]}
        r = gna._profit_take_next_tier(TIERS, 45.0, "e1", prior)
        assert r["gain"] == 40 and r["fraction"] == 0.5
        assert r["new_marker"]["fired"] == [20.0, 40.0]

    def test_all_tiers_fired_returns_none(self):
        prior = {"entry": "e1", "fired": [20.0, 40.0]}
        assert gna._profit_take_next_tier(TIERS, 60.0, "e1", prior) is None

    def test_reentry_resets_fired(self):
        prior = {"entry": "OLD", "fired": [20.0, 40.0]}
        r = gna._profit_take_next_tier(TIERS, 25.0, "NEW", prior)
        assert r["gain"] == 20 and r["new_marker"]["fired"] == [20.0]

    def test_legacy_string_marker_treated_as_lowest_tier_fired(self):
        # a legacy single-fire marker == entry_key ⇒ lowest tier already taken
        r = gna._profit_take_next_tier(TIERS, 45.0, "e1", "e1")
        assert r["gain"] == 40  # tier 20 considered already fired

    def test_malformed_tiers_skipped(self):
        cfg = {"profit_take_tiers": [["x", "y"], [20, 1.5], [25, 0.4]]}
        r = gna._profit_take_next_tier(cfg, 30.0, "e1", None)
        assert r["gain"] == 25 and r["fraction"] == 0.4  # bad rows dropped
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_tiered_profit_take.py::TestProfitTakeNextTier -q`
Expected: FAIL `AttributeError: ... '_profit_take_next_tier'`

- [ ] **Step 3: Add the helper** (just above `def _evaluate_position_risk(` ~line 16888)

```python
def _profit_take_next_tier(config, unrealized_pct, entry_key, prior_marker):
    """Next un-fired profit-take tier crossed at unrealized_pct, or None.
    Fires the LOWEST un-fired crossed tier (one per bar → progressive
    scale-out). prior_marker may be the dict form {"entry","fired"}, a legacy
    entry-key string (treated as lowest tier already taken), or None."""
    tiers_raw = config.get("profit_take_tiers") or []
    if not tiers_raw:
        return None
    tiers = []
    for t in tiers_raw:
        try:
            g = float(t[0]); f = float(t[1])
        except (TypeError, ValueError, IndexError):
            continue
        if g > 0 and 0.0 < f < 1.0:
            tiers.append((g, f))
    if not tiers:
        return None
    tiers.sort(key=lambda x: x[0])
    if isinstance(prior_marker, dict):
        fired = (set(float(x) for x in (prior_marker.get("fired") or []))
                 if prior_marker.get("entry") == entry_key else set())
    elif isinstance(prior_marker, str) and prior_marker == entry_key:
        fired = {tiers[0][0]}  # legacy single-fire marker → lowest tier taken
    else:
        fired = set()
    try:
        upct = float(unrealized_pct)
    except (TypeError, ValueError):
        return None
    for g, f in tiers:  # ascending → lowest first
        if g not in fired and upct >= g:
            return {"gain": g, "fraction": f,
                    "new_marker": {"entry": entry_key, "fired": sorted(fired | {g})}}
    return None
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && python3 -m pytest tests/test_tiered_profit_take.py::TestProfitTakeNextTier -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/strategies/graph_nexus_analysis.py backend/tests/test_tiered_profit_take.py
git commit -m "feat(nexus): _profit_take_next_tier helper for staged profit-taking"
```

---

### Task 2: Wire tiers into the profit-take block + fraction plumbing + schema

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` — profit-take block `:17379-17404`, `extras` site `:17472-17473`, `_finalize_scores` sites `:17677-17678` and `:17685-17686`, schema header line 1.
- Test: `backend/tests/test_tiered_profit_take.py`

**Interfaces:**
- Consumes: `_profit_take_next_tier` (Task 1).

- [ ] **Step 1: Write the failing integration + schema tests** (append)

```python
from datetime import datetime, timezone  # noqa: E402
import json, re  # noqa: E402
from nexus_real_config import real_config  # noqa: E402  (ensures module import parity)


class _Emu:
    def __init__(self): self._positions = {}; self._trades = []
    def add(self, t, sh, px): self._positions[t] = self._positions.get(t, 0.0) + sh


def _cfg(**ov):
    c = {"profit_take_enabled": True, "profit_take_tiers": [[20, 0.33], [40, 0.5]],
         "max_open_loss_pct": -50.0, "fast_loser_cut_pct": -50.0,
         "trailing_stop_activation_pct": 90.0, "trailing_stop_pct": 90.0,
         "max_hold_days": 999, "profit_take_gain_pct": 40.0, "profit_take_sell_fraction": 0.5}
    c.update(ov); return c


def _eval(sym, gain_pct, cache, cfg, held_days=100):
    emu = _Emu(); emu.add(sym, 100, 100.0)
    return gna._evaluate_position_risk(
        sym, fresh_score=0, fresh_reason="No graph signal", config=cfg,
        portfolio_emulator=emu, strategy_cache=cache, prices={sym: 100.0 * (1 + gain_pct/100.0)},
        price_history={}, date_key="2026-04-01", propagated={},
        entry_buy_ts=datetime(2026, 1, 1, tzinfo=timezone.utc), held_days=held_days, max_hold_days=999)


class TestTierIntegration:
    def test_tier1_fires_with_tier_fraction(self):
        cache = {}
        score, reason, extras = _eval("FOO", 25.0, cache, _cfg())
        assert score == -1 and "Profit take" in reason
        assert extras["sell_fraction"] == 0.33
        assert cache["_nexus_profit_take_state"]["FOO"]["fired"] == [20.0]

    def test_tier2_fires_after_tier1_with_its_fraction(self):
        cache = {"_nexus_profit_take_state": {"FOO": {"entry": "2026-01-01T00:00:00+00:00", "fired": [20.0]}}}
        score, reason, extras = _eval("FOO", 45.0, cache, _cfg())
        assert score == -1 and extras["sell_fraction"] == 0.5

    def test_no_refire_same_tier(self):
        cache = {"_nexus_profit_take_state": {"FOO": {"entry": "2026-01-01T00:00:00+00:00", "fired": [20.0]}}}
        score, reason, _ = _eval("FOO", 25.0, cache, _cfg())  # only tier 20 crossed, already fired
        assert score != -1 or "Profit take" not in (reason or "")

    def test_tiers_absent_uses_single_fire(self):
        cache = {}
        cfg = _cfg(profit_take_tiers=[], profit_take_gain_pct=20.0, profit_take_sell_fraction=0.5)
        score, reason, extras = _eval("FOO", 25.0, cache, cfg)
        assert score == -1 and extras["sell_fraction"] == 0.5  # global fraction, legacy path


class TestSchema:
    def test_new_keys_registered(self):
        line1 = open(gna.__file__, encoding="utf-8").readline()
        cfg = json.loads(re.search(r"INTELLISTOCK_SCHEMA:\s*(\{.*\})\s*$", line1.strip()).group(1))["config"]
        assert cfg["profit_take_enabled"] is False
        assert cfg["profit_take_tiers"] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_tiered_profit_take.py::TestTierIntegration tests/test_tiered_profit_take.py::TestSchema -q`
Expected: FAIL (tier fraction not threaded / schema keys absent).

- [ ] **Step 3: Rewrite the profit-take block** `:17379-17404`. Replace:

```python
                _profit_take_enabled = bool(config.get("profit_take_enabled", False))
                _profit_take_gain_pct = float(config.get("profit_take_gain_pct", 40.0) or 40.0)
                _profit_take_sell_fraction = float(config.get("profit_take_sell_fraction", 0.50) or 0.50)
                if _profit_take_enabled and fresh_score >= 0 and _profit_take_gain_pct > 0 and 0.0 < _profit_take_sell_fraction < 1.0:
                    _profit_state = _normalize_cache_mapping(strategy_cache, "_nexus_profit_take_state")
                    _entry_marker = entry_buy_ts
                    if isinstance(_entry_marker, str):
                        _entry_key = _entry_marker[:32]
                    elif hasattr(_entry_marker, "isoformat"):
                        try:
                            _entry_key = str(_entry_marker.isoformat())[:32]
                        except Exception:
                            _entry_key = str(_entry_marker)[:32]
                    else:
                        _entry_key = str(_entry_marker or "")
                    _profit_marker = _profit_state.get(sym)
                    if _unrealized_pct >= _profit_take_gain_pct and _entry_key and _profit_marker != _entry_key:
                        fresh_score = -1
                        fresh_reason = (
                            f"Profit take: +{_unrealized_pct:.1f}% gain exceeds "
                            f"{_profit_take_gain_pct:.0f}% threshold"
                        )
                        _profit_state[sym] = _entry_key
                        _log(f"Profit take TRIGGER: {sym} gain={_unrealized_pct:+.1f}% >= {_profit_take_gain_pct:.0f}% threshold, sell_fraction={_profit_take_sell_fraction:.0%}", "magenta")
                    elif _unrealized_pct >= _profit_take_gain_pct and _entry_key and _profit_marker == _entry_key:
                        _log(f"Profit take SKIP (already taken): {sym} gain={_unrealized_pct:+.1f}% but already trimmed for entry={_entry_key[:16]}", "cyan")
```

with (adds `_profit_take_fired_fraction` init + tiered branch, keeps single-fire fallback):

```python
                _profit_take_enabled = bool(config.get("profit_take_enabled", False))
                _profit_take_gain_pct = float(config.get("profit_take_gain_pct", 40.0) or 40.0)
                _profit_take_sell_fraction = float(config.get("profit_take_sell_fraction", 0.50) or 0.50)
                _profit_take_tiers_cfg = config.get("profit_take_tiers") or []
                _profit_take_fired_fraction = None
                if _profit_take_enabled and fresh_score >= 0 and (
                    _profit_take_tiers_cfg
                    or (_profit_take_gain_pct > 0 and 0.0 < _profit_take_sell_fraction < 1.0)
                ):
                    _profit_state = _normalize_cache_mapping(strategy_cache, "_nexus_profit_take_state")
                    _entry_marker = entry_buy_ts
                    if isinstance(_entry_marker, str):
                        _entry_key = _entry_marker[:32]
                    elif hasattr(_entry_marker, "isoformat"):
                        try:
                            _entry_key = str(_entry_marker.isoformat())[:32]
                        except Exception:
                            _entry_key = str(_entry_marker)[:32]
                    else:
                        _entry_key = str(_entry_marker or "")
                    _profit_marker = _profit_state.get(sym)
                    if _profit_take_tiers_cfg:
                        _pt_tier = _profit_take_next_tier(config, _unrealized_pct, _entry_key, _profit_marker)
                        if _pt_tier is not None:
                            fresh_score = -1
                            fresh_reason = (
                                f"Profit take tier: +{_unrealized_pct:.1f}% gain crossed "
                                f"+{_pt_tier['gain']:.0f}% tier"
                            )
                            _profit_state[sym] = _pt_tier["new_marker"]
                            _profit_take_fired_fraction = _pt_tier["fraction"]
                            _log(f"Profit take tier TRIGGER: {sym} +{_unrealized_pct:+.1f}% >= +{_pt_tier['gain']:.0f}% tier, sell_fraction={_pt_tier['fraction']:.0%}", "magenta")
                    elif _unrealized_pct >= _profit_take_gain_pct and _entry_key and _profit_marker != _entry_key:
                        fresh_score = -1
                        fresh_reason = (
                            f"Profit take: +{_unrealized_pct:.1f}% gain exceeds "
                            f"{_profit_take_gain_pct:.0f}% threshold"
                        )
                        _profit_state[sym] = _entry_key
                        _log(f"Profit take TRIGGER: {sym} gain={_unrealized_pct:+.1f}% >= {_profit_take_gain_pct:.0f}% threshold, sell_fraction={_profit_take_sell_fraction:.0%}", "magenta")
                    elif _unrealized_pct >= _profit_take_gain_pct and _entry_key and _profit_marker == _entry_key:
                        _log(f"Profit take SKIP (already taken): {sym} gain={_unrealized_pct:+.1f}% but already trimmed for entry={str(_entry_key)[:16]}", "cyan")
```

- [ ] **Step 4: Thread the tier fraction into `extras`** at `:17472-17473`. Replace:

```python
    if fresh_score == -1 and "Profit take" in (fresh_reason or ""):
        extras["sell_fraction"] = float(config.get("profit_take_sell_fraction", 0.50) or 0.50)
```

with:

```python
    if fresh_score == -1 and "Profit take" in (fresh_reason or ""):
        extras["sell_fraction"] = (
            _profit_take_fired_fraction if _profit_take_fired_fraction is not None
            else float(config.get("profit_take_sell_fraction", 0.50) or 0.50)
        )
```

- [ ] **Step 5: Prefer the threaded fraction in `_finalize_scores`** at `:17677-17678` and `:17685-17686`. In BOTH sites replace:

```python
                out[sym]["sell_fraction"] = float(config.get("profit_take_sell_fraction", 0.50) or 0.50)
```

with:

```python
                out[sym]["sell_fraction"] = _epr_extras.get(
                    "sell_fraction", float(config.get("profit_take_sell_fraction", 0.50) or 0.50)
                )
```

- [ ] **Step 6: Register schema keys** on line 1. Replace `"profit_take_gain_pct": 40.0, "profit_take_sell_fraction": 0.5,` with `"profit_take_gain_pct": 40.0, "profit_take_sell_fraction": 0.5, "profit_take_enabled": false, "profit_take_tiers": [],`

- [ ] **Step 7: Run tests + import + schema validity**

Run: `cd backend && python3 -c "import strategies.graph_nexus_analysis; import json,re; l=open('strategies/graph_nexus_analysis.py').readline(); json.loads(re.search(r'INTELLISTOCK_SCHEMA:\s*(\{.*\})\s*$', l.strip()).group(1)); print('OK')"`
Then: `cd backend && python3 -m pytest tests/test_tiered_profit_take.py tests/test_nexus_evaluate_position_risk.py -q`
Expected: PASS (all tiered tests + the existing risk suite unchanged — single-fire equivalence holds).

- [ ] **Step 8: Commit**

```bash
git add backend/strategies/graph_nexus_analysis.py backend/tests/test_tiered_profit_take.py
git commit -m "feat(nexus): staged profit-take tiers wired through sell_fraction plumbing + schema"
```

---

### Task 3: Full-suite bisect verification

- [ ] **Step 1: `gitnexus_detect_changes`** — confirm only expected symbols/flows; warn if HIGH/CRITICAL.
- [ ] **Step 2: Full suite head**: `cd backend && python3 -m pytest tests/ -q -p no:cacheprovider --continue-on-collection-errors --color=no 2>&1 | grep -E "passed|failed|error" | tail -1`
- [ ] **Step 3:** Compare failing set to base `5abc86c` (21 known). Must be ZERO new failures. If any new failure, return to Task 2.

## Self-review notes
- Spec coverage: change A (tiered code) = Tasks 1-2; schema registration = Task 2 Step 6; validation protocol = spec-only (run later, user-gated); config B (doc-179) = NOT in this plan (applied post-validation by the user). #1 bypass-off + #2 = config, not code — applied to doc-179 later, not here.
- Types consistent: `_profit_take_next_tier` → `dict|None` with `gain`/`fraction`/`new_marker` used identically in Tasks 1-2.
- No placeholders: all steps carry real code + commands.
