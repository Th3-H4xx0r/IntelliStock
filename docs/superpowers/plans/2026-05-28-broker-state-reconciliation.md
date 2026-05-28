# Broker State Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate contamination of strategy state from prior brokerage orders/positions at live boot, while preserving restart continuity. Strategy-owned positions are identified via LiveOrderWAL provenance; external positions are quarantined; an explicit `initial_value` bypasses broker equity. Backward-compatible behind a `clean_room_mode` flag (default False).

**Architecture:** WAL-anchored reconciliation. The strategy's own LiveOrderWAL rows are the provenance signal — broker positions matched against terminal `filled` WAL rows (by ticker + `client_order_id` prefix) are strategy-owned; everything else is external. The dormant `seed_trades_from_broker` and `initial_value` constructor parameters (already plumbed but never used at the broker.py call site) are activated. A new `LiveBootAudit` table records exactly what was adopted/quarantined per boot.

**Tech Stack:** Python 3.x, pytest, RethinkDB (via rethinkdb-python), existing AlpacaAdapter/RobinhoodAdapter wrappers in `backend/broker_adapters/`. Spec: `docs/superpowers/specs/2026-05-28-broker-state-reconciliation-design.md`.

**Test command (always):**
```
python3 -m pytest backend/tests/ -q \
  --ignore=backend/tests/test_intellistock_logger.py \
  --ignore=backend/tests/test_redact_logger.py
```
Baseline = 21 pre-existing failures. Success = 0 NEW failures.

**Pre-existing rules (from CLAUDE.md and prior handoffs):**
- NEVER stage `AGENTS.md` or `CLAUDE.md` (GitNexus auto-edits).
- Commit footer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
- No backticks in commit body lines (they break the shell).
- Branch: `claude-code-integration` (already on it).

---

## File Structure

**Files to create:**
- `backend/broker_adapters/_classifier.py` — WAL-based broker-position classifier
- `backend/live_boot_audit.py` — LiveBootAudit table helper
- `scripts/inspect_broker_state.py` — read-only pre-flight tool
- `scripts/migrate_external_position.py` — operator action to adopt external
- `backend/tests/test_broker_classifier.py` — classifier unit tests
- `backend/tests/test_clean_room_adapter_init.py` — adapter __init__ behavior in clean_room_mode
- `backend/tests/test_clean_room_scenarios.py` — end-to-end behavior tests (4 scenarios)
- `backend/tests/test_live_boot_audit.py` — audit table helper

**Files to modify:**
- `backend/broker_adapters/_wal.py` — add `list_terminal_rows()` query method (small additive change)
- `backend/broker_adapters/base.py` — add `_external_positions` attribute + `get_external_positions()` ABC method (default implementations)
- `backend/broker_adapters/alpaca.py` — add `clean_room_mode` constructor branch
- `backend/broker_adapters/robinhood.py` — add `clean_room_mode` constructor branch
- `backend/broker_adapters/factory.py` — thread `clean_room_mode` through
- `backend/broker.py` — read Instances row fields, pass to factory, write LiveBootAudit, alert on external
- `scripts/clear_main_instance_lookback_state.py` — add `LiveBootAudit` to per-instance clear targets

Existing files we **must not break**:
- `backend/strategies/graph_nexus_analysis.py` — strategy code that reads `_positions`/`_trades`/`_initial_value`. Reads only; no signature changes.
- Existing tests in `backend/tests/` — full baseline must hold.

---

## Task 1: Add `list_terminal_rows` to WALStore

Goal: a single query method that returns this-instance's filled/partial WAL rows since a cutoff, used by the classifier.

**Files:**
- Modify: `backend/broker_adapters/_wal.py`
- Test: `backend/tests/test_broker_classifier.py` (create)

- [ ] **Step 1: Read existing `_wal.py` to understand current shape**

Run:
```
grep -n "class WAL\|def \|_status\|client_order_id\|filled_at" backend/broker_adapters/_wal.py
```
Expected: see the WALStore class signature and existing methods. Note the row-dict structure and the table name.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_broker_classifier.py`:
```python
"""Unit tests for WAL-based broker position classifier and the
list_terminal_rows query method on WALStore."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest


class _FakeWAL:
    """Lightweight stand-in for WALStore.list_terminal_rows used by
    classifier tests so we don't need a live RethinkDB."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def list_terminal_rows(
        self,
        client_order_id_prefix: str,
        statuses: tuple[str, ...] = ("filled",),
        since_utc: datetime | None = None,
    ) -> list[dict]:
        out = []
        for r in self._rows:
            if not r["client_order_id"].startswith(client_order_id_prefix):
                continue
            if r["status"] not in statuses:
                continue
            if since_utc is not None and r["filled_at_utc"] < since_utc:
                continue
            out.append(r)
        return out


def test_fake_wal_filters_by_prefix_and_status_and_time():
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    rows = [
        {"client_order_id": "main-abc-1", "status": "filled", "filled_at_utc": now - timedelta(days=1), "ticker": "TSLA", "side": "BUY", "filled_qty": 10.0, "filled_avg_price": 200.0, "broker_order_id": "bx-1"},
        {"client_order_id": "other-xyz", "status": "filled", "filled_at_utc": now - timedelta(days=1), "ticker": "AAPL", "side": "BUY", "filled_qty": 5.0, "filled_avg_price": 180.0, "broker_order_id": "bx-2"},
        {"client_order_id": "main-abc-2", "status": "canceled", "filled_at_utc": now - timedelta(days=2), "ticker": "TSLA", "side": "BUY", "filled_qty": 0.0, "filled_avg_price": None, "broker_order_id": "bx-3"},
        {"client_order_id": "main-abc-3", "status": "filled", "filled_at_utc": now - timedelta(days=400), "ticker": "TSLA", "side": "BUY", "filled_qty": 7.0, "filled_avg_price": 100.0, "broker_order_id": "bx-4"},
    ]
    wal = _FakeWAL(rows)
    out = wal.list_terminal_rows(
        client_order_id_prefix="main-",
        statuses=("filled", "partial"),
        since_utc=now - timedelta(days=180),
    )
    assert len(out) == 1
    assert out[0]["broker_order_id"] == "bx-1"
```

This first test just locks in the FakeWAL contract that subsequent classifier tests will use. The real WALStore.list_terminal_rows is added next.

- [ ] **Step 3: Run test to verify it passes (no real code yet, just the fake)**

Run:
```
python3 -m pytest backend/tests/test_broker_classifier.py::test_fake_wal_filters_by_prefix_and_status_and_time -v
```
Expected: PASS

- [ ] **Step 4: Write the test for real WALStore.list_terminal_rows**

Append to `backend/tests/test_broker_classifier.py`:
```python
def test_walstore_list_terminal_rows_signature_exists():
    """Smoke test: WALStore exposes list_terminal_rows with the expected signature."""
    from backend.broker_adapters._wal import WALStore
    assert hasattr(WALStore, "list_terminal_rows"), \
        "WALStore.list_terminal_rows is required by the broker-state classifier"
    # Don't connect to RethinkDB in unit tests; we only check the method shape.
    import inspect
    sig = inspect.signature(WALStore.list_terminal_rows)
    params = list(sig.parameters)
    assert "client_order_id_prefix" in params
    assert "statuses" in params
    assert "since_utc" in params
```

- [ ] **Step 5: Run test to verify it fails (method not yet implemented)**

Run:
```
python3 -m pytest backend/tests/test_broker_classifier.py::test_walstore_list_terminal_rows_signature_exists -v
```
Expected: FAIL with `AssertionError: WALStore.list_terminal_rows is required` or `AttributeError`.

- [ ] **Step 6: Implement WALStore.list_terminal_rows**

Open `backend/broker_adapters/_wal.py`. Read the WALStore class, locate the existing query methods (look for `r.table` calls), and add a sibling method matching that pattern. Add this method to `WALStore`:

```python
def list_terminal_rows(
    self,
    client_order_id_prefix: str,
    statuses: tuple[str, ...] = ("filled",),
    since_utc=None,
) -> list[dict]:
    """Return WAL rows whose client_order_id starts with the given prefix,
    whose status is in ``statuses``, and (optionally) whose filled_at_utc
    is >= ``since_utc``. Used by the broker-state classifier to determine
    which broker positions are strategy-owned.

    NOTE: callers are expected to filter further in Python — RethinkDB-side
    we keep the query simple so this is safe to call on cold-start with no
    secondary index. If the WAL grows beyond ~100k rows we should add an
    index on client_order_id.
    """
    import datetime as _dt
    from rethinkdb import RethinkDB

    r = RethinkDB()
    conn = self._get_conn()  # reuse existing connector — adjust attribute name if needed
    try:
        cur = (
            r.table(self._table_name)
            .filter(lambda row: row["client_order_id"].match(f"^{client_order_id_prefix}"))
            .run(conn)
        )
        rows = list(cur)
    finally:
        # Match the existing close-on-error pattern in this file
        pass
    out: list[dict] = []
    for row in rows:
        if row.get("status") not in statuses:
            continue
        ts = row.get("filled_at_utc") or row.get("submitted_at_utc")
        if since_utc is not None and ts is not None:
            # ts may be a rethinkdb-aware datetime; both compare correctly
            if isinstance(ts, str):
                ts = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if ts < since_utc:
                continue
        out.append(row)
    return out
```

If the existing WALStore stores rows under a different attribute name (e.g. `self._table` instead of `self._table_name`, or uses `self.conn` vs `self._get_conn()`), adjust to match the established pattern in that file.

- [ ] **Step 7: Run signature test**

Run:
```
python3 -m pytest backend/tests/test_broker_classifier.py::test_walstore_list_terminal_rows_signature_exists -v
```
Expected: PASS

- [ ] **Step 8: Commit**

```
git add backend/broker_adapters/_wal.py backend/tests/test_broker_classifier.py
git commit -m "feat(broker): add WALStore.list_terminal_rows for clean-room classifier

Adds a single query method to WALStore that returns filled/partial WAL
rows matching a client_order_id prefix within a retention window. Used by
the upcoming broker-state classifier to determine which broker positions
are strategy-owned vs externally placed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: BrokerAdapter ABC adds `_external_positions` + `get_external_positions()`

Goal: a default-empty quarantine dict on the ABC so adapters that don't override still satisfy the contract.

**Files:**
- Modify: `backend/broker_adapters/base.py`
- Test: append to `backend/tests/test_broker_classifier.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_broker_classifier.py`:
```python
def test_base_adapter_has_external_positions_default():
    """The ABC must declare _external_positions and a default
    get_external_positions() returning {}."""
    from backend.broker_adapters.base import BrokerAdapter
    # _external_positions is a class-level declared attribute; default {}
    assert hasattr(BrokerAdapter, "get_external_positions"), \
        "BrokerAdapter must expose get_external_positions()"
    # default impl shape:
    import inspect
    method = inspect.getattr_static(BrokerAdapter, "get_external_positions")
    # Either a regular method or an abstractmethod is fine; not None.
    assert method is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```
