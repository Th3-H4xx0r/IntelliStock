# LLM Cost Attribution Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix LLM cost rows recording the scoped runtime instance ID (`main|<24-hex>`) instead of the numeric BacktestResults ID. Add live-vs-backtest separation to the cost screen. Wipe stale `LLMUsage` data before deploying.

**Architecture:** Six `llm_call_context(backtest_id=instance_id, ...)` calls in `graph_nexus_analysis.py` override the broker's correct outer telemetry frame. Removing those overrides lets the outer frame's numeric backtest_id (or `None` for live) propagate correctly. Cost aggregation endpoint then partitions rows by `backtest_id IS NULL` to emit `kind="live"` (keyed by instance_id) or `kind="backtest"` (keyed by numeric id) buckets.

**Tech Stack:** Python 3 (broker + FastAPI endpoints), Vue 3 (frontend), RethinkDB (LLMUsage table), pytest.

**Spec:** `docs/superpowers/specs/2026-05-21-llm-cost-attribution-fix-design.md`

---

## File Structure

**Modified:**
- `backend/strategies/graph_nexus_analysis.py` — drop `backtest_id=` arg from 6 nested `llm_call_context` calls (one commit).
- `backend/api/main.py` — extend `_llm_usage_by_backtest` to also emit `kind="live"` buckets keyed by `instance_id`; emit `kind` and `display_label` on every row.
- `backend/tests/test_api_llm_usage.py` — 3 new tests for live-bucket + kind/display_label semantics.
- `frontend/src/views/TokenUsageView.vue` — section heading, KIND pill column, navigation gate.

**Untouched (no code changes needed, but verified via tests):**
- `backend/llm_telemetry.py` — context-stack semantics unchanged.
- `backend/strategies/{ml_news.py, earnings.py, nexus_analyst_panel.py}` — already correct.
- `frontend/src/views/BacktestDetailView.vue` — AI Credits card works automatically once attribution is correct.

---

