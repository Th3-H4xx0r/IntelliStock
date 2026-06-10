import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';

// ── Models ────────────────────────────────────────────────────────────────────

class NexusStage {
  NexusStage({
    required this.key,
    required this.label,
    required this.status,
    this.message,
    this.durationSec,
    this.substepsCompleted,
    this.totalSubsteps,
    this.stageIndex = 0,
  });

  final String key;
  final String label;
  final String status; // pending | running | completed | skipped | stopped | failed
  final String? message;
  final double? durationSec;
  final int? substepsCompleted;
  final int? totalSubsteps;
  final int stageIndex;

  double get substepFraction {
    if (totalSubsteps == null || totalSubsteps == 0) return 0;
    return ((substepsCompleted ?? 0) / totalSubsteps!).clamp(0.0, 1.0);
  }

  factory NexusStage.fromJson(Map<String, dynamic> j) => NexusStage(
        key: (j['key'] ?? '').toString(),
        label: (j['label'] ?? '').toString(),
        status: (j['status'] ?? 'pending').toString(),
        message: j['message']?.toString(),
        durationSec: (j['duration_sec'] as num?)?.toDouble(),
        substepsCompleted: (j['substeps_completed'] as num?)?.toInt(),
        totalSubsteps: (j['total_substeps'] as num?)?.toInt(),
        stageIndex: (j['stage_index'] as num?)?.toInt() ?? 0,
      );
}

class NexusGraphBuild {
  NexusGraphBuild({
    this.status,
    this.progressPct = 0,
    this.etaFormatted,
    this.currentPhaseLabel,
    this.message,
    this.stages = const [],
    this.lastUpdated,
  });

  final String? status;
  final double progressPct;
  final String? etaFormatted;
  final String? currentPhaseLabel;
  final String? message;
  final List<NexusStage> stages;
  final DateTime? lastUpdated;

  factory NexusGraphBuild.fromJson(Map<String, dynamic> j) => NexusGraphBuild(
        status: j['status']?.toString(),
        progressPct: (j['progress_pct'] as num?)?.toDouble() ?? 0,
        etaFormatted: j['eta_formatted']?.toString(),
        currentPhaseLabel: j['current_phase_label']?.toString(),
        message: j['message']?.toString(),
        stages: (j['stages'] as List? ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(NexusStage.fromJson)
            .toList(),
        lastUpdated: j['last_updated'] == null
            ? null
            : DateTime.tryParse(j['last_updated'].toString()),
      );
}

class NexusRelCount {
  NexusRelCount({
    required this.key,
    required this.label,
    this.activeCount,
    this.totalCount,
  });

  final String key;
  final String label;
  final int? activeCount;
  final int? totalCount;

  factory NexusRelCount.fromJson(Map<String, dynamic> j) => NexusRelCount(
        key: (j['key'] ?? '').toString(),
        label: (j['label'] ?? j['key'] ?? '').toString(),
        activeCount: (j['active_count'] as num?)?.toInt(),
        totalCount: (j['total_count'] as num?)?.toInt(),
      );
}

class NexusGraphSummary {
  NexusGraphSummary({
    this.relationshipCounts = const [],
    this.nodeCounts = const {},
  });

  final List<NexusRelCount> relationshipCounts;
  final Map<String, dynamic> nodeCounts;

  factory NexusGraphSummary.fromJson(Map<String, dynamic> j) => NexusGraphSummary(
        relationshipCounts: (j['relationship_counts'] as List? ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(NexusRelCount.fromJson)
            .toList(),
        nodeCounts:
            (j['node_counts'] as Map?)?.cast<String, dynamic>() ?? const {},
      );
}

class NexusPhaseOption {
  const NexusPhaseOption({required this.value, required this.label});
  final int value;
  final String label;

