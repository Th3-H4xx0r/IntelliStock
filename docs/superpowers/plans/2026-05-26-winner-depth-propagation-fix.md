# Winner-depth + propagation-hygiene fix (A+B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development. Steps use `- [ ]` checkboxes. C (re-entry guard, idle-cash) is DEFERRED to a backtest-gated follow-up — not in this plan.

**Goal:** Recover the cold-start P&L gap by (A) re-tuning 5 doc-179 knobs toward winner depth + propagation hygiene, and (B) adding a fail-safe per-seed propagation fan-out cap that stops one news item flooding the scoring queue.

**Architecture:** A = value-only writes to prod Strategies doc 179 (authoritative; code already reads via `config.get`, so no code edit). B = one pure helper `_cap_propagation_fanout_per_seed` wired into the 1-hop propagation loop, gated by a new knob `propagation_max_per_seed` (code default 0 = disabled; shipped on at 8 in doc 179 + schema + cli).

**Tech Stack:** Python, pytest, RethinkDB (prod doc 179 via RETHINKDB_HOST=REDACTED-IP:28015), single strategy file `backend/strategies/graph_nexus_analysis.py`.

---

## A — doc 179 value changes (no code)
| Knob | From | To |
|------|------|----|
| winner_add_max_multiple_of_entry | 2 | 3 |
| winner_add_max_drawdown_from_peak_pct | 8 | 10 |
| propagation_expansion_min_raw_score | 0.5 | 0.56 |
| max_propagated_scoring_slots | 40 | 20 |
| momentum_discovery_max_per_day | 6 | 12 |
| propagation_max_per_seed (NEW) | — | 8 |

Applied via a merge-only script (Task 5). Rollback = restore the From column + delete propagation_max_per_seed.

---

### Task 1: B — per-seed fan-out cap helper (TDD)

**Files:**
- Test: `backend/tests/test_winner_depth_propagation_fix.py` (create)
- Modify: `backend/strategies/graph_nexus_analysis.py` (add module-level helper near the other propagation helpers, before the propagation function)

- [ ] **Step 1: Write failing tests**

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from strategies.graph_nexus_analysis import _cap_propagation_fanout_per_seed

def _edges(src, n, conf_base=0.5):
    return [{"source": src, "target": f"T{i}", "confidence": conf_base + i*0.01, "revenue_pct": 0.0} for i in range(n)]

def test_caps_over_limit_seed_keeping_strongest():
    edges = _edges("HOOD", 20)
    out = _cap_propagation_fanout_per_seed(edges, {"propagation_max_per_seed": 8})
    assert len(out) == 8
    kept = {e["target"] for e in out}
    # strongest confidence = highest index
    assert kept == {f"T{i}" for i in range(12, 20)}

def test_under_limit_seed_unchanged():
    edges = _edges("HOOD", 5)
    out = _cap_propagation_fanout_per_seed(edges, {"propagation_max_per_seed": 8})
    assert [e["target"] for e in out] == [f"T{i}" for i in range(5)]

def test_absent_or_zero_is_noop():
    edges = _edges("HOOD", 50)
    assert _cap_propagation_fanout_per_seed(edges, {}) is edges
    assert _cap_propagation_fanout_per_seed(edges, {"propagation_max_per_seed": 0}) is edges

def test_multiple_seeds_capped_independently_order_preserved():
    edges = _edges("AAA", 10) + _edges("BBB", 3)
    out = _cap_propagation_fanout_per_seed(edges, {"propagation_max_per_seed": 4})
    assert sum(1 for e in out if e["source"] == "AAA") == 4
    assert sum(1 for e in out if e["source"] == "BBB") == 3
    # original relative order preserved
    assert out == [e for e in edges if e in out]
```

- [ ] **Step 2: Run, expect ImportError/fail.** `python3 -m pytest backend/tests/test_winner_depth_propagation_fix.py -q`

- [ ] **Step 3: Implement helper** (insert before the propagation function that contains `all_1hop = out_edges + in_edges`, ~line 15300-15700 region; place at module scope):

```python
def _cap_propagation_fanout_per_seed(edges: list[dict], config: dict) -> list[dict]:
    """Cap how many 1-hop propagation edges a single source ("seed") contributes,
    keeping the strongest-coupled neighbors (confidence, then revenue_pct).
    Prevents one news item (e.g. a HOOD earnings hit) from injecting its whole
    COMPETES_WITH cohort and flooding the scoring/backfill queue.
    Disabled (no-op, returns the same list object) when propagation_max_per_seed<=0."""
    max_per_seed = int(config.get("propagation_max_per_seed", 0) or 0)
    if max_per_seed <= 0 or not edges:
        return edges
    by_source: dict[str, list[dict]] = {}
    for e in edges:
        by_source.setdefault(str(e.get("source") or ""), []).append(e)
    keep: set[int] = set()
    for group in by_source.values():
        if len(group) <= max_per_seed:
            keep.update(id(e) for e in group)
        else:
            ranked = sorted(
                group,
                key=lambda e: (float(e.get("confidence", 0.0) or 0.0), float(e.get("revenue_pct", 0.0) or 0.0)),
                reverse=True,
            )[:max_per_seed]
            keep.update(id(e) for e in ranked)
    return [e for e in edges if id(e) in keep]
