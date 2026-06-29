import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';
import 'models/strategy.dart';

/// Thin API layer for all strategy-related endpoints.
class StrategyRepository {
  const StrategyRepository(this._client);

  final ApiClient _client;

  // ── Strategy CRUD ──────────────────────────────────────────────────────────

  /// GET /strategies → {strategies: [...]}
  Future<List<Map<String, dynamic>>> list() async {
    final data = await _client.get<Map<String, dynamic>>('/strategies');
    return (data['strategies'] as List? ?? const [])
        .whereType<Map<String, dynamic>>()
        .toList();
  }

  /// GET /strategies/:id
  Future<Map<String, dynamic>> get(String id) async {
    final data = await _client.get<Map<String, dynamic>>('/strategies/$id');
    return (data['strategy'] as Map<String, dynamic>?) ?? data;
  }

  /// PUT /strategies/:id
  ///
  /// When [preserveHistory] is true, the backend re-stamps existing Nexus
  /// saved-state to the new model identities so the next boot reuses it instead
  /// of running a destructive lookback + cleanup. Set it only after the operator
  /// confirms the preserve-history prompt (see [previewConfigChange]).
  Future<Map<String, dynamic>> update(
    String id,
    Map<String, dynamic> body, {
    bool preserveHistory = false,
  }) async {
    final payload = preserveHistory
        ? {...body, 'preserve_history': true}
        : body;
    return _client.put<Map<String, dynamic>>('/strategies/$id', body: payload);
  }

  /// POST /strategies/:id/config-change-preview
  ///
  /// Read-only dry-run: returns `{needs_prompt, instances: [...]}` indicating
  /// whether saving [strategies] would rebuild Nexus history for any linked
  /// instance that has existing live state.
  Future<Map<String, dynamic>> previewConfigChange(
    String id,
    List<dynamic> strategies,
  ) async {
    return _client.post<Map<String, dynamic>>(
      '/strategies/$id/config-change-preview',
      body: {'strategies': strategies},
    );
  }

  /// GET /strategies/available → list of available strategy type names.
  Future<List<String>> available() async {
    final data = await _client.get<Map<String, dynamic>>('/strategies/available');
    return (data['strategies'] as List? ?? const [])
        .map((e) => e.toString())
        .toList();
  }

  // ── Agent result endpoints ─────────────────────────────────────────────────

