# Kalshi Paper vs Real-Money Separation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline). Steps use checkbox (`- [ ]`) syntax. Spec: `docs/superpowers/specs/2026-07-06-kalshi-paper-real-separation-design.md`.

**Goal:** Keep paper and real-money Kalshi trading in two logically separate spaces — engine, API, and UI each see only the active mode's data; paper data hidden-but-preserved; engine restarts on a paper-mode toggle.

**Architecture:** One discriminator — `is_real_mode()` (a pure mirror of `engine.should_execute`) — applied on every read. Paper data is filtered, never moved/deleted. Engine stops marking/expiring paper positions when real; the `server.py` changefeed restarts the container on a mode change; the API scopes decisions/orders/paper-P&L by mode; the web UI gates paper sections on the existing `liveReal`.

**Tech Stack:** Python (FastAPI, RethinkDB, pytest), Docker (per-instance engine container), Vue 3 (frontend).

## Global Constraints

- **Do NOT modify `engine.should_execute` (`backend/kalshi/engine.py:38`)** — it is the real-order gate. Mirror it in `kalshi/mode.py` and enforce parity with a contract test.
- Discriminator everywhere: `is_real_mode(environment, live_enabled, paper_mode)`; `show_paper = not is_real_mode(...)`. Demo instances are `dry`/paper-showing.
- No schema migration, no backfill, nothing deleted. Paper rows already carry `paper=True` on placed fills.
- Restart trigger scoped to a paper_mode/live_enabled change on a **running** instance only.
- Run `gitnexus_impact` before editing each symbol; `gitnexus_detect_changes` before committing.
- Tests run from `backend/`: `cd backend && python3 -m pytest tests/<file> -q`.

---

### Task 0: Confirm test harness

- [ ] Run an existing Kalshi test to confirm the command/paths.
  Run: `cd backend && python3 -m pytest tests/test_kalshi_decisions.py -q`
  Expected: PASS (baseline).

---

### Task 1: `kalshi/mode.py` — pure mode discriminator + row scoping

**Files:**
- Create: `backend/kalshi/mode.py`
- Test: `backend/tests/test_kalshi_mode.py`

**Interfaces — Produces:**
- `is_real_mode(environment: str, live_enabled: bool, paper_mode: bool = False) -> bool`
- `scope_decisions(rows: list[dict], show_paper: bool) -> list[dict]`

- [ ] **Step 1: Write failing tests** (`backend/tests/test_kalshi_mode.py`):

```python
from kalshi.mode import is_real_mode, scope_decisions
from kalshi.engine import should_execute


def test_is_real_mode_truth_table():
    assert is_real_mode("demo", False, False) is True        # demo executes freely
    assert is_real_mode("live", True, False) is True          # live + gate on
    assert is_real_mode("live", False, False) is False        # live, gate off
    assert is_real_mode("live", True, True) is False          # paper_mode hard override
    assert is_real_mode("", True, False) is False             # unknown env


def test_is_real_mode_matches_should_execute_contract():
    # mode.is_real_mode MUST never diverge from the real-order gate.
    for env in ("demo", "live", "prod", "", "weird"):
        for le in (True, False):
            for pm in (True, False):
                assert is_real_mode(env, le, pm) == should_execute(env, le, pm)


def test_scope_decisions_paper_vs_real():
    rows = [
        {"id": "a", "paper": True, "decision": "placed"},
        {"id": "b", "decision": "skipped"},              # no flag -> real side
        {"id": "c", "paper": False, "decision": "placed"},
        {"id": "d", "paper": True, "decision": "skipped"},
    ]
    paper = {r["id"] for r in scope_decisions(rows, show_paper=True)}
    real = {r["id"] for r in scope_decisions(rows, show_paper=False)}
    assert paper == {"a", "d"}
    assert real == {"b", "c"}
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: kalshi.mode`).
  Run: `cd backend && python3 -m pytest tests/test_kalshi_mode.py -q`

- [ ] **Step 3: Implement `backend/kalshi/mode.py`:**

