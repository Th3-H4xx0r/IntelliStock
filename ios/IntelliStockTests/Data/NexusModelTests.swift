import Foundation
import Testing
@testable import IntelliStock

/// The model groups of test/features/nexus/nexus_test.dart. (The
/// phase-selection payload, auto-update summary and `_fmtDuration` groups
/// mirror NexusScreen helpers; the nexus feature ports them.)
struct NexusStageTests {
    @Test func parsesAllStandardFields() {
        let stage = NexusStage(json: [
            "key": "phase_1",
            "label": "Company universe",
            "status": "completed",
            "message": "Done",
            "duration_sec": 12.5,
            "substeps_completed": 8,
            "total_substeps": 10,
            "stage_index": 0,
        ])
        #expect(stage.key == "phase_1")
        #expect(stage.label == "Company universe")
        #expect(stage.status == "completed")
        #expect(stage.message == "Done")
        #expect(close(stage.durationSec, 12.5, 0.001))
        #expect(stage.substepsCompleted == 8)
        #expect(stage.totalSubsteps == 10)
        #expect(stage.stageIndex == 0)
    }

    @Test func defaultsStatusToPendingWhenMissing() {
        #expect(NexusStage(json: ["key": "p1", "label": "Phase 1"]).status == "pending")
    }

    @Test func toleratesFullyEmptyMap() {
        let stage = NexusStage(json: [:])
        #expect(stage.key == "")
        #expect(stage.label == "")
        #expect(stage.status == "pending")
    }

    @Test func substepFraction() {
        #expect(NexusStage(json: ["key": "k", "label": "l"]).substepFraction == 0.0)
        #expect(NexusStage(json: ["substeps_completed": 5, "total_substeps": 0]).substepFraction == 0.0)
        #expect(close(NexusStage(json: ["substeps_completed": 3, "total_substeps": 4]).substepFraction, 0.75, 0.001))
        #expect(NexusStage(json: ["substeps_completed": 12, "total_substeps": 10]).substepFraction == 1.0)
        #expect(NexusStage(json: ["total_substeps": 10]).substepFraction == 0.0)
    }
}

struct NexusControlTests {
    @Test func parsesRunningAndAutoUpdateFields() {
        let ctrl = NexusControl(json: [
            "running": true,
            "auto_update_enabled": true,
            "auto_update_interval_hours": 24,
            "auto_update_start_phase": 3,
            "auto_update_end_phase": 10,
        ])
        #expect(ctrl.running)
        #expect(ctrl.autoUpdateEnabled)
        #expect(ctrl.autoUpdateIntervalHours == 24)
        #expect(ctrl.autoUpdateStartPhase == 3)
        #expect(ctrl.autoUpdateEndPhase == 10)
    }

    @Test func defaultsWhenAbsent() {
        let ctrl = NexusControl(json: [:])
        #expect(ctrl.autoUpdateIntervalHours == 168)
        #expect(ctrl.autoUpdateStartPhase == 3)
        #expect(ctrl.autoUpdateEndPhase == 14)
    }

    @Test func parsesSelectedPhasesList() {
        #expect(NexusControl(json: ["selected_phases": [1, 3, 7, 14]]).selectedPhases == [1, 3, 7, 14])
        #expect(NexusControl(json: ["selected_phases": [1, "2", 3.0]]).selectedPhases == [1, 3])
    }

    @Test func parsesDeleteOperationFields() {
        let ctrl = NexusControl(json: [
            "delete_operation_active": true,
            "delete_operation_step": "Deleting phase 3",
            "delete_operation_current": 150,
            "delete_operation_total": 500,
            "delete_operation_unit": "edges",
            "delete_operation_selected_phases": [3, 4],
            "delete_operation_phase_rows": [
                ["phase": 3, "label": "Supply chain", "current": 150, "total": 300, "deleted": 50],
            ],
        ])
        #expect(ctrl.deleteOperationActive)
        #expect(ctrl.deleteOperationStep == "Deleting phase 3")
        #expect(ctrl.deleteOperationCurrent == 150)
        #expect(ctrl.deleteOperationTotal == 500)
        #expect(ctrl.deleteOperationUnit == "edges")
        #expect(ctrl.deleteOperationSelectedPhases == [3, 4])
        #expect(ctrl.deleteOperationPhaseRows.count == 1)
        #expect(ctrl.deleteOperationPhaseRows[0]["phase"] == 3)
    }

