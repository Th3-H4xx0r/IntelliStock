# Dashboard & Stock-Screen Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Phase 2 features from the 2026-06-18 design spec — Risk card, Agent activity timeline, Market open/closed + live chip, Watchlist, and Dividends received — on top of the shipped Phase 1 Insights section.

**Architecture:** Same patterns as Phase 1 — pure helpers in `portfolio_analytics.dart` (extended) and a new `market_hours.dart`, never-throw Riverpod `FutureProvider`/family providers in `insights_controller.dart` (+ a new `watchlist_controller.dart`), UI cards in `insights_section.dart` (+ a `_LiveStatusChip` near the hero in `portfolio_chart.dart`). New backend only for Watchlist (a `Watchlist` RethinkDB table + endpoints) and possibly Dividends.

**Tech Stack:** Flutter + Riverpod + go_router (mobile); Python FastAPI + RethinkDB (backend). Tests: `flutter test`, `pytest`.

**Reference:** `docs/superpowers/specs/2026-06-18-dashboard-stock-feature-expansion-design.md`.

---

## Ordering note

Tasks 1–8 are independent and can be done in any order. Recommended order: Risk (1–2), Market chip (3–4), Agent timeline (5), Watchlist (6–7), Dividends (8 — investigation-gated, may defer). Each task block ends in its own commit.

---

### Task 1: Risk metrics — pure helpers

**Files:**
- Modify: `mobile/lib/features/dashboard/application/portfolio_analytics.dart` (append)
- Test: `mobile/test/features/dashboard/portfolio_analytics_test.dart` (append a group)

