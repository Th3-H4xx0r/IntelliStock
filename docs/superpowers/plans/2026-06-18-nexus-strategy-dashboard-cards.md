# Nexus Strategy Dashboard Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new "Strategy" section to the mobile dashboard with seven read-only cards that surface the `graph_nexus_analysis` bot's live state (market trends active + recently-ended, reversal watch, backfill queue, discovered stocks, bot rationale, outcome scorecard, momentum watchlist).

**Architecture:** New read-only backend endpoints in `backend/api/main.py` (+ two read-only actions in `interactive_utils.py`, one pure helper module `nexus_telemetry.py`) read from RethinkDB tables and the persisted `NexusStrategyCache` the strategy already writes — **zero edits to `graph_nexus_analysis.py`**. Flutter cards follow the existing Riverpod provider → `DashboardRepository` → `ApiClient` pattern; each card self-hides when its data is empty.

**Tech Stack:** Python 3 / FastAPI / RethinkDB (pytest); Flutter / Dart / Riverpod (flutter test + goldens). Spec: `docs/superpowers/specs/2026-06-18-nexus-strategy-dashboard-cards-design.md`.

---

## File Structure

**Backend**
- Create `backend/nexus_telemetry.py` — pure helpers: `summarize_outcomes(docs)`, `normalize_backfill_item(raw)`, `dedupe_latest_contexts(docs, limit)`, `newest_watchlist(watchlist, limit)`. No DB, no FastAPI — fully unit-testable.
- Create `backend/tests/test_nexus_telemetry.py` — unit tests for the helpers.
- Modify `backend/interactive_utils.py` — add `action_nexus_trade_contexts(...)` and `action_nexus_outcome_stats(...)` (thin DB reads that call the pure helpers).
- Modify `backend/api/main.py` — extend `GET /brokerages/{id}/trends` (status+limit), add 4 endpoints (`backfill-queue`, `momentum-watchlist`, `trade-contexts`, `nexus-outcomes`).

**Mobile**
- Create `mobile/lib/features/dashboard/data/nexus_models.dart` — DTOs: `MarketTrend`, `NexusTrendsView`, `BackfillItem`, `DiscoveredStock`, `TradeRationale`, `OutcomeStats`, `WatchlistSummary`.
- Create `mobile/lib/features/dashboard/application/nexus_strategy_controller.dart` — Riverpod providers.
- Create `mobile/lib/features/dashboard/presentation/strategy_section.dart` — `StrategySection` + 7 card widgets.
- Modify `mobile/lib/features/dashboard/data/dashboard_repository.dart` — add repo methods.
- Modify `mobile/lib/features/dashboard/presentation/dashboard_screen.dart` — insert `StrategySection`.
- Create `mobile/test/features/dashboard/nexus_models_test.dart` — model `fromJson` tests.
- Create `mobile/test/features/dashboard/strategy_trends_card_golden_test.dart` (+ goldens) — Market Trends card golden.

---

## Task 1: Pure telemetry helpers (backend)

**Files:**
- Create: `backend/nexus_telemetry.py`
- Test: `backend/tests/test_nexus_telemetry.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_nexus_telemetry.py
from nexus_telemetry import (
    summarize_outcomes,
    normalize_backfill_item,
    newest_watchlist,
)


def test_summarize_outcomes_empty():
    s = summarize_outcomes([])
    assert s == {"hit_rate": 0.0, "n": 0, "n_correct": 0, "avg_return": 0.0, "recent": []}


def test_summarize_outcomes_hit_rate_and_direction():
    docs = [
        {"symbol": "A", "action_intent": "buy", "latest_return": 5.0,
         "latest_observation_date": "2026-06-10", "entry_date": "2026-06-01"},
        {"symbol": "B", "action_intent": "buy", "latest_return": -2.0,
         "latest_observation_date": "2026-06-11", "entry_date": "2026-06-02"},
        {"symbol": "C", "action_intent": "sell", "latest_return": -3.0,
         "latest_observation_date": "2026-06-12", "entry_date": "2026-06-03"},
        {"symbol": "D", "action_intent": "backfill_rotation_buy", "latest_return": 1.0,
         "latest_observation_date": "2026-06-13", "entry_date": "2026-06-04"},
    ]
    s = summarize_outcomes(docs)
    # buy+pos=correct, buy+neg=wrong, sell+neg=correct, *buy+pos=correct → 3/4
    assert s["n"] == 4
    assert s["n_correct"] == 3
    assert abs(s["hit_rate"] - 0.75) < 1e-9
    assert abs(s["avg_return"] - 0.25) < 1e-9
    # recent sorted by latest_observation_date desc, newest first
    assert [r["symbol"] for r in s["recent"]][:2] == ["D", "C"]


def test_normalize_backfill_item_score_fallback_and_defaults():
    a = normalize_backfill_item({"ticker": "nvda", "raw_net_score": 1.4, "n_paths": 3,
                                 "source": "propagation", "priority": 1})
    assert a == {"ticker": "NVDA", "score": 1.4, "n_paths": 3,
                 "source": "propagation", "priority": True}
    b = normalize_backfill_item({"ticker": "amd", "score": 0.9})
    assert b["ticker"] == "AMD" and b["score"] == 0.9 and b["n_paths"] == 0
    assert b["source"] == "" and b["priority"] is False
    assert normalize_backfill_item({"is_priority": True, "ticker": "t"})["priority"] is True


def test_newest_watchlist_sorts_and_caps():
    wl = {
        "AAA": {"first_seen_bar": 10, "ret_20d": 1.1},
        "BBB": {"first_seen_bar": 30, "ret_20d": 2.2},
        "CCC": {"first_seen_bar": 20, "ret_20d": 3.3},
    }
    out = newest_watchlist(wl, limit=2)
    assert [e["symbol"] for e in out] == ["BBB", "CCC"]
    assert out[0] == {"symbol": "BBB", "first_seen_bar": 30, "ret_20d": 2.2}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_nexus_telemetry.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nexus_telemetry'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/nexus_telemetry.py
"""Pure, DB-free helpers for nexus strategy telemetry endpoints.

Kept separate from api/main.py and the strategy so the shaping/aggregation
logic is unit-testable without RethinkDB or a running strategy.
"""
from __future__ import annotations


def _is_long(intent: str) -> bool:
    s = (intent or "").lower()
    return "buy" in s or "long" in s


def _is_short(intent: str) -> bool:
    s = (intent or "").lower()
    return "sell" in s or "short" in s


def summarize_outcomes(docs: list) -> dict:
    """Aggregate GraphNexusTradeOutcomes docs into a scorecard.

    A signal is "correct" when its direction matched the realized return:
    long & return>0, or short & return<0.
    """
    docs = [d for d in (docs or []) if isinstance(d, dict)]
    n = len(docs)
    if n == 0:
        return {"hit_rate": 0.0, "n": 0, "n_correct": 0, "avg_return": 0.0, "recent": []}
    n_correct = 0
    total_ret = 0.0
    for d in docs:
        intent = str(d.get("action_intent") or "")
        ret = float(d.get("latest_return") or 0.0)
        total_ret += ret
        if (_is_long(intent) and ret > 0) or (_is_short(intent) and ret < 0):
            n_correct += 1
    recent = sorted(
        docs,
        key=lambda d: str(d.get("latest_observation_date") or d.get("entry_date") or ""),
        reverse=True,
    )[:8]
    recent_view = [
        {
            "symbol": str(d.get("symbol") or "").upper(),
            "action_intent": str(d.get("action_intent") or ""),
            "latest_return": float(d.get("latest_return") or 0.0),
            "dominant_event_type": str(d.get("dominant_event_type") or ""),
            "entry_date": str(d.get("entry_date") or ""),
        }
        for d in recent
    ]
    return {
        "hit_rate": n_correct / n,
        "n": n,
        "n_correct": n_correct,
        "avg_return": total_ret / n,
        "recent": recent_view,
    }


def normalize_backfill_item(raw: dict) -> dict:
    """Map a _backfill_queue item to a stable view, tolerating key drift
    (raw_net_score|score, priority|is_priority)."""
    raw = raw or {}
    score = raw.get("raw_net_score")
    if score is None:
        score = raw.get("score")
    priority = raw.get("priority")
    if priority is None:
        priority = raw.get("is_priority")
    return {
        "ticker": str(raw.get("ticker") or "").upper(),
        "score": float(score or 0.0),
        "n_paths": int(raw.get("n_paths") or 0),
        "source": str(raw.get("source") or ""),
        "priority": bool(priority),
    }


def newest_watchlist(watchlist: dict, limit: int = 12) -> list:
    """Newest entries of the _momentum_watchlist dict, by first_seen_bar desc."""
    if not isinstance(watchlist, dict):
        return []
    rows = []
    for sym, meta in watchlist.items():
        meta = meta if isinstance(meta, dict) else {}
        rows.append({
            "symbol": str(sym).upper(),
            "first_seen_bar": int(meta.get("first_seen_bar") or 0),
            "ret_20d": float(meta.get("ret_20d") or 0.0),
        })
    rows.sort(key=lambda e: e["first_seen_bar"], reverse=True)
    return rows[: max(0, int(limit))]


def dedupe_latest_contexts(docs: list, limit: int = 40) -> list:
    """Latest GraphNexusTradeContexts doc per symbol (docs assumed newest-first),
    shaped for the rationale card with a truncated reason."""
    seen = set()
    out = []
    for d in (docs or []):
        if not isinstance(d, dict):
            continue
        sym = str(d.get("symbol") or "").upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append({
            "symbol": sym,
            "reason": str(d.get("reason") or "")[:240],
            "dominant_event_type": str(d.get("dominant_event_type") or ""),
            "action_intent": str(d.get("action_intent") or ""),
            "score": float(d.get("score") or 0.0),
            "date_key": str(d.get("date_key") or d.get("entry_date") or ""),
        })
        if len(out) >= max(1, int(limit)):
            break
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_nexus_telemetry.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus_telemetry.py backend/tests/test_nexus_telemetry.py
git commit -m "feat(backend): pure nexus telemetry helpers (outcomes/backfill/watchlist/contexts)"
```

