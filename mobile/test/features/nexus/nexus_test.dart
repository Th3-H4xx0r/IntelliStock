import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/nexus/data/nexus_repository.dart';

// ── NexusStage model ──────────────────────────────────────────────────────────

void main() {
  group('NexusStage.fromJson', () {
    test('parses all standard fields', () {
      final stage = NexusStage.fromJson({
        'key': 'phase_1',
        'label': 'Company universe',
        'status': 'completed',
        'message': 'Done',
        'duration_sec': 12.5,
        'substeps_completed': 8,
        'total_substeps': 10,
        'stage_index': 0,
      });
      expect(stage.key, 'phase_1');
      expect(stage.label, 'Company universe');
      expect(stage.status, 'completed');
      expect(stage.message, 'Done');
      expect(stage.durationSec, closeTo(12.5, 0.001));
      expect(stage.substepsCompleted, 8);
      expect(stage.totalSubsteps, 10);
      expect(stage.stageIndex, 0);
    });

    test('defaults status to pending when missing', () {
      final stage = NexusStage.fromJson({'key': 'p1', 'label': 'Phase 1'});
      expect(stage.status, 'pending');
    });

    test('tolerates fully empty map', () {
      final stage = NexusStage.fromJson({});
      expect(stage.key, '');
      expect(stage.label, '');
      expect(stage.status, 'pending');
    });
  });

  // ── NexusStage.substepFraction ─────────────────────────────────────────────

  group('NexusStage.substepFraction', () {
    test('returns 0 when totalSubsteps is null', () {
      final stage = NexusStage.fromJson({'key': 'k', 'label': 'l'});
      expect(stage.substepFraction, 0.0);
    });

    test('returns 0 when totalSubsteps is 0', () {
      final stage = NexusStage.fromJson({
        'key': 'k',
        'label': 'l',
        'substeps_completed': 5,
        'total_substeps': 0,
      });
      expect(stage.substepFraction, 0.0);
    });

    test('returns correct fraction mid-progress', () {
      final stage = NexusStage.fromJson({
        'key': 'k',
        'label': 'l',
        'substeps_completed': 3,
        'total_substeps': 4,
      });
      expect(stage.substepFraction, closeTo(0.75, 0.001));
    });

    test('clamps to 1.0 when substeps_completed > total_substeps', () {
      final stage = NexusStage.fromJson({
        'key': 'k',
        'label': 'l',
        'substeps_completed': 12,
        'total_substeps': 10,
      });
      expect(stage.substepFraction, 1.0);
    });

    test('returns 0 when substeps_completed is null', () {
      final stage = NexusStage.fromJson({
        'key': 'k',
        'label': 'l',
        'total_substeps': 10,
      });
      expect(stage.substepFraction, 0.0);
    });
  });

  // ── NexusControl.fromJson ──────────────────────────────────────────────────

  group('NexusControl.fromJson', () {
    test('parses running and auto-update fields', () {
      final ctrl = NexusControl.fromJson({
        'running': true,
        'auto_update_enabled': true,
        'auto_update_interval_hours': 24,
        'auto_update_start_phase': 3,
        'auto_update_end_phase': 10,
      });
      expect(ctrl.running, isTrue);
      expect(ctrl.autoUpdateEnabled, isTrue);
      expect(ctrl.autoUpdateIntervalHours, 24);
      expect(ctrl.autoUpdateStartPhase, 3);
      expect(ctrl.autoUpdateEndPhase, 10);
    });

    test('defaults autoUpdateIntervalHours to 168 when absent', () {
      final ctrl = NexusControl.fromJson({});
      expect(ctrl.autoUpdateIntervalHours, 168);
      expect(ctrl.autoUpdateStartPhase, 3);
      expect(ctrl.autoUpdateEndPhase, 14);
    });

    test('parses selectedPhases list', () {
      final ctrl = NexusControl.fromJson({
        'selected_phases': [1, 3, 7, 14],
      });
      expect(ctrl.selectedPhases, [1, 3, 7, 14]);
    });

    test('parses delete operation fields', () {
      final ctrl = NexusControl.fromJson({
        'delete_operation_active': true,
        'delete_operation_step': 'Deleting phase 3',
        'delete_operation_current': 150,
        'delete_operation_total': 500,
        'delete_operation_unit': 'edges',
        'delete_operation_selected_phases': [3, 4],
        'delete_operation_phase_rows': [
          {
            'phase': 3,
            'label': 'Supply chain',
            'current': 150,
            'total': 300,
            'deleted': 50,
          }
        ],
      });
      expect(ctrl.deleteOperationActive, isTrue);
      expect(ctrl.deleteOperationStep, 'Deleting phase 3');
      expect(ctrl.deleteOperationCurrent, 150);
      expect(ctrl.deleteOperationTotal, 500);
      expect(ctrl.deleteOperationUnit, 'edges');
      expect(ctrl.deleteOperationSelectedPhases, [3, 4]);
      expect(ctrl.deleteOperationPhaseRows.length, 1);
      expect(ctrl.deleteOperationPhaseRows[0]['phase'], 3);
    });

    test('parses rebuild operation fields', () {
      final ctrl = NexusControl.fromJson({
        'rebuild_operation_active': true,
        'rebuild_operation_destructive': true,
        'rebuild_operation_step': 'Wiping graph',
        'rebuild_operation_message': 'Preparing...',
      });
      expect(ctrl.rebuildOperationActive, isTrue);
      expect(ctrl.rebuildOperationDestructive, isTrue);
      expect(ctrl.rebuildOperationStep, 'Wiping graph');
    });

    test('parses phase options list', () {
      final ctrl = NexusControl.fromJson({
        'phase_options': [
          {'value': 1, 'label': 'Phase 1'},
          {'value': 3, 'label': 'Phase 2b'},
        ],
      });
      expect(ctrl.phaseOptions.length, 2);
      expect(ctrl.phaseOptions[0].value, 1);
      expect(ctrl.phaseOptions[1].label, 'Phase 2b');
    });

    test('empty phase_options returns empty list', () {
      final ctrl = NexusControl.fromJson({'phase_options': []});
      expect(ctrl.phaseOptions, isEmpty);
    });

    test('parses historical mode fields', () {
      final ctrl = NexusControl.fromJson({
        'historical_mode_enabled': true,
        'historical_start_date': '2020-01-01',
        'historical_coverage_end': '2024-12-31',
        'phase7_history_quarters': 4,
      });
      expect(ctrl.historicalModeEnabled, isTrue);
      expect(ctrl.historicalStartDate, '2020-01-01');
      expect(ctrl.historicalCoverageEnd, '2024-12-31');
      expect(ctrl.phase7HistoryQuarters, 4);
    });

    test('nextAutoUpdateAt parses ISO date string', () {
      final ctrl = NexusControl.fromJson({
        'next_auto_update_at': '2026-06-17T00:00:00Z',
      });
      expect(ctrl.nextAutoUpdateAt, isNotNull);
      expect(ctrl.nextAutoUpdateAt!.year, 2026);
    });

    test('nextAutoUpdateAt is null when absent', () {
      final ctrl = NexusControl.fromJson({});
      expect(ctrl.nextAutoUpdateAt, isNull);
    });
  });

  // ── NexusStatus.fromJson ───────────────────────────────────────────────────

  group('NexusStatus.fromJson', () {
    test('isBuilding = true when service running + graph_build status=running',
        () {
      final status = NexusStatus.fromJson({
        'control': {'running': true},
        'graph_build': {'status': 'running', 'progress_pct': 45},
        'graph_built': false,
      });
      expect(status.isBuilding, isTrue);
      expect(status.showBuilt, isFalse);
    });

    test('showBuilt = true when graph_built + not building', () {
      final status = NexusStatus.fromJson({
        'control': {'running': false},
        'graph_build': {'status': 'completed'},
        'graph_built': true,
      });
      expect(status.isBuilding, isFalse);
      expect(status.showBuilt, isTrue);
    });

    test('isBuilding = false when service not running', () {
      final status = NexusStatus.fromJson({
        'control': {'running': false},
        'graph_build': {'status': 'running'},
      });
      expect(status.isBuilding, isFalse);
    });

    test('parses graph_summary relationship counts', () {
      final status = NexusStatus.fromJson({
        'control': {},
        'graph_summary': {
          'relationship_counts': [
            {'key': 'supply_chain', 'label': 'Supply Chain', 'active_count': 500, 'total_count': 600},
          ],
          'node_counts': {'company': 8000},
        },
      });
      expect(status.graphSummary?.relationshipCounts.length, 1);
      expect(status.graphSummary?.relationshipCounts[0].key, 'supply_chain');
      expect(status.graphSummary?.relationshipCounts[0].activeCount, 500);
      expect(status.graphSummary?.nodeCounts['company'], 8000);
    });

    test('tolerates missing optional sections', () {
      final status = NexusStatus.fromJson({'control': {}});
      expect(status.graphBuild, isNull);
      expect(status.graphSummary, isNull);
      expect(status.bootstrap, isNull);
      expect(status.scraper, isNull);
      expect(status.graphBuilt, isFalse);
    });
  });

  // ── NexusGraphBuild.fromJson ───────────────────────────────────────────────

  group('NexusGraphBuild.fromJson', () {
    test('parses progress and stages', () {
      final build = NexusGraphBuild.fromJson({
        'status': 'running',
        'progress_pct': 62.5,
        'eta_formatted': '~3m',
        'current_phase_label': 'Supply chain',
        'stages': [
          {'key': 'p1', 'label': 'Phase 1', 'status': 'completed'},
          {'key': 'p2', 'label': 'Phase 2', 'status': 'running'},
        ],
      });
      expect(build.status, 'running');
      expect(build.progressPct, closeTo(62.5, 0.001));
      expect(build.etaFormatted, '~3m');
      expect(build.stages.length, 2);
      expect(build.stages[0].status, 'completed');
      expect(build.stages[1].status, 'running');
    });

    test('empty stages tolerates missing list', () {
      final build = NexusGraphBuild.fromJson({'status': 'idle'});
      expect(build.stages, isEmpty);
      expect(build.progressPct, 0.0);
    });

    test('parses last_updated timestamp', () {
      final build = NexusGraphBuild.fromJson({
        'last_updated': '2026-06-10T12:00:00Z',
      });
      expect(build.lastUpdated, isNotNull);
      expect(build.lastUpdated!.year, 2026);
    });
  });

  // ── NexusBootstrap.fromJson ────────────────────────────────────────────────

  group('NexusBootstrap.fromJson', () {
    test('parses enabled bootstrap with dates', () {
      final b = NexusBootstrap.fromJson({
        'enabled': true,
        'status': 'completed',
        'start_date': '2015-01-01',
        'coverage_end': '2025-12-31',
        'complete': true,
        'completed_phases': 14,
        'total_phases': 14,
        'duration_sec': 7200.0,
      });
      expect(b.enabled, isTrue);
      expect(b.status, 'completed');
      expect(b.startDate, '2015-01-01');
      expect(b.complete, isTrue);
      expect(b.completedPhases, 14);
      expect(b.durationSec, closeTo(7200.0, 0.01));
    });

    test('defaults status to disabled when missing', () {
      final b = NexusBootstrap.fromJson({});
      expect(b.status, 'disabled');
      expect(b.enabled, isFalse);
    });
  });

  // ── kFallbackPhaseOptions ──────────────────────────────────────────────────

  group('kFallbackPhaseOptions', () {
    test('has exactly 14 entries', () {
      expect(kFallbackPhaseOptions.length, 14);
    });

    test('entries are numbered 1 through 14', () {
      for (var i = 0; i < 14; i++) {
        expect(kFallbackPhaseOptions[i].value, i + 1);
      }
    });

    test('first entry is Phase 1: Company universe', () {
      expect(kFallbackPhaseOptions[0].value, 1);
      expect(kFallbackPhaseOptions[0].label, contains('Company universe'));
    });

    test('last entry is Phase 12: ETF universe', () {
      expect(kFallbackPhaseOptions[13].value, 14);
      expect(kFallbackPhaseOptions[13].label, contains('ETF universe'));
    });
  });

  // ── kFallbackDeletePhaseOptions ────────────────────────────────────────────

  group('kFallbackDeletePhaseOptions', () {
    test('has 12 entries (phases 3-14)', () {
      expect(kFallbackDeletePhaseOptions.length, 12);
    });

    test('does not include phases 1 or 2', () {
      final values = kFallbackDeletePhaseOptions.map((p) => p.value).toSet();
      expect(values.contains(1), isFalse);
      expect(values.contains(2), isFalse);
    });

    test('includes phase 3 (value=3)', () {
      final values = kFallbackDeletePhaseOptions.map((p) => p.value).toSet();
      expect(values.contains(3), isTrue);
      expect(values.contains(14), isTrue);
    });
  });

  // ── NexusCacheInfo.fromJson ────────────────────────────────────────────────

  group('NexusCacheInfo.fromJson', () {
    test('parses available cache with entries', () {
      final info = NexusCacheInfo.fromJson({
        'available': true,
        'cache_root': '/app/.cache',
        'entries': [
          {'path': 'companies.pkl', 'is_dir': false, 'size_bytes': 204800},
          {'path': 'ownership/', 'is_dir': true},
        ],
      });
      expect(info.available, isTrue);
      expect(info.cacheRoot, '/app/.cache');
      expect(info.entries.length, 2);
      expect(info.entries[0].path, 'companies.pkl');
      expect(info.entries[0].isDir, isFalse);
      expect(info.entries[0].sizeBytes, 204800);
      expect(info.entries[1].isDir, isTrue);
    });

    test('defaults cacheRoot when absent', () {
      final info = NexusCacheInfo.fromJson({'available': false});
      expect(info.cacheRoot, '/app/.cache');
      expect(info.entries, isEmpty);
    });

    test('parses error field', () {
      final info = NexusCacheInfo.fromJson({
        'available': false,
        'error': 'Permission denied',
      });
      expect(info.error, 'Permission denied');
    });
  });

  // ── NexusRelCount.fromJson ─────────────────────────────────────────────────

  group('NexusRelCount.fromJson', () {
    test('label falls back to key when absent', () {
      final rc = NexusRelCount.fromJson({'key': 'supply_chain'});
      expect(rc.label, 'supply_chain');
    });

    test('uses explicit label over key', () {
      final rc = NexusRelCount.fromJson({
        'key': 'supply_chain',
        'label': 'Supply Chain',
        'active_count': 400,
        'total_count': 500,
      });
      expect(rc.label, 'Supply Chain');
      expect(rc.activeCount, 400);
      expect(rc.totalCount, 500);
    });
  });

  // ── Phase-selection payload building ──────────────────────────────────────
  // Mirrors the payload construction logic in the NexusScreen start modal.

  group('Phase-selection payload', () {
    Map<String, dynamic> buildStartPayload({
      required List<int> selectedPhases,
      int phase7Quarters = 1,
      bool historicalMode = false,
      String? historicalStartDate,
      bool forceBootstrap = false,
    }) {
      return {
        'action': 'start',
        'selected_phases': selectedPhases,
        'phase7_history_quarters': phase7Quarters,
        'historical_mode_enabled': historicalMode,
        if (historicalMode && historicalStartDate != null)
          'historical_start_date': historicalStartDate,
        'force_bootstrap': forceBootstrap,
      };
    }

    test('all phases selected produces 14-element list', () {
      final payload = buildStartPayload(
        selectedPhases: List.generate(14, (i) => i + 1),
      );
      expect((payload['selected_phases'] as List).length, 14);
    });

    test('historical_start_date omitted when historicalMode=false', () {
      final payload = buildStartPayload(
        selectedPhases: [1, 2, 3],
        historicalMode: false,
        historicalStartDate: '2020-01-01',
      );
      expect(payload.containsKey('historical_start_date'), isFalse);
    });

    test('historical_start_date included when historicalMode=true', () {
      final payload = buildStartPayload(
        selectedPhases: [1, 9],
        historicalMode: true,
        historicalStartDate: '2019-01-01',
      );
      expect(payload['historical_start_date'], '2019-01-01');
      expect(payload['historical_mode_enabled'], isTrue);
    });

    test('force_bootstrap is included in payload', () {
      final payload = buildStartPayload(
        selectedPhases: [1],
        forceBootstrap: true,
      );
      expect(payload['force_bootstrap'], isTrue);
    });

    test('empty selectedPhases produces empty list in payload', () {
      final payload = buildStartPayload(selectedPhases: []);
      expect(payload['selected_phases'], isEmpty);
    });
  });

  // ── Auto-update summary label helper ──────────────────────────────────────
  // Mirrors _autoUpdateSummary logic from NexusScreen.

  group('Auto-update summary label', () {
    String autoUpdateSummary(NexusControl ctrl) {
      if (!ctrl.autoUpdateEnabled) return 'Disabled';
      final hours = ctrl.autoUpdateIntervalHours;
      final interval = hours < 24
          ? '${hours}h'
          : hours % 24 == 0
              ? '${hours ~/ 24}d'
              : '${hours}h';
      final from = ctrl.autoUpdateStartPhaseLabel ??
          'Phase ${ctrl.autoUpdateStartPhase}';
      final to =
          ctrl.autoUpdateEndPhaseLabel ?? 'Phase ${ctrl.autoUpdateEndPhase}';
      return 'Every $interval · $from → $to';
    }

    test('disabled returns Disabled', () {
      final ctrl = NexusControl.fromJson({'auto_update_enabled': false});
      expect(autoUpdateSummary(ctrl), 'Disabled');
    });

    test('24h interval formats as 1d', () {
      final ctrl = NexusControl.fromJson({
        'auto_update_enabled': true,
        'auto_update_interval_hours': 24,
        'auto_update_start_phase': 3,
        'auto_update_end_phase': 14,
        'auto_update_start_phase_label': 'Phase 2b: SEC',
        'auto_update_end_phase_label': 'Phase 12: ETF',
      });
      expect(autoUpdateSummary(ctrl), 'Every 1d · Phase 2b: SEC → Phase 12: ETF');
    });

    test('48h interval formats as 2d', () {
      final ctrl = NexusControl.fromJson({
        'auto_update_enabled': true,
        'auto_update_interval_hours': 48,
        'auto_update_start_phase': 1,
        'auto_update_end_phase': 5,
      });
      expect(autoUpdateSummary(ctrl), 'Every 2d · Phase 1 → Phase 5');
    });

    test('168h (1 week) formats as 7d', () {
      final ctrl = NexusControl.fromJson({
        'auto_update_enabled': true,
        'auto_update_interval_hours': 168,
        'auto_update_start_phase': 3,
        'auto_update_end_phase': 14,
      });
      expect(autoUpdateSummary(ctrl), 'Every 7d · Phase 3 → Phase 14');
    });

    test('12h formats as 12h (not days)', () {
      final ctrl = NexusControl.fromJson({
        'auto_update_enabled': true,
        'auto_update_interval_hours': 12,
        'auto_update_start_phase': 3,
        'auto_update_end_phase': 14,
      });
      expect(autoUpdateSummary(ctrl), 'Every 12h · Phase 3 → Phase 14');
    });
  });

  // ── _fmtDuration helper ────────────────────────────────────────────────────
  // Mirrors the duration-formatting used in the build stage stepper.

  group('_fmtDuration', () {
    String fmtDuration(double sec) {
      if (sec < 60) return '${sec.toStringAsFixed(1)}s';
      final m = (sec / 60).floor();
      final s = (sec % 60).round();
      return s > 0 ? '${m}m ${s}s' : '${m}m';
    }

    test('< 60s formats as seconds with 1 decimal', () {
      expect(fmtDuration(3.5), '3.5s');
      expect(fmtDuration(59.9), '59.9s');
    });

    test('60s exactly = 1m', () {
      expect(fmtDuration(60), '1m');
    });

    test('90s = 1m 30s', () {
      expect(fmtDuration(90), '1m 30s');
    });

    test('3600s = 60m', () {
      expect(fmtDuration(3600), '60m');
    });

    test('3661s = 61m 1s', () {
      expect(fmtDuration(3661), '61m 1s');
    });
  });
}
