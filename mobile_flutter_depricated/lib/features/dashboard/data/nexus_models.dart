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

/// Aggregate signal->outcome scorecard (GET /brokerages/{id}/nexus-outcomes).
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

/// One newest watchlist entry. The strategy persists the bar it was first seen
/// and the price at that time (no return field is stored).
class WatchlistEntry {
  const WatchlistEntry(
      {required this.symbol, required this.firstSeenBar, required this.firstSeenPrice});
  final String symbol;
  final int firstSeenBar;
  final double firstSeenPrice;

  factory WatchlistEntry.fromJson(Map<String, dynamic> json) => WatchlistEntry(
        symbol: (json['symbol'] as String? ?? '').toUpperCase(),
        firstSeenBar: (json['first_seen_bar'] as num?)?.toInt() ?? 0,
        firstSeenPrice: (json['first_seen_price'] as num?)?.toDouble() ?? 0,
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