python3 -m pytest backend/tests/test_broker_classifier.py::test_base_adapter_has_external_positions_default -v
```
Expected: FAIL with `AssertionError: BrokerAdapter must expose get_external_positions()`.

- [ ] **Step 3: Add the attribute + default method to the ABC**

In `backend/broker_adapters/base.py`, locate the `class BrokerAdapter(ABC):` block (around line 70-81 where `_positions`, `_trades`, etc. are declared). Add:

```python
    _external_positions: dict[str, dict]
```

next to the other underscore attributes. Then near the end of the class (or wherever class methods sit), add a NON-abstract default:

```python
    # --- External-position quarantine (clean-room mode) ---
    # Adapters that don't classify (i.e. clean_room_mode=False) leave this
    # dict empty. In clean-room mode, the adapter populates it with
    # {ticker: {qty, market_value, note, first_seen_utc}} for any broker
    # position that lacked WAL provenance. The strategy NEVER reads this
    # dict; it's for operator visibility (audit log + Discord alert).
    def get_external_positions(self) -> dict[str, dict]:
        return getattr(self, "_external_positions", {}) or {}
```

Note: this is intentionally a regular method (not abstract) so existing adapters work unchanged. Adapters that want to surface external positions just populate `self._external_positions` and the default getter returns it.

- [ ] **Step 4: Run test to verify it passes**

Run:
```
python3 -m pytest backend/tests/test_broker_classifier.py::test_base_adapter_has_external_positions_default -v
```
Expected: PASS

- [ ] **Step 5: Run full broker_adapters tests to confirm no regression**

Run:
```
python3 -m pytest backend/tests/test_broker_classifier.py backend/tests/test_robinhood_adapter_smoke.py -q 2>&1 | tail -20
```
Expected: existing tests still pass; the 1 new test passes.

- [ ] **Step 6: Commit**

```
git add backend/broker_adapters/base.py backend/tests/test_broker_classifier.py
git commit -m "feat(broker): add _external_positions + get_external_positions to BrokerAdapter ABC

Adds the quarantine dict attribute and a non-abstract default getter.
Existing adapters work unchanged; clean-room mode will populate the dict
with broker positions that lacked WAL provenance.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Position classifier — core matching + external

Goal: a pure-Python function that partitions broker positions into strategy-owned vs external based on a WAL row source. Stateless, testable in isolation.

**Files:**
- Create: `backend/broker_adapters/_classifier.py`
- Test: append to `backend/tests/test_broker_classifier.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_broker_classifier.py`:
```python
def _pos(symbol: str, qty: float, mv: float = 0.0):
    """Helper to build a PositionDTO-shaped object for tests."""
    from backend.broker_adapters.base import PositionDTO
    return PositionDTO(symbol=symbol, qty=qty, avg_entry_price=0.0, market_value=mv)


def test_classifier_strategy_owned_match():
    """Broker has TSLA 10sh; WAL has filled BUY 10sh with matching prefix → strategy-owned."""
    from backend.broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    wal = _FakeWAL([
        {"client_order_id": "intellistock-main-20260520-abc", "status": "filled",
         "filled_at_utc": now - timedelta(days=8), "ticker": "TSLA", "side": "BUY",
         "filled_qty": 10.0, "filled_avg_price": 200.0, "broker_order_id": "bx-1"},
    ])
    positions = [_pos("TSLA", 10.0, mv=2200.0)]
    owned, external, trades = classify_broker_positions(
        positions=positions, wal=wal,
        instance_id="main",
        cid_prefix="intellistock-main-",
        retention_days=180,
        now_utc=now,
    )
    assert owned == {"TSLA": 10.0}
    assert external == {}
    assert len(trades) == 1
    assert trades[0]["ticker"] == "TSLA" and trades[0]["action"] == "BUY"


def test_classifier_external_no_wal_match():
    """Broker has AAPL 5sh; WAL has no rows → external."""
    from backend.broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    wal = _FakeWAL([])
    positions = [_pos("AAPL", 5.0, mv=920.0)]
    owned, external, trades = classify_broker_positions(
        positions=positions, wal=wal,
        instance_id="main",
        cid_prefix="intellistock-main-",
        retention_days=180,
        now_utc=now,
    )
    assert owned == {}
    assert "AAPL" in external
    assert external["AAPL"]["qty"] == 5.0
    assert "no WAL trace" in external["AAPL"]["note"]
    assert trades == []


def test_classifier_cross_instance_cid_is_external():
    """Broker has TSLA 10sh; WAL has matching ticker but different instance's prefix → external."""
    from backend.broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    wal = _FakeWAL([
        {"client_order_id": "intellistock-OTHER-20260520-xyz", "status": "filled",
         "filled_at_utc": now - timedelta(days=8), "ticker": "TSLA", "side": "BUY",
         "filled_qty": 10.0, "filled_avg_price": 200.0, "broker_order_id": "bx-99"},
    ])
    positions = [_pos("TSLA", 10.0, mv=2200.0)]
    owned, external, trades = classify_broker_positions(
        positions=positions, wal=wal,
        instance_id="main",
        cid_prefix="intellistock-main-",
        retention_days=180,
        now_utc=now,
    )
    assert owned == {}
    assert "TSLA" in external
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
python3 -m pytest backend/tests/test_broker_classifier.py -v -k "classifier" 2>&1 | tail -30
```
Expected: 3 failures with `ModuleNotFoundError: No module named 'backend.broker_adapters._classifier'`.

- [ ] **Step 3: Implement the classifier**

Create `backend/broker_adapters/_classifier.py`:
```python
"""WAL-based broker-position classifier.

Used at adapter ``__init__`` when ``clean_room_mode=True`` to partition the
positions reported by the brokerage account into:

- ``strategy_owned``: positions whose most recent FILLED BUY in this
  instance's LiveOrderWAL accounts for the current broker qty
- ``external``: positions with no WAL provenance (manually placed, or
  pre-dating the WAL retention window)

Also reconstructs the strategy's ``_trades`` list from the same WAL rows so
that V32 risk-exit logic (trailing stop, fast-loser, days-held) sees the
correct entry timestamps on restart.

The function is pure (no I/O, no mutation of inputs). Tests call it with a
stub WAL; production calls it with the real ``WALStore``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Protocol

# Tolerance for qty matching when classifying. We use the LARGER of an
# absolute float-epsilon and 1% of broker qty so fractional-share rounding
# doesn't tip a fully-owned position into "partial external".
_QTY_ABS_TOL = 1e-6
_QTY_REL_TOL = 0.01


class _WALLike(Protocol):
    """Structural type for WAL sources accepted by the classifier."""

    def list_terminal_rows(
        self,
        client_order_id_prefix: str,
        statuses: tuple[str, ...] = ("filled",),
        since_utc: Optional[datetime] = None,
    ) -> list[dict]: ...


def classify_broker_positions(
    positions: list[Any],
    wal: _WALLike,
    instance_id: str,
    cid_prefix: str,
    retention_days: int = 180,
    now_utc: Optional[datetime] = None,
) -> tuple[dict[str, float], dict[str, dict], list[dict]]:
    """Partition broker positions into (strategy_owned, external) + reconstruct _trades.

    Args:
        positions: list of PositionDTO-shaped objects (must expose .symbol,
                   .qty, .market_value)
        wal:       WAL source supporting list_terminal_rows
        instance_id: this instance's id (used for audit notes only)
        cid_prefix:  client_order_id prefix that identifies orders from
                     THIS instance (e.g. "intellistock-main-")
        retention_days: how far back to walk the WAL
        now_utc: optional injection for tests; defaults to UTC now

    Returns:
        owned:    {ticker: qty} for strategy-owned positions
        external: {ticker: {qty, market_value, note, first_seen_utc}}
        trades:   list of dicts shaped like ``_trades`` entries
                  (ticker, action, shares, price, timestamp, ...)

    Edge cases:
        - WAL implies LESS than broker qty: partial-external split.
        - WAL implies MORE than broker qty (e.g. manual sell during downtime):
          we trust broker (authoritative); strategy_owned = broker qty.
          The discrepancy is implicit; caller can compare WAL-implied vs
          broker qty via the returned trades.
        - Same-day BUY + SELL netting to zero: the position is closed, so
          no broker row will appear; nothing classified.
    """
    now = now_utc or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)

    # Pull this-instance's filled/partial rows within retention
    rows = wal.list_terminal_rows(
        client_order_id_prefix=cid_prefix,
        statuses=("filled", "partial"),
        since_utc=cutoff,
    )

    # Build per-ticker net qty from chronologically-sorted fills
    def _ts(row: dict) -> datetime:
        ts = row.get("filled_at_utc") or row.get("submitted_at_utc")
        if isinstance(ts, str):
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return ts or now

    rows = sorted(rows, key=_ts)
    net_qty_by_ticker: dict[str, float] = {}
    trades: list[dict] = []

    for row in rows:
        side = (row.get("side") or "").upper()
        qty = float(row.get("filled_qty") or 0.0)
        if qty <= 0 or side not in ("BUY", "SELL"):
            continue
        delta = qty if side == "BUY" else -qty
        net_qty_by_ticker[row["ticker"]] = net_qty_by_ticker.get(row["ticker"], 0.0) + delta

        trades.append({
            "ticker": row["ticker"],
            "action": side,
            "shares": qty,
            "price": float(row.get("filled_avg_price") or 0.0),
            "timestamp": _ts(row),
            "client_order_id": row.get("client_order_id"),
            "broker_order_id": row.get("broker_order_id"),
            "source": "wal",
        })

    # Classify each broker position
    owned: dict[str, float] = {}
    external: dict[str, dict] = {}

    for pos in positions:
        ticker = getattr(pos, "symbol", None) or pos["symbol"]
        broker_qty = float(getattr(pos, "qty", None) if hasattr(pos, "qty") else pos["qty"])
        mv = float(getattr(pos, "market_value", 0.0) or 0.0)
        wal_qty = max(0.0, net_qty_by_ticker.get(ticker, 0.0))

        tol = max(_QTY_ABS_TOL, _QTY_REL_TOL * broker_qty)

        if wal_qty <= _QTY_ABS_TOL:
            # No WAL trace -> fully external
            external[ticker] = {
                "qty": broker_qty,
                "market_value": mv,
                "note": "no WAL trace within retention window",
                "first_seen_utc": now.isoformat(),
            }
        elif abs(wal_qty - broker_qty) <= tol:
            # Match within tolerance -> fully strategy-owned
            owned[ticker] = broker_qty
        elif wal_qty < broker_qty:
            # WAL implies less than broker shows -> partial external split
            owned[ticker] = wal_qty
            excess = broker_qty - wal_qty
            external[ticker] = {
                "qty": excess,
                "market_value": mv * (excess / broker_qty) if broker_qty > 0 else 0.0,
                "note": f"partial external: WAL implies {wal_qty:.4f}sh, broker shows {broker_qty:.4f}sh",
                "first_seen_utc": now.isoformat(),
            }
        else:
            # WAL implies more than broker (e.g. manual sell during downtime).
            # Broker is authoritative; trim our view to broker reality.
            owned[ticker] = broker_qty

    return owned, external, trades
```

