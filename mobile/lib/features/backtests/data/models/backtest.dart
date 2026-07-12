// Plain immutable models for the backtests feature. No codegen.
// All fromJson factories are nullable-tolerant (missing fields default to null).

class BacktestRow {
  const BacktestRow({
    required this.id,
    this.instanceId,
    this.status,
    this.stocks = const [],
    this.startDate,
    this.endDate,
    this.completedAt,
    this.pnl,
    this.pnlPercent,
    this.timeElapsedSeconds,
  });

  final String id;
  final String? instanceId;
  final String? status;
  final List<String> stocks;
  final String? startDate;
  final String? endDate;
  final dynamic completedAt; // epoch or ISO
  final num? pnl;
  final num? pnlPercent;
  final num? timeElapsedSeconds;

  factory BacktestRow.fromJson(Map<String, dynamic> j) => BacktestRow(
        id: j['id']?.toString() ?? '',
        instanceId:
            (j['instance_id'] ?? j['instance'])?.toString(),
        status: j['status']?.toString(),
        stocks: _strList(j['stocks'] ?? j['tickers']),
        startDate: j['start_date']?.toString(),
        endDate: j['end_date']?.toString(),
        completedAt: j['completed_at'],
        pnl: _num(j['pnl']),
        pnlPercent: _num(j['pnl_percent']),
        timeElapsedSeconds: _num(j['time_elapsed_seconds']),
      );
}

class BacktestStatus {
  const BacktestStatus({
    this.status,
    this.progress,
    this.nexusLookback,
    this.timeElapsedSeconds,
  });

  final String? status;
  final num? progress;
  final NexusLookback? nexusLookback;
  final num? timeElapsedSeconds;

  factory BacktestStatus.fromJson(Map<String, dynamic> j) => BacktestStatus(
        status: j['status']?.toString(),
        progress: _num(j['progress']),
        nexusLookback: j['nexus_lookback'] is Map
            ? NexusLookback.fromJson(
                Map<String, dynamic>.from(j['nexus_lookback'] as Map))
            : null,
        timeElapsedSeconds: _num(j['time_elapsed_seconds']),
      );
}

class NexusLookback {
  const NexusLookback({
    required this.current,
    required this.total,
    this.currentDate,
    this.startDate,
    this.endDate,
  });

  final int current;
  final int total;
  final String? currentDate;
  final String? startDate;
  final String? endDate;

  factory NexusLookback.fromJson(Map<String, dynamic> j) => NexusLookback(
        current: (j['current'] as num?)?.toInt() ?? 0,
        total: (j['total'] as num?)?.toInt() ?? 0,
        currentDate: j['current_date']?.toString(),
        startDate: j['start_date']?.toString(),
        endDate: j['end_date']?.toString(),
      );

  double get fraction => total > 0 ? current / total : 0;
}

class BacktestSummary {
  const BacktestSummary({
    this.id,
    this.status,
    this.pnl,
    this.pnlPercent,
    this.fees,
    this.portfolioStartValue,
    this.portfolioEndValue,
    this.totalTrades,
    this.totalBuys,
    this.totalSells,
    this.timeElapsedSeconds,
    this.winRatePercent,
    this.winningRoundTrips,
    this.losingRoundTrips,
    this.portfolioValueHigh,
    this.portfolioValueLow,
    this.roundTrips,
    this.pnlPerStock,
    this.pnlPercentPerStock,
    this.stockPriceChange,
    this.tickers = const [],
    this.startDate,
    this.endDate,
    this.strategySchema,
    this.strategyId,
    this.instanceId,
    this.granularity,
    this.initialCash,
    // LLM pause fields
    this.pauseReasonTag,
    this.pauseProvider,
    this.pauseModel,
    this.pauseCallSite,
    this.pauseAttempts,
    this.pauseBarTime,
    this.pausedAt,
    this.pauseSample,
    // round-trip extra
    this.totalRoundTripPnl,
    this.avgWinningRoundTrip,
    this.avgLosingRoundTrip,
  });