## Task 1: Fix the 6 strategy sites in graph_nexus_analysis.py

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` lines 3227, 3278, 3433, 3493, 4323, 13901
- Test: rely on existing telemetry tests (no new strategy-level unit test — the merge semantics are already covered in `backend/tests/test_llm_telemetry.py`)

- [ ] **Step 1.1: Use Edit (replace_all=false) for each of the 6 sites**

Each site currently looks like:
```python
with llm_call_context(
    backtest_id=instance_id,
    strategy="GraphNexusAnalysis",
    call_site="<site_label>",
):
```

(Or `backtest_id=_sent_instance_id` for the sentiment site at line 13901.)

After the fix, each site looks like:
```python
with llm_call_context(
    strategy="GraphNexusAnalysis",
    call_site="<site_label>",
):
```

**The 6 exact edits to make:**

| # | Line | call_site label | old_string | new_string |
|---|---|---|---|---|
| 1 | 3227 | `company_classification` (main) | `with llm_call_context(\n        backtest_id=instance_id,\n        strategy="GraphNexusAnalysis",\n        call_site="company_classification",\n    ):` | `with llm_call_context(\n        strategy="GraphNexusAnalysis",\n        call_site="company_classification",\n    ):` |
| 2 | 3278 | `company_classification` (retry-split) | `with llm_call_context(\n            backtest_id=instance_id,\n            strategy="GraphNexusAnalysis",\n            call_site="company_classification",\n        ):` | `with llm_call_context(\n            strategy="GraphNexusAnalysis",\n            call_site="company_classification",\n        ):` |
| 3 | 3433 | `macro_classification` (main) | `with llm_call_context(\n        backtest_id=instance_id,\n        strategy="GraphNexusAnalysis",\n        call_site="macro_classification",\n    ):` | `with llm_call_context(\n        strategy="GraphNexusAnalysis",\n        call_site="macro_classification",\n    ):` |
| 4 | 3493 | `macro_classification` (retry-split) | `with llm_call_context(\n            backtest_id=instance_id,\n            strategy="GraphNexusAnalysis",\n            call_site="macro_classification",\n        ):` | `with llm_call_context(\n            strategy="GraphNexusAnalysis",\n            call_site="macro_classification",\n        ):` |
| 5 | 4323 | `active_event_maintenance` | `with llm_call_context(\n                    backtest_id=instance_id,\n                    strategy="GraphNexusAnalysis",\n                    call_site="active_event_maintenance",\n                ):` | `with llm_call_context(\n                    strategy="GraphNexusAnalysis",\n                    call_site="active_event_maintenance",\n                ):` |
| 6 | 13901 | `sentiment` | `with llm_call_context(\n            backtest_id=_sent_instance_id,\n            strategy="GraphNexusAnalysis",\n            call_site="sentiment",\n        ):` | `with llm_call_context(\n            strategy="GraphNexusAnalysis",\n            call_site="sentiment",\n        ):` |

Two notes:
- Sites #2, #4, #5 have deeper indentation (12, 12, 16 spaces respectively) — make sure the Edit's `old_string` matches the existing indentation exactly.
- Site #6 uses `_sent_instance_id`, not `instance_id`. The lines just before it (13897-13899) compute `_sent_instance_id` from `config["runtime_instance_id"]`. That computation is no longer needed for telemetry, BUT may be referenced elsewhere — leave it in place; only delete the `backtest_id=` line of the `with` call.

- [ ] **Step 1.2: Verify no other `backtest_id=` overrides remain in this file**

Run: `grep -n "backtest_id=" backend/strategies/graph_nexus_analysis.py`

Expected: zero matches inside `llm_call_context(...)` calls. (Other matches that aren't telemetry — e.g., function args, dict keys, log messages — are fine.)

- [ ] **Step 1.3: Syntax check**

Run: `python -c "import ast; ast.parse(open('backend/strategies/graph_nexus_analysis.py', encoding='utf-8').read()); print('ok')"`

Expected: `ok`.

- [ ] **Step 1.4: Commit**

```bash
git add backend/strategies/graph_nexus_analysis.py
git commit -m "fix(nexus): stop overriding telemetry backtest_id with scoped runtime instance_id"
```

No Co-Authored-By trailer (per project memory).

---

## Task 2: Extend `_llm_usage_by_backtest` endpoint with live-bucket aggregation + kind/display_label

**Files:**
- Modify: `backend/api/main.py` (replace `_llm_usage_by_backtest` at line 3283)
- Test: `backend/tests/test_api_llm_usage.py` (add 3 tests at end of file)

- [ ] **Step 2.1: Append 3 failing tests to `backend/tests/test_api_llm_usage.py`**

```python
def test_by_backtest_groups_backtest_rows_by_backtest_id():
    """Rows with non-null backtest_id group by backtest_id with kind='backtest'."""
    import importlib
    from backend.api import main as api_main

    fake_rows = [
        {"backtest_id": "100", "instance_id": "main", "ts": 1000, "input_tokens": 50, "output_tokens": 30, "total_cost_usd": 0.05, "ok": True},
        {"backtest_id": "100", "instance_id": "main", "ts": 1100, "input_tokens": 60, "output_tokens": 40, "total_cost_usd": 0.07, "ok": True},
        {"backtest_id": "200", "instance_id": "main", "ts": 1200, "input_tokens": 10, "output_tokens": 10, "total_cost_usd": 0.01, "ok": False},
    ]

    class _FakeBetween:
        def run(self, conn): return iter(fake_rows)
    class _FakeTable:
        def between(self, start, end, index): return _FakeBetween()
    class _FakeDB:
        def table(self, name): return _FakeTable()
    class _FakeR:
        def db(self, name): return _FakeDB()

    original_r = api_main._r_auth
    api_main._r_auth = _FakeR()
    try:
        out = api_main._llm_usage_by_backtest(range_str="24h", limit=100, conn=object())
    finally:
        api_main._r_auth = original_r

    rows_by_key = {(r.get("kind"), r.get("key")): r for r in out}
    assert ("backtest", "100") in rows_by_key
    assert ("backtest", "200") in rows_by_key
    r100 = rows_by_key[("backtest", "100")]
    assert r100["calls"] == 2
    assert r100["cost_usd"] == pytest.approx(0.12)
    assert r100["display_label"] == "Backtest #100"