- [ ] **Step 4: Run the 3 classifier tests; verify pass**

Run:
```
python3 -m pytest backend/tests/test_broker_classifier.py -v -k "classifier" 2>&1 | tail -15
```
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```
git add backend/broker_adapters/_classifier.py backend/tests/test_broker_classifier.py
git commit -m "feat(broker): WAL-based position classifier (core matching + external)

Pure-Python classifier that partitions broker positions into strategy-owned
(matched against this-instance WAL filled rows) vs external (no provenance).
Also reconstructs _trades from the matched WAL rows for restart continuity.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Classifier edge cases — partial-external, retention, manual sell

Goal: cover the three remaining classifier branches with tests, plus harden against malformed WAL rows.

**Files:**
- Test: append to `backend/tests/test_broker_classifier.py`
- Modify: `backend/broker_adapters/_classifier.py` (only if a test reveals a bug)

- [ ] **Step 1: Write the 3 edge-case tests**

Append to `backend/tests/test_broker_classifier.py`:
```python
def test_classifier_partial_external_split():
    """Broker shows 50sh; WAL implies only 30sh (10 BUY + 20 BUY) → 30 owned + 20 external."""
    from backend.broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    wal = _FakeWAL([
        {"client_order_id": "intellistock-main-20260501-a", "status": "filled",
         "filled_at_utc": now - timedelta(days=27), "ticker": "NVDA", "side": "BUY",
         "filled_qty": 10.0, "filled_avg_price": 180.0, "broker_order_id": "bx-1"},
        {"client_order_id": "intellistock-main-20260510-b", "status": "filled",
         "filled_at_utc": now - timedelta(days=18), "ticker": "NVDA", "side": "BUY",
         "filled_qty": 20.0, "filled_avg_price": 200.0, "broker_order_id": "bx-2"},
    ])
    positions = [_pos("NVDA", 50.0, mv=10000.0)]
    owned, external, _trades = classify_broker_positions(
        positions=positions, wal=wal,
        instance_id="main",
        cid_prefix="intellistock-main-",
        now_utc=now,
    )
    assert owned == {"NVDA": 30.0}
    assert "NVDA" in external
    assert external["NVDA"]["qty"] == 20.0
    assert "partial external" in external["NVDA"]["note"]


def test_classifier_retention_window_excludes_old_buys():
    """A BUY older than retention_days does NOT count toward strategy ownership."""
    from backend.broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    wal = _FakeWAL([
        {"client_order_id": "intellistock-main-20250101-a", "status": "filled",
         "filled_at_utc": now - timedelta(days=400), "ticker": "AAPL", "side": "BUY",
         "filled_qty": 5.0, "filled_avg_price": 180.0, "broker_order_id": "bx-1"},
    ])
    positions = [_pos("AAPL", 5.0, mv=920.0)]
    owned, external, _trades = classify_broker_positions(
        positions=positions, wal=wal,
        instance_id="main",
        cid_prefix="intellistock-main-",
        retention_days=180,
        now_utc=now,
    )
    # The old BUY is outside the retention window → external
    assert owned == {}
    assert "AAPL" in external


def test_classifier_manual_sell_during_downtime_trusts_broker():
    """WAL says 10sh held; broker shows 0sh (operator manually sold). owned reflects broker."""
    from backend.broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    wal = _FakeWAL([
        {"client_order_id": "intellistock-main-a", "status": "filled",
         "filled_at_utc": now - timedelta(days=5), "ticker": "MSFT", "side": "BUY",
         "filled_qty": 10.0, "filled_avg_price": 400.0, "broker_order_id": "bx-1"},
    ])
    # Broker shows ZERO MSFT despite the WAL buy → operator manually sold
    positions = [_pos("AAPL", 5.0, mv=920.0)]  # different ticker; MSFT just absent
    owned, external, _trades = classify_broker_positions(
        positions=positions, wal=wal,
        instance_id="main",
        cid_prefix="intellistock-main-",
        now_utc=now,
    )
    # AAPL has no WAL trace → external
    # MSFT is gone entirely (not even in positions) → not classified at all
    assert owned == {}
    assert "AAPL" in external
    assert "MSFT" not in owned and "MSFT" not in external


def test_classifier_handles_malformed_wal_row():
    """A WAL row with missing/None qty does not crash the classifier."""
    from backend.broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    wal = _FakeWAL([
        {"client_order_id": "intellistock-main-a", "status": "filled",
         "filled_at_utc": now - timedelta(days=1), "ticker": "TSLA", "side": "BUY",
         "filled_qty": None, "filled_avg_price": 200.0, "broker_order_id": "bx-bad"},
        {"client_order_id": "intellistock-main-b", "status": "filled",
         "filled_at_utc": now - timedelta(days=1), "ticker": "TSLA", "side": "BUY",
         "filled_qty": 10.0, "filled_avg_price": 200.0, "broker_order_id": "bx-1"},
    ])
    positions = [_pos("TSLA", 10.0, mv=2200.0)]
    owned, _external, _trades = classify_broker_positions(
        positions=positions, wal=wal,
        instance_id="main",
        cid_prefix="intellistock-main-",
        now_utc=now,
    )
    assert owned == {"TSLA": 10.0}
```

- [ ] **Step 2: Run tests**

Run:
```
python3 -m pytest backend/tests/test_broker_classifier.py -v 2>&1 | tail -20
```
Expected: ALL tests in the file pass (the 3 new edge cases + the malformed test + the previous 4 from Tasks 1-3 = 8 total).

If any of the 4 new edge cases FAIL, inspect the classifier output and fix the implementation. The most likely fix points:

- partial-external arithmetic uses `_QTY_REL_TOL = 0.01` of broker_qty as tolerance, so for `broker_qty=50, wal_qty=30`, the gap is 20 vs tol 0.5 — clearly partial. Should work first try.
- malformed row should be skipped by the `if qty <= 0` guard.

- [ ] **Step 3: Commit (regardless of whether impl was touched)**

```
git add -u backend/broker_adapters/_classifier.py backend/tests/test_broker_classifier.py
git commit -m "test(broker): edge cases for classifier — partial, retention, manual-sell, malformed

Covers: partial-external split when broker qty > WAL qty; retention window
exclusion; manual-sell-during-downtime (broker authoritative); robustness
to malformed WAL rows.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: AlpacaAdapter — plumb `clean_room_mode` (default off, behavior unchanged)

Goal: add the parameter to the constructor, default False, do nothing yet. Establish that the legacy path is unchanged.

**Files:**
- Modify: `backend/broker_adapters/alpaca.py`
- Test: `backend/tests/test_clean_room_adapter_init.py` (create)

- [ ] **Step 1: Write the failing legacy-behavior-preserved test**

Create `backend/tests/test_clean_room_adapter_init.py`:
```python
"""Tests for clean_room_mode behavior in AlpacaAdapter / RobinhoodAdapter
``__init__``. Uses mocked broker clients so no live broker call is made.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


def _stub_alpaca_client_with(positions=None, cash=10000.0, equity=10000.0):
    """Build a fake Alpaca TradingClient that satisfies the calls
    AlpacaAdapter.__init__ makes."""
    client = MagicMock()
    client.get_account.return_value = MagicMock(
        cash=str(cash), buying_power=str(cash), daytrading_buying_power=str(cash),
        equity=str(equity), last_equity=str(equity),
        pattern_day_trader=False, daytrade_count=0, account_blocked=False,
        trading_blocked=False,
    )
    client.get_all_positions.return_value = positions or []
    client.get_orders.return_value = []
    return client


def test_alpaca_clean_room_mode_param_accepted():
    """AlpacaAdapter.__init__ accepts clean_room_mode without error."""
    import inspect
    from backend.broker_adapters.alpaca import AlpacaAdapter

    sig = inspect.signature(AlpacaAdapter.__init__)
    assert "clean_room_mode" in sig.parameters, \
        "AlpacaAdapter.__init__ must accept clean_room_mode"
    # Default must be False so existing callers don't see a behavior change.
    assert sig.parameters["clean_room_mode"].default is False
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```
python3 -m pytest backend/tests/test_clean_room_adapter_init.py::test_alpaca_clean_room_mode_param_accepted -v
```
Expected: FAIL with `AssertionError`.

- [ ] **Step 3: Add the parameter to AlpacaAdapter.__init__**

Open `backend/broker_adapters/alpaca.py`. Locate the `__init__` signature (around line 129-180). Add the new keyword-only parameter (after the existing parameters, before the body):

```python
    def __init__(
        self,
        # ... existing parameters unchanged ...
        seed_trades_from_broker: bool = True,
        initial_value: Optional[float] = None,
        # NEW:
        clean_room_mode: bool = False,
        # NEW: WAL injected by factory in clean_room_mode for classifier
        wal_store: Optional[Any] = None,  # may already be present — keep one
        cid_prefix: Optional[str] = None,
        clean_room_retention_days: int = 180,
    ) -> None:
        # ... preserve existing body unchanged ...
        self._clean_room_mode = bool(clean_room_mode)
        self._cid_prefix = cid_prefix or ""
        self._clean_room_retention_days = int(clean_room_retention_days)
        self._external_positions = {}  # populated only if clean_room_mode is True
        # ... rest of existing init unchanged ...
```

Inspect the file for the existing `wal_store` parameter (Task 1 mentioned it; the existing adapter already accepts it). If it's there, do NOT duplicate; just add the three new params (`clean_room_mode`, `cid_prefix`, `clean_room_retention_days`).

- [ ] **Step 4: Run test to verify pass**

Run:
```
python3 -m pytest backend/tests/test_clean_room_adapter_init.py::test_alpaca_clean_room_mode_param_accepted -v
```
Expected: PASS.

- [ ] **Step 5: Run existing Alpaca smoke tests to confirm no regression**

Run:
```
python3 -m pytest backend/tests/ -q -k "alpaca" 2>&1 | tail -20
```
Expected: existing alpaca tests pass; no new failures.

- [ ] **Step 6: Commit**

```
git add backend/broker_adapters/alpaca.py backend/tests/test_clean_room_adapter_init.py
git commit -m "feat(broker): AlpacaAdapter accepts clean_room_mode (no behavior change yet)

