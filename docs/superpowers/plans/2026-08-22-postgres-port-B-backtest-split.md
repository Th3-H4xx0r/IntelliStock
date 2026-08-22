# Postgres Port — Plan B: The `BacktestResults` Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the multi-MB `BacktestResults` document into three tables — a verbatim metadata row, an insert-only `BacktestSteps` table, and a hot `BacktestProgress` row — and port every writer and reader so the JSON the application sees is byte-identical to today's.

**Architecture:** `BacktestResults.doc` keeps every key that is not one of the six step arrays, verbatim, plus 16 STORED generated summary columns. The six arrays move to `BacktestSteps(backtest_id, kind, seq, final, doc)`. `status`, `progress`, `time_elapsed_seconds`, and `_last_active` move to `BacktestProgress`, which carries the `status_norm` index. A single `assemble(backtest_id)` function reconstructs the legacy document — including the legacy caps and lexicographic key order — and is the only thing detail and playback readers call.

**Tech Stack:** Python 3.11 (prod) / 3.14 (this laptop), PostgreSQL 17 + JSONB, `backend/db` (Plan A), pytest.

**Spec:** `docs/superpowers/specs/2026-08-22-postgres-port-design.md` (§3.5 is this plan's contract; §12 items 3–6 and 11 are its interface decisions)

**Depends on:** `docs/superpowers/plans/2026-08-22-postgres-port-A-db-core.md`. Plan A must be complete and green before Task 2 of this plan starts. Plan A's Task 5 already ships the `BacktestSteps` and `BacktestProgress` DDL and the `BacktestResults` generated columns and expression indexes; this plan does not create them.

## Global Constraints

- **The user's invariant, binding on every decision:** *"Keep all functionality the same completely as using rethinkdb but just different db."* A P&L difference in any backtest is a bug. Any change visible to Flutter is a bug.
- **Byte-identical assembly is the gate.** `canonical(assemble(id)) == canonical(legacy_doc)` at every lifecycle stage: stub, running, paused, stopped, errored, finished.
- **Python 3.11 compatible.** Prod image is `python:3.11-slim` (`backend/Dockerfile:2`).
- **`COLLATE "C"` everywhere**, no GIN, `STORED` written explicitly.
- **Tasks use `store` calls, never the `rethinkdb` driver.** The only exception is Task 1's one-shot fixture-fetch script, which is a developer tool and is not imported by anything.
- **`assert_secret_free()` stays exactly where it is** on the terminal path (`broker.py:12931`) and the stub path (`broker.py:12119/12126`).
- **Table names quoted** (`"BacktestResults"`, `"BacktestSteps"`, `"BacktestProgress"`).
- **Tests run as** `python3 -m pytest backend/tests` from the repo root; real-Postgres tests skip without `PG_TEST_DSN` (`./scripts/dev_pg.sh up` provides it).
- **Commit trailer on every commit in this plan:**
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
  ```
- **Before editing any existing symbol**, run `gitnexus_impact({target: "<symbol>", direction: "upstream"})` and report the blast radius; escalate HIGH/CRITICAL. Run `gitnexus_detect_changes()` before each commit. `broker.py` and `interactive_utils.py` are the two largest files in the repo — expect HIGH on most symbols here and report it rather than skipping the check.

---

## Interfaces this plan consumes from Plan A

Exact signatures. Every task's code uses these and nothing else from the data layer.

```python
from db import store, watch
from db.errors import StoreError

store.get(table: str, row_id: Any) -> dict | None
store.insert(table: str, doc_or_docs, *, conflict: str = "error",
             durability: str = "hard") -> InsertResult   # .inserted .replaced .errors ...
store.update(table: str, selector, patch: dict) -> WriteResult      # deep merge
store.replace(table: str, row_id: Any, doc: dict) -> WriteResult
store.replace_if(table, row_id, *, when, doc, insert_if_absent=False) -> dict | None
store.delete(table: str, selector) -> WriteResult
store.filter(table: str, predicate: dict | Predicate) -> Selection
store.between(table, lo, hi, *, index=None, left_bound="closed",
              right_bound="open") -> Selection
store.order_by(selection, *, index=None, fields=(), desc=False) -> Selection
store.limit(selection, n: int) -> Selection
store.slice(selection, start: int, end: int) -> Selection
store.run(selection) -> list[dict]
store.iter(selection, *, batch: int = 1000) -> Iterator[dict]
store.count(table_or_selection) -> int
store.pluck(rows_or_selection, *fields) -> list[dict]
store.sql(query: str, params: Sequence[Any] = ()) -> list[dict]
store.coerce_id(table: str, value: Any) -> str
store.asc(field: str, *, numeric: bool = False) -> Order
store.desc(field: str, *, numeric: bool = False) -> Order
store.P.field(key: str) -> FieldRef      # .eq .ne .lt .default .is_in .starts_with ...
store.Selection(table: str)              # empty selection over a whole table

watch.watch_table(table, on_change, *, label, fields=None, include_initial=True,
                  squash=False, poll_interval=2.0, log=None,
                  should_continue=None) -> Watcher
watch.watch_row(table, row_id, on_change, *, label, ...) -> Watcher

db.json.canonical(value) -> str
db.json.canonical_sha256(value) -> str
db.schema.ensure_schema(*, tables=None) -> list[str]
```

Plan A's `db/schema.py` already declares:

- `"BacktestResults"` — `id_type="int"`, generated columns `status, instance_id, instance, backtest_id, start_date, end_date, started_at, created_at, timestamp, completed_at` + `tickers_total` (`jsonb_array_length`), expression indexes `instance_or_instance_id`, `list_ts`, `instance_ts`.
- `"BacktestSteps"` — `PRIMARY KEY (backtest_id, kind, final, seq)`, `notify=False`.
- `"BacktestProgress"` — `id, status, progress, time_elapsed_seconds, last_active, updated_at` + the `status_norm` `CASE` index.

---

## File Structure

**Created by this plan:**

| file | responsibility |
|---|---|
| `backend/backtest_result_store.py` | `split_doc()`, `assemble()`, `assemble_field()`, `_STEP_KEYS`, `_ALWAYS_PRESENT`, the watermark helpers, and the three writer entry points |
| `scripts/dev_fetch_backtest_fixture.py` | one-shot, read-only pull of a real `BacktestResults` document from live RethinkDB, secrets stripped |
| `backend/tests/fixtures/backtest_result_*.json.gz` | the lifecycle fixtures the round-trip gate runs against |
| `backend/tests/db/test_backtest_split.py` | the round-trip gate |
| `backend/tests/test_backtest_split_writers.py` | the four writers against a real `BacktestProgress`/`BacktestSteps` |
| `backend/tests/test_backtest_list_endpoint.py` | the list fast path, byte-compared to the legacy shape |

**Modified by this plan:**

| file | sites |
|---|---|
| `backend/engines/backtest_engine.py` | `_ensure_backtest_result_row` (~927-966), the difficulty write (~1129), `_remove_row_and_mark_done` (~838-852) |
| `backend/broker.py` | stub (~12120, 12133), heartbeat (~12173-12193), progress (~17802-17866), terminal (~12933/12936), stop/pause (~5841, 5890, 5910), nexus lookback (~6929, 6948) |
| `backend/backtest_critical_abort.py` | `:170`, `:303` |
| `backend/interactive_utils.py` | list fast path (5215-5300), detail/playback gets (1440, 5827, 6039, 6085, 6316, 6841, 6875), delete (5829), stop (5852), best-per-strategy (6811-6814) |
| `backend/engines/self_learning_engine.py` | `:531` the plucked changefeed |

---

## Task 1: Lifecycle fixtures from a real document

**Files:**
- Create: `scripts/dev_fetch_backtest_fixture.py`
- Create: `backend/tests/fixtures/backtest_result_running.json.gz`
- Create: `backend/tests/fixtures/backtest_result_finished.json.gz`
- Create: `backend/tests/fixtures/backtest_result_stopped.json.gz`
- Create: `backend/tests/fixtures/backtest_result_error.json.gz`
- Create: `backend/tests/fixtures/backtest_result_stub.json.gz`
- Create: `backend/tests/fixtures/backtest_result_paused.json.gz`
- Create: `backend/tests/db/test_fixtures_shape.py`

**Interfaces:**
- Consumes: nothing from Plan A (this is a developer tool plus data).
- Produces: `backend/tests/fixtures/backtest_result_<stage>.json.gz`, each a gzipped JSON object with the exact top-level key set a real document of that stage carries, and `load_fixture(stage) -> dict` in the test module.

**Why real shapes matter:** the round-trip gate is only as good as the document it round-trips. A synthetic document invented from the spec would omit the keys that actually vary by lifecycle stage. These are the live top-level key sets, read read-only from the production RethinkDB on 2026-08-22:

| stage | live id | distinguishing keys |
|---|---|---|
| running | 138148 | `backtest_decisions` (5,165), `backtest_prices` **[]**, `experiment_*` all null, no `pnl_per_stock` |
| finished | 102463 | adds `cadence_mode`, `code_version`, `dividend_summary`, `execution_cost_model`(+`_version`), `execution_promotion_eligible`/`_error`, `execution_provenance_complete`, `fees`, `fill_provenance`, `pnl_per_stock`, `pnl_percent_per_stock`, `rejected_order_count`, `slippage_cost`, `spread_cost`, `stock_price_change`, `total_fees`, `unfilled_order_count`; `backtest_prices` has 2,597 entries |
| stopped | 108477 | every array **empty except `logs`**, `pnl`/`pnl_percent` null, adds `nexus_lookback` |
| error | 101666 | adds `error` (a string), `progress` is a float |

Note the live statuses are `running` / `finished` / `stopped` / `error` — there is no `completed`. `paused` and the stub have no live sample (a stub lives for seconds and a paused row is rare), so those two are derived mechanically from the others: the stub is `_ensure_backtest_result_row`'s literal payload, and `paused` is the running fixture with `status="paused_llm_critical"` plus the `pause_*` fields `backtest_critical_abort.py:160-174` writes.

**Fixture hygiene:** the fetch script strips secrets before writing (`assert_secret_free` first, then a hard key-denylist), truncates `backtest_decisions` to 200 entries and `backtest_prices` to 200 so the committed files stay small, and rewrites `instance_id` to a scrub value. A 3.1 MB real document is never committed. If the live DB is unreachable, the script emits a synthetic document carrying the same top-level keys and types, and says so in its output.

- [ ] **Step 1: Write the failing test**

`backend/tests/db/test_fixtures_shape.py`:

```python
"""The lifecycle fixtures must carry the key sets real documents carry.

Key sets recorded 2026-08-22 from a read-only query against the production
RethinkDB (ids 138148 running, 102463 finished, 108477 stopped, 101666 error).
"""
import gzip
import json
import os

import pytest

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "fixtures")

STAGES = ("stub", "running", "paused", "stopped", "error", "finished")

_BASE = {
    "_last_active", "backtest_decisions", "backtest_id", "backtest_prices",
    "backtest_refusals", "backtest_trades", "difficulty", "end_date",
    "granularity_sec", "id", "initial_cash", "instance_id", "logs", "pnl",
    "pnl_percent", "portfolio_value_history", "progress", "start_date",
    "status", "strategy_id", "strategy_schema", "tickers",
    "time_elapsed_seconds", "timestamp",
}
_FINISHED_EXTRA = {
    "cadence_mode", "code_version", "dividend_summary",
    "dual_cadence_backtest_simulation", "execution_cost_model",
    "execution_cost_model_version", "execution_promotion_eligible",
    "execution_promotion_error", "execution_provenance_complete", "fees",
    "fill_provenance", "pnl_per_stock", "pnl_percent_per_stock",
    "rejected_order_count", "slippage_cost", "spread_cost",
    "stock_price_change", "total_fees", "unfilled_order_count",
}
_STEP_ARRAYS = ("backtest_decisions", "backtest_refusals", "backtest_trades",
                "portfolio_value_history", "logs", "backtest_prices")


def load_fixture(stage: str) -> dict:
    path = os.path.join(FIXTURE_DIR, "backtest_result_%s.json.gz" % stage)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.mark.parametrize("stage", STAGES)
def test_fixture_exists_and_is_an_object(stage):
    doc = load_fixture(stage)
    assert isinstance(doc, dict) and doc


@pytest.mark.parametrize("stage", STAGES)
def test_fixture_carries_the_base_key_set(stage):
    doc = load_fixture(stage)
    if stage == "stub":
        # The stub is _ensure_backtest_result_row's literal payload: it has no
        # backtest_decisions / backtest_refusals yet, and four empty arrays.
        assert set(doc) == _BASE - {"backtest_decisions", "backtest_refusals",
                                    "_last_active", "granularity_sec",
                                    "difficulty", "initial_cash"}
        return
    assert _BASE <= set(doc), "missing: %r" % (_BASE - set(doc))


def test_finished_fixture_carries_the_terminal_only_keys():
    assert _FINISHED_EXTRA <= set(load_fixture("finished"))


def test_error_fixture_carries_an_error_string():
    assert isinstance(load_fixture("error")["error"], str)


def test_stopped_fixture_has_every_array_empty_except_logs():
    doc = load_fixture("stopped")
    for key in _STEP_ARRAYS:
        if key == "logs":
            assert doc[key], "stopped runs still carry their log tail"
        else:
            assert doc[key] == []


def test_paused_fixture_carries_the_pause_metadata():
    doc = load_fixture("paused")
    assert doc["status"] == "paused_llm_critical"
    for key in ("pause_reason", "pause_call_site", "pause_attempts",
                "pause_bar_time", "paused_at"):
        assert key in doc


@pytest.mark.parametrize("stage", STAGES)
def test_fixtures_are_secret_free(stage):
    blob = json.dumps(load_fixture(stage)).lower()
    for marker in ("api_key", "apikey", "secret", "password", "token",
                   "pk_live", "sk_live", "bearer "):
        assert marker not in blob, "%s fixture leaks %r" % (stage, marker)


