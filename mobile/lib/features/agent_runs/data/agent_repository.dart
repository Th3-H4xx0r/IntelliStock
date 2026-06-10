import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';

// ── Models ────────────────────────────────────────────────────────────────────

class AgentStage {
  AgentStage({
    required this.label,
    required this.status,
    this.stocks = const [],
    this.pnl,
    this.pnlPct,
    this.details,
  });

  final String label;
  final String status;
  final List<String> stocks;
  final num? pnl;
  final num? pnlPct;
  final String? details;

  factory AgentStage.fromJson(Map<String, dynamic> j) => AgentStage(
        label: (j['label'] ?? '').toString(),
        status: (j['status'] ?? 'pending').toString(),
        stocks: (j['stocks'] as List? ?? const []).cast<String>(),
        pnl: j['pnl'] as num?,
        pnlPct: j['pnl_pct'] as num?,
        details: j['details']?.toString(),
      );
}

class AgentRun {
  AgentRun({
    required this.id,
    required this.status,
    this.cycleId,
    this.name,
    this.createdAt,
    this.stages = const [],
    this.finalResult,
  });

  final String id;
  final String status;
  final String? cycleId;
  final String? name;
  final DateTime? createdAt;
  final List<AgentStage> stages;
  final String? finalResult;

  factory AgentRun.fromJson(Map<String, dynamic> j) => AgentRun(
        id: (j['id'] ?? '').toString(),
        status: (j['status'] ?? 'stopped').toString(),
        cycleId: j['cycle_id']?.toString(),
        name: j['name']?.toString(),
        createdAt: j['created_at'] == null
            ? null
            : DateTime.tryParse(j['created_at'].toString()),
        stages: (j['stages'] as List? ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(AgentStage.fromJson)
            .toList(),
        finalResult: j['final_result']?.toString(),
      );
}

class AgentRunsPage {
  AgentRunsPage({
    required this.runs,
    required this.total,
    required this.totalPages,
    required this.page,
  });

  final List<AgentRun> runs;
  final int total;
  final int totalPages;
  final int page;

  factory AgentRunsPage.fromJson(Map<String, dynamic> j) => AgentRunsPage(
        runs: (j['runs'] as List? ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(AgentRun.fromJson)
            .toList(),
        total: (j['total'] as num?)?.toInt() ?? 0,
        totalPages: (j['total_pages'] as num?)?.toInt() ?? 1,
        page: (j['page'] as num?)?.toInt() ?? 1,
      );
}

class AgentControl {
  const AgentControl({
    this.running = false,
    this.paused = false,
  });

  final bool running;
  final bool paused;

  bool get isRunning => running && !paused;
  bool get isPaused => running && paused;
  bool get isStopped => !running;

  factory AgentControl.fromJson(Map<String, dynamic> j) => AgentControl(
        running: j['running'] == true,
        paused: j['paused'] == true,
      );
}

// ── Repository ────────────────────────────────────────────────────────────────

class AgentRepository {
  const AgentRepository(this._client);

  final ApiClient _client;

  Future<AgentRunsPage> runs({int page = 1, int perPage = 20}) async {
    final data = await _client.get<Map<String, dynamic>>(
      '/agent/runs',
      query: {'page': page.toString(), 'per_page': perPage.toString()},
    );
    return AgentRunsPage.fromJson(data);
  }

  Future<AgentControl> control() async {
    final data = await _client.get<Map<String, dynamic>>('/agent/control');
    return AgentControl.fromJson(data);
  }

  Future<void> setControl({
    bool? running,
    bool? paused,
    String? specialRequest,
  }) async {
    final body = <String, dynamic>{
      if (running != null) 'running': running,
      if (paused != null) 'paused': paused,
      if (specialRequest != null && specialRequest.isNotEmpty)
        'special_request': specialRequest,
    };
    await _client.post<dynamic>('/agent/control', body: body);
  }

  Future<void> forceStop(String logId) async {
    await _client.post<dynamic>('/agent/runs/$logId/force-stop');
  }
}

final agentRepositoryProvider = Provider<AgentRepository>(
  (ref) => AgentRepository(ref.watch(apiClientProvider)),
);