Adds the constructor parameter with default False. Legacy callers see no
behavior change; the next task wires the actual classifier path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: AlpacaAdapter — wire clean_room_mode behavior

Goal: when `clean_room_mode=True`, the adapter uses the classifier instead of blindly adopting broker state. `_initial_value` is resolved from explicit param (no fallback to broker equity).

**Files:**
- Modify: `backend/broker_adapters/alpaca.py`
- Test: append to `backend/tests/test_clean_room_adapter_init.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_clean_room_adapter_init.py`:
```python
def test_alpaca_clean_room_classifies_strategy_owned():
    """With clean_room_mode=True, _positions reflects only WAL-matched broker positions."""
    from backend.broker_adapters.alpaca import AlpacaAdapter
    from backend.broker_adapters.base import PositionDTO

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    # Broker reports 2 positions: TSLA (in WAL) and AAPL (not in WAL)
    broker_positions = [
        MagicMock(symbol="TSLA", qty="10.0", market_value="2200.0",
                  avg_entry_price="200.0", unrealized_pl="0.0", current_price="220.0"),
        MagicMock(symbol="AAPL", qty="5.0", market_value="920.0",
                  avg_entry_price="180.0", unrealized_pl="0.0", current_price="184.0"),
    ]
    fake_client = _stub_alpaca_client_with(positions=broker_positions, cash=10000.0, equity=12000.0)

    # Fake WAL with one filled BUY for TSLA carrying the correct prefix
    class _StubWAL:
        def list_terminal_rows(self, client_order_id_prefix, statuses=("filled",), since_utc=None):
            return [{
                "client_order_id": "intellistock-main-20260520-abc",
                "status": "filled",
                "filled_at_utc": now - timedelta(days=8),
                "ticker": "TSLA", "side": "BUY",
                "filled_qty": 10.0, "filled_avg_price": 200.0,
                "broker_order_id": "bx-1",
            }] if client_order_id_prefix == "intellistock-main-" else []
        def insert(self, *a, **kw): pass  # other methods stubbed if hit
        def mark_filled(self, *a, **kw): pass

    with patch("backend.broker_adapters.alpaca.TradingClient", return_value=fake_client):
        adapter = AlpacaAdapter(
            api_key="k", api_secret="s", paper=True,
            instance_id="main",
            wal_store=_StubWAL(),
            seed_trades_from_broker=False,  # important: don't pull broker history in clean_room
            initial_value=10000.0,
            clean_room_mode=True,
            cid_prefix="intellistock-main-",
        )

    # TSLA is strategy-owned; AAPL is quarantined
    assert adapter._positions == {"TSLA": 10.0}
    assert "AAPL" in adapter._external_positions
    assert adapter._external_positions["AAPL"]["qty"] == 5.0
    # _initial_value comes from explicit param, NOT broker equity
    assert adapter._initial_value == 10000.0
    # _trades reconstructed from WAL only
    assert len(adapter._trades) == 1
    assert adapter._trades[0]["ticker"] == "TSLA"
    assert adapter._trades[0]["source"] == "wal"


def test_alpaca_clean_room_requires_explicit_initial_value():
    """clean_room_mode=True without initial_value AND without snapshot/instance
    field falls through to broker equity (legacy) — or raises if no broker."""
    from backend.broker_adapters.alpaca import AlpacaAdapter
    from backend.broker_adapters.errors import BrokerError

    fake_client = _stub_alpaca_client_with(positions=[], cash=0.0, equity=0.0)
    class _EmptyWAL:
        def list_terminal_rows(self, **kw): return []

    with patch("backend.broker_adapters.alpaca.TradingClient", return_value=fake_client):
        with pytest.raises(BrokerError):
            AlpacaAdapter(
                api_key="k", api_secret="s", paper=True,
                instance_id="main",
                wal_store=_EmptyWAL(),
                seed_trades_from_broker=False,
                # NO initial_value passed; broker equity is 0 → BrokerError
                clean_room_mode=True,
                cid_prefix="intellistock-main-",
            )
```

- [ ] **Step 2: Run the new tests; verify they fail**

Run:
```
python3 -m pytest backend/tests/test_clean_room_adapter_init.py -v -k "clean_room_classifies or clean_room_requires" 2>&1 | tail -20
```
Expected: both FAIL (positions still contain AAPL, _initial_value is broker equity, etc.).

- [ ] **Step 3: Add the clean_room branch to AlpacaAdapter.__init__**

In `backend/broker_adapters/alpaca.py`, locate the `refresh_positions()` call inside `__init__` (around line 251). The current sequence is:

```python
        self.refresh_cash()
        self.refresh_positions()
        if seed_trades_from_broker:
            self._seed_trades_from_broker(limit=500)
        # ...
        if initial_value is not None:
            self._initial_value = float(initial_value)
        else:
            try:
                acct_dto = self.refresh_account()
                self._initial_value = float(acct_dto.equity)
            except Exception:
                self._initial_value = self._cash + self.get_positions_value({})
```

Replace with a clean-room aware version:

```python
        self.refresh_cash()
        self.refresh_positions()  # populates self._positions with the FULL broker set
        if self._clean_room_mode:
            # Replace self._positions with the classifier's strategy-owned subset.
            # The full set we just pulled is fed into classify_broker_positions.
            from ._classifier import classify_broker_positions
            broker_positions = []
            for sym, qty in (self._positions or {}).items():
                # Build a PositionDTO-shaped object so classifier can read it
                broker_positions.append(PositionDTO(
                    symbol=sym, qty=float(qty), avg_entry_price=0.0,
                    market_value=float(self._last_prices.get(sym, 0.0) or 0.0) * float(qty),
                ))
            wal = wal_store if wal_store is not None else getattr(self, "_wal", None)
            if wal is None:
                raise BrokerError(
                    "AlpacaAdapter clean_room_mode=True requires wal_store; "
                    "no WAL was provided to the constructor."
                )
            owned, external, wal_trades = classify_broker_positions(
                positions=broker_positions,
                wal=wal,
                instance_id=instance_id or "",
                cid_prefix=self._cid_prefix,
                retention_days=self._clean_room_retention_days,
            )
            self._positions = dict(owned)
            self._external_positions = dict(external)
            self._trades = list(wal_trades)
            # initial_value: explicit param wins; otherwise we MUST have one
            if initial_value is None:
                raise BrokerError(
                    "AlpacaAdapter clean_room_mode=True requires an explicit "
                    "initial_value (configure Instances.<id>.initial_value or "
                    "pass LIVE_INITIAL_VALUE env)."
                )
            self._initial_value = float(initial_value)
        else:
            if seed_trades_from_broker:
                try:
                    self._seed_trades_from_broker(limit=500)
                except Exception:
                    pass
            if initial_value is not None:
                self._initial_value = float(initial_value)
            else:
                try:
                    acct_dto = self.refresh_account()
                    self._initial_value = float(acct_dto.equity)
                except Exception:
                    self._initial_value = self._cash + self.get_positions_value({})

        if self._initial_value <= 0:
            raise BrokerError(
                "AlpacaAdapter init: account equity <= 0. "
                "Check credentials and that the account is funded."
            )
```

The classifier import is local-to-method so the dependency stays explicit and import-cycle-safe.

- [ ] **Step 4: Run the new tests; verify pass**

Run:
```
python3 -m pytest backend/tests/test_clean_room_adapter_init.py -v 2>&1 | tail -20
```
Expected: all pass (3 in this file now).

- [ ] **Step 5: Run full pytest sweep to confirm baseline**

Run:
```
python3 -m pytest backend/tests/ -q --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py 2>&1 | tail -25
```
Expected: 21 pre-existing failures, 0 NEW failures.

- [ ] **Step 6: Commit**

```
git add backend/broker_adapters/alpaca.py backend/tests/test_clean_room_adapter_init.py
git commit -m "feat(broker): AlpacaAdapter clean_room_mode wiring with WAL classifier

When clean_room_mode=True, refresh_positions still pulls all broker positions
but the classifier partitions them into strategy-owned (_positions) and
external (_external_positions) based on this-instance WAL provenance.
_trades is rebuilt from WAL filled rows. _initial_value MUST be explicit;
broker equity is no longer the silent fallback.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: RobinhoodAdapter — mirror Task 5+6

Goal: same parameter + same classifier branch for RobinhoodAdapter. Robinhood is what the `main` instance uses, so this is the path that actually fires in production.

**Files:**
- Modify: `backend/broker_adapters/robinhood.py`
- Test: append to `backend/tests/test_clean_room_adapter_init.py`

- [ ] **Step 1: Write parallel tests for RobinhoodAdapter**

Append to `backend/tests/test_clean_room_adapter_init.py`:
```python
def _stub_rh_client_with(positions=None, cash=10000.0, equity=10000.0):
    """Build a fake RobinhoodClient that satisfies RobinhoodAdapter init calls."""
    client = MagicMock()
    client.get_account_summary.return_value = {
        "buying_power": str(cash),
        "cash": str(cash),
        "settled_funds": str(cash),
        "equity": str(equity),
        "last_equity": str(equity),
        "pattern_day_trader": False,
        "daytrade_count": 0,
        "account_blocked": False,
        "trading_blocked": False,
    }
    client.get_positions.return_value = positions or []
    client.list_orders.return_value = []
    return client


def test_robinhood_clean_room_mode_param_accepted():
    import inspect
    from backend.broker_adapters.robinhood import RobinhoodAdapter

    sig = inspect.signature(RobinhoodAdapter.__init__)
    assert "clean_room_mode" in sig.parameters
    assert sig.parameters["clean_room_mode"].default is False


def test_robinhood_clean_room_classifies_strategy_owned():
    """RH adapter quarantines un-matched broker positions and trusts the classifier."""
    from backend.broker_adapters.robinhood import RobinhoodAdapter

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    broker_positions = [
        {"symbol": "TSLA", "quantity": "10.0", "average_buy_price": "200.0",
         "instrument": "https://api.robinhood.com/instruments/x1/"},
        {"symbol": "AAPL", "quantity": "5.0", "average_buy_price": "180.0",
         "instrument": "https://api.robinhood.com/instruments/x2/"},
    ]
    fake_client = _stub_rh_client_with(positions=broker_positions, cash=10000.0, equity=12000.0)

    class _StubWAL:
        def list_terminal_rows(self, client_order_id_prefix, statuses=("filled",), since_utc=None):
            return [{
                "client_order_id": "intellistock-main-20260520-abc",
                "status": "filled",
                "filled_at_utc": now - timedelta(days=8),
                "ticker": "TSLA", "side": "BUY",
                "filled_qty": 10.0, "filled_avg_price": 200.0,
                "broker_order_id": "rh-1",
            }] if client_order_id_prefix == "intellistock-main-" else []

    with patch("backend.broker_adapters.robinhood.RobinhoodClient", return_value=fake_client):
        adapter = RobinhoodAdapter(
            api_key="k", api_secret="s",
            instance_id="main",
            wal_store=_StubWAL(),
            account_number="<redacted>",
            device_token="dt",
            seed_trades_from_broker=False,
            initial_value=10000.0,
            clean_room_mode=True,
            cid_prefix="intellistock-main-",
        )

    assert adapter._positions == {"TSLA": 10.0}
    assert "AAPL" in adapter._external_positions
    assert adapter._initial_value == 10000.0
    assert len(adapter._trades) == 1
    assert adapter._trades[0]["source"] == "wal"
