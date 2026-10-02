import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';
import 'models/instance.dart';

/// Data layer for all instance-related API calls.
class InstanceRepository {
  const InstanceRepository(this._client);

  final ApiClient _client;

  // ── Instances ───────────────────────────────────────────────────────────────

  Future<List<Instance>> listInstances() async {
    final data = await _client.get<Map<String, dynamic>>('/instances');
    final list = data['instances'] as List? ?? const [];
    return list
        .whereType<Map<String, dynamic>>()
        // Kalshi + crypto bots each have their own dedicated screen/workflow, so
        // they're excluded from the general equity instances list.
        .where((j) => j['kind'] != 'kalshi' && j['kind'] != 'crypto')
        .map(Instance.fromJson)
        .toList();
  }

  Future<Instance> getInstance(String id) async {
    final data = await _client.get<Map<String, dynamic>>('/instances/$id');
    return Instance.fromJson(data);
  }

  Future<Instance> createInstance({
    required String id,
    String? name,
    String? granularity,
    bool runCommand = false,
    String? brokerageId,
    double? maxUsage,
    String? strategyId,
  }) async {
    final body = <String, dynamic>{
      'id': id,
      if (name != null && name.isNotEmpty) 'name': name,
      'granularity': granularity ?? '60',
      'run_command': runCommand,
      if (brokerageId != null && brokerageId.isNotEmpty)
        'brokerage_id': brokerageId,
      if (maxUsage != null) 'max_usage': maxUsage,
      if (strategyId != null && strategyId.isNotEmpty)
        'strategy_id': strategyId,
    };
    final data = await _client.post<Map<String, dynamic>>('/instances', body: body);
    return Instance.fromJson(data['instance'] as Map<String, dynamic>? ?? data);
  }

  Future<Instance> patchInstance(String id, Map<String, dynamic> patch) async {
    final data =
        await _client.patch<Map<String, dynamic>>('/instances/$id', body: patch);
    return Instance.fromJson(data['instance'] as Map<String, dynamic>? ?? data);
  }

  Future<void> deleteInstance(String id, {bool force = false}) async {
    await _client.delete<dynamic>(
      '/instances/$id',
      query: force ? {'force': 'true'} : null,
    );
  }

  Future<void> startInstance(String id) async {
    await _client.post<dynamic>('/instances/$id/start');
  }

  Future<void> stopInstance(String id) async {
    await _client.post<dynamic>('/instances/$id/stop');
  }

  Future<void> clearState(String id, String scope,
      {bool apply = false, String? confirm}) async {
    await _client.post<dynamic>(
      '/instances/$id/clear-state',
      body: {
        'scope': scope,
        'apply': apply,
        if (confirm != null) 'confirm': confirm,
      },
    );
  }

  Future<Map<String, dynamic>> previewClearState(
      String id, String scope) async {
    final data = await _client.post<Map<String, dynamic>>(
      '/instances/$id/clear-state',
      body: {'scope': scope, 'apply': false},
    );
    return data;
  }

  Future<Map<String, dynamic>> applyClearState(
      String id, String scope) async {
    final data = await _client.post<Map<String, dynamic>>(
      '/instances/$id/clear-state',
      body: {'scope': scope, 'apply': true, 'confirm': id},
    );
    return data;
  }

  // ── Stock management ────────────────────────────────────────────────────────

  Future<void> addStock(String id, String symbol) async {
    await _client.post<dynamic>(
      '/instances/$id/stocks',
      body: {'symbol': symbol},
    );
  }

  Future<void> removeStock(String id, String symbol) async {
    await _client.delete<dynamic>('/instances/$id/stocks/$symbol');
  }

  // ── Link / unlink brokerage & strategy ─────────────────────────────────────

  Future<void> linkBrokerage(String id, String brokerageId) async {
    await _client.post<dynamic>(
      '/instances/$id/link-brokerage',
      body: {'brokerage_id': brokerageId},
    );
  }

  Future<void> unlinkBrokerage(String id) async {
    await _client.patch<dynamic>(
      '/instances/$id',
      body: {'brokerage_id': ''},
    );
  }

  Future<void> linkDataBrokerage(String id, String? brokerageId) async {
    await _client.post<dynamic>(
      '/instances/$id/link-data-brokerage',
      body: {'brokerage_id': brokerageId},
    );
  }

  Future<void> linkStrategy(String id, String strategyId) async {
    await _client.post<dynamic>(
      '/instances/$id/link-strategy',
      body: {'strategy_id': int.tryParse(strategyId) ?? strategyId},
    );
  }

  Future<void> unlinkStrategy(String id) async {
    await _client.post<dynamic>('/instances/$id/unlink-strategy');
  }

  // ── Backtests ───────────────────────────────────────────────────────────────

  Future<Map<String, dynamic>> listBacktests(
    String instanceId, {
    int page = 1,
    int perPage = 15,
    String sortBy = 'completed_at',
    String sortOrder = 'desc',
  }) async {
    final data = await _client.get<Map<String, dynamic>>(
      '/instances/$instanceId/backtests',
      query: {
        'page': page.toString(),
        'per_page': perPage.toString(),
        'sort_by': sortBy,
        'sort_order': sortOrder,
      },
    );
    return data;
  }

  Future<void> createBacktest({
    required String instanceId,
    required List<String> stocks,
    required String startDate,
    required String endDate,
    String granularity = '60',
    double initialCash = 100000,
  }) async {
    await _client.post<dynamic>(
      '/backtests',
      body: {
        'instance_id': instanceId,
        'stocks': stocks,
        'start_date': startDate,
        'end_date': endDate,
        'granularity': granularity,
        'initial_cash': initialCash,
      },
    );
  }

  Future<Map<String, dynamic>> getBacktestStatus(String backtestId) async {
    return await _client.get<Map<String, dynamic>>('/backtests/$backtestId/status');
  }

  // ── Brokerages (for selectors) ──────────────────────────────────────────────

  Future<List<Map<String, dynamic>>> listBrokerages() async {
    final data = await _client.get<Map<String, dynamic>>('/brokerages');
    final list = data['accounts'] as List? ?? const [];
    return list.whereType<Map<String, dynamic>>().toList();
  }

  // ── Strategies (for selectors) ──────────────────────────────────────────────

  Future<List<Map<String, dynamic>>> listStrategies() async {
    final data = await _client.get<Map<String, dynamic>>('/strategies');
    final list = data['strategies'] as List? ?? const [];
    return list.whereType<Map<String, dynamic>>().toList();
  }
}

final instanceRepositoryProvider = Provider<InstanceRepository>(
  (ref) => InstanceRepository(ref.watch(apiClientProvider)),
);