  final String? id;
  final String? status;
  final num? pnl;
  final num? pnlPercent;
  /// Crypto fee accounting {total_fees, total_volume, taker_rate}; null for
  /// equity (commission-free) runs.
  final Map<String, num>? fees;
  final num? portfolioStartValue;
  final num? portfolioEndValue;
  final num? totalTrades;
  final num? totalBuys;
  final num? totalSells;
  final num? timeElapsedSeconds;
  final num? winRatePercent;
  final num? winningRoundTrips;
  final num? losingRoundTrips;
  final num? portfolioValueHigh;
  final num? portfolioValueLow;
  final num? roundTrips;
  final Map<String, num>? pnlPerStock;
  final Map<String, num>? pnlPercentPerStock;
  final Map<String, num>? stockPriceChange;
  final List<String> tickers;
  final String? startDate;
  final String? endDate;
  final StrategySchema? strategySchema;
  final String? strategyId;
  final String? instanceId;
  final String? granularity;
  final num? initialCash;
  // LLM pause
  final String? pauseReasonTag;
  final String? pauseProvider;
  final String? pauseModel;
  final String? pauseCallSite;
  final num? pauseAttempts;
  final String? pauseBarTime;
  final dynamic pausedAt;
  final String? pauseSample;
  // round-trips
  final num? totalRoundTripPnl;
  final num? avgWinningRoundTrip;
  final num? avgLosingRoundTrip;

  factory BacktestSummary.fromJson(Map<String, dynamic> j) => BacktestSummary(
        id: j['id']?.toString(),
        status: j['status']?.toString(),
        pnl: _num(j['pnl']),
        pnlPercent: _num(j['pnl_percent']),
        fees: _numMap(j['fees']),
        portfolioStartValue: _num(j['portfolio_start_value']),
        portfolioEndValue: _num(j['portfolio_end_value']),
        totalTrades: _num(j['total_trades']),
        totalBuys: _num(j['total_buys']),
        totalSells: _num(j['total_sells']),
        timeElapsedSeconds: _num(j['time_elapsed_seconds']),
        winRatePercent: _num(j['win_rate_percent']),
        winningRoundTrips: _num(j['winning_round_trips']),
        losingRoundTrips: _num(j['losing_round_trips']),
        portfolioValueHigh: _num(j['portfolio_value_high']),
        portfolioValueLow: _num(j['portfolio_value_low']),
        roundTrips: _num(j['round_trips']),
        pnlPerStock: _numMap(j['pnl_per_stock']),
        pnlPercentPerStock: _numMap(j['pnl_percent_per_stock']),
        stockPriceChange: _changePctMap(j['stock_price_change']),
        tickers: _strList(j['tickers']),
        startDate: j['start_date']?.toString(),
        endDate: j['end_date']?.toString(),
        strategySchema: j['strategy_schema'] is Map
            ? StrategySchema.fromJson(
                Map<String, dynamic>.from(j['strategy_schema'] as Map))
            : null,
        strategyId: j['strategy_id']?.toString(),
        instanceId: (j['instance_id'] ?? j['instance'])?.toString(),
        granularity: j['granularity']?.toString(),
        initialCash: _num(j['initial_cash']),
        pauseReasonTag: j['pause_reason_tag']?.toString(),
        pauseProvider: j['pause_provider']?.toString(),
        pauseModel: j['pause_model']?.toString(),
        pauseCallSite: j['pause_call_site']?.toString(),
        pauseAttempts: _num(j['pause_attempts']),
        pauseBarTime: j['pause_bar_time']?.toString(),
        pausedAt: j['paused_at'],
        pauseSample: j['pause_sample']?.toString(),
        totalRoundTripPnl: _num(j['total_round_trip_pnl']),
        avgWinningRoundTrip: _num(j['avg_winning_round_trip']),
        avgLosingRoundTrip: _num(j['avg_losing_round_trip']),
      );
}

class StrategySchema {
  const StrategySchema({this.name, this.strategies = const []});

  final String? name;
  final List<SubStrategy> strategies;