  factory NexusPhaseOption.fromJson(Map<String, dynamic> j) => NexusPhaseOption(
        value: (j['value'] as num?)?.toInt() ?? 0,
        label: (j['label'] ?? '').toString(),
      );
}

class NexusControl {
  NexusControl({
    this.running = false,
    this.autoUpdateEnabled = false,
    this.autoUpdateIntervalHours = 168,
    this.autoUpdateStartPhase = 3,
    this.autoUpdateEndPhase = 14,
    this.nextAutoUpdateAt,
    this.selectedPhases = const [],
    this.phase7HistoryQuarters = 1,
    this.historicalModeEnabled = false,
    this.historicalStartDate,
    this.historicalCoverageEnd,
    this.autoUpdateStartPhaseLabel,
    this.autoUpdateEndPhaseLabel,
    this.phaseOptions = const [],
    this.deletePhaseOptions = const [],
    // Delete operation
    this.deleteOperationActive = false,
    this.deleteOperationStep,
    this.deleteOperationMessage,
    this.deleteOperationCurrent,
    this.deleteOperationTotal,
    this.deleteOperationUnit,
    this.deleteOperationError,
    this.deleteOperationSelectedPhases = const [],
    this.deleteOperationPhaseRows = const [],
    // Rebuild operation
    this.rebuildOperationActive = false,
    this.rebuildOperationDestructive = false,
    this.rebuildOperationStep,
    this.rebuildOperationMessage,
    this.rebuildOperationCurrent,
    this.rebuildOperationTotal,
    this.rebuildOperationUnit,
  });

  final bool running;
  final bool autoUpdateEnabled;
  final int autoUpdateIntervalHours;
  final int autoUpdateStartPhase;
  final int autoUpdateEndPhase;
  final DateTime? nextAutoUpdateAt;
  final List<int> selectedPhases;
  final int phase7HistoryQuarters;
  final bool historicalModeEnabled;
  final String? historicalStartDate;
  final String? historicalCoverageEnd;
  final String? autoUpdateStartPhaseLabel;
  final String? autoUpdateEndPhaseLabel;
  final List<NexusPhaseOption> phaseOptions;
  final List<NexusPhaseOption> deletePhaseOptions;

  final bool deleteOperationActive;
  final String? deleteOperationStep;
  final String? deleteOperationMessage;
  final num? deleteOperationCurrent;
  final num? deleteOperationTotal;
  final String? deleteOperationUnit;
  final String? deleteOperationError;
  final List<int> deleteOperationSelectedPhases;
  final List<Map<String, dynamic>> deleteOperationPhaseRows;

  final bool rebuildOperationActive;
  final bool rebuildOperationDestructive;
  final String? rebuildOperationStep;
  final String? rebuildOperationMessage;
  final num? rebuildOperationCurrent;
  final num? rebuildOperationTotal;
  final String? rebuildOperationUnit;

  factory NexusControl.fromJson(Map<String, dynamic> j) {
    List<NexusPhaseOption> parsePhaseOptions(dynamic raw) {
      if (raw is! List || raw.isEmpty) return const [];
      return raw
          .whereType<Map<String, dynamic>>()
          .map(NexusPhaseOption.fromJson)
          .toList();
    }

    return NexusControl(
      running: j['running'] == true,
      autoUpdateEnabled: j['auto_update_enabled'] == true,
      autoUpdateIntervalHours:
          (j['auto_update_interval_hours'] as num?)?.toInt() ?? 168,
      autoUpdateStartPhase:
          (j['auto_update_start_phase'] as num?)?.toInt() ?? 3,
      autoUpdateEndPhase:
          (j['auto_update_end_phase'] as num?)?.toInt() ?? 14,
      nextAutoUpdateAt: j['next_auto_update_at'] == null
          ? null
          : DateTime.tryParse(j['next_auto_update_at'].toString()),
      selectedPhases: (j['selected_phases'] as List? ?? const [])
          .whereType<num>()
          .map((n) => n.toInt())
          .toList(),
      phase7HistoryQuarters:
          (j['phase7_history_quarters'] as num?)?.toInt() ?? 1,
      historicalModeEnabled: j['historical_mode_enabled'] == true,
      historicalStartDate: j['historical_start_date']?.toString(),
      historicalCoverageEnd: j['historical_coverage_end']?.toString(),
      autoUpdateStartPhaseLabel:
          j['auto_update_start_phase_label']?.toString(),
      autoUpdateEndPhaseLabel: j['auto_update_end_phase_label']?.toString(),
      phaseOptions: parsePhaseOptions(j['phase_options']),
      deletePhaseOptions: parsePhaseOptions(j['delete_phase_options']),
      deleteOperationActive: j['delete_operation_active'] == true,
      deleteOperationStep: j['delete_operation_step']?.toString(),
      deleteOperationMessage: j['delete_operation_message']?.toString(),
      deleteOperationCurrent: j['delete_operation_current'] as num?,
      deleteOperationTotal: j['delete_operation_total'] as num?,
      deleteOperationUnit: j['delete_operation_unit']?.toString(),
      deleteOperationError: j['delete_operation_error']?.toString(),
      deleteOperationSelectedPhases:
          (j['delete_operation_selected_phases'] as List? ?? const [])
              .whereType<num>()
              .map((n) => n.toInt())
              .toList(),
      deleteOperationPhaseRows:
          (j['delete_operation_phase_rows'] as List? ?? const [])
              .whereType<Map<String, dynamic>>()
              .toList(),
      rebuildOperationActive: j['rebuild_operation_active'] == true,
      rebuildOperationDestructive: j['rebuild_operation_destructive'] == true,
      rebuildOperationStep: j['rebuild_operation_step']?.toString(),
      rebuildOperationMessage: j['rebuild_operation_message']?.toString(),
      rebuildOperationCurrent: j['rebuild_operation_current'] as num?,
      rebuildOperationTotal: j['rebuild_operation_total'] as num?,
      rebuildOperationUnit: j['rebuild_operation_unit']?.toString(),
    );
  }
}

class NexusBootstrap {
  NexusBootstrap({
    this.enabled = false,
    this.status = 'disabled',
    this.startDate,
    this.coverageEnd,
    this.complete = false,
    this.lastStatus,
    this.phases = const [],
    this.completedPhases,
    this.totalPhases,
    this.durationSec,
    this.completedAt,
    this.startedAt,
  });

