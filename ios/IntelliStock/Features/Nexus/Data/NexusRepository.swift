import Foundation

// Ported from features/nexus/data/nexus_repository.dart.

// MARK: - Models

nonisolated struct NexusStage: Hashable, Sendable {
    let key: String
    let label: String
    /// pending | running | completed | skipped | stopped | failed
    let status: String
    let message: String?
    let durationSec: Double?
    let substepsCompleted: Int?
    let totalSubsteps: Int?
    let stageIndex: Int

    init(json j: JSON) {
        key = j["key"].stringOr("")
        label = j["label"].stringOr("")
        status = j["status"].stringOr("pending")
        message = j["message"].string
        durationSec = j["duration_sec"].double
        substepsCompleted = j["substeps_completed"].int
        totalSubsteps = j["total_substeps"].int
        stageIndex = j["stage_index"].intOr(0)
    }

    var substepFraction: Double {
        guard let totalSubsteps, totalSubsteps != 0 else { return 0 }
        return min(max(Double(substepsCompleted ?? 0) / Double(totalSubsteps), 0.0), 1.0)
    }
}

nonisolated struct NexusGraphBuild: Hashable, Sendable {
    let status: String?
    let progressPct: Double
    let etaFormatted: String?
    let currentPhaseLabel: String?
    let message: String?
    let stages: [NexusStage]
    let lastUpdated: Date?

    init(json j: JSON) {
        status = j["status"].string
        progressPct = j["progress_pct"].doubleOr(0)
        etaFormatted = j["eta_formatted"].string
        currentPhaseLabel = j["current_phase_label"].string
        message = j["message"].string
        stages = j["stages"].objectElements.map(NexusStage.init(json:))
        lastUpdated = j["last_updated"].isNull ? nil : DartDateTime.tryParse(j["last_updated"].dartDescription)
    }
}

nonisolated struct NexusRelCount: Hashable, Sendable {
    let key: String
    let label: String
    let activeCount: Int?
    let totalCount: Int?

    init(json j: JSON) {
        key = j["key"].stringOr("")
        label = j["label"].or(j["key"]).stringOr("")
        activeCount = j["active_count"].int
        totalCount = j["total_count"].int
    }
}

nonisolated struct NexusGraphSummary: Hashable, Sendable {
    let relationshipCounts: [NexusRelCount]
    let nodeCounts: JSONObject

    init(json j: JSON) {
        relationshipCounts = j["relationship_counts"].objectElements.map(NexusRelCount.init(json:))
        nodeCounts = j["node_counts"].orderedObjectValue
    }
}

nonisolated struct NexusPhaseOption: Hashable, Sendable {
    let value: Int
    let label: String

    init(value: Int, label: String) {
        self.value = value
        self.label = label
    }

    init(json j: JSON) {
        self.init(value: j["value"].intOr(0), label: j["label"].stringOr(""))
    }
}