---

## Task 2: Read-only DB actions for contexts + outcomes (backend)

**Files:**
- Modify: `backend/interactive_utils.py` (add two actions near `action_list_discovered_stocks`, ~line 6807)

**Context:** `interactive_utils.py` already defines table-name constants and uses module-level `r` + `DB_NAME` (e.g. `_NEXUS_DISCOVERED_TABLE`, `action_list_trends`). The trade-context/outcome tables are `GraphNexusTradeContexts` / `GraphNexusTradeOutcomes` (confirmed in `graph_nexus_analysis.py:334-335`, written at `~9200-9202`). These actions return `{}`-safe dicts; the API layer wraps them with `_run`.

- [ ] **Step 1: Run gitnexus impact (read-only file, but follow CLAUDE.md before editing)**

Run via MCP: `gitnexus_impact({target: "action_list_discovered_stocks", direction: "upstream"})` (nearest existing symbol in the edit region). Expected: LOW risk, `affected_processes` limited to API list flows. Report the blast radius. We are ADDING new functions, not modifying existing ones, so risk is informational.

- [ ] **Step 2: Add the two actions**

Insert after `action_remove_discovered_stock` (after line 6825):

```python
_NEXUS_TRADE_CONTEXTS_TABLE = "GraphNexusTradeContexts"
_NEXUS_TRADE_OUTCOMES_TABLE = "GraphNexusTradeOutcomes"


def _ensure_nexus_trade_tables(conn):
    tables = r.db(DB_NAME).table_list().run(conn)
    if _NEXUS_TRADE_CONTEXTS_TABLE not in tables:
        r.db(DB_NAME).table_create(_NEXUS_TRADE_CONTEXTS_TABLE).run(conn)
    if _NEXUS_TRADE_OUTCOMES_TABLE not in tables:
        r.db(DB_NAME).table_create(_NEXUS_TRADE_OUTCOMES_TABLE).run(conn)


def action_nexus_trade_contexts(conn, instance_id, limit=40):
    """Latest per-symbol bot rationale (reason + event type) for an instance.
    Read-only. Empty when the instance has no nexus trade contexts."""
    from nexus_telemetry import dedupe_latest_contexts
    if not instance_id:
        return {"contexts": []}
    _ensure_nexus_trade_tables(conn)
    cursor = (
        r.db(DB_NAME)
        .table(_NEXUS_TRADE_CONTEXTS_TABLE)
        .filter(lambda doc: doc["instance_id"] == instance_id)
        .order_by(r.desc("date_key"))
        .limit(400)
        .run(conn)
    )
    return {"contexts": dedupe_latest_contexts(list(cursor), limit=limit)}


def action_nexus_outcome_stats(conn, instance_id):
    """Aggregate signal→outcome hit-rate for an instance. Read-only."""
    from nexus_telemetry import summarize_outcomes
    if not instance_id:
        return summarize_outcomes([])
    _ensure_nexus_trade_tables(conn)
    cursor = (
        r.db(DB_NAME)
        .table(_NEXUS_TRADE_OUTCOMES_TABLE)
        .filter(lambda doc: doc["instance_id"] == instance_id)
        .limit(2000)
        .run(conn)
    )
    return summarize_outcomes(list(cursor))
```

- [ ] **Step 3: Sanity-check import + syntax**

Run: `cd backend && python3 -c "import interactive_utils; print('ok', hasattr(interactive_utils, 'action_nexus_outcome_stats'))"`
Expected: `ok True` (no import error).

- [ ] **Step 4: Commit**

```bash
git add backend/interactive_utils.py
git commit -m "feat(backend): read-only nexus trade-context + outcome-stats actions"
```

---

## Task 3: Backend endpoints (extend trends + 4 new) (backend)

**Files:**
- Modify: `backend/api/main.py` — `api_brokerage_trends` (line 3813); add new routes after `api_brokerage_nexus_momentum` (after line 3919).

**Context:** Endpoints follow the established pattern: resolve instance via `_resolve_instance_for_brokerage(conn, brokerage_id)`, return empty on no instance, never 500. Table-backed reads use `_run(action, conn, ...)`. Cache reads mirror `api_brokerage_nexus_momentum` (line 3901) using `load_strategy_cache_from_db(conn, _r_auth, iid, "graph_nexus_analysis")`.

- [ ] **Step 1: Run gitnexus impact on the symbol being modified**

Run via MCP: `gitnexus_impact({target: "api_brokerage_trends", direction: "upstream"})`.
Expected: LOW risk (a leaf FastAPI handler; `affected_processes` should be `[]` or API-only). Report blast radius. If HIGH/CRITICAL or any trade-path process appears, STOP and surface to the user before editing.

- [ ] **Step 2: Extend the trends endpoint to accept status + limit**

Replace `api_brokerage_trends` (lines 3813-3820):

```python
@app.get("/brokerages/{brokerage_id}/trends", response_class=JSONResponse)
def api_brokerage_trends(brokerage_id: str, status: str = "active", limit: int = 50, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Detected market trends for the instance behind this account. `status` is
    one of active|weakening|ended (default active). Empty when no instance is
    linked. Read-only."""
    iid = _resolve_instance_for_brokerage(conn, brokerage_id)
    if not iid:
        return {"trends": [], "count": 0}
    res = _run(action_list_trends, conn, iid, status)
    trends = (res or {}).get("trends") or []
    n = max(1, min(int(limit or 50), 100))
    trends = trends[:n]
    return {"trends": trends, "count": len(trends)}
```