  final bool enabled;
  final String status;
  final String? startDate;
  final String? coverageEnd;
  final bool complete;
  final String? lastStatus;
  final List<Map<String, dynamic>> phases;
  final int? completedPhases;
  final int? totalPhases;
  final double? durationSec;
  final DateTime? completedAt;
  final DateTime? startedAt;

  factory NexusBootstrap.fromJson(Map<String, dynamic> j) => NexusBootstrap(
        enabled: j['enabled'] == true,
        status: (j['status'] ?? 'disabled').toString(),
        startDate: j['start_date']?.toString(),
        coverageEnd: j['coverage_end']?.toString(),
        complete: j['complete'] == true,
        lastStatus: j['last_status']?.toString(),
        phases: (j['phases'] as List? ?? const [])
            .whereType<Map<String, dynamic>>()
            .toList(),
        completedPhases: (j['completed_phases'] as num?)?.toInt(),
        totalPhases: (j['total_phases'] as num?)?.toInt(),
        durationSec: (j['duration_sec'] as num?)?.toDouble(),
        completedAt: j['completed_at'] == null
            ? null
            : DateTime.tryParse(j['completed_at'].toString()),
        startedAt: j['started_at'] == null
            ? null
            : DateTime.tryParse(j['started_at'].toString()),
      );
}

class NexusScraper {
  NexusScraper({
    this.status,
    this.index,
    this.totalTickers,
    this.edgesCount,
    this.progressPct = 0,
    this.etaFormatted,
  });

  final String? status;
  final int? index;
  final int? totalTickers;
  final int? edgesCount;
  final double progressPct;
  final String? etaFormatted;

  factory NexusScraper.fromJson(Map<String, dynamic> j) => NexusScraper(
        status: j['status']?.toString(),
        index: (j['index'] as num?)?.toInt(),
        totalTickers: (j['total_tickers'] as num?)?.toInt(),
        edgesCount: (j['edges_count'] as num?)?.toInt(),
        progressPct: (j['progress_pct'] as num?)?.toDouble() ?? 0,
        etaFormatted: j['eta_formatted']?.toString(),
      );
}

class NexusStatus {
  NexusStatus({
    required this.control,
    this.graphBuild,
    this.graphSummary,
    this.scraper,
    this.bootstrap,
    this.graphBuilt = false,
  });

  final NexusControl control;
  final NexusGraphBuild? graphBuild;
  final NexusGraphSummary? graphSummary;
  final NexusScraper? scraper;
  final NexusBootstrap? bootstrap;
  final bool graphBuilt;

  bool get serviceRunning => control.running;
  bool get isBuilding =>
      serviceRunning &&
      (graphBuild?.status ?? '').toLowerCase() == 'running';
  bool get showBuilt => graphBuilt && !isBuilding;