@pytest.mark.parametrize("stage", STAGES)
def test_fixtures_are_small_enough_to_commit(stage):
    path = os.path.join(FIXTURE_DIR, "backtest_result_%s.json.gz" % stage)
    assert os.path.getsize(path) < 512 * 1024
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/db/test_fixtures_shape.py -v`
Expected: FAIL — `FileNotFoundError: .../backend/tests/fixtures/backtest_result_stub.json.gz`

- [ ] **Step 3: Write minimal implementation**

`scripts/dev_fetch_backtest_fixture.py`:

```python
#!/usr/bin/env python3
"""Pull ONE BacktestResults document per lifecycle stage, read-only, and write
gzipped test fixtures with secrets stripped.

Developer tool. Nothing imports it, and it is the only file in this plan
allowed to touch the rethinkdb driver -- lazily, inside the fetch function.
Run it once; the fixtures it writes are committed.

    python3 scripts/dev_fetch_backtest_fixture.py
    python3 scripts/dev_fetch_backtest_fixture.py --synthetic   # no live DB

Live host comes from .env RETHINKDB_HOST. The connection is read-only: no
insert, update, delete, or index_create is issued.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_DIR = os.path.join(REPO, "backend", "tests", "fixtures")
DB_NAME = "IntelliStock"

# Stage -> the status value to sample. There is no "completed" status live.
STAGES = {"running": "running", "finished": "finished",
          "stopped": "stopped", "error": "error"}

# Hard denylist applied after assert_secret_free, at every depth.
_SECRET_KEYS = {"api_key", "apikey", "secret", "secret_key", "password",
                "token", "access_token", "refresh_token", "authorization",
                "private_key", "credentials"}

_TRUNCATE = {"backtest_decisions": 200, "backtest_prices": 200,
             "portfolio_value_history": 200, "logs": 100,
             "backtest_trades": 50, "backtest_refusals": 50}


def _scrub(value):
    if isinstance(value, dict):
        return {k: ("REDACTED" if k.lower() in _SECRET_KEYS else _scrub(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _shrink(doc: dict) -> dict:
    out = _scrub(copy.deepcopy(doc))
    for key, cap in _TRUNCATE.items():
        if isinstance(out.get(key), list):
            out[key] = out[key][:cap]
    out["instance_id"] = "fixture-instance"
    return out


def fetch_live() -> dict:
    """Returns {stage: doc}. Raises if the live DB is unreachable."""
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO, ".env"))
    from rethinkdb import RethinkDB           # noqa: PLC0415 - lazy on purpose
    r = RethinkDB()
    conn = r.connect(host=os.environ["RETHINKDB_HOST"],
                     port=int(os.environ.get("RETHINKDB_PORT", "28015")),
                     timeout=15)
    try:
        out = {}
        for stage, status in STAGES.items():
            rows = list(r.db(DB_NAME).table("BacktestResults")
                        .filter({"status": status}).limit(1).run(conn))
            if not rows:
                raise RuntimeError("no live document with status=%r" % status)
            out[stage] = _shrink(rows[0])
        return out
    finally:
        conn.close()


def synthesize() -> dict:
    """Same top-level keys and types as the live documents, invented values."""
    base = {
        "_last_active": "2026-08-22T03:37:00.123456+00:00",
        "backtest_decisions": [{"date": "2026-03-02", "ticker": "AACI",
                                "action": "BUY", "reason": "signal"}
                               for _ in range(40)],
        "backtest_id": 460555,
        "backtest_prices": [],
        "backtest_refusals": [{"date": "2026-03-02", "ticker": "AACI",
                               "reason": "gate"}],
        "backtest_trades": [{"symbol": "AACI", "qty": 3, "price": 12.5,
                             "side": "buy", "ts": "2026-03-02T14:30:00"}],
        "difficulty": 3,
        "end_date": "2026-04-01 00:00:00",
        "granularity_sec": 900,
        "id": 460555,
        "initial_cash": 10000,
        "instance_id": "fixture-instance",
        "logs": ["[14:30:00] tick 1"] * 60,
        "pnl": 123.45,
        "pnl_percent": 1.2345,
        "portfolio_value_history": [{"t": "2026-03-02T14:30:00", "v": 10000.0}
                                    for _ in range(30)],
        "progress": 42.5,
        "start_date": "2026-03-01 00:00:00",
        "status": "running",
        "strategy_id": 179,
        "strategy_schema": {"name": "graph_nexus", "version": 9,
                            "config": {"k%d" % i: i for i in range(200)}},
        "tickers": ["AACI", "AA", "ZZ", "AAPL", "MSFT", "NVDA"],
        "time_elapsed_seconds": 812,
        "timestamp": "2026-08-22T03:37:00.123456",
        "experiment_fingerprint": None,
        "experiment_id": None,
        "experiment_search_scope": None,
    }
    finished = dict(base)
    finished.update({
        "status": "finished", "progress": 100, "backtest_prices":
            [{"date": "2026-03-02", "ticker": "AACI", "close": 12.5}
             for _ in range(50)],
        "cadence_mode": "daily_bars_intraday_marks",
        "code_version": "f1d9fde-2026-08-21T00:00Z",
        "dividend_summary": {"total": 0.0, "events": 0, "accrued": 0.0,
                             "paid": 0.0, "per_symbol": {}, "currency": "USD"},
        "dual_cadence_backtest_simulation": True,
        "execution_cost_model": {"spread_bps": 5.0, "slippage_bps": 2.0,
                                 "fee_bps": 0.0, "impact_bps": 0.0,
                                 "version": "2026-07-01"},
        "execution_cost_model_version": "2026-07-01T00:00:00Z",
        "execution_promotion_eligible": True,
        "execution_promotion_error": None,
        "execution_provenance_complete": True,
        "fees": None,
        "fill_provenance": [{"symbol": "AACI", "source": "bar_close"}],
        "pnl_per_stock": {"AACI": 12.5},
        "pnl_percent_per_stock": {"AACI": 1.1},
        "rejected_order_count": 0,
        "slippage_cost": 1.25,
        "spread_cost": 2.5,
        "stock_price_change": {"AACI": 0.03},
        "total_fees": 0.0,
        "unfilled_order_count": 0,
    })
    stopped = dict(base)
    stopped.update({"status": "stopped", "progress": 0, "pnl": None,
                    "pnl_percent": None, "backtest_decisions": [],
                    "backtest_refusals": [], "backtest_trades": [],
                    "backtest_prices": [], "portfolio_value_history": [],
                    "tickers": [],
                    "nexus_lookback": {"current": 5, "total": 10,
                                       "current_date": "2026-03-02",
                                       "start_date": "2026-03-01",
                                       "end_date": "2026-04-01"}})
    errored = dict(base)
    errored.update({"status": "error", "progress": 100.0,
                    "error": "strategy raised: no strategy linked to instance"})
    return {"running": base, "finished": finished,
            "stopped": stopped, "error": errored}


def derive_stub(running: dict) -> dict:
    """backend/engines/backtest_engine.py:938-955's literal payload."""
    return {
        "id": running["id"],
        "backtest_id": running["id"],
        "status": "running",
        "progress": 0,
        "timestamp": running["timestamp"],
        "instance_id": None,
        "strategy_id": None,
        "pnl": None,
        "pnl_percent": None,
        "start_date": running["start_date"],
        "end_date": running["end_date"],
        "tickers": list(running["tickers"]),
        "time_elapsed_seconds": None,
        "portfolio_value_history": [],
        "backtest_trades": [],
        "backtest_prices": [],
        "logs": [],
    }


def derive_paused(running: dict) -> dict:
    """backend/backtest_critical_abort.py:160-174's payload."""
    out = dict(running)
    out.update({
        "status": "paused_llm_critical",
        "pause_reason": "llm_critical_failure",
        "pause_call_site": "graph_nexus_analysis.overlay",
        "pause_attempts": 3,
        "pause_bar_time": "2026-03-02T14:30:00+00:00",
        "paused_at": "2026-08-22T03:40:00+00:00",
        "pause_sample": "provider returned 429 rate limit",
    })
    return out