def test_by_backtest_includes_live_rows_with_null_backtest_id():
    """Rows with null backtest_id group by instance_id with kind='live'."""
    import importlib
    from backend.api import main as api_main

    fake_rows = [
        {"backtest_id": None, "instance_id": "main", "ts": 1000, "input_tokens": 100, "output_tokens": 50, "total_cost_usd": 0.20, "ok": True},
        {"backtest_id": None, "instance_id": "main", "ts": 1100, "input_tokens": 200, "output_tokens": 100, "total_cost_usd": 0.40, "ok": True},
        {"backtest_id": "", "instance_id": "paper", "ts": 1200, "input_tokens": 30, "output_tokens": 20, "total_cost_usd": 0.06, "ok": True},
    ]

    class _FakeBetween:
        def run(self, conn): return iter(fake_rows)
    class _FakeTable:
        def between(self, start, end, index): return _FakeBetween()
    class _FakeDB:
        def table(self, name): return _FakeTable()
    class _FakeR:
        def db(self, name): return _FakeDB()

    original_r = api_main._r_auth
    api_main._r_auth = _FakeR()
    try:
        out = api_main._llm_usage_by_backtest(range_str="24h", limit=100, conn=object())
    finally:
        api_main._r_auth = original_r

    rows_by_key = {(r.get("kind"), r.get("key")): r for r in out}
    assert ("live", "main") in rows_by_key
    assert ("live", "paper") in rows_by_key
    rmain = rows_by_key[("live", "main")]
    assert rmain["calls"] == 2
    assert rmain["cost_usd"] == pytest.approx(0.60)
    assert rmain["display_label"] == "Live: main"


def test_by_backtest_emits_kind_and_display_label_on_every_row():
    """Every returned row must carry kind and display_label fields."""
    import importlib
    from backend.api import main as api_main

    fake_rows = [
        {"backtest_id": "777", "instance_id": "main", "ts": 1000, "input_tokens": 10, "output_tokens": 5, "total_cost_usd": 0.01, "ok": True},
        {"backtest_id": None, "instance_id": "main", "ts": 1100, "input_tokens": 20, "output_tokens": 10, "total_cost_usd": 0.02, "ok": True},
    ]

    class _FakeBetween:
        def run(self, conn): return iter(fake_rows)
    class _FakeTable:
        def between(self, start, end, index): return _FakeBetween()
    class _FakeDB:
        def table(self, name): return _FakeTable()
    class _FakeR:
        def db(self, name): return _FakeDB()

    original_r = api_main._r_auth
    api_main._r_auth = _FakeR()
    try:
        out = api_main._llm_usage_by_backtest(range_str="24h", limit=100, conn=object())
    finally:
        api_main._r_auth = original_r

    for row in out:
        assert "kind" in row, f"row missing kind: {row}"
        assert "display_label" in row, f"row missing display_label: {row}"
        assert row["kind"] in ("backtest", "live")
```

**Important note about existing tests:** the 5 pre-existing tests in `test_api_llm_usage.py` may assert the OLD output shape (e.g., asserting `row["backtest_id"]` directly with no `kind` field present). If they break in Step 2.4, update each broken assertion to use the new shape (`row["kind"]`, `row["display_label"]`, `row["key"]`). Do not delete or weaken existing tests — adapt them to the new field set.

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_api_llm_usage.py::test_by_backtest_groups_backtest_rows_by_backtest_id backend/tests/test_api_llm_usage.py::test_by_backtest_includes_live_rows_with_null_backtest_id backend/tests/test_api_llm_usage.py::test_by_backtest_emits_kind_and_display_label_on_every_row -v`

Expected: FAIL — at minimum, the assertion `("live", "main") in rows_by_key` fails because the current implementation drops null-backtest_id rows entirely.

- [ ] **Step 2.3: Replace `_llm_usage_by_backtest` body in `backend/api/main.py`**

Replace the function at line 3283 (the whole body, leave the signature):

