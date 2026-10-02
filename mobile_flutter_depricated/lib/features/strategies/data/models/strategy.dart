/// Plain immutable models for the Strategies feature.
library;

// ── SubStrategy ──────────────────────────────────────────────────────────────

/// A single sub-strategy entry inside a [Strategy].
class SubStrategy {
  const SubStrategy({
    required this.strategy,
    required this.executionPosition,
    required this.decisionPhase,
    required this.weight,
    required this.executionScope,
    required this.config,
  });

  /// Strategy type name (e.g. "graph_nexus_analysis").
  final String strategy;
  final int executionPosition;

  /// "pre" | "entry" | "exit" | "post"
  final String decisionPhase;
  final double? weight;
  final String? executionScope;

  /// Merged conditions + config map (all non-null/non-empty entries).
  final Map<String, dynamic> config;

  factory SubStrategy.fromJson(Map<String, dynamic> j, {int fallbackPosition = 0}) {
    // Merge legacy conditions into config.
    final merged = <String, dynamic>{};
    for (final entry in (j['conditions'] as Map? ?? const {}).entries) {
      if (entry.value != null && entry.value != '') {
        merged[entry.key.toString()] = entry.value;
      }
    }
    for (final entry in (j['config'] as Map? ?? const {}).entries) {
      if (entry.value != null && entry.value != '') {
        merged[entry.key.toString()] = entry.value;
      }
    }
    return SubStrategy(
      strategy: (j['strategy'] ?? j['type'] ?? '').toString(),
      executionPosition:
          (j['execution_position'] as num?)?.toInt() ?? fallbackPosition,
      decisionPhase: (j['decision_phase'] ?? 'pre').toString(),
      weight: (j['weight'] as num?)?.toDouble(),
      executionScope: j['execution_scope']?.toString(),
      config: merged,
    );
  }
}

// ── Strategy ─────────────────────────────────────────────────────────────────

/// Full strategy object returned by GET /strategies/:id.
class Strategy {
  const Strategy({
    required this.id,
    required this.name,
    this.description,
    required this.strategies,
  });

  final int id;
  final String name;
  final String? description;
  final List<SubStrategy> strategies;

  factory Strategy.fromJson(Map<String, dynamic> j) {
    final rawSubs = j['strategies'] as List? ?? const [];
    final subs = <SubStrategy>[];
    for (int i = 0; i < rawSubs.length; i++) {
      final raw = rawSubs[i];
      if (raw is Map<String, dynamic>) {
        subs.add(SubStrategy.fromJson(raw, fallbackPosition: i));
      }
    }
    return Strategy(
      id: (j['id'] as num?)?.toInt() ?? 0,
      name: (j['name'] ?? '').toString(),
      description: j['description']?.toString(),
      strategies: subs,
    );
  }
}

// ── StrategyListRow ───────────────────────────────────────────────────────────

/// A merged row for the strategies list, combining API fields with
/// client-side best-backtest stats (mirrors the Vue enrichedStrategies computed).
class StrategyListRow {
  const StrategyListRow({
    required this.id,
    required this.name,
    required this.subCount,
    required this.instancesUsing,
    required this.runCount,
    this.bestPnl,
    this.bestPnlBid,
    this.bestPct,
    this.bestPctBid,
    this.rank,
    this.subStrategyNames = const [],
  });

  final int id;
  final String name;
  final int subCount;

  /// Instance IDs that reference this strategy.
  final List<String> instancesUsing;
  final int runCount;
  final double? bestPnl;

  /// Backtest ID with the best absolute P&L.
  final String? bestPnlBid;
  final double? bestPct;

  /// Backtest ID with the best P&L%.
  final String? bestPctBid;

  /// Top-5 rank (1–5), or null if not in top-5.
  final int? rank;

  /// Sub-strategy type names (for pills).
  final List<String> subStrategyNames;

  bool get isTop5 => rank != null;

  /// Build a [StrategyListRow] from a raw strategy JSON map + pre-computed
  /// best-backtest stats from agent results and the top-5 lists.
  factory StrategyListRow.fromStrategyJson(
    Map<String, dynamic> j, {
    double? bestPnl,
    String? bestPnlBid,
    double? bestPct,
    String? bestPctBid,
    int runCount = 0,
    int? rank,
  }) {
    final rawSubs = j['strategies'] as List? ?? const [];
    final subNames = <String>[];
    for (final s in rawSubs) {
      if (s is Map) {
        final name = (s['strategy'] ?? s['type'] ?? '').toString();
        if (name.isNotEmpty) subNames.add(name);
      }
    }
    final instances = <String>[];
    for (final i in (j['instances_using'] as List? ?? const [])) {
      instances.add(i.toString());
    }
    return StrategyListRow(
      id: (j['id'] as num?)?.toInt() ?? 0,
      name: (j['name'] ?? '').toString(),
      subCount: rawSubs.length,
      instancesUsing: instances,
      runCount: runCount,
      bestPnl: bestPnl,
      bestPnlBid: bestPnlBid,
      bestPct: bestPct,
      bestPctBid: bestPctBid,
      rank: rank,
      subStrategyNames: subNames,
    );
  }
}

// ── AgentResult ───────────────────────────────────────────────────────────────

/// Lightweight model for a single entry from GET /agent/results.
class AgentResult {
  const AgentResult({
    required this.backtestId,
    this.strategyId,
    this.overallProfit,
    this.pnlPercent,
    this.stocksUsed = const [],
    this.startDate,
    this.endDate,
    this.createdAt,
  });

  final String backtestId;
  final int? strategyId;
  final double? overallProfit;
  final double? pnlPercent;
  final List<String> stocksUsed;
  final String? startDate;
  final String? endDate;
  final String? createdAt;

  factory AgentResult.fromJson(Map<String, dynamic> j) {
    final stocks = <String>[];
    for (final s in (j['stocks_used'] as List? ?? const [])) {
      if (s is String && s.isNotEmpty) stocks.add(s);
    }
    return AgentResult(
      backtestId:
          (j['backtest_id'] ?? j['id'] ?? '').toString(),
      strategyId: (j['strategy_id'] as num?)?.toInt(),
      overallProfit: _toDouble(j['overall_profit']),
      pnlPercent: _toDouble(j['pnl_percent']),
      stocksUsed: stocks,
      startDate: j['start_date']?.toString(),
      endDate: j['end_date']?.toString(),
      createdAt: j['created_at']?.toString(),
    );
  }

  static double? _toDouble(dynamic v) {
    if (v == null) return null;
    if (v is num) return v.toDouble();
    return double.tryParse(v.toString());
  }
}

// ── BestPerStrategy ───────────────────────────────────────────────────────────

/// Holds the best backtest stats for a single strategy (client-computed).
class BestPerStrategy {
  BestPerStrategy({
    required this.strategyId,
    required this.bestPnl,
    required this.bestPnlBid,
    required this.bestPct,
    required this.bestPctBid,
    required this.count,
    required this.latest,
  });

  final int strategyId;
  double bestPnl;
  String bestPnlBid;
  double bestPct;
  String bestPctBid;
  int count;
  String? latest;

  /// Fold an additional [AgentResult] into this accumulator.
  void fold(AgentResult r) {
    final profit = r.overallProfit ?? 0;
    final pct = r.pnlPercent ?? 0;
    if (profit > bestPnl) {
      bestPnl = profit;
      bestPnlBid = r.backtestId;
    }
    if (pct > bestPct) {
      bestPct = pct;
      bestPctBid = r.backtestId;
    }
    count++;
    final ts = r.createdAt ?? '';
    if (latest == null || ts.compareTo(latest!) > 0) latest = ts;
  }
}
