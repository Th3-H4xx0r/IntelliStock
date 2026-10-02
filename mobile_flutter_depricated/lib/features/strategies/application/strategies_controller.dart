import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/strategy_repository.dart';
import '../data/models/strategy.dart';

// ── State ─────────────────────────────────────────────────────────────────────

enum StrategySortField { name, backtests, bestPnl, bestPct }

class StrategiesState {
  const StrategiesState({
    this.rawStrategies = const [],
    this.agentResults = const [],
    this.top5 = const [],
    this.allBestByStrat = const {},
    this.sortField = StrategySortField.bestPnl,
    this.sortAsc = false,
    this.page = 1,
    this.perPage = 20,
    this.loading = false,
    this.error,
  });

  final List<Map<String, dynamic>> rawStrategies;
  final List<AgentResult> agentResults;
  final List<Map<String, dynamic>> top5;
  final Map<String, dynamic> allBestByStrat;
  final StrategySortField sortField;
  final bool sortAsc;
  final int page;
  final int perPage;
  final bool loading;
  final String? error;

  StrategiesState copyWith({
    List<Map<String, dynamic>>? rawStrategies,
    List<AgentResult>? agentResults,
    List<Map<String, dynamic>>? top5,
    Map<String, dynamic>? allBestByStrat,
    StrategySortField? sortField,
    bool? sortAsc,
    int? page,
    int? perPage,
    bool? loading,
    Object? error = _sentinel,
  }) {
    return StrategiesState(
      rawStrategies: rawStrategies ?? this.rawStrategies,
      agentResults: agentResults ?? this.agentResults,
      top5: top5 ?? this.top5,
      allBestByStrat: allBestByStrat ?? this.allBestByStrat,
      sortField: sortField ?? this.sortField,
      sortAsc: sortAsc ?? this.sortAsc,
      page: page ?? this.page,
      perPage: perPage ?? this.perPage,
      loading: loading ?? this.loading,
      error: error == _sentinel ? this.error : error as String?,
    );
  }
}

const _sentinel = Object();

// ── Computed ──────────────────────────────────────────────────────────────────

List<StrategyListRow> _sortRows(
  List<StrategyListRow> rows,
  StrategySortField field,
  bool asc,
) {
  final list = [...rows];
  list.sort((a, b) {
    int cmp;
    switch (field) {
      case StrategySortField.name:
        cmp = a.name.toLowerCase().compareTo(b.name.toLowerCase());
      case StrategySortField.backtests:
        cmp = a.runCount.compareTo(b.runCount);
      case StrategySortField.bestPnl:
        final av = a.bestPnl ?? double.negativeInfinity;
        final bv = b.bestPnl ?? double.negativeInfinity;
        cmp = av.compareTo(bv);
      case StrategySortField.bestPct:
        final av = a.bestPct ?? double.negativeInfinity;
        final bv = b.bestPct ?? double.negativeInfinity;
        cmp = av.compareTo(bv);
    }
    return asc ? cmp : -cmp;
  });
  return list;
}

// ── Controller ────────────────────────────────────────────────────────────────

class StrategiesController extends AutoDisposeNotifier<StrategiesState> {
  @override
  StrategiesState build() {
    // Kick off load on first build.
    Future.microtask(fetchAll);
    return const StrategiesState(loading: true);
  }

  StrategyRepository get _repo => ref.read(strategyRepositoryProvider);

  Future<void> fetchAll() async {
    state = state.copyWith(loading: true, error: null);
    try {
      final results = await Future.wait([
        _repo.list().catchError((_) => <Map<String, dynamic>>[]),
        _repo.agentResults().catchError((_) => <AgentResult>[]),
        _repo.top5().catchError((_) => <Map<String, dynamic>>[]),
        _repo.bestPerStrategy().catchError((_) => <String, dynamic>{}),
      ]);
      final rawStrats = results[0] as List<Map<String, dynamic>>;
      final agentRes = results[1] as List<AgentResult>;
      final top5 = results[2] as List<Map<String, dynamic>>;
      // Sort top5 by rank ascending.
      top5.sort((a, b) =>
          ((a['rank'] as num?)?.toInt() ?? 99)
              .compareTo((b['rank'] as num?)?.toInt() ?? 99));
      final allBest = results[3] as Map<String, dynamic>;

      state = state.copyWith(
        rawStrategies: rawStrats,
        agentResults: agentRes,
        top5: top5,
        allBestByStrat: allBest,
        loading: false,
        page: 1,
        error: null,
      );
    } catch (e) {
      state = state.copyWith(loading: false, error: e.toString());
    }
  }

  void setSort(StrategySortField field) {
    if (state.sortField == field) {
      state = state.copyWith(sortAsc: !state.sortAsc, page: 1);
    } else {
      state = state.copyWith(sortField: field, sortAsc: false, page: 1);
    }
  }

  void setPage(int p) => state = state.copyWith(page: p);

  void setPerPage(int n) => state = state.copyWith(perPage: n, page: 1);

  // ── Computed helpers ───────────────────────────────────────────────────────

  List<StrategyListRow> get rows {
    final best = StrategyRepository.computeBestByStrategy(state.agentResults);
    final merged = StrategyRepository.mergeStrategyRows(
      state.rawStrategies,
      best,
      state.top5,
      state.allBestByStrat,
    );
    return _sortRows(merged, state.sortField, state.sortAsc);
  }