- [ ] **Step 3: Add the four new endpoints**

Insert after `api_brokerage_nexus_momentum` (after line 3919):

```python
@app.get("/brokerages/{brokerage_id}/backfill-queue", response_class=JSONResponse)
def api_brokerage_backfill_queue(brokerage_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Pending buy candidates queued by the nexus strategy (read-only cache).
    Empty unless the instance runs graph_nexus_analysis."""
    iid = _resolve_instance_for_brokerage(conn, brokerage_id)
    if not iid:
        return {"queue": [], "count": 0}
    try:
        from strategy_cache_persistence import load_strategy_cache_from_db
        from nexus_telemetry import normalize_backfill_item
        cache = load_strategy_cache_from_db(conn, _r_auth, iid, "graph_nexus_analysis")
        raw = (cache or {}).get("_backfill_queue") or []
        items = [normalize_backfill_item(q) for q in raw if isinstance(q, dict)]
        items = [q for q in items if q["ticker"]]
        return {"queue": items, "count": len(items)}
    except Exception:
        return {"queue": [], "count": 0}


@app.get("/brokerages/{brokerage_id}/momentum-watchlist", response_class=JSONResponse)
def api_brokerage_momentum_watchlist(brokerage_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Count + newest names in the nexus momentum watchlist (read-only cache).
    Count saturates at the persist cap (500). Empty unless momentum_watchlist_enabled."""
    iid = _resolve_instance_for_brokerage(conn, brokerage_id)
    if not iid:
        return {"count": 0, "newest": []}
    try:
        from strategy_cache_persistence import load_strategy_cache_from_db
        from nexus_telemetry import newest_watchlist
        cache = load_strategy_cache_from_db(conn, _r_auth, iid, "graph_nexus_analysis")
        wl = (cache or {}).get("_momentum_watchlist") or {}
        return {"count": len(wl), "newest": newest_watchlist(wl, limit=12)}
    except Exception:
        return {"count": 0, "newest": []}


@app.get("/brokerages/{brokerage_id}/trade-contexts", response_class=JSONResponse)
def api_brokerage_trade_contexts(brokerage_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Latest per-symbol bot rationale for the instance behind this account.
    Empty when no instance is linked. Read-only."""
    iid = _resolve_instance_for_brokerage(conn, brokerage_id)
    if not iid:
        return {"contexts": []}
    return _run(action_nexus_trade_contexts, conn, iid, 40)


@app.get("/brokerages/{brokerage_id}/nexus-outcomes", response_class=JSONResponse)
def api_brokerage_nexus_outcomes(brokerage_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Signal→outcome scorecard (hit-rate) for the instance behind this account.
    Empty when no instance is linked. Read-only."""
    iid = _resolve_instance_for_brokerage(conn, brokerage_id)
    if not iid:
        return {"hit_rate": 0.0, "n": 0, "n_correct": 0, "avg_return": 0.0, "recent": []}
    return _run(action_nexus_outcome_stats, conn, iid)
```

Confirm `action_nexus_trade_contexts` and `action_nexus_outcome_stats` are imported in `api/main.py`. Check how `action_list_trends` is imported (search `from interactive_utils import` near the top) and add the two new names to that import list.

- [ ] **Step 4: Verify the app imports cleanly + detect changes**

Run: `cd backend && python3 -c "import api.main as m; print('routes ok')"`
Expected: `routes ok` (no import/syntax error).

Run via MCP: `gitnexus_detect_changes()` — confirm only `api_brokerage_trends` (modified) + the new handlers appear, and no trade/decision-path process is affected. The stale-index symbol attribution is line-drift noise; trust `affected_processes` + risk level.

- [ ] **Step 5: Commit**

```bash
git add backend/api/main.py
git commit -m "feat(backend): brokerage-scoped nexus telemetry endpoints (trends status, backfill-queue, momentum-watchlist, trade-contexts, nexus-outcomes)"
```

---

## Task 4: Mobile DTOs (nexus_models.dart)

**Files:**
- Create: `mobile/lib/features/dashboard/data/nexus_models.dart`
- Test: `mobile/test/features/dashboard/nexus_models_test.dart`

**Context:** Models are plain classes with a `fromJson` factory and defensive `(json['x'] as num?)?.toDouble()` parsing — mirrors `AccountPosition` (`dashboard_repository.dart:84-112`).

- [ ] **Step 1: Write the failing test**

```dart
// mobile/test/features/dashboard/nexus_models_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock/features/dashboard/data/nexus_models.dart';

void main() {
  test('MarketTrend.fromJson parses direction/strength/tickers', () {
    final t = MarketTrend.fromJson({
      'id': 'inst_ai',
      'name': 'AI Rally',
      'status': 'active',
      'direction': 'bullish',
      'strength': 0.78,
      'affected_tickers': ['NVDA', 'AMD'],
      'reversal_articles': [],
      'end_date': null,
    });
    expect(t.name, 'AI Rally');
    expect(t.bullish, isTrue);
    expect(t.strength, 0.78);
    expect(t.tickers, ['NVDA', 'AMD']);
    expect(t.hasReversal, isFalse);
  });

  test('MarketTrend.endedLabelSource prefers ended_at then end_date', () {
    final t = MarketTrend.fromJson({
      'name': 'X', 'status': 'ended', 'direction': 'bearish',
      'ended_at': '2026-06-16T00:00:00', 'end_date': '2026-06-10',
    });
    expect(t.endedAt, '2026-06-16T00:00:00');
  });

  test('BackfillItem.fromJson reads normalized server shape', () {
    final b = BackfillItem.fromJson(
        {'ticker': 'NVDA', 'score': 1.4, 'n_paths': 3, 'source': 'propagation', 'priority': true});
    expect(b.ticker, 'NVDA');
    expect(b.priority, isTrue);
    expect(b.nPaths, 3);
  });

  test('OutcomeStats.fromJson parses hit rate + recent', () {
    final s = OutcomeStats.fromJson({
      'hit_rate': 0.6, 'n': 10, 'n_correct': 6, 'avg_return': 1.2,
      'recent': [
        {'symbol': 'A', 'action_intent': 'buy', 'latest_return': 2.0,
         'dominant_event_type': 'm_and_a', 'entry_date': '2026-06-01'}
      ],
    });
    expect(s.hitRate, 0.6);
    expect(s.n, 10);
    expect(s.recent.single.symbol, 'A');
  });

  test('WatchlistSummary.fromJson parses count + newest', () {
    final w = WatchlistSummary.fromJson({
      'count': 42,
      'newest': [
        {'symbol': 'NVDA', 'first_seen_bar': 30, 'ret_20d': 1.1}
      ],
    });
    expect(w.count, 42);
    expect(w.newest.single.symbol, 'NVDA');
  });

  test('DiscoveredStock + TradeRationale fromJson', () {
    final d = DiscoveredStock.fromJson(
        {'ticker': 'AVGO', 'source': 'sector_peer', 'source_ticker': 'NVDA', 'discovered_at': '2026-06-15'});
    expect(d.ticker, 'AVGO');
    expect(d.sourceTicker, 'NVDA');
    final r = TradeRationale.fromJson(
        {'symbol': 'NVDA', 'reason': 'capex', 'dominant_event_type': 'supply_disruption', 'score': 3.0});
    expect(r.symbol, 'NVDA');
    expect(r.reason, 'capex');
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && flutter test test/features/dashboard/nexus_models_test.dart`
Expected: FAIL — `Error: Couldn't resolve the package 'nexus_models.dart'` / type not found.

(Note: the package import prefix is `package:intellistock/...`. Confirm the package name in `mobile/pubspec.yaml` `name:` and use it; if it differs, update the import.)

- [ ] **Step 3: Write minimal implementation**