nonisolated struct NexusControl: Hashable, Sendable {
    let running: Bool
    let autoUpdateEnabled: Bool
    let autoUpdateIntervalHours: Int
    let autoUpdateStartPhase: Int
    let autoUpdateEndPhase: Int
    let nextAutoUpdateAt: Date?
    let selectedPhases: [Int]
    let phase7HistoryQuarters: Int
    let historicalModeEnabled: Bool
    let historicalStartDate: String?
    let historicalCoverageEnd: String?
    let autoUpdateStartPhaseLabel: String?
    let autoUpdateEndPhaseLabel: String?
    let phaseOptions: [NexusPhaseOption]
    let deletePhaseOptions: [NexusPhaseOption]

    let deleteOperationActive: Bool
    let deleteOperationStep: String?
    let deleteOperationMessage: String?
    let deleteOperationCurrent: Num?
    let deleteOperationTotal: Num?
    let deleteOperationUnit: String?
    let deleteOperationError: String?
    let deleteOperationSelectedPhases: [Int]
    let deleteOperationPhaseRows: [JSONObject]

    let rebuildOperationActive: Bool
    let rebuildOperationDestructive: Bool
    let rebuildOperationStep: String?
    let rebuildOperationMessage: String?
    let rebuildOperationCurrent: Num?
    let rebuildOperationTotal: Num?
    let rebuildOperationUnit: String?

    init(json j: JSON) {
        func parsePhaseOptions(_ raw: JSON) -> [NexusPhaseOption] {
            raw.objectElements.map(NexusPhaseOption.init(json:))
        }
        /// `whereType<num>().map((n) => n.toInt())`.
        func ints(_ raw: JSON) -> [Int] { raw.arrayValue.compactMap(\.int) }

        running = j["running"].bool
        autoUpdateEnabled = j["auto_update_enabled"].bool
        autoUpdateIntervalHours = j["auto_update_interval_hours"].intOr(168)
        autoUpdateStartPhase = j["auto_update_start_phase"].intOr(3)
        autoUpdateEndPhase = j["auto_update_end_phase"].intOr(14)
        nextAutoUpdateAt = j["next_auto_update_at"].isNull
            ? nil : DartDateTime.tryParse(j["next_auto_update_at"].dartDescription)
        selectedPhases = ints(j["selected_phases"])
        phase7HistoryQuarters = j["phase7_history_quarters"].intOr(1)
        historicalModeEnabled = j["historical_mode_enabled"].bool
        historicalStartDate = j["historical_start_date"].string
        historicalCoverageEnd = j["historical_coverage_end"].string
        autoUpdateStartPhaseLabel = j["auto_update_start_phase_label"].string
        autoUpdateEndPhaseLabel = j["auto_update_end_phase_label"].string
        phaseOptions = parsePhaseOptions(j["phase_options"])
        deletePhaseOptions = parsePhaseOptions(j["delete_phase_options"])
        deleteOperationActive = j["delete_operation_active"].bool
        deleteOperationStep = j["delete_operation_step"].string
        deleteOperationMessage = j["delete_operation_message"].string
        deleteOperationCurrent = j["delete_operation_current"].num
        deleteOperationTotal = j["delete_operation_total"].num
        deleteOperationUnit = j["delete_operation_unit"].string
        deleteOperationError = j["delete_operation_error"].string
        deleteOperationSelectedPhases = ints(j["delete_operation_selected_phases"])
        deleteOperationPhaseRows = j["delete_operation_phase_rows"].objectElements.map(\.orderedObjectValue)
        rebuildOperationActive = j["rebuild_operation_active"].bool
        rebuildOperationDestructive = j["rebuild_operation_destructive"].bool
        rebuildOperationStep = j["rebuild_operation_step"].string
        rebuildOperationMessage = j["rebuild_operation_message"].string
        rebuildOperationCurrent = j["rebuild_operation_current"].num
        rebuildOperationTotal = j["rebuild_operation_total"].num
        rebuildOperationUnit = j["rebuild_operation_unit"].string
    }
}

nonisolated struct NexusBootstrap: Hashable, Sendable {
    let enabled: Bool
    let status: String
    let startDate: String?
    let coverageEnd: String?
    let complete: Bool
    let lastStatus: String?
    let phases: [JSONObject]
    let completedPhases: Int?
    let totalPhases: Int?
    let durationSec: Double?
    let completedAt: Date?
    let startedAt: Date?

    init(json j: JSON) {
        enabled = j["enabled"].bool
        status = j["status"].stringOr("disabled")
        startDate = j["start_date"].string
        coverageEnd = j["coverage_end"].string
        complete = j["complete"].bool
        lastStatus = j["last_status"].string
        phases = j["phases"].objectElements.map(\.orderedObjectValue)
        completedPhases = j["completed_phases"].int
        totalPhases = j["total_phases"].int
        durationSec = j["duration_sec"].double
        completedAt = j["completed_at"].isNull ? nil : DartDateTime.tryParse(j["completed_at"].dartDescription)
        startedAt = j["started_at"].isNull ? nil : DartDateTime.tryParse(j["started_at"].dartDescription)
    }
}

nonisolated struct NexusScraper: Hashable, Sendable {
    let status: String?
    let index: Int?
    let totalTickers: Int?
    let edgesCount: Int?
    let progressPct: Double
    let etaFormatted: String?

    init(json j: JSON) {
        status = j["status"].string
        index = j["index"].int
        totalTickers = j["total_tickers"].int
        edgesCount = j["edges_count"].int
        progressPct = j["progress_pct"].doubleOr(0)
        etaFormatted = j["eta_formatted"].string
    }
}