- [ ] **Step 1: Write the failing tests** (append to the existing test file's `main()`)

```dart
  group('riskMetrics', () {
    test('flat curve → zero vol, zero drawdown, null sharpe', () {
      final r = riskMetrics([100, 100, 100, 100]);
      expect(r.volatility, 0);
      expect(r.maxDrawdown, 0);
      expect(r.sharpe, isNull); // zero stddev → undefined
    });

    test('max drawdown is the largest peak-to-trough drop', () {
      // peak 120 → trough 90 = 25% drawdown
      final r = riskMetrics([100, 120, 90, 110]);
      expect(r.maxDrawdown, closeTo(25.0, 0.001));
    });

    test('rising curve → positive sharpe', () {
      final r = riskMetrics([100, 101, 102, 103, 104]);
      expect(r.sharpe, isNotNull);
      expect(r.sharpe!, greaterThan(0));
      expect(r.maxDrawdown, 0); // monotonic up
    });

    test('too few points → empty', () {
      expect(riskMetrics([100]).isEmpty, isTrue);
      expect(riskMetrics([]).isEmpty, isTrue);
    });
  });
```

- [ ] **Step 2: Run to verify failure**

Run: `cd mobile && flutter test test/features/dashboard/portfolio_analytics_test.dart`
Expected: FAIL — `riskMetrics`/`RiskMetrics` undefined.

- [ ] **Step 3: Implement** (append to `portfolio_analytics.dart`)

```dart
/// Risk summary derived from an equity curve. Percentages are 0..100.
class RiskMetrics {
  const RiskMetrics({
    required this.volatility,
    required this.maxDrawdown,
    required this.sharpe,
    required this.points,
  });
  final double volatility; // annualized stdev of periodic returns, %
  final double maxDrawdown; // worst peak-to-trough, %
  final double? sharpe; // annualized; null when stdev is 0
  final int points;

  bool get isEmpty => points < 2;
}

/// Compute volatility, max drawdown and Sharpe from an equity [values] series.
/// Returns are period-over-period; annualization uses √252 (daily-ish bars) as
/// a simple, consistent scale factor. Risk-free is assumed 0.
RiskMetrics riskMetrics(List<double> values) {
  if (values.length < 2) {
    return const RiskMetrics(
        volatility: 0, maxDrawdown: 0, sharpe: null, points: 0);
  }
  final returns = <double>[];
  for (var i = 1; i < values.length; i++) {
    if (values[i - 1] != 0) returns.add(values[i] / values[i - 1] - 1);
  }
  // Max drawdown.
  var peak = values.first;
  var maxDd = 0.0;
  for (final v in values) {
    if (v > peak) peak = v;
    if (peak > 0) {
      final dd = (peak - v) / peak;
      if (dd > maxDd) maxDd = dd;
    }
  }
  if (returns.isEmpty) {
    return RiskMetrics(
        volatility: 0, maxDrawdown: maxDd * 100, sharpe: null, points: values.length);
  }
  final mean = returns.reduce((a, b) => a + b) / returns.length;
  final variance =
      returns.map((r) => (r - mean) * (r - mean)).reduce((a, b) => a + b) /
          returns.length;
  final stdev = variance <= 0 ? 0.0 : _sqrt(variance);
  const annualize = 15.874507866; // √252
  final vol = stdev * annualize * 100;
  final sharpe = stdev == 0 ? null : (mean / stdev) * annualize;
  return RiskMetrics(
    volatility: vol,
    maxDrawdown: maxDd * 100,
    sharpe: sharpe,
    points: values.length,
  );
}

double _sqrt(double x) {
  // Avoid importing dart:math into this otherwise-import-free file for one call.
  if (x <= 0) return 0;
  var g = x;
  for (var i = 0; i < 30; i++) {
    g = (g + x / g) / 2;
  }
  return g;
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd mobile && flutter test test/features/dashboard/portfolio_analytics_test.dart`
Expected: PASS (all groups).

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/features/dashboard/application/portfolio_analytics.dart mobile/test/features/dashboard/portfolio_analytics_test.dart
git commit -m "feat(mobile): risk metrics helper (vol, drawdown, sharpe) + tests"
```

---

### Task 2: Risk card — provider + UI

**Files:**
- Modify: `mobile/lib/features/dashboard/application/insights_controller.dart` (add provider)
- Modify: `mobile/lib/features/dashboard/presentation/insights_section.dart` (add `_RiskCard`, mount it)

- [ ] **Step 1: Add the provider** (append to `insights_controller.dart`)

Uses a long-range equity curve so the metrics are meaningful. Never-throws.

```dart
/// Risk metrics for an account, computed from its 1Y equity curve. Empty when
/// unavailable. keepAlive: the curve barely changes intraday.
final riskMetricsProvider =
    FutureProvider.autoDispose.family<RiskMetrics, String>(
  (ref, brokerageId) async {
    ref.keepAlive();
    try {
      final h = await ref
          .read(dashboardRepositoryProvider)
          .portfolioHistory(brokerageId, '1Y');
      return riskMetrics(h.values);
    } catch (_) {
      return const RiskMetrics(
          volatility: 0, maxDrawdown: 0, sharpe: null, points: 0);
    }
  },
);
```

- [ ] **Step 2: Add `_RiskCard`** (append to `insights_section.dart`, mount inside `InsightsSection`'s analytics column right after `_SectorAllocationCard`)

```dart
class _RiskCard extends ConsumerWidget {
  const _RiskCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(riskMetricsProvider(brokerageId));
    final r = async.valueOrNull;
    if (r != null && r.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _tileLabel('RISK'),
            const SizedBox(height: 12),
            if (r == null)
              const Skeleton(height: 22, radius: 6)
            else
              Row(
                children: [
                  Expanded(child: _metric('Volatility', '${r.volatility.round()}%')),
                  Expanded(child: _metric('Max drawdown', '${r.maxDrawdown.round()}%')),
                  Expanded(
                      child: _metric('Sharpe',
                          r.sharpe == null ? '—' : r.sharpe!.toStringAsFixed(2))),
                ],
              ),
          ],
        ),
      ),
    );
  }

  Widget _metric(String label, String value) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(value,
              style: AppTextStyles.value.copyWith(fontWeight: FontWeight.w700)),
          const SizedBox(height: 3),
          Text(label,
              style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
        ],
      );
}
```

Mount: in `InsightsSection.build`, add `_RiskCard(brokerageId: id),` immediately after the `_SectorAllocationCard(brokerageId: id)` line. Add `import 'package:flutter_riverpod/flutter_riverpod.dart';` already present.

- [ ] **Step 3: Verify**

Run: `cd mobile && flutter analyze lib/features/dashboard`
Expected: No issues found.

- [ ] **Step 4: Commit**

```bash
git add mobile/lib/features/dashboard/application/insights_controller.dart mobile/lib/features/dashboard/presentation/insights_section.dart
git commit -m "feat(mobile): dashboard risk card (vol / drawdown / sharpe)"
```

---

### Task 3: Market hours — pure helper

**Files:**
- Create: `mobile/lib/features/dashboard/application/market_hours.dart`
- Test: `mobile/test/features/dashboard/market_hours_test.dart`

US equity regular session is 9:30–16:00 America/New_York, Mon–Fri. The helper takes an explicit "now in ET" so it's deterministic for tests; the provider passes the real ET time.

- [ ] **Step 1: Write the failing test**

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/dashboard/application/market_hours.dart';

void main() {
  // All inputs are wall-clock ET (the caller converts).
  test('open during regular weekday session', () {
    expect(isMarketOpenAtEt(DateTime(2026, 6, 18, 10, 0)), isTrue); // Thu 10:00
    expect(isMarketOpenAtEt(DateTime(2026, 6, 18, 9, 30)), isTrue);
    expect(isMarketOpenAtEt(DateTime(2026, 6, 18, 15, 59)), isTrue);
  });

  test('closed before open, after close, and on weekends', () {
    expect(isMarketOpenAtEt(DateTime(2026, 6, 18, 9, 29)), isFalse);
    expect(isMarketOpenAtEt(DateTime(2026, 6, 18, 16, 0)), isFalse);
    expect(isMarketOpenAtEt(DateTime(2026, 6, 20, 12, 0)), isFalse); // Sat
    expect(isMarketOpenAtEt(DateTime(2026, 6, 21, 12, 0)), isFalse); // Sun
  });
}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd mobile && flutter test test/features/dashboard/market_hours_test.dart`
Expected: FAIL — `isMarketOpenAtEt` undefined.

