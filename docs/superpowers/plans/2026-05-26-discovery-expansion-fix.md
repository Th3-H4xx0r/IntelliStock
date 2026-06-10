# Discovery-Expansion Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the discovery funnel from flooding the equity pool with leveraged/inverse/commodity ETFs and from diluting position sizing, so a cold-start run on kimi-k2.5 beats backtest 404780's +152%.

**Architecture:** Two config-gated code changes in `backend/strategies/graph_nexus_analysis.py`: (1) exclude leveraged/inverse/commodity ETFs from momentum *equity* discovery; (2) concentrate the buy-side code defaults (pools/overlay/max-buys) while keeping discovery breadth wide. Queue saturation (Component 1c) and winner retention (Component 3) are addressed *upstream* by these two changes — no risky surgery on the queue/rotation displacement code — and validated by the backtest. The aggressive prod values in Strategies doc 179 are rolled back by an operator DB write (operational, not code).

**Tech Stack:** Python, RethinkDB, pytest. Backend tests run from repo root.

**Spec:** `docs/superpowers/specs/2026-05-26-discovery-expansion-fix-design.md`

---

## File Structure

- `backend/strategies/graph_nexus_analysis.py` — schema template (line 1), `_get_effective_nexus_config` (≈8125), `_discover_stocks_from_momentum` (≈10978), ETF ticker sets (≈12196), in-function `config.get` fallbacks (e.g. ≈22758).
- `backend/cli.py` — knob defaults (sync).
- `backend/interactive_utils.py` — nexus-config display fallbacks (sync).
- `backend/tests/test_nexus_discovery_expansion_fix.py` — NEW: ETF-exclusion + concentrated-default tests.
- `backend/tests/test_nexus_discovery_expansion.py` — UPDATE: `test_effective_config_expansion_defaults` (≈148) and `test_schema_template_expansion_defaults` (≈176) to the concentrated values.

**Config changes (code defaults):**

| key | current code default | new |
|---|--:|--:|
| momentum_discovery_exclude_leveraged_etfs | (absent) | **true (NEW)** |
| pool_a_base | 12 | 10 |
| pool_b_base | 6 | 4 |
| llm_overlay_max_stock_candidates | 40 | 30 |
| max_stock_buys_per_day | 12 | 8 |
| max_discovered_stocks | 90 | 90 (keep wide) |
| momentum_discovery_max_per_day | 6 | 6 (keep) |
| allocation_max_new_stock_buys | 6 | 6 (keep) |

**Operator DB write to Strategies doc 179 (operational, after decouple):** set pool_a_base=10, pool_b_base=4, llm_overlay_max_stock_candidates=30, max_stock_buys_per_day=8, momentum_discovery_max_per_day=6, momentum_discovery_exclude_leveraged_etfs=true; keep max_discovered_stocks broad (90–120). Merge-only; never print api_key.

---

### Task 1: Leveraged/inverse ETF set + momentum-discovery exclusion

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` (add set near ≈12205; add filter in `_discover_stocks_from_momentum` after candidate collection ≈11084)
- Test: `backend/tests/test_nexus_discovery_expansion_fix.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_nexus_discovery_expansion_fix.py
import backend.strategies.graph_nexus_analysis as gna


def test_leveraged_inverse_set_covers_404780_offenders():
    s = gna._LEVERAGED_INVERSE_ETF_TICKERS
    for t in ("SOXS", "OILD", "BOIL", "KOLD", "COPX", "COPZ", "CPER", "KCOP", "SLVX"):
        assert t in s, f"{t} missing from leveraged/inverse set"


def test_momentum_excluded_set_is_union_with_commodity():
    excl = gna._MOMENTUM_EXCLUDED_ETF_TICKERS
    assert gna._COMMODITY_ETF_TICKERS <= excl
    assert gna._LEVERAGED_INVERSE_ETF_TICKERS <= excl
    # plain equities must NOT be excluded
    for t in ("INTC", "ICHR", "NGD", "GEV", "SLAB"):
        assert t not in excl


def test_filter_drops_leveraged_etfs_when_enabled():
    cands = [("INTC", 25.0, 50.0), ("SOXS", 30.0, 10.0), ("ICHR", 24.0, 41.0), ("BOIL", 40.0, 5.0)]
    out = gna._filter_momentum_etf_candidates(cands, {"momentum_discovery_exclude_leveraged_etfs": True})
    assert [c[0] for c in out] == ["INTC", "ICHR"]


