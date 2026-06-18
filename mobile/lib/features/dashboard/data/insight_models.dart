import '../../../core/formatters/formatters.dart';

/// A stock the Discover engine flagged as an opportunity, from
/// GET /brokerages/{id}/discovered (`_NEXUS_DISCOVERED_TABLE` rows). Field names
/// are matched with fallbacks since the writer's exact keys vary.
class DiscoveredStock {
  const DiscoveredStock({
    required this.ticker,
    this.reason = '',
    this.discoveredAt,
    this.score,
  });

  final String ticker;
  final String reason;
  final DateTime? discoveredAt;
  final double? score;

  factory DiscoveredStock.fromJson(Map<String, dynamic> j) => DiscoveredStock(
        ticker: (j['ticker'] ?? j['symbol'] ?? '').toString().toUpperCase(),
        reason:
            (j['reason'] ?? j['rationale'] ?? j['description'] ?? j['summary'] ?? '')
                .toString(),
        discoveredAt:
            parseDateTime(j['discovered_at'] ?? j['created_at'] ?? j['updated_at']),
        score: (j['score'] as num?)?.toDouble() ??
            (j['confidence'] as num?)?.toDouble(),
      );
}

/// A market trend/theme detected by the Nexus/Discover engines, from
/// GET /brokerages/{id}/trends (`_NEXUS_TRENDS_TABLE` rows).
class MarketTrend {
  const MarketTrend({
    required this.id,
    this.title = '',
    this.description = '',
    this.symbols = const [],
    this.strength,
  });

  final String id;
  final String title;
  final String description;
  final List<String> symbols;
  final double? strength; // 0..1 (or 0..100); rendered as a relative bar

  factory MarketTrend.fromJson(Map<String, dynamic> j) => MarketTrend(
        id: (j['id'] ?? '').toString(),
        title: (j['title'] ?? j['name'] ?? j['theme'] ?? '').toString(),
        description: (j['description'] ?? j['summary'] ?? '').toString(),
        symbols: ((j['symbols'] ?? j['tickers']) as List? ?? const [])
            .map((e) => e.toString().toUpperCase())
            .where((s) => s.isNotEmpty)
            .toList(),
        strength: (j['strength'] as num?)?.toDouble() ??
            (j['confidence'] as num?)?.toDouble() ??
            (j['score'] as num?)?.toDouble(),
      );
}