- [ ] **Step 3: Implement** (`market_hours.dart`)

```dart
/// True when [etNow] (wall-clock America/New_York) is within the US equity
/// regular session: Mon–Fri, 09:30–16:00. Holidays are not modeled (a known
/// limitation; acceptable for a "live" status hint).
bool isMarketOpenAtEt(DateTime etNow) {
  if (etNow.weekday == DateTime.saturday ||
      etNow.weekday == DateTime.sunday) {
    return false;
  }
  final minutes = etNow.hour * 60 + etNow.minute;
  const open = 9 * 60 + 30;
  const close = 16 * 60;
  return minutes >= open && minutes < close;
}

/// Convert a UTC instant to wall-clock ET. ET = UTC-4 (EDT) Mar–Nov, UTC-5
/// (EST) otherwise — approximated by month (no exact DST-boundary handling,
/// which is fine for an open/closed hint).
DateTime etFromUtc(DateTime utc) {
  final u = utc.toUtc();
  final isDst = u.month > 3 && u.month < 11; // Apr–Oct always EDT
  return u.subtract(Duration(hours: isDst ? 4 : 5));
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd mobile && flutter test test/features/dashboard/market_hours_test.dart`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/features/dashboard/application/market_hours.dart mobile/test/features/dashboard/market_hours_test.dart
git commit -m "feat(mobile): market-hours helper + tests"
```

---

### Task 4: Live status chip near the hero

**Files:**
- Modify: `mobile/lib/features/dashboard/presentation/portfolio_chart.dart` (add `_LiveStatusChip`, render in the hero header)

- [ ] **Step 1: Add the chip widget** (append near `_PulsingEndDot` in `portfolio_chart.dart`)

```dart
/// "Markets open · Live" / "Markets closed" pill — pairs with the pulsing end
/// dot. Self-ticks every 30 s so it flips at the open/close boundary.
class _LiveStatusChip extends StatefulWidget {
  const _LiveStatusChip();
  @override
  State<_LiveStatusChip> createState() => _LiveStatusChipState();
}

