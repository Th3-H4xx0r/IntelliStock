/// Plain immutable model for a trading instance.
class Instance {
  const Instance({
    required this.id,
    required this.name,
    required this.createdBy,
    required this.runCommand,
    this.crashed = false,
    this.strategyId,
    this.brokerageId,
    this.granularityTimeIncrement,
    this.maxUsage,
    this.uptimeSeconds,
    this.stocks = const [],
    this.brokerage,
    this.strategy,
    this.alpacaDataBrokerageId,
  });

  final String id;
  final String name;

  /// 'user' | 'ai'
  final String createdBy;
  final bool runCommand;

  /// True when the instance process died (not an operator Stop) and is held open
  /// for log capture — surfaced as a 'Crashed' badge.
  final bool crashed;
  final String? strategyId;
  final String? brokerageId;
  final String? alpacaDataBrokerageId;

  /// Granularity in seconds (e.g. 60, 300, 900, 3600, 86400).
  final int? granularityTimeIncrement;
  final double? maxUsage;
  final int? uptimeSeconds;
  final List<String> stocks;

  /// Nested brokerage map (if API returns it).
  final Map<String, dynamic>? brokerage;

  /// Nested strategy map (if API returns it).
  final Map<String, dynamic>? strategy;

  factory Instance.fromJson(Map<String, dynamic> j) {
    final stocks = <String>[];
    for (final s in (j['stocks'] as List? ?? const [])) {
      if (s is Map) {
        final sym = s['symbol'] ?? s['ticker'] ?? '';
        if (sym.toString().isNotEmpty) stocks.add(sym.toString());
      } else if (s is String && s.isNotEmpty) {
        stocks.add(s);
      }
    }
    return Instance(
      id: (j['id'] ?? '').toString(),
      name: (j['name'] ?? j['id'] ?? '').toString(),
      createdBy: (j['created_by'] ?? 'user').toString(),
      runCommand: j['run_command'] == true ||
          j['runCommand'] == true,
      crashed: j['crashed'] == true,
      strategyId: j['strategy_id']?.toString(),
      brokerageId: j['brokerage_id']?.toString(),
      alpacaDataBrokerageId: j['alpaca_data_brokerage_id']?.toString(),
      granularityTimeIncrement:
          (j['granularity_time_increment'] as num?)?.toInt() ??
              (j['granularity'] != null
                  ? int.tryParse(j['granularity'].toString())
                  : null),
      maxUsage: (j['max_usage'] as num?)?.toDouble(),
      uptimeSeconds: (j['uptime_seconds'] as num?)?.toInt(),
      stocks: stocks,
      brokerage: j['brokerage'] as Map<String, dynamic>?,
      strategy: j['strategy'] as Map<String, dynamic>?,
    );
  }

  Instance copyWith({
    String? id,
    String? name,
    String? createdBy,
    bool? runCommand,
    bool? crashed,
    Object? strategyId = _sentinel,
    Object? brokerageId = _sentinel,
    Object? alpacaDataBrokerageId = _sentinel,
    Object? granularityTimeIncrement = _sentinel,
    Object? maxUsage = _sentinel,
    Object? uptimeSeconds = _sentinel,
    List<String>? stocks,
    Object? brokerage = _sentinel,
    Object? strategy = _sentinel,
  }) =>
      Instance(
        id: id ?? this.id,
        name: name ?? this.name,
        createdBy: createdBy ?? this.createdBy,
        runCommand: runCommand ?? this.runCommand,
        crashed: crashed ?? this.crashed,
        strategyId:
            strategyId == _sentinel ? this.strategyId : strategyId as String?,
        brokerageId:
            brokerageId == _sentinel ? this.brokerageId : brokerageId as String?,
        alpacaDataBrokerageId: alpacaDataBrokerageId == _sentinel
            ? this.alpacaDataBrokerageId
            : alpacaDataBrokerageId as String?,
        granularityTimeIncrement: granularityTimeIncrement == _sentinel
            ? this.granularityTimeIncrement
            : granularityTimeIncrement as int?,
        maxUsage:
            maxUsage == _sentinel ? this.maxUsage : maxUsage as double?,
        uptimeSeconds: uptimeSeconds == _sentinel
            ? this.uptimeSeconds
            : uptimeSeconds as int?,
        stocks: stocks ?? this.stocks,
        brokerage:
            brokerage == _sentinel ? this.brokerage : brokerage as Map<String, dynamic>?,
        strategy:
            strategy == _sentinel ? this.strategy : strategy as Map<String, dynamic>?,
      );
}

const _sentinel = Object();

// ────────────────────────────────────────────────────────────────────────────
// BacktestRow — lightweight model for the backtests list.
// ────────────────────────────────────────────────────────────────────────────

class BacktestRow {
  const BacktestRow({
    required this.id,
    required this.stocks,
    this.startDate,
    this.endDate,
    this.completedAt,
    required this.status,
    this.timeElapsedSeconds,
    this.pnl,
    this.pnlPercent,
    this.instanceId,
    this.granularity,
    this.initialCash,
    this.progress,
  });

  final String id;
  final List<String> stocks;
  final String? startDate;
  final String? endDate;
  final String? completedAt;
  final String status;
  final double? timeElapsedSeconds;
  final double? pnl;
  final double? pnlPercent;
  final String? instanceId;
  final String? granularity;
  final double? initialCash;

  /// Progress 0–100 from the polling endpoint.
  final int? progress;

  factory BacktestRow.fromJson(Map<String, dynamic> j) {
    final stocks = <String>[];
    for (final s in (j['stocks'] as List? ?? const [])) {
      if (s is String && s.isNotEmpty) stocks.add(s);
    }
    return BacktestRow(
      id: (j['id'] ?? '').toString(),
      stocks: stocks,
      startDate: j['start_date']?.toString(),
      endDate: j['end_date']?.toString(),
      completedAt: j['completed_at']?.toString(),
      status: (j['status'] ?? 'unknown').toString(),
      timeElapsedSeconds: (j['time_elapsed_seconds'] as num?)?.toDouble(),
      pnl: (j['pnl'] as num?)?.toDouble(),
      pnlPercent: (j['pnl_percent'] as num?)?.toDouble(),
      instanceId: j['instance_id']?.toString(),
      granularity: j['granularity']?.toString(),
      initialCash: (j['initial_cash'] as num?)?.toDouble(),
      progress: (j['progress'] as num?)?.toInt(),
    );
  }

  BacktestRow copyWith({int? progress, String? status}) => BacktestRow(
        id: id,
        stocks: stocks,
        startDate: startDate,
        endDate: endDate,
        completedAt: completedAt,
        status: status ?? this.status,
        timeElapsedSeconds: timeElapsedSeconds,
        pnl: pnl,
        pnlPercent: pnlPercent,
        instanceId: instanceId,
        granularity: granularity,
        initialCash: initialCash,
        progress: progress ?? this.progress,
      );
}
