import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';

// ── Models ────────────────────────────────────────────────────────────────────

class LearningOverview {
  LearningOverview({
    required this.mode,
    required this.actsAutonomously,
    required this.enabled,
    required this.openFindings,
    required this.runsObserved,
    required this.decisionsObserved,
    required this.refusalsObserved,
  });

  final String mode;
  final bool actsAutonomously;
  final bool enabled;
  final int openFindings;
  final int runsObserved;
  final int decisionsObserved;
  final int refusalsObserved;

  factory LearningOverview.fromJson(Map<String, dynamic> j) => LearningOverview(
        mode: (j['mode'] ?? 'observe').toString(),
        actsAutonomously: j['acts_autonomously'] == true,
        enabled: j['enabled'] != false,
        openFindings: (j['open_findings'] as num?)?.toInt() ?? 0,
        runsObserved: (j['runs_observed'] as num?)?.toInt() ?? 0,
        decisionsObserved: (j['decisions_observed'] as num?)?.toInt() ?? 0,
        refusalsObserved: (j['refusals_observed'] as num?)?.toInt() ?? 0,
      );
}

class LearningFinding {
  LearningFinding({
    required this.id,
    required this.kind,
    required this.target,
    required this.severity,
    required this.title,
    required this.detail,
    required this.detectedAt,
    required this.runId,
  });

  final String id;
  final String kind;
  final String target;
  final String severity;
  final String title;
  final String detail;
  final String detectedAt;
  final String runId;

  factory LearningFinding.fromJson(Map<String, dynamic> j) => LearningFinding(
        id: (j['id'] ?? '').toString(),
        kind: (j['kind'] ?? '').toString(),
        target: (j['target'] ?? '').toString(),
        severity: (j['severity'] ?? 'low').toString(),
        title: (j['title'] ?? '').toString(),
        detail: (j['detail'] ?? '').toString(),
        detectedAt: (j['detected_at'] ?? '').toString(),
        runId: (j['run_id'] ?? '').toString(),
      );
}

class LearningFunnel {
  LearningFunnel({
    required this.runId,
    required this.target,
    required this.decided,
    required this.executed,
    required this.refused,
    required this.buyDecided,
    required this.buyExecuted,
  });

  final String runId;
  final String target;
  final int decided;
  final int executed;
  final int refused;
  final int buyDecided;
  final int buyExecuted;

  /// Null when the run decided no buys — an undefined ratio, not zero.
  double? get buyConversionPct =>
      buyDecided == 0 ? null : (buyExecuted / buyDecided) * 100.0;

  factory LearningFunnel.fromJson(Map<String, dynamic> j) => LearningFunnel(
        runId: (j['run_id'] ?? '').toString(),
        target: (j['target'] ?? '').toString(),
        decided: (j['decided'] as num?)?.toInt() ?? 0,
        executed: (j['executed'] as num?)?.toInt() ?? 0,
        refused: (j['refused'] as num?)?.toInt() ?? 0,
        buyDecided: (j['buy_decided'] as num?)?.toInt() ?? 0,
        buyExecuted: (j['buy_executed'] as num?)?.toInt() ?? 0,
      );
}

// ── Repository ────────────────────────────────────────────────────────────────

class LearningRepository {
  const LearningRepository(this._client);

  final ApiClient _client;

  Future<LearningOverview> overview() async {
    final data = await _client.get<Map<String, dynamic>>('/learning/overview');
    return LearningOverview.fromJson(data);
  }

  Future<List<LearningFinding>> findings({int limit = 100}) async {
    final data = await _client
        .get<Map<String, dynamic>>('/learning/findings?limit=$limit');
    final rows = (data['findings'] as List?) ?? const [];
    return rows
        .whereType<Map<String, dynamic>>()
        .map(LearningFinding.fromJson)
        .toList();
  }

  Future<List<LearningFunnel>> funnels({int limit = 100}) async {
    final data = await _client
        .get<Map<String, dynamic>>('/learning/funnels?limit=$limit');
    final rows = (data['funnels'] as List?) ?? const [];
    return rows
        .whereType<Map<String, dynamic>>()
        .map(LearningFunnel.fromJson)
        .toList();
  }
}

final learningRepositoryProvider = Provider<LearningRepository>(
  (ref) => LearningRepository(ref.watch(apiClientProvider)),
);
