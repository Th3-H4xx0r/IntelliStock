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
    required this.engineRunning,
  });

  final String mode;
  final bool actsAutonomously;
  final bool enabled;
  final int openFindings;
  final int runsObserved;
  final int decisionsObserved;
  final int refusalsObserved;
  final bool engineRunning;

  factory LearningOverview.fromJson(Map<String, dynamic> j) => LearningOverview(
        mode: (j['mode'] ?? 'observe').toString(),
        actsAutonomously: j['acts_autonomously'] == true,
        enabled: j['enabled'] != false,
        openFindings: (j['open_findings'] as num?)?.toInt() ?? 0,
        runsObserved: (j['runs_observed'] as num?)?.toInt() ?? 0,
        decisionsObserved: (j['decisions_observed'] as num?)?.toInt() ?? 0,
        refusalsObserved: (j['refusals_observed'] as num?)?.toInt() ?? 0,
        engineRunning: j['engine_running'] == true,
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
    required this.status,
    required this.evidence,
  });

  final String id;
  final String kind;
  final String target;
  final String severity;
  final String title;
  final String detail;
  final String detectedAt;
  final String runId;
  final String status;
  final Map<String, dynamic> evidence;

  factory LearningFinding.fromJson(Map<String, dynamic> j) => LearningFinding(
        id: (j['id'] ?? '').toString(),
        kind: (j['kind'] ?? '').toString(),
        target: (j['target'] ?? '').toString(),
        severity: (j['severity'] ?? 'low').toString(),
        title: (j['title'] ?? '').toString(),
        detail: (j['detail'] ?? '').toString(),
        detectedAt: (j['detected_at'] ?? '').toString(),
        runId: (j['run_id'] ?? '').toString(),
        status: (j['status'] ?? 'open').toString(),
        evidence: (j['evidence'] as Map?)?.cast<String, dynamic>() ?? const {},
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

class LearningApproval {
  LearningApproval({
    required this.id,
    required this.rung,
    required this.actionClass,
    required this.target,
    required this.summary,
    required this.documentId,
    required this.requestedAt,
    required this.holdsForever,
  });

  final String id;
  final String rung;
  final String actionClass;
  final String target;
  final String summary;
  final String documentId;
  final String requestedAt;

  /// Live rungs wait indefinitely — silence is never consent for real money.
  final bool holdsForever;

  factory LearningApproval.fromJson(Map<String, dynamic> j) => LearningApproval(
        id: (j['id'] ?? '').toString(),
        rung: (j['rung'] ?? '').toString(),
        actionClass: (j['action_class'] ?? '').toString(),
        target: (j['target'] ?? '').toString(),
        summary: (j['summary'] ?? '').toString(),
        documentId: (j['document_id'] ?? '').toString(),
        requestedAt: (j['requested_at'] ?? '').toString(),
        holdsForever: j['holds_forever'] == true,
      );
}

class LearningFloor {
  LearningFloor({
    required this.target,
    required this.windowClass,
    required this.floorPp,
    required this.n,
    required this.measured,
    required this.reason,
  });

  final String target;
  final String windowClass;
  final double floorPp;
  final int n;
  final bool measured;
  final String reason;

  factory LearningFloor.fromJson(Map<String, dynamic> j) => LearningFloor(
        target: (j['target'] ?? '').toString(),
        windowClass: (j['window_class'] ?? '').toString(),
        floorPp: (j['floor_pp'] as num?)?.toDouble() ?? 0.0,
        n: (j['n'] as num?)?.toInt() ?? 0,
        measured: j['measured'] == true,
        reason: (j['reason'] ?? '').toString(),
      );
}

/// A strategy document the subsystem could be allowed to write to.
class LearningStrategyTarget {
  LearningStrategyTarget({
    required this.id,
    required this.name,
    required this.subStrategies,
    required this.instanceNames,
    required this.money,
  });

  final String id;
  final String name;
  final int subStrategies;
  final List<String> instanceNames;

  /// "live" | "paper" | "unknown" | "none". Three states rather than a boolean:
  /// inferring live from "has a brokerage" flagged every document as REAL
  /// MONEY, which buried the one that actually was.
  final String money;

  bool get isLive => money == 'live';

  factory LearningStrategyTarget.fromJson(Map<String, dynamic> j) =>
      LearningStrategyTarget(
        id: (j['id'] ?? '').toString(),
        name: (j['name'] ?? '').toString(),
        subStrategies: (j['sub_strategies'] as num?)?.toInt() ?? 0,
        instanceNames: ((j['instance_names'] as List?) ?? const [])
            .map((e) => e.toString())
            .toList(),
        money: (j['money'] ?? 'unknown').toString(),
      );
}

/// An instance whose runs the engine can be pointed at.
class LearningInstanceTarget {
  LearningInstanceTarget({
    required this.id,
    required this.name,
    required this.kind,
    required this.strategyId,
    required this.running,
    required this.money,
  });

  final String id;
  final String name;
  final String kind;
  final String? strategyId;
  final bool running;
  final String money;

  bool get isLive => money == 'live';

  factory LearningInstanceTarget.fromJson(Map<String, dynamic> j) =>
      LearningInstanceTarget(
        id: (j['id'] ?? '').toString(),
        name: (j['name'] ?? '').toString(),
        kind: (j['kind'] ?? '').toString(),
        strategyId: j['strategy_id']?.toString(),
        running: j['running'] == true,
        money: (j['money'] ?? 'unknown').toString(),
      );
}

class LearningTargets {
  LearningTargets({
    required this.strategies,
    required this.instances,
    required this.documentAllowlist,
    required this.watchedInstances,
    required this.watchingAll,
  });

  final List<LearningStrategyTarget> strategies;
  final List<LearningInstanceTarget> instances;
  final List<String> documentAllowlist;
  final List<String> watchedInstances;

  /// An empty watch list means EVERY instance — the opposite of the allowlist,
  /// where empty means write nowhere.
  final bool watchingAll;

  factory LearningTargets.fromJson(Map<String, dynamic> j) => LearningTargets(
        strategies: ((j['strategies'] as List?) ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(LearningStrategyTarget.fromJson)
            .toList(),
        instances: ((j['instances'] as List?) ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(LearningInstanceTarget.fromJson)
            .toList(),
        documentAllowlist: ((j['document_allowlist'] as List?) ?? const [])
            .map((e) => e.toString())
            .toList(),
        watchedInstances: ((j['watched_instances'] as List?) ?? const [])
            .map((e) => e.toString())
            .toList(),
        watchingAll: j['watching_all'] == true,
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
    final data = await _client.get<Map<String, dynamic>>(
        '/learning/findings', query: {'limit': limit.toString()});
    final rows = (data['findings'] as List?) ?? const [];
    return rows
        .whereType<Map<String, dynamic>>()
        .map(LearningFinding.fromJson)
        .toList();
  }

  Future<List<LearningApproval>> approvals({int limit = 100}) async {
    final data = await _client.get<Map<String, dynamic>>(
        '/learning/approvals', query: {'limit': limit.toString()});
    final rows = (data['pending'] as List?) ?? const [];
    return rows
        .whereType<Map<String, dynamic>>()
        .map(LearningApproval.fromJson)
        .toList();
  }

  Future<List<LearningFloor>> noiseFloors() async {
    final data =
        await _client.get<Map<String, dynamic>>('/learning/noise-floors');
    final rows = (data['floors'] as List?) ?? const [];
    return rows
        .whereType<Map<String, dynamic>>()
        .map(LearningFloor.fromJson)
        .toList();
  }

  Future<LearningTargets> targets() async {
    final data = await _client.get<Map<String, dynamic>>('/learning/targets');
    return LearningTargets.fromJson(data);
  }

  Future<void> setDocumentAllowlist(List<String> ids) async {
    await _client.post<dynamic>('/learning/control',
        body: {'config': {'document_allowlist': ids}});
  }

  Future<void> setWatchedInstances(List<String> ids) async {
    await _client.post<dynamic>('/learning/control',
        body: {'config': {'watched_instances': ids}});
  }

  Future<Map<String, dynamic>> control() async {
    return await _client.get<Map<String, dynamic>>('/learning/control');
  }

  Future<void> setRunning(bool running) async {
    await _client.post<dynamic>('/learning/control', body: {'running': running});
  }

  Future<void> setMode(String mode) async {
    await _client.post<dynamic>('/learning/control',
        body: {'config': {'mode': mode}});
  }

  Future<void> decide(String approvalId, String decision) async {
    await _client.post<dynamic>('/learning/approvals/$approvalId',
        body: {'decision': decision});
  }

  Future<List<LearningFunnel>> funnels({int limit = 100}) async {
    final data = await _client.get<Map<String, dynamic>>(
        '/learning/funnels', query: {'limit': limit.toString()});
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