  factory StrategySchema.fromJson(Map<String, dynamic> j) => StrategySchema(
        name: j['name']?.toString(),
        strategies: (j['strategies'] as List? ?? [])
            .whereType<Map>()
            .map((s) =>
                SubStrategy.fromJson(Map<String, dynamic>.from(s)))
            .toList(),
      );
}

class SubStrategy {
  const SubStrategy({
    this.strategy,
    this.weight,
    this.executionPosition,
    this.decisionPhase,
    this.executionScope,
    this.conditions = const {},
    this.config = const {},
  });

  final String? strategy;
  final num? weight;
  final num? executionPosition;
  final String? decisionPhase;
  final String? executionScope;
  final Map<String, dynamic> conditions;
  final Map<String, dynamic> config;

  factory SubStrategy.fromJson(Map<String, dynamic> j) => SubStrategy(
        strategy: j['strategy']?.toString(),
        weight: _num(j['weight']),
        executionPosition: _num(j['execution_position']),
        decisionPhase: j['decision_phase']?.toString(),
        executionScope: j['execution_scope']?.toString(),
        conditions: j['conditions'] is Map
            ? Map<String, dynamic>.from(j['conditions'] as Map)
            : const {},
        config: j['config'] is Map
            ? Map<String, dynamic>.from(j['config'] as Map)
            : const {},
      );
}

class LlmCost {
  const LlmCost({
    this.totalCostUsd,
    this.totalCalls,
    this.okCalls,
    this.failedCalls,
    this.totalInputTokens,
    this.totalOutputTokens,
    this.totalReasoningTokens,
    this.byModel = const [],
    this.byCallSite = const [],
    this.byProvider = const [],
  });

  final num? totalCostUsd;
  final num? totalCalls;
  final num? okCalls;
  final num? failedCalls;
  final num? totalInputTokens;
  final num? totalOutputTokens;
  final num? totalReasoningTokens;
  final List<LlmCostRow> byModel;
  final List<LlmCostRow> byCallSite;
  final List<LlmCostRow> byProvider;

  factory LlmCost.fromJson(Map<String, dynamic> j) => LlmCost(
        totalCostUsd: _num(j['total_cost_usd']),
        totalCalls: _num(j['total_calls']),
        okCalls: _num(j['ok_calls']),
        failedCalls: _num(j['failed_calls']),
        totalInputTokens: _num(j['total_input_tokens']),
        totalOutputTokens: _num(j['total_output_tokens']),
        totalReasoningTokens: _num(j['total_reasoning_tokens']),
        byModel: _costRows(j['by_model']),
        byCallSite: _costRows(j['by_call_site']),
        byProvider: _costRows(j['by_provider']),
      );

  static List<LlmCostRow> _costRows(dynamic v) {
    if (v is! List) return const [];
    return v
        .whereType<Map>()
        .map((m) => LlmCostRow.fromJson(Map<String, dynamic>.from(m)))
        .toList();
  }
}

class LlmCostRow {
  const LlmCostRow({required this.key, this.costUsd});

  final String key;
  final num? costUsd;

  factory LlmCostRow.fromJson(Map<String, dynamic> j) => LlmCostRow(
        key: j['key']?.toString() ?? '?',
        costUsd: _num(j['cost_usd']),
      );
}

class PortfolioValuePoint {
  const PortfolioValuePoint({required this.timestamp, required this.value});

  final DateTime timestamp;
  final double value;

  factory PortfolioValuePoint.fromJson(Map<String, dynamic> j) {
    final raw = j['timestamp'] ?? j['date'] ?? j['time'];
    final dt = raw is num
        ? DateTime.fromMillisecondsSinceEpoch(
            raw > 1e12 ? raw.toInt() : (raw * 1000).toInt())
        : (DateTime.tryParse(raw?.toString() ?? '') ?? DateTime.now());
    return PortfolioValuePoint(
      timestamp: dt,
      value: (j['value'] as num?)?.toDouble() ?? 0,
    );
  }
}