    @Test func parsesRebuildOperationFields() {
        let ctrl = NexusControl(json: [
            "rebuild_operation_active": true,
            "rebuild_operation_destructive": true,
            "rebuild_operation_step": "Wiping graph",
            "rebuild_operation_message": "Preparing...",
        ])
        #expect(ctrl.rebuildOperationActive)
        #expect(ctrl.rebuildOperationDestructive)
        #expect(ctrl.rebuildOperationStep == "Wiping graph")
    }

    @Test func parsesPhaseOptionsList() {
        let ctrl = NexusControl(json: [
            "phase_options": [["value": 1, "label": "Phase 1"], ["value": 3, "label": "Phase 2b"]],
        ])
        #expect(ctrl.phaseOptions.count == 2)
        #expect(ctrl.phaseOptions[0].value == 1)
        #expect(ctrl.phaseOptions[1].label == "Phase 2b")
        #expect(NexusControl(json: ["phase_options": []]).phaseOptions.isEmpty)
    }

    @Test func parsesHistoricalModeFields() {
        let ctrl = NexusControl(json: [
            "historical_mode_enabled": true,
            "historical_start_date": "2020-01-01",
            "historical_coverage_end": "2024-12-31",
            "phase7_history_quarters": 4,
        ])
        #expect(ctrl.historicalModeEnabled)
        #expect(ctrl.historicalStartDate == "2020-01-01")
        #expect(ctrl.historicalCoverageEnd == "2024-12-31")
        #expect(ctrl.phase7HistoryQuarters == 4)
    }

    @Test func nextAutoUpdateAtParsesIsoAndIsNilWhenAbsent() throws {
        let ctrl = NexusControl(json: ["next_auto_update_at": "2026-06-17T00:00:00Z"])
        let date = try #require(ctrl.nextAutoUpdateAt)
        #expect(etWallClockCalendar.component(.year, from: date) == 2026)
        #expect(NexusControl(json: [:]).nextAutoUpdateAt == nil)
    }
}

struct NexusStatusTests {
    @Test func isBuildingWhenServiceRunningAndGraphBuildRunning() {
        let status = NexusStatus(json: [
            "control": ["running": true],
            "graph_build": ["status": "running", "progress_pct": 45],
            "graph_built": false,
        ])
        #expect(status.isBuilding)
        #expect(!status.showBuilt)
    }

    @Test func showBuiltWhenGraphBuiltAndNotBuilding() {
        let status = NexusStatus(json: [
            "control": ["running": false],
            "graph_build": ["status": "completed"],
            "graph_built": true,
        ])
        #expect(!status.isBuilding)
        #expect(status.showBuilt)
    }

    @Test func isBuildingFalseWhenServiceNotRunning() {
        let status = NexusStatus(json: ["control": ["running": false], "graph_build": ["status": "running"]])
        #expect(!status.isBuilding)
    }

    @Test func parsesGraphSummaryRelationshipCounts() {
        let status = NexusStatus(json: [
            "control": [:],
            "graph_summary": [
                "relationship_counts": [
                    ["key": "supply_chain", "label": "Supply Chain", "active_count": 500, "total_count": 600],
                ],
                "node_counts": ["company": 8000],
            ],
        ])
        #expect(status.graphSummary?.relationshipCounts.count == 1)
        #expect(status.graphSummary?.relationshipCounts[0].key == "supply_chain")
        #expect(status.graphSummary?.relationshipCounts[0].activeCount == 500)
        #expect(status.graphSummary?.nodeCounts["company"] == 8000)
    }

    @Test func toleratesMissingOptionalSections() {
        let status = NexusStatus(json: ["control": [:]])
        #expect(status.graphBuild == nil)
        #expect(status.graphSummary == nil)
        #expect(status.bootstrap == nil)
        #expect(status.scraper == nil)
        #expect(!status.graphBuilt)
    }
}