```

- [ ] **Step 4: Run tests, expect PASS.**
- [ ] **Step 5: Commit** `feat(nexus): per-seed propagation fan-out cap helper (B)`

### Task 2: B — wire the cap into the 1-hop loop

**Files:** Modify `backend/strategies/graph_nexus_analysis.py:15762` (right after `all_1hop = out_edges + in_edges`).

- [ ] **Step 1:** Insert immediately after `all_1hop = out_edges + in_edges`:

```python
        all_1hop = _cap_propagation_fanout_per_seed(all_1hop, config)
```

- [ ] **Step 2: Run** the new test file + a smoke import: `python3 -c "import backend.strategies.graph_nexus_analysis"` (from repo root with PYTHONPATH=backend). Expect no error.
- [ ] **Step 3: Commit** `feat(nexus): apply per-seed fan-out cap in 1-hop propagation`

### Task 3: B — plumb the knob (effective-config + log + cli + schema)

**Files:** Modify `graph_nexus_analysis.py` (_get_effective_nexus_config ~8229; effective-config log line ~20237; schema line 1), `backend/cli.py` (~1110).

- [ ] **Step 1: Test effective-config + schema validity** (append to test file):

```python
import json, re, pathlib
from strategies.graph_nexus_analysis import _get_effective_nexus_config

def test_effective_config_resolves_per_seed_default_and_value():
    assert _get_effective_nexus_config({})["propagation_max_per_seed"] == 0
    assert _get_effective_nexus_config({"propagation_max_per_seed": 8})["propagation_max_per_seed"] == 8

def test_schema_line_is_valid_json_and_has_new_knob():
    src = pathlib.Path(__file__).resolve().parents[1] / "strategies" / "graph_nexus_analysis.py"
    first = src.read_text().splitlines()[0]
    blob = first.split("INTELLISTOCK_SCHEMA:", 1)[1].strip()
    parsed = json.loads(blob)
    assert "propagation_max_per_seed" in parsed["config"]
```

- [ ] **Step 2: Run, expect fail** (KeyError / missing knob).
- [ ] **Step 3: Implement** — add to the `_get_effective_nexus_config` return dict (near line 8229):

```python
        "propagation_max_per_seed": int(config.get("propagation_max_per_seed", 0) or 0),
```

  add to the effective-config log line (near `prop_slots:` at ~20237), appended in the `discover=` group:

```python
            f"seedcap:{_effective_cfg['propagation_max_per_seed']} "
```

  add to the schema JSON (line 1) immediately after `"max_propagated_scoring_slots": 15,`: `"propagation_max_per_seed": 8, `
  add to `backend/cli.py` knob dict after the `max_propagated_scoring_slots` entry (~1110):

```python
            'propagation_max_per_seed': ('Max 1-hop propagation edges per source seed; caps news-item fan-out (0=off, default 8)', 8),
```

- [ ] **Step 4: Run tests, expect PASS.**
- [ ] **Step 5: Commit** `feat(nexus): surface propagation_max_per_seed in config/cli/diagnostic`

### Task 4: Regression verification

- [ ] **Step 1:** Run the focused suites:
`python3 -m pytest backend/tests/test_winner_depth_propagation_fix.py backend/tests/test_nexus_discovery_expansion_fix.py backend/tests/test_nexus_fixes.py -q`
Expect: new file green; no NEW failures vs the 21-failure baseline.

### Task 5: Apply A+B to prod doc 179 (merge-only)

- [ ] **Step 1:** Write `scripts/apply_doc179_winner_depth_fix.py` — connects (RETHINKDB_HOST), reads `Strategies.get(179)`, prints before→after for the 6 keys, sets them in `doc['strategies'][0]['config']`, writes back with `.update()` (merge — never replace; preserves all other keys + secrets), re-reads to confirm.
- [ ] **Step 2:** Dry-run (print only), then apply, then confirm the 6 values.

### Task 6: Bug-sweep + push
- [ ] Parallel-agent bug sweep on the diff; fix findings.
- [ ] Full pytest baseline check (0 new failures).
- [ ] Commit any fixes; `git push`.

## Self-review
- Spec coverage: A (5 knobs + new) ✓ Task5/3; B (cap+wire+plumb) ✓ Tasks1-3; C deferred ✓ (stated). C2 idle-cash = flood-downstream, covered by A/B ✓.
- Placeholders: none.
- Type consistency: `_cap_propagation_fanout_per_seed(edges, config)` + `propagation_max_per_seed` used identically across tasks ✓.
