import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';

// ── Models ──────────────────────────────────────────────────────────────────

class KalshiPortfolio {
  KalshiPortfolio({
    required this.value,
    required this.cash,
    required this.dayChange,
    required this.series,
  });

  final double value;
  final double cash;
  final double dayChange;
  final List<double> series; // value points, chronological

  factory KalshiPortfolio.fromJson(Map<String, dynamic> j) => KalshiPortfolio(
        value: (j['value'] as num?)?.toDouble() ?? 0,
        cash: (j['cash'] as num?)?.toDouble() ?? 0,
        dayChange: (j['day_change'] as num?)?.toDouble() ?? 0,
        series: ((j['series'] as List?) ?? [])
            .map((p) => ((p as Map)['value'] as num?)?.toDouble() ?? 0)
            .toList(),
      );
}

class KalshiEdge {
  KalshiEdge({required this.marketTicker, required this.side, required this.edge});
  final String marketTicker;
  final String side;
  final double edge;

  factory KalshiEdge.fromJson(Map<String, dynamic> j) => KalshiEdge(
        marketTicker: (j['market_ticker'] ?? '').toString(),
        side: (j['side'] ?? '').toString(),
        edge: (j['edge'] as num?)?.toDouble() ?? 0,
      );
}

class KalshiPosition {
  KalshiPosition({
    required this.marketTicker,
    required this.side,
    required this.contracts,
    this.unrealizedCents,
  });
  final String marketTicker;
  final String side;
  final int contracts;
  final double? unrealizedCents;

  factory KalshiPosition.fromJson(Map<String, dynamic> j) => KalshiPosition(
        marketTicker: (j['market_ticker'] ?? '').toString(),
        side: (j['side'] ?? '').toString(),
        contracts: (j['contracts'] as num?)?.toInt() ?? 0,
        unrealizedCents: (j['unrealized_cents'] as num?)?.toDouble(),
      );
}

class KalshiInstance {
  KalshiInstance({required this.id, required this.name, required this.running, required this.liveEnabled});
  final String id;
  final String name;
  final bool running;
  final bool liveEnabled;

  factory KalshiInstance.fromJson(Map<String, dynamic> j) => KalshiInstance(
        id: (j['id'] ?? '').toString(),
        name: (j['name'] ?? 'Kalshi instance').toString(),
        running: j['running'] == true,
        liveEnabled: j['live_enabled'] == true,
      );
}

// ── Repository ──────────────────────────────────────────────────────────────

class KalshiRepository {
  KalshiRepository(this._client);
  final ApiClient _client;

  Future<KalshiPortfolio> portfolio(String bid) async {
    final d = await _client.get<Map<String, dynamic>>('/brokerages/$bid/kalshi/portfolio');
    return KalshiPortfolio.fromJson(d);
  }

  Future<List<KalshiEdge>> edges(String bid) async {
    final d = await _client.get<Map<String, dynamic>>('/brokerages/$bid/kalshi/edges', query: {'limit': 10});
    return ((d['edges'] as List?) ?? []).map((e) => KalshiEdge.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<List<KalshiPosition>> positions(String bid) async {
    final d = await _client.get<Map<String, dynamic>>('/brokerages/$bid/kalshi/positions');
    return ((d['positions'] as List?) ?? []).map((p) => KalshiPosition.fromJson(p as Map<String, dynamic>)).toList();
  }

  Future<Map<String, dynamic>> kill(String bid) =>
      _client.post<Map<String, dynamic>>('/brokerages/$bid/kalshi/kill');

  Future<List<KalshiInstance>> instances(String bid) async {
    final d = await _client.get<Map<String, dynamic>>('/brokerages/$bid/kalshi/instances');
    return ((d['instances'] as List?) ?? []).map((e) => KalshiInstance.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<void> createInstance(String bid, Map<String, dynamic> body) =>
      _client.post('/brokerages/$bid/kalshi/instances', body: body);

  Future<void> startInstance(String id) => _client.post('/instances/$id/start');
  Future<void> stopInstance(String id) => _client.post('/instances/$id/stop');

  Future<Map<String, dynamic>> instanceDetail(String id) =>
      _client.get<Map<String, dynamic>>('/instances/$id/kalshi/detail');

  Future<Map<String, dynamic>> instanceDecisions(String id) =>
      _client.get<Map<String, dynamic>>('/instances/$id/kalshi/decisions', query: {'limit': 200});
}

final kalshiRepositoryProvider = Provider<KalshiRepository>(
  (ref) => KalshiRepository(ref.watch(apiClientProvider)),
);

// Family providers keyed by brokerageId. autoDispose (NOT keepAlive) so a
// stale-empty first fetch can't get stuck for the app lifetime — pull-to-refresh
// invalidates them. (Known repo gotcha with keep-alive telemetry providers.)
final kalshiPortfolioProvider =
    FutureProvider.autoDispose.family<KalshiPortfolio, String>((ref, bid) {
  return ref.watch(kalshiRepositoryProvider).portfolio(bid);
});

final kalshiEdgesProvider =
    FutureProvider.autoDispose.family<List<KalshiEdge>, String>((ref, bid) {
  return ref.watch(kalshiRepositoryProvider).edges(bid);
});

final kalshiPositionsProvider =
    FutureProvider.autoDispose.family<List<KalshiPosition>, String>((ref, bid) {
  return ref.watch(kalshiRepositoryProvider).positions(bid);
});

final kalshiInstancesProvider =
    FutureProvider.autoDispose.family<List<KalshiInstance>, String>((ref, bid) {
  return ref.watch(kalshiRepositoryProvider).instances(bid);
});

final kalshiInstanceDetailProvider =
    FutureProvider.autoDispose.family<Map<String, dynamic>, String>((ref, id) {
  return ref.watch(kalshiRepositoryProvider).instanceDetail(id);
});

final kalshiInstanceDecisionsProvider =
    FutureProvider.autoDispose.family<Map<String, dynamic>, String>((ref, id) {
  return ref.watch(kalshiRepositoryProvider).instanceDecisions(id);
});