```dart
// mobile/lib/features/dashboard/data/nexus_models.dart

/// A market trend tracked by the nexus strategy (GraphNexusMarketTrends).
class MarketTrend {
  const MarketTrend({
    required this.id,
    required this.name,
    required this.status,
    required this.direction,
    required this.strength,
    required this.tickers,
    required this.sectors,
    required this.reversalCount,
    required this.endedAt,
  });

  final String id;
  final String name;
  final String status; // active | weakening | ended
  final String direction; // bullish | bearish
  final double strength; // 0..1
  final List<String> tickers;
  final List<String> sectors;
  final int reversalCount;
  final String? endedAt;

  bool get bullish => direction.toLowerCase() == 'bullish';
  bool get hasReversal => reversalCount > 0 || status.toLowerCase() == 'weakening';

  static List<String> _strs(dynamic v) =>
      (v as List? ?? const []).map((e) => e.toString()).toList();

  factory MarketTrend.fromJson(Map<String, dynamic> json) => MarketTrend(
        id: (json['id'] as String? ?? ''),
        name: (json['name'] as String? ?? ''),
        status: (json['status'] as String? ?? 'active'),
        direction: (json['direction'] as String? ?? 'bullish'),
        strength: (json['strength'] as num?)?.toDouble() ?? 0,
        tickers: _strs(json['affected_tickers']),
        sectors: _strs(json['affected_sectors']),
        reversalCount: (json['reversal_articles'] as List? ?? const []).length,
        endedAt: (json['ended_at'] as String?) ??
            (json['end_date'] as String?) ??
            (json['last_confirmed_date'] as String?),
      );
}

/// Active + recently-ended trends for one account (two endpoint calls).
class NexusTrendsView {
  const NexusTrendsView({required this.active, required this.recentlyEnded});
  final List<MarketTrend> active;
  final List<MarketTrend> recentlyEnded;

  List<MarketTrend> get reversalWatch =>
      active.where((t) => t.hasReversal).toList();

  bool get isEmpty => active.isEmpty && recentlyEnded.isEmpty;
}

/// A pending buy candidate in the strategy's backfill queue.
class BackfillItem {
  const BackfillItem({
    required this.ticker,
    required this.score,
    required this.nPaths,
    required this.source,
    required this.priority,
  });

  final String ticker;
  final double score;
  final int nPaths;
  final String source;
  final bool priority;

  factory BackfillItem.fromJson(Map<String, dynamic> json) => BackfillItem(
        ticker: (json['ticker'] as String? ?? '').toUpperCase(),
        score: (json['score'] as num?)?.toDouble() ?? 0,
        nPaths: (json['n_paths'] as num?)?.toInt() ?? 0,
        source: (json['source'] as String? ?? ''),
        priority: (json['priority'] as bool?) ?? false,
      );
}

/// A stock the discover engine surfaced (GraphNexusDiscoveredStocks).
class DiscoveredStock {
  const DiscoveredStock({
    required this.ticker,
    required this.source,
    required this.sourceTicker,
    required this.discoveredAt,
  });

  final String ticker;
  final String source;
  final String? sourceTicker;
  final String? discoveredAt;

  factory DiscoveredStock.fromJson(Map<String, dynamic> json) => DiscoveredStock(
        ticker: (json['ticker'] as String? ?? '').toUpperCase(),
        source: (json['source'] as String? ?? ''),
        sourceTicker: json['source_ticker'] as String?,
        discoveredAt: (json['discovered_at'] as String?) ??
            (json['discovered_date'] as String?),
      );
}

/// The bot's persisted rationale for a symbol (GraphNexusTradeContexts).
class TradeRationale {
  const TradeRationale({
    required this.symbol,
    required this.reason,
    required this.eventType,
    required this.actionIntent,
    required this.score,
  });

  final String symbol;
  final String reason;
  final String eventType;
  final String actionIntent;
  final double score;

  factory TradeRationale.fromJson(Map<String, dynamic> json) => TradeRationale(
        symbol: (json['symbol'] as String? ?? '').toUpperCase(),
        reason: (json['reason'] as String? ?? ''),
        eventType: (json['dominant_event_type'] as String? ?? ''),
        actionIntent: (json['action_intent'] as String? ?? ''),
        score: (json['score'] as num?)?.toDouble() ?? 0,
      );
}

/// One realized signal outcome (for the scorecard's recent list).
class OutcomeRow {
  const OutcomeRow({
    required this.symbol,
    required this.actionIntent,
    required this.latestReturn,
    required this.eventType,
    required this.entryDate,
  });

  final String symbol;
  final String actionIntent;
  final double latestReturn;
  final String eventType;
  final String entryDate;

  bool get isLong =>
      actionIntent.toLowerCase().contains('buy') ||
      actionIntent.toLowerCase().contains('long');
  bool get correct =>
      (isLong && latestReturn > 0) || (!isLong && latestReturn < 0);

  factory OutcomeRow.fromJson(Map<String, dynamic> json) => OutcomeRow(
        symbol: (json['symbol'] as String? ?? '').toUpperCase(),
        actionIntent: (json['action_intent'] as String? ?? ''),
        latestReturn: (json['latest_return'] as num?)?.toDouble() ?? 0,
        eventType: (json['dominant_event_type'] as String? ?? ''),
        entryDate: (json['entry_date'] as String? ?? ''),
      );
}

/// Aggregate signal→outcome scorecard (GET /brokerages/{id}/nexus-outcomes).
class OutcomeStats {
  const OutcomeStats({
    required this.hitRate,
    required this.n,
    required this.nCorrect,
    required this.avgReturn,
    required this.recent,
  });

  final double hitRate; // 0..1
  final int n;
  final int nCorrect;
  final double avgReturn;
  final List<OutcomeRow> recent;

  bool get isEmpty => n == 0;

  factory OutcomeStats.fromJson(Map<String, dynamic> json) => OutcomeStats(
        hitRate: (json['hit_rate'] as num?)?.toDouble() ?? 0,
        n: (json['n'] as num?)?.toInt() ?? 0,
        nCorrect: (json['n_correct'] as num?)?.toInt() ?? 0,
        avgReturn: (json['avg_return'] as num?)?.toDouble() ?? 0,
        recent: (json['recent'] as List? ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(OutcomeRow.fromJson)
            .toList(),
      );
}

/// One newest watchlist entry.
class WatchlistEntry {
  const WatchlistEntry({required this.symbol, required this.firstSeenBar, required this.ret20d});
  final String symbol;
  final int firstSeenBar;
  final double ret20d;

  factory WatchlistEntry.fromJson(Map<String, dynamic> json) => WatchlistEntry(
        symbol: (json['symbol'] as String? ?? '').toUpperCase(),
        firstSeenBar: (json['first_seen_bar'] as num?)?.toInt() ?? 0,
        ret20d: (json['ret_20d'] as num?)?.toDouble() ?? 0,
      );
}

/// Momentum watchlist summary (GET /brokerages/{id}/momentum-watchlist).
class WatchlistSummary {
  const WatchlistSummary({required this.count, required this.newest});
  final int count;
  final List<WatchlistEntry> newest;

  bool get isEmpty => count == 0 && newest.isEmpty;

  factory WatchlistSummary.fromJson(Map<String, dynamic> json) => WatchlistSummary(
        count: (json['count'] as num?)?.toInt() ?? 0,
        newest: (json['newest'] as List? ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(WatchlistEntry.fromJson)
            .toList(),
      );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && flutter test test/features/dashboard/nexus_models_test.dart`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/features/dashboard/data/nexus_models.dart mobile/test/features/dashboard/nexus_models_test.dart