```python
"""Pure paper-vs-real mode discriminator + decision-row scoping. No DB, no imports
of the heavy engine module — so the API can import this cheaply on hot read paths.

`is_real_mode` MIRRORS `engine.should_execute` (the real-order gate). The gate is
NOT modified; `test_kalshi_mode.test_is_real_mode_matches_should_execute_contract`
enforces they never diverge.
"""
from __future__ import annotations


def is_real_mode(environment: str, live_enabled: bool, paper_mode: bool = False) -> bool:
    """True iff this instance places REAL orders. Demo executes freely; live requires
    the explicit gate; paper_mode is a HARD override (never real). The single
    discriminator for paper-vs-real scoping across engine, API, and UI."""
    if paper_mode:
        return False
    env = (environment or "").lower()
    if env == "demo":
        return True
    if env in ("live", "prod"):
        return bool(live_enabled)
    return False


def scope_decisions(rows: list[dict], show_paper: bool) -> list[dict]:
    """Keep only rows for the active mode. show_paper=True -> paper rows (paper
    truthy); False -> real rows (paper falsy / absent)."""
    want = bool(show_paper)
    return [r for r in rows if bool(r.get("paper")) == want]
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** `feat(kalshi): pure is_real_mode + scope_decisions mode discriminator`.

---

### Task 2: `prune_finished_decisions` — gate paper-position expiry

**Files:**
- Modify: `backend/kalshi/reconcile.py:183` (`prune_finished_decisions`)
- Modify: `backend/kalshi/db.py:304` (`prune_finished` — thread the param)
- Test: `backend/tests/test_kalshi_reconcile.py` (add a case)

**Interfaces — Produces:** `prune_finished_decisions(rows, open_tickers, now_iso, *, delete_after_hours=3.0, expire_after_hours=12.0, expire_paper=True)`; `db.prune_finished(..., expire_paper=True)`.

- [ ] **Step 1: Failing test** — assert `expire_paper=False` yields an empty `expire` plan but still deletes stale skipped rows. (Read the existing test file first for row-shape fixtures; mirror them. Write a case building a stuck-open placed paper row + a stale skipped row for a finished market, assert `plan["expire"] == []` when `expire_paper=False`, and the skipped row still in `plan["delete"]`.)
- [ ] **Step 2: Run — expect FAIL** (unexpected `expire_paper` kwarg).
- [ ] **Step 3: Implement:** add `expire_paper: bool = True` to `prune_finished_decisions`; when False, force `expire = []` (keep `delete` unchanged). In `db.prune_finished`, add `expire_paper: bool = True` param and pass it through to `prune_finished_decisions`.
- [ ] **Step 4: Run — expect PASS** (`tests/test_kalshi_reconcile.py`).
- [ ] **Step 5: Commit** `feat(kalshi): prune_finished can skip paper-position expiry (expire_paper flag)`.

---

### Task 3: Engine — stamp mode on every decision row + gate paper mutations

**Files:** Modify `backend/kalshi/engine.py` (inside `run_instance` tick loop).

- [ ] `gitnexus_impact({target: "run_instance", direction: "upstream"})` and `gitnexus_impact({target: "mark_paper_positions"})`; report blast radius.
- [ ] **Stamp pregame rows:** in the `for d in decisions:` loop (`~:808`), before the row insert (`~:894`), set `d["paper"] = bool(dry)`. (Supersedes the placed-only `d["paper"] = True` at `:888`, which stays consistent.)
- [ ] **Stamp in-play rows:** before the in-play insert (`~:991`), set `r["paper"] = bool(live_dry)` for every `r` (generalises the placed-only `:989`).
- [ ] **Gate marking:** wrap the `mark_paper_positions` block (`:911-920`) in `if dry:` so the live engine never marks paper positions.
- [ ] **Gate expiry:** change the `prune_finished(...)` call (`:944`) to pass `expire_paper=dry`.
- [ ] **Verify:** `cd backend && python3 -m pytest tests/test_kalshi_engine.py -q` (existing engine tests still green).
- [ ] **Commit** `feat(kalshi): engine stamps mode on all decision rows; live engine leaves paper positions frozen`.

---

### Task 4: API — scope decisions & orders by mode

**Files:** Modify `backend/api/main.py` (`api_kalshi_instance_decisions:4503`, `api_kalshi_instance_orders:4705`; add helper `_kalshi_show_paper`).

- [ ] `gitnexus_impact` on `api_kalshi_instance_decisions` and `api_kalshi_instance_orders`.
- [ ] Add helper near the other kalshi helpers:

```python
def _kalshi_show_paper(conn, instance_row: dict) -> bool:
    """Whether this instance should surface PAPER data (i.e. it is NOT placing real
    orders). Mirrors the engine's dry state."""
    from kalshi.mode import is_real_mode
    cfg = instance_row.get("kalshi_config") or {}
    env = "demo"
    try:
        bk = _r_auth.db("IntelliStock").table("BrokerageAccounts").get(instance_row.get("brokerage_id")).run(conn) or {}
        env = bk.get("kalshi_environment") or "demo"
    except Exception:
        pass
    return not is_real_mode(env, bool(cfg.get("live_enabled")), bool(cfg.get("paper_mode")))
```

- [ ] **Decisions endpoint:** capture `row = _kalshi_instance_row(...)` (currently the return is unused), compute `show_paper = _kalshi_show_paper(conn, row)`, then `from kalshi.mode import scope_decisions; rows = scope_decisions(rows, show_paper)` right after the fetch (`~:4515`) so `out`, `summary`, `count`, and the `paper` block all derive from the scoped set. Return the `paper` block only when `show_paper` (`else None`).
- [ ] **Orders endpoint:** compute `show_paper = _kalshi_show_paper(conn, row)` (row already fetched `:4710`); build `mock` / `mock_history` only when `show_paper` (else leave `mock={}` / `mock_history=[]`). The `info_by_ticker`/`edge_by_ticker` lookups stay (mode-agnostic enrichment).
- [ ] **Test (pure helper already covered in Task 1).** Add/adjust `tests/test_kalshi_api_payloads.py` only if a pure shaper changed (it didn't) — endpoint wiring verified E2E in Task 7.
- [ ] **Verify:** `cd backend && python3 -m pytest tests/test_kalshi_api_payloads.py tests/test_kalshi_mode.py -q`.
- [ ] **Commit** `feat(kalshi): decisions & orders endpoints return only the active mode's data`.

