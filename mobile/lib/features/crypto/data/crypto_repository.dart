import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/network/api_client.dart';
import '../../instances/data/models/instance.dart';

/// Data layer for crypto (kind='crypto') instances. Crypto instances run through
/// the SAME `/instances` API as equity instances (kind-gated on the backend), so
/// create/edit carry a `crypto_config` blob (band + per-coin allocations) plus a
/// fixed symbol universe; start/stop/delete reuse the shared instance endpoints.
class CryptoRepository {
  const CryptoRepository(this._client);

  final ApiClient _client;

  /// GET /instances → only the crypto (`kind == 'crypto'`) instances.
  Future<List<Instance>> listInstances() async {
    final data = await _client.get<Map<String, dynamic>>('/instances');
    final list = data['instances'] as List? ?? const [];
    return list
        .whereType<Map<String, dynamic>>()
        .where((j) => j['kind'] == 'crypto')
        .map(Instance.fromJson)
        .toList();
  }

  /// POST /instances — create a crypto instance. [body] is the fully-formed
  /// payload (id, name, granularity, run_command, kind, brokerage_id,
  /// strategy_id, stocks, crypto_config) built by the sheet.
  Future<Instance> createInstance(Map<String, dynamic> body) async {
    final data = await _client.post<Map<String, dynamic>>('/instances', body: body);
    return Instance.fromJson(data['instance'] as Map<String, dynamic>? ?? data);
  }

  /// PATCH /instances/:id — edit an existing crypto instance's allocation
  /// (crypto_config + stocks).
  Future<Instance> updateInstance(String id, Map<String, dynamic> body) async {
    final data =
        await _client.patch<Map<String, dynamic>>('/instances/$id', body: body);
    return Instance.fromJson(data['instance'] as Map<String, dynamic>? ?? data);
  }

  // ── Lifecycle (shared instance endpoints) ───────────────────────────────────

  Future<void> startInstance(String id) =>
      _client.post<dynamic>('/instances/$id/start');

  Future<void> stopInstance(String id) =>
      _client.post<dynamic>('/instances/$id/stop');

  Future<void> deleteInstance(String id, {bool force = false}) =>
      _client.delete<dynamic>(
        '/instances/$id',
        query: force ? {'force': 'true'} : null,
      );

  // ── Selectors used by the create/edit sheet ─────────────────────────────────

  /// GET /brokerages → raw account maps (id / account_name / brokerage_type).
  Future<List<Map<String, dynamic>>> brokerages() async {
    final data = await _client.get<Map<String, dynamic>>('/brokerages');
    final list = data['accounts'] as List? ?? const [];
    return list.whereType<Map<String, dynamic>>().toList();
  }

  /// GET /strategies → existing strategy docs (used to resolve the chosen
  /// dynamic-strategy name → its integer strategy_id).
  Future<List<Map<String, dynamic>>> strategies() async {
    final data = await _client.get<Map<String, dynamic>>('/strategies');
    final list = data['strategies'] as List? ?? const [];
    return list.whereType<Map<String, dynamic>>().toList();
  }

  /// Account equity for a brokerage: uninvested cash + Σ position market value.
  /// Drives the %↔$ conversion in the allocation editor. Returns 0 when the
  /// balance is unavailable.
  Future<double> accountEquity(String brokerageId) async {
    try {
      final data = await _client
          .get<Map<String, dynamic>>('/brokerages/$brokerageId/positions');
      final cash = (data['cash'] as num?)?.toDouble() ?? 0;
      final positions = (data['positions'] as List? ?? const [])
          .whereType<Map<String, dynamic>>();
      final invested = positions.fold<double>(
          0, (sum, p) => sum + ((p['marketValue'] as num?)?.toDouble() ?? 0));
      return cash + invested;
    } catch (_) {
      return 0;
    }
  }
}

final cryptoRepositoryProvider = Provider<CryptoRepository>(
  (ref) => CryptoRepository(ref.watch(apiClientProvider)),
);

/// All crypto instances. autoDispose + keepAlive-on-non-empty mirrors the Kalshi
/// providers so the list survives a transient dispose+refetch when the app
/// briefly backgrounds, without re-introducing an empty-cache trap.
final cryptoInstancesProvider =
    FutureProvider.autoDispose<List<Instance>>((ref) async {
  final v = await ref.watch(cryptoRepositoryProvider).listInstances();
  if (v.isNotEmpty) ref.keepAlive();
  return v;
});