class BacktestTrade {
  const BacktestTrade({
    required this.ticker,
    this.action,
    this.timestamp,
    this.price,
    this.shares,
    this.total,
    this.cashAfter,
  });

  final String ticker;
  final String? action;
  final DateTime? timestamp;
  final num? price;
  final num? shares;
  final num? total;
  final num? cashAfter;

  factory BacktestTrade.fromJson(Map<String, dynamic> j) => BacktestTrade(
        ticker: (j['ticker'] ?? j['symbol'] ?? '').toString(),
        action: j['action']?.toString(),
        timestamp: DateTime.tryParse(j['timestamp']?.toString() ?? ''),
        price: _num(j['price']),
        shares: _num(j['shares']),
        total: _num(j['total']),
        cashAfter: _num(j['cash_after']),
      );
}

class BacktestDecision {
  const BacktestDecision({
    this.symbol,
    this.timestamp,
    this.decision,
    this.action,
    this.normalizedScore,
    this.finalReason,
    this.overrideApplied,
    this.preOverrideAction,
    this.preOverrideDecision,
    this.primaryStrategy,
    this.primaryActionIntent,
    this.strategies = const [],
    this.postDecision = const [],
    this.rawJson = const {},
  });

  final String? symbol;
  final DateTime? timestamp;
  final dynamic decision; // int or null
  final String? action;
  final num? normalizedScore;
  final String? finalReason;
  final bool? overrideApplied;
  final String? preOverrideAction;
  final dynamic preOverrideDecision;
  final String? primaryStrategy;
  final String? primaryActionIntent;
  final List<DecisionStrategy> strategies;
  final List<PostDecision> postDecision;
  final Map<String, dynamic> rawJson;

  factory BacktestDecision.fromJson(Map<String, dynamic> j) => BacktestDecision(
        symbol: j['symbol']?.toString(),
        timestamp: DateTime.tryParse(j['timestamp']?.toString() ?? ''),
        decision: j['decision'],
        action: j['action']?.toString(),
        normalizedScore: _num(j['normalized_score']),
        finalReason: j['final_reason']?.toString(),
        overrideApplied: j['override_applied'] as bool?,
        preOverrideAction: j['pre_override_action']?.toString(),
        preOverrideDecision: j['pre_override_decision'],
        primaryStrategy: j['primary_strategy']?.toString(),
        primaryActionIntent: j['primary_action_intent']?.toString(),
        strategies: (j['strategies'] as List? ?? [])
            .whereType<Map>()
            .map((s) =>
                DecisionStrategy.fromJson(Map<String, dynamic>.from(s)))
            .toList(),
        postDecision: (j['post_decision'] as List? ?? [])
            .whereType<Map>()
            .map((p) =>
                PostDecision.fromJson(Map<String, dynamic>.from(p)))
            .toList(),
        rawJson: j,
      );

  String decisionLabel() {
    if (action != null && action!.isNotEmpty) return action!.toUpperCase();
    switch (decision?.toString()) {
      case '1':
        return 'BUY';
      case '-1':
        return 'SELL';
      default:
        return 'HOLD';
    }
  }
}

class DecisionStrategy {
  const DecisionStrategy({
    this.strategy,
    this.decision,
    this.weight,
    this.actionIntent,
    this.reason,
  });

  final String? strategy;
  final dynamic decision;
  final num? weight;
  final String? actionIntent;
  final String? reason;

  factory DecisionStrategy.fromJson(Map<String, dynamic> j) => DecisionStrategy(
        strategy: j['strategy']?.toString(),
        decision: j['decision'],
        weight: _num(j['weight']),
        actionIntent: j['action_intent']?.toString(),
        reason: j['reason']?.toString(),
      );

  String decisionLabel() {
    switch (decision?.toString()) {
      case '1':
        return 'BUY';
      case '-1':
        return 'SELL';
      default:
        return 'HOLD';
    }
  }
}

class PostDecision {
  const PostDecision({this.strategy, this.decision, this.reason});

  final String? strategy;
  final dynamic decision;
  final String? reason;

