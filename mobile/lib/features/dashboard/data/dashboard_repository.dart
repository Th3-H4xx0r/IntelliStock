import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';
import '../../../core/models/portfolio_history.dart';

// ── Plain data models ─────────────────────────────────────────────────────────

/// A single engine entry from GET /status.
class EngineStatus {
  const EngineStatus({
    required this.id,
    required this.status,
    this.details,
  });

  final String id;
  final String status;
  final String? details;

  factory EngineStatus.fromJson(Map<String, dynamic> json) => EngineStatus(
        id: (json['id'] as String? ?? ''),
        status: (json['status'] as String? ?? 'stopped'),
        details: json['details'] as String?,
      );
}

/// Full services snapshot (result of the parallel 4-endpoint fetch).
class ServicesSnapshot {
  const ServicesSnapshot({
    required this.engines,
    this.agentControl,
    this.digestControl,
    this.nexusStatus,
  });

  final List<EngineStatus> engines;
  final Map<String, dynamic>? agentControl;
  final Map<String, dynamic>? digestControl;
  final Map<String, dynamic>? nexusStatus;

  EngineStatus? engineById(String id) {
    try {
      return engines.firstWhere((e) => e.id == id);
    } catch (_) {
      return null;
    }
  }

  String statusFor(String id) =>
      engineById(id)?.status.toLowerCase() ?? 'stopped';

  bool isRunning(String id) => statusFor(id) == 'running';
  bool isPaused(String id) => statusFor(id) == 'paused';
}

/// A single brokerage account from GET /brokerages.
class BrokerageAccount {
  const BrokerageAccount({
    required this.id,
    required this.accountName,
    required this.brokerageType,
    required this.status,
    this.alpacaPaper = false,
  });

  final String id;
  final String accountName;
  final String brokerageType;
  final String status;
  final bool alpacaPaper;

  factory BrokerageAccount.fromJson(Map<String, dynamic> json) =>
      BrokerageAccount(
        id: (json['id'] as String? ?? ''),
        accountName: (json['account_name'] as String? ?? ''),
        brokerageType: (json['brokerage_type'] as String? ?? ''),
        status: (json['status'] as String? ?? ''),
        alpacaPaper: json['alpaca_paper'] == true,
      );

  bool get isActive => status.toLowerCase() == 'active';
}

// ── Repository ────────────────────────────────────────────────────────────────

/// Thin data layer for all dashboard endpoints.
class DashboardRepository {
  const DashboardRepository(this._client);

  final ApiClient _client;

  /// Fetches all four service-status endpoints in parallel.
  Future<ServicesSnapshot> services() async {
    final results = await Future.wait([
      _client
          .get<Map<String, dynamic>>('/status')
          .catchError((_) => <String, dynamic>{}),
      _client
          .get<Map<String, dynamic>>('/agent/control')
          .catchError((_) => <String, dynamic>{}),
      _client
          .get<Map<String, dynamic>>('/digest/control')
          .catchError((_) => <String, dynamic>{}),
      _client
          .get<Map<String, dynamic>>('/nexus/status')
          .catchError((_) => <String, dynamic>{}),
    ]);

    final statusData = results[0];
    final agentData = results[1];
    final digestData = results[2];
    final nexusData = results[3];

    final rawEngines = (statusData['engines'] as List? ?? const []);
    final engines = rawEngines
        .whereType<Map<String, dynamic>>()
        .map(EngineStatus.fromJson)
        .toList();

    return ServicesSnapshot(
      engines: engines,
      agentControl: agentData.isEmpty ? null : agentData,
      digestControl: digestData.isEmpty ? null : digestData,
      nexusStatus: nexusData.isEmpty ? null : nexusData,
    );
  }

  /// GET /brokerages → {accounts: [...]}
  Future<List<BrokerageAccount>> brokerages() async {
    final data = await _client.get<Map<String, dynamic>>('/brokerages');
    final raw = (data['accounts'] as List? ?? const []);
    return raw
        .whereType<Map<String, dynamic>>()
        .map(BrokerageAccount.fromJson)
        .toList();
  }

  /// GET /brokerages/{id}/portfolio-history?range=
  Future<PortfolioHistory> portfolioHistory(String id, String range) async {
    final data = await _client.get<Map<String, dynamic>>(
      '/brokerages/$id/portfolio-history',
      query: {'range': range},
    );
    return PortfolioHistory.fromJson(data);
  }

  // ── Control POSTs ──────────────────────────────────────────────────────────

  Future<void> startPriceService() =>
      _client.post<dynamic>('/config/run-price-service');

  Future<void> terminatePrice() =>
      _client.post<dynamic>('/config/terminate-price');

  Future<void> controlDiscover({required bool running}) =>
      _client.post<dynamic>('/discover/control', body: {'running': running});

  Future<void> controlAgent({
    bool? running,
    bool? paused,
    String? specialRequest,
  }) {
    final body = <String, dynamic>{};
    if (running != null) body['running'] = running;
    if (paused != null) body['paused'] = paused;
    if (specialRequest != null) body['special_request'] = specialRequest;
    return _client.post<dynamic>('/agent/control', body: body);
  }

  Future<void> controlDigest({required bool running}) =>
      _client.post<dynamic>('/digest/control', body: {'running': running});

  Future<void> digestSendNow() =>
      _client.post<dynamic>('/digest/send-now');

  Future<void> controlNexus({required bool running}) =>
      _client.post<dynamic>('/nexus/control', body: {'running': running});
}

final dashboardRepositoryProvider = Provider<DashboardRepository>(
  (ref) => DashboardRepository(ref.watch(apiClientProvider)),
);