  factory NexusStatus.fromJson(Map<String, dynamic> j) => NexusStatus(
        control: NexusControl.fromJson(_asMap(j['control'])),
        graphBuild: j['graph_build'] == null
            ? null
            : NexusGraphBuild.fromJson(_asMap(j['graph_build'])),
        graphSummary: j['graph_summary'] == null
            ? null
            : NexusGraphSummary.fromJson(_asMap(j['graph_summary'])),
        scraper: j['scraper'] == null
            ? null
            : NexusScraper.fromJson(_asMap(j['scraper'])),
        bootstrap: j['bootstrap'] == null
            ? null
            : NexusBootstrap.fromJson(_asMap(j['bootstrap'])),
        graphBuilt: j['graph_built'] == true,
      );
}

/// Tolerant map coercion: API maps decode as `Map<String,dynamic>`, but Dart
/// map literals (and some sources) can be `Map<dynamic,dynamic>`; coerce safely.
Map<String, dynamic> _asMap(dynamic v) =>
    v is Map ? v.cast<String, dynamic>() : <String, dynamic>{};

class NexusCacheEntry {
  NexusCacheEntry({
    required this.path,
    required this.isDir,
    this.sizeBytes,
  });

  final String path;
  final bool isDir;
  final int? sizeBytes;

  factory NexusCacheEntry.fromJson(Map<String, dynamic> j) => NexusCacheEntry(
        path: (j['path'] ?? '').toString(),
        isDir: j['is_dir'] == true,
        sizeBytes: (j['size_bytes'] as num?)?.toInt(),
      );
}

class NexusCacheInfo {
  NexusCacheInfo({
    this.available = false,
    this.cacheRoot = '/app/.cache',
    this.entries = const [],
    this.error,
  });

  final bool available;
  final String cacheRoot;
  final List<NexusCacheEntry> entries;
  final String? error;

  factory NexusCacheInfo.fromJson(Map<String, dynamic> j) => NexusCacheInfo(
        available: j['available'] == true,
        cacheRoot: (j['cache_root'] ?? '/app/.cache').toString(),
        entries: (j['entries'] as List? ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(NexusCacheEntry.fromJson)
            .toList(),
        error: j['error']?.toString(),
      );
}

// ── Fallback phase options ────────────────────────────────────────────────────

const List<NexusPhaseOption> kFallbackPhaseOptions = [
  NexusPhaseOption(value: 1, label: 'Phase 1: Company universe'),
  NexusPhaseOption(value: 2, label: 'Phase 2: Easy relationships'),
  NexusPhaseOption(value: 3, label: 'Phase 2b: SEC sector/industry'),
  NexusPhaseOption(value: 4, label: 'Phase 3: Supply chain'),
  NexusPhaseOption(value: 5, label: 'Phase 4: Competitive'),
  NexusPhaseOption(value: 6, label: 'Phase 5: Macro/BEA'),
  NexusPhaseOption(value: 7, label: 'Phase 6: GLEIF hierarchy'),
  NexusPhaseOption(value: 8, label: 'Phase 6B: SEC EX-21 hierarchy'),
  NexusPhaseOption(value: 9, label: 'Phase 7: 13F ownership'),
  NexusPhaseOption(value: 10, label: 'Phase 8: USASpending'),
  NexusPhaseOption(value: 11, label: 'Phase 9: Wikidata'),
  NexusPhaseOption(value: 12, label: 'Phase 10: PatentsView'),
  NexusPhaseOption(value: 13, label: 'Phase 11: 8-K agreements'),
  NexusPhaseOption(value: 14, label: 'Phase 12: ETF universe'),
];

const Set<int> _kDeletePhaseValues = {
  3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14,
};

List<NexusPhaseOption> get kFallbackDeletePhaseOptions =>
    kFallbackPhaseOptions
        .where((p) => _kDeletePhaseValues.contains(p.value))
        .toList();

// ── Repository ────────────────────────────────────────────────────────────────

class NexusRepository {
  const NexusRepository(this._client);

  final ApiClient _client;

  Future<NexusStatus> status() async {
    final data = await _client.get<Map<String, dynamic>>('/nexus/status');
    return NexusStatus.fromJson(data);
  }

  Future<void> control(Map<String, dynamic> body) async {
    await _client.post<dynamic>('/nexus/control', body: body);
  }

  Future<Map<String, dynamic>> rebuild(Map<String, dynamic> body) async {
    return await _client.post<Map<String, dynamic>>('/nexus/rebuild', body: body);
  }

  Future<void> deleteEdges(Map<String, dynamic> body) async {
    await _client.post<dynamic>('/nexus/delete-edges', body: body);
  }

  Future<NexusCacheInfo> cache() async {
    final data = await _client.get<Map<String, dynamic>>('/nexus/cache');
    return NexusCacheInfo.fromJson(data);
  }
}

final nexusRepositoryProvider = Provider<NexusRepository>(
  (ref) => NexusRepository(ref.watch(apiClientProvider)),
);