  /// GET /agent/results?limit=10000 → {results: [...]}
  Future<List<AgentResult>> agentResults() async {
    final data = await _client.get<Map<String, dynamic>>(
      '/agent/results',
      query: {'limit': '10000'},
    );
    return (data['results'] as List? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(AgentResult.fromJson)
        .toList();
  }

  /// GET /agent/top5 → {top5: [...]}
  Future<List<Map<String, dynamic>>> top5() async {
    final data = await _client.get<Map<String, dynamic>>('/agent/top5');
    return (data['top5'] as List? ?? const [])
        .whereType<Map<String, dynamic>>()
        .toList();
  }

  /// GET /agent/best → the single best backtest record.
  Future<Map<String, dynamic>?> agentBest() async {
    try {
      return await _client.get<Map<String, dynamic>>('/agent/best');
    } catch (_) {
      return null;
    }
  }

  /// GET /backtests/best-per-strategy → {by_strategy: {str(id): {...}}}
  Future<Map<String, dynamic>> bestPerStrategy() async {
    final data =
        await _client.get<Map<String, dynamic>>('/backtests/best-per-strategy');
    return (data['by_strategy'] as Map?)?.cast<String, dynamic>() ??
        const {};
  }

  // ── Instances (for backtest modal) ─────────────────────────────────────────

  /// GET /instances → {instances: [...]}
  Future<List<Map<String, dynamic>>> instances() async {
    final data = await _client.get<Map<String, dynamic>>('/instances');
    return (data['instances'] as List? ?? const [])
        .whereType<Map<String, dynamic>>()
        .toList();
  }

  /// POST /instances (create new)
  Future<Map<String, dynamic>> createInstance(Map<String, dynamic> body) {
    return _client.post<Map<String, dynamic>>('/instances', body: body);
  }

  /// POST /instances/:id/link-strategy
  Future<void> linkStrategy(String instanceId, int strategyId) {
    return _client.post<dynamic>(
      '/instances/${Uri.encodeComponent(instanceId)}/link-strategy',
      body: {'strategy_id': strategyId},
    );
  }

  /// POST /backtests (create backtest run)
  Future<Map<String, dynamic>> createBacktest(Map<String, dynamic> body) {
    return _client.post<Map<String, dynamic>>('/backtests', body: body);
  }

  // ── Client-side merge helpers ──────────────────────────────────────────────

  /// Compute best-backtest stats per strategy from [results], mirroring the
  /// Vue `bestByStrategy` computed. Returns a map of strategyId → stats.
  static Map<int, BestPerStrategy> computeBestByStrategy(
    List<AgentResult> results,
  ) {
    final m = <int, BestPerStrategy>{};
    for (final r in results) {
      final sid = r.strategyId;
      if (sid == null) continue;
      final existing = m[sid];
      if (existing == null) {
        m[sid] = BestPerStrategy(
          strategyId: sid,
          bestPnl: r.overallProfit ?? 0,
          bestPnlBid: r.backtestId,
          bestPct: r.pnlPercent ?? 0,
          bestPctBid: r.backtestId,
          count: 1,
          latest: r.createdAt,
        );
      } else {
        existing.fold(r);
      }
    }
    return m;
  }

  /// Merge strategy list JSON with best-backtest stats and top-5 rank info.
  /// Mirrors the Vue `enrichedStrategies` computed.
  static List<StrategyListRow> mergeStrategyRows(
    List<Map<String, dynamic>> strategies,
    Map<int, BestPerStrategy> bestByStrategy,
    List<Map<String, dynamic>> top5Entries,
    Map<String, dynamic> allBestByStrat,
  ) {
    // Build rank map: strategy_id → rank
    final rankMap = <int, int>{};
    for (final e in top5Entries) {
      final sid = (e['strategy_id'] as num?)?.toInt();
      final rank = (e['rank'] as num?)?.toInt();
      if (sid != null && rank != null) rankMap[sid] = rank;
    }

    return strategies.map((s) {
      final sid = (s['id'] as num?)?.toInt() ?? 0;
      final best = bestByStrategy[sid];
      final top5entry = top5Entries.cast<Map<String, dynamic>?>().firstWhere(
            (e) => (e?['strategy_id'] as num?)?.toInt() == sid,
            orElse: () => null,
          );
      final allBest = allBestByStrat[sid.toString()] as Map?;
      final rank = rankMap[sid];

      double? bestPnl = best?.bestPnl;
      String? bestPnlBid = best?.bestPnlBid;
      double? bestPct = best?.bestPct;
      String? bestPctBid = best?.bestPctBid;
      int runCount = best?.count ?? 0;

      if (bestPnl == null && top5entry != null) {
        bestPnl = _toDouble(top5entry['overall_profit']);
        bestPnlBid = top5entry['backtest_id']?.toString();
      }
      if (bestPnl == null && allBest != null) {
        bestPnl = _toDouble(allBest['best_pnl']);
        bestPnlBid = allBest['backtest_id']?.toString();
      }
      if (bestPct == null && top5entry != null) {
        bestPct = _toDouble(top5entry['pnl_percent']);
      }
      if (bestPct == null && allBest != null) {
        bestPct = _toDouble(allBest['best_pct']);
      }
      bestPctBid ??= bestPnlBid;

      return StrategyListRow.fromStrategyJson(
        s,
        bestPnl: bestPnl,
        bestPnlBid: bestPnlBid,
        bestPct: bestPct,
        bestPctBid: bestPctBid,
        runCount: runCount,
        rank: rank,
      );
    }).toList();
  }

  static double? _toDouble(dynamic v) {
    if (v == null) return null;
    if (v is num) return v.toDouble();
    return double.tryParse(v.toString());
  }
}

final strategyRepositoryProvider = Provider<StrategyRepository>(
  (ref) => StrategyRepository(ref.watch(apiClientProvider)),
);