```

If the RobinhoodClient constructor takes extra required args in your codebase (re-link tokens, brokerage_id), thread them through using existing patterns in `robinhood.py:159-330`.

- [ ] **Step 2: Run the new tests; verify they fail**

Run:
```
python3 -m pytest backend/tests/test_clean_room_adapter_init.py -v -k "robinhood" 2>&1 | tail -20
```
Expected: FAIL.

- [ ] **Step 3: Add the param + clean_room branch to RobinhoodAdapter**

Mirror the Alpaca changes from Tasks 5 and 6 in `backend/broker_adapters/robinhood.py`:

1. Add the three new parameters to `__init__` signature with defaults: `clean_room_mode: bool = False`, `cid_prefix: Optional[str] = None`, `clean_room_retention_days: int = 180`.
2. Initialize `self._clean_room_mode`, `self._cid_prefix`, `self._clean_room_retention_days`, and `self._external_positions = {}` early in `__init__`.
3. Locate the existing block around line 425-450 that calls `refresh_cash()`, `refresh_positions(force=True)`, optionally `_seed_trades_from_broker(limit=200)`, and sets `_initial_value`. Replace with the same conditional shape as in Alpaca Task 6 — branch on `self._clean_room_mode`.

The classifier call is identical to Alpaca's (it's broker-agnostic by design); only the surrounding code is Robinhood-specific.

- [ ] **Step 4: Run new tests; verify pass**

Run:
```
python3 -m pytest backend/tests/test_clean_room_adapter_init.py -v 2>&1 | tail -20
```
Expected: all 5 pass.

- [ ] **Step 5: Run full pytest sweep**

Run:
```
python3 -m pytest backend/tests/ -q --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py 2>&1 | tail -10
```
Expected: 21 pre-existing failures, 0 NEW.

- [ ] **Step 6: Commit**

```
git add backend/broker_adapters/robinhood.py backend/tests/test_clean_room_adapter_init.py
git commit -m "feat(broker): RobinhoodAdapter clean_room_mode wiring (mirrors Alpaca)

Same classifier branch as AlpacaAdapter. main instance uses RH so this is
the production path. _trades rebuilt from WAL; _external_positions
populated; _initial_value requires explicit value.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Factory wiring

Goal: `build_adapter` accepts the new parameters and threads them to both adapters.

**Files:**
- Modify: `backend/broker_adapters/factory.py`
- Test: append to `backend/tests/test_clean_room_adapter_init.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_clean_room_adapter_init.py`:
```python
def test_factory_threads_clean_room_params():
    """build_adapter accepts clean_room_mode + cid_prefix + clean_room_retention_days
    and passes them through to the adapter."""
    import inspect
    from backend.broker_adapters import factory

    sig = inspect.signature(factory.build_adapter)
    for required in ("clean_room_mode", "cid_prefix"):
        assert required in sig.parameters, f"build_adapter missing {required}"
```

- [ ] **Step 2: Run test; verify fail**

Run:
```
python3 -m pytest backend/tests/test_clean_room_adapter_init.py::test_factory_threads_clean_room_params -v
```
Expected: FAIL.

- [ ] **Step 3: Modify the factory**

Open `backend/broker_adapters/factory.py`. In the `build_adapter` function, add the new keyword-only params alongside the existing ones, and thread to both adapter constructors:

```python
def build_adapter(
    *,
    broker_type: str,
    # ... existing params ...
    initial_value: Optional[float] = None,
    seed_trades_from_broker: bool = True,
    # NEW
    clean_room_mode: bool = False,
    cid_prefix: Optional[str] = None,
    clean_room_retention_days: int = 180,
    # ... rest unchanged ...
):
    # ... existing dispatch unchanged, but pass through the new params:
    if broker_type == "alpaca":
        return AlpacaAdapter(
            # ... existing ...
            initial_value=initial_value,
            seed_trades_from_broker=seed_trades_from_broker,
            clean_room_mode=clean_room_mode,
            cid_prefix=cid_prefix,
            clean_room_retention_days=clean_room_retention_days,
        )
    elif broker_type == "robinhood":
        return RobinhoodAdapter(
            # ... existing ...
            initial_value=initial_value,
            seed_trades_from_broker=seed_trades_from_broker,
            clean_room_mode=clean_room_mode,
            cid_prefix=cid_prefix,
            clean_room_retention_days=clean_room_retention_days,
        )
```

- [ ] **Step 4: Run test; verify pass**

Run:
```
python3 -m pytest backend/tests/test_clean_room_adapter_init.py::test_factory_threads_clean_room_params -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```
git add backend/broker_adapters/factory.py backend/tests/test_clean_room_adapter_init.py
git commit -m "feat(broker): factory.build_adapter threads clean_room_mode params

Adds clean_room_mode, cid_prefix, clean_room_retention_days to the factory
signature and passes them through to AlpacaAdapter / RobinhoodAdapter.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: LiveBootAudit helper

Goal: a small module that writes a per-boot audit row to a new `LiveBootAudit` table.

**Files:**
- Create: `backend/live_boot_audit.py`
- Test: `backend/tests/test_live_boot_audit.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_live_boot_audit.py`:
```python
"""Tests for the LiveBootAudit helper."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock


def test_build_audit_row_minimal_fields():
    from backend.live_boot_audit import build_audit_row

    row = build_audit_row(
        instance_id="main",
        broker_type="robinhood",
        mode="clean_room",
        broker_cash_at_boot=9876.54,
        broker_positions_total=3,
        strategy_owned={"TSLA": 10.0},
        external={"AAPL": {"qty": 5.0, "market_value": 920.0, "note": "no WAL trace"}},
        initial_value=10000.0,
        initial_value_source="explicit",
        snapshot_loaded=True,
        snapshot_keys=42,
        trades_seeded=1,
        trades_seeded_source="wal",
        notes=["external positions detected: 1 ticker"],
    )

    assert row["instance_id"] == "main"
    assert row["broker_type"] == "robinhood"
    assert row["mode"] == "clean_room"
    assert row["strategy_owned_count"] == 1
    assert row["external_count"] == 1
    assert row["strategy_owned_tickers"] == ["TSLA"]
    assert "AAPL" in row["external_tickers_qty"]
    assert row["initial_value"] == 10000.0
    assert row["initial_value_source"] == "explicit"
    assert isinstance(row["id"], str) and row["id"].startswith("main|")


def test_persist_audit_row_calls_table_insert():
    """persist_audit_row uses rethinkdb to insert into LiveBootAudit."""
    from backend.live_boot_audit import persist_audit_row

    fake_r = MagicMock()
    fake_conn = MagicMock()
    fake_table = MagicMock()
    fake_r.db.return_value.table.return_value = fake_table
    fake_table.insert.return_value.run.return_value = {"inserted": 1}

    row = {"id": "main|2026-05-28T00:00:00Z", "instance_id": "main"}
    result = persist_audit_row(r=fake_r, conn=fake_conn, row=row, db_name="IntelliStock")

    fake_r.db.assert_called_once_with("IntelliStock")
    fake_r.db.return_value.table.assert_called_once_with("LiveBootAudit")
    fake_table.insert.assert_called_once()
    assert result["inserted"] == 1
```

- [ ] **Step 2: Run tests; verify fail**

Run:
```
python3 -m pytest backend/tests/test_live_boot_audit.py -v
```
Expected: both FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the helper**

Create `backend/live_boot_audit.py`:
```python
"""LiveBootAudit table helper.

A small, focused module that builds and persists one audit row per live
broker boot. Captures exactly what the adapter adopted (strategy-owned)
and quarantined (external) so an operator can forensically reconstruct
"what did this instance start with at this boot".

Table schema is created lazily on first write — no migration required.

Indexed on instance_id and boot_at_utc.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

_DB_NAME = "IntelliStock"
_TABLE = "LiveBootAudit"


def build_audit_row(
    *,
    instance_id: str,
    broker_type: str,
    mode: str,                        # "clean_room" | "legacy"
    broker_cash_at_boot: float,
    broker_positions_total: int,
    strategy_owned: dict[str, float],
    external: dict[str, dict],
    initial_value: float,
    initial_value_source: str,         # "explicit" | "instance_row" | "snapshot" | "broker_equity"
    snapshot_loaded: bool,
    snapshot_keys: int,
    trades_seeded: int,
    trades_seeded_source: str,         # "wal" | "broker_history" | "none"
    notes: Optional[list[str]] = None,
    boot_at_utc: Optional[datetime] = None,
) -> dict:
    """Construct a LiveBootAudit row dict. Does not write to the DB."""
    ts = boot_at_utc or datetime.now(timezone.utc)
    return {
        "id": f"{instance_id}|{ts.isoformat()}",
        "instance_id": instance_id,
        "boot_at_utc": ts.isoformat(),
        "broker_type": broker_type,
        "mode": mode,
        "broker_cash_at_boot": float(broker_cash_at_boot),
        "broker_positions_total": int(broker_positions_total),
        "strategy_owned_count": len(strategy_owned),
        "strategy_owned_tickers": sorted(strategy_owned.keys()),
        "external_count": len(external),
        "external_tickers_qty": {k: v.get("qty", 0.0) for k, v in external.items()},
        "external_detail": external,   # full dict for forensics
        "initial_value": float(initial_value),
        "initial_value_source": initial_value_source,
        "snapshot_loaded": bool(snapshot_loaded),
        "snapshot_keys": int(snapshot_keys),
        "trades_seeded": int(trades_seeded),
        "trades_seeded_source": trades_seeded_source,
        "notes": list(notes or []),
    }


def persist_audit_row(*, r: Any, conn: Any, row: dict, db_name: str = _DB_NAME) -> dict:
    """Insert the audit row into the LiveBootAudit table. Returns the rethinkdb result.

    Auto-creates the table if missing (idempotent). Caller supplies the
    rethinkdb instance + open connection so this stays unit-testable.
    """
    db = r.db(db_name)
    try:
        existing = list(db.table_list().run(conn))
        if _TABLE not in existing:
            db.table_create(_TABLE, primary_key="id").run(conn)
            # Add useful indices
            db.table(_TABLE).index_create("instance_id").run(conn)
            db.table(_TABLE).index_create("boot_at_utc").run(conn)
            db.table(_TABLE).index_wait("instance_id", "boot_at_utc").run(conn)
    except Exception:
        # Index creation is best-effort; don't crash adapter boot if it fails.
        pass
    return db.table(_TABLE).insert(row).run(conn)
```