  factory PostDecision.fromJson(Map<String, dynamic> j) => PostDecision(
        strategy: j['strategy']?.toString(),
        decision: j['decision'],
        reason: j['reason']?.toString(),
      );
}

class BacktestPrice {
  const BacktestPrice({
    required this.symbol,
    required this.timestamp,
    required this.close,
  });

  final String symbol;
  final DateTime timestamp;
  final double close;

  factory BacktestPrice.fromJson(Map<String, dynamic> j) => BacktestPrice(
        symbol: (j['symbol'] ?? '').toString(),
        timestamp: DateTime.tryParse(j['timestamp']?.toString() ?? '') ??
            DateTime.now(),
        close: (j['close'] as num?)?.toDouble() ?? 0,
      );
}

class BacktestGraphData {
  const BacktestGraphData({
    this.portfolioValueHistory = const [],
    this.backtestPrices = const [],
    this.backtestTrades = const [],
    this.backtestDecisions = const [],
  });

  final List<PortfolioValuePoint> portfolioValueHistory;
  final List<BacktestPrice> backtestPrices;
  final List<BacktestTrade> backtestTrades;
  final List<BacktestDecision> backtestDecisions;

  factory BacktestGraphData.fromJson(Map<String, dynamic> j) =>
      BacktestGraphData(
        portfolioValueHistory: _mapList(
            j['portfolio_value_history'], PortfolioValuePoint.fromJson),
        backtestPrices:
            _mapList(j['backtest_prices'], BacktestPrice.fromJson),
        backtestTrades:
            _mapList(j['backtest_trades'], BacktestTrade.fromJson),
        backtestDecisions:
            _mapList(j['backtest_decisions'], BacktestDecision.fromJson),
      );
}

// ── Playback ──────────────────────────────────────────────────────────────────

class PlaybackMetadata {
  const PlaybackMetadata({this.initialCash, this.extra = const {}});

  final num? initialCash;
  final Map<String, dynamic> extra;

  factory PlaybackMetadata.fromJson(Map<String, dynamic> j) => PlaybackMetadata(
        initialCash: _num(j['initial_cash']),
        extra: j,
      );
}

class PlaybackEvent {
  const PlaybackEvent({
    required this.id,
    required this.type,
    this.label,
    this.time,
    this.name,
    this.desc,
    this.reason,
    this.decision,
    this.tickers = const [],
    this.details,
    this.buys = const [],
    this.sells = const [],
    this.portfolioValue,
    this.holdings = const [],
    this.date,
    this.raw = const {},
  });

  final String id;
  final String type; // 'date' | 'strategy' | 'outcome' | 'decision' | 'portfolio'
  final String? label;
  final String? time;
  final String? name;
  final String? desc;
  final String? reason;
  final String? decision;
  final List<String> tickers;
  final String? details;
  final List<PlaybackTrade> buys;
  final List<PlaybackTrade> sells;
  final num? portfolioValue;
  final List<PlaybackHolding> holdings;
  final String? date;
  final Map<String, dynamic> raw;

  factory PlaybackEvent.fromJson(Map<String, dynamic> j, int index) =>
      PlaybackEvent(
        id: '${j['type']}_$index',
        type: j['type']?.toString() ?? 'unknown',
        label: j['label']?.toString(),
        time: j['time']?.toString(),
        name: j['name']?.toString(),
        desc: j['desc']?.toString(),
        reason: j['reason']?.toString(),
        decision: j['decision']?.toString(),
        tickers: _strList(j['tickers']),
        details: j['details']?.toString(),
        buys: _tradeList(j['buys']),
        sells: _tradeList(j['sells']),
        portfolioValue: _num(j['value']),
        holdings: (j['holdings'] as List? ?? [])
            .whereType<Map>()
            .map((h) => PlaybackHolding.fromJson(Map<String, dynamic>.from(h)))
            .toList(),
        date: j['date']?.toString(),
        raw: j,
      );

  static List<PlaybackTrade> _tradeList(dynamic v) {
    if (v is! List) return const [];
    return v
        .whereType<Map>()
        .map((m) => PlaybackTrade.fromJson(Map<String, dynamic>.from(m)))
        .toList();
  }
}