  List<Map<String, dynamic>> get top5Enriched {
    return state.top5.map((e) {
      final snapshot = e['strategy_snapshot'] as Map? ?? const {};
      final name = (snapshot['name'] ??
              (e['strategy_id'] != null
                  ? 'Strategy #${e['strategy_id']}'
                  : 'Strategy'))
          .toString();
      final subs = (snapshot['strategies'] as List? ?? const [])
          .map((s) => (s is Map ? (s['strategy'] ?? s['type'] ?? '?') : '?')
              .toString())
          .toList();
      return {...e, 'strategy_name': name, 'sub_strategies': subs};
    }).toList();
  }

  int get totalPages =>
      (rows.length / state.perPage).ceil().clamp(1, 999);

  List<StrategyListRow> get pagedRows {
    final all = rows;
    final start = (state.page - 1) * state.perPage;
    if (start >= all.length) return const [];
    return all.sublist(start, (start + state.perPage).clamp(0, all.length));
  }
}

final strategiesControllerProvider = AutoDisposeNotifierProvider<
    StrategiesController, StrategiesState>(
  StrategiesController.new,
);

// ── Detail controller ─────────────────────────────────────────────────────────

class StrategyDetailState {
  const StrategyDetailState({
    this.strategy,
    this.agentResults = const [],
    this.agentBestId,
    this.btSortField = 'created_at',
    this.btSortAsc = false,
    this.loading = false,
    this.error,
  });

  final Strategy? strategy;
  final List<AgentResult> agentResults;
  final String? agentBestId;
  final String btSortField;
  final bool btSortAsc;
  final bool loading;
  final String? error;

  StrategyDetailState copyWith({
    Strategy? strategy,
    List<AgentResult>? agentResults,
    Object? agentBestId = _sentinel,
    String? btSortField,
    bool? btSortAsc,
    bool? loading,
    Object? error = _sentinel,
  }) =>
      StrategyDetailState(
        strategy: strategy ?? this.strategy,
        agentResults: agentResults ?? this.agentResults,
        agentBestId: agentBestId == _sentinel
            ? this.agentBestId
            : agentBestId as String?,
        btSortField: btSortField ?? this.btSortField,
        btSortAsc: btSortAsc ?? this.btSortAsc,
        loading: loading ?? this.loading,
        error: error == _sentinel ? this.error : error as String?,
      );

  List<AgentResult> get strategyBacktests {
    final sid = strategy?.id;
    if (sid == null) return const [];
    return agentResults.where((r) => r.strategyId == sid).toList();
  }

  List<AgentResult> get sortedBacktests {
    final list = [...strategyBacktests];
    list.sort((a, b) {
      int cmp;
      switch (btSortField) {
        case 'pnl':
          cmp = (a.overallProfit ?? 0).compareTo(b.overallProfit ?? 0);
        case 'pct':
          cmp = (a.pnlPercent ?? 0).compareTo(b.pnlPercent ?? 0);
        default:
          cmp = (a.createdAt ?? '').compareTo(b.createdAt ?? '');
      }
      return btSortAsc ? cmp : -cmp;
    });
    return list;
  }

  AgentResult? get bestPnlBacktest {
    AgentResult? best;
    for (final r in strategyBacktests) {
      if (best == null ||
          (r.overallProfit ?? 0) > (best.overallProfit ?? 0)) {
        best = r;
      }
    }
    return best;
  }
}

class StrategyDetailController
    extends AutoDisposeFamilyNotifier<StrategyDetailState, String> {
  late final String _strategyId;

  @override
  StrategyDetailState build(String strategyId) {
    _strategyId = strategyId;
    Future.microtask(() => _load(strategyId));
    return const StrategyDetailState(loading: true);
  }

  StrategyRepository get _repo => ref.read(strategyRepositoryProvider);

  Future<void> _load(String id) async {
    state = state.copyWith(loading: true, error: null);
    try {
      final results = await Future.wait([
        _repo.get(id),
        _repo.agentResults().catchError((_) => <AgentResult>[]),
        _repo.agentBest(),
      ]);
      final stratJson = results[0] as Map<String, dynamic>;
      final agentRes = results[1] as List<AgentResult>;
      final bestData = results[2] as Map<String, dynamic>?;
      final bestId = (bestData?['id'] ?? bestData?['backtest_id'])?.toString();

      state = state.copyWith(
        strategy: Strategy.fromJson(stratJson),
        agentResults: agentRes,
        agentBestId: bestId,
        loading: false,
        error: null,
      );
    } catch (e) {
      state = state.copyWith(loading: false, error: e.toString());
    }
  }

  Future<void> refresh() => _load(_strategyId);

  void setBtSort(String field) {
    if (state.btSortField == field) {
      state = state.copyWith(btSortAsc: !state.btSortAsc);
    } else {
      state = state.copyWith(btSortField: field, btSortAsc: false);
    }
  }

  bool get isAgentBest =>
      state.agentBestId != null &&
      state.strategy != null &&
      state.agentBestId == state.strategy!.id.toString();

  String get strategyId => _strategyId;
}

final strategyDetailControllerProvider = AutoDisposeNotifierProvider.family<
    StrategyDetailController, StrategyDetailState, String>(
  StrategyDetailController.new,
);