class _LiveStatusChipState extends State<_LiveStatusChip> {
  Timer? _t;
  @override
  void initState() {
    super.initState();
    _t = Timer.periodic(const Duration(seconds: 30), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _t?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final open = isMarketOpenAtEt(etFromUtc(DateTime.now().toUtc()));
    final c = open ? AppColors.success : AppColors.textFaint;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
            width: 6,
            height: 6,
            decoration: BoxDecoration(shape: BoxShape.circle, color: c)),
        const SizedBox(width: 5),
        Text(open ? 'Markets open · Live' : 'Markets closed',
            style: AppTextStyles.nano.copyWith(color: c)),
      ],
    );
  }
}
```

Add imports to `portfolio_chart.dart`: `import 'dart:async';` (if not present) and
`import '../application/market_hours.dart';`. Render `const _LiveStatusChip()` in the
hero header `Row` (the `if (!hero)` block builds `_CardHeader`; for the hero, add the
chip beside the freshness line — place `const _LiveStatusChip()` in the `_ValueRow`'s
parent column, e.g. just above `_RangeTabs`).

- [ ] **Step 2: Verify**

Run: `cd mobile && flutter analyze lib/features/dashboard/presentation/portfolio_chart.dart`
Expected: No issues found.

- [ ] **Step 3: Commit**

```bash
git add mobile/lib/features/dashboard/presentation/portfolio_chart.dart
git commit -m "feat(mobile): markets open/closed live chip on the hero"
```

---

### Task 5: Agent activity timeline

No new backend — reuses `GET /agent/runs` (existing `AgentRunsPage`/`AgentRun` in `mobile/lib/features/agent_runs/data/agent_repository.dart`) and `GET /agent/best` (`action_agent_get_best`).

**Files:**
- Modify: `mobile/lib/features/dashboard/application/insights_controller.dart` (add `agentTimelineProvider`)
- Modify: `mobile/lib/features/dashboard/presentation/insights_section.dart` (add `_AgentTimelineCard`, mount in the Bot-ideas group)

- [ ] **Step 1: Add provider** (append to `insights_controller.dart`; import `agent_repository.dart`)

```dart
/// The most recent agent runs (cycle log) for the dashboard timeline. Reuses
/// the agent_runs page model. Never-throws.
final agentTimelineProvider =
    FutureProvider.autoDispose<List<AgentRun>>((ref) async {
  try {
    final data = await ref
        .read(apiClientProvider)
        .get<Map<String, dynamic>>('/agent/runs', query: {'page': 1, 'per_page': 5});
    return AgentRunsPage.fromJson(data).runs;
  } catch (_) {
    return const [];
  }
});
```

- [ ] **Step 2: Add `_AgentTimelineCard`** (append to `insights_section.dart`; import `agent_repository.dart`). Mount inside `_BotIdeasGroup` (so it shares the "Bot ideas" header), and include it in the `hasDiscovered || hasTrends` visibility OR show it whenever runs exist.

```dart
class _AgentTimelineCard extends ConsumerWidget {
  const _AgentTimelineCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final runs = ref.watch(agentTimelineProvider).valueOrNull;
    if (runs == null || runs.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _tileLabel('AGENT ACTIVITY'),
            const SizedBox(height: 10),
            for (final r in runs.take(4))
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 7),
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        (r.name?.isNotEmpty ?? false) ? r.name! : 'Trading cycle',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: AppTextStyles.body
                            .copyWith(color: AppColors.textMd),
                      ),
                    ),
                    if (r.createdAt != null)
                      Text(fmtRelative(r.createdAt),
                          style: AppTextStyles.nano
                              .copyWith(color: AppColors.textDim)),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}
```

Visibility: change `_BotIdeasGroup` so it also watches `agentTimelineProvider`; show the group when discovered OR trends OR runs are non-empty, and render `const _AgentTimelineCard()` after the trends/discovered cards.

- [ ] **Step 3: Verify**

Run: `cd mobile && flutter analyze lib/features/dashboard`
Expected: No issues found.

- [ ] **Step 4: Commit**

```bash
git add mobile/lib/features/dashboard/application/insights_controller.dart mobile/lib/features/dashboard/presentation/insights_section.dart
git commit -m "feat(mobile): dashboard agent-activity timeline card"
```

---

### Task 6: Watchlist — backend (new RethinkDB table + endpoints)

**Files:**
- Modify: `backend/interactive_utils.py` (add table + actions near the bot-trade-decisions section)
- Modify: `backend/api/main.py` (add 3 endpoints + imports)
- Test: `backend/tests/test_watchlist.py`

Per-user watchlist keyed by the authenticated user id (the JWT `sub`). One row per (user, symbol).

- [ ] **Step 1: Write the failing action test** (`backend/tests/test_watchlist.py`)

Use the in-memory doc shape only (pure parts); the DB actions are integration-style and validated by the API smoke. Test the doc-id builder:

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import interactive_utils as iu

def test_watchlist_doc_id_is_user_symbol():
    assert iu._watchlist_doc_id("u1", "aapl") == "u1:AAPL"
    assert iu._watchlist_doc_id("u1", " msft ") == "u1:MSFT"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && python3 -m pytest tests/test_watchlist.py -q`
Expected: FAIL — `_watchlist_doc_id` undefined.

- [ ] **Step 3: Implement actions** (append to `interactive_utils.py`, after the bot-trade-decisions section)

