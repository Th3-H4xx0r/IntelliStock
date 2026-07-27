import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';
import 'models/brokerage.dart';

/// Data access for /brokerages endpoints.
class BrokerageRepository {
  const BrokerageRepository(this._client);

  final ApiClient _client;

  // ── Read ────────────────────────────────────────────────────────────────────

  /// GET /brokerages → {accounts: [...]}
  Future<List<Brokerage>> list() async {
    final data = await _client.get<Map<String, dynamic>>('/brokerages');
    final raw = data['accounts'] as List<dynamic>? ?? [];
    return raw
        .whereType<Map<String, dynamic>>()
        .map(Brokerage.fromJson)
        .toList();
  }

  // ── Mutations ───────────────────────────────────────────────────────────────

  /// POST /brokerages — link a new account.
  Future<Map<String, dynamic>> link(Map<String, dynamic> body) async {
    return _client.post<Map<String, dynamic>>('/brokerages', body: body);
  }

  /// PUT /brokerages/{id} — edit an existing account.
  Future<Map<String, dynamic>> edit(
    String id,
    Map<String, dynamic> body,
  ) async {
    return _client.put<Map<String, dynamic>>('/brokerages/$id', body: body);
  }

  /// DELETE /brokerages/{id}
  Future<void> remove(String id) async {
    await _client.delete<dynamic>('/brokerages/$id');
  }

  /// POST /brokerages/test-alpaca — diagnostic probe, does NOT save.
  Future<Map<String, dynamic>> testAlpaca(Map<String, dynamic> body) async {
    return _client.post<Map<String, dynamic>>(
      '/brokerages/test-alpaca',
      body: body,
    );
  }
}

final brokerageRepositoryProvider = Provider<BrokerageRepository>(
  (ref) => BrokerageRepository(ref.watch(apiClientProvider)),
);