git commit -m "feat(mobile): nexus strategy DTOs (trends, backfill, discovered, rationale, outcomes, watchlist)"
```

---

## Task 5: Repository methods (dashboard_repository.dart)

**Files:**
- Modify: `mobile/lib/features/dashboard/data/dashboard_repository.dart` (add import + methods inside `DashboardRepository`, after `accountHoldings`, ~line 199)

**Context:** Mirror existing methods: `_client.get<Map<String, dynamic>>('/path', query: {...})`, then map a list field with `.whereType<Map<String, dynamic>>().map(X.fromJson)`. Errors propagate to providers, which swallow them.

- [ ] **Step 1: Add the import (top of file, after line 2)**

```dart
import 'nexus_models.dart';
```

- [ ] **Step 2: Add methods inside `DashboardRepository` (after `accountHoldings`)**

```dart
  /// GET /brokerages/{id}/trends?status=&limit= → market trends.
  Future<List<MarketTrend>> nexusTrends(String id, {String status = 'active', int limit = 50}) async {
    final data = await _client.get<Map<String, dynamic>>(
      '/brokerages/$id/trends',
      query: {'status': status, 'limit': '$limit'},
    );
    return (data['trends'] as List? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(MarketTrend.fromJson)
        .toList();
  }

  /// GET /brokerages/{id}/backfill-queue → pending buy candidates.
  Future<List<BackfillItem>> backfillQueue(String id) async {
    final data = await _client.get<Map<String, dynamic>>('/brokerages/$id/backfill-queue');
    return (data['queue'] as List? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(BackfillItem.fromJson)
        .toList();
  }

  /// GET /brokerages/{id}/discovered → discover-engine opportunities.
  Future<List<DiscoveredStock>> discoveredStocks(String id) async {
    final data = await _client.get<Map<String, dynamic>>('/brokerages/$id/discovered');
    return (data['stocks'] as List? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(DiscoveredStock.fromJson)
        .toList();
  }

  /// GET /brokerages/{id}/trade-contexts → per-symbol bot rationale.
  Future<List<TradeRationale>> tradeContexts(String id) async {
    final data = await _client.get<Map<String, dynamic>>('/brokerages/$id/trade-contexts');
    return (data['contexts'] as List? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(TradeRationale.fromJson)
        .toList();
  }

  /// GET /brokerages/{id}/nexus-outcomes → signal→outcome scorecard.
  Future<OutcomeStats> nexusOutcomes(String id) async {
    final data = await _client.get<Map<String, dynamic>>('/brokerages/$id/nexus-outcomes');
    return OutcomeStats.fromJson(data);
  }

  /// GET /brokerages/{id}/momentum-watchlist → watchlist count + newest names.
  Future<WatchlistSummary> momentumWatchlist(String id) async {
    final data = await _client.get<Map<String, dynamic>>('/brokerages/$id/momentum-watchlist');
    return WatchlistSummary.fromJson(data);
  }
```

- [ ] **Step 3: Verify analysis is clean**

Run: `cd mobile && flutter analyze lib/features/dashboard/data/dashboard_repository.dart`
Expected: "No issues found!" (or only pre-existing warnings).

- [ ] **Step 4: Commit**

```bash
git add mobile/lib/features/dashboard/data/dashboard_repository.dart
git commit -m "feat(mobile): repository methods for nexus telemetry endpoints"
```

---

## Task 6: Riverpod providers (nexus_strategy_controller.dart)

**Files:**
- Create: `mobile/lib/features/dashboard/application/nexus_strategy_controller.dart`

**Context:** Mirror `nexusMomentumProvider` (`insights_controller.dart:82`) — `FutureProvider.autoDispose.family<…, String>`, each catching errors and returning an empty/zero value so a failed fetch simply hides the card.

- [ ] **Step 1: Create the providers**

```dart
// mobile/lib/features/dashboard/application/nexus_strategy_controller.dart
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/dashboard_repository.dart';
import '../data/nexus_models.dart';

/// Active + recently-ended trends, fetched together (one provider feeds the
/// Market Trends card and the Reversal Watch card).
final nexusTrendsProvider =
    FutureProvider.autoDispose.family<NexusTrendsView, String>((ref, id) async {
  final repo = ref.read(dashboardRepositoryProvider);
  try {
    final results = await Future.wait([
      repo.nexusTrends(id, status: 'active', limit: 30),
      repo.nexusTrends(id, status: 'ended', limit: 6),
    ]);
    return NexusTrendsView(active: results[0], recentlyEnded: results[1]);
  } catch (_) {
    return const NexusTrendsView(active: [], recentlyEnded: []);
  }
});

final backfillQueueProvider =
    FutureProvider.autoDispose.family<List<BackfillItem>, String>((ref, id) async {
  try {
    return await ref.read(dashboardRepositoryProvider).backfillQueue(id);
  } catch (_) {
    return const [];
  }
});

final discoveredStocksProvider =
    FutureProvider.autoDispose.family<List<DiscoveredStock>, String>((ref, id) async {
  try {
    return await ref.read(dashboardRepositoryProvider).discoveredStocks(id);
  } catch (_) {
    return const [];
  }
});

final tradeContextsProvider =
    FutureProvider.autoDispose.family<List<TradeRationale>, String>((ref, id) async {
  try {
    return await ref.read(dashboardRepositoryProvider).tradeContexts(id);
  } catch (_) {
    return const [];
  }
});

final nexusOutcomesProvider =
    FutureProvider.autoDispose.family<OutcomeStats, String>((ref, id) async {
  try {
    return await ref.read(dashboardRepositoryProvider).nexusOutcomes(id);
  } catch (_) {
    return const OutcomeStats(hitRate: 0, n: 0, nCorrect: 0, avgReturn: 0, recent: []);
  }
});

final momentumWatchlistProvider =
    FutureProvider.autoDispose.family<WatchlistSummary, String>((ref, id) async {
  try {
    return await ref.read(dashboardRepositoryProvider).momentumWatchlist(id);
  } catch (_) {
    return const WatchlistSummary(count: 0, newest: []);
  }
});
```

- [ ] **Step 2: Verify analysis is clean**

Run: `cd mobile && flutter analyze lib/features/dashboard/application/nexus_strategy_controller.dart`
Expected: "No issues found!"

- [ ] **Step 3: Commit**

```bash
git add mobile/lib/features/dashboard/application/nexus_strategy_controller.dart
git commit -m "feat(mobile): riverpod providers for nexus strategy cards"
```

---

## Task 7: Strategy section + 7 card widgets (strategy_section.dart)

**Files:**
- Create: `mobile/lib/features/dashboard/presentation/strategy_section.dart`

**Context:** Cards use `GlassCard(frosted: true, padding: EdgeInsets.all(16))` with a `_tileLabel(...)` eyebrow and compact rows, and tap → `context.push('/stock/$sym', extra: StockScreenArgs(brokerageId: id))`. Icons via `symbol('name')` from `material_symbols.dart`. Each card returns `SizedBox.shrink()` when empty. `StrategySection` resolves the selected account exactly like `InsightsSection` (`insights_section.dart:28-33`).

- [ ] **Step 1: Create the section + cards**

```dart
// mobile/lib/features/dashboard/presentation/strategy_section.dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/skeleton.dart';
import '../../stock/presentation/stock_screen.dart';
import '../application/dashboard_controller.dart';
import '../data/nexus_models.dart';
import '../application/nexus_strategy_controller.dart';

/// Eyebrow label, matching insights_section.dart's _tileLabel.
Widget _label(String s) => Text(
      s,
      style: AppTextStyles.nano.copyWith(
        color: AppColors.textFaint,
        fontWeight: FontWeight.w700,
        letterSpacing: 0.8,
      ),
    );

class StrategySection extends ConsumerWidget {
  const StrategySection({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final accounts = ref.watch(brokeragesProvider).valueOrNull;
    if (accounts == null || accounts.isEmpty) return const SizedBox.shrink();
    final selectedId = ref.watch(selectedAccountProvider);
    final id = (selectedId != null && accounts.any((a) => a.id == selectedId))
        ? selectedId
        : accounts.first.id;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Strategy', style: AppTextStyles.h3),
        const SizedBox(height: 14),
        _MarketTrendsCard(brokerageId: id),
        _ReversalWatchCard(brokerageId: id),
        _BackfillQueueCard(brokerageId: id),
        _DiscoveredStocksCard(brokerageId: id),
        _BotRationaleCard(brokerageId: id),
        _OutcomeScorecardCard(brokerageId: id),
        _MomentumWatchlistCard(brokerageId: id),
      ],
    );
  }
}

void _openStock(BuildContext context, String sym, String brokerageId) {
  if (sym.isEmpty) return;
  context.push('/stock/$sym', extra: StockScreenArgs(brokerageId: brokerageId));
}

GlassCard _cardShell({required List<Widget> children}) => GlassCard(
      frosted: true,
      padding: const EdgeInsets.all(16),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: children),
    );

// ── 1. Market Trends (active + recently ended) ──────────────────────────────

class _MarketTrendsCard extends ConsumerWidget {
  const _MarketTrendsCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final view = ref.watch(nexusTrendsProvider(brokerageId)).valueOrNull;
    if (view != null && view.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: _cardShell(children: [
        Row(children: [
          Icon(symbol('trending_up'), size: 15, color: AppColors.primary),
          const SizedBox(width: 6),
          _label('MARKET TRENDS'),
        ]),
        const SizedBox(height: 10),
        if (view == null)
          const Skeleton(height: 80, radius: 7)
        else ...[
          for (final t in view.active) _TrendRow(trend: t, brokerageId: brokerageId),
          if (view.recentlyEnded.isNotEmpty) ...[
            const SizedBox(height: 6),
            _label('RECENTLY ENDED'),
            const SizedBox(height: 4),
            for (final t in view.recentlyEnded)
              _EndedTrendRow(trend: t),
          ],
        ],
      ]),
    );
  }
}

class _TrendRow extends StatelessWidget {
  const _TrendRow({required this.trend, required this.brokerageId});
  final MarketTrend trend;
  final String brokerageId;

  @override
  Widget build(BuildContext context) {
    final c = trend.bullish ? AppColors.success : AppColors.danger;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            Icon(trend.bullish ? Icons.arrow_upward : Icons.arrow_downward, size: 13, color: c),
            const SizedBox(width: 6),
            Expanded(
              child: Text(trend.name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: AppTextStyles.micro.copyWith(
                      color: AppColors.textHi, fontWeight: FontWeight.w700)),
            ),
            Text('${(trend.strength * 100).round()}%',
                style: AppTextStyles.nano.copyWith(color: c, fontWeight: FontWeight.w700)),
          ]),
          const SizedBox(height: 4),
          ClipRRect(
            borderRadius: BorderRadius.circular(3),
            child: LinearProgressIndicator(
              value: trend.strength.clamp(0.0, 1.0),
              minHeight: 4,
              backgroundColor: Colors.white.withValues(alpha: 0.06),
              valueColor: AlwaysStoppedAnimation<Color>(c),
            ),
          ),
          if (trend.tickers.isNotEmpty) ...[
            const SizedBox(height: 5),
            Wrap(
              spacing: 6,
              children: [
                for (final s in trend.tickers.take(5))
                  GestureDetector(
                    onTap: () => _openStock(context, s, brokerageId),
                    child: Text(s,
                        style: AppTextStyles.nano.copyWith(color: AppColors.textMuted)),
                  ),
                if (trend.tickers.length > 5)
                  Text('+${trend.tickers.length - 5}',
                      style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _EndedTrendRow extends StatelessWidget {
  const _EndedTrendRow({required this.trend});
  final MarketTrend trend;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(children: [
        Icon(symbol('check'), size: 12, color: AppColors.textFaint),
        const SizedBox(width: 6),
        Expanded(
          child: Text(trend.name,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: AppTextStyles.nano.copyWith(color: AppColors.textMuted)),
        ),
        Text(_agoLabel(trend.endedAt),
            style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
      ]),
    );
  }
}

/// "ended 2d ago" from an ISO date string; empty when unparseable.
String _agoLabel(String? iso) {
  if (iso == null || iso.isEmpty) return '';
  final dt = DateTime.tryParse(iso);
  if (dt == null) return '';
  final days = DateTime.now().difference(dt).inDays;
  if (days <= 0) return 'ended today';
  return 'ended ${days}d ago';
}

// ── 2. Reversal Watch ───────────────────────────────────────────────────────

class _ReversalWatchCard extends ConsumerWidget {
  const _ReversalWatchCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final view = ref.watch(nexusTrendsProvider(brokerageId)).valueOrNull;
    final items = view?.reversalWatch ?? const [];
    if (items.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: _cardShell(children: [
        Row(children: [
          Icon(symbol('warning'), size: 15, color: AppColors.warning),
          const SizedBox(width: 6),
          _label('REVERSAL WATCH'),
        ]),
        const SizedBox(height: 10),
        for (final t in items)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 5),
            child: Row(children: [
              Expanded(
                child: Text(t.name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: AppTextStyles.micro.copyWith(color: AppColors.textHi)),
              ),
              Text('${t.reversalCount} signal${t.reversalCount == 1 ? '' : 's'}',
                  style: AppTextStyles.nano.copyWith(color: AppColors.warning)),
            ]),
          ),
      ]),
    );
  }
}

// ── 3. Backfill Queue ───────────────────────────────────────────────────────

class _BackfillQueueCard extends ConsumerWidget {
  const _BackfillQueueCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final items = ref.watch(backfillQueueProvider(brokerageId)).valueOrNull;
    if (items != null && items.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: _cardShell(children: [
        Row(children: [
          Icon(symbol('queue'), size: 15, color: AppColors.info),
          const SizedBox(width: 6),
          _label('BACKFILL QUEUE'),
          const Spacer(),
          if (items != null)
            Text('${items.length} pending',
                style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
        ]),
        const SizedBox(height: 10),
        if (items == null)
          const Skeleton(height: 60, radius: 7)
        else
          for (final q in items.take(12))
            GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: () => _openStock(context, q.ticker, brokerageId),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 5),
                child: Row(children: [
                  if (q.priority) ...[
                    Icon(symbol('star'), size: 12, color: AppColors.warning),
                    const SizedBox(width: 4),
                  ],
                  SizedBox(
                    width: 64,
                    child: Text(q.ticker,
                        style: AppTextStyles.micro.copyWith(
                            color: AppColors.textHi, fontWeight: FontWeight.w700)),
                  ),
                  Expanded(
                    child: Text(q.source,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
                  ),
                  if (q.nPaths > 0)
                    Text('${q.nPaths} paths',
                        style: AppTextStyles.nano.copyWith(color: AppColors.textMuted)),
                ]),
              ),
            ),
      ]),
    );
  }
}