```python
# --- Watchlist (per-user tracked symbols) ---

WATCHLIST_TABLE = "Watchlist"


def ensure_watchlist_table(conn):
    dbs = list(r.db_list().run(conn))
    if DB_NAME not in dbs:
        r.db_create(DB_NAME).run(conn)
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if WATCHLIST_TABLE not in tables:
        r.db(DB_NAME).table_create(WATCHLIST_TABLE).run(conn)
    idxs = list(r.db(DB_NAME).table(WATCHLIST_TABLE).index_list().run(conn))
    if "user_id" not in idxs:
        r.db(DB_NAME).table(WATCHLIST_TABLE).index_create("user_id").run(conn)
        r.db(DB_NAME).table(WATCHLIST_TABLE).index_wait("user_id").run(conn)


def _watchlist_doc_id(user_id, symbol):
    return f"{user_id}:{str(symbol or '').strip().upper()}"


def action_list_watchlist(conn, user_id):
    ensure_watchlist_table(conn)
    rows = list(
        r.db(DB_NAME).table(WATCHLIST_TABLE)
        .get_all(str(user_id), index="user_id").run(conn)
    )
    rows.sort(key=lambda d: str(d.get("added_at") or ""), reverse=True)
    return {"symbols": [d.get("symbol") for d in rows], "items": rows}


def action_add_watchlist(conn, user_id, symbol):
    ensure_watchlist_table(conn)
    from datetime import datetime, timezone
    sym = str(symbol or "").strip().upper()
    if not sym:
        raise ValueError("symbol required")
    doc = {
        "id": _watchlist_doc_id(user_id, sym),
        "user_id": str(user_id),
        "symbol": sym,
        "added_at": datetime.now(timezone.utc).isoformat() + "Z",
    }
    r.db(DB_NAME).table(WATCHLIST_TABLE).insert(doc, conflict="replace").run(conn)
    return {"added": True, "symbol": sym}


def action_remove_watchlist(conn, user_id, symbol):
    ensure_watchlist_table(conn)
    r.db(DB_NAME).table(WATCHLIST_TABLE).get(
        _watchlist_doc_id(user_id, symbol)).delete().run(conn)
    return {"removed": True, "symbol": str(symbol or "").strip().upper()}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && python3 -m pytest tests/test_watchlist.py -q`
Expected: PASS.

- [ ] **Step 5: Add endpoints** (`backend/api/main.py`; add the three actions to the `interactive_utils` import block). The user id comes from `current_user` (the decoded JWT) — match how other per-user endpoints read it (verify the key, e.g. `current_user.get("sub")` or `["user_id"]`, against an existing per-user route before finalizing).

```python
@app.get("/watchlist", response_class=JSONResponse)
def api_watchlist_list(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    uid = current_user.get("sub") or current_user.get("user_id") or "default"
    return _run(action_list_watchlist, conn, uid)


@app.post("/watchlist/{symbol}", response_class=JSONResponse)
def api_watchlist_add(symbol: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    uid = current_user.get("sub") or current_user.get("user_id") or "default"
    return _run(action_add_watchlist, conn, uid, symbol)


@app.delete("/watchlist/{symbol}", response_class=JSONResponse)
def api_watchlist_remove(symbol: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    uid = current_user.get("sub") or current_user.get("user_id") or "default"
    return _run(action_remove_watchlist, conn, uid, symbol)
```

- [ ] **Step 6: Verify + commit**

Run: `cd backend && python3 -m py_compile api/main.py interactive_utils.py && python3 -m pytest tests/test_watchlist.py -q`
Expected: compiles + PASS.

```bash
git add backend/interactive_utils.py backend/api/main.py backend/tests/test_watchlist.py
git commit -m "feat(backend): per-user watchlist table + /watchlist endpoints"
```

---

### Task 7: Watchlist — mobile (add-from-stock-screen + dashboard card)

**Files:**
- Create: `mobile/lib/features/dashboard/application/watchlist_controller.dart`
- Modify: `mobile/lib/features/dashboard/presentation/insights_section.dart` (add `_WatchlistCard`, mount in `InsightsSection`)
- Modify: `mobile/lib/features/stock/presentation/stock_screen.dart` (add a star toggle in `_topBar`)

- [ ] **Step 1: Provider + mutations** (`watchlist_controller.dart`)