- [ ] **Step 4: Run tests; verify pass**

Run:
```
python3 -m pytest backend/tests/test_live_boot_audit.py -v
```
Expected: both PASS.

- [ ] **Step 5: Commit**

```
git add backend/live_boot_audit.py backend/tests/test_live_boot_audit.py
git commit -m "feat(broker): LiveBootAudit table helper

Builds and persists a forensic row per broker boot recording what was
adopted as strategy-owned vs quarantined as external, plus initial_value
source. Auto-creates the table on first write.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: broker.py wiring — read Instances row, pass to factory, write audit

Goal: at boot in `broker.py`, read `Instances.<id>.clean_room_mode` and `Instances.<id>.initial_value` (plus env-var overrides), pass them to the factory, write the LiveBootAudit row, and alert on non-empty external.

**Files:**
- Modify: `backend/broker.py`
- Test: smoke only (broker.py is not import-safe due to argparse-at-load); behavior verified via scenario tests in Task 12.

- [ ] **Step 1: Read the relevant broker.py section**

Run:
```
sed -n '5180,5320p' backend/broker.py
```
Locate the area where `_build_adapter(...)` is called (around line 5259-5276). Note the surrounding helper functions used to read `Instances` row fields.

- [ ] **Step 2: Add clean-room resolution helper**

Just above the `_build_adapter` call (around line 5258), insert:

```python
        # --- Clean-room mode resolution ---------------------------------
        # Sources, in precedence order:
        #   1. env LIVE_CLEAN_ROOM_MODE (set on the broker host)
        #   2. Instances.<id>.clean_room_mode (per-instance DB field)
        #   3. default False (backward-compatible legacy behavior)
        _env_cr = os.environ.get("LIVE_CLEAN_ROOM_MODE", "").strip().lower()
        if _env_cr in ("1", "true", "yes", "on"):
            _clean_room_mode = True
        elif _env_cr in ("0", "false", "no", "off"):
            _clean_room_mode = False
        else:
            _clean_room_mode = bool(_instance_row.get("clean_room_mode", False))

        _env_iv = os.environ.get("LIVE_INITIAL_VALUE", "").strip()
        if _env_iv:
            try:
                _initial_value = float(_env_iv)
            except ValueError:
                _initial_value = None
        else:
            _initial_value = _instance_row.get("initial_value", None)
            if _initial_value is not None:
                try:
                    _initial_value = float(_initial_value)
                except (TypeError, ValueError):
                    _initial_value = None

        _cid_prefix = f"intellistock-{instance_id}-"
        _clean_room_retention_days = int(os.environ.get("LIVE_CLEAN_ROOM_WAL_RETENTION_DAYS", "180") or 180)
        if _clean_room_mode:
            _log(
                f"[live_boot] CLEAN_ROOM_MODE=true (instance={instance_id}, "
                f"initial_value={_initial_value}, retention_days={_clean_room_retention_days})",
                "yellow",
            )
```

Then change the `_build_adapter` call (~line 5259):

```python
        try:
            live_adapter = _build_adapter(
                broker_type=live_broker_type,
                api_key=key,
                api_secret=secret,
                paper=live_broker_paper,
                instance_id=str(instance_id),
                wal_store=live_wal,
                account_number=_rh_account_number,
                device_token=_rh_device_token,
                rh_obtained_at_epoch=_rh_obtained_at,
                rh_expires_in=_rh_expires_in,
                rh_account_url=_rh_account_url,
                rh_brokerage_id=live_brokerage_id,
                # NEW
                clean_room_mode=_clean_room_mode,
                cid_prefix=_cid_prefix,
                clean_room_retention_days=_clean_room_retention_days,
                initial_value=_initial_value,
                # In clean_room_mode we forbid the bulk broker-history seed
                seed_trades_from_broker=(not _clean_room_mode),
            )
            live_adapter.start_trade_updates()
```

- [ ] **Step 3: Write the LiveBootAudit row after adapter ready**

Locate the post-WAL-reconcile / pre-tick-loop section (around `broker.py:5648` where the "Live broker adapter ready" line is logged). Just AFTER that log line, insert:

```python
        # --- LiveBootAudit row ----------------------------------------------------
        try:
            from live_boot_audit import build_audit_row, persist_audit_row
            _ext = getattr(live_adapter, "_external_positions", {}) or {}
            _audit_row = build_audit_row(
                instance_id=str(instance_id),
                broker_type=live_broker_type,
                mode=("clean_room" if _clean_room_mode else "legacy"),
                broker_cash_at_boot=float(getattr(live_adapter, "_cash", 0.0) or 0.0),
                broker_positions_total=len(getattr(live_adapter, "_positions", {}) or {}) + len(_ext),
                strategy_owned=dict(getattr(live_adapter, "_positions", {}) or {}),
                external=_ext,
                initial_value=float(getattr(live_adapter, "_initial_value", 0.0) or 0.0),
                initial_value_source=("explicit" if _initial_value is not None
                                      else ("snapshot" if _clean_room_mode else "broker_equity")),
                snapshot_loaded=bool(globals().get("_strategy_cache_loaded_from_db", False)),
                snapshot_keys=len(_nexus_cache or {}),
                trades_seeded=len(getattr(live_adapter, "_trades", []) or []),
                trades_seeded_source=("wal" if _clean_room_mode else ("broker_history" if (not _clean_room_mode) else "none")),
                notes=[],
            )
            _audit_conn = get_conn_retry(max_attempts=3, delay=2)
            if _audit_conn is not None:
                try:
                    persist_audit_row(r=r, conn=_audit_conn, row=_audit_row)
                except Exception as _e:
                    _log(f"[live_boot] audit write failed (non-fatal): {_e}", "yellow")
                finally:
                    try:
                        _audit_conn.close()
                    except Exception:
                        pass
        except Exception as _e:
            _log(f"[live_boot] audit module load failed (non-fatal): {_e}", "yellow")

        # --- External-positions alert --------------------------------------------
        _ext_now = getattr(live_adapter, "_external_positions", {}) or {}
        if _ext_now:
            _ext_summary = ", ".join(
                f"{t}={v.get('qty', 0):.4f}sh" for t, v in sorted(_ext_now.items())
            )
            _log(
                f"[live_boot] EXTERNAL positions quarantined (not managed by strategy): {_ext_summary}",
                "yellow",
            )
            try:
                _alert_strategy_error(
                    instance_id=str(instance_id),
                    tag="external_positions_detected",
                    message=f"External (non-strategy) positions found at boot: {_ext_summary}. "
                            "Strategy will NOT sell these. Use scripts/migrate_external_position.py "
                            "to adopt or scripts/inspect_broker_state.py for details.",
                )
            except Exception:
                pass
```

- [ ] **Step 4: Confirm broker.py still imports / lints**

Run:
```
python3 -c "import ast; ast.parse(open('backend/broker.py').read())"
```
Expected: no output (clean parse).

- [ ] **Step 5: Run full pytest sweep**

Run:
```
python3 -m pytest backend/tests/ -q --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py 2>&1 | tail -10
```
Expected: 21 pre-existing failures, 0 NEW. broker.py is not loaded by tests, so this validates we didn't break anything sibling.

- [ ] **Step 6: Commit**

```
git add backend/broker.py
git commit -m "feat(broker): wire clean_room_mode + initial_value + LiveBootAudit into boot

Reads LIVE_CLEAN_ROOM_MODE env and Instances.<id>.clean_room_mode field;
threads clean_room_mode + cid_prefix + initial_value to the adapter factory.
Writes a LiveBootAudit row after adapter is ready. Fires Discord alert via
_alert_strategy_error when external positions are detected.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: `scripts/inspect_broker_state.py` — read-only pre-flight

Goal: an operator script that prints what the adapter WOULD adopt and quarantine, WITHOUT actually booting the broker daemon.

**Files:**
- Create: `scripts/inspect_broker_state.py`
- Test: `backend/tests/test_inspect_broker_state_smoke.py` (create — argparse smoke only)

- [ ] **Step 1: Write the smoke test**

Create `backend/tests/test_inspect_broker_state_smoke.py`:
```python
"""Smoke test: scripts/inspect_broker_state.py is importable and exposes
the expected CLI entry."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def test_inspect_broker_state_imports():
    path = Path(__file__).resolve().parents[2] / "scripts" / "inspect_broker_state.py"
    spec = importlib.util.spec_from_file_location("inspect_broker_state", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    # Just ensure the module loads (no DB connect at import time)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    assert hasattr(module, "main")
    assert callable(module.main)
```

- [ ] **Step 2: Run test; verify fail**

Run:
```
python3 -m pytest backend/tests/test_inspect_broker_state_smoke.py -v
```
Expected: FAIL.

- [ ] **Step 3: Write the script**