def write(stage: str, doc: dict) -> None:
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    path = os.path.join(FIXTURE_DIR, "backtest_result_%s.json.gz" % stage)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(doc, fh, sort_keys=True, indent=1)
    print("wrote %s (%d bytes)" % (path, os.path.getsize(path)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--synthetic", action="store_true",
                    help="skip the live DB and emit synthetic documents")
    args = ap.parse_args(argv)
    if args.synthetic:
        docs = synthesize()
        print("SYNTHETIC fixtures (no live DB read)")
    else:
        try:
            docs = fetch_live()
            print("LIVE fixtures read read-only from RETHINKDB_HOST")
        except Exception as exc:
            print("live fetch failed (%s: %s); falling back to synthetic"
                  % (type(exc).__name__, exc), file=sys.stderr)
            docs = synthesize()
    docs["stub"] = derive_stub(docs["running"])
    docs["paused"] = derive_paused(docs["running"])
    for stage in ("stub", "running", "paused", "stopped", "error", "finished"):
        write(stage, docs[stage])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Then generate the fixtures and inspect what you got:

```bash
python3 scripts/dev_fetch_backtest_fixture.py
python3 -c "
import gzip, json
for s in ('stub','running','paused','stopped','error','finished'):
    d = json.load(gzip.open('backend/tests/fixtures/backtest_result_%s.json.gz' % s, 'rt'))
    print(s, len(d), sorted(d)[:6])
"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/db/test_fixtures_shape.py -v`
Expected: PASS, 22 tests (6 stages × 3 parametrised checks + 4 stage-specific)

- [ ] **Step 5: Commit**

```bash
git add scripts/dev_fetch_backtest_fixture.py backend/tests/fixtures/ \
        backend/tests/db/test_fixtures_shape.py
git commit -m "$(cat <<'EOF'
test(backtest): lifecycle fixtures from real BacktestResults documents

Six stages -- stub, running, paused, stopped, error, finished -- with the key
sets live documents actually carry (ids 138148/102463/108477/101666, read
read-only 2026-08-22). The finished stage alone adds 19 terminal-only keys;
a fixture invented from the spec would have missed every one. Secrets are
stripped and the step arrays truncated, so no multi-MB document is committed.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 2: `backtest_result_store.py` — split and assemble (GATE)

**Files:**
- Create: `backend/backtest_result_store.py`
- Modify: `backend/db/schema.py` — the `TableSpec("BacktestProgress")` DDL (see "Two amendments" below)
- Create: `backend/tests/db/test_backtest_split.py`

**Interfaces:**
- Consumes: `store.get`, `store.insert`, `store.sql`, `store.delete`, `store.filter`, `store.coerce_id`, `db.json.canonical`, and `broker_snapshot_helpers.downsample_history(history, n)`.
- Produces:
  ```python
  STEP_KINDS = ("decision", "refusal", "trade", "pv", "log", "price")
  _STEP_KEYS = (("decision", "backtest_decisions", None),
                ("refusal",  "backtest_refusals",  None),
                ("trade",    "backtest_trades",    ("tail", 1000)),
                ("pv",       "portfolio_value_history", ("downsample", 3000)),
                ("log",      "logs",               ("tail", 500)),
                ("price",    "backtest_prices",    None))
  _ALWAYS_PRESENT = {"portfolio_value_history", "backtest_trades",
                     "backtest_prices", "logs"}
  KEY_FOR_KIND: dict[str, str]           # kind -> legacy doc key
  KIND_FOR_KEY: dict[str, str]           # legacy doc key -> kind

  def split_doc(doc: dict) -> tuple[dict, dict[str, list], dict]
      # -> (metadata_doc, {kind: [entry, ...]}, progress_payload)
  def write_split(doc: dict, *, final: bool) -> None
  def append_steps(backtest_id, kind: str, entries: Sequence, *,
                   start_seq: int, final: bool = False) -> int
  def finalize_steps(backtest_id, kind: str, entries: Sequence) -> None
  def watermarks(backtest_id) -> dict[str, int]      # kind -> max(seq) over live rows
  def write_progress(backtest_id, payload: dict, *, last_active=None) -> None
  def read_progress(backtest_id) -> dict | None      # the payload, or None
  def assemble(backtest_id) -> dict | None
  def assemble_field(backtest_id, key: str)          # one step array, capped
  def delete_backtest(backtest_id) -> bool
  ```

**Two amendments to Plan A's schema, both made in this task:**

1. **`BacktestProgress` gets a `payload jsonb` column, and `status`/`progress`/`time_elapsed_seconds` become generated columns over it.** Without this the overlay cannot be byte-identical: live documents carry `progress` as an int on a stopped run (`108477`) and a float on a running one (`138148`), and `time_elapsed_seconds` as an int on a running run and a **float** on a finished one (`102463`). A `double precision` column would turn `0` into `0.0` and change the bytes. `payload` stores the values exactly as written; the typed columns exist for the `status_norm` index and the list endpoint. `last_active` stays a plain writer-set column — a text-to-`timestamptz` cast is only STABLE, so it cannot be generated.
2. **`final=true` rows reserve `seq = 0` as a finality marker** whose `doc` is JSON `null`; real entries are `seq = index + 1`. Without it, "the terminal writer finalized this kind with zero entries" is indistinguishable from "the terminal writer never ran" — which is exactly the stopped-run case, where five of the six arrays are legitimately empty.

**The cap rule that makes both halves work:** writers append **uncapped** entries; `assemble` applies the legacy caps at read time. The legacy progress writer stored `trades[-1000:]`, `downsample_history(history, 3000)`, and `logs[-500:]`, all derived from the same uncapped in-memory sources — so tailing and downsampling at read time reproduces the stored bytes exactly, and does it without the writer having to rewrite the whole array every 2%.

- [ ] **Step 1: Write the failing test**

`backend/tests/db/test_backtest_split.py`:

```python
"""The round-trip gate.

A document -> split into metadata + steps + progress -> assemble() ->
canonical() byte-identical to the input, at every lifecycle stage.

BACKTEST_SPLIT_FIXTURE=<path> runs the same assertions against an
operator-supplied real export instead of the checked-in fixtures.
"""
import gzip
import json
import os

import pytest

import backtest_result_store as brs
from db import schema
from db.json import canonical

from .conftest import requires_pg
from .test_fixtures_shape import STAGES, load_fixture


def _fixtures():
    override = os.environ.get("BACKTEST_SPLIT_FIXTURE")
    if override:
        with gzip.open(override, "rt", encoding="utf-8") as fh:
            return {"operator": json.load(fh)}
    return {stage: load_fixture(stage) for stage in STAGES}


@pytest.fixture
def split_schema(pg_schema):
    schema.ensure_schema(tables=["BacktestResults", "BacktestSteps",
                                 "BacktestProgress"])
    return pg_schema


# ---- pure split/assemble mechanics --------------------------------------

def test_step_keys_cover_the_six_arrays():
    keys = {key for _, key, _ in brs._STEP_KEYS}
    assert keys == {"backtest_decisions", "backtest_refusals", "backtest_trades",
                    "portfolio_value_history", "logs", "backtest_prices"}


def test_always_present_is_the_four_arrays_the_stub_creates_empty():
    assert brs._ALWAYS_PRESENT == {"portfolio_value_history", "backtest_trades",
                                   "backtest_prices", "logs"}


def test_split_doc_removes_every_step_array_from_the_metadata():
    doc = load_fixture("running")
    meta, steps, progress = brs.split_doc(doc)
    for _, key, _ in brs._STEP_KEYS:
        assert key not in meta
    assert steps["decision"] == doc["backtest_decisions"]
    assert progress["status"] == doc["status"]
    assert progress["progress"] == doc["progress"]


def test_split_doc_keeps_every_other_key_verbatim():
    doc = load_fixture("finished")
    meta, _, _ = brs.split_doc(doc)
    step_keys = {key for _, key, _ in brs._STEP_KEYS}
    hot_keys = {"status", "progress", "time_elapsed_seconds", "_last_active"}
    for key, value in doc.items():
        if key in step_keys:
            continue
        assert meta[key] == value, key
    assert hot_keys <= set(doc)          # they stay in doc too, unchanged


# ---- the gate -----------------------------------------------------------

@requires_pg
@pytest.mark.parametrize("stage", sorted(_fixtures()))
def test_round_trip_is_byte_identical(split_schema, stage):
    doc = _fixtures()[stage]
    brs.write_split(doc, final=True)
    got = brs.assemble(doc["id"])
    assert canonical(got) == canonical(doc)


@requires_pg
@pytest.mark.parametrize("stage", sorted(_fixtures()))
def test_assembled_keys_are_lexicographic(split_schema, stage):
    doc = _fixtures()[stage]
    brs.write_split(doc, final=True)
    got = brs.assemble(doc["id"])
    assert list(got) == sorted(got)


@requires_pg
def test_progress_scalar_types_survive_the_overlay(split_schema):
    """Live data: stopped runs carry progress as an int, running as a float;
    finished runs carry time_elapsed_seconds as a float, running as an int.
    A double precision column would turn 0 into 0.0 and change the bytes."""
    stopped, finished = load_fixture("stopped"), load_fixture("finished")
    for doc in (stopped, finished):
        brs.write_split(doc, final=True)
        got = brs.assemble(doc["id"])
        assert type(got["progress"]) is type(doc["progress"])
        assert type(got["time_elapsed_seconds"]) is type(doc["time_elapsed_seconds"])


@requires_pg
def test_the_four_always_present_arrays_exist_from_the_first_read(split_schema):
    stub = load_fixture("stub")
    brs.write_split(stub, final=False)
    got = brs.assemble(stub["id"])
    for key in brs._ALWAYS_PRESENT:
        assert got[key] == []


@requires_pg
def test_decisions_and_refusals_are_absent_until_written(split_schema):
    stub = load_fixture("stub")
    brs.write_split(stub, final=False)
    got = brs.assemble(stub["id"])
    assert "backtest_decisions" not in got
    assert "backtest_refusals" not in got


@requires_pg
def test_a_finalized_empty_array_is_not_the_same_as_never_written(split_schema):
    stopped = load_fixture("stopped")
    brs.write_split(stopped, final=True)
    got = brs.assemble(stopped["id"])
    assert got["backtest_decisions"] == []      # finalized empty, so present


@requires_pg
def test_live_reads_apply_the_legacy_caps(split_schema):
    doc = dict(load_fixture("running"))
    doc["id"] = 999001
    doc["backtest_trades"] = [{"n": i} for i in range(1500)]
    doc["logs"] = ["line %d" % i for i in range(900)]
    doc["portfolio_value_history"] = [{"t": i, "v": float(i)} for i in range(5000)]
    brs.write_split(doc, final=False)
    got = brs.assemble(999001)
    assert got["backtest_trades"] == doc["backtest_trades"][-1000:]
    assert got["logs"] == doc["logs"][-500:]
    assert len(got["portfolio_value_history"]) <= 3000
    assert got["portfolio_value_history"][0] == doc["portfolio_value_history"][0]


@requires_pg
def test_final_reads_are_uncapped(split_schema):
    doc = dict(load_fixture("finished"))
    doc["id"] = 999002
    doc["backtest_trades"] = [{"n": i} for i in range(1500)]
    brs.write_split(doc, final=True)
    assert brs.assemble(999002)["backtest_trades"] == doc["backtest_trades"]


@requires_pg
def test_seq_is_the_only_ordering(split_schema):
    doc = dict(load_fixture("running"))
    doc["id"] = 999003
    doc["backtest_decisions"] = [{"n": i} for i in range(50)]
    brs.write_split(doc, final=False)
    assert brs.assemble(999003)["backtest_decisions"] == doc["backtest_decisions"]


@requires_pg
def test_the_progress_overlay_beats_a_stale_doc(split_schema):
    doc = dict(load_fixture("running"))
    doc["id"] = 999004
    brs.write_split(doc, final=False)
    brs.write_progress(999004, {"status": "stopped", "progress": 100,
                                "time_elapsed_seconds": 5,
                                "_last_active": "2026-08-22T04:00:00+00:00"})
    got = brs.assemble(999004)
    assert got["status"] == "stopped" and got["progress"] == 100
    assert got["time_elapsed_seconds"] == 5


@requires_pg
def test_watermarks_report_max_seq_per_kind(split_schema):
    brs.append_steps(999005, "decision", [{"n": 1}, {"n": 2}], start_seq=0)
    brs.append_steps(999005, "log", [{"m": "x"}], start_seq=0)
    assert brs.watermarks(999005) == {"decision": 2, "log": 1}


@requires_pg
def test_append_steps_from_a_watermark_never_duplicates(split_schema):
    entries = [{"n": i} for i in range(10)]
    brs.append_steps(999006, "decision", entries[:4], start_seq=0)
    marks = brs.watermarks(999006)
    brs.append_steps(999006, "decision", entries[marks["decision"]:],
                     start_seq=marks["decision"])
    rows = brs.assemble_field(999006, "backtest_decisions")
    assert rows == entries


@requires_pg
def test_assemble_of_a_missing_backtest_is_none(split_schema):
    assert brs.assemble(424242) is None


@requires_pg
def test_assemble_field_reads_one_array_without_the_whole_document(split_schema):
    doc = load_fixture("running")
    brs.write_split(doc, final=False)
    got = brs.assemble_field(doc["id"], "portfolio_value_history")
    assert got == doc["portfolio_value_history"]


@requires_pg
def test_delete_backtest_removes_all_three_tables(split_schema):
    doc = load_fixture("running")
    brs.write_split(doc, final=True)
    assert brs.delete_backtest(doc["id"]) is True
    assert brs.assemble(doc["id"]) is None
    assert brs.read_progress(doc["id"]) is None
    from db import store
    assert store.sql('SELECT count(*) AS n FROM "BacktestSteps" '
                     "WHERE backtest_id = %s",
                     (str(doc["id"]),))[0]["n"] == 0
    assert brs.delete_backtest(doc["id"]) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/db/test_backtest_split.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtest_result_store'`

- [ ] **Step 3: Write minimal implementation**

First, replace the `TableSpec("BacktestProgress")` entry in `backend/db/schema.py`:

```python
    TableSpec(
        "BacktestProgress",
        ddl='''CREATE TABLE IF NOT EXISTS "BacktestProgress" (
  id          text PRIMARY KEY COLLATE "C",
  payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
  status      text COLLATE "C"
              GENERATED ALWAYS AS (payload ->> 'status') STORED,
  progress    double precision
              GENERATED ALWAYS AS (CASE WHEN jsonb_typeof(payload -> 'progress')
                                          = 'number'
                                        THEN (payload ->> 'progress')::double precision
                                   END) STORED,
  time_elapsed_seconds double precision
              GENERATED ALWAYS AS (CASE WHEN jsonb_typeof(
                                            payload -> 'time_elapsed_seconds')
                                          = 'number'
                                        THEN (payload ->> 'time_elapsed_seconds')
                                             ::double precision
                                   END) STORED,
  last_active timestamptz,
  updated_at  timestamptz NOT NULL DEFAULT now()
)''',
        compound_indexes={
            "status_norm":
                "((CASE WHEN lower(status) LIKE 'paused%' THEN 'paused' "
                "      ELSE lower(status) END) COLLATE \"C\")",
        }),
```

Then `backend/backtest_result_store.py`:

```python
"""The BacktestResults split: metadata row + step rows + hot progress row.

Today three concurrent writers rewrite one multi-MB document: a heartbeat
every 15s re-sending the last 500 log lines (broker.py:12173-12193), a
progress writer every +2% rewriting backtest_decisions and backtest_refusals
IN THEIR ENTIRETY (broker.py:17802-17866), and a terminal write
(broker.py:12933/12936). The live sample is 3.1 MB while still running, 89% of
it backtest_decisions. On Postgres that is ~8 GB of WAL and ~4M dead TOAST
chunks per backtest.

So: BacktestResults.doc keeps every key that is not one of the six step
arrays, verbatim; the arrays become insert-only BacktestSteps rows; and
status/progress/time_elapsed_seconds/_last_active live in a hot
BacktestProgress row. assemble() puts the legacy document back together byte
for byte.

Two conventions the rest of the code depends on:

  * ``seq`` starts at 1. For ``final=true`` rows, ``seq = 0`` is a marker row
    with a JSON-null doc, so "finalized with zero entries" (a stopped run's
    five empty arrays) is distinguishable from "never finalized".
  * Writers append UNCAPPED entries; assemble applies the legacy caps at read
    time -- trades tail-1000, logs tail-500, portfolio history downsampled to
    3000 -- which reproduces exactly what the legacy writer stored.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Optional, Sequence

from db import store
from db.errors import StoreError

RESULTS_TABLE = "BacktestResults"
STEPS_TABLE = "BacktestSteps"
PROGRESS_TABLE = "BacktestProgress"

_STEP_KEYS = (           # order fixed; drives the SELECT, not the output order
    ("decision", "backtest_decisions", None),
    ("refusal",  "backtest_refusals",  None),
    ("trade",    "backtest_trades",    ("tail", 1000)),
    ("pv",       "portfolio_value_history", ("downsample", 3000)),
    ("log",      "logs",               ("tail", 500)),
    ("price",    "backtest_prices",    None),
)
STEP_KINDS = tuple(kind for kind, _, _ in _STEP_KEYS)
KEY_FOR_KIND = {kind: key for kind, key, _ in _STEP_KEYS}
KIND_FOR_KEY = {key: kind for kind, key, _ in _STEP_KEYS}
_CAP_FOR_KIND = {kind: cap for kind, _, cap in _STEP_KEYS}

# The four arrays the stub creates empty at backtest_engine.py:952-955, so
# they exist from the first read exactly as today. backtest_decisions and
# backtest_refusals appear only once written, matching today.
_ALWAYS_PRESENT = {"portfolio_value_history", "backtest_trades",
                   "backtest_prices", "logs"}

# The keys the hot row owns. They stay in doc too (unchanged); the overlay
# just wins on read, so a stale doc status can never beat the hot row.
_PROGRESS_KEYS = ("status", "progress", "time_elapsed_seconds", "_last_active")


def _bid(backtest_id) -> str:
    return store.coerce_id(RESULTS_TABLE, backtest_id)


# ---- split ---------------------------------------------------------------

def split_doc(doc: dict):
    """(metadata_doc, {kind: [entry, ...]}, progress_payload)."""
    if not isinstance(doc, dict) or doc.get("id") is None:
        raise StoreError("BacktestResults document needs an id")
    meta = {k: v for k, v in doc.items() if k not in KIND_FOR_KEY}
    steps = {}
    for kind, key, _cap in _STEP_KEYS:
        if key in doc:
            steps[kind] = list(doc[key] or [])
    progress = {k: doc[k] for k in _PROGRESS_KEYS if k in doc}
    return meta, steps, progress


def write_split(doc: dict, *, final: bool) -> None:
    """Write a whole legacy document into the three tables.

    Used by the terminal writer, by the migration script, and by tests. The
    incremental writers use append_steps/write_progress instead.
    """
    meta, steps, progress = split_doc(doc)
    store.insert(RESULTS_TABLE, meta, conflict="replace")
    for kind in STEP_KINDS:
        entries = steps.get(kind)
        if entries is None:
            continue
        if final:
            finalize_steps(doc["id"], kind, entries)
        else:
            append_steps(doc["id"], kind, entries, start_seq=0)
    if progress:
        write_progress(doc["id"], progress)


# ---- steps ---------------------------------------------------------------

def append_steps(backtest_id, kind: str, entries: Sequence, *,
                 start_seq: int, final: bool = False) -> int:
    """Append entries at seq = start_seq+1 ... and return the new watermark."""
    if kind not in KEY_FOR_KIND:
        raise StoreError("unknown step kind %r" % kind)
    rows = list(entries)
    if not rows:
        return start_seq
    bid = _bid(backtest_id)
    values = []
    for offset, entry in enumerate(rows, start=1):
        values.append((bid, kind, start_seq + offset, final, entry))
    store.sql(
        'INSERT INTO "BacktestSteps" (backtest_id, kind, seq, final, doc) '
        "SELECT b, k, s, f, d FROM unnest("
        "  %s::text[], %s::text[], %s::bigint[], %s::boolean[], %s::jsonb[]"
        ") AS t(b, k, s, f, d) "
        "ON CONFLICT (backtest_id, kind, final, seq) DO NOTHING",
        (
            [v[0] for v in values],
            [v[1] for v in values],
            [v[2] for v in values],
            [v[3] for v in values],
            [_dumps(v[4]) for v in values],
        ))
    return start_seq + len(rows)


def finalize_steps(backtest_id, kind: str, entries: Sequence) -> None:
    """Write the authoritative array for one kind, plus the seq=0 marker."""
    bid = _bid(backtest_id)
    store.sql('DELETE FROM "BacktestSteps" WHERE backtest_id = %s '
              "AND kind = %s AND final", (bid, kind))
    store.sql('INSERT INTO "BacktestSteps" (backtest_id, kind, seq, final, doc) '
              "VALUES (%s, %s, 0, true, 'null'::jsonb)", (bid, kind))
    append_steps(backtest_id, kind, entries, start_seq=0, final=True)


def watermarks(backtest_id) -> dict:
    """kind -> max(seq) over the LIVE rows. A reconnecting writer re-reads
    this so it never duplicates or skips."""
    rows = store.sql('SELECT kind, max(seq) AS m FROM "BacktestSteps" '
                     "WHERE backtest_id = %s AND NOT final GROUP BY kind",
                     (_bid(backtest_id),))
    return {r["kind"]: int(r["m"]) for r in rows}


def _fetch_steps(backtest_id) -> dict:
    """{kind: (final_entries, live_entries)}, each already ordered by seq."""
    out = {kind: ([], [], False) for kind in STEP_KINDS}
    rows = store.sql('SELECT kind, seq, final, doc FROM "BacktestSteps" '
                     "WHERE backtest_id = %s ORDER BY kind, final, seq",
                     (_bid(backtest_id),))
    for row in rows:
        kind = row["kind"]
        if kind not in out:
            continue
        final_entries, live_entries, has_final = out[kind]
        if row["final"]:
            has_final = True
            if row["seq"] > 0:
                final_entries.append(row["doc"])
        else:
            live_entries.append(row["doc"])
        out[kind] = (final_entries, live_entries, has_final)
    return out


def _apply_cap(values: list, cap) -> list:
    if cap is None:
        return values
    mode, n = cap
    if mode == "tail":
        return values[-n:]
    if mode == "downsample":
        # Keep the true start and the shape, do not tail-slice: a long,
        # high-cadence RUNNING backtest must show the real start value.
        from broker_snapshot_helpers import downsample_history
        return list(downsample_history(values, n))
    raise StoreError("unknown cap %r" % (cap,))


# ---- progress ------------------------------------------------------------

def write_progress(backtest_id, payload: dict, *, last_active=None) -> None:
    """Upsert the hot row. ``payload`` carries the legacy scalar VALUES
    verbatim, so their JSON types survive (a stopped run's progress is an int,
    a running run's is a float)."""
    bid = _bid(backtest_id)
    when = last_active
    if when is None and payload.get("_last_active"):
        try:
            when = _dt.datetime.fromisoformat(str(payload["_last_active"]))
        except ValueError:
            when = None
    store.sql(
        'INSERT INTO "BacktestProgress" (id, payload, last_active) '
        "VALUES (%s, %s::jsonb, %s) "
        "ON CONFLICT (id) DO UPDATE SET "
        '  payload = jsonb_deep_merge("BacktestProgress".payload, EXCLUDED.payload), '
        "  last_active = coalesce(EXCLUDED.last_active, "
        '                         "BacktestProgress".last_active), '
        "  updated_at = now()",
        (bid, _dumps(payload), when))


def read_progress(backtest_id) -> Optional[dict]:
    rows = store.sql('SELECT payload FROM "BacktestProgress" WHERE id = %s',
                     (_bid(backtest_id),))
    return rows[0]["payload"] if rows else None


# ---- assemble ------------------------------------------------------------

def assemble(backtest_id) -> Optional[dict]:
    """Reconstruct the legacy JSON document, byte for byte."""
    row = store.get(RESULTS_TABLE, backtest_id)
    if row is None:
        return None
    doc = dict(row)                                  # verbatim metadata
    steps = _fetch_steps(backtest_id)
    for kind, key, cap in _STEP_KEYS:
        final_entries, live_entries, has_final = steps.get(kind, ([], [], False))
        if has_final:                                # terminal write happened
            values = list(final_entries)             # ORDER BY seq, no cap
        elif live_entries:
            values = _apply_cap(list(live_entries), cap)
        elif key in _ALWAYS_PRESENT:
            values = []
        else:
            continue                                 # absent, matching today
        doc[key] = values
    progress = read_progress(backtest_id)
    if progress:                                     # hot row wins over doc
        for key in _PROGRESS_KEYS:
            if key in progress:
                doc[key] = progress[key]
    # Lexicographic key order, matching RethinkDB's keys(), so a naive
    # json.dumps(doc) produces the same bytes as before. Every fingerprint
    # independently goes through db.json.canonical, so this is belt and
    # braces, not the guarantee.
    return dict(sorted(doc.items()))


def assemble_field(backtest_id, key: str):
    """One step array, capped the same way assemble() would cap it, without
    paying for the whole document. interactive_utils.py:6841 reads only
    portfolio_value_history."""
    kind = KIND_FOR_KEY.get(key)
    if kind is None:
        doc = assemble(backtest_id)
        return None if doc is None else doc.get(key)
    final_entries, live_entries, has_final = _fetch_steps(backtest_id).get(
        kind, ([], [], False))
    if has_final:
        return list(final_entries)
    if live_entries:
        return _apply_cap(list(live_entries), _CAP_FOR_KIND[kind])
    return [] if key in _ALWAYS_PRESENT else None


def delete_backtest(backtest_id) -> bool:
    """Remove the row from all three tables. False when nothing was there."""
    bid = _bid(backtest_id)
    existed = store.get(RESULTS_TABLE, backtest_id) is not None
    store.sql('DELETE FROM "BacktestSteps" WHERE backtest_id = %s', (bid,))
    store.sql('DELETE FROM "BacktestProgress" WHERE id = %s', (bid,))
    store.delete(RESULTS_TABLE, backtest_id)
    return existed


def _dumps(value: Any) -> str:
    from db import json as dbjson
    return dbjson.dumps(value)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest backend/tests/db/test_backtest_split.py -v
python3 -m pytest backend/tests/db/test_schema_ensure.py -v     # the DDL amendment
```
Expected: PASS. The round-trip gate runs at all six stages; if `canonical(assemble(id)) != canonical(doc)` for any of them, **stop** and fix the split — every later task in this plan assumes this gate holds.

- [ ] **Step 5: Commit**

```bash
git add backend/backtest_result_store.py backend/db/schema.py \
        backend/tests/db/test_backtest_split.py
git commit -m "$(cat <<'EOF'
feat(backtest): split BacktestResults into metadata + steps + hot progress

assemble() reproduces the legacy document byte for byte at all six lifecycle
stages, including the four always-present empty arrays, the absent
decisions/refusals on a fresh stub, the legacy caps (trades tail-1000, logs
tail-500, portfolio history downsampled to 3000), and lexicographic key order.

Two schema amendments this needed: BacktestProgress.payload jsonb, because a
double precision column would turn a stopped run's integer progress 0 into
0.0 and change the bytes; and seq=0 as a finality marker, because a stopped
run legitimately finalizes five empty arrays and that must not read as "never
finalized".

Writers append uncapped entries and assemble caps on read, which reproduces
exactly what the legacy writer stored without rewriting a 2.7 MB array every
2% of progress.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 3: The stub writer

**Files:**
- Modify: `backend/engines/backtest_engine.py` — `_ensure_backtest_result_row` (~927-966)
- Modify: `backend/broker.py` — the two `insert(stub, conflict='replace')` sites (~12120, ~12133)
- Modify: `backend/backtest_result_store.py` — add `write_stub()`
- Create: `backend/tests/test_backtest_split_writers.py`

**Interfaces:**
- Consumes: `store.insert(table, doc, conflict="replace")`, `brs.write_progress(backtest_id, payload)`, `brs.split_doc(doc)`.
- Produces: `backtest_result_store.write_stub(stub: dict) -> None` — inserts the metadata document **without** the six arrays and upserts `BacktestProgress` with the stub's `status` and `progress`.

**Blast-radius note:** `_ensure_backtest_result_row` is called twice in a row at `backtest_engine.py:1126-1127` (once with `'pending'`, once with `'running'`), so `write_stub` must be idempotent under `conflict='replace'` and must not reset step watermarks that a broker has already started writing. It resets nothing: it only touches `BacktestResults` and `BacktestProgress`.

Run `gitnexus_impact({target: "_ensure_backtest_result_row", direction: "upstream"})` and report before editing.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_backtest_split_writers.py`:

```python
"""The four BacktestResults writers, against a real split schema."""
import datetime as dt
import os

import pytest

import backtest_result_store as brs
from db import schema, store

pytestmark = pytest.mark.skipif(
    not os.environ.get("PG_TEST_DSN"),
    reason="PG_TEST_DSN not set (run: ./scripts/dev_pg.sh up)")


@pytest.fixture
def split_schema(pg_schema):
    schema.ensure_schema(tables=["BacktestResults", "BacktestSteps",
                                 "BacktestProgress", "BacktestInstances"])
    return pg_schema


STUB = {
    "id": 700001, "backtest_id": 700001, "status": "running", "progress": 0,
    "timestamp": "2026-08-22T03:00:00Z", "instance_id": None,
    "strategy_id": None, "pnl": None, "pnl_percent": None,
    "start_date": "2026-03-01 00:00:00", "end_date": "2026-04-01 00:00:00",
    "tickers": ["AACI", "AA"], "time_elapsed_seconds": None,
    "portfolio_value_history": [], "backtest_trades": [],
    "backtest_prices": [], "logs": [],
}


def test_write_stub_stores_no_arrays_in_the_metadata_row(split_schema):
    brs.write_stub(dict(STUB))
    row = store.get("BacktestResults", 700001)
    for key in ("portfolio_value_history", "backtest_trades",
                "backtest_prices", "logs"):
        assert key not in row


def test_write_stub_populates_the_hot_progress_row(split_schema):
    brs.write_stub(dict(STUB))
    assert brs.read_progress(700001) == {"status": "running", "progress": 0,
                                         "time_elapsed_seconds": None}


def test_the_assembled_stub_matches_the_legacy_document(split_schema):
    from db.json import canonical
    brs.write_stub(dict(STUB))
    assert canonical(brs.assemble(700001)) == canonical(STUB)


def test_write_stub_is_idempotent(split_schema):
    brs.write_stub(dict(STUB))
    brs.write_stub(dict(STUB))
    assert store.count("BacktestResults") == 1


def test_write_stub_twice_does_not_reset_steps_a_broker_already_wrote(split_schema):
    brs.write_stub(dict(STUB))
    brs.append_steps(700001, "decision", [{"n": 1}, {"n": 2}], start_seq=0)
    brs.write_stub(dict(STUB))          # backtest_engine.py:1126-1127
    assert brs.watermarks(700001) == {"decision": 2}
    assert brs.assemble(700001)["backtest_decisions"] == [{"n": 1}, {"n": 2}]


def test_write_stub_with_an_error_status_keeps_the_error_in_the_metadata(split_schema):
    stub = dict(STUB)
    stub.update({"status": "error", "progress": 100.0,
                 "error": "no strategy linked to instance"})
    brs.write_stub(stub)
    assert store.get("BacktestResults", 700001)["error"].startswith("no strategy")
    assert brs.read_progress(700001)["status"] == "error"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest backend/tests/test_backtest_split_writers.py -v
```
Expected: FAIL — `AttributeError: module 'backtest_result_store' has no attribute 'write_stub'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/backtest_result_store.py`:

```python
def write_stub(stub: dict) -> None:
    """The row-creation write: metadata WITHOUT the six arrays, plus the hot
    progress row. Resets nothing -- backtest_engine.py:1126-1127 calls it
    twice in a row (pending, then running), and a broker may already have
    appended steps by then.
    """
    meta, _steps, progress = split_doc(stub)
    store.insert(RESULTS_TABLE, meta, conflict="replace")
    write_progress(stub["id"], {k: stub.get(k) for k in
                                ("status", "progress", "time_elapsed_seconds")
                                if k in stub})
```

`backend/engines/backtest_engine.py` — replace the tail of `_ensure_backtest_result_row` (the `stub = {...}` literal is unchanged; only the write changes):

```python
    # ``stub`` keeps its four empty arrays: they are the legacy contract that
    # portfolio_value_history / backtest_trades / backtest_prices / logs exist
    # from the first read. write_stub strips them out of the metadata row and
    # assemble() puts them back (backtest_result_store._ALWAYS_PRESENT).
    import backtest_result_store as _brs
    _brs.write_stub(stub)
```

Delete the `conn` parameter's only remaining use in that function; keep the parameter in the signature for now so callers at `:1126-1127` do not change arity in the same commit (the `conn` plumbing is removed by group J).

`backend/broker.py` — both sites become:

```python
                stub['status'] = 'error'
                stub['progress'] = 100.0
                stub['error'] = err_msg[:2000]
                assert_secret_free(stub)
                import backtest_result_store as _brs
                _brs.write_stub(stub)
```

and

```python
            # Ensure row exists: write_stub upserts, so this works when the
            # broker runs standalone (no engine).
            assert_secret_free(stub)
            import backtest_result_store as _brs
            _brs.write_stub(stub)
            _log(f"Backtest result row (id={_backtest_result_id}) ensured in DB, status=running", "green")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest backend/tests/test_backtest_split_writers.py -v
python3 -m pytest backend/tests/db/test_backtest_split.py -q
```
Expected: PASS, 6 tests; the round-trip gate still green.

- [ ] **Step 5: Commit**

```bash
git add backend/backtest_result_store.py backend/engines/backtest_engine.py \
        backend/broker.py backend/tests/test_backtest_split_writers.py
git commit -m "$(cat <<'EOF'
feat(backtest): stub writer writes metadata + hot progress, not four arrays

The stub literal is unchanged -- its four empty arrays are the legacy contract
that they exist from the first read -- but they now live in
_ALWAYS_PRESENT rather than in the row. write_stub resets nothing, so the
double call at backtest_engine.py:1126-1127 cannot clobber steps a broker
has already appended.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 4: The heartbeat writer and the step watermarks

**Files:**
- Modify: `backend/broker.py` — `_backtest_heartbeat` (~12167-12200), `_steps_written` declaration (~11877, reset at ~11939/11954)
- Modify: `backend/intellistock_logger.py` — the monotonic emitted counter (`__init__` ~89, `set_context_log_buffer` ~103-107, `clear_context_log_buffer`, the fan-out loop ~210-215, new `context_log_lines_emitted`)
- Modify: `backend/backtest_result_store.py` — add `heartbeat()` and `resume_watermarks()`
- Modify: `backend/tests/test_backtest_split_writers.py`

**Interfaces:**
- Consumes: `brs.write_progress`, `brs.append_steps`, `brs.watermarks`.
- Produces:
  ```python
  def heartbeat(backtest_id, *, last_active: str, elapsed_seconds=None,
                new_log_lines: Sequence[str] = (), log_seq: int = 0) -> int
      # appends the new log lines past log_seq, updates the hot row, returns
      # the new log watermark
  def resume_watermarks(backtest_id) -> dict[str, int]
      # watermarks(), used by a reconnecting writer so it never duplicates
  ```

**What changes:** today the heartbeat re-sends the **entire last-500-line log list every 15 seconds** (`broker.py:12191`). After this task it writes two scalars into the hot row and appends only the log lines past its in-process watermark. The broker keeps `_steps_written: dict[str, int]`, reset to `{}` on the stub write and re-seeded from `resume_watermarks()` after any reconnect.

**The log buffer is bounded, and this is the trap in this task.** `_backtest_log_buffer` is a plain list that `intellistock_logger` trims FIFO — `intellistock_logger.py:210-215` runs `while len(buf_list) > max_lines: buf_list.pop(0)` with `max_lines=500` (set at `broker.py:11942`). So `len(buffer)` **saturates at 500** and an index-into-the-buffer watermark stops producing new lines forever once it reaches 500. The fix is a monotonic emitted-line counter in the logger, added in this task; do not try to derive "what is new" from the buffer's length.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_backtest_split_writers.py`:

```python
def test_heartbeat_updates_only_the_hot_row_scalars(split_schema):
    brs.write_stub(dict(STUB))
    before = store.get("BacktestResults", 700001)
    brs.heartbeat(700001, last_active="2026-08-22T03:15:00+00:00",
                  elapsed_seconds=900)
    assert store.get("BacktestResults", 700001) == before   # metadata untouched
    prog = brs.read_progress(700001)
    assert prog["time_elapsed_seconds"] == 900
    assert prog["_last_active"] == "2026-08-22T03:15:00+00:00"


def test_heartbeat_appends_only_new_log_lines(split_schema):
    brs.write_stub(dict(STUB))
    mark = brs.heartbeat(700001, last_active="t1", new_log_lines=["a", "b"],
                         log_seq=0)
    assert mark == 2
    mark = brs.heartbeat(700001, last_active="t2", new_log_lines=["c"],
                         log_seq=mark)
    assert mark == 3
    assert brs.assemble(700001)["logs"] == ["a", "b", "c"]


def test_heartbeat_never_rewrites_the_whole_log_list(split_schema):
    """The legacy heartbeat re-sent 500 lines every 15s. Appending 1 line
    must write exactly 1 step row."""
    brs.write_stub(dict(STUB))
    brs.heartbeat(700001, last_active="t1",
                  new_log_lines=["l%d" % i for i in range(500)], log_seq=0)
    n_before = store.sql('SELECT count(*) AS n FROM "BacktestSteps" '
                         "WHERE backtest_id='700001' AND kind='log'")[0]["n"]
    brs.heartbeat(700001, last_active="t2", new_log_lines=["l500"], log_seq=500)
    n_after = store.sql('SELECT count(*) AS n FROM "BacktestSteps" '
                        "WHERE backtest_id='700001' AND kind='log'")[0]["n"]
    assert n_after - n_before == 1


def test_resume_watermarks_lets_a_reconnecting_writer_continue(split_schema):
    brs.write_stub(dict(STUB))
    brs.heartbeat(700001, last_active="t1", new_log_lines=["a", "b", "c"],
                  log_seq=0)
    # Simulate the writer losing its in-process watermark on reconnect.
    marks = brs.resume_watermarks(700001)
    assert marks["log"] == 3
    brs.heartbeat(700001, last_active="t2", new_log_lines=["d"],
                  log_seq=marks["log"])
    assert brs.assemble(700001)["logs"] == ["a", "b", "c", "d"]


def test_the_read_still_tails_500_lines(split_schema):
    brs.write_stub(dict(STUB))
    lines = ["l%d" % i for i in range(900)]
    brs.heartbeat(700001, last_active="t1", new_log_lines=lines, log_seq=0)
    assert brs.assemble(700001)["logs"] == lines[-500:]


def test_the_logger_counts_lines_it_has_trimmed_away():
    """intellistock_logger.py:210-215 trims the buffer FIFO to max_lines, so
    len(buffer) saturates at 500 and cannot be a watermark. The emitted
    counter must keep climbing."""
    import intellistock_logger
    buf = []
    intellistock_logger.set_backtest_log_buffer(buf, max_lines=5)
    try:
        for i in range(12):
            intellistock_logger.log("line %d" % i, "white")
        assert len(buf) == 5
        assert intellistock_logger.context_log_lines_emitted("backtest") == 12
    finally:
        intellistock_logger.clear_backtest_log_buffer()
    assert intellistock_logger.context_log_lines_emitted("backtest") == 0
```

> The module exposes a singleton logger; if `intellistock_logger.log` /
> `set_backtest_log_buffer` are methods on an instance rather than
> module-level functions, call them on that instance — check the bottom of
> `backend/intellistock_logger.py` for how the rest of the repo imports it
> (`grep -n "^intellistock_logger\|^logger = \|^_instance" backend/intellistock_logger.py`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest backend/tests/test_backtest_split_writers.py -k heartbeat -v`
Expected: FAIL — `AttributeError: module 'backtest_result_store' has no attribute 'heartbeat'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/backtest_result_store.py`:

```python
def heartbeat(backtest_id, *, last_active: str, elapsed_seconds=None,
              new_log_lines: Sequence = (), log_seq: int = 0) -> int:
    """The 15-second liveness write.

    The legacy version re-sent the whole last-500-line log list every tick
    (broker.py:12191). This writes two scalars into the hot row and appends
    only the lines past ``log_seq``; assemble() still tails 500 on read, so
    the document the UI sees is unchanged.

    Returns the new log watermark.
    """
    payload = {"_last_active": last_active}
    if elapsed_seconds is not None:
        payload["time_elapsed_seconds"] = elapsed_seconds
    write_progress(backtest_id, payload)
    if new_log_lines:
        return append_steps(backtest_id, "log", new_log_lines, start_seq=log_seq)
    return log_seq


def resume_watermarks(backtest_id) -> dict:
    """Re-seed a writer's in-process watermarks after a reconnect, so it never
    duplicates or skips."""
    return watermarks(backtest_id)
```

`backend/intellistock_logger.py` — add the monotonic counter. In `__init__` beside `self._ctx_buffers` (`:89`):

```python
        # ctx -> total lines EVER emitted into that context's buffer. The
        # buffer itself is trimmed FIFO to max_lines, so its length saturates
        # and cannot be used as a watermark.
        self._ctx_emitted = {}
```

In `set_context_log_buffer` (`:103-107`) and `clear_context_log_buffer`, reset it:

```python
            self._ctx_emitted[ctx] = 0
```

In the fan-out loop (`:210-215`), count before trimming — the loop currently iterates `self._ctx_buffers.values()`, so switch it to `items()` to know which context it is on:

```python
        # Fan out to every attached context buffer, trimming FIFO to max_lines.
        for ctx_name, (buf_list, max_lines) in list(self._ctx_buffers.items()):
            try:
                buf_list.append(line)
                self._ctx_emitted[ctx_name] = self._ctx_emitted.get(ctx_name, 0) + 1
                while len(buf_list) > max_lines:
                    buf_list.pop(0)
```

And expose it next to the other context helpers:

```python
    def context_log_lines_emitted(self, ctx: str) -> int:
        """Total lines ever emitted into ``ctx``'s buffer.

        The buffer is trimmed FIFO to max_lines, so len(buffer) saturates.
        The BacktestSteps log watermark keys off this counter instead
        (backend/backtest_result_store.py).
        """
        return int(self._ctx_emitted.get(ctx, 0))
```

`backend/broker.py` — declare the watermark dict next to `_last_progress_updated` (`:11877`) and reset it where the buffer is created (`:11939`, `:11954`):

```python
_steps_written = {}      # kind -> highest seq written by this process
```

Then replace the body of `_backtest_heartbeat`'s try block (`:12178-12193`):

```python
            try:
                import backtest_result_store as _brs
                now_hb = _dt_hb.datetime.now(_dt_hb.timezone.utc).isoformat()
                elapsed_hb = max(0, int(time.time() - backtest_start_time)) if backtest_start_time else None
                # Append only the log lines past our watermark. The legacy
                # write re-sent the whole last-500 list every 15 seconds;
                # assemble() still tails 500 on read, so the UI is unchanged.
                #
                # The watermark keys off the logger's monotonic emitted count,
                # NOT len(buffer): the buffer is trimmed FIFO to 500 lines
                # (intellistock_logger.py:210-215) and its length saturates.
                new_lines, start_seq = [], _steps_written.get('log', 0)
                if _backtest_log_buffer is not None:
                    buffered = list(_backtest_log_buffer)
                    emitted = intellistock_logger.context_log_lines_emitted("backtest")
                    n_new = min(max(emitted - start_seq, 0), len(buffered))
                    if n_new:
                        new_lines = buffered[len(buffered) - n_new:]
                        # If more than 500 lines were emitted since the last
                        # tick the buffer already dropped the overflow; start
                        # the seq run where the surviving lines actually are,
                        # so seq stays aligned with the emitted index and
                        # nothing is duplicated.
                        start_seq = emitted - n_new
                _steps_written['log'] = _brs.heartbeat(
                    _backtest_result_id, last_active=now_hb,
                    elapsed_seconds=elapsed_hb, new_log_lines=new_lines,
                    log_seq=start_seq)
            except Exception:
                # Lost the connection -- re-seed the watermarks from the DB on
                # the next tick so we neither duplicate nor skip.
                try:
                    import backtest_result_store as _brs2
                    _steps_written.update(_brs2.resume_watermarks(_backtest_result_id))
                except Exception:
                    pass
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest backend/tests/test_backtest_split_writers.py -v
python3 -m pytest backend/tests/db/test_backtest_split.py -q
```
Expected: PASS, 11 tests

- [ ] **Step 5: Commit**

```bash
git add backend/backtest_result_store.py backend/broker.py \
        backend/tests/test_backtest_split_writers.py
git commit -m "$(cat <<'EOF'
feat(backtest): heartbeat writes two scalars, appends only new log lines

The legacy heartbeat re-sent the entire last-500-line log list every 15
seconds into a multi-MB document. It now updates the hot BacktestProgress row
and appends the lines past an in-process watermark, re-seeded from the DB
after any reconnect so it never duplicates or skips. assemble() still tails
500 on read, so the document the UI sees is unchanged.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 5: The progress writer

**Files:**
- Modify: `backend/broker.py` — the progress block (~17802-17866)
- Modify: `backend/backtest_result_store.py` — add `write_progress_tick()`
- Modify: `backend/tests/test_backtest_split_writers.py`

**Interfaces:**
- Consumes: `brs.write_progress`, `brs.append_steps`, `store.update`.
- Produces:
  ```python
  def write_progress_tick(backtest_id, *, hot: dict, metadata: dict,
                          appended: Mapping[str, Sequence],
                          seqs: MutableMapping[str, int]) -> dict[str, int]
      # hot      -> BacktestProgress payload (status, progress,
      #             time_elapsed_seconds, _last_active)
      # metadata -> deep-merged into BacktestResults.doc (backtest_id, pnl,
      #             pnl_percent, timestamp, tickers)
      # appended -> {kind: full_uncapped_source_list}; only entries past
      #             seqs[kind] are written
      # returns the updated watermarks (also mutates ``seqs`` in place)
  ```

**What changes:** today this block rewrites `backtest_trades` (last 1000), `portfolio_value_history` (downsampled to 3000), `logs` (last 500), and **`backtest_decisions` + `backtest_refusals` in their entirety** on every +2% of progress. A 50-tick run therefore rewrites the whole decisions array 50 times, each rewrite larger than the last — 2.7 MB on the live sample. After this task it writes four scalars to the hot row, a small deep-merge patch to `doc`, and the new tail of each array.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_backtest_split_writers.py`:

```python
def test_progress_tick_writes_scalars_to_the_hot_row(split_schema):
    brs.write_stub(dict(STUB))
    seqs = {}
    brs.write_progress_tick(
        700001,
        hot={"status": "running", "progress": 42.5,
             "time_elapsed_seconds": 812, "_last_active": "t"},
        metadata={"pnl": 123.45, "pnl_percent": 1.2345,
                  "timestamp": "2026-08-22T03:37:00", "tickers": ["AACI"]},
        appended={}, seqs=seqs)
    assert brs.read_progress(700001)["progress"] == 42.5
    row = store.get("BacktestResults", 700001)
    assert row["pnl"] == 123.45 and row["tickers"] == ["AACI"]


def test_progress_tick_appends_only_the_new_entries(split_schema):
    brs.write_stub(dict(STUB))
    seqs = {}
    decisions = [{"n": i} for i in range(10)]
    brs.write_progress_tick(700001, hot={"status": "running", "progress": 10},
                            metadata={}, appended={"decision": decisions[:4]},
                            seqs=seqs)
    assert seqs["decision"] == 4
    brs.write_progress_tick(700001, hot={"status": "running", "progress": 20},
                            metadata={}, appended={"decision": decisions},
                            seqs=seqs)
    assert seqs["decision"] == 10
    n = store.sql('SELECT count(*) AS n FROM "BacktestSteps" '
                  "WHERE backtest_id='700001' AND kind='decision'")[0]["n"]
    assert n == 10          # 10 rows written, not 14
    assert brs.assemble(700001)["backtest_decisions"] == decisions


def test_progress_tick_metadata_is_a_deep_merge_not_a_replace(split_schema):
    stub = dict(STUB)
    stub["strategy_schema"] = {"name": "gna", "config": {"a": 1, "b": 2}}
    brs.write_stub(stub)
    brs.write_progress_tick(700001, hot={}, metadata={"pnl": 1.0},
                            appended={}, seqs={})
    row = store.get("BacktestResults", 700001)
    assert row["strategy_schema"]["config"] == {"a": 1, "b": 2}
    assert row["pnl"] == 1.0


def test_the_assembled_document_matches_a_legacy_progress_write(split_schema):
    """What the legacy writer would have stored, from the same sources."""
    from broker_snapshot_helpers import downsample_history
    from db.json import canonical
    brs.write_stub(dict(STUB))
    trades = [{"n": i} for i in range(1500)]
    history = [{"t": i, "v": float(i)} for i in range(5000)]
    logs = ["l%d" % i for i in range(900)]
    decisions = [{"d": i} for i in range(300)]
    refusals = [{"r": i} for i in range(5)]
    seqs = {}
    brs.write_progress_tick(
        700001,
        hot={"status": "running", "progress": 50.0,
             "time_elapsed_seconds": 100, "_last_active": "t"},
        metadata={"backtest_id": 700001, "pnl": 1.0, "pnl_percent": 0.01,
                  "timestamp": "2026-08-22T03:37:00", "tickers": ["AACI"]},
        appended={"trade": trades, "pv": history, "log": logs,
                  "decision": decisions, "refusal": refusals},
        seqs=seqs)
    got = brs.assemble(700001)
    legacy = dict(STUB)
    legacy.update({
        "backtest_id": 700001, "progress": 50.0, "pnl": 1.0,
        "pnl_percent": 0.01, "status": "running",
        "timestamp": "2026-08-22T03:37:00", "_last_active": "t",
        "tickers": ["AACI"], "time_elapsed_seconds": 100,
        "backtest_trades": trades[-1000:],
        "portfolio_value_history": list(downsample_history(history, 3000)),
        "logs": logs[-500:],
        "backtest_decisions": decisions, "backtest_refusals": refusals,
    })
    assert canonical(got) == canonical(dict(sorted(legacy.items())))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest backend/tests/test_backtest_split_writers.py -k progress_tick -v`
Expected: FAIL — `AttributeError: module 'backtest_result_store' has no attribute 'write_progress_tick'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/backtest_result_store.py`:

```python
def write_progress_tick(backtest_id, *, hot: dict, metadata: dict,
                        appended, seqs) -> dict:
    """The +2%-of-progress write.

    The legacy version rewrote backtest_trades (last 1000), the downsampled
    portfolio history, the last 500 logs, and backtest_decisions +
    backtest_refusals IN THEIR ENTIRETY every tick -- 2.7 MB on the live
    sample, growing with every tick. This writes four scalars to the hot row,
    a small deep-merge patch to doc, and only the entries past each kind's
    watermark.

    ``appended`` maps a kind to its FULL uncapped source list; the caps are
    applied by assemble() on read.
    """
    if hot:
        write_progress(backtest_id, hot)
    if metadata:
        store.update(RESULTS_TABLE, backtest_id, metadata)
    for kind, values in (appended or {}).items():
        written = int(seqs.get(kind, 0))
        rows = list(values or [])
        if len(rows) > written:
            seqs[kind] = append_steps(backtest_id, kind, rows[written:],
                                      start_seq=written)
        else:
            seqs.setdefault(kind, written)
    return dict(seqs)
```

`backend/broker.py` — replace the `update_payload` construction and the single `.update(...)` call (`:17828-17866`) with:

```python
                        import backtest_result_store as _brs
                        backtest_id_raw = backtest_row_id
                        backtest_id_int = int(backtest_id_raw) if backtest_id_raw and str(backtest_id_raw).isdigit() else None
                        current_tickers = sorted(set(symbols or []) | set((portfolio_emulator.get_positions() or {}).keys()))
                        _hot = {
                            'status': 'running',
                            'progress': round(progress_pct, 2),
                            '_last_active': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
                        }
                        if backtest_start_time is not None:
                            try:
                                _hot['time_elapsed_seconds'] = max(0, int(now_loop - backtest_start_time))
                            except Exception:
                                pass
                        _meta = {
                            'backtest_id': backtest_id_int,
                            'pnl': pnl,
                            'pnl_percent': round(pnl_percent, 4) if pnl_percent is not None else None,
                            'timestamp': __import__('datetime').datetime.now().isoformat(),
                            'tickers': current_tickers,
                        }
                        # FULL uncapped sources: assemble() applies the legacy
                        # caps (trades tail-1000, pv downsample-3000, logs
                        # tail-500) on read, so the document is unchanged while
                        # the write stops rewriting megabytes every 2%.
                        _appended = {}
                        try:
                            _appended['trade'] = _convert_datetimes_to_iso(
                                list(portfolio_emulator.get_trade_history() or []))
                        except Exception:
                            pass
                        try:
                            _appended['pv'] = _convert_datetimes_to_iso(
                                list(portfolio_emulator.get_portfolio_history() or []))
                        except Exception:
                            pass
                        # NOTE: logs are deliberately NOT appended here. The
                        # heartbeat owns the log stream, because the log buffer
                        # is trimmed FIFO to 500 lines and only the heartbeat's
                        # emitted-count watermark can slice it correctly. Two
                        # owners keying off the same bounded list would either
                        # duplicate or drop lines.
                        if _backtest_decisions is not None:
                            _appended['decision'] = _convert_datetimes_to_iso(list(_backtest_decisions))
                            _appended['refusal'] = _convert_datetimes_to_iso(list(_backtest_refusals))
                        _brs.write_progress_tick(
                            _backtest_result_id, hot=_hot, metadata=_meta,
                            appended=_appended, seqs=_steps_written)
                        progress_update_ok = True
                        _backtest_progress_fail_count = 0
```

The `should_update` gating above it (`:17795-17807`, the daily-bar / date-change / +2% ladder) is **unchanged** — the write cadence is the same, only its payload shrinks.

> **Only the heartbeat writes logs.** The progress writer used to write `logs[-500:]` alongside everything else; it no longer touches them. The log buffer is trimmed FIFO to 500 lines, so slicing it correctly requires the logger's monotonic emitted counter, and exactly one writer may own that watermark. `_steps_written['log']` therefore has a single owner. The other five kinds have full uncapped in-memory sources and are safe to slice by index.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest backend/tests/test_backtest_split_writers.py -v
python3 -m pytest backend/tests/db/test_backtest_split.py -q
```
Expected: PASS, 15 tests

- [ ] **Step 5: Commit**

```bash
git add backend/backtest_result_store.py backend/broker.py \
        backend/tests/test_backtest_split_writers.py
git commit -m "$(cat <<'EOF'
feat(backtest): progress writer appends deltas instead of rewriting arrays

The legacy +2% write rewrote backtest_decisions and backtest_refusals in
their entirety plus three capped arrays -- 2.7 MB on the live sample, growing
every tick, ~8 GB of WAL per backtest on Postgres. It now writes four scalars
to the hot row, a small deep-merge patch to doc, and only the entries past
each kind's watermark. The write cadence and the assembled document are both
unchanged; a test reconstructs what the legacy writer would have stored from
the same sources and byte-compares.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 6: The terminal writer

**Files:**
- Modify: `backend/broker.py` — the terminal update/insert (~12933/12936)
- Modify: `backend/backtest_result_store.py` — add `write_terminal()`
- Modify: `backend/tests/test_backtest_split_writers.py`

**Interfaces:**
- Consumes: `brs.split_doc`, `brs.finalize_steps`, `brs.write_progress`, `store.update`, `store.insert`.
- Produces:
  ```python
  def write_terminal(backtest_id, result: dict, *, insert_if_absent: bool = False) -> None
      # metadata deep-merged into doc; each present array finalized into
      # BacktestSteps(final=true); BacktestProgress set to the terminal status
  ```

**What must not move:** `assert_secret_free(backtest_result)` stays at `broker.py:12931`, before the write, unchanged. The evidence projection (`_finalize_evidence_success`) stays where it is and lands in `doc` verbatim like any other metadata key.

**Update vs insert:** the legacy code updates when `_backtest_result_id is not None` and inserts otherwise (`:12934-12939`). `write_terminal(..., insert_if_absent=True)` covers the insert branch; the id is the one already in `result`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_backtest_split_writers.py`:

```python
def test_terminal_write_finalizes_every_present_array(split_schema):
    from db.json import canonical
    brs.write_stub(dict(STUB))
    result = dict(STUB)
    result.update({
        "status": "finished", "progress": 100, "pnl": 500.0,
        "pnl_percent": 5.0, "time_elapsed_seconds": 1234.5,
        "backtest_decisions": [{"d": i} for i in range(1200)],
        "backtest_refusals": [],
        "backtest_trades": [{"t": i} for i in range(1500)],
        "portfolio_value_history": [{"v": i} for i in range(4000)],
        "logs": ["l%d" % i for i in range(900)],
        "backtest_prices": [{"p": i} for i in range(2597)],
        "evidence": {"fixture": "sealed"},
    })
    brs.write_terminal(700001, result)
    got = brs.assemble(700001)
    assert canonical(got) == canonical(dict(sorted(result.items())))


def test_terminal_arrays_are_returned_uncapped(split_schema):
    brs.write_stub(dict(STUB))
    result = dict(STUB)
    result.update({"status": "finished",
                   "backtest_trades": [{"t": i} for i in range(1500)],
                   "logs": ["l%d" % i for i in range(900)]})
    brs.write_terminal(700001, result)
    got = brs.assemble(700001)
    assert len(got["backtest_trades"]) == 1500      # not tail-1000
    assert len(got["logs"]) == 900                  # not tail-500


def test_terminal_write_supersedes_the_live_rows(split_schema):
    brs.write_stub(dict(STUB))
    brs.append_steps(700001, "decision", [{"d": "live"}], start_seq=0)
    result = dict(STUB)
    result.update({"status": "finished", "backtest_decisions": [{"d": "final"}]})
    brs.write_terminal(700001, result)
    assert brs.assemble(700001)["backtest_decisions"] == [{"d": "final"}]


def test_terminal_write_sets_the_hot_row_to_the_terminal_status(split_schema):
    brs.write_stub(dict(STUB))
    result = dict(STUB)
    result.update({"status": "finished", "progress": 100,
                   "time_elapsed_seconds": 1234.5})
    brs.write_terminal(700001, result)
    prog = brs.read_progress(700001)
    assert prog["status"] == "finished" and prog["progress"] == 100
    assert prog["time_elapsed_seconds"] == 1234.5      # a float, preserved


def test_terminal_write_deep_merges_metadata(split_schema):
    stub = dict(STUB)
    stub["strategy_schema"] = {"name": "gna", "config": {"a": 1}}
    brs.write_stub(stub)
    result = {"id": 700001, "status": "finished",
              "strategy_schema": {"version": 9}}
    brs.write_terminal(700001, result)
    row = store.get("BacktestResults", 700001)
    assert row["strategy_schema"] == {"name": "gna", "config": {"a": 1},
                                      "version": 9}


def test_terminal_write_inserts_when_the_row_is_absent(split_schema):
    result = dict(STUB)
    result.update({"id": 700009, "backtest_id": 700009, "status": "finished"})
    brs.write_terminal(700009, result, insert_if_absent=True)
    assert brs.assemble(700009)["status"] == "finished"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest backend/tests/test_backtest_split_writers.py -k terminal -v`
Expected: FAIL — `AttributeError: module 'backtest_result_store' has no attribute 'write_terminal'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/backtest_result_store.py`:

```python
def write_terminal(backtest_id, result: dict, *,
                   insert_if_absent: bool = False) -> None:
    """The end-of-run write.

    Metadata is deep-merged into doc (the legacy call was ``.update(...)``,
    which merges); each array present in ``result`` is finalized into
    BacktestSteps with final=true, superseding the live rows; the hot row is
    set to the terminal status.

    assert_secret_free() is the CALLER's responsibility and stays at
    broker.py:12931, unchanged.
    """
    meta, steps, progress = split_doc(dict(result, id=result.get("id",
                                                                 backtest_id)))
    if insert_if_absent and store.get(RESULTS_TABLE, backtest_id) is None:
        store.insert(RESULTS_TABLE, meta, conflict="replace")
    else:
        store.update(RESULTS_TABLE, backtest_id, meta)
    for kind in STEP_KINDS:
        if kind in steps:
            finalize_steps(backtest_id, kind, steps[kind])
    if progress:
        write_progress(backtest_id, progress)
```

`backend/broker.py` — replace `:12931-12939`:

```python
                            assert_secret_free(backtest_result)
                            import backtest_result_store as _brs
                            if _backtest_result_id is not None:
                                _brs.write_terminal(_backtest_result_id, backtest_result)
                                _log(f"Updated backtest results in database (id={_backtest_result_id}, status=finished, P&L={final_pnl})", "green")
                            else:
                                _brs.write_terminal(backtest_result.get('id'),
                                                    backtest_result, insert_if_absent=True)
                                _log(f"Saved backtest results to database (instance_id={instance_id_for_db}, strategy_id={strategy_row_id})", "green")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest backend/tests/test_backtest_split_writers.py -v
python3 -m pytest backend/tests/db/test_backtest_split.py -q
```
Expected: PASS, 21 tests

- [ ] **Step 5: Commit**

```bash
git add backend/backtest_result_store.py backend/broker.py \
        backend/tests/test_backtest_split_writers.py
git commit -m "$(cat <<'EOF'
feat(backtest): terminal writer finalizes step arrays uncapped

Each array present in the terminal result is written to BacktestSteps with
final=true and a seq=0 marker, superseding the live rows and reading back
uncapped -- which is what the legacy terminal write stored. Metadata is
deep-merged (the legacy call was .update(), which merges) and the hot row
takes the terminal status, preserving the float time_elapsed_seconds a
finished run carries. assert_secret_free stays at broker.py:12931, unmoved.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 7: The stop, pause, and side-channel writers

**Files:**
- Modify: `backend/broker.py` — row-deleted stop (~5841), critical-pause resume CAS (~5890), `run=false` stop (~5910), nexus lookback set/clear (~6929, ~6948)
- Modify: `backend/backtest_critical_abort.py` — the pause write (`:170`) and its retry seam (`:303`)
- Modify: `backend/engines/backtest_engine.py` — the difficulty write (~1129), `_remove_row_and_mark_done` (~838-852)
- Modify: `backend/interactive_utils.py` — stop (`:5852`), delete (`:5827-5831`)
- Modify: `backend/backtest_result_store.py` — add `set_status()` and `patch_metadata()`
- Modify: `backend/tests/test_backtest_split_writers.py`

**Interfaces:**
- Consumes: `brs.write_progress`, `brs.delete_backtest`, `store.update`, `store.replace_if`, `store.delete`, `store.P.field(...).default("").eq(...)`.
- Produces:
  ```python
  def set_status(backtest_id, status: str, *, timestamp: str | None = None,
                 extra_metadata: dict | None = None) -> None
      # status -> hot row; timestamp and extra_metadata -> doc deep-merge
  def patch_metadata(backtest_id, patch: dict) -> None
      # doc deep-merge only; never touches the hot row or the steps
  def resume_from_critical_pause(backtest_id, cleared_fields: dict) -> bool
      # the CAS: only when the hot row's status is "paused_llm_critical"
  ```

**The one genuinely tricky site** is `broker.py:5890`, which today is a server-side `r.branch` inside `.replace(lambda row: ...)` — a compare-and-swap on `status == "paused_llm_critical"`, gated so a manual pause is not stomped. `status` now lives on the hot row, so the CAS moves to `BacktestProgress` and the field-clearing moves to a `doc` patch. Both must happen only when the predicate holds.

`backtest_critical_abort.py:170` and `:303` both carry a comment about RethinkDB's type-strict `get(int(backtest_id))`. That coercion is now `store.coerce_id("BacktestResults", backtest_id)`, which accepts both and raises on garbage — the comment should be updated, not deleted, because the reason it exists (a string id silently no-opping with `{skipped: 1}`) is exactly what `coerce_id` now prevents.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_backtest_split_writers.py`:

```python
def test_set_status_writes_the_hot_row_and_the_doc_timestamp(split_schema):
    brs.write_stub(dict(STUB))
    brs.set_status(700001, "stopped", timestamp="2026-08-22T04:00:00Z")
    assert brs.read_progress(700001)["status"] == "stopped"
    assert store.get("BacktestResults", 700001)["timestamp"] == "2026-08-22T04:00:00Z"
    assert brs.assemble(700001)["status"] == "stopped"


def test_patch_metadata_touches_neither_the_hot_row_nor_the_steps(split_schema):
    brs.write_stub(dict(STUB))
    brs.append_steps(700001, "decision", [{"n": 1}], start_seq=0)
    before = brs.read_progress(700001)
    brs.patch_metadata(700001, {"nexus_lookback": {"current": 5, "total": 10}})
    assert brs.read_progress(700001) == before
    assert brs.watermarks(700001) == {"decision": 1}
    assert store.get("BacktestResults", 700001)["nexus_lookback"]["current"] == 5


def test_patch_metadata_can_null_a_field(split_schema):
    """broker.py:6948 clears nexus_lookback with {"nexus_lookback": None}."""
    brs.write_stub(dict(STUB))
    brs.patch_metadata(700001, {"nexus_lookback": {"current": 5}})
    brs.patch_metadata(700001, {"nexus_lookback": None})
    row = store.get("BacktestResults", 700001)
    assert "nexus_lookback" in row and row["nexus_lookback"] is None


def test_resume_from_critical_pause_only_fires_on_the_critical_status(split_schema):
    brs.write_stub(dict(STUB))
    brs.write_progress(700001, {"status": "paused_llm_critical"})
    brs.patch_metadata(700001, {"pause_reason": "llm", "pause_attempts": 3})
    cleared = {"pause_reason": None, "pause_attempts": None}
    assert brs.resume_from_critical_pause(700001, cleared) is True
    assert brs.read_progress(700001)["status"] == "running"
    assert store.get("BacktestResults", 700001)["pause_reason"] is None


def test_resume_from_critical_pause_does_not_stomp_a_manual_pause(split_schema):
    brs.write_stub(dict(STUB))
    brs.write_progress(700001, {"status": "paused"})     # manual
    assert brs.resume_from_critical_pause(700001, {"pause_reason": None}) is False
    assert brs.read_progress(700001)["status"] == "paused"


def test_set_status_accepts_an_int_or_string_backtest_id(split_schema):
    brs.write_stub(dict(STUB))
    brs.set_status("700001", "error")
    assert brs.read_progress(700001)["status"] == "error"


def test_delete_removes_the_row_from_all_three_tables(split_schema):
    brs.write_stub(dict(STUB))
    brs.append_steps(700001, "decision", [{"n": 1}], start_seq=0)
    assert brs.delete_backtest(700001) is True
    assert store.sql('SELECT count(*) AS n FROM "BacktestSteps" '
                     "WHERE backtest_id='700001'")[0]["n"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest backend/tests/test_backtest_split_writers.py -k "set_status or patch_metadata or resume_from" -v`
Expected: FAIL — `AttributeError: module 'backtest_result_store' has no attribute 'set_status'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/backtest_result_store.py`:

```python
def set_status(backtest_id, status: str, *, timestamp: Optional[str] = None,
               extra_metadata: Optional[dict] = None) -> None:
    """Status goes to the hot row; timestamp and anything else deep-merges
    into doc. This is the stop/error/pause path."""
    write_progress(backtest_id, {"status": status})
    patch = dict(extra_metadata or {})
    if timestamp is not None:
        patch["timestamp"] = timestamp
    if patch:
        store.update(RESULTS_TABLE, backtest_id, patch)


def patch_metadata(backtest_id, patch: dict) -> None:
    """Deep-merge into doc. Never touches the hot row or the steps.

    ``{"k": None}`` sets JSON null, it does not delete the key -- which is
    what broker.py:6948's nexus_lookback clear expects.
    """
    if patch:
        store.update(RESULTS_TABLE, backtest_id, patch)


def resume_from_critical_pause(backtest_id, cleared_fields: dict) -> bool:
    """Compare-and-swap: flip the hot row to "running" and clear the stale
    pause_* metadata ONLY when the status is "paused_llm_critical", so a
    manual pause is never stomped. Replaces broker.py:5890's server-side
    r.branch inside .replace(lambda row: ...).
    """
    rows = store.sql(
        'UPDATE "BacktestProgress" SET '
        "  payload = jsonb_deep_merge(payload, '{\"status\":\"running\"}'::jsonb), "
        "  updated_at = now() "
        "WHERE id = %s AND payload ->> 'status' = 'paused_llm_critical' "
        "RETURNING id",
        (_bid(backtest_id),))
    if not rows:
        return False
    patch = dict(cleared_fields or {})
    patch["resumed_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    store.update(RESULTS_TABLE, backtest_id, patch)
    return True
```

Then the call sites, each a one-for-one replacement:

`backend/broker.py:5841` and `:5910` (both stop paths) become:

```python
                    if _backtest_result_id is not None:
                        import backtest_result_store as _brs
                        _brs.set_status(_backtest_result_id, 'stopped',
                                        timestamp=datetime.utcnow().isoformat() + 'Z')
```

`backend/broker.py:5890` (the CAS) becomes:

```python
                        from backtest_critical_abort import cleared_pause_fields as _bca_cleared_pause
                        import backtest_result_store as _brs
                        _brs.resume_from_critical_pause(_backtest_result_id,
                                                        _bca_cleared_pause())
```

Note `resume_from_critical_pause` writes `resumed_at` itself, so drop the `"resumed_at": r.now()` entry that the ReQL branch carried.

`backend/broker.py:6929` and `:6948` (nexus lookback) become `_brs.patch_metadata(_backtest_result_id, {...})` with the same payload dicts, and their `_backtest_db_conn is None` guards drop (the pool owns connections now — keep the `_backtest_result_id is None` guard).

`backend/backtest_critical_abort.py:170`:

```python
                # BacktestResults.id is written as int by the engine
                # (broker.py:5842 and every other writer). store.coerce_id
                # accepts an int or a string and raises on anything else, so
                # the old failure mode -- a string id silently no-opping with
                # {skipped: 1} -- is impossible now rather than merely avoided.
                import backtest_result_store as _brs
                _brs.set_status(backtest_id, payload.get("status") or "paused_llm_critical",
                                extra_metadata={k: v for k, v in payload.items()
                                                if k != "status"})
```

with `"paused_at": r.now()` in the payload replaced by
`"paused_at": datetime.datetime.now(datetime.timezone.utc).isoformat()`.

`backend/backtest_critical_abort.py:303`'s `_do(c, rdb)` seam collapses to a single call — the retry-on-a-fresh-connection logic is now the pool's job:

```python
    def _do():
        import backtest_result_store as _brs
        _brs.set_status(rrow_id, payload.get("status") or "paused_llm_critical",
                        extra_metadata={k: v for k, v in payload.items()
                                        if k != "status"})
```

Keep the surrounding `try/except` and the `conn` parameter in the signature so the ~10 tests that cage this seam do not change arity in this commit.

`backend/engines/backtest_engine.py:1129` (difficulty) becomes:

```python
                    import backtest_result_store as _brs
                    _brs.patch_metadata(row_id, {'difficulty': avg_difficulty})
```

`backend/engines/backtest_engine.py:842` (`_remove_row_and_mark_done`) becomes `store.delete("BacktestInstances", row_id)` — this is the **queue** row, not the result row, so it does not go through `delete_backtest`.

`backend/interactive_utils.py:5852` (stop) becomes:

```python
        try:
            import backtest_result_store as _brs
            _brs.set_status(bid, "stopped")
        except Exception:
            pass
```

`backend/interactive_utils.py:5827-5831` (delete) becomes:

```python
    if "BacktestResults" in tables:
        import backtest_result_store as _brs
        if _brs.delete_backtest(bid):
            found = True
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest backend/tests/test_backtest_split_writers.py -v
python3 -m pytest backend/tests/test_backtest_critical_abort.py -v
python3 -m pytest backend/tests/db/test_backtest_split.py -q
```
Expected: PASS. `test_backtest_critical_abort.py` exercises the `:303` seam; if it fails, the seam's signature changed and that is a bug in this task.

- [ ] **Step 5: Commit**

```bash
git add backend/backtest_result_store.py backend/broker.py \
        backend/backtest_critical_abort.py backend/engines/backtest_engine.py \
        backend/interactive_utils.py backend/tests/test_backtest_split_writers.py
git commit -m "$(cat <<'EOF'
feat(backtest): stop, pause, and side-channel writers on the split tables

Status goes to the hot row, everything else deep-merges into doc. The
critical-pause resume keeps its compare-and-swap semantics -- it fires only
when the status is paused_llm_critical, so a manual pause is never stomped --
now as one conditional UPDATE on BacktestProgress instead of a server-side
r.branch inside .replace(lambda row: ...). backtest_critical_abort's int()
coercion becomes store.coerce_id, which raises on garbage rather than
silently no-opping with {skipped: 1}.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 8: The list endpoint fast path

**Files:**
- Modify: `backend/interactive_utils.py` — the fast path (5205-5273), delete the slow path (5279-5300 and its `_slim` at ~5330-5340), and the index-ensure block (370-385)
- Modify: `backend/backtest_result_store.py` — add `list_rows()`
- Create: `backend/tests/test_backtest_list_endpoint.py`

**Interfaces:**
- Consumes: `store.sql`, `_LIST_TICKER_PREVIEW` (= 4, `interactive_utils.py:5145`).
- Produces:
  ```python
  LIST_FIELDS = ("id", "backtest_id", "instance_id", "instance", "tickers",
                 "start_date", "end_date", "status", "progress",
                 "time_elapsed_seconds", "started_at", "created_at",
                 "timestamp", "completed_at")
  def list_rows(*, instance_filter: str | None, page: int, per_page: int,
                sort_order: str, ticker_preview: int) -> tuple[list[dict], list[dict], int]
      # -> (active_rows, page_rows, db_total)
  ```

**What the SQL must do, and why:** the legacy `_slim()` is `.pluck(16 fields).merge(lambda row: {tickers: row["tickers"].default([]).limit(4), tickers_total: row["tickers"].default([]).count()})`. RethinkDB's `pluck` still materialises the whole document server-side, which is why one list render read the entire table in **12.2 s for 1,426 rows** (`scripts/create_backtest_list_indices.py:6-11`). The replacement reads only generated columns and computes the preview with `jsonb_path_query_array(doc, '$.tickers[0 to N]')` and `jsonb_array_length`, so a 5-13 MB `doc` is never detoasted. `status` and `progress` come from `BacktestProgress`, joined; the active set comes from its `status_norm` index.

**What must not change:** the active-then-page merge, the dedupe, and the page-1-only pinning at `:5271-5273` stay byte-identical. `_pre_paged_total` still comes from a `count()` over the same base selection.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_backtest_list_endpoint.py`:

```python
"""The list fast path must return what the legacy pluck+merge returned."""
import os

import pytest

import backtest_result_store as brs
from db import schema, store

pytestmark = pytest.mark.skipif(
    not os.environ.get("PG_TEST_DSN"),
    reason="PG_TEST_DSN not set (run: ./scripts/dev_pg.sh up)")

_LIST_TICKER_PREVIEW = 4


@pytest.fixture
def seeded(pg_schema):
    schema.ensure_schema(tables=["BacktestResults", "BacktestSteps",
                                 "BacktestProgress"])
    for i in range(1, 8):
        doc = {
            "id": 800000 + i, "backtest_id": 800000 + i,
            "instance_id": "main" if i % 2 else 5,
            "status": "running" if i == 7 else "finished",
            "progress": 42.5 if i == 7 else 100,
            "timestamp": "2026-08-%02dT00:00:00" % (10 + i),
            "start_date": "2026-03-01 00:00:00", "end_date": "2026-04-01 00:00:00",
            "tickers": ["T%d" % n for n in range(i)],
            "time_elapsed_seconds": 10 * i,
            "pnl": 1.0 * i, "pnl_percent": 0.1 * i,
            "portfolio_value_history": [], "backtest_trades": [],
            "backtest_prices": [], "logs": [],
            "strategy_schema": {"blob": "x" * 40000},   # must never be read
        }
        brs.write_split(doc, final=(i != 7))
    return pg_schema


def test_list_returns_only_the_summary_fields(seeded):
    active, page, total = brs.list_rows(instance_filter=None, page=1,
                                        per_page=50, sort_order="desc",
                                        ticker_preview=_LIST_TICKER_PREVIEW)
    assert total == 7
    for row in page:
        assert "strategy_schema" not in row
        assert "backtest_decisions" not in row
        assert set(row) <= set(brs.LIST_FIELDS) | {"tickers_total"}


def test_tickers_are_previewed_and_counted(seeded):
    _active, page, _ = brs.list_rows(instance_filter=None, page=1, per_page=50,
                                     sort_order="desc",
                                     ticker_preview=_LIST_TICKER_PREVIEW)
    row = [r for r in page if r["id"] == 800007][0]
    assert row["tickers"] == ["T0", "T1", "T2", "T3"]       # limit(4)
    assert row["tickers_total"] == 7                        # count()


def test_a_row_without_tickers_previews_as_an_empty_list(seeded):
    brs.write_split({"id": 800100, "status": "finished", "progress": 100,
                     "timestamp": "2026-08-01T00:00:00"}, final=True)
    _a, page, _t = brs.list_rows(instance_filter=None, page=1, per_page=50,
                                 sort_order="desc", ticker_preview=4)
    row = [r for r in page if r["id"] == 800100][0]
    assert row["tickers"] == [] and row["tickers_total"] == 0


def test_status_and_progress_come_from_the_hot_row(seeded):
    brs.write_progress(800001, {"status": "paused", "progress": 12.5})
    _a, page, _t = brs.list_rows(instance_filter=None, page=1, per_page=50,
                                 sort_order="desc", ticker_preview=4)
    row = [r for r in page if r["id"] == 800001][0]
    assert row["status"] == "paused" and row["progress"] == 12.5


def test_active_rows_use_the_status_norm_index(seeded):
    brs.write_progress(800002, {"status": "paused_llm_critical"})
    active, _page, _t = brs.list_rows(instance_filter=None, page=1, per_page=50,
                                      sort_order="desc", ticker_preview=4)
    ids = {r["id"] for r in active}
    assert 800007 in ids                     # running
    assert 800002 in ids                     # paused_* normalises to paused


def test_ordering_is_timestamp_desc_bytewise(seeded):
    _a, page, _t = brs.list_rows(instance_filter=None, page=1, per_page=50,
                                 sort_order="desc", ticker_preview=4)
    stamps = [r["timestamp"] for r in page]
    assert stamps == sorted(stamps, key=lambda s: s.encode("utf-8"), reverse=True)


def test_instance_filter_matches_both_number_and_string_instance_ids(seeded):
    """592 live rows carry instance_id as a NUMBER, 833 as a STRING."""
    _a, page, total = brs.list_rows(instance_filter="5", page=1, per_page=50,
                                    sort_order="desc", ticker_preview=4)
    assert total == 3                        # the even-numbered seeds
    assert all(str(r["instance_id"]) == "5" for r in page)


def test_paging(seeded):
    _a, p1, total = brs.list_rows(instance_filter=None, page=1, per_page=3,
                                  sort_order="desc", ticker_preview=4)
    _a2, p2, _t = brs.list_rows(instance_filter=None, page=2, per_page=3,
                                sort_order="desc", ticker_preview=4)
    assert total == 7 and len(p1) == 3 and len(p2) == 3
    assert not ({r["id"] for r in p1} & {r["id"] for r in p2})


def test_the_slow_path_is_gone():
    import inspect

    import interactive_utils
    src = inspect.getsource(interactive_utils)
    assert "_LIST_TICKER_PREVIEW" in src           # the constant survives
    assert src.count("def _slim(") == 0, "the pluck+merge slow path must be deleted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_backtest_list_endpoint.py -v`
Expected: FAIL — `AttributeError: module 'backtest_result_store' has no attribute 'list_rows'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/backtest_result_store.py`:

```python
LIST_FIELDS = ("id", "backtest_id", "instance_id", "instance", "tickers",
               "start_date", "end_date", "status", "progress",
               "time_elapsed_seconds", "started_at", "created_at",
               "timestamp", "completed_at")

# Generated columns only -- doc is never detoasted here. The legacy pluck
# still materialised the whole document server-side, which is why one list
# render read the entire table in 12.2s for 1,426 rows
# (scripts/create_backtest_list_indices.py:6-11).
_LIST_SELECT = '''
SELECT r.id,
       r.doc -> 'backtest_id'          AS backtest_id,
       r.doc -> 'instance_id'          AS instance_id,
       r.doc -> 'instance'             AS instance,
       r.start_date, r.end_date, r.started_at, r.created_at,
       r.timestamp, r.completed_at,
       coalesce(p.payload -> 'status',   r.doc -> 'status')   AS status,
       coalesce(p.payload -> 'progress', r.doc -> 'progress') AS progress,
       coalesce(p.payload -> 'time_elapsed_seconds',
                r.doc -> 'time_elapsed_seconds')              AS time_elapsed_seconds,
       coalesce(jsonb_path_query_array(r.doc, ('$.tickers[0 to '
                || %(preview)s::text || ']')::jsonpath), '[]'::jsonb) AS tickers,
       coalesce(jsonb_array_length(r.doc -> 'tickers'), 0)      AS tickers_total
FROM "BacktestResults" r
LEFT JOIN "BacktestProgress" p ON p.id = r.id
'''

_ACTIVE_STATUSES = ("running", "queued", "pending", "paused")


def _list_row(row: dict) -> dict:
    out = {"id": int(row["id"]) if str(row["id"]).lstrip("-").isdigit()
           else row["id"]}
    for key in ("backtest_id", "instance_id", "instance", "start_date",
                "end_date", "started_at", "created_at", "timestamp",
                "completed_at", "status", "progress", "time_elapsed_seconds"):
        value = row.get(key)
        if value is not None:                 # ReQL pluck OMITS absent keys
            out[key] = value
    out["tickers"] = row.get("tickers") or []
    out["tickers_total"] = int(row.get("tickers_total") or 0)
    return out


def list_rows(*, instance_filter, page: int, per_page: int, sort_order: str,
              ticker_preview: int):
    """(active_rows, page_rows, db_total) for the backtest list endpoint."""
    preview = max(int(ticker_preview) - 1, 0)
    direction = "DESC" if sort_order == "desc" else "ASC"
    where, params = "", {"preview": preview}
    if instance_filter:
        # coalesce(instance_id, instance, '') is the instance_or_instance_id
        # index expression, and ->> stringifies, so the NUMBER/STRING split
        # across 592/833 live rows stops mattering.
        where = ("WHERE coalesce(r.doc->>'instance_id', r.doc->>'instance', '') "
                 "COLLATE \"C\" = %(instance)s ")
        params["instance"] = str(instance_filter)

    active_sql = (_LIST_SELECT + where +
                  ("AND " if where else "WHERE ") +
                  "(CASE WHEN lower(p.status) LIKE 'paused%%' THEN 'paused' "
                  " ELSE lower(p.status) END) = ANY(%(active)s)")
    active_params = dict(params, active=list(_ACTIVE_STATUSES))
    active_rows = [_list_row(r) for r in store.sql(active_sql, active_params)]

    # ``where`` is written against the ``r`` alias, so the count reuses it
    # verbatim. Extra keys in the params mapping (``preview``) are ignored.
    count_sql = 'SELECT count(*) AS n FROM "BacktestResults" r ' + where
    db_total = int(store.sql(count_sql, params)[0]["n"])

    page_i = max(1, int(page))
    pp = max(1, min(100, int(per_page)))
    # The list_ts index expression, verbatim: coalesce(doc->>'timestamp','')
    # COLLATE "C". Bytewise, so ISO-8601 lexicographic order is chronological.
    order_expr = "coalesce(r.doc->>'timestamp', '') COLLATE \"C\""
    page_sql = (_LIST_SELECT + where + "ORDER BY " + order_expr + " " +
                direction + " LIMIT %(limit)s OFFSET %(offset)s")
    page_params = dict(params, limit=pp, offset=(page_i - 1) * pp)
    page_rows = [_list_row(r) for r in store.sql(page_sql, page_params)]
    return active_rows, page_rows, db_total
```

> **`store.sql` and named parameters:** psycopg supports `%(name)s` placeholders with a dict of parameters, and `store.sql(query, params)` passes `params` straight through, so a dict works. Note the doubled `%%` in the `LIKE 'paused%%'` literal — a bare `%` inside a query that also uses placeholders is a psycopg format error.

`backend/interactive_utils.py` — replace the whole `if _fast:` block (`:5220-5273`) with:

```python
    if _fast:
        import backtest_result_store as _brs
        _active_rows, _page_rows, _db_total = _brs.list_rows(
            instance_filter=instance_filter, page=page, per_page=per_page,
            sort_order=sort_order, ticker_preview=_LIST_TICKER_PREVIEW)
        _page_i = max(1, int(page))
        # Explicit active rows belong to page 1 only -- the python sort pins
        # them first, and pinning them onto every page would repeat them.
        # merged{} below dedupes any active row the slice also contained.
        result_rows = (_active_rows if _page_i == 1 else []) + _page_rows
        _pre_paged_total = _db_total
```

Everything from `merged{}` onward is untouched.

Delete the `if has_results and not _fast:` slow path (`:5279-5300` and its `_slim` definition around `:5330-5340`) and simplify `_fast` to `has_results and sort_by == "completed_at"` — `ensure_schema()` guarantees the indexes, so the missing-index fallback has nothing to fall back from. Delete the `index_list()` probe at `:5205-5215` and the `instance_or_instance_id` ensure block at `:370-385`, both subsumed by `db.schema.ensure_schema()`.

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest backend/tests/test_backtest_list_endpoint.py -v
python3 -m pytest backend/tests -q -k backtest
```
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add backend/backtest_result_store.py backend/interactive_utils.py \
        backend/tests/test_backtest_list_endpoint.py
git commit -m "$(cat <<'EOF'
perf(backtest): list endpoint reads generated columns, never detoasts doc

The legacy _slim() pluck+merge still materialised the whole 5-13 MB document
server-side -- 12.2s for 1,426 rows, measured. The replacement reads generated
columns joined to BacktestProgress and computes the ticker preview with
jsonb_path_query_array / jsonb_array_length, so doc is never touched. The
active-then-page merge, dedupe, and page-1-only pinning are unchanged. The
missing-index slow path is deleted: ensure_schema guarantees the indexes.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 9: The detail, playback, and cross-table readers

**Files:**
- Modify: `backend/interactive_utils.py` — the seven bare `.get(bid)` sites (`:1440`, `:5827`, `:6039`, `:6085`, `:6316`, `:6841`, `:6875`) and best-per-strategy (`:6805-6820`)
- Modify: `backend/backtest_result_store.py` — add `read_status()` and `best_by_strategy_rows()`
- Create: `backend/tests/test_backtest_readers.py`

**Interfaces:**
- Consumes: `brs.assemble`, `brs.assemble_field`, `brs.read_progress`, `store.sql`.
- Produces:
  ```python
  def read_status(backtest_id) -> str | None
      # the hot row's status; None when there is no row at all
  def best_by_strategy_rows() -> list[dict]
      # [{"id", "instance_id", "pnl", "pnl_percent", "status"}, ...] over the
      # whole table, generated columns only -- the replacement for
      # interactive_utils.py:6811-6814's full-table pluck
  ```

**Site-by-site mapping.** Five of the seven need the whole document and become `assemble(bid)`. Two do not and get narrow helpers, which is the whole point of the split:

| site | today | after |
|---|---|---|
| `:1440` | `.get(rid)`, reads `status`, `pnl`, `pnl_percent`, `progress` | `read_progress(rid)` for status/progress plus `store.get("BacktestResults", rid)` for pnl — no step rows fetched |
| `:5827` | `.get(bid)` to test existence before deleting | folded into `delete_backtest(bid)` in Task 7; the bare get is deleted |
| `:6039` | `.get(bid)`, reads `status` | `read_status(bid)` |
| `:6085` | `.get(bid)`, full document (playback) | `assemble(bid)` |
| `:6316` | `.get(bid)`, full document (logs endpoint) | `assemble(bid)` |
| `:6841` | `.get(bid)` then reads only `portfolio_value_history` | `assemble_field(bid, "portfolio_value_history")` |
| `:6875` | `.get(bid)`, reads `status` and more | `assemble(bid)` — it reads more than status, so it keeps the full read |

Each site's `if doc is None: raise ValueError("Backtest result not found: %s" % bid)` stays exactly as written, and the narrow helpers return `None` for a missing backtest so that check still fires.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_backtest_readers.py`:

```python
import os

import pytest

import backtest_result_store as brs
from db import schema, store

pytestmark = pytest.mark.skipif(
    not os.environ.get("PG_TEST_DSN"),
    reason="PG_TEST_DSN not set (run: ./scripts/dev_pg.sh up)")


@pytest.fixture
def seeded(pg_schema):
    schema.ensure_schema(tables=["BacktestResults", "BacktestSteps",
                                 "BacktestProgress", "Instances"])
    brs.write_split({
        "id": 810001, "backtest_id": 810001, "instance_id": "main",
        "status": "finished", "progress": 100, "pnl": 55.0, "pnl_percent": 5.5,
        "timestamp": "2026-08-20T00:00:00", "tickers": ["A"],
        "time_elapsed_seconds": 12,
        "portfolio_value_history": [{"v": i} for i in range(20)],
        "backtest_trades": [], "backtest_prices": [], "logs": ["x"],
        "strategy_schema": {"blob": "y" * 1000},
    }, final=True)
    brs.write_split({
        "id": 810002, "backtest_id": 810002, "instance_id": "alt",
        "status": "running", "progress": 10.0, "pnl": -3.0, "pnl_percent": -0.3,
        "timestamp": "2026-08-21T00:00:00", "tickers": [],
        "time_elapsed_seconds": 4,
        "portfolio_value_history": [], "backtest_trades": [],
        "backtest_prices": [], "logs": [],
    }, final=False)
    return pg_schema


def test_read_status_returns_the_hot_row_status(seeded):
    assert brs.read_status(810001) == "finished"
    assert brs.read_status(810002) == "running"


def test_read_status_of_a_missing_backtest_is_none(seeded):
    assert brs.read_status(999999) is None


def test_read_status_follows_the_hot_row_not_a_stale_doc(seeded):
    brs.write_progress(810001, {"status": "stopped"})
    assert brs.read_status(810001) == "stopped"
    assert store.get("BacktestResults", 810001)["status"] == "finished"  # stale


def test_assemble_field_reads_the_history_without_the_document(seeded):
    got = brs.assemble_field(810001, "portfolio_value_history")
    assert got == [{"v": i} for i in range(20)]


def test_assemble_field_of_a_missing_backtest_is_none_or_empty(seeded):
    assert brs.assemble_field(999999, "portfolio_value_history") == []


def test_assemble_returns_the_whole_document_for_playback(seeded):
    doc = brs.assemble(810001)
    assert doc["strategy_schema"]["blob"].startswith("y")
    assert doc["portfolio_value_history"] and doc["logs"] == ["x"]


def test_best_by_strategy_rows_reads_only_the_summary_fields(seeded):
    rows = brs.best_by_strategy_rows()
    assert len(rows) == 2
    for row in rows:
        assert set(row) == {"id", "instance_id", "pnl", "pnl_percent", "status"}


def test_best_by_strategy_rows_reports_the_hot_row_status(seeded):
    rows = {r["id"]: r for r in brs.best_by_strategy_rows()}
    assert rows[810002]["status"] == "running"


def test_best_by_strategy_rows_omits_absent_keys_the_way_pluck_did(seeded):
    brs.write_split({"id": 810003, "status": "finished", "progress": 100,
                     "timestamp": "2026-08-22T00:00:00"}, final=True)
    row = [r for r in brs.best_by_strategy_rows() if r["id"] == 810003][0]
    assert "pnl" not in row and "instance_id" not in row
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_backtest_readers.py -v`
Expected: FAIL — `AttributeError: module 'backtest_result_store' has no attribute 'read_status'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/backtest_result_store.py`:

```python
def read_status(backtest_id) -> Optional[str]:
    """The hot row's status. None when there is no BacktestResults row at all
    -- so the callers' ``if doc is None: raise ValueError(...)`` still fires."""
    progress = read_progress(backtest_id)
    if progress and progress.get("status") is not None:
        return str(progress["status"])
    row = store.get(RESULTS_TABLE, backtest_id)
    if row is None:
        return None
    status = row.get("status")
    return None if status is None else str(status)


def best_by_strategy_rows() -> list:
    """Every row's five summary fields, generated columns only.

    Replaces interactive_utils.py:6811-6814's full-table
    .pluck("id","instance_id","pnl","pnl_percent","status"), which
    materialised every 5-13 MB document server-side. Absent keys are OMITTED,
    not emitted as null, matching ReQL pluck.
    """
    rows = store.sql('''
        SELECT r.id,
               r.doc -> 'instance_id' AS instance_id,
               r.doc -> 'pnl'         AS pnl,
               r.doc -> 'pnl_percent' AS pnl_percent,
               coalesce(p.payload -> 'status', r.doc -> 'status') AS status
        FROM "BacktestResults" r
        LEFT JOIN "BacktestProgress" p ON p.id = r.id
    ''')
    out = []
    for row in rows:
        rid = row["id"]
        picked = {"id": int(rid) if str(rid).lstrip("-").isdigit() else rid}
        for key in ("instance_id", "pnl", "pnl_percent", "status"):
            if row.get(key) is not None:
                picked[key] = row[key]
        out.append(picked)
    return out
```

Then the seven sites. `:6085`, `:6316`, `:6875` each become:

```python
    import backtest_result_store as _brs
    doc = _brs.assemble(bid)
    if doc is None:
        raise ValueError("Backtest result not found: %s" % bid)
```

`:6841` becomes:

```python
    import backtest_result_store as _brs
    portfolio_value_history = _brs.assemble_field(bid, "portfolio_value_history")
    if portfolio_value_history is None:
        raise ValueError("Backtest result not found: %s" % bid)
    if not portfolio_value_history:
```

`:6039` becomes:

```python
    import backtest_result_store as _brs
    _status = _brs.read_status(bid)
    if _status is not None:
        status = _status.strip() or "running"
```

`:1440` becomes:

```python
            if has_results:
                import backtest_result_store as _brs
                _prog = _brs.read_progress(rid) or {}
                result_doc = store.get("BacktestResults", rid)
                if result_doc is not None:
                    result_doc = dict(result_doc)
                    result_doc.update({k: v for k, v in _prog.items()})
```

leaving the `if result_doc:` block below it unchanged.

`:6805-6820` becomes:

```python
    import backtest_result_store as _brs
    results = _brs.best_by_strategy_rows()
```

with the `skip_statuses` filter loop below it unchanged.

Also delete the now-dead `tables = list(r.db(DB_NAME).table_list().run(conn))` / `if "BacktestResults" not in tables: raise ValueError(...)` preambles at these sites only if `store.table_list()` is already called nearby; otherwise port them to `store.table_list()` verbatim. Do not delete the `ValueError` — the API contract depends on it.

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest backend/tests/test_backtest_readers.py -v
python3 -m pytest backend/tests -q -k "backtest or playback or interactive"
```
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add backend/backtest_result_store.py backend/interactive_utils.py \
        backend/tests/test_backtest_readers.py
git commit -m "$(cat <<'EOF'
feat(backtest): detail and playback readers on assemble(); narrow helpers

Five of the seven bare .get(bid) sites need the whole document and call
assemble(). The two that do not get narrow helpers -- :6841 reads only
portfolio_value_history and :6039 reads only status -- so they stop paying for
a 5-13 MB fetch. The cross-table best-per-strategy scan reads five generated
columns instead of plucking the entire table, and still omits absent keys the
way ReQL pluck did.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 10: The self-learning watcher

**Files:**
- Modify: `backend/engines/self_learning_engine.py` — `:525-545` (the `_open_feed` / `_handle` pair) and the `run_reconnecting_changefeed` call at `:576-579`
- Create: `backend/tests/test_self_learning_progress_watch.py`

**Interfaces:**
- Consumes: `watch.watch_table("BacktestProgress", on_change, label=..., fields=("id","status"), squash=True, include_initial=True, log=..., should_continue=...)` from Plan A.
- Produces: nothing new — the handler's shape is unchanged.

**Why this site is easy now.** Today it opens a changefeed on the whole `BacktestResults` table with a server-side `.pluck({"new_val": ["id","status"]})`, and the comment at `:529-530` says why: *"without it `new_val` is the whole 5-13MB document on every progress tick of a running backtest."* After the split the hot row **is** that projection, so the watcher moves to `BacktestProgress` and the projection stops being an optimisation at all.

**What must not change:** `include_initial=True` replays every row on each reconnect, and the persisted `processed_run_ids` watermark (`self_learning/store.py` `DEFAULT_CONFIG`) dedupes. Both stay. So does the `_TURN_INTERVAL_SECONDS` heartbeat thread at `:547-566`.

**One shape note:** `BacktestProgress` rows carry `id` and `payload`; `watch_table(..., fields=("id","status"))` projects the assembled row. The watcher reads rows through `store.run(Selection("BacktestProgress"))`, which returns `doc` — and `BacktestProgress` has no `doc` column. Give the handler what it needs by watching with `fields=None` and reading `id`/`status` off the row, or add a `doc`-shaped view. The implementation below takes the first option and reads the generated columns directly through a small adapter, because adding a view for one watcher is not worth the DDL.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_self_learning_progress_watch.py`:

```python
import os
import time

import pytest

import backtest_result_store as brs
from db import schema

pytestmark = pytest.mark.skipif(
    not os.environ.get("PG_TEST_DSN"),
    reason="PG_TEST_DSN not set (run: ./scripts/dev_pg.sh up)")


def _wait_for(predicate, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def progress_schema(pg_schema):
    schema.ensure_schema(tables=["BacktestResults", "BacktestSteps",
                                 "BacktestProgress"])
    return pg_schema


def test_watch_progress_rows_delivers_id_and_status(progress_schema):
    import self_learning_progress as slp        # the new adapter module
    seen = []
    w = slp.watch_progress_rows(lambda c: seen.append(c), label="test")
    w.start()
    try:
        brs.write_progress(820001, {"status": "running", "progress": 0})
        assert _wait_for(lambda: any(
            (c["new_val"] or {}).get("id") == "820001" for c in seen))
        change = [c for c in seen if (c["new_val"] or {}).get("id") == "820001"][0]
        assert set(change["new_val"]) == {"id", "status"}
        assert change["new_val"]["status"] == "running"
    finally:
        w.stop()


def test_watch_progress_rows_never_carries_the_document(progress_schema):
    """The whole point of the old server-side pluck: new_val must not be a
    5-13 MB document."""
    import self_learning_progress as slp
    brs.write_split({"id": 820002, "status": "running", "progress": 0,
                     "timestamp": "2026-08-22T00:00:00",
                     "strategy_schema": {"blob": "z" * 50000}}, final=False)
    seen = []
    w = slp.watch_progress_rows(lambda c: seen.append(c), label="test")
    w.start()
    try:
        assert _wait_for(lambda: seen)
        for change in seen:
            assert "strategy_schema" not in (change["new_val"] or {})
    finally:
        w.stop()


def test_include_initial_replays_existing_rows(progress_schema):
    import self_learning_progress as slp
    brs.write_progress(820003, {"status": "finished", "progress": 100})
    seen = []
    w = slp.watch_progress_rows(lambda c: seen.append(c), label="test")
    w.start()
    try:
        assert _wait_for(lambda: any(
            (c["new_val"] or {}).get("id") == "820003" for c in seen))
    finally:
        w.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_self_learning_progress_watch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'self_learning_progress'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/self_learning_progress.py`:

```python
"""Adapter: watch BacktestProgress and deliver ReQL-shaped (id, status) changes.

self_learning_engine.py used to open a changefeed on the whole
BacktestResults table with a server-side .pluck({"new_val": ["id","status"]}),
because "without it new_val is the whole 5-13MB document on every progress
tick of a running backtest" (:529-530). After the split the hot row IS that
projection, so this is a plain watch on BacktestProgress.

BacktestProgress has no ``doc`` column, so this adapter reads the generated
columns and hands the handler the same two-key dict the pluck produced.
"""
from __future__ import annotations

from typing import Callable, Optional

from db import store, watch

TABLE = "BacktestProgress"


def _project(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    return {"id": row.get("id"), "status": row.get("status")}


def current_rows() -> list:
    return [_project(r) for r in store.sql(
        'SELECT id, payload ->> \'status\' AS status FROM "BacktestProgress"')]


def watch_progress_rows(on_change: Callable[[dict], None], *, label: str,
                        log=None, should_continue=None):
    """A Watcher over BacktestProgress delivering {"old_val", "new_val"} with
    only ``id`` and ``status`` populated.

    include_initial=True replays every row on start and on every reconnect;
    the persisted processed_run_ids watermark in self_learning/store.py
    dedupes, exactly as it does today.
    """
    def _adapt(change: dict) -> None:
        on_change({"old_val": _project(change.get("old_val")),
                   "new_val": _project(change.get("new_val"))})

    return watch.watch_table(
        TABLE, _adapt, label=label, include_initial=True, squash=True,
        log=log, should_continue=should_continue)
```

> **Implementation note for `db/watch.py`:** `Watcher._snapshot()` calls `store.run(Selection(table))`, which selects `doc`. `BacktestProgress` has no `doc` column, so add a two-line special case in `_snapshot`: when `store.sql("SELECT to_regclass...")`-style detection is overkill, check `schema.spec(table).ddl is not None and "doc jsonb" not in (spec.ddl or "")` and fall back to `store.sql('SELECT * FROM "<T>"')`, keying by `pk_field`. Write the failing test for that in this task and fix `watch.py` here rather than pre-emptively in Plan A — this is the only table in the repo with that shape.

`backend/engines/self_learning_engine.py` — replace `:525-545` and `:576-579`:

```python
    processed = set(store.get_config(conn).get("processed_run_ids") or [])

    def _handle(change):
        try:
            if not _should_run(None):
                return
            new_val = (change or {}).get("new_val") or {}
            run_id = new_val.get("id")
            if run_id is None:
                return
            _handle_run(None, run_id, new_val.get("status"), processed)
        except Exception as exc:
            _log(f"change handler error: {type(exc).__name__}: {exc}", "red")

    import self_learning_progress as _slp
    _watcher = _slp.watch_progress_rows(_handle, label="SelfLearningRuns",
                                        log=_log_raw,
                                        should_continue=_should_continue)
    _watcher.start()
```

`_should_run(conn)` and `_handle_run(conn, ...)` take a connection today; pass `None` and let them fall back to `store` internally (that change belongs to group F, which owns `self_learning/store.py`). If they still require a live connection at this point in the port, keep `run_reconnecting_changefeed` with `open_feed=lambda c: watch.feed("BacktestProgress", include_initial=True)` and `get_conn=pool.listen_connection` instead — the signature is unchanged (Plan A Task 12), so that is a one-line swap either way.

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest backend/tests/test_self_learning_progress_watch.py -v
python3 -m pytest backend/tests/db/test_watch.py -q
python3 -m pytest backend/tests -q -k self_learning
```
Expected: PASS, 3 new tests; the Plan A watch suite still green after the `_snapshot` special case.

- [ ] **Step 5: Commit**

```bash
git add backend/self_learning_progress.py backend/db/watch.py \
        backend/engines/self_learning_engine.py \
        backend/tests/test_self_learning_progress_watch.py
git commit -m "$(cat <<'EOF'
feat(backtest): self-learning watches BacktestProgress, not the whole table

The old feed needed a server-side pluck because new_val was otherwise the
whole 5-13 MB document on every progress tick. After the split the hot row IS
that projection, so the projection stops being an optimisation. include_initial
and the persisted processed_run_ids watermark are unchanged.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 11: The end-to-end lifecycle test

**Files:**
- Create: `backend/tests/test_backtest_lifecycle_e2e.py`

**Interfaces:**
- Consumes: everything built in Tasks 2–10. No production code changes in this task.
- Produces: the regression net that catches a writer and a reader drifting apart.

**What it proves:** a backtest driven through its whole life — stub, three progress ticks, five heartbeats, a critical pause, a resume, a terminal write — produces at every step exactly the document the legacy writers would have produced, and the list endpoint and the detail endpoint agree with each other throughout.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_backtest_lifecycle_e2e.py`:

```python
"""One backtest, driven through its whole life, checked at every step."""
import os

import pytest

import backtest_result_store as brs
from broker_snapshot_helpers import downsample_history
from db import schema, store
from db.json import canonical

pytestmark = pytest.mark.skipif(
    not os.environ.get("PG_TEST_DSN"),
    reason="PG_TEST_DSN not set (run: ./scripts/dev_pg.sh up)")

BID = 830001


@pytest.fixture
def lifecycle(pg_schema):
    schema.ensure_schema(tables=["BacktestResults", "BacktestSteps",
                                 "BacktestProgress"])
    return pg_schema


def _stub():
    return {
        "id": BID, "backtest_id": BID, "status": "running", "progress": 0,
        "timestamp": "2026-08-22T03:00:00Z", "instance_id": None,
        "strategy_id": None, "pnl": None, "pnl_percent": None,
        "start_date": "2026-03-01 00:00:00", "end_date": "2026-04-01 00:00:00",
        "tickers": ["AACI", "AA"], "time_elapsed_seconds": None,
        "portfolio_value_history": [], "backtest_trades": [],
        "backtest_prices": [], "logs": [],
    }


def test_a_backtest_reads_correctly_at_every_step_of_its_life(lifecycle):
    # --- stub -----------------------------------------------------------
    brs.write_stub(_stub())
    assert canonical(brs.assemble(BID)) == canonical(dict(sorted(_stub().items())))

    # --- three progress ticks + five heartbeats --------------------------
    seqs, log_seq = {}, 0
    decisions, trades, history, logs = [], [], [], []
    for tick in range(1, 4):
        decisions += [{"d": tick * 100 + i} for i in range(400)]
        trades += [{"t": tick * 100 + i} for i in range(600)]
        history += [{"v": float(tick * 1000 + i)} for i in range(2000)]
        brs.write_progress_tick(
            BID,
            hot={"status": "running", "progress": tick * 25.0,
                 "time_elapsed_seconds": tick * 100,
                 "_last_active": "2026-08-22T03:%02d:00+00:00" % tick},
            metadata={"pnl": 1.0 * tick, "pnl_percent": 0.1 * tick,
                      "timestamp": "2026-08-22T03:%02d:00" % tick,
                      "tickers": ["AACI", "AA"]},
            appended={"decision": decisions, "trade": trades, "pv": history},
            seqs=seqs)
        for beat in range(5):
            new = ["t%d-b%d" % (tick, beat)]
            logs += new
            log_seq = brs.heartbeat(BID, last_active="hb", new_log_lines=new,
                                    log_seq=log_seq)
        got = brs.assemble(BID)
        assert got["backtest_decisions"] == decisions
        assert got["backtest_trades"] == trades[-1000:]
        assert got["portfolio_value_history"] == list(downsample_history(history, 3000))
        assert got["logs"] == logs[-500:]
        assert got["progress"] == tick * 25.0
        assert got["pnl"] == 1.0 * tick

    # --- critical pause and resume ---------------------------------------
    brs.set_status(BID, "paused_llm_critical",
                   extra_metadata={"pause_reason": "llm", "pause_attempts": 3})
    assert brs.read_status(BID) == "paused_llm_critical"
    assert brs.assemble(BID)["status"] == "paused_llm_critical"
    assert brs.resume_from_critical_pause(BID, {"pause_reason": None,
                                                "pause_attempts": None}) is True
    assert brs.read_status(BID) == "running"

    # The steps survived the pause untouched.
    assert brs.assemble(BID)["backtest_decisions"] == decisions

    # --- terminal write ---------------------------------------------------
    result = dict(_stub())
    result.update({
        "status": "finished", "progress": 100, "pnl": 42.0,
        "pnl_percent": 4.2, "time_elapsed_seconds": 412.5,
        "backtest_decisions": decisions, "backtest_refusals": [],
        "backtest_trades": trades, "portfolio_value_history": history,
        "logs": logs, "backtest_prices": [{"p": i} for i in range(2597)],
        "evidence": {"fixture": "sealed"},
        "pause_reason": None, "pause_attempts": None,
        "resumed_at": brs.assemble(BID)["resumed_at"],
        "timestamp": "2026-08-22T03:03:00",
    })
    brs.write_terminal(BID, result)
    assert canonical(brs.assemble(BID)) == canonical(dict(sorted(result.items())))


def test_the_list_and_detail_endpoints_agree_throughout(lifecycle):
    brs.write_stub(_stub())
    for progress in (0.0, 33.0, 66.0, 100.0):
        brs.write_progress_tick(BID, hot={"status": "running",
                                          "progress": progress},
                                metadata={}, appended={}, seqs={})
        _active, page, _total = brs.list_rows(
            instance_filter=None, page=1, per_page=50, sort_order="desc",
            ticker_preview=4)
        listed = [r for r in page if r["id"] == BID][0]
        detail = brs.assemble(BID)
        assert listed["status"] == detail["status"]
        assert listed["progress"] == detail["progress"]
        assert listed["tickers_total"] == len(detail["tickers"])


def test_a_stopped_run_reads_as_a_stopped_run(lifecycle):
    brs.write_stub(_stub())
    brs.append_steps(BID, "decision", [{"d": 1}], start_seq=0)
    brs.set_status(BID, "stopped", timestamp="2026-08-22T04:00:00Z")
    got = brs.assemble(BID)
    assert got["status"] == "stopped"
    assert got["timestamp"] == "2026-08-22T04:00:00Z"
    assert got["backtest_decisions"] == [{"d": 1}]     # not lost by the stop
    assert got["logs"] == []                           # always-present, empty


def test_deleting_mid_run_leaves_nothing_behind(lifecycle):
    brs.write_stub(_stub())
    brs.write_progress_tick(BID, hot={"status": "running", "progress": 5},
                            metadata={}, appended={"decision": [{"d": 1}]},
                            seqs={})
    assert brs.delete_backtest(BID) is True
    assert brs.assemble(BID) is None
    assert store.sql('SELECT count(*) AS n FROM "BacktestSteps" '
                     "WHERE backtest_id = %s", (str(BID),))[0]["n"] == 0
    assert store.sql('SELECT count(*) AS n FROM "BacktestProgress" '
                     "WHERE id = %s", (str(BID),))[0]["n"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_backtest_lifecycle_e2e.py -v`
Expected: FAIL — with Tasks 2–10 done it should fail only if a writer and a reader disagree. If it passes on the first run, confirm the file is actually collected (`-v` shows four test names) before believing it.

- [ ] **Step 3: Fix whatever it caught**

No new production module. Any failure here is a bug in Tasks 2–10; fix it in the module that owns the behaviour:

- a wrong array → `backtest_result_store._fetch_steps` or `_apply_cap`
- a wrong scalar type → `write_progress` / the `BacktestProgress.payload` column
- a key present that should be absent, or absent that should be present → `_ALWAYS_PRESENT` and the `has_final` marker logic
- list and detail disagreeing → `list_rows`'s `coalesce(p.payload -> ..., r.doc -> ...)` fallbacks

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest backend/tests/test_backtest_lifecycle_e2e.py -v
python3 -m pytest backend/tests/db -q
env -u PG_TEST_DSN python3 -m pytest backend/tests -q
```
Expected: PASS. The last command is the no-database gate: the whole suite must still run on a laptop with no Postgres, with the split tests skipping.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_backtest_lifecycle_e2e.py
git commit -m "$(cat <<'EOF'
test(backtest): end-to-end lifecycle regression for the split

Drives one backtest through stub, three progress ticks, fifteen heartbeats, a
critical pause, a resume, and a terminal write, byte-comparing the assembled
document against what the legacy writers would have produced at every step,
and asserting the list and detail endpoints agree throughout. This is the net
that catches a writer and a reader drifting apart.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Final verification

```bash
export PG_TEST_DSN="$(./scripts/dev_pg.sh dsn)"
python3 -m pytest backend/tests/db/test_backtest_split.py -v          # the gate
python3 -m pytest backend/tests/test_backtest_lifecycle_e2e.py -v
python3 -m pytest backend/tests -q                                   # everything
env -u PG_TEST_DSN python3 -m pytest backend/tests -q                # no-DB tier
grep -n "BacktestResults" backend/broker.py backend/interactive_utils.py \
     backend/engines/backtest_engine.py backend/backtest_critical_abort.py \
  | grep -v backtest_result_store
```

Plan B is done when: the round-trip gate is byte-identical at all six stages, the lifecycle test passes, the whole suite passes in both tiers, and the last `grep` shows no remaining direct ReQL access to `BacktestResults` outside `backtest_result_store.py`.

**Not in Plan B, deliberately:** the migration script's `BacktestResults` split during the COPY (spec §7.1 step 5) — it calls `backtest_result_store.write_split(doc, final=True)`, which this plan ships and tests, but the script itself belongs to the migration task. The rest of `broker.py`, `interactive_utils.py`, and `backtest_engine.py` (spec §10.2 groups A, D, J) also stay out; Plan B owns only the `BacktestResults` sites, and D and J run on top of its commits because a file cannot have two agents.

---

## Ambiguities resolved while writing this plan

| # | gap in the spec | resolution | reversible? |
|---|---|---|---|
| 1 | Spec §3.5's `BacktestProgress` DDL types `progress` as `double precision` and `time_elapsed_seconds` as `integer`, but the overlay must be byte-identical. Live data: a stopped run's `progress` is an **int** (`108477`), a running run's is a **float** (`138148`); a running run's `time_elapsed_seconds` is an **int**, a finished run's is a **float** (`102463`). | `BacktestProgress` gains `payload jsonb` holding the values verbatim; `status`/`progress`/`time_elapsed_seconds` become STORED generated columns over it for the index and the list endpoint. `last_active` stays a plain writer-set column — a text→`timestamptz` cast is only STABLE and cannot be generated. | yes |
| 2 | Spec §3.5's `assemble()` uses `if final_rows:` to detect a terminal write, which cannot distinguish "finalized with zero entries" from "never finalized". A stopped run legitimately finalizes five empty arrays. | `final=true` reserves `seq = 0` as a marker row with a JSON-null doc; entries start at `seq = 1`. | yes |
| 3 | Spec §3.5's writer table has the progress writer appending "the entries past each kind's watermark", but `logs` come from a buffer that `intellistock_logger.py:210-215` trims FIFO to 500 lines, so `len(buffer)` saturates and an index watermark silently stops advancing. | A monotonic `context_log_lines_emitted(ctx)` counter is added to the logger, and **only the heartbeat writes logs** — one owner for that watermark. The other five kinds have full uncapped in-memory sources. | yes |
| 4 | Spec §3.5 says the progress writer applies the legacy caps on write and `assemble` applies them on read; doing both would downsample twice. | Writers append **uncapped**; `assemble` caps on read. Since the legacy writer derived its capped arrays from the same uncapped sources, the bytes match. | no — doing it the other way breaks byte-identity |
| 5 | Spec §3.5 lists `:6875` among the sites that get a narrow helper, but the code at that line reads `status` **and more**. | `:6875` keeps the full `assemble(bid)`. Only `:6841` (portfolio history) and `:6039` (status) get narrow helpers. | yes |
| 6 | `store.run(Selection("BacktestProgress"))` selects `doc`, and `BacktestProgress` has no `doc` column, so Plan A's `Watcher._snapshot()` cannot watch it. | Task 10 adds a `_snapshot` fallback in `db/watch.py` for tables whose `TableSpec.ddl` has no `doc jsonb`, keyed by `pk_field`. It is the only such table in the repo. | yes |
| 7 | Spec §3.5 says the terminal write puts "each final array into `BacktestSteps(final=true)` in one `COPY`". | `finalize_steps` uses a single multi-row `INSERT ... SELECT FROM unnest(...)` rather than `COPY`. Same one round trip, and it composes with `ON CONFLICT DO NOTHING`, which `COPY` does not. Switch to `COPY` if a benchmark shows it matters. | yes |
| 8 | The spec does not say whether the fixture is real or synthetic. | Both: `scripts/dev_fetch_backtest_fixture.py` pulls real documents read-only when the live DB is reachable and falls back to synthetic with the same key sets when it is not; `BACKTEST_SPLIT_FIXTURE=<path>` runs the gate against an operator-supplied export. | yes |
