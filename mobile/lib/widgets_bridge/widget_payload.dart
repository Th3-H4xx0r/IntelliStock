/// Data contract between the Flutter app and the native iOS/Android widget.
///
/// [WidgetSyncService] serialises this to JSON and writes it to the App Group
/// shared container. The SwiftUI/Glance widget reads the same keys.
library;

// ── Sub-models ────────────────────────────────────────────────────────────────

class IntradayPoint {
  const IntradayPoint({required this.t, required this.v});

  /// Unix epoch seconds.
  final int t;

  /// Portfolio value at this timestamp.
  final double v;

  Map<String, dynamic> toJson() => {'t': t, 'v': v};

  factory IntradayPoint.fromJson(Map<String, dynamic> j) => IntradayPoint(
        t: (j['t'] as num?)?.toInt() ?? 0,
        v: (j['v'] as num?)?.toDouble() ?? 0,
      );
}

class WidgetPortfolio {
  const WidgetPortfolio({
    required this.accountValue,
    required this.dayPnlAbs,
    required this.dayPnlPct,
    required this.intradayPoints,
    required this.asOf,
  });

  final double accountValue;
  final double dayPnlAbs;
  final double dayPnlPct;
  final List<IntradayPoint> intradayPoints;

  /// ISO-8601 timestamp string.
  final String asOf;

  Map<String, dynamic> toJson() => {
        'accountValue': accountValue,
        'dayPnlAbs': dayPnlAbs,
        'dayPnlPct': dayPnlPct,
        'intradayPoints': intradayPoints.map((p) => p.toJson()).toList(),
        'asOf': asOf,
      };

  factory WidgetPortfolio.fromJson(Map<String, dynamic> j) => WidgetPortfolio(
        accountValue: (j['accountValue'] as num?)?.toDouble() ?? 0,
        dayPnlAbs: (j['dayPnlAbs'] as num?)?.toDouble() ?? 0,
        dayPnlPct: (j['dayPnlPct'] as num?)?.toDouble() ?? 0,
        intradayPoints: (j['intradayPoints'] as List<dynamic>?)
                ?.whereType<Map<String, dynamic>>()
                .map(IntradayPoint.fromJson)
                .toList() ??
            [],
        asOf: j['asOf'] as String? ?? '',
      );
}

/// One selectable portfolio (brokerage account) for the configurable widget.
class WidgetAccount {
  const WidgetAccount({
    required this.id,
    required this.label,
    required this.accountValue,
    required this.dayPnlAbs,
    required this.dayPnlPct,
    this.intradayPoints = const [],
    this.positions = const [],
  });

  final String id;
  final String label;
  final double accountValue;
  final double dayPnlAbs;
  final double dayPnlPct;
  final List<IntradayPoint> intradayPoints;
  final List<WidgetPosition> positions;

  Map<String, dynamic> toJson() => {
        'id': id,
        'label': label,
        'accountValue': accountValue,
        'dayPnlAbs': dayPnlAbs,
        'dayPnlPct': dayPnlPct,
        'intradayPoints': intradayPoints.map((p) => p.toJson()).toList(),
        'positions': positions.map((p) => p.toJson()).toList(),
      };

  /// The primary-portfolio shape (for the non-configurable fallback widget).
  Map<String, dynamic> toPortfolioJson() => {
        'accountValue': accountValue,
        'dayPnlAbs': dayPnlAbs,
        'dayPnlPct': dayPnlPct,
        'intradayPoints': intradayPoints.map((p) => p.toJson()).toList(),
        'asOf': '',
      };
}

class WidgetPosition {
  const WidgetPosition({
    required this.symbol,
    required this.qty,
    required this.marketValue,
    required this.unrealizedPnlAbs,
    required this.unrealizedPnlPct,
  });

  final String symbol;
  final double qty;
  final double marketValue;
  final double unrealizedPnlAbs;
  final double unrealizedPnlPct;

  Map<String, dynamic> toJson() => {
        'symbol': symbol,
        'qty': qty,
        'marketValue': marketValue,
        'unrealizedPnlAbs': unrealizedPnlAbs,
        'unrealizedPnlPct': unrealizedPnlPct,
      };

  factory WidgetPosition.fromJson(Map<String, dynamic> j) => WidgetPosition(
        symbol: j['symbol'] as String? ?? '',
        qty: (j['qty'] as num?)?.toDouble() ?? 0,
        marketValue: (j['marketValue'] as num?)?.toDouble() ?? 0,
        unrealizedPnlAbs: (j['unrealizedPnlAbs'] as num?)?.toDouble() ?? 0,
        unrealizedPnlPct: (j['unrealizedPnlPct'] as num?)?.toDouble() ?? 0,
      );
}

class WidgetInstance {
  const WidgetInstance({
    required this.id,
    required this.name,
    required this.running,
    required this.pnlAbs,
    required this.pnlPct,
  });

  final String id;
  final String name;
  final bool running;
  final double pnlAbs;
  final double pnlPct;

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'running': running,
        'pnlAbs': pnlAbs,
        'pnlPct': pnlPct,
      };

  factory WidgetInstance.fromJson(Map<String, dynamic> j) => WidgetInstance(
        id: j['id'] as String? ?? '',
        name: j['name'] as String? ?? '',
        running: j['running'] as bool? ?? false,
        pnlAbs: (j['pnlAbs'] as num?)?.toDouble() ?? 0,
        pnlPct: (j['pnlPct'] as num?)?.toDouble() ?? 0,
      );
}

// ── Root payload ──────────────────────────────────────────────────────────────

/// Complete snapshot written to the App Group on every sync.
class WidgetPayload {
  const WidgetPayload({
    required this.portfolio,
    required this.positions,
    required this.instances,
  });

  final WidgetPortfolio portfolio;
  final List<WidgetPosition> positions;
  final List<WidgetInstance> instances;

  Map<String, dynamic> toJson() => {
        'portfolio': portfolio.toJson(),
        'positions': positions.map((p) => p.toJson()).toList(),
        'instances': instances.map((i) => i.toJson()).toList(),
      };

  factory WidgetPayload.fromJson(Map<String, dynamic> j) => WidgetPayload(
        portfolio: WidgetPortfolio.fromJson(
            (j['portfolio'] as Map<String, dynamic>?) ?? {}),
        positions: (j['positions'] as List<dynamic>?)
                ?.whereType<Map<String, dynamic>>()
                .map(WidgetPosition.fromJson)
                .toList() ??
            [],
        instances: (j['instances'] as List<dynamic>?)
                ?.whereType<Map<String, dynamic>>()
                .map(WidgetInstance.fromJson)
                .toList() ??
            [],
      );
}