Create `scripts/inspect_broker_state.py`:
```python
"""Read-only pre-flight inspector for a live trading instance.

Connects to RethinkDB + the brokerage account and prints what an adapter
boot under clean_room_mode WOULD adopt (strategy-owned) and quarantine
(external). Does NOT boot the broker daemon, does NOT submit orders, does
NOT mutate any DB state.

Usage:
  python3 scripts/inspect_broker_state.py --instance main
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone


def _connect_db():
    from rethinkdb import RethinkDB
    r = RethinkDB()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    conn = r.connect(host=host, port=port, db="IntelliStock")
    return r, conn


def _resolve_brokerage(r, conn, instance_id: str):
    inst = r.table("Instances").get(instance_id).run(conn)
    if inst is None:
        return None, None
    brokerage_id = inst.get("brokerage_id")
    if not brokerage_id:
        return inst, None
    bra = r.table("BrokerageAccounts").get(brokerage_id).run(conn)
    return inst, bra


def _list_wal_rows_for_instance(r, conn, instance_id: str, retention_days: int):
    """Naive scan of LiveOrderWAL filtered to this instance's CID prefix.
    For a few thousand rows this is fine; if WAL grows we'll add an index."""
    cid_prefix = f"intellistock-{instance_id}-"
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    cur = r.table("LiveOrderWAL").run(conn)
    rows = []
    for row in cur:
        cid = row.get("client_order_id") or ""
        if not cid.startswith(cid_prefix):
            continue
        ts = row.get("filled_at_utc") or row.get("submitted_at_utc")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                ts = None
        if ts is not None and ts < cutoff:
            continue
        rows.append(row)
    return rows, cid_prefix


def _fetch_broker_positions_and_cash(brokerage_row: dict):
    """Read-only broker call: positions + cash + open orders. Uses the
    existing adapter modules, NOT the full daemon boot path."""
    btype = (brokerage_row.get("brokerage_type") or "").lower()
    if btype == "alpaca":
        from alpaca.trading.client import TradingClient
        client = TradingClient(
            api_key=brokerage_row.get("alpaca_key"),
            secret_key=brokerage_row.get("alpaca_secret"),
            paper=bool(brokerage_row.get("alpaca_paper", True)),
        )
        positions = client.get_all_positions() or []
        account = client.get_account()
        cash = float(getattr(account, "cash", 0.0) or 0.0)
        return [
            {"symbol": p.symbol, "qty": float(p.qty), "market_value": float(p.market_value or 0.0)}
            for p in positions
        ], cash
    elif btype == "robinhood":
        # Lazy import so this script works even when the RH module has issues.
        try:
            from backend.robinhood_engine import RobinhoodClient
        except ImportError:
            from robinhood_engine import RobinhoodClient
        # Re-link tokens to client from DB
        # (Mirror the pattern in backend/broker.py _load_robinhood_extras_from_db.)
        client = RobinhoodClient.from_brokerage_row(brokerage_row)  # adjust if API differs
        positions = client.get_positions(account_number=brokerage_row.get("robinhood_account_number")) or []
        account = client.get_account_summary(account_number=brokerage_row.get("robinhood_account_number")) or {}
        cash = float(account.get("cash", 0.0) or 0.0)
        return [
            {"symbol": p.get("symbol"), "qty": float(p.get("quantity", 0.0)),
             "market_value": float(p.get("quantity", 0.0)) * float(p.get("average_buy_price", 0.0) or 0.0)}
            for p in positions
        ], cash
    else:
        raise SystemExit(f"unknown brokerage_type {btype!r} on this instance")


def main():
    p = argparse.ArgumentParser(description="Read-only pre-flight inspector for live instances")
    p.add_argument("--instance", required=True, help="Instances row id (e.g. main)")
    p.add_argument("--retention-days", type=int, default=180)
    args = p.parse_args()

    r, conn = _connect_db()
    try:
        inst, bra = _resolve_brokerage(r, conn, args.instance)
        if inst is None:
            print(f"ERROR: Instances row '{args.instance}' not found", file=sys.stderr)
            return 2
        if bra is None:
            print(f"ERROR: brokerage_id missing on Instances row {args.instance}", file=sys.stderr)
            return 2

        print(f"Instance: {args.instance}  (broker: {bra.get('brokerage_type')})")
        print(f"Account:  {bra.get('alpaca_account_number') or bra.get('robinhood_account_number')}  "
              f"({bra.get('account_name')})")
        if bra.get("brokerage_type") == "alpaca":
            print(f"Paper:    {bra.get('alpaca_paper')}")

        wal_rows, cid_prefix = _list_wal_rows_for_instance(r, conn, args.instance, args.retention_days)
        print(f"WAL rows (last {args.retention_days}d, prefix {cid_prefix!r}): {len(wal_rows)}")

        positions, cash = _fetch_broker_positions_and_cash(bra)
        print(f"Broker cash:     ${cash:,.2f}")
        print(f"Broker positions: {len(positions)} total")

        # Run the classifier
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
        from broker_adapters._classifier import classify_broker_positions

        class _StubWAL:
            def __init__(self, rows): self._rows = rows
            def list_terminal_rows(self, **kw):
                out = []
                for row in self._rows:
                    if row.get("status") not in kw.get("statuses", ("filled",)):
                        continue
                    out.append(row)
                return out

        # Adapt position shape for the classifier (it accepts dict OR PositionDTO)
        owned, external, trades = classify_broker_positions(
            positions=[type("P", (), pos) for pos in positions],
            wal=_StubWAL(wal_rows),
            instance_id=args.instance,
            cid_prefix=cid_prefix,
            retention_days=args.retention_days,
        )

        for sym, qty in sorted(owned.items()):
            print(f"  - {sym:6s}  {qty:>10.4f}sh  STRATEGY-OWNED  (matched WAL)")
        for sym, info in sorted(external.items()):
            print(f"  - {sym:6s}  {info['qty']:>10.4f}sh  EXTERNAL        ({info.get('note', '')})")

        print()
        print(f"Verdict: under clean_room_mode=True, the strategy would adopt {len(owned)} "
              f"position(s) and quarantine {len(external)}. Manually flatten externals via "
              f"the broker UI if you want them gone before boot.")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run smoke test; verify pass**

Run:
```
python3 -m pytest backend/tests/test_inspect_broker_state_smoke.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```
git add scripts/inspect_broker_state.py backend/tests/test_inspect_broker_state_smoke.py
git commit -m "feat(broker): scripts/inspect_broker_state.py read-only pre-flight inspector

Operator tool that connects to RethinkDB + brokerage account and prints
which positions would be adopted as strategy-owned vs quarantined as
external under clean_room_mode. Does not boot the daemon or mutate state.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: `scripts/migrate_external_position.py` — adopt external

Goal: an operator script that writes a synthetic WAL row (status=`filled`, source=`migrated`) so a specific ticker's broker position becomes strategy-owned on next refresh.

**Files:**
- Create: `scripts/migrate_external_position.py`
- Test: `backend/tests/test_migrate_external_position_smoke.py`

- [ ] **Step 1: Smoke test**

Create `backend/tests/test_migrate_external_position_smoke.py`:
```python
"""Smoke test: scripts/migrate_external_position.py imports cleanly."""
import importlib.util
from pathlib import Path


def test_migrate_script_imports():
    path = Path(__file__).resolve().parents[2] / "scripts" / "migrate_external_position.py"
    spec = importlib.util.spec_from_file_location("migrate_external_position", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    assert hasattr(module, "main")
    assert callable(module.main)
```

- [ ] **Step 2: Run; verify fail**

Run:
```
python3 -m pytest backend/tests/test_migrate_external_position_smoke.py -v
```
Expected: FAIL.

- [ ] **Step 3: Write the script**

Create `scripts/migrate_external_position.py`:
```python
"""Operator action: adopt an external broker position as strategy-owned by
writing a synthetic WAL row.

WHY THIS IS NEEDED: Approach A treats any broker position without a WAL
provenance trail as EXTERNAL (quarantined; strategy won't sell). If you
have a legitimate pre-existing position you want the strategy to manage,
this script writes a synthetic WAL row with status=filled, source=migrated,
so the next refresh classifies it as strategy-owned with the entry price
you specify.

Usage:
  python3 scripts/migrate_external_position.py \
    --instance main --ticker AAPL --qty 50.0 --avg-price 184.70
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timezone


def _connect_db():
    from rethinkdb import RethinkDB
    r = RethinkDB()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    return r, r.connect(host=host, port=port, db="IntelliStock")


def main():
    p = argparse.ArgumentParser(description="Adopt a broker position as strategy-owned")
    p.add_argument("--instance", required=True)
    p.add_argument("--ticker", required=True)
    p.add_argument("--qty", type=float, required=True)
    p.add_argument("--avg-price", type=float, required=True)
    p.add_argument("--apply", action="store_true", help="actually write (default: dry-run)")
    args = p.parse_args()

    cid = f"intellistock-{args.instance}-MIGRATED-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    row = {
        "client_order_id": cid,
        "broker_order_id": f"MIGRATED-{uuid.uuid4().hex[:12]}",
        "status": "filled",
        "ticker": args.ticker.upper(),
        "side": "BUY",
        "filled_qty": float(args.qty),
        "filled_avg_price": float(args.avg_price),
        "filled_at_utc": now.isoformat(),
        "submitted_at_utc": now.isoformat(),
        "source": "migrated",
        "instance_id": args.instance,  # advisory field (WAL is globally scoped)
        "note": "Synthetic WAL row written by migrate_external_position.py "
                "to adopt a pre-existing broker position into strategy management.",
    }

    print("Will insert into LiveOrderWAL:")
    for k, v in sorted(row.items()):
        print(f"  {k} = {v!r}")
    if not args.apply:
        print("\nDRY RUN. Re-run with --apply to write.")
        return 0

    r, conn = _connect_db()
    try:
        res = r.table("LiveOrderWAL").insert(row).run(conn)
        print(f"\nInserted: {res}")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run smoke test; verify pass**

Run:
```
python3 -m pytest backend/tests/test_migrate_external_position_smoke.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```
git add scripts/migrate_external_position.py backend/tests/test_migrate_external_position_smoke.py
git commit -m "feat(broker): scripts/migrate_external_position.py — adopt external

Writes a synthetic LiveOrderWAL row (status=filled, source=migrated) so a
pre-existing broker position is recognized as strategy-owned by the
classifier on next refresh. Dry-run by default.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Extend per-instance clear script with LiveBootAudit

Goal: when an operator runs the per-instance clean-up script, the new audit table is also pruned (per-instance scoped).

**Files:**
- Modify: `scripts/clear_main_instance_lookback_state.py`

- [ ] **Step 1: Open the targets list (`_build_targets`)**

Open `scripts/clear_main_instance_lookback_state.py`. Locate the `_build_targets` function (around line 52).

- [ ] **Step 2: Append the new table to the targets list**

Inside the returned list, append (between the `GraphNexusAnalystPanel` entry and the function's closing `]`):

```python
        # LiveBootAudit: per-instance boot audit rows (id pattern "<instance>|<ts>")
        ("LiveBootAudit", [
            ("instance_id", instance_id, "exact"),
            ("id", f"{instance_id}|", "prefix"),
        ]),
```

- [ ] **Step 3: Also update the docstring**

In the module docstring at top, add to the "What this SCRIPT CLEARS" list:

```
- LiveBootAudit: per-instance boot audit rows
```

- [ ] **Step 4: Run the existing dry-run smoke (no DB changes)**

Run:
```
python3 -c "import ast; ast.parse(open('scripts/clear_main_instance_lookback_state.py').read())"
```
Expected: clean parse.

- [ ] **Step 5: Commit**

```
git add scripts/clear_main_instance_lookback_state.py
git commit -m "chore(broker): include LiveBootAudit in per-instance clear script

The new audit table is per-instance scoped (id pattern <instance>|<ts>);
add it to the standard pre-launch cleanup target list.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: Scenario behavior tests

Goal: four high-value end-to-end behavior tests that validate the design against the four scenarios from the spec.

**Files:**
- Create: `backend/tests/test_clean_room_scenarios.py`

- [ ] **Step 1: Write the four scenario tests**

Create `backend/tests/test_clean_room_scenarios.py`:
```python
"""End-to-end behavior tests for clean_room_mode covering the four scenarios
from the design spec (docs/superpowers/specs/2026-05-28-broker-state-reconciliation-design.md):

  1. First-ever live boot, broker contains prior bad-test positions.
  2. Strategy bought TSLA yesterday; daemon restart 5 seconds later.
  3. Strategy holds TSLA in WAL; broker shows 0 (operator manually sold).
  4. Partial-external: broker shows 50sh; WAL implies 30sh.

Tests are full-stack at the classifier+adapter boundary; they use mock
broker clients but the real classifier code path.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


_NOW = datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc)