```python
def _llm_usage_by_backtest(*, range_str: str, limit: int, conn) -> list:
    """Aggregate LLMUsage rows within ``range_str`` into per-run buckets.

    Rows where ``backtest_id`` is non-empty group by ``backtest_id`` (kind="backtest").
    Rows where ``backtest_id`` is null or empty group by ``instance_id`` (kind="live").
    Both kinds appear in the same flat list, sorted by cost desc, capped at ``limit``.
    """
    start, end = _range_to_ms_window(range_str)
    try:
        rows = list(
            _r_auth.db("IntelliStock").table("LLMUsage")
            .between(start, end, index="ts")
            .run(conn)
        )
    except Exception:
        rows = []

    buckets: dict = {}

    def _bucket(kind: str, key: str, instance_id: str | None):
        bk = (kind, key)
        if bk not in buckets:
            label = f"Backtest #{key}" if kind == "backtest" else f"Live: {key}"
            buckets[bk] = {
                "kind": kind,
                "key": key,
                "backtest_id": key if kind == "backtest" else None,
                "instance_id": instance_id,
                "display_label": label,
                "calls": 0,
                "tokens": 0,
                "cost_usd": 0.0,
                "first_ts": None,
                "last_ts": None,
                "ok_calls": 0,
                "failed_calls": 0,
            }
        return buckets[bk]

    for row in rows:
        bt_id = row.get("backtest_id")
        inst_id = row.get("instance_id")
        if bt_id is None or bt_id == "":
            # Live mode — bucket by instance_id. Untagged rows with no instance
            # either (e.g. /llm/test smoke probes) get dropped to avoid an
            # "(unset)" pseudo-row polluting the table.
            if inst_id is None or inst_id == "":
                continue
            b = _bucket("live", str(inst_id), str(inst_id))
        else:
            b = _bucket("backtest", str(bt_id), str(inst_id) if inst_id else None)

        b["calls"] += 1
        b["tokens"] += int(row.get("input_tokens", 0) or 0) + int(row.get("output_tokens", 0) or 0)
        b["cost_usd"] += float(row.get("total_cost_usd", 0.0) or 0.0)
        ts = int(row.get("ts", 0) or 0)
        if b["first_ts"] is None or ts < b["first_ts"]:
            b["first_ts"] = ts
        if b["last_ts"] is None or ts > b["last_ts"]:
            b["last_ts"] = ts
        if row.get("ok"):
            b["ok_calls"] += 1
        else:
            b["failed_calls"] += 1
        # Prefer non-empty instance_id from any row in the group.
        if not b.get("instance_id") and inst_id:
            b["instance_id"] = str(inst_id)

    out = sorted(buckets.values(), key=lambda x: x["cost_usd"], reverse=True)
    return out[: max(1, int(limit or 100))]
```

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_api_llm_usage.py -v`

Expected: all original tests PLUS the 3 new ones pass.

- [ ] **Step 2.5: Commit**

```bash
git add backend/api/main.py backend/tests/test_api_llm_usage.py
git commit -m "feat(api/llm-usage): partition by kind=backtest|live; emit display_label per row"
```

No Co-Authored-By trailer.

---

## Task 3: Update frontend TokenUsageView.vue (heading + KIND pill + click gate)

**Files:**
- Modify: `frontend/src/views/TokenUsageView.vue`

- [ ] **Step 3.1: Update the section heading**

Find line 539:
```html
<h2 class="mt-1 text-lg font-semibold text-slate-100">LLM cost by backtest</h2>
```

Replace with:
```html
<h2 class="mt-1 text-lg font-semibold text-slate-100">LLM cost by run</h2>
```

- [ ] **Step 3.2: Update the table to add a KIND column and use display_label**

Find the table around lines 555-580. The current row binding is:
```html
<tr
    v-for="row in byBacktest"
    :key="row.backtest_id"
    class="cursor-pointer hover:bg-slate-800/40"
    @click="$router.push({ name: 'backtest-detail', params: { id: row.backtest_id } })"
>
    <td class="px-5 py-3 font-mono text-xs text-slate-300">#{{ row.backtest_id }}</td>
    <td class="px-5 py-3 text-xs text-slate-300">{{ row.instance_id || '-' }}</td>
    ...
```

Replace with:
```html
<tr
    v-for="row in byBacktest"
    :key="`${row.kind}|${row.key}`"
    class="hover:bg-slate-800/40"
    :class="row.kind === 'backtest' ? 'cursor-pointer' : 'cursor-default'"
    @click="row.kind === 'backtest' && $router.push({ name: 'backtest-detail', params: { id: row.backtest_id } })"
>
    <td class="px-5 py-3 font-mono text-xs text-slate-300">{{ row.display_label }}</td>
    <td class="px-5 py-3">
        <span
            class="inline-flex rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
            :class="row.kind === 'backtest' ? 'bg-indigo-900/40 text-indigo-200' : 'bg-emerald-900/40 text-emerald-200'"
        >{{ row.kind }}</span>
    </td>
    <td class="px-5 py-3 text-xs text-slate-300">{{ row.instance_id || '-' }}</td>
    ...