class PlaybackTrade {
  const PlaybackTrade({this.ticker, this.qty, this.price, this.reason});

  final String? ticker;
  final num? qty;
  final num? price;
  final String? reason;

  factory PlaybackTrade.fromJson(Map<String, dynamic> j) => PlaybackTrade(
        ticker: j['ticker']?.toString(),
        qty: _num(j['qty']),
        price: _num(j['price']),
        reason: j['reason']?.toString(),
      );
}

class PlaybackHolding {
  const PlaybackHolding(
      {required this.ticker, this.qty, this.avg, this.curr});

  final String ticker;
  final num? qty;
  final num? avg;
  final num? curr;

  factory PlaybackHolding.fromJson(Map<String, dynamic> j) => PlaybackHolding(
        ticker: (j['ticker'] ?? j['symbol'] ?? '').toString(),
        qty: _num(j['qty']),
        avg: _num(j['avg']),
        curr: _num(j['curr']),
      );
}

class PlaybackData {
  const PlaybackData({
    required this.events,
    required this.metadata,
  });

  final List<PlaybackEvent> events;
  final PlaybackMetadata metadata;

  factory PlaybackData.fromJson(Map<String, dynamic> j) => PlaybackData(
        events: (j['events'] as List? ?? [])
            .whereType<Map>()
            .toList()
            .asMap()
            .entries
            .map((e) => PlaybackEvent.fromJson(
                Map<String, dynamic>.from(e.value), e.key))
            .toList(),
        metadata: j['metadata'] is Map
            ? PlaybackMetadata.fromJson(
                Map<String, dynamic>.from(j['metadata'] as Map))
            : const PlaybackMetadata(),
      );
}

class BacktestListResponse {
  const BacktestListResponse({
    required this.backtests,
    required this.total,
    required this.totalPages,
    required this.page,
  });

  final List<BacktestRow> backtests;
  final int total;
  final int totalPages;
  final int page;

  factory BacktestListResponse.fromJson(Map<String, dynamic> j) =>
      BacktestListResponse(
        backtests: (j['backtests'] as List? ?? [])
            .whereType<Map>()
            .map((b) =>
                BacktestRow.fromJson(Map<String, dynamic>.from(b)))
            .toList(),
        total: (j['total'] as num?)?.toInt() ?? 0,
        totalPages: (j['total_pages'] as num?)?.toInt() ?? 1,
        page: (j['page'] as num?)?.toInt() ?? 1,
      );
}

// ── Shared helpers ────────────────────────────────────────────────────────────

num? _num(dynamic v) {
  if (v == null) return null;
  if (v is num) return v;
  return num.tryParse(v.toString());
}

List<String> _strList(dynamic v) {
  if (v is! List) return const [];
  return v.map((e) => e.toString()).toList();
}

Map<String, num>? _numMap(dynamic v) {
  if (v is! Map) return null;
  final result = <String, num>{};
  v.forEach((k, val) {
    final n = _num(val);
    if (n != null) result[k.toString()] = n;
  });
  return result.isEmpty ? null : result;
}

/// stock_price_change values are dicts {start_price, end_price, change_percent};
/// pull the change_percent so the UI renders the real %, not '—' or NaN%.
Map<String, num>? _changePctMap(dynamic v) {
  if (v is! Map) return null;
  final result = <String, num>{};
  v.forEach((k, val) {
    if (val is Map) {
      final n = _num(val['change_percent']);
      if (n != null) result[k.toString()] = n;
    } else {
      final n = _num(val); // tolerate a flat-number shape too
      if (n != null) result[k.toString()] = n;
    }
  });
  return result.isEmpty ? null : result;
}

List<T> _mapList<T>(dynamic v, T Function(Map<String, dynamic>) fromJson) {
  if (v is! List) return const [];
  return v
      .whereType<Map>()
      .map((m) => fromJson(Map<String, dynamic>.from(m)))
      .toList();
}