def _stub_alpaca_client(positions, cash=10000.0, equity=10000.0):
    c = MagicMock()
    c.get_account.return_value = MagicMock(
        cash=str(cash), buying_power=str(cash), daytrading_buying_power=str(cash),
        equity=str(equity), last_equity=str(equity), pattern_day_trader=False,
        daytrade_count=0, account_blocked=False, trading_blocked=False,
    )
    c.get_all_positions.return_value = positions
    c.get_orders.return_value = []
    return c


def _pos(symbol, qty, mv):
    return MagicMock(symbol=symbol, qty=str(qty), market_value=str(mv),
                     avg_entry_price="0.0", unrealized_pl="0.0", current_price=str(mv/qty if qty else 0))


class _WAL:
    def __init__(self, rows): self.rows = rows
    def list_terminal_rows(self, client_order_id_prefix, statuses=("filled",), since_utc=None):
        out = []
        for r in self.rows:
            if not r["client_order_id"].startswith(client_order_id_prefix):
                continue
            if r["status"] not in statuses:
                continue
            ts = r["filled_at_utc"]
            if since_utc and ts < since_utc:
                continue
            out.append(r)
        return out


def test_scenario_1_first_boot_with_contaminated_broker():
    """Bad-test orders exist in the broker; WAL is empty. ALL broker positions
    must be quarantined; strategy starts with empty _positions."""
    from backend.broker_adapters.alpaca import AlpacaAdapter

    broker_positions = [
        _pos("TSLA", 12.0, 2640.0),
        _pos("NVDA", 8.0, 1920.0),
        _pos("AAPL", 50.0, 9200.0),
    ]
    with patch("backend.broker_adapters.alpaca.TradingClient",
               return_value=_stub_alpaca_client(broker_positions)):
        adapter = AlpacaAdapter(
            api_key="k", api_secret="s", paper=True, instance_id="main",
            wal_store=_WAL([]),
            seed_trades_from_broker=False,
            initial_value=10000.0,
            clean_room_mode=True,
            cid_prefix="intellistock-main-",
        )
    assert adapter._positions == {}, \
        "no broker positions should be adopted when WAL is empty"
    assert set(adapter._external_positions.keys()) == {"TSLA", "NVDA", "AAPL"}, \
        "all 3 contaminating positions must be quarantined"
    assert adapter._initial_value == 10000.0
    assert adapter._trades == []


def test_scenario_2_restart_continuity_5sec_later():
    """Strategy bought TSLA yesterday; restart 5 seconds later. Broker still
    shows TSLA. The strategy MUST recognize TSLA as its own."""
    from backend.broker_adapters.alpaca import AlpacaAdapter

    wal_rows = [
        {
            "client_order_id": "intellistock-main-20260527-abc",
            "broker_order_id": "bx-1",
            "status": "filled",
            "ticker": "TSLA",
            "side": "BUY",
            "filled_qty": 10.0,
            "filled_avg_price": 220.0,
            "filled_at_utc": _NOW - timedelta(days=1, hours=2),
        },
    ]
    with patch("backend.broker_adapters.alpaca.TradingClient",
               return_value=_stub_alpaca_client([_pos("TSLA", 10.0, 2200.0)])):
        adapter = AlpacaAdapter(
            api_key="k", api_secret="s", paper=True, instance_id="main",
            wal_store=_WAL(wal_rows),
            seed_trades_from_broker=False,
            initial_value=10000.0,
            clean_room_mode=True,
            cid_prefix="intellistock-main-",
        )
    assert adapter._positions == {"TSLA": 10.0}
    assert adapter._external_positions == {}
    # _trades reconstructed from WAL → V32 risk-exit reads correct entry
    assert len(adapter._trades) == 1
    assert adapter._trades[0]["ticker"] == "TSLA"
    assert adapter._trades[0]["price"] == 220.0
    assert adapter._trades[0]["source"] == "wal"


def test_scenario_3_manual_sell_during_downtime():
    """WAL has TSLA filled BUY; broker shows ZERO TSLA (operator sold manually).
    Strategy state must reflect broker reality (TSLA not in positions)."""
    from backend.broker_adapters.alpaca import AlpacaAdapter

    wal_rows = [
        {
            "client_order_id": "intellistock-main-20260520-xyz",
            "broker_order_id": "bx-old",
            "status": "filled",
            "ticker": "TSLA",
            "side": "BUY",
            "filled_qty": 10.0,
            "filled_avg_price": 200.0,
            "filled_at_utc": _NOW - timedelta(days=8),
        },
    ]
    # Broker shows EMPTY positions
    with patch("backend.broker_adapters.alpaca.TradingClient",
               return_value=_stub_alpaca_client([])):
        adapter = AlpacaAdapter(
            api_key="k", api_secret="s", paper=True, instance_id="main",
            wal_store=_WAL(wal_rows),
            seed_trades_from_broker=False,
            initial_value=10000.0,
            clean_room_mode=True,
            cid_prefix="intellistock-main-",
        )
    assert adapter._positions == {}, "TSLA was manually sold; strategy must not think it's held"
    assert adapter._external_positions == {}
    # _trades reflects the historical WAL fill (it really happened); strategy
    # consumers won't try to manage a position they don't own.
    assert len(adapter._trades) == 1


def test_scenario_4_partial_external_split():
    """Broker shows 50 NVDA; WAL implies only 30 NVDA. Strategy owns 30,
    quarantines 20."""
    from backend.broker_adapters.alpaca import AlpacaAdapter

    wal_rows = [
        {
            "client_order_id": "intellistock-main-20260501-a", "broker_order_id": "bx1",
            "status": "filled", "ticker": "NVDA", "side": "BUY",
            "filled_qty": 10.0, "filled_avg_price": 180.0,
            "filled_at_utc": _NOW - timedelta(days=27),
        },
        {
            "client_order_id": "intellistock-main-20260510-b", "broker_order_id": "bx2",
            "status": "filled", "ticker": "NVDA", "side": "BUY",
            "filled_qty": 20.0, "filled_avg_price": 200.0,
            "filled_at_utc": _NOW - timedelta(days=18),
        },
    ]
    with patch("backend.broker_adapters.alpaca.TradingClient",
               return_value=_stub_alpaca_client([_pos("NVDA", 50.0, 10000.0)])):
        adapter = AlpacaAdapter(
            api_key="k", api_secret="s", paper=True, instance_id="main",
            wal_store=_WAL(wal_rows),
            seed_trades_from_broker=False,
            initial_value=10000.0,
            clean_room_mode=True,
            cid_prefix="intellistock-main-",
        )
    assert adapter._positions == {"NVDA": 30.0}
    assert "NVDA" in adapter._external_positions
    assert adapter._external_positions["NVDA"]["qty"] == 20.0
    assert "partial external" in adapter._external_positions["NVDA"]["note"]
```

- [ ] **Step 2: Run scenario tests; verify pass**

Run:
```
python3 -m pytest backend/tests/test_clean_room_scenarios.py -v 2>&1 | tail -25
```
Expected: 4 PASS.

If any scenario FAILS, this represents a real bug in the design or implementation. Read the failure carefully, trace through the classifier output, and fix at the lowest level (classifier or adapter init branch).

- [ ] **Step 3: Run full pytest sweep**

Run:
```
python3 -m pytest backend/tests/ -q --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py 2>&1 | tail -10
```
Expected: 21 pre-existing failures, 0 NEW.

- [ ] **Step 4: Commit**

```
git add backend/tests/test_clean_room_scenarios.py
git commit -m "test(broker): four scenario tests for clean_room_mode design

Locks in the 4 scenarios from the spec: first-boot-contaminated,
restart-continuity, manual-sell-during-downtime, partial-external.
End-to-end at the classifier + adapter __init__ boundary.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: Baseline verification + parallel bug sweep + push

Goal: confirm the full test suite holds at the 21-pre-existing-failures baseline, dispatch parallel bug-sweep agents on the new code, address findings, then push.

**Files:** none modified directly; sweep may identify bugs in earlier tasks.

- [ ] **Step 1: Full pytest sweep**

Run:
```
python3 -m pytest backend/tests/ -q --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py 2>&1 | tail -30
```
Expected: 21 pre-existing failures, 0 NEW. If new failures appear, fix at the offending task's level (revert+redo or add a follow-up commit).

- [ ] **Step 2: Run git log to confirm full task chain landed**

Run:
```
git log --oneline 6e2dcf0..HEAD
```
Expected: ~14 commits, all signed with the Co-Authored-By footer.

- [ ] **Step 3: Dispatch parallel bug-sweep agents**

Three independent sweeps (run in parallel):

(a) **Classifier correctness sweep** — check `backend/broker_adapters/_classifier.py` for off-by-one in retention math, edge case where SELL row precedes BUY row, negative qty handling, missing `filled_at_utc` (uses `submitted_at_utc` fallback), tolerance arithmetic for fractional shares.

(b) **Adapter init refactor sweep** — check `backend/broker_adapters/alpaca.py` and `robinhood.py` for: imports left dangling; the `_initial_value <= 0` guard runs after the clean-room branch (so a zero initial_value error message still makes sense); `_external_positions` is set BEFORE any code that might read it; the legacy path is byte-identical to pre-refactor behavior when `clean_room_mode=False`.

(c) **broker.py integration sweep** — check `backend/broker.py:5258-5310` and the new audit-write block for: variable name collisions with existing locals; `_alert_strategy_error` call signature matches the existing pattern at `broker.py:5283`; `_audit_conn` is closed in a `finally`; env-var parsing handles malformed input without crashing the daemon.

- [ ] **Step 4: Apply any bug-sweep fixes**

For each finding, commit a focused fix (one commit per logical bug) using the same Co-Authored-By footer.

- [ ] **Step 5: Re-run full pytest after fixes**

Run the same command as Step 1. Expected: still 21 pre-existing failures, 0 NEW.

- [ ] **Step 6: Push**

```
git push origin claude-code-integration
```
Expected: clean push to remote; CI should reflect the new commits.

- [ ] **Step 7: Store handoff in JarvisCopilot memory**

Use the session-handoff skill to write a comprehensive handoff describing where this lands and what the operator must do next (run inspect_broker_state.py, manually flatten externals, write the apply script for `Instances.main.clean_room_mode=True`).
