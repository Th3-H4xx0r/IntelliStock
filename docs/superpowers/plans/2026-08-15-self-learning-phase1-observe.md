# Self-Learning Phase 1 (Observe) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record every decision IntelliStock makes — including the ones it declined to act on — resolve them to outcomes, and automatically raise a finding when a scored field has no variance.

**Architecture:** A pure, DB-free package `backend/self_learning/` plus one I/O module and one engine daemon, following the repo's existing `engines/*.py` + pure-helper idiom. Data comes from documents that already exist (`BacktestResults.backtest_decisions`, `.backtest_trades`, `BotTradeDecisions`), written by `broker.py` in the common execution path — so **no strategy file is modified** and strategy-agnosticism is inherited rather than enforced.

**Tech Stack:** Python 3 (stdlib + `rethinkdb`), pytest, FastAPI, Vue 3 + Vite + Tailwind, Flutter/Riverpod/go_router.

**Spec:** `docs/superpowers/specs/2026-08-15-self-learning-design.md`

## Global Constraints

- **No strategy file may be modified in Phase 1.** If a task appears to need one, stop — the design is wrong.
- **Never write a test that re-implements the logic it tests.** Two files in this repo stayed green over live defects doing exactly that and were deleted. Tests call production functions.
- **Fixtures are built from real document shapes**, copied from the field lists in this plan — not from an idealised shape.
- **Test baseline is 19 pre-existing failures**: `test_adv_exit_discipline_findings` ×11, `test_core_sleeve_adversarial` ×7, `test_zz_adversarial_sweep` ×1. Compare the failure **set**, not the count. Run with `python3 -m pytest backend/tests -q -p no:randomly`.
- **Pure modules do no I/O.** Only `store.py` and the engine touch RethinkDB. This is what makes the rest unit-testable without a database.
- **Phase 1 writes no strategy config and takes no autonomous action.** It observes only.
- Commit footer on every commit:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0122BxJkAu1ARf3gp4LFEM8k
  ```

## Source Document Shapes (verbatim — build fixtures from these)

`BacktestResults.backtest_decisions[]` — written at `backend/broker.py:17342`:
```python
{"timestamp": "2026-04-01T13:30:00", "symbol": "INTC", "action": "buy",
 "decision": 1, "normalized_score": 1.0, "override_applied": False,
 "pre_override_action": None, "pre_override_decision": None,
 "primary_strategy": "graph_nexus_analysis", "primary_action_intent": "buy",
 "final_reason": "...", "strategies": [{"strategy": "...", "decision": 1,
 "weight": 0.5, "reason": "..."}], "post_decision": []}
```

`BacktestResults.backtest_trades[]` — from `PortfolioEmulator._trades`, `backend/portfolio_emulator.py:229`:
```python
{"timestamp": "2026-04-01T13:30:00", "action": "buy", "ticker": "INTC",
 "shares": 10.0, "price": 22.5, "total": 225.0, "cash_after": 5000.0}