nonisolated struct NexusStatus: Hashable, Sendable {
    let control: NexusControl
    let graphBuild: NexusGraphBuild?
    let graphSummary: NexusGraphSummary?
    let scraper: NexusScraper?
    let bootstrap: NexusBootstrap?
    let graphBuilt: Bool

    var serviceRunning: Bool { control.running }
    var isBuilding: Bool { serviceRunning && (graphBuild?.status ?? "").lowercased() == "running" }
    var showBuilt: Bool { graphBuilt && !isBuilding }

    /// Dart `_asMap`: a map, or `{}` for anything else.
    init(json j: JSON) {
        func asMap(_ v: JSON) -> JSON { v.isObject ? v : .object([:]) }
        control = NexusControl(json: asMap(j["control"]))
        graphBuild = j["graph_build"].isNull ? nil : NexusGraphBuild(json: asMap(j["graph_build"]))
        graphSummary = j["graph_summary"].isNull ? nil : NexusGraphSummary(json: asMap(j["graph_summary"]))
        scraper = j["scraper"].isNull ? nil : NexusScraper(json: asMap(j["scraper"]))
        bootstrap = j["bootstrap"].isNull ? nil : NexusBootstrap(json: asMap(j["bootstrap"]))
        graphBuilt = j["graph_built"].bool
    }
}

nonisolated struct NexusCacheEntry: Hashable, Sendable {
    let path: String
    let isDir: Bool
    let sizeBytes: Int?

    init(json j: JSON) {
        path = j["path"].stringOr("")
        isDir = j["is_dir"].bool
        sizeBytes = j["size_bytes"].int
    }
}

nonisolated struct NexusCacheInfo: Hashable, Sendable {
    let available: Bool
    let cacheRoot: String
    let entries: [NexusCacheEntry]
    let error: String?

    init(json j: JSON) {
        available = j["available"].bool
        cacheRoot = j["cache_root"].stringOr("/app/.cache")
        entries = j["entries"].objectElements.map(NexusCacheEntry.init(json:))
        error = j["error"].string
    }
}

// MARK: - Fallback phase options

nonisolated let kFallbackPhaseOptions: [NexusPhaseOption] = [
    NexusPhaseOption(value: 1, label: "Phase 1: Company universe"),
    NexusPhaseOption(value: 2, label: "Phase 2: Easy relationships"),
    NexusPhaseOption(value: 3, label: "Phase 2b: SEC sector/industry"),
    NexusPhaseOption(value: 4, label: "Phase 3: Supply chain"),
    NexusPhaseOption(value: 5, label: "Phase 4: Competitive"),
    NexusPhaseOption(value: 6, label: "Phase 5: Macro/BEA"),
    NexusPhaseOption(value: 7, label: "Phase 6: GLEIF hierarchy"),
    NexusPhaseOption(value: 8, label: "Phase 6B: SEC EX-21 hierarchy"),
    NexusPhaseOption(value: 9, label: "Phase 7: 13F ownership"),
    NexusPhaseOption(value: 10, label: "Phase 8: USASpending"),
    NexusPhaseOption(value: 11, label: "Phase 9: Wikidata"),
    NexusPhaseOption(value: 12, label: "Phase 10: PatentsView"),
    NexusPhaseOption(value: 13, label: "Phase 11: 8-K agreements"),
    NexusPhaseOption(value: 14, label: "Phase 12: ETF universe"),
]

nonisolated private let kDeletePhaseValues: Set<Int> = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]

nonisolated var kFallbackDeletePhaseOptions: [NexusPhaseOption] {
    kFallbackPhaseOptions.filter { kDeletePhaseValues.contains($0.value) }
}

// MARK: - Repository

nonisolated struct NexusRepository: Sendable {
    let client: ApiClient

    func status() async throws -> NexusStatus {
        NexusStatus(json: try await client.get("/nexus/status"))
    }

    func control(_ body: JSONObject) async throws {
        _ = try await client.post("/nexus/control", body: .object(body))
    }

    func rebuild(_ body: JSONObject) async throws -> JSONObject {
        try await client.post("/nexus/rebuild", body: .object(body)).orderedObjectValue
    }

    func deleteEdges(_ body: JSONObject) async throws {
        _ = try await client.post("/nexus/delete-edges", body: .object(body))
    }

    func cache() async throws -> NexusCacheInfo {
        NexusCacheInfo(json: try await client.get("/nexus/cache"))
    }
}