```

Add a new `<th>` to the table header (around line 549, between BACKTEST and INSTANCE):
```html
<th class="px-5 py-3 text-left font-medium uppercase tracking-wide text-[11px] text-slate-400">KIND</th>
```

Rename the existing `BACKTEST` column header to `RUN`:
```html
<th class="px-5 py-3 text-left font-medium uppercase tracking-wide text-[11px] text-slate-400">RUN</th>
```

- [ ] **Step 3.3: Update the "Sorted by cost · click a row to open the backtest" caption**

Find the helper caption (search for `Sorted by cost`). Replace with:
```html
<span class="text-xs text-slate-500">Sorted by cost · click a Backtest row to open it; Live rows are summary-only</span>
```

- [ ] **Step 3.4: Update the empty-state text**

Find the `<tr v-if="!byBacktest.length">` row's text content (around line 575). Replace any "No backtests" copy with:
```html
No LLM cost data in this range. Backtest or live runs that made LLM calls will appear here.
```

- [ ] **Step 3.5: Build the frontend to verify no syntax errors**

```bash
cd frontend && npx vite build
```

Expected: build succeeds. `TokenUsageView*.js` chunk size should remain ~22kB (small delta from the changes).

- [ ] **Step 3.6: Commit**

```bash
git add frontend/src/views/TokenUsageView.vue
git commit -m "feat(ui/cost-screen): rename to LLM cost by run; add KIND pill column for backtest vs live"
```

No Co-Authored-By trailer.

---

## Task 4: Full test sweep + sanity checks

**Files:** none modified

- [ ] **Step 4.1: Run all phase-1-and-prior + new tests**

```bash
python -m pytest backend/tests/test_api_llm_usage.py backend/tests/test_llm_telemetry.py backend/tests/test_strategy_cache_persistence.py backend/tests/test_broker_live_boot_with_snapshot.py backend/tests/test_clear_main_instance_lookback_state.py -v
```

Expected: all pass.

- [ ] **Step 4.2: Run a broader sweep to catch unexpected regressions**

```bash
python -m pytest backend/tests/ -q --ignore=backend/tests/test_nexus_v25.py
```

(Ignoring `test_nexus_v25.py` because it has a pre-existing unrelated failure noted in prior session — confirm it's still the only failure if you skip the ignore.)

Expected: 0 failures (or the single pre-existing failure if you didn't ignore).

- [ ] **Step 4.3: Syntax check the modified backend files**

```bash
python -m py_compile backend/strategies/graph_nexus_analysis.py backend/api/main.py
```

Expected: no errors.

- [ ] **Step 4.4: Frontend build sanity (re-verify)**

```bash
cd frontend && npx vite build
```

Expected: succeeds; `TokenUsageView*.js` ~22kB.

---

## Task 5: Parallel bug sweep (5 agents)

**Files:** none modified — review only

- [ ] **Step 5.1: Dispatch 5 parallel agents covering**

1. **Strategy fix correctness** — verify all 6 sites in `graph_nexus_analysis.py` were edited correctly (no missed lines, no broken indentation, no accidental removal of `strategy=` or `call_site=`).
2. **Endpoint behavior** — read the updated `_llm_usage_by_backtest` carefully; look for ordering bugs, missing fields, dict-key collisions when same string appears as both a backtest_id and an instance_id.
3. **Test design** — verify the new tests actually exercise the new behavior (not trivially-passing assertions); check for monkeypatching gaps that could let buggy code pass.
4. **Frontend correctness** — read the Vue diff; confirm v-for key uniqueness, click handler gating, KIND pill colors, accessibility.
5. **Telemetry impact / regression scan** — confirm no other callers in the codebase relied on the strategy-overridden `backtest_id` for anything beyond the LLMUsage row (e.g., no log line, no comparison, no debugging output that broke).

- [ ] **Step 5.2: Apply HIGH-severity fixes if any are found**

Iterate: implement fixes → re-run Task 4 tests → if still clean, proceed. Document MED/LOW findings in the post-deploy notes for follow-up.

---

## Task 6: Pre-deploy ops (production RethinkDB)

**Files:** none modified — production ops via Tailscale to `REDACTED-IP:28015`

- [ ] **Step 6.1: Stop backtest 877964**

```python
# Use the API endpoint for graceful shutdown
import urllib.request, json
req = urllib.request.Request(
    "https://REDACTED-DOMAIN/backtests/877964/stop",
    method="POST",
    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    data=b"{}",
)
print(urllib.request.urlopen(req).read())
```

OR direct RethinkDB:
```python
from rethinkdb import RethinkDB
r = RethinkDB()
conn = r.connect(host='REDACTED-IP', port=28015, db='IntelliStock')
# Find the BacktestInstances row corresponding to backtest 877964 (instance="main", status="running")
candidates = list(r.table('BacktestInstances').filter({'instance': 'main', 'run': True}).run(conn))
for c in candidates:
    r.table('BacktestInstances').get(c['id']).update({'run': False}).run(conn)
    print(f"stopped BacktestInstances row id={c['id']}")