struct NexusGraphBuildTests {
    @Test func parsesProgressAndStages() {
        let build = NexusGraphBuild(json: [
            "status": "running",
            "progress_pct": 62.5,
            "eta_formatted": "~3m",
            "current_phase_label": "Supply chain",
            "stages": [
                ["key": "p1", "label": "Phase 1", "status": "completed"],
                ["key": "p2", "label": "Phase 2", "status": "running"],
            ],
        ])
        #expect(build.status == "running")
        #expect(close(build.progressPct, 62.5, 0.001))
        #expect(build.etaFormatted == "~3m")
        #expect(build.stages.count == 2)
        #expect(build.stages[0].status == "completed")
        #expect(build.stages[1].status == "running")
    }

    @Test func emptyStagesToleratesMissingList() {
        let build = NexusGraphBuild(json: ["status": "idle"])
        #expect(build.stages.isEmpty)
        #expect(build.progressPct == 0.0)
    }

    @Test func parsesLastUpdatedTimestamp() throws {
        let build = NexusGraphBuild(json: ["last_updated": "2026-06-10T12:00:00Z"])
        let date = try #require(build.lastUpdated)
        #expect(etWallClockCalendar.component(.year, from: date) == 2026)
    }
}

struct NexusBootstrapTests {
    @Test func parsesEnabledBootstrapWithDates() {
        let b = NexusBootstrap(json: [
            "enabled": true,
            "status": "completed",
            "start_date": "2015-01-01",
            "coverage_end": "2025-12-31",
            "complete": true,
            "completed_phases": 14,
            "total_phases": 14,
            "duration_sec": 7200.0,
        ])
        #expect(b.enabled)
        #expect(b.status == "completed")
        #expect(b.startDate == "2015-01-01")
        #expect(b.complete)
        #expect(b.completedPhases == 14)
        #expect(close(b.durationSec, 7200.0, 0.01))
    }

    @Test func defaultsStatusToDisabledWhenMissing() {
        let b = NexusBootstrap(json: [:])
        #expect(b.status == "disabled")
        #expect(!b.enabled)
    }
}

struct NexusFallbackPhaseOptionsTests {
    @Test func fallbackPhaseOptions() {
        #expect(kFallbackPhaseOptions.count == 14)
        for i in 0..<14 { #expect(kFallbackPhaseOptions[i].value == i + 1) }
        #expect(kFallbackPhaseOptions[0].value == 1)
        #expect(kFallbackPhaseOptions[0].label.contains("Company universe"))
        #expect(kFallbackPhaseOptions[13].value == 14)
        #expect(kFallbackPhaseOptions[13].label.contains("ETF universe"))
    }

    @Test func fallbackDeletePhaseOptions() {
        #expect(kFallbackDeletePhaseOptions.count == 12)
        let values = Set(kFallbackDeletePhaseOptions.map(\.value))
        #expect(!values.contains(1))
        #expect(!values.contains(2))
        #expect(values.contains(3))
        #expect(values.contains(14))
    }
}

struct NexusCacheInfoTests {
    @Test func parsesAvailableCacheWithEntries() {
        let info = NexusCacheInfo(json: [
            "available": true,
            "cache_root": "/app/.cache",
            "entries": [
                ["path": "companies.pkl", "is_dir": false, "size_bytes": 204800],
                ["path": "ownership/", "is_dir": true],
            ],
        ])
        #expect(info.available)
        #expect(info.cacheRoot == "/app/.cache")
        #expect(info.entries.count == 2)
        #expect(info.entries[0].path == "companies.pkl")
        #expect(!info.entries[0].isDir)
        #expect(info.entries[0].sizeBytes == 204800)
        #expect(info.entries[1].isDir)
    }

    @Test func defaultsCacheRootWhenAbsent() {
        let info = NexusCacheInfo(json: ["available": false])
        #expect(info.cacheRoot == "/app/.cache")
        #expect(info.entries.isEmpty)
    }

    @Test func parsesErrorField() {
        #expect(NexusCacheInfo(json: ["available": false, "error": "Permission denied"]).error == "Permission denied")
    }
}

struct NexusRelCountTests {
    @Test func labelFallsBackToKeyWhenAbsent() {
        #expect(NexusRelCount(json: ["key": "supply_chain"]).label == "supply_chain")
    }

    @Test func usesExplicitLabelOverKey() {
        let rc = NexusRelCount(json: ["key": "supply_chain", "label": "Supply Chain", "active_count": 400, "total_count": 500])
        #expect(rc.label == "Supply Chain")
        #expect(rc.activeCount == 400)
        #expect(rc.totalCount == 500)
    }
}