```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';

/// The user's watchlist symbols (uppercased). Never-throws.
final watchlistProvider = FutureProvider.autoDispose<List<String>>((ref) async {
  try {
    final data =
        await ref.read(apiClientProvider).get<Map<String, dynamic>>('/watchlist');
    return ((data['symbols'] as List?) ?? const [])
        .map((e) => e.toString().toUpperCase())
        .where((s) => s.isNotEmpty)
        .toList();
  } catch (_) {
    return const [];
  }
});

/// Add/remove helpers that refresh the provider on success.
Future<void> addToWatchlist(WidgetRef ref, String symbol) async {
  try {
    await ref.read(apiClientProvider).post<dynamic>('/watchlist/$symbol');
    ref.invalidate(watchlistProvider);
  } catch (_) {}
}

Future<void> removeFromWatchlist(WidgetRef ref, String symbol) async {
  try {
    await ref.read(apiClientProvider).delete<dynamic>('/watchlist/$symbol');
    ref.invalidate(watchlistProvider);
  } catch (_) {}
}
```

(Confirm `ApiClient` exposes `delete<T>` — if not, add it mirroring `post`.)

- [ ] **Step 2: `_WatchlistCard`** (append to `insights_section.dart`; mount in `InsightsSection` after the analytics group, before `_BotIdeasGroup`). Each symbol fetches a 1D sparkline via the existing `holdingsSparklinesProvider`-style path or `liveRepository.symbolHistoricals` — reuse a mini-spark widget; tap → stock screen. (Mirror Phase 1 `_MoverChip` styling for a compact list.)

```dart
class _WatchlistCard extends ConsumerWidget {
  const _WatchlistCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final symbols = ref.watch(watchlistProvider).valueOrNull;
    if (symbols == null || symbols.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _tileLabel('WATCHLIST'),
            const SizedBox(height: 8),
            for (final s in symbols)
              GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTap: () => context.push('/stock/$s',
                    extra: StockScreenArgs(brokerageId: brokerageId)),
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 9),
                  child: Row(
                    children: [
                      Text(s,
                          style: AppTextStyles.bodyHi
                              .copyWith(fontWeight: FontWeight.w700)),
                      const Spacer(),
                      Icon(symbol('arrow_forward'),
                          size: 13, color: AppColors.textFaint),
                    ],
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
```

- [ ] **Step 3: Star toggle on the stock screen** (`stock_screen.dart` `_topBar`): add a trailing star `IconButton` that watches `watchlistProvider` (contains `widget.symbol`?) and calls `addToWatchlist`/`removeFromWatchlist`. Use `symbol('star')` if present in `material_symbols.dart`, else add a `star`/`star_border` entry to that map first.

- [ ] **Step 4: Verify + commit**

Run: `cd mobile && flutter analyze lib/features/dashboard lib/features/stock`
Expected: No issues found.

```bash
git add mobile/lib/features/dashboard/application/watchlist_controller.dart mobile/lib/features/dashboard/presentation/insights_section.dart mobile/lib/features/stock/presentation/stock_screen.dart
git commit -m "feat(mobile): watchlist card + add/remove from stock screen"
```

---

### Task 8: Dividends received — INVESTIGATION-GATED

The Phase-1 audit found **no dividend fields** in the positions payload or any
`action_*` — so the data source does not yet exist. This task is gated on
finding/creating one; if the broker adapter can't readily supply dividend
history, **defer this feature** rather than fabricate it.

- [ ] **Step 1: Investigate the data source**

```bash
cd backend
grep -rniE "dividend|distribution|income" broker_adapters/ | grep -vi test | head -40
grep -rniE "get_activities|account_activities|NONTAXABLE|CASH_DISBURSEMENT|DIV" broker_adapters/alpaca.py | head
```

Determine: does the Alpaca adapter expose (or can it call) an account-activities
endpoint that returns dividend events (Alpaca `GET /v2/account/activities?activity_types=DIV`)?
Record the exact response fields.

- [ ] **Step 2: Decision point**
  - **If a source exists:** add `action_dividends_for_brokerage(conn, brokerage_id)` (resolve instance via `_resolve_instance_for_brokerage`, call the adapter's activities, sum YTD by symbol), expose `GET /brokerages/{id}/dividends`, then a mobile `dividendsProvider` + a small "Dividends (YTD)" line in the stock screen `_positionCard` and a dashboard total. Write the YTD-sum helper as a **pure, tested** function first (TDD).
  - **If no source / rate-limited / unreliable:** STOP. Document the gap in the spec's Phase-2 section and drop the feature. Do not ship placeholder/zero dividend data.

- [ ] **Step 3: Commit** (only if implemented)

```bash
git commit -m "feat: dividends received (YTD) per position + account total"
```

---

## Deploy & backend rollout

- Mobile tasks ship via `cd mobile && scripts/deploy.sh 1` after analyze passes.
- Backend tasks (Watchlist endpoints, any Dividends work) require the user's
  Dockploy deploy of `main` to take effect on the device.