```

Expected: the broker container watching `BacktestInstances.run` flips to `False` and exits within ~10 seconds. Confirm `BacktestResults.id=877964` then has `status="stopped"`.

- [ ] **Step 6.2: Sanity-check no other running backtests on instance=main**

```python
remaining = int(r.table('BacktestInstances').filter({'instance': 'main', 'run': True}).count().run(conn) or 0)
print(f"remaining running backtests on instance=main: {remaining}")
```

Expected: 0. If > 0, decide per-case whether to stop them too.

- [ ] **Step 6.3: Wipe LLMUsage + LLMUsageDaily**

```python
res_llm = r.table('LLMUsage').delete().run(conn)
print(f"LLMUsage deleted: {res_llm.get('deleted')}")
existing = list(r.db('IntelliStock').table_list().run(conn))
if 'LLMUsageDaily' in existing:
    res_daily = r.table('LLMUsageDaily').delete().run(conn)
    print(f"LLMUsageDaily deleted: {res_daily.get('deleted')}")
else:
    print("LLMUsageDaily table not present; skipping")
```

Expected: deletes report a row count matching the prior table size. Tables remain (we only deleted rows, not the tables themselves).

- [ ] **Step 6.4: Confirm wipe**

```python
remaining = int(r.table('LLMUsage').count().run(conn) or 0)
print(f"LLMUsage remaining rows: {remaining}")
```

Expected: 0.

- [ ] **Step 6.5: (No commit — production ops only)**

---

## Task 7: Push + post-deploy validation

**Files:** none modified — just `git push`

- [ ] **Step 7.1: Confirm clean working tree**

```bash
git status --short
```

Expected: no uncommitted changes. (Spec + 3 task commits should all be present in `git log`.)

- [ ] **Step 7.2: List the commits about to be pushed**

```bash
git log --oneline origin/claude-code-integration..HEAD
```

Expected (approximately):
```
<sha7> feat(ui/cost-screen): rename to LLM cost by run; add KIND pill column for backtest vs live
<sha6> feat(api/llm-usage): partition by kind=backtest|live; emit display_label per row
<sha5> fix(nexus): stop overriding telemetry backtest_id with scoped runtime instance_id
<sha4> docs(spec): llm cost attribution fix design
```

- [ ] **Step 7.3: Push**

```bash
git push origin claude-code-integration
```

- [ ] **Step 7.4: Wait for Dockploy redeploy**

Approximately 4 minutes. Use a background `sleep 240` task.

- [ ] **Step 7.5: Post-deploy validation — kick a small smoke backtest**

Operator does this OR (if automated) via API:
- Create a 1-2 day backtest (e.g., 2026-05-19 → 2026-05-21) on instance=main with the same strategy 179.
- Watch the broker log for the first LLM call.

- [ ] **Step 7.6: Verify LLMUsage row shape**

```python
rows = list(r.table('LLMUsage').limit(5).run(conn))
for row in rows:
    print({k: row.get(k) for k in ('backtest_id', 'instance_id', 'strategy', 'call_site', 'ts')})
```

Expected: `backtest_id` is a numeric string (e.g., `"123456"`), NOT a `"main|<hash>"` string.

- [ ] **Step 7.7: Open the cost screen at `/cost`**

Expected: see a row like `Backtest #<id>` with KIND pill `BACKTEST`. No `#main|<hash>` rows present.

- [ ] **Step 7.8: Open `/backtests/<id>` for the smoke backtest**

Expected: the AI Credits card populates with real numbers; no "No LLM calls were attributed" message.

- [ ] **Step 7.9: Reindex GitNexus**

```bash
npx gitnexus analyze --embeddings
```

Expected: completes successfully.

---

## Out-of-Scope Reminders

- Not changing how the scoped runtime instance ID is constructed (still used for cache scoping, sentiment-cache scope, History scope log).
- Not adding a `mode` enum column to LLMUsage (the implicit derivation via `backtest_id is None` is sufficient).
- Not modifying `llm_telemetry.py`'s context-stack semantics.
- Not changing `BacktestDetailView.vue` (works automatically after the fix).
- Not backfilling pre-existing rows (we wiped them).