// ── 4. Discovered Stocks ────────────────────────────────────────────────────

class _DiscoveredStocksCard extends ConsumerWidget {
  const _DiscoveredStocksCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final items = ref.watch(discoveredStocksProvider(brokerageId)).valueOrNull;
    if (items != null && items.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: _cardShell(children: [
        Row(children: [
          Icon(symbol('explore'), size: 15, color: AppColors.teal),
          const SizedBox(width: 6),
          _label('DISCOVERED'),
        ]),
        const SizedBox(height: 10),
        if (items == null)
          const Skeleton(height: 60, radius: 7)
        else
          for (final d in items.take(12))
            GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: () => _openStock(context, d.ticker, brokerageId),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 5),
                child: Row(children: [
                  SizedBox(
                    width: 64,
                    child: Text(d.ticker,
                        style: AppTextStyles.micro.copyWith(
                            color: AppColors.textHi, fontWeight: FontWeight.w700)),
                  ),
                  Expanded(
                    child: Text(
                        d.sourceTicker != null && d.sourceTicker!.isNotEmpty
                            ? '${d.source} · via ${d.sourceTicker}'
                            : d.source,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
                  ),
                ]),
              ),
            ),
      ]),
    );
  }
}

// ── 5. Bot Rationale ────────────────────────────────────────────────────────