---

### Task 5: Restart engine on paper-mode toggle (+ cancel resting orders on live→paper)

**Files:** Modify `backend/api/main.py:4328` (`api_kalshi_update_instance`); `backend/server.py:1215` (`run_thread_service_change`); add pure helper `kalshi_mode_changed`.

- [ ] `gitnexus_impact` on `api_kalshi_update_instance` and `run_thread_service_change`.
- [ ] **PATCH — cancel resting real orders on a live→paper switch.** After computing `paper`/`live_enabled` and BEFORE writing config, compute prev vs next real-mode from `kalshi.mode.is_real_mode` (env from the brokerage `bk`); if `was_real and not now_real`, best-effort `_kalshi_client_from_row(bk).cancel_all_open_orders()` (wrap in try/except; never block the save).
- [ ] **server.py — restart on mode change.** Add pure helper (top of file or a small util) and unit test:

```python
def kalshi_mode_changed(old_val, new_val) -> bool:
    """True iff a running kalshi instance's paper/live mode flipped between two
    changefeed snapshots (so the container must be recycled to rebuild EngineConfig)."""
    if not old_val or not new_val:
        return False
    if (new_val.get("kind") != "kalshi"):
        return False
    o = old_val.get("kalshi_config") or {}
    n = new_val.get("kalshi_config") or {}
    return (bool(o.get("paper_mode")) != bool(n.get("paper_mode"))
            or bool(o.get("live_enabled")) != bool(n.get("live_enabled")))
```

  In `run_thread_service_change`, add a branch after the start/stop elif: when `run_approval and instance_id in running_threads_objs and kalshi_mode_changed(old_val, new_val)`, recycle the container — `stop_instance_container(instance_id)`, then re-`append`/`thread_count += 1` and `start_instance_container(instance_id)` (mirror the existing start branch's bookkeeping).
- [ ] **Test:** `backend/tests/test_server_kalshi_restart.py` — `kalshi_mode_changed` true on paper flip, false on name-only change / non-kalshi / missing old_val.
- [ ] **Verify:** `cd backend && python3 -m pytest tests/test_server_kalshi_restart.py -q`.
- [ ] **Commit** `feat(kalshi): restart engine on paper-mode toggle; cancel resting orders on live→paper`.

---

### Task 6: Web UI — show only the active mode

**Files:** Modify `frontend/src/views/KalshiInstanceDetailView.vue`, `frontend/src/components/kalshi/KalshiPortfolioChart.vue`.

- [ ] Read both files. Gate on the existing `liveReal` (`KalshiInstanceDetailView.vue:98`):
  - MOCK progress chart (`:345`), MOCK trades summary (`:355`), per-row MOCK badge (`:486`) + per-row paper P&L (`:502`), Mock positions / Mock filled (`:541-590`): render only when `!liveReal`.
  - Pass `:is-real="liveReal"` (or `:paper-mode="!liveReal"`) into `<KalshiPortfolioChart>`; in the component replace `isPaper = paperSeries.length > 0` (`:55`) with the prop, so it shows the real value curve in real mode and the paper P&L curve otherwise.
  - Add real-mode empty states where a paper card is hidden ("No real trades yet").
- [ ] **Verify:** `cd frontend && npm run build` (typecheck/build clean); visual check in Task 7.
- [ ] **Commit** `feat(kalshi-web): hide paper sections in real-money mode, show real equivalents`.

---

### Task 7: End-to-end verification

- [ ] Backend: `cd backend && python3 -m pytest tests/test_kalshi_mode.py tests/test_kalshi_reconcile.py tests/test_kalshi_engine.py tests/test_kalshi_decisions.py tests/test_kalshi_api_payloads.py tests/test_server_kalshi_restart.py -q` — all green.
- [ ] Drive the real detail page: in real mode, MOCK/paper sections gone, real sections present; toggle to paper → engine restarts and paper view restored; toggle back → restarts into real. Confirm a `live` engine log no longer prints `marked N paper position(s)`.
- [ ] `gitnexus_detect_changes()` — confirm only expected symbols/flows changed.
- [ ] Open PR.

## Self-Review (against spec)

- Spec §3 write-path stamp → Task 3. §4.1 engine gating → Tasks 2–3. §4.2 restart + cancel → Task 5. §4.3 API scoping → Tasks 1,4. §4.4 web UI → Task 6. §6 tests → Tasks 1,2,5,7. §4.5 mobile → explicitly deferred (server-side gating in Task 4 covers its decisions/orders data). No placeholders; `is_real_mode`/`scope_decisions`/`show_paper`/`expire_paper`/`kalshi_mode_changed` names consistent across tasks.