def test_filter_noop_when_disabled():
    cands = [("INTC", 25.0, 50.0), ("SOXS", 30.0, 10.0)]
    out = gna._filter_momentum_etf_candidates(cands, {"momentum_discovery_exclude_leveraged_etfs": False})
    assert [c[0] for c in out] == ["INTC", "SOXS"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_nexus_discovery_expansion_fix.py -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`
Expected: FAIL — `_LEVERAGED_INVERSE_ETF_TICKERS` / `_MOMENTUM_EXCLUDED_ETF_TICKERS` / `_filter_momentum_etf_candidates` not defined.

- [ ] **Step 3: Add the sets + pure helper** (after `_SECTOR_ETF_TICKERS`, ≈12211)

```python
# Fix (2026-05-26, backtest 404780): leveraged/inverse/commodity ETFs that top a
# 20-day return screen and masquerade as momentum EQUITIES. mom/day=12 flooded the
# equity pool with these (SOXS/OILD/BOIL/KOLD/COPZ...), saturating the backfill queue
# and starving real winners (INTC/SLAB/TWST). Excluded from momentum equity discovery;
# ETF exposure still flows through the dedicated, capped ETF-allocation path.
_LEVERAGED_INVERSE_ETF_TICKERS = frozenset({
    "SOXL", "SOXS", "OILU", "OILD", "BOIL", "KOLD", "NRGU", "NRGD", "GUSH", "DRIP",
    "LABU", "LABD", "TQQQ", "SQQQ", "UVXY", "SVXY", "UVIX", "TNA", "TZA", "FAS", "FAZ",
    "YINN", "YANG", "UCO", "SCO", "UGL", "GLL", "AGQ", "ZSL", "JNUG", "JDST",
    "NUGT", "DUST", "ERX", "ERY", "TECL", "TECS", "SPXL", "SPXS", "UPRO", "SPXU",
    "UDOW", "SDOW", "TMF", "TMV", "BITX", "CONL", "DPST", "WANT", "CWEB",
    "COPX", "COPZ", "CPER", "KCOP", "SLVX", "SLVO",
})

# Union with the existing commodity-ETF set — both are noise in momentum EQUITY discovery.
_MOMENTUM_EXCLUDED_ETF_TICKERS = _LEVERAGED_INVERSE_ETF_TICKERS | set(_COMMODITY_ETF_TICKERS)


def _filter_momentum_etf_candidates(
    candidates: list[tuple[str, float, float]], config: dict,
) -> list[tuple[str, float, float]]:
    """Drop leveraged/inverse/commodity ETFs from momentum equity-discovery
    candidates. Gated by ``momentum_discovery_exclude_leveraged_etfs`` (default True)."""
    if not config.get("momentum_discovery_exclude_leveraged_etfs", True):
        return candidates
    return [c for c in candidates if str(c[0]).strip().upper() not in _MOMENTUM_EXCLUDED_ETF_TICKERS]
```

- [ ] **Step 4: Wire the filter into `_discover_stocks_from_momentum`** (immediately before the sort at ≈11087)

```python
    # Fix (2026-05-26): exclude leveraged/inverse/commodity ETFs from momentum
    # equity discovery (backtest 404780 queue-flooding). See _filter_momentum_etf_candidates.
    _pre = len(candidates)
    candidates = _filter_momentum_etf_candidates(candidates, config)
    if len(candidates) < _pre:
        _log(f"  Momentum ETF exclusion: dropped {_pre - len(candidates)} leveraged/inverse/commodity ETF candidate(s)", "yellow")

    # Sort by strongest momentum first; ticker ASC tiebreak for full determinism
    candidates.sort(key=lambda x: (-max(x[1], x[2]), -x[1], -x[2], x[0]))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_nexus_discovery_expansion_fix.py -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/strategies/graph_nexus_analysis.py backend/tests/test_nexus_discovery_expansion_fix.py
git commit -m "feat(nexus): exclude leveraged/inverse/commodity ETFs from momentum discovery"
```

---

### Task 2: New config gate + concentrated buy-side defaults

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` (schema line 1; `_get_effective_nexus_config` ≈8139–8225; in-function fallbacks e.g. ≈22758)
- Modify: `backend/cli.py`, `backend/interactive_utils.py` (sync defaults)
- Test: `backend/tests/test_nexus_discovery_expansion_fix.py`; update `backend/tests/test_nexus_discovery_expansion.py`

- [ ] **Step 1: Write the failing test** (append to the new test file)

```python
def test_effective_config_concentrated_buyside_defaults():
    eff = gna._get_effective_nexus_config({})
    assert eff["pool_a_base"] == 10
    assert eff["pool_b_base"] == 4
    assert eff["llm_overlay_max_stock_candidates"] == 30
    assert eff["max_stock_buys_per_day"] == 8
    # discovery breadth stays wide
    assert eff["max_discovered_stocks"] == 90
    assert eff["momentum_discovery_max_per_day"] == 6
    # new gate defaults on
    assert eff["momentum_discovery_exclude_leveraged_etfs"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_nexus_discovery_expansion_fix.py::test_effective_config_concentrated_buyside_defaults -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`
Expected: FAIL (pool_a_base==12, and missing exclude key).

- [ ] **Step 3: Edit the schema template (line 1)** — change `"pool_a_base": 12`→`10`, `"pool_b_base": 6`→`4`, `"llm_overlay_max_stock_candidates": 40`→`30`, `"max_stock_buys_per_day": 12`→`8`, and add `"momentum_discovery_exclude_leveraged_etfs": true` (place adjacent to the other momentum_discovery_* keys).

- [ ] **Step 4: Edit `_get_effective_nexus_config`** (≈8139–8225) — update the fallbacks to match: `pool_a_base` 12→10, `pool_b_base` 6→4, `llm_overlay_max_stock_candidates` 40→30, `max_stock_buys_per_day` 12→8, and add `"momentum_discovery_exclude_leveraged_etfs": bool(config.get("momentum_discovery_exclude_leveraged_etfs", True)),`.

- [ ] **Step 5: Sync scattered in-function fallbacks** — grep and update each `config.get("pool_a_base", 12)`→`10`, `config.get("pool_b_base", 6)`→`4`, `config.get("llm_overlay_max_stock_candidates", 40)`→`30`, `config.get("max_stock_buys_per_day", 12)`→`8` in `graph_nexus_analysis.py` (e.g. ≈22758), `backend/cli.py`, `backend/interactive_utils.py`.

Run to find them: `grep -rnE "pool_a_base\", 12|pool_b_base\", 6|llm_overlay_max_stock_candidates\", 40|max_stock_buys_per_day\", 12" backend --include='*.py'`

- [ ] **Step 6: Update the existing default-assertion tests** in `backend/tests/test_nexus_discovery_expansion.py` (`test_effective_config_expansion_defaults` ≈148, `test_schema_template_expansion_defaults` ≈176) to the concentrated values (pool 10/4, overlay 30, max_buys 8; keep max_disc 90, mom/day 6).

- [ ] **Step 7: Run the full discovery test files**

Run: `python3 -m pytest backend/tests/test_nexus_discovery_expansion_fix.py backend/tests/test_nexus_discovery_expansion.py -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`
Expected: PASS (all).

- [ ] **Step 8: Commit**

```bash
git add backend/strategies/graph_nexus_analysis.py backend/cli.py backend/interactive_utils.py backend/tests/test_nexus_discovery_expansion_fix.py backend/tests/test_nexus_discovery_expansion.py
git commit -m "feat(nexus): concentrate buy-side defaults + add momentum ETF-exclusion gate"
```

---

### Task 3: Full-suite regression check + bug sweep + validation handoff

- [ ] **Step 1: Run the backend suite** (confirm 0 new failures vs the 21-failure baseline)

Run: `python3 -m pytest backend/tests/ -q --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`
Expected: no new failures beyond the documented 21 pre-existing.

- [ ] **Step 2: Parallel bug sweep** — dispatch 3 agents (caller-completeness/determinism, config-consistency across all sync sites, ETF-set correctness + no-equity-excluded). Fix ALL findings before push.

- [ ] **Step 3: Push** (only after the sweep). Then operational handoff:
  - Decouple `nexus-live` from Strategies doc 179 (own doc, safe config).
  - Operator applies the concentrated config + ETF gate to the backtest doc (179 or decoupled test doc).
  - Operator: redeploy → clear → cold backtest on kimi-k2.5 → share result ID → 3-agent decomposition vs +152%.

**Components 1c (queue) & 3 (retention):** no direct code change — addressed upstream by Tasks 1+2 (de-crowded discovery + concentrated buys give clean early entries so winner_add/winner_lock engage naturally). Validate in the backtest; tune rotation knobs from data only if winners are still churned.

---

## Self-Review

- **Spec coverage:** Component 1a → Task 1. Component 1b (mom/day) → kept at code default 6; the 12 was prod-only (operator write, documented). Component 2 → Task 2. Component 1c + 3 → upstream via Tasks 1+2 (documented rationale). Component 4: ICHR → expected free from Task 1 (validate in backtest); VOYG → follow-up spec. Validation flow → Task 3. Operational/decouple → Task 3 Step 3.
- **Placeholder scan:** none — all code blocks concrete; sync sites have a grep command.
- **Type consistency:** `_LEVERAGED_INVERSE_ETF_TICKERS` (frozenset), `_MOMENTUM_EXCLUDED_ETF_TICKERS` (set), `_filter_momentum_etf_candidates(candidates, config) -> list[tuple]`, config key `momentum_discovery_exclude_leveraged_etfs` — names consistent across Tasks 1–2 and tests.