class _BotRationaleCard extends ConsumerWidget {
  const _BotRationaleCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final items = ref.watch(tradeContextsProvider(brokerageId)).valueOrNull;
    if (items != null && items.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: _cardShell(children: [
        Row(children: [
          Icon(symbol('psychology'), size: 15, color: AppColors.primary),
          const SizedBox(width: 6),
          _label('BOT RATIONALE'),
        ]),
        const SizedBox(height: 10),
        if (items == null)
          const Skeleton(height: 70, radius: 7)
        else
          for (final r in items.take(8))
            GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: () => _openStock(context, r.symbol, brokerageId),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 6),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: [
                      Text(r.symbol,
                          style: AppTextStyles.micro.copyWith(
                              color: AppColors.textHi, fontWeight: FontWeight.w700)),
                      const SizedBox(width: 8),
                      if (r.eventType.isNotEmpty)
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                          decoration: BoxDecoration(
                            color: AppColors.fill(AppColors.primary),
                            borderRadius: BorderRadius.circular(5),
                          ),
                          child: Text(r.eventType,
                              style: AppTextStyles.nano.copyWith(color: AppColors.primary)),
                        ),
                    ]),
                    if (r.reason.isNotEmpty) ...[
                      const SizedBox(height: 3),
                      Text(r.reason,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: AppTextStyles.nano.copyWith(
                              color: AppColors.textMuted, height: 1.3)),
                    ],
                  ],
                ),
              ),
            ),
      ]),
    );
  }
}

// ── 6. Outcome Scorecard ────────────────────────────────────────────────────

class _OutcomeScorecardCard extends ConsumerWidget {
  const _OutcomeScorecardCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final s = ref.watch(nexusOutcomesProvider(brokerageId)).valueOrNull;
    if (s != null && s.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: _cardShell(children: [
        Row(children: [
          Icon(symbol('scoreboard'), size: 15, color: AppColors.info),
          const SizedBox(width: 6),
          _label('OUTCOME SCORECARD'),
        ]),
        const SizedBox(height: 10),
        if (s == null)
          const Skeleton(height: 60, radius: 7)
        else ...[
          Row(children: [
            Text('${(s.hitRate * 100).round()}%',
                style: AppTextStyles.valueLg.copyWith(
                    color: s.hitRate >= 0.5 ? AppColors.success : AppColors.danger)),
            const SizedBox(width: 8),
            Text('hit rate · ${s.nCorrect}/${s.n} signals',
                style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
          ]),
          const SizedBox(height: 8),
          for (final o in s.recent)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 3),
              child: Row(children: [
                Icon(o.correct ? symbol('check') : symbol('close'),
                    size: 12, color: o.correct ? AppColors.success : AppColors.danger),
                const SizedBox(width: 6),
                SizedBox(
                  width: 56,
                  child: Text(o.symbol,
                      style: AppTextStyles.nano.copyWith(
                          color: AppColors.textHi, fontWeight: FontWeight.w700)),
                ),
                Expanded(
                  child: Text(o.eventType,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
                ),
                Text('${o.latestReturn >= 0 ? '+' : ''}${o.latestReturn.toStringAsFixed(1)}%',
                    style: AppTextStyles.nano.copyWith(
                        color: o.latestReturn >= 0 ? AppColors.success : AppColors.danger)),
              ]),
            ),
        ],
      ]),
    );
  }
}

// ── 7. Momentum Watchlist ───────────────────────────────────────────────────

class _MomentumWatchlistCard extends ConsumerWidget {
  const _MomentumWatchlistCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final w = ref.watch(momentumWatchlistProvider(brokerageId)).valueOrNull;
    if (w != null && w.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: _cardShell(children: [
        Row(children: [
          Icon(symbol('visibility'), size: 15, color: AppColors.teal),
          const SizedBox(width: 6),
          _label('MOMENTUM WATCHLIST'),
          const Spacer(),
          if (w != null)
            Text('monitoring ${w.count}',
                style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
        ]),
        const SizedBox(height: 10),
        if (w == null)
          const Skeleton(height: 40, radius: 7)
        else
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final e in w.newest)
                GestureDetector(
                  onTap: () => _openStock(context, e.symbol, brokerageId),
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 6),
                    decoration: BoxDecoration(
                      color: AppColors.fill(AppColors.teal),
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(color: AppColors.stroke(AppColors.teal)),
                    ),
                    child: Row(mainAxisSize: MainAxisSize.min, children: [
                      Text(e.symbol,
                          style: AppTextStyles.nano.copyWith(
                              color: AppColors.textHi, fontWeight: FontWeight.w700)),
                      if (e.ret20d != 0) ...[
                        const SizedBox(width: 5),
                        Text('${e.ret20d >= 0 ? '+' : ''}${e.ret20d.toStringAsFixed(0)}%',
                            style: AppTextStyles.nano.copyWith(
                                color: e.ret20d >= 0 ? AppColors.success : AppColors.danger)),
                      ],
                    ]),
                  ),
                ),
            ],
          ),
      ]),
    );
  }
}
```

- [ ] **Step 2: Verify analysis is clean**

Run: `cd mobile && flutter analyze lib/features/dashboard/presentation/strategy_section.dart`
Expected: "No issues found!". Fix any name mismatches against the real `material_symbols.dart` (some `symbol('…')` names may differ — if `flutter analyze` or a runtime "missing glyph" appears, swap to a valid name from `core/widgets/material_symbols.dart`) and `AppColors`/`AppTextStyles` members (e.g. confirm `AppColors.teal`, `AppColors.info`, `AppTextStyles.valueLg` exist; they were referenced in the exploration but verify).

- [ ] **Step 3: Commit**

```bash
git add mobile/lib/features/dashboard/presentation/strategy_section.dart
git commit -m "feat(mobile): Strategy section with 7 nexus telemetry cards"
```

---

## Task 8: Wire the section into the dashboard (dashboard_screen.dart)

**Files:**
- Modify: `mobile/lib/features/dashboard/presentation/dashboard_screen.dart` (import + insert into the sliver list, line ~58)

- [ ] **Step 1: Add the import (with the other feature imports near the top)**

```dart
import 'strategy_section.dart';
```

- [ ] **Step 2: Insert `StrategySection` after `InsightsSection`**

Change the sliver list (lines 57-60) from:

```dart
                  // ── Insights + Bot ideas ───────────────────────────────────
                  const InsightsSection(),
                  const SizedBox(height: 32),
```

to:

```dart
                  // ── Insights + Bot ideas ───────────────────────────────────
                  const InsightsSection(),
                  const SizedBox(height: 32),

                  // ── Strategy (nexus telemetry) ─────────────────────────────
                  const StrategySection(),
                  const SizedBox(height: 32),
```

- [ ] **Step 3: Verify analysis + a full project analyze**

Run: `cd mobile && flutter analyze lib/features/dashboard`
Expected: "No issues found!" (or only pre-existing warnings unrelated to these files).

- [ ] **Step 4: Commit**

```bash
git add mobile/lib/features/dashboard/presentation/dashboard_screen.dart
git commit -m "feat(mobile): mount Strategy section on the dashboard"
```

---

## Task 9: Market Trends card golden test

**Files:**
- Create: `mobile/test/features/dashboard/strategy_trends_card_golden_test.dart`

**Context:** Mirror `test/features/dashboard/sector_3d_chart_golden_test.dart`. Use the bundled-fonts harness (`test/flutter_test_config.dart` sets `allowRuntimeFetching=false`). Override `nexusTrendsProvider` with a fixed `NexusTrendsView` via a `ProviderScope` override so the test is deterministic. `flutter test` renders glyphs as boxes regardless — the golden captures layout, not glyphs.

- [ ] **Step 1: Write the golden test**

```dart
// mobile/test/features/dashboard/strategy_trends_card_golden_test.dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:intellistock/features/dashboard/application/nexus_strategy_controller.dart';
import 'package:intellistock/features/dashboard/data/nexus_models.dart';
import 'package:intellistock/features/dashboard/presentation/strategy_section.dart';