```

**Join gotcha:** decisions key the symbol as `symbol`; trades key it as `ticker`. They are different field names for the same thing.

`BotTradeDecisions` rows (live twin, indexed by `brokerage_id`) carry `symbol`, `ts`, `decision`, `strategy_summary`, plus the fields `bot_decision_log.primary_decision()` derives.

## File Structure

| file | responsibility |
|---|---|
| `backend/self_learning/__init__.py` | package exports |
| `backend/self_learning/types.py` | `Observation`, `VarianceReport`, `Finding`, `Lever` + content hashing |
| `backend/self_learning/observers.py` | normalize backtest + live documents into `Observation`s, including the refusal join |
| `backend/self_learning/variance.py` | Guard 3 — input-variance assertion |
| `backend/self_learning/levers.py` | derive the tunable surface from declared strategy schemas |
| `backend/self_learning/findings.py` | build `Finding`s from guard results |
| `backend/self_learning/retention.py` | pure retention/rollup policy |
| `backend/self_learning/store.py` | the only RethinkDB module: table bootstrap, idempotent writes, reads |
| `backend/engines/self_learning_engine.py` | the daemon |
| `backend/engine_control.py` | register the engine id |
| `backend/server.py` | table creation + engine container watcher |
| `backend/interactive_utils.py` | `action_*` read functions |
| `backend/api/main.py` | HTTP routes |
| `frontend/src/views/LearningView.vue` + router + nav | web tab |
| `mobile/lib/features/learning/**` + More sheet | mobile tab |
| `scripts/backfill_learning_observations.py` | one-shot historical backfill |

---

### Task 1: Core types and content hashing

**Files:**
- Create: `backend/self_learning/__init__.py`, `backend/self_learning/types.py`
- Test: `backend/tests/test_self_learning_types.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Observation`, `VarianceReport`, `Finding`, `Lever` frozen dataclasses; `content_id(kind: str, identity: dict) -> str` returning a 64-char hex digest; every record type exposes `.to_doc() -> dict` and `.id` .

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_self_learning_types.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.types import Observation, content_id


def _obs(**over):
    base = dict(
        run_id="559934", origin="backtest", venue="equity",
        strategy_id="graph_nexus_analysis", as_of="2026-04-01T13:30:00",
        symbol="INTC", action="buy", decision=1, normalized_score=1.0,
        executed=False, refusal_reason="unknown",
        votes=(("graph_nexus_analysis", 1, 0.5),), config_hash=None,
    )
    base.update(over)
    return Observation(**base)


def test_content_id_is_stable_and_64_hex():
    a = content_id("observation", {"run_id": "1", "symbol": "X"})
    b = content_id("observation", {"symbol": "X", "run_id": "1"})
    assert a == b, "key order must not change the identity"
    assert len(a) == 64 and all(c in "0123456789abcdef" for c in a)


def test_same_decision_point_is_the_same_observation():
    assert _obs().id == _obs().id


def test_a_different_bar_is_a_different_observation():
    assert _obs().id != _obs(as_of="2026-04-02T13:30:00").id


def test_identity_ignores_fields_that_are_not_the_decision_point():
    # executed/refusal_reason are RESOLVED later; re-resolving must update the
    # same row, not create a second one. This is what makes backfill idempotent.
    assert _obs(executed=False).id == _obs(executed=True, refusal_reason=None).id


def test_to_doc_round_trips_votes_as_lists():
    doc = _obs().to_doc()
    assert doc["id"] == _obs().id
    assert doc["votes"] == [["graph_nexus_analysis", 1, 0.5]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_self_learning_types.py -q -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'self_learning'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/self_learning/types.py
"""Typed, content-addressed records for the self-learning subsystem.

Identity is deliberately narrow: an Observation is identified by WHERE and WHEN
a decision happened, never by what we later learned about it. Resolving an
outcome must UPDATE that row rather than create a second one, or backfill would
duplicate every record it re-reads.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

SCHEMA_VERSION = 1


def content_id(kind: str, identity: dict) -> str:
    """A stable 64-hex identity over `kind` + the natural key. Key order and
    dict nesting never change the result."""
    canonical = json.dumps(
        {"schema_version": SCHEMA_VERSION, "kind": kind, "identity": identity},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Observation:
    run_id: str
    origin: str            # "backtest" | "live"
    venue: str             # "equity" | "crypto" | "kalshi"
    strategy_id: str
    as_of: str             # ISO timestamp of the bar
    symbol: str
    action: str
    decision: int          # 1 buy | 0 hold | -1 sell
    normalized_score: float | None
    executed: bool
    refusal_reason: str | None
    votes: tuple           # ((strategy, decision, weight), ...)
    config_hash: str | None

    @property
    def id(self) -> str:
        return content_id("observation", {
            "run_id": self.run_id, "origin": self.origin,
            "symbol": self.symbol, "as_of": self.as_of,
        })

    def to_doc(self) -> dict:
        return {
            "id": self.id, "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id, "origin": self.origin, "venue": self.venue,
            "strategy_id": self.strategy_id, "as_of": self.as_of,
            "symbol": self.symbol, "action": self.action,
            "decision": self.decision, "normalized_score": self.normalized_score,
            "executed": self.executed, "refusal_reason": self.refusal_reason,
            "votes": [list(v) for v in self.votes],
            "config_hash": self.config_hash,
        }


@dataclass(frozen=True)
class VarianceReport:
    field_name: str
    n: int
    distinct: int
    top_value: object
    top_share: float
    saturated: bool

    def to_doc(self) -> dict:
        return {
            "field_name": self.field_name, "n": self.n,
            "distinct": self.distinct, "top_value": self.top_value,
            "top_share": round(self.top_share, 6), "saturated": self.saturated,
        }


@dataclass(frozen=True)
class Finding:
    kind: str
    target: str            # "<venue>/<strategy_id>"
    severity: str          # "low" | "medium" | "high"
    title: str
    detail: str
    evidence: dict = field(default_factory=dict)
    detected_at: str = ""
    run_id: str = ""
    status: str = "open"

    @property
    def id(self) -> str:
        return content_id("finding", {
            "kind": self.kind, "target": self.target, "title": self.title,
        })

    def to_doc(self) -> dict:
        return {
            "id": self.id, "schema_version": SCHEMA_VERSION, "kind": self.kind,
            "target": self.target, "severity": self.severity,
            "title": self.title, "detail": self.detail,
            "evidence": self.evidence, "detected_at": self.detected_at,
            "run_id": self.run_id, "status": self.status,
        }


@dataclass(frozen=True)
class Lever:
    strategy_id: str
    kind: str              # "config" | "weight" | "execution_position" | "membership"
    key: str
    value_type: str        # "bool" | "number" | "string" | "list" | "dict" | "null"
    default: object

    def to_doc(self) -> dict:
        return {
            "strategy_id": self.strategy_id, "kind": self.kind, "key": self.key,
            "value_type": self.value_type, "default": self.default,
        }
```

```python
# backend/self_learning/__init__.py
"""Self-learning subsystem: observe, measure, hypothesise, act.

Phase 1 is OBSERVE only — nothing in this package writes strategy config or
takes an autonomous action. Every module except `store` is pure and DB-free so
it unit-tests without RethinkDB, matching the idiom of `scheduler.py`,
`nexus_telemetry.py` and `bot_decision_log.py`.
"""
from self_learning.types import (  # noqa: F401
    Finding, Lever, Observation, VarianceReport, content_id,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/test_self_learning_types.py -q -p no:randomly`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/self_learning/__init__.py backend/self_learning/types.py backend/tests/test_self_learning_types.py
git commit -m "Self-learning: typed, content-addressed records"
```

---

### Task 2: Backtest observer — including the refusal join

**Files:**
- Create: `backend/self_learning/observers.py`
- Test: `backend/tests/test_self_learning_observers.py`

**Interfaces:**
- Consumes: `Observation` from Task 1
- Produces: `observations_from_backtest(doc: dict, *, venue: str = "equity") -> list[Observation]`, `funnel_summary(doc: dict) -> dict` returning `{"decided": int, "executed": int, "refused": int, "buy_decided": int, "buy_executed": int}`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_self_learning_observers.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.observers import funnel_summary, observations_from_backtest


def _doc():
    """Shape copied from broker.py:17342 (decisions) and
    portfolio_emulator.py:229 (trades). Note symbol vs ticker."""
    return {
        "id": 559934,
        "backtest_decisions": [
            {"timestamp": "2026-04-01T13:30:00", "symbol": "INTC", "action": "buy",
             "decision": 1, "normalized_score": 1.0, "primary_strategy": "graph_nexus_analysis",
             "final_reason": "graph", "strategies": [
                 {"strategy": "graph_nexus_analysis", "decision": 1, "weight": 0.5,
                  "reason": "graph"}]},
            {"timestamp": "2026-04-01T13:30:00", "symbol": "XOM", "action": "buy",
             "decision": 1, "normalized_score": 1.0, "primary_strategy": "graph_nexus_analysis",
             "final_reason": "graph", "strategies": []},
            {"timestamp": "2026-04-02T13:30:00", "symbol": "INTC", "action": "hold",
             "decision": 0, "normalized_score": 1.0, "primary_strategy": "rsi",
             "final_reason": "", "strategies": []},
        ],
        "backtest_trades": [
            {"timestamp": "2026-04-01T13:30:00", "action": "buy", "ticker": "INTC",
             "shares": 10.0, "price": 22.5, "total": 225.0, "cash_after": 5000.0},
        ],
    }


def test_every_decision_becomes_an_observation():
    obs = observations_from_backtest(_doc())
    assert len(obs) == 3


def test_the_filled_name_is_marked_executed_despite_symbol_vs_ticker():
    intc = [o for o in observations_from_backtest(_doc())
            if o.symbol == "INTC" and o.as_of == "2026-04-01T13:30:00"][0]
    assert intc.executed is True
    assert intc.refusal_reason is None


def test_a_buy_that_never_filled_is_a_refusal():
    xom = [o for o in observations_from_backtest(_doc()) if o.symbol == "XOM"][0]
    assert xom.executed is False
    # Phase 1 records THAT it was refused; WHY lives only in run logs.
    assert xom.refusal_reason == "unknown"


def test_a_hold_is_not_a_refusal():
    hold = [o for o in observations_from_backtest(_doc()) if o.decision == 0][0]
    assert hold.executed is False
    assert hold.refusal_reason is None


def test_strategy_id_comes_from_the_primary_strategy_not_a_hardcoded_name():
    ids = {o.strategy_id for o in observations_from_backtest(_doc())}
    assert ids == {"graph_nexus_analysis", "rsi"}


def test_votes_are_carried_through():
    intc = [o for o in observations_from_backtest(_doc())
            if o.symbol == "INTC" and o.decision == 1][0]
    assert intc.votes == (("graph_nexus_analysis", 1, 0.5),)


def test_funnel_summary_counts_the_refusals():
    assert funnel_summary(_doc()) == {
        "decided": 3, "executed": 1, "refused": 1,
        "buy_decided": 2, "buy_executed": 1,
    }


def test_a_document_with_no_decisions_yields_nothing_and_does_not_raise():
    assert observations_from_backtest({"id": 1}) == []
    assert funnel_summary({"id": 1})["decided"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_self_learning_observers.py -q -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'self_learning.observers'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/self_learning/observers.py
"""Normalize existing decision documents into `Observation`s.

The sources are written by `broker.py` in the COMMON execution path, not by any
strategy, so this module is strategy-agnostic by construction: RSI and
graph_nexus_analysis produce the same record shape.

The refusal set is the point. `backtest_decisions` records what was DECIDED and
`backtest_trades` records what EXECUTED; a BUY present in the first and absent
from the second is a refusal, and refusals are where this project's findings
have always lived (0 of 134 grants cleared the min-position floor; 0% of the 52
names that moved 30%+ were bought). Nothing else records them.
"""
from __future__ import annotations

from self_learning.types import Observation

_BUY_ACTIONS = frozenset({"buy", "initial_buy"})


def _norm_symbol(value) -> str:
    return str(value or "").strip().upper()


def _trade_keys(doc: dict) -> set:
    """(symbol, timestamp) for every executed trade. Trades call it `ticker`;
    decisions call it `symbol` — the same thing under two names."""
    keys = set()
    for trade in (doc.get("backtest_trades") or []):
        if not isinstance(trade, dict):
            continue
        keys.add((_norm_symbol(trade.get("ticker") or trade.get("symbol")),
                  str(trade.get("timestamp") or "")))
    return keys


def _votes(entry: dict) -> tuple:
    out = []
    for sub in (entry.get("strategies") or []):
        if not isinstance(sub, dict):
            continue
        try:
            weight = float(sub.get("weight") or 0.0)
        except (TypeError, ValueError):
            weight = 0.0
        try:
            decision = int(sub.get("decision") or 0)
        except (TypeError, ValueError):
            decision = 0
        out.append((str(sub.get("strategy") or ""), decision, weight))
    return tuple(out)


def _score(entry: dict):
    raw = entry.get("normalized_score")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def observations_from_backtest(doc: dict, *, venue: str = "equity") -> list:
    """One `Observation` per decision record in a `BacktestResults` document."""
    decisions = (doc or {}).get("backtest_decisions") or []
    executed_keys = _trade_keys(doc or {})
    run_id = str((doc or {}).get("id") or "")
    out = []
    for entry in decisions:
        if not isinstance(entry, dict):
            continue
        symbol = _norm_symbol(entry.get("symbol"))
        as_of = str(entry.get("timestamp") or "")
        try:
            decision = int(entry.get("decision") or 0)
        except (TypeError, ValueError):
            decision = 0
        executed = (symbol, as_of) in executed_keys
        action = str(entry.get("action") or "").strip().lower()
        # A hold that did not trade is not a refusal — it is the system doing
        # exactly what it decided. Only an unexecuted BUY/SELL is a refusal.
        refused = (not executed) and (decision != 0)
        out.append(Observation(
            run_id=run_id, origin="backtest", venue=venue,
            strategy_id=str(entry.get("primary_strategy") or ""),
            as_of=as_of, symbol=symbol, action=action, decision=decision,
            normalized_score=_score(entry), executed=executed,
            refusal_reason="unknown" if refused else None,
            votes=_votes(entry), config_hash=(doc or {}).get("config_hash"),
        ))
    return out


def funnel_summary(doc: dict) -> dict:
    """Aggregate counts for one run. Written as an aggregate, never as rows."""
    obs = observations_from_backtest(doc)
    buys = [o for o in obs if o.action in _BUY_ACTIONS or o.decision == 1]
    return {
        "decided": len(obs),
        "executed": sum(1 for o in obs if o.executed),
        "refused": sum(1 for o in obs if o.refusal_reason is not None),
        "buy_decided": len(buys),
        "buy_executed": sum(1 for o in buys if o.executed),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/test_self_learning_observers.py -q -p no:randomly`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/self_learning/observers.py backend/tests/test_self_learning_observers.py
git commit -m "Self-learning: the refusal join, where the findings actually live"
```

---

### Task 3: Live observer

**Files:**
- Modify: `backend/self_learning/observers.py`
- Modify: `backend/tests/test_self_learning_observers.py`

**Interfaces:**
- Produces: `observations_from_live(rows: list, *, instance_id: str, venue: str = "equity") -> list[Observation]`

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_self_learning_observers.py
from self_learning.observers import observations_from_live


def _live_rows():
    """BotTradeDecisions shape — written by broker.py via
    bot_decision_log.primary_decision()."""
    return [
        {"id": "a", "brokerage_id": "alpaca-main", "symbol": "MSFT",
         "ts": "2026-08-14T17:02:00Z", "decision": 1, "action": "buy",
         "primary_strategy": "graph_nexus_analysis", "normalized_score": 1.0,
         "filled": True,
         "strategy_summary": [{"strategy": "graph_nexus_analysis",
                               "decision": 1, "weight": 0.5, "reason": "r"}]},
        {"id": "b", "brokerage_id": "alpaca-main", "symbol": "HAPN",
         "ts": "2026-08-14T17:02:00Z", "decision": 1, "action": "buy",
         "primary_strategy": "graph_nexus_analysis", "normalized_score": 1.0,
         "filled": False, "strategy_summary": []},
    ]


def test_live_rows_become_observations_with_live_origin():
    obs = observations_from_live(_live_rows(), instance_id="alpaca-main")
    assert len(obs) == 2
    assert {o.origin for o in obs} == {"live"}
    assert {o.run_id for o in obs} == {"alpaca-main"}


def test_an_unfilled_live_buy_is_a_refusal():
    obs = observations_from_live(_live_rows(), instance_id="alpaca-main")
    hapn = [o for o in obs if o.symbol == "HAPN"][0]
    assert hapn.executed is False and hapn.refusal_reason == "unknown"


def test_live_and_backtest_observations_never_collide_on_id():
    live = observations_from_live(_live_rows(), instance_id="559934")[0]
    bt = observations_from_backtest(_doc())[0]
    assert live.id != bt.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_self_learning_observers.py -q -p no:randomly`
Expected: FAIL — `ImportError: cannot import name 'observations_from_live'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to backend/self_learning/observers.py

def observations_from_live(rows: list, *, instance_id: str,
                           venue: str = "equity") -> list:
    """One `Observation` per `BotTradeDecisions` row.

    `origin` participates in the content id, so a live instance and a backtest
    that happen to share an id can never collide.
    """
    out = []
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        try:
            decision = int(row.get("decision") or 0)
        except (TypeError, ValueError):
            decision = 0
        executed = bool(row.get("filled"))
        refused = (not executed) and (decision != 0)
        out.append(Observation(
            run_id=str(instance_id), origin="live", venue=venue,
            strategy_id=str(row.get("primary_strategy") or ""),
            as_of=str(row.get("ts") or row.get("created_at") or ""),
            symbol=_norm_symbol(row.get("symbol")),
            action=str(row.get("action") or "").strip().lower(),
            decision=decision, normalized_score=_score(row), executed=executed,
            refusal_reason="unknown" if refused else None,
            votes=_votes({"strategies": row.get("strategy_summary") or []}),
            config_hash=row.get("config_hash"),
        ))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/test_self_learning_observers.py -q -p no:randomly`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/self_learning/observers.py backend/tests/test_self_learning_observers.py
git commit -m "Self-learning: live observer over BotTradeDecisions"
```

---

### Task 4: Guard 3 — the input-variance assertion

**Files:**
- Create: `backend/self_learning/variance.py`
- Test: `backend/tests/test_self_learning_variance.py`

**Interfaces:**
- Consumes: `VarianceReport` from Task 1
- Produces: `assess_variance(values, *, field_name: str, threshold: float = 0.95, min_n: int = 30) -> VarianceReport`, `assess_observations(observations, *, field_name: str = "normalized_score", **kw) -> VarianceReport`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_self_learning_variance.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.types import Observation
from self_learning.variance import assess_observations, assess_variance


def test_a_constant_field_is_saturated():
    rep = assess_variance([1.0] * 100, field_name="normalized_score")
    assert rep.saturated is True
    assert rep.distinct == 1 and rep.top_share == 1.0


def test_the_real_history_ratio_is_saturated():
    # 717 of 723 candidates scored exactly +1.000 (measured across
    # bt 866880 / 235194 / 559934 / 599773). This is the case the guard exists
    # for: it must fire on THIS, not only on a perfect constant.
    rep = assess_variance([1.0] * 717 + [0.4] * 6, field_name="normalized_score")
    assert rep.saturated is True
    assert round(rep.top_share, 3) == 0.992


def test_a_healthy_spread_is_not_saturated():
    rep = assess_variance([i / 100.0 for i in range(100)],
                          field_name="normalized_score")
    assert rep.saturated is False


def test_a_small_sample_is_never_declared_saturated():
    # 5 identical values is not evidence of a constant signal, it is a small
    # sample. Declaring it saturated would raise a finding on every fresh run.
    rep = assess_variance([1.0] * 5, field_name="normalized_score")
    assert rep.saturated is False
    assert rep.n == 5


def test_none_values_are_excluded_from_the_denominator():
    rep = assess_variance([1.0] * 40 + [None] * 10, field_name="x")
    assert rep.n == 40


def test_all_none_is_not_saturated_and_does_not_divide_by_zero():
    rep = assess_variance([None] * 50, field_name="x")
    assert rep.n == 0 and rep.saturated is False and rep.top_share == 0.0


def test_assess_observations_reads_the_named_field():
    obs = [Observation(
        run_id="1", origin="backtest", venue="equity", strategy_id="s",
        as_of=f"2026-04-{i:02d}", symbol="X", action="buy", decision=1,
        normalized_score=1.0, executed=False, refusal_reason="unknown",
        votes=(), config_hash=None) for i in range(1, 32)]
    rep = assess_observations(obs)
    assert rep.field_name == "normalized_score" and rep.saturated is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_self_learning_variance.py -q -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'self_learning.variance'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/self_learning/variance.py
"""Guard 3 — assert an input actually varies before learning from it.

The selection signal in this project was a CONSTANT for months: 717 of 723 buy
candidates scored exactly +1.000, which made every A/B ever run a measurement of
noise, because a 97%-equal-weight allocator returns the same book whatever is
tuned. Nothing detected it. This module is the detector, and a saturated field
raises a DEFECT FINDING rather than becoming a feature the learner trusts.
"""
from __future__ import annotations

from collections import Counter

from self_learning.types import VarianceReport


def assess_variance(values, *, field_name: str, threshold: float = 0.95,
                    min_n: int = 30) -> VarianceReport:
    """`saturated` when one value holds >= `threshold` of a sample of at least
    `min_n`. The floor matters: five identical values is a small sample, not a
    constant signal, and declaring it saturated would fire on every fresh run.
    """
    present = [v for v in (values or []) if v is not None]
    n = len(present)
    if n == 0:
        return VarianceReport(field_name=field_name, n=0, distinct=0,
                              top_value=None, top_share=0.0, saturated=False)
    counts = Counter(present)
    top_value, top_count = counts.most_common(1)[0]
    top_share = top_count / float(n)
    return VarianceReport(
        field_name=field_name, n=n, distinct=len(counts), top_value=top_value,
        top_share=top_share, saturated=bool(n >= min_n and top_share >= threshold),
    )


def assess_observations(observations, *, field_name: str = "normalized_score",
                        **kwargs) -> VarianceReport:
    """Run the assertion over an attribute of a list of `Observation`s."""
    values = [getattr(o, field_name, None) for o in (observations or [])]
    return assess_variance(values, field_name=field_name, **kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/test_self_learning_variance.py -q -p no:randomly`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/self_learning/variance.py backend/tests/test_self_learning_variance.py
git commit -m "Self-learning: the guard that would have caught the constant signal"
```

---

### Task 5: Lever surface from declared strategy schemas

**Files:**
- Create: `backend/self_learning/levers.py`
- Test: `backend/tests/test_self_learning_levers.py`

**Interfaces:**
- Consumes: `Lever` from Task 1
- Produces: `levers_from_schema(strategy_id: str, schema: dict) -> list[Lever]`, `lever_surface(strategies: list) -> list[Lever]` where `strategies` is the output of `strategies_meta.get_available_strategies()`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_self_learning_levers.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.levers import lever_surface, levers_from_schema

# Real header shape, copied from backend/strategies/rsi.py line 1.
_RSI = {"strategy": "Rsi", "weight": 0.5, "execution_position": 0,
        "conditions": {}, "config": {"period": 14, "oversold": 30,
                                     "overbought": 70, "use_midline": False}}


def test_each_declared_config_key_becomes_a_config_lever():
    keys = {l.key for l in levers_from_schema("rsi", _RSI) if l.kind == "config"}
    assert keys == {"period", "oversold", "overbought", "use_midline"}


def test_weight_and_execution_position_and_membership_are_levers_too():
    kinds = {l.kind for l in levers_from_schema("rsi", _RSI)}
    assert {"config", "weight", "execution_position", "membership"} <= kinds


def test_value_types_come_from_the_declared_defaults():
    by_key = {l.key: l for l in levers_from_schema("rsi", _RSI)}
    assert by_key["period"].value_type == "number"
    assert by_key["use_midline"].value_type == "bool"


def test_a_credential_placeholder_is_not_a_tunable_lever():
    schema = {"config": {"llm_api_key": "<optional>", "buy_threshold": 0.15}}
    keys = {l.key for l in levers_from_schema("x", schema) if l.kind == "config"}
    assert keys == {"buy_threshold"}


def test_a_strategy_with_no_schema_yields_no_levers_and_does_not_raise():
    assert levers_from_schema("helper", None) == []
    assert levers_from_schema("helper", {}) == []


def test_lever_surface_spans_every_strategy_it_is_given():
    surface = lever_surface([
        {"id": "rsi", "schema": _RSI},
        {"id": "macd", "schema": {"config": {"fast": 12, "slow": 26}}},
    ])
    assert {l.strategy_id for l in surface} == {"rsi", "macd"}


def test_the_real_registry_produces_levers_for_more_than_one_strategy():
    """Calls production discovery — the point of the design is that it works
    for any strategy, so this must not be mocked."""
    from strategies_meta import get_available_strategies
    surface = lever_surface(get_available_strategies())
    strategies = {l.strategy_id for l in surface}
    assert "rsi" in strategies
    assert len(strategies) > 5
    # Nexus declares far more tunables than RSI; same code path.
    nexus = [l for l in surface if l.strategy_id == "graph_nexus_analysis"]
    rsi = [l for l in surface if l.strategy_id == "rsi"]
    assert len(nexus) > len(rsi) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_self_learning_levers.py -q -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'self_learning.levers'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/self_learning/levers.py
"""Derive the tunable surface from what strategies already declare.

All 29 strategy modules carry an `INTELLISTOCK_SCHEMA:` header that
`strategies_meta` already parses. Reading it here is what makes the subsystem
strategy-agnostic: RSI's four tunables and graph_nexus_analysis's ~300 come
through one code path, and a strategy written next year is discovered for free
because it declares its own schema.
"""
from __future__ import annotations

from self_learning.types import Lever

# A schema value of "<optional>" marks a credential slot, not a tunable. Tuning
# an API key is not a strategy change; it is an outage.
_PLACEHOLDER = "<optional>"

_SECRETISH = ("api_key", "password", "secret", "token", "_user", "_uri",
              "endpoint", "base_url")


def _value_type(value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "null"


def _is_tunable(key: str, value) -> bool:
    if isinstance(value, str) and value.strip() == _PLACEHOLDER:
        return False
    lowered = str(key).lower()
    return not any(marker in lowered for marker in _SECRETISH)


def levers_from_schema(strategy_id: str, schema) -> list:
    """Every lever class a single declared schema exposes."""
    if not isinstance(schema, dict) or not schema:
        return []
    out = []
    for key, value in (schema.get("config") or {}).items():
        if not _is_tunable(key, value):
            continue
        out.append(Lever(strategy_id=strategy_id, kind="config", key=str(key),
                         value_type=_value_type(value), default=value))
    out.append(Lever(strategy_id=strategy_id, kind="weight", key="weight",
                     value_type="number", default=schema.get("weight")))
    out.append(Lever(strategy_id=strategy_id, kind="execution_position",
                     key="execution_position", value_type="number",
                     default=schema.get("execution_position")))
    # Presence in the document's `strategies` list is itself a lever.
    out.append(Lever(strategy_id=strategy_id, kind="membership",
                     key="present", value_type="bool", default=True))
    return out


def lever_surface(strategies: list) -> list:
    """Flatten `strategies_meta.get_available_strategies()` into levers."""
    out = []
    for entry in (strategies or []):
        if not isinstance(entry, dict):
            continue
        out.extend(levers_from_schema(str(entry.get("id") or ""),
                                      entry.get("schema")))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/test_self_learning_levers.py -q -p no:randomly`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/self_learning/levers.py backend/tests/test_self_learning_levers.py
git commit -m "Self-learning: derive the lever surface from declared schemas"
```

---

### Task 6: Findings from guard results

**Files:**
- Create: `backend/self_learning/findings.py`
- Test: `backend/tests/test_self_learning_findings.py`

**Interfaces:**
- Consumes: `Finding`, `VarianceReport`, `Observation`
- Produces: `finding_from_variance(report, *, target, run_id, detected_at) -> Finding | None`, `finding_from_funnel(summary, *, target, run_id, detected_at, min_refused=20) -> Finding | None`, `findings_for_run(observations, summary, *, target, run_id, detected_at) -> list[Finding]`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_self_learning_findings.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.findings import (
    finding_from_funnel, finding_from_variance, findings_for_run,
)
from self_learning.types import Observation
from self_learning.variance import assess_variance


def _saturated():
    return assess_variance([1.0] * 717 + [0.4] * 6, field_name="normalized_score")


def _healthy():
    return assess_variance([i / 100.0 for i in range(100)], field_name="normalized_score")


def test_a_saturated_field_raises_a_high_severity_finding():
    f = finding_from_variance(_saturated(), target="equity/nexus",
                              run_id="559934", detected_at="2026-08-15T00:00:00Z")
    assert f is not None
    assert f.kind == "constant_signal" and f.severity == "high"
    assert f.evidence["top_share"] > 0.99


def test_a_healthy_field_raises_nothing():
    assert finding_from_variance(_healthy(), target="equity/nexus", run_id="1",
                                 detected_at="t") is None


def test_the_same_defect_on_the_same_target_is_the_same_finding():
    a = finding_from_variance(_saturated(), target="equity/nexus", run_id="1", detected_at="t1")
    b = finding_from_variance(_saturated(), target="equity/nexus", run_id="2", detected_at="t2")
    assert a.id == b.id, "re-detecting must update one thread, not spawn a new one per run"


def test_the_same_defect_on_a_different_target_is_a_different_finding():
    a = finding_from_variance(_saturated(), target="equity/nexus", run_id="1", detected_at="t")
    b = finding_from_variance(_saturated(), target="crypto/meanrev", run_id="1", detected_at="t")
    assert a.id != b.id


def test_a_run_that_refused_most_of_its_buys_raises_a_conversion_finding():
    f = finding_from_funnel({"decided": 200, "executed": 6, "refused": 140,
                             "buy_decided": 146, "buy_executed": 6},
                            target="equity/nexus", run_id="559934", detected_at="t")
    assert f is not None and f.kind == "buy_conversion"
    assert f.evidence["buy_executed"] == 6


def test_a_run_that_converted_its_buys_raises_nothing():
    assert finding_from_funnel({"decided": 100, "executed": 90, "refused": 2,
                                "buy_decided": 40, "buy_executed": 38},
                               target="equity/nexus", run_id="1",
                               detected_at="t") is None


def test_a_tiny_refusal_count_is_not_a_finding():
    assert finding_from_funnel({"decided": 10, "executed": 1, "refused": 3,
                                "buy_decided": 4, "buy_executed": 1},
                               target="equity/nexus", run_id="1",
                               detected_at="t") is None


def test_findings_for_run_returns_both_when_both_apply():
    obs = [Observation(run_id="1", origin="backtest", venue="equity",
                       strategy_id="s", as_of=f"2026-04-{i:02d}", symbol="X",
                       action="buy", decision=1, normalized_score=1.0,
                       executed=False, refusal_reason="unknown", votes=(),
                       config_hash=None) for i in range(1, 32)]
    out = findings_for_run(obs, {"decided": 31, "executed": 0, "refused": 31,
                                 "buy_decided": 31, "buy_executed": 0},
                           target="equity/nexus", run_id="1", detected_at="t")
    assert {f.kind for f in out} == {"constant_signal", "buy_conversion"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_self_learning_findings.py -q -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'self_learning.findings'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/self_learning/findings.py
"""Turn guard results into findings — the thread roots of the subsystem.

A finding's identity is (kind, target, title) and deliberately excludes the run
that detected it: re-detecting the same defect on the same target must update
ONE thread rather than spawn a new thread per run, or the feed becomes a
scrolling wall of the same fact.
"""
from __future__ import annotations

from self_learning.types import Finding
from self_learning.variance import assess_observations

# A run that decides a lot of buys and executes almost none is the documented
# failure of this codebase: in one window 100% of the 52 names that moved 30%+
# were discovered and 0% were bought. Thresholds are deliberately loose — this
# raises a QUESTION, it does not diagnose.
_MIN_BUY_DECIDED = 20
_CONVERSION_FLOOR = 0.25


def finding_from_variance(report, *, target: str, run_id: str,
                          detected_at: str):
    if report is None or not report.saturated:
        return None
    pct = report.top_share * 100.0
    return Finding(
        kind="constant_signal",
        target=target,
        severity="high",
        title=f"`{report.field_name}` has no variance on {target}",
        detail=(
            f"{report.n - int(round(report.top_share * report.n))} of {report.n} "
            f"samples differ: {pct:.1f}% take the single value "
            f"{report.top_value!r}. A field this saturated cannot rank anything, "
            f"so any A/B tuned against it measures noise rather than the lever."
        ),
        evidence=report.to_doc(),
        detected_at=detected_at,
        run_id=str(run_id),
    )


def finding_from_funnel(summary: dict, *, target: str, run_id: str,
                        detected_at: str, min_buy_decided: int = _MIN_BUY_DECIDED):
    summary = summary or {}
    decided = int(summary.get("buy_decided") or 0)
    executed = int(summary.get("buy_executed") or 0)
    if decided < min_buy_decided:
        return None
    rate = executed / float(decided) if decided else 0.0
    if rate >= _CONVERSION_FLOOR:
        return None
    return Finding(
        kind="buy_conversion",
        target=target,
        severity="high" if rate < 0.1 else "medium",
        title=f"Buy decisions are not converting into fills on {target}",
        detail=(
            f"{executed} of {decided} decided buys executed ({rate * 100:.1f}%). "
            f"The names it declined are recorded; their forward outcomes are "
            f"what tell you whether the refusals cost anything."
        ),
        evidence=dict(summary),
        detected_at=detected_at,
        run_id=str(run_id),
    )


def findings_for_run(observations, summary, *, target: str, run_id: str,
                     detected_at: str) -> list:
    """Every finding a single run supports. Order is stable for the UI."""
    out = []
    variance = finding_from_variance(
        assess_observations(observations), target=target, run_id=run_id,
        detected_at=detected_at)
    if variance is not None:
        out.append(variance)
    funnel = finding_from_funnel(summary, target=target, run_id=run_id,
                                 detected_at=detected_at)
    if funnel is not None:
        out.append(funnel)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/test_self_learning_findings.py -q -p no:randomly`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/self_learning/findings.py backend/tests/test_self_learning_findings.py
git commit -m "Self-learning: findings as thread roots"
```

---

### Task 7: Retention policy (pure)

**Files:**
- Create: `backend/self_learning/retention.py`
- Test: `backend/tests/test_self_learning_retention.py`

**Interfaces:**
- Produces: `expired_ids(docs, *, now_iso: str, retain_days: int = 90) -> list[str]`, `rollup(docs) -> list[dict]` producing one aggregate per `(run_id, strategy_id, date)`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_self_learning_retention.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.retention import expired_ids, rollup


def _docs():
    return [
        {"id": "old", "as_of": "2026-01-01T00:00:00", "run_id": "1",
         "strategy_id": "rsi", "decision": 1, "executed": False,
         "refusal_reason": "unknown", "normalized_score": 1.0},
        {"id": "new", "as_of": "2026-08-01T00:00:00", "run_id": "1",
         "strategy_id": "rsi", "decision": 1, "executed": True,
         "refusal_reason": None, "normalized_score": 0.4},
    ]


def test_only_rows_past_the_window_expire():
    assert expired_ids(_docs(), now_iso="2026-08-15T00:00:00", retain_days=90) == ["old"]


def test_nothing_expires_inside_the_window():
    assert expired_ids(_docs(), now_iso="2026-08-15T00:00:00", retain_days=3650) == []


def test_an_unparseable_timestamp_is_never_deleted():
    """Deleting on a parse failure would silently destroy data. Keep it."""
    docs = [{"id": "bad", "as_of": "not-a-date"}]
    assert expired_ids(docs, now_iso="2026-08-15T00:00:00", retain_days=1) == []


def test_rollup_aggregates_one_row_per_run_strategy_and_date():
    out = rollup(_docs())
    assert len(out) == 2
    assert {r["date"] for r in out} == {"2026-01-01", "2026-08-01"}


def test_rollup_preserves_the_counts_that_carry_the_learning_value():
    out = rollup(_docs() + [dict(_docs()[0], id="old2")])
    jan = [r for r in out if r["date"] == "2026-01-01"][0]
    assert jan["decided"] == 2 and jan["executed"] == 0 and jan["refused"] == 2
    assert jan["score_top_share"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_self_learning_retention.py -q -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'self_learning.retention'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/self_learning/retention.py
"""Retention and rollup for LearningObservations — the only large table.

RethinkDB is already this deployment's bottleneck: PriceHistory at ~2.3M rows
drove 17 restarts in 12 days on a memory-starved VM. So raw observations expire
and a daily rollup keeps the learning value permanently. A row whose timestamp
cannot be parsed is NEVER deleted — a parse bug must not become data loss.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from self_learning.variance import assess_variance


def _parse(value):
    raw = str(value or "").strip().replace("Z", "+00:00")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=None)


def expired_ids(docs, *, now_iso: str, retain_days: int = 90) -> list:
    now = _parse(now_iso)
    if now is None:
        return []
    cutoff = now - timedelta(days=int(retain_days))
    out = []
    for doc in (docs or []):
        stamp = _parse((doc or {}).get("as_of"))
        if stamp is None:
            continue        # unparseable is kept, never deleted
        if stamp < cutoff:
            out.append(str(doc.get("id")))
    return out


def rollup(docs) -> list:
    """One aggregate per (run_id, strategy_id, date). Keeps the counts and the
    saturation share, which is what later phases actually read."""
    buckets = defaultdict(list)
    for doc in (docs or []):
        stamp = _parse((doc or {}).get("as_of"))
        if stamp is None:
            continue
        key = (str(doc.get("run_id") or ""), str(doc.get("strategy_id") or ""),
               stamp.strftime("%Y-%m-%d"))
        buckets[key].append(doc)
    out = []
    for (run_id, strategy_id, date), rows in sorted(buckets.items()):
        scores = [r.get("normalized_score") for r in rows]
        report = assess_variance(scores, field_name="normalized_score", min_n=1)
        out.append({
            "id": f"{run_id}|{strategy_id}|{date}",
            "run_id": run_id, "strategy_id": strategy_id, "date": date,
            "decided": len(rows),
            "executed": sum(1 for r in rows if r.get("executed")),
            "refused": sum(1 for r in rows if r.get("refusal_reason")),
            "score_distinct": report.distinct,
            "score_top_share": round(report.top_share, 6),
        })
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/test_self_learning_retention.py -q -p no:randomly`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/self_learning/retention.py backend/tests/test_self_learning_retention.py
git commit -m "Self-learning: retention so the observation table cannot become a second elephant"
```

---

### Task 8: Store — the only I/O module

**Files:**
- Create: `backend/self_learning/store.py`
- Modify: `backend/server.py` (add the new tables to `ensure_db_and_tables`)
- Test: `backend/tests/test_self_learning_store.py`

**Interfaces:**
- Produces: `LEARNING_TABLES` tuple; `ensure_tables(conn)`, `put_observations(conn, observations)`, `put_findings(conn, findings)`, `put_funnel(conn, run_id, summary, ...)`, `list_findings(conn, limit=100)`, `list_observations(conn, run_id, limit=500)`, `get_config(conn)`, `DEFAULT_CONFIG`

Tests here cover the pure parts (payload construction, defaults merging) with a fake connection object; the RethinkDB calls themselves are exercised by the engine integration test in Task 9.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_self_learning_store.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.store import (
    DEFAULT_CONFIG, LEARNING_TABLES, merge_config, observation_payloads,
)
from self_learning.types import Observation


def _obs():
    return [Observation(run_id="1", origin="backtest", venue="equity",
                        strategy_id="rsi", as_of="2026-04-01T00:00:00",
                        symbol="X", action="buy", decision=1,
                        normalized_score=1.0, executed=False,
                        refusal_reason="unknown", votes=(), config_hash=None)]


def test_every_declared_table_is_prefixed_so_it_is_greppable():
    assert all(t.startswith("Learning") for t in LEARNING_TABLES)
    assert "LearningObservations" in LEARNING_TABLES
    assert "LearningFindings" in LEARNING_TABLES


def test_payloads_carry_the_content_id_as_the_primary_key():
    payloads = observation_payloads(_obs())
    assert payloads[0]["id"] == _obs()[0].id


def test_payloads_are_idempotent_for_the_same_decision_point():
    assert observation_payloads(_obs()) == observation_payloads(_obs())


def test_merge_config_fills_missing_keys_from_defaults():
    merged = merge_config({"retain_days": 30})
    assert merged["retain_days"] == 30
    assert merged["enabled"] == DEFAULT_CONFIG["enabled"]


def test_merge_config_of_none_is_the_defaults():
    assert merge_config(None) == DEFAULT_CONFIG


def test_defaults_ship_observe_only_and_with_an_empty_allowlist():
    # Phase 1 observes. An empty allowlist is what makes that structural
    # rather than a promise.
    assert DEFAULT_CONFIG["mode"] == "observe"
    assert DEFAULT_CONFIG["document_allowlist"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_self_learning_store.py -q -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'self_learning.store'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/self_learning/store.py
"""The only module in `self_learning` that touches RethinkDB.

Everything else is pure so it unit-tests without a database. Writes are
`conflict="update"` on a content id, which makes both the changefeed path and
the historical backfill idempotent: re-reading a run updates its rows instead of
duplicating them.
"""
from __future__ import annotations

import os

from rethinkdb import RethinkDB

r = RethinkDB()
DB_NAME = "IntelliStock"
RETHINKDB_HOST = os.environ.get("RETHINKDB_HOST", "localhost")
RETHINKDB_PORT = int(os.environ.get("RETHINKDB_PORT", "28015"))

OBSERVATIONS = "LearningObservations"
ROLLUPS = "LearningObservationRollups"
FINDINGS = "LearningFindings"
FUNNELS = "LearningFunnels"
CONFIG = "LearningConfig"

LEARNING_TABLES = (OBSERVATIONS, ROLLUPS, FINDINGS, FUNNELS, CONFIG)

CONFIG_DOC_ID = "LearningConfig"

DEFAULT_CONFIG = {
    "id": CONFIG_DOC_ID,
    # Phase 1 is observe-only. Later phases widen this; it is a stored value
    # rather than a code constant so widening it is an operator action.
    "mode": "observe",
    "enabled": True,
    "retain_days": 90,
    "variance_threshold": 0.95,
    "variance_min_n": 30,
    # Empty until an operator arms a document. Nothing is promotable on day one
    # anyway: no target has a measured noise floor yet.
    "document_allowlist": [],
}


def get_conn():
    return r.connect(host=RETHINKDB_HOST, port=RETHINKDB_PORT)


def ensure_tables(conn) -> None:
    existing = list(r.db(DB_NAME).table_list().run(conn))
    for table in LEARNING_TABLES:
        if table not in existing:
            r.db(DB_NAME).table_create(table).run(conn)
    idxs = list(r.db(DB_NAME).table(OBSERVATIONS).index_list().run(conn))
    for index in ("run_id", "strategy_id"):
        if index not in idxs:
            r.db(DB_NAME).table(OBSERVATIONS).index_create(index).run(conn)
            r.db(DB_NAME).table(OBSERVATIONS).index_wait(index).run(conn)


def merge_config(doc) -> dict:
    merged = dict(DEFAULT_CONFIG)
    if isinstance(doc, dict):
        merged.update({k: v for k, v in doc.items() if v is not None})
    return merged


def get_config(conn) -> dict:
    try:
        doc = r.db(DB_NAME).table(CONFIG).get(CONFIG_DOC_ID).run(conn)
    except Exception:
        doc = None
    return merge_config(doc)


def observation_payloads(observations) -> list:
    return [o.to_doc() for o in (observations or [])]


def put_observations(conn, observations) -> int:
    payloads = observation_payloads(observations)
    if not payloads:
        return 0
    r.db(DB_NAME).table(OBSERVATIONS).insert(
        payloads, conflict="update").run(conn)
    return len(payloads)


def put_findings(conn, findings) -> int:
    payloads = [f.to_doc() for f in (findings or [])]
    if not payloads:
        return 0
    r.db(DB_NAME).table(FINDINGS).insert(payloads, conflict="update").run(conn)
    return len(payloads)


def put_funnel(conn, run_id, summary, *, origin="backtest", target="",
               observed_at="") -> None:
    r.db(DB_NAME).table(FUNNELS).insert({
        "id": f"{origin}|{run_id}", "run_id": str(run_id), "origin": origin,
        "target": target, "observed_at": observed_at, **(summary or {}),
    }, conflict="update").run(conn)


def list_findings(conn, limit: int = 100) -> list:
    rows = list(r.db(DB_NAME).table(FINDINGS).run(conn))
    rows.sort(key=lambda d: str(d.get("detected_at") or ""), reverse=True)
    return rows[:max(1, int(limit))]


def list_observations(conn, run_id, limit: int = 500) -> list:
    rows = list(r.db(DB_NAME).table(OBSERVATIONS)
                .get_all(str(run_id), index="run_id").run(conn))
    rows.sort(key=lambda d: str(d.get("as_of") or ""))
    return rows[:max(1, int(limit))]


def list_funnels(conn, limit: int = 100) -> list:
    rows = list(r.db(DB_NAME).table(FUNNELS).run(conn))
    rows.sort(key=lambda d: str(d.get("observed_at") or ""), reverse=True)
    return rows[:max(1, int(limit))]
```

- [ ] **Step 4: Wire the tables into server bootstrap**

In `backend/server.py`, `ensure_db_and_tables`, extend the `tables` tuple with the five learning tables so a fresh deployment creates them:

```python
    tables = ('Config', 'Instances', 'LivePricesStocks', 'LivePrices', 'PriceHistory', 'Strategies', 'BacktestResults', 'BacktestInstances', 'AIBacktestingResults', 'AgentBest', 'GraphNexusNewsCache', 'GraphNexusProgress', 'EngineControl', 'EarningsLLMCache', 'BrokerageAccounts', 'Models', 'LearningObservations', 'LearningObservationRollups', 'LearningFindings', 'LearningFunnels', 'LearningConfig')
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/test_self_learning_store.py -q -p no:randomly`
Expected: PASS (6 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/self_learning/store.py backend/tests/test_self_learning_store.py backend/server.py
git commit -m "Self-learning: persistence with idempotent content-keyed writes"
```

---

### Task 9: The engine daemon

**Files:**
- Create: `backend/engines/self_learning_engine.py`
- Modify: `backend/engine_control.py`
- Create: `backend/self_learning/pipeline.py`
- Test: `backend/tests/test_self_learning_pipeline.py`

**Interfaces:**
- Produces: `process_backtest_document(doc, *, detected_at, venue="equity", variance_threshold=0.95, variance_min_n=30) -> dict` returning `{"observations": [...], "findings": [...], "summary": {...}, "target": str}` — the whole per-run pipeline as a **pure function**, so the engine is a thin I/O shell around a fully tested core.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_self_learning_pipeline.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.pipeline import process_backtest_document


def _saturated_doc():
    """40 buy decisions, all scoring exactly 1.0, only one of which filled —
    the shape this project's real runs actually had."""
    decisions = [
        {"timestamp": f"2026-04-{(i % 28) + 1:02d}T13:30:00", "symbol": f"S{i}",
         "action": "buy", "decision": 1, "normalized_score": 1.0,
         "primary_strategy": "graph_nexus_analysis", "strategies": []}
        for i in range(40)
    ]
    return {"id": 559934, "backtest_decisions": decisions,
            "backtest_trades": [{"timestamp": "2026-04-01T13:30:00",
                                 "action": "buy", "ticker": "S0",
                                 "shares": 1, "price": 1.0}]}


def test_the_pipeline_produces_observations_findings_and_a_summary():
    out = process_backtest_document(_saturated_doc(), detected_at="t")
    assert len(out["observations"]) == 40
    assert out["summary"]["buy_decided"] == 40
    assert out["summary"]["buy_executed"] == 1


def test_it_raises_both_guards_on_a_run_shaped_like_the_real_ones():
    out = process_backtest_document(_saturated_doc(), detected_at="t")
    assert {f.kind for f in out["findings"]} == {"constant_signal", "buy_conversion"}


def test_the_target_is_derived_from_the_data_not_hardcoded():
    out = process_backtest_document(_saturated_doc(), detected_at="t")
    assert out["target"] == "equity/graph_nexus_analysis"


def test_a_different_strategy_yields_a_different_target():
    doc = _saturated_doc()
    for d in doc["backtest_decisions"]:
        d["primary_strategy"] = "rsi"
    assert process_backtest_document(doc, detected_at="t")["target"] == "equity/rsi"


def test_an_empty_document_is_handled_without_raising():
    out = process_backtest_document({"id": 1}, detected_at="t")
    assert out["observations"] == [] and out["findings"] == []


def test_thresholds_are_injected_not_hardcoded():
    # With min_n above the sample size the variance guard must stay silent.
    out = process_backtest_document(_saturated_doc(), detected_at="t",
                                    variance_min_n=1000)
    assert "constant_signal" not in {f.kind for f in out["findings"]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_self_learning_pipeline.py -q -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'self_learning.pipeline'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/self_learning/pipeline.py
"""The per-run pipeline, as a pure function.

Keeping this pure is what lets the engine be a thin I/O shell: every branch that
decides what gets recorded and what gets raised is tested without a database or
a changefeed.
"""
from __future__ import annotations

from collections import Counter

from self_learning.findings import finding_from_funnel, finding_from_variance
from self_learning.observers import funnel_summary, observations_from_backtest
from self_learning.variance import assess_observations


def _dominant_strategy(observations) -> str:
    names = Counter(o.strategy_id for o in observations if o.strategy_id)
    return names.most_common(1)[0][0] if names else "unknown"


def process_backtest_document(doc, *, detected_at: str, venue: str = "equity",
                              variance_threshold: float = 0.95,
                              variance_min_n: int = 30) -> dict:
    observations = observations_from_backtest(doc or {}, venue=venue)
    summary = funnel_summary(doc or {})
    run_id = str((doc or {}).get("id") or "")
    target = f"{venue}/{_dominant_strategy(observations)}" if observations else ""

    findings = []
    if observations:
        variance = finding_from_variance(
            assess_observations(observations, threshold=variance_threshold,
                                min_n=variance_min_n),
            target=target, run_id=run_id, detected_at=detected_at)
        if variance is not None:
            findings.append(variance)
        funnel = finding_from_funnel(summary, target=target, run_id=run_id,
                                     detected_at=detected_at)
        if funnel is not None:
            findings.append(funnel)

    return {"observations": observations, "findings": findings,
            "summary": summary, "target": target, "run_id": run_id}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/test_self_learning_pipeline.py -q -p no:randomly`
Expected: PASS (6 passed)

- [ ] **Step 5: Register the engine id**

In `backend/engine_control.py`, add alongside the existing ids:

```python
ENGINE_ID_SELF_LEARNING = "self_learning_engine"
```

and append `ENGINE_ID_SELF_LEARNING` to `ALL_ENGINE_IDS`.

- [ ] **Step 6: Write the daemon**

```python
# backend/engines/self_learning_engine.py
"""Self-Learning Engine (Phase 1: OBSERVE).

Watches BacktestResults for completed runs, normalizes their decisions into
LearningObservations, and raises findings when a guard trips. It writes NO
strategy config and takes NO autonomous action — later phases add that behind
the permission matrix in LearningConfig.

Controlled via EngineControl.self_learning_engine (running true/false), exactly
like daily_digest_engine and discover_engine.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
os.chdir(_backend_dir)

from rethinkdb import RethinkDB

r = RethinkDB()
DB_NAME = "IntelliStock"

try:
    from intellistock_logger import intellistock_logger

    def _log(msg, color="white"):
        intellistock_logger.log(msg, color, service="SELF_LEARNING")
except Exception:                                    # pragma: no cover
    def _log(msg, color="white"):
        print(f"[SELF_LEARNING] {msg}")

from self_learning import store
from self_learning.pipeline import process_backtest_document

_TERMINAL = frozenset({"completed", "complete", "finished", "done"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _process(conn, doc, config) -> None:
    result = process_backtest_document(
        doc, detected_at=_now_iso(),
        variance_threshold=float(config.get("variance_threshold", 0.95)),
        variance_min_n=int(config.get("variance_min_n", 30)),
    )
    if not result["observations"]:
        return
    written = store.put_observations(conn, result["observations"])
    store.put_funnel(conn, result["run_id"], result["summary"],
                     target=result["target"], observed_at=_now_iso())
    store.put_findings(conn, result["findings"])
    summary = result["summary"]
    # Every lever in this project that shipped without its own log line became
    # unprovable. This one announces itself.
    _log(
        f"OBSERVED run {result['run_id']} target={result['target']} "
        f"observations={written} decided={summary['decided']} "
        f"executed={summary['executed']} refused={summary['refused']} "
        f"findings={len(result['findings'])}",
        "cyan",
    )
    for finding in result["findings"]:
        _log(f"FINDING [{finding.severity}] {finding.title}", "yellow")


def _is_running(conn) -> bool:
    try:
        doc = r.db(DB_NAME).table("EngineControl").get(
            "self_learning_engine").run(conn)
    except Exception:
        return True
    return bool((doc or {}).get("running", True))


def main() -> None:
    conn = store.get_conn()
    store.ensure_tables(conn)
    _log("Self-learning engine started (Phase 1: observe-only)", "green")
    seen = set()
    while True:
        try:
            if not _is_running(conn):
                time.sleep(10)
                continue
            config = store.get_config(conn)
            if not config.get("enabled", True):
                time.sleep(30)
                continue
            rows = list(r.db(DB_NAME).table("BacktestResults")
                        .pluck("id", "status").run(conn))
            for row in rows:
                rid = row.get("id")
                if rid in seen:
                    continue
                if str(row.get("status") or "").strip().lower() not in _TERMINAL:
                    continue
                doc = r.db(DB_NAME).table("BacktestResults").get(rid).run(conn)
                if doc:
                    _process(conn, doc, config)
                seen.add(rid)
            time.sleep(30)
        except Exception as exc:                      # pragma: no cover
            _log(f"loop error: {type(exc).__name__}: {exc}", "red")
            time.sleep(30)
            try:
                conn = store.get_conn()
            except Exception:
                pass


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Verify the daemon imports cleanly**

Run: `cd backend && python3 -c "import ast,sys; ast.parse(open('engines/self_learning_engine.py').read()); print('parsed ok')"`
Expected: `parsed ok`

- [ ] **Step 8: Commit**

```bash
git add backend/self_learning/pipeline.py backend/engines/self_learning_engine.py backend/engine_control.py backend/tests/test_self_learning_pipeline.py
git commit -m "Self-learning: the observe engine, a thin shell over a pure pipeline"
```

---

### Task 10: Read API

**Files:**
- Modify: `backend/interactive_utils.py` (append `action_*` functions near the other read actions)
- Modify: `backend/api/main.py` (import + routes)
- Test: `backend/tests/test_self_learning_api_shaping.py`

**Interfaces:**
- Produces: `action_learning_findings(conn, limit=100)`, `action_learning_funnels(conn, limit=100)`, `action_learning_observations(conn, run_id, limit=500)`, `action_learning_overview(conn)`; routes `GET /learning/findings`, `/learning/funnels`, `/learning/observations/{run_id}`, `/learning/overview`
- Pure shaping lives in `self_learning/api_shape.py` so it is testable without a DB.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_self_learning_api_shaping.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.api_shape import overview


def test_overview_counts_open_findings_by_severity():
    out = overview(
        findings=[{"severity": "high", "status": "open"},
                  {"severity": "high", "status": "open"},
                  {"severity": "medium", "status": "open"},
                  {"severity": "high", "status": "closed"}],
        funnels=[], config={"mode": "observe", "enabled": True})
    assert out["open_findings"] == 3
    assert out["by_severity"] == {"high": 2, "medium": 1}


def test_overview_reports_observed_runs_and_totals():
    out = overview(findings=[],
                   funnels=[{"run_id": "1", "decided": 10, "refused": 4},
                            {"run_id": "2", "decided": 5, "refused": 1}],
                   config={"mode": "observe", "enabled": True})
    assert out["runs_observed"] == 2
    assert out["decisions_observed"] == 15
    assert out["refusals_observed"] == 5


def test_overview_surfaces_the_mode_so_the_ui_cannot_imply_autonomy():
    out = overview(findings=[], funnels=[], config={"mode": "observe", "enabled": True})
    assert out["mode"] == "observe"
    assert out["acts_autonomously"] is False


def test_overview_of_nothing_is_zeroes_not_an_error():
    out = overview(findings=[], funnels=[], config={})
    assert out["open_findings"] == 0 and out["runs_observed"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_self_learning_api_shaping.py -q -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'self_learning.api_shape'`

- [ ] **Step 3: Write the pure shaping module**

```python
# backend/self_learning/api_shape.py
"""Pure response shaping for the learning endpoints.

Kept out of api/main.py so the aggregation is unit-testable without RethinkDB —
the same reason `nexus_telemetry.py` exists.
"""
from __future__ import annotations

from collections import Counter


def overview(*, findings, funnels, config) -> dict:
    open_findings = [f for f in (findings or [])
                     if str((f or {}).get("status") or "open") == "open"]
    severities = Counter(str(f.get("severity") or "low") for f in open_findings)
    mode = str((config or {}).get("mode") or "observe")
    return {
        "mode": mode,
        # Phase 1 cannot act. Saying so explicitly stops the UI from implying
        # an autonomy that is not wired yet.
        "acts_autonomously": mode not in ("observe", ""),
        "enabled": bool((config or {}).get("enabled", True)),
        "open_findings": len(open_findings),
        "by_severity": dict(severities),
        "runs_observed": len(funnels or []),
        "decisions_observed": sum(int((f or {}).get("decided") or 0)
                                  for f in (funnels or [])),
        "refusals_observed": sum(int((f or {}).get("refused") or 0)
                                 for f in (funnels or [])),
    }
```

- [ ] **Step 4: Add the DB-backed actions**

Append to `backend/interactive_utils.py`:

```python
# ── Self-learning subsystem reads (Phase 1: observe-only) ──────────────────
def action_learning_findings(conn, limit=100):
    from self_learning import store
    store.ensure_tables(conn)
    return {"findings": store.list_findings(conn, limit=limit)}


def action_learning_funnels(conn, limit=100):
    from self_learning import store
    store.ensure_tables(conn)
    return {"funnels": store.list_funnels(conn, limit=limit)}


def action_learning_observations(conn, run_id, limit=500):
    from self_learning import store
    store.ensure_tables(conn)
    return {"observations": store.list_observations(conn, run_id, limit=limit)}


def action_learning_overview(conn):
    from self_learning import store
    from self_learning.api_shape import overview
    store.ensure_tables(conn)
    return overview(findings=store.list_findings(conn, limit=500),
                    funnels=store.list_funnels(conn, limit=500),
                    config=store.get_config(conn))
```

- [ ] **Step 5: Add the routes**

In `backend/api/main.py`, add to the `interactive_utils` import list:
`action_learning_findings, action_learning_funnels, action_learning_observations, action_learning_overview,`

and add routes next to the other read endpoints:

```python
@app.get("/learning/overview", response_class=JSONResponse)
def api_learning_overview(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Headline counters for the Learning tab."""
    return _run(action_learning_overview, conn)


@app.get("/learning/findings", response_class=JSONResponse)
def api_learning_findings(limit: int = 100, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Findings raised by the self-learning guards, newest first."""
    return _run(action_learning_findings, conn, limit)


@app.get("/learning/funnels", response_class=JSONResponse)
def api_learning_funnels(limit: int = 100, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Per-run decision/execution/refusal counts."""
    return _run(action_learning_funnels, conn, limit)


@app.get("/learning/observations/{run_id}", response_class=JSONResponse)
def api_learning_observations(run_id: str, limit: int = 500, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Decision-level observations for one run."""
    return _run(action_learning_observations, conn, run_id, limit)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/test_self_learning_api_shaping.py -q -p no:randomly`
Expected: PASS (4 passed)

- [ ] **Step 7: Commit**

```bash
git add backend/self_learning/api_shape.py backend/interactive_utils.py backend/api/main.py backend/tests/test_self_learning_api_shaping.py
git commit -m "Self-learning: read API for the Learning tab"
```

---

### Task 11: Historical backfill script

**Files:**
- Create: `scripts/backfill_learning_observations.py`

**Interfaces:**
- Consumes: `process_backtest_document`, `store`
- Produces: a CLI: `python3 scripts/backfill_learning_observations.py [--limit N] [--apply]`, dry-run by default

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""Backfill LearningObservations from BacktestResults already on disk.

Phase 1's sources are documents that already exist, so the subsystem starts with
real history instead of an empty table. Writes are content-keyed and idempotent,
so re-running this is safe.

    python3 scripts/backfill_learning_observations.py            # dry run
    python3 scripts/backfill_learning_observations.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "backend"))

from rethinkdb import RethinkDB                                   # noqa: E402

from self_learning import store                                   # noqa: E402
from self_learning.pipeline import process_backtest_document      # noqa: E402

r = RethinkDB()
DB_NAME = "IntelliStock"
_TERMINAL = frozenset({"completed", "complete", "finished", "done"})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0,
                        help="process at most N runs (0 = all)")
    parser.add_argument("--apply", action="store_true",
                        help="write; omit for a dry run")
    args = parser.parse_args()

    conn = store.get_conn()
    store.ensure_tables(conn)
    now = datetime.now(timezone.utc).isoformat()

    rows = list(r.db(DB_NAME).table("BacktestResults").pluck("id", "status").run(conn))
    done = [row for row in rows
            if str(row.get("status") or "").strip().lower() in _TERMINAL]
    done.sort(key=lambda d: str(d.get("id")), reverse=True)
    if args.limit:
        done = done[:args.limit]

    print(f"{len(done)} completed run(s) to process "
          f"({'APPLY' if args.apply else 'DRY RUN'})")
    total_obs = total_find = 0
    for row in done:
        doc = r.db(DB_NAME).table("BacktestResults").get(row["id"]).run(conn)
        if not doc:
            continue
        result = process_backtest_document(doc, detected_at=now)
        if not result["observations"]:
            continue
        total_obs += len(result["observations"])
        total_find += len(result["findings"])
        summary = result["summary"]
        print(f"  run {result['run_id']:>8}  {result['target']:<34} "
              f"obs={len(result['observations']):>5} "
              f"buys={summary['buy_executed']}/{summary['buy_decided']} "
              f"findings={len(result['findings'])}")
        for finding in result["findings"]:
            print(f"      [{finding.severity}] {finding.title}")
        if args.apply:
            store.put_observations(conn, result["observations"])
            store.put_funnel(conn, result["run_id"], summary,
                             target=result["target"], observed_at=now)
            store.put_findings(conn, result["findings"])

    print(f"\ntotal observations={total_obs} findings={total_find}")
    if not args.apply:
        print("DRY RUN — nothing written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify it parses and its help works**

Run: `python3 -c "import ast; ast.parse(open('scripts/backfill_learning_observations.py').read()); print('parsed ok')"`
Expected: `parsed ok`

- [ ] **Step 3: Commit**

```bash
git add scripts/backfill_learning_observations.py
git commit -m "Self-learning: backfill so the subsystem starts with real history"
```

---

### Task 12: Web tab

**Files:**
- Create: `frontend/src/views/LearningView.vue`
- Modify: `frontend/src/router/index.js`
- Modify: the authenticated nav component (find it with `grep -rn "agent-runs" frontend/src --include=*.vue`)

**Interfaces:**
- Consumes: `GET /learning/overview`, `/learning/findings`, `/learning/funnels`
- Produces: route `name: 'learning'`, path `/learning`

- [ ] **Step 1: Read two existing views to copy the house style**

Run: `sed -n '1,80p' frontend/src/views/NexusView.vue` and `grep -rn "agent-runs" frontend/src --include=*.vue`
Match the existing API helper, auth handling, loading/empty states, and Tailwind class vocabulary. Do not introduce a new HTTP client or state library.

- [ ] **Step 2: Build the view with the three specified sections**

`LearningView.vue` renders, in this order:
1. **Header** — mode badge (must read "observe-only" while `acts_autonomously` is false), enabled state, counters from `/learning/overview`.
2. **Pending approvals** — present as a section, showing an explicit empty state: "No approvals — the subsystem is observe-only in Phase 1." Do not hide the section; its absence would misrepresent the roadmap.
3. **Findings & reports** — cards from `/learning/findings`: severity chip, title, detail, target, detected_at, evidence expandable.
4. **Thread detail (ladder stepper)** — clicking a finding opens a vertical stepper with the six rungs (`PROPOSED`, `BACKTEST`, `SHADOW`, `PAPER`, `LIVE_CAPPED`, `LIVE_FULL`). In Phase 1 only the detection step is populated and the remaining rungs render as locked/greyed with the label "not reached — Phase 1 observes only".
5. **Observed runs** — table from `/learning/funnels`: run id, target, decided, executed, refused, buy conversion %.

- [ ] **Step 3: Register the route**

In `frontend/src/router/index.js`, alongside the other authenticated routes:

```js
  {
    path: '/learning',
    name: 'learning',
    component: () => import('../views/LearningView.vue'),
    meta: { requiresAuth: true },
  },
```

- [ ] **Step 4: Add the nav entry**

Add a "Learning" entry to the authenticated nav next to Nexus, matching the existing item markup exactly.

- [ ] **Step 5: Verify the build**

Run: `cd frontend && npm run build`
Expected: build succeeds with no new errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/LearningView.vue frontend/src/router/index.js
git add -u frontend/src
git commit -m "Self-learning: web tab with findings, approvals and the ladder stepper"
```

---

### Task 13: Mobile tab

**Files:**
- Create: `mobile/lib/features/learning/learning_screen.dart`, `mobile/lib/features/learning/learning_providers.dart`, `mobile/lib/features/learning/learning_models.dart`
- Modify: `mobile/lib/core/router/router.dart`, `mobile/lib/core/router/more_sheet.dart`

**Interfaces:**
- Consumes: the same three endpoints
- Produces: route `/learning`, an entry in the More sheet

- [ ] **Step 1: Read an existing feature to copy the pattern**

Run: `ls mobile/lib/features/nexus && sed -n '1,60p' mobile/lib/features/nexus/*_screen.dart`
Match the existing Riverpod provider style, the shared API client, `AppColors`, and the loading/empty/error widgets. Do not add a new HTTP or state package.

- [ ] **Step 2: Build the screen with the same three sections**

Pending approvals (with the explicit observe-only empty state), Findings & reports, and the tappable finding → vertical ladder stepper. Reuse the shared card and chip widgets rather than new ones.

- [ ] **Step 3: Register the route and the More-sheet entry**

Add a `/learning` route in `router.dart` as a detail route pushed over the shell (not a new bottom-tab branch — all five slots are taken), and an entry in `more_sheet.dart` next to Backtests.

- [ ] **Step 4: Verify**

Run: `cd mobile && flutter analyze`
Expected: no new issues.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/features/learning mobile/lib/core/router/router.dart mobile/lib/core/router/more_sheet.dart
git commit -m "Self-learning: mobile tab in the More sheet"
```

---

### Task 14: Full-suite verification

- [ ] **Step 1: Run the whole backend suite**

Run: `python3 -m pytest backend/tests -q -p no:randomly`
Expected: the failure **set** is exactly the 19 documented baseline failures — `test_adv_exit_discipline_findings` ×11, `test_core_sleeve_adversarial` ×7, `test_zz_adversarial_sweep` ×1 — plus zero new ones. If any new test fails, fix it before proceeding; do not adjust the baseline.

- [ ] **Step 2: Confirm no strategy file was touched**

Run: `git diff --name-only origin/main...HEAD -- backend/strategies/`
Expected: empty output. Phase 1 must not modify any strategy.

- [ ] **Step 3: Commit any fixes and push**

```bash
git push origin main
python3 scripts/check_deployed_code.py
```

---

## Self-Review

**Spec coverage:** §2.1 modules → Tasks 1–10 (`hypotheses`, `experiments`, `noise`, `execution_proof`, `ladder`, `permissions`, `budget`, `actions/`, `llm` are Phase 2+ by design and correctly absent). §2.2 engine → Task 9. §2.3 strategy-agnosticism → Task 5 + Task 2 (`primary_strategy` derived, never hardcoded) + Task 14 Step 2. §3.0 sources → Task 2/3. §3.1 tables → Task 8 (Phase 1 subset: Observations, Rollups, Findings, Funnels, Config; Hypotheses/Experiments/Verdicts/Actions/Reports/NoiseFloor/BudgetLedger arrive with the phases that write them). §3.2 volume → Tasks 7 + 2 (`funnel_summary` is an aggregate). §3.3 Guard 3 → Task 4. Guards 1 and 2 are Phase 2 and are correctly not in this plan. §6.1/6.2 UI → Tasks 12/13.

**Known gap, deliberate:** `LearningOutcomes` (forward-return resolution via `benchmark_alpha/outcomes.py`) is specified in §3.1 but **not** in this plan. It requires the exchange-authoritative calendar plus price series per symbol, which is a substantial task of its own and is the natural first task of the Phase 2 plan. Phase 1 records the refusals; Phase 2 prices them. This is called out rather than silently dropped.

**Placeholder scan:** no TBDs; every code step carries runnable code. Tasks 12 and 13 intentionally specify sections and behaviour rather than full component source, because both must match house style discovered by reading neighbouring files — the read step is the first step of each.

**Type consistency:** `Observation`/`Finding`/`VarianceReport`/`Lever` field names are used identically in Tasks 1–11. `process_backtest_document` returns the same five keys in Tasks 9 and 11. `funnel_summary` returns the same five keys in Tasks 2, 6, 9, 10. Store function names in Task 8 match their call sites in Tasks 9, 10, 11.