void main() {
  testWidgets('Market Trends card — active + recently ended golden', (tester) async {
    final view = NexusTrendsView(
      active: [
        MarketTrend.fromJson({
          'name': 'AI Semiconductor Rally', 'status': 'active', 'direction': 'bullish',
          'strength': 0.78, 'affected_tickers': ['NVDA', 'AMD', 'TSM', 'AVGO', 'MU', 'INTC'],
        }),
        MarketTrend.fromJson({
          'name': 'Regional Bank Stress', 'status': 'active', 'direction': 'bearish',
          'strength': 0.52, 'affected_tickers': ['KRE', 'PACW'],
        }),
      ],
      recentlyEnded: [
        MarketTrend.fromJson({
          'name': 'Energy Squeeze', 'status': 'ended', 'direction': 'bullish',
          'ended_at': DateTime.now().subtract(const Duration(days: 2)).toIso8601String(),
        }),
      ],
    );

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          nexusTrendsProvider('acct1').overrideWith((ref) async => view),
        ],
        child: MaterialApp(
          home: Scaffold(
            backgroundColor: const Color(0xFF04040C),
            body: InheritedGoRouter(
              goRouter: GoRouter(routes: [GoRoute(path: '/', builder: (_, __) => const SizedBox())]),
              child: const SingleChildScrollView(
                padding: EdgeInsets.all(16),
                child: _TrendsCardHarness(brokerageId: 'acct1'),
              ),
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await expectLater(
      find.byType(_TrendsCardHarness),
      matchesGoldenFile('goldens/strategy_trends_card.png'),
    );
  });
}

/// Renders just the Market Trends card by reusing StrategySection's account
/// resolution path is overkill for a unit golden, so this harness pumps the
/// card via the provider override above.
class _TrendsCardHarness extends ConsumerWidget {
  const _TrendsCardHarness({required this.brokerageId});
  final String brokerageId;
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // The card widget is private to strategy_section.dart; to keep it testable,
    // expose a tiny public wrapper there (see Step 2).
    return MarketTrendsCardForTest(brokerageId: brokerageId);
  }
}
```

- [ ] **Step 2: Expose a test-only wrapper for the private card**

Add to the bottom of `strategy_section.dart`:

```dart
/// Test-only public wrapper around the private Market Trends card so golden
/// tests can render it in isolation. Not used by the app.
@visibleForTesting
class MarketTrendsCardForTest extends StatelessWidget {
  const MarketTrendsCardForTest({super.key, required this.brokerageId});
  final String brokerageId;
  @override
  Widget build(BuildContext context) => _MarketTrendsCard(brokerageId: brokerageId);
}
```

Add the import at the top of `strategy_section.dart` if not present: `import 'package:flutter/foundation.dart';` (for `@visibleForTesting`).

- [ ] **Step 3: Generate the golden, then run the test**

Run: `cd mobile && flutter test --update-goldens test/features/dashboard/strategy_trends_card_golden_test.dart`
Then Read the generated `mobile/test/features/dashboard/goldens/strategy_trends_card.png` to eyeball the layout (two active trend rows with strength bars + a "recently ended" row).
Run: `cd mobile && flutter test test/features/dashboard/strategy_trends_card_golden_test.dart`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add mobile/test/features/dashboard/strategy_trends_card_golden_test.dart \
        mobile/test/features/dashboard/goldens/strategy_trends_card.png \
        mobile/lib/features/dashboard/presentation/strategy_section.dart
git commit -m "test(mobile): golden for Market Trends card (active + recently ended)"
```

---

## Task 10: Full verification + deploy

**Files:** none (verification only)

- [ ] **Step 1: Backend test suite**

Run: `cd backend && python3 -m pytest tests/test_nexus_telemetry.py tests/test_holding_opens.py -q`
Expected: all PASS.

- [ ] **Step 2: Mobile test suite + analyze**

Run: `cd mobile && flutter test test/features/dashboard/`
Run: `cd mobile && flutter analyze lib/features/dashboard`
Expected: all PASS / no new issues.

- [ ] **Step 3: gitnexus detect changes (pre-merge audit)**

Run via MCP: `gitnexus_detect_changes()` — confirm only the expected symbols changed and NO trade/decision-path execution flow is affected. Trust `affected_processes` + risk over stale line-drift attribution.

- [ ] **Step 4: Deploy + on-device verify**

- Backend: push branch; operator redeploys `main` to Dockploy (backend is not auto-deployed). Smoke-test one endpoint, e.g. `GET /brokerages/{alpaca-main-id}/nexus-outcomes` returns a JSON scorecard (not 500).
- Mobile: `cd mobile && scripts/deploy.sh 1` (re-run on transient `CoreDeviceError 4000`).
- On device: the **Strategy** section appears below Insights with populated cards on the `alpaca-main` brokerage; cards self-hide where data is absent (e.g. Momentum Watchlist hides if `momentum_watchlist_enabled` is off). Tapping a ticker opens the stock screen.

- [ ] **Step 5: Final commit / PR**

Open a PR for `feat/nexus-strategy-dashboard-cards` summarizing the new Strategy section + endpoints.

---

## Self-Review

**Spec coverage:** Every spec card (#1 Market Trends, #2 Reversal Watch, #3 Backfill Queue, #4 Discovered, #5 Bot Rationale, #6 Outcome Scorecard, #7 Momentum Watchlist) maps to a widget in Task 7; each backend endpoint maps to Task 1/2/3; the "no strategy edit" decision is honored (no `graph_nexus_analysis.py` task). Testing section → Tasks 1, 4, 9. Deploy → Task 10. ✅

**Placeholder scan:** No "TBD"/"implement later"/"add error handling" — all steps carry real code/commands. The two genuine runtime-confirm notes (material-symbol names in Task 7 Step 2; package name in Task 4 Step 2) are explicit verification steps with a concrete fix action, not gaps. ✅

**Type consistency:** `MarketTrend`/`NexusTrendsView`/`BackfillItem`/`DiscoveredStock`/`TradeRationale`/`OutcomeRow`/`OutcomeStats`/`WatchlistEntry`/`WatchlistSummary` are defined in Task 4 and used identically in Tasks 5/6/7/9. Provider names (`nexusTrendsProvider`, `backfillQueueProvider`, `discoveredStocksProvider`, `tradeContextsProvider`, `nexusOutcomesProvider`, `momentumWatchlistProvider`) defined in Task 6 match Task 7 usage. Backend `summarize_outcomes`/`normalize_backfill_item`/`newest_watchlist`/`dedupe_latest_contexts` defined in Task 1, consumed in Tasks 2/3. Endpoint paths match repo methods in Task 5. ✅

## Open items carried from spec (resolve during implementation)
- Confirm `material_symbols.dart` has the icon names used (`trending_up`, `queue`, `explore`, `psychology`, `scoreboard`, `visibility`, `warning`, `check`, `close`, `star`); swap any missing ones (Task 7 Step 2).
- Confirm `AppColors.teal`, `AppColors.info`, `AppTextStyles.valueLg` exist (Task 7 Step 2).
- Backfill item keys (`raw_net_score` vs `score`, `priority` vs `is_priority`, `n_paths`) are normalized server-side (Task 1) and verified against the live cache during Task 10 smoke-test.
- Outcomes table name is `GraphNexusTradeOutcomes` (confirmed `graph_nexus_analysis.py:335`); the older `GraphNexusOutcomes` is a different table and is NOT used.
