import Foundation

// Ported from features/learning/data/learning_repository.dart.

// MARK: - Models

nonisolated struct LearningOverview: Hashable, Sendable {
    let mode: String
    let actsAutonomously: Bool
    let enabled: Bool
    let openFindings: Int
    let runsObserved: Int
    let decisionsObserved: Int
    let refusalsObserved: Int
    let engineRunning: Bool

    init(json j: JSON) {
        mode = j["mode"].stringOr("observe")
        actsAutonomously = j["acts_autonomously"].bool
        enabled = j["enabled"] != .bool(false)
        openFindings = j["open_findings"].intOr(0)
        runsObserved = j["runs_observed"].intOr(0)
        decisionsObserved = j["decisions_observed"].intOr(0)
        refusalsObserved = j["refusals_observed"].intOr(0)
        engineRunning = j["engine_running"].bool
    }
}

nonisolated struct LearningFinding: Hashable, Sendable, Identifiable {
    let id: String
    let kind: String
    let target: String
    let severity: String
    let title: String
    let detail: String
    let detectedAt: String
    let runId: String
    let status: String
    let evidence: JSONObject

    init(json j: JSON) {
        id = j["id"].stringOr("")
        kind = j["kind"].stringOr("")
        target = j["target"].stringOr("")
        severity = j["severity"].stringOr("low")
        title = j["title"].stringOr("")
        detail = j["detail"].stringOr("")
        detectedAt = j["detected_at"].stringOr("")
        runId = j["run_id"].stringOr("")
        status = j["status"].stringOr("open")
        evidence = j["evidence"].orderedObjectValue
    }
}

nonisolated struct LearningFunnel: Hashable, Sendable {
    let runId: String
    let target: String
    let decided: Int
    let executed: Int
    let refused: Int
    let buyDecided: Int
    let buyExecuted: Int

    init(json j: JSON) {
        runId = j["run_id"].stringOr("")
        target = j["target"].stringOr("")
        decided = j["decided"].intOr(0)
        executed = j["executed"].intOr(0)
        refused = j["refused"].intOr(0)
        buyDecided = j["buy_decided"].intOr(0)
        buyExecuted = j["buy_executed"].intOr(0)
    }

    /// nil when the run decided no buys — an undefined ratio, not zero.
    var buyConversionPct: Double? {
        buyDecided == 0 ? nil : (Double(buyExecuted) / Double(buyDecided)) * 100.0
    }
}

nonisolated struct LearningApproval: Hashable, Sendable, Identifiable {
    let id: String
    let rung: String
    let actionClass: String
    let target: String
    let summary: String
    let documentId: String
    let requestedAt: String
    /// Live rungs wait indefinitely — silence is never consent for real money.
    let holdsForever: Bool

    init(json j: JSON) {
        id = j["id"].stringOr("")
        rung = j["rung"].stringOr("")
        actionClass = j["action_class"].stringOr("")
        target = j["target"].stringOr("")
        summary = j["summary"].stringOr("")
        documentId = j["document_id"].stringOr("")
        requestedAt = j["requested_at"].stringOr("")
        holdsForever = j["holds_forever"].bool
    }
}

nonisolated struct LearningFloor: Hashable, Sendable {
    let target: String
    let windowClass: String
    let floorPp: Double
    let n: Int
    let measured: Bool
    let reason: String

    init(json j: JSON) {
        target = j["target"].stringOr("")
        windowClass = j["window_class"].stringOr("")
        floorPp = j["floor_pp"].doubleOr(0.0)
        n = j["n"].intOr(0)
        measured = j["measured"].bool
        reason = j["reason"].stringOr("")
    }
}

/// A strategy document the subsystem could be allowed to write to.
nonisolated struct LearningStrategyTarget: Hashable, Sendable, Identifiable {
    let id: String
    let name: String
    let subStrategies: Int
    let instanceNames: [String]
    /// "live" | "paper" | "unknown" | "none". Three states rather than a
    /// boolean: inferring live from "has a brokerage" flagged every document
    /// as REAL MONEY, which buried the one that actually was.
    let money: String

    var isLive: Bool { money == "live" }

    init(json j: JSON) {
        id = j["id"].stringOr("")
        name = j["name"].stringOr("")
        subStrategies = j["sub_strategies"].intOr(0)
        instanceNames = j["instance_names"].stringElements
        money = j["money"].stringOr("unknown")
    }
}

/// An instance whose runs the engine can be pointed at.
nonisolated struct LearningInstanceTarget: Hashable, Sendable, Identifiable {
    let id: String
    let name: String
    let kind: String
    let strategyId: String?
    let running: Bool
    let money: String

    var isLive: Bool { money == "live" }

    init(json j: JSON) {
        id = j["id"].stringOr("")
        name = j["name"].stringOr("")
        kind = j["kind"].stringOr("")
        strategyId = j["strategy_id"].string
        running = j["running"].bool
        money = j["money"].stringOr("unknown")
    }
}

nonisolated struct LearningTargets: Hashable, Sendable {
    let strategies: [LearningStrategyTarget]
    let instances: [LearningInstanceTarget]
    let documentAllowlist: [String]
    let watchedInstances: [String]
    /// An empty watch list means EVERY instance — the opposite of the
    /// allowlist, where empty means write nowhere.
    let watchingAll: Bool

    init(json j: JSON) {
        strategies = j["strategies"].objectElements.map(LearningStrategyTarget.init(json:))
        instances = j["instances"].objectElements.map(LearningInstanceTarget.init(json:))
        documentAllowlist = j["document_allowlist"].stringElements
        watchedInstances = j["watched_instances"].stringElements
        watchingAll = j["watching_all"].bool
    }
}

// MARK: - Repository

nonisolated struct LearningRepository: Sendable {
    let client: ApiClient

    func overview() async throws -> LearningOverview {
        LearningOverview(json: try await client.get("/learning/overview"))
    }

    func findings(limit: Int = 100) async throws -> [LearningFinding] {
        let data = try await client.get("/learning/findings", query: ["limit": .string(String(limit))])
        return data["findings"].objectElements.map(LearningFinding.init(json:))
    }

    func approvals(limit: Int = 100) async throws -> [LearningApproval] {
        let data = try await client.get("/learning/approvals", query: ["limit": .string(String(limit))])
        return data["pending"].objectElements.map(LearningApproval.init(json:))
    }

    func noiseFloors() async throws -> [LearningFloor] {
        let data = try await client.get("/learning/noise-floors")
        return data["floors"].objectElements.map(LearningFloor.init(json:))
    }

    func targets() async throws -> LearningTargets {
        LearningTargets(json: try await client.get("/learning/targets"))
    }

    func setDocumentAllowlist(_ ids: [String]) async throws {
        _ = try await client.post(
            "/learning/control",
            body: ["config": ["document_allowlist": .array(ids.map(JSON.string))]]
        )
    }

    func setWatchedInstances(_ ids: [String]) async throws {
        _ = try await client.post(
            "/learning/control",
            body: ["config": ["watched_instances": .array(ids.map(JSON.string))]]
        )
    }

    func control() async throws -> JSONObject {
        try await client.get("/learning/control").orderedObjectValue
    }

    func setRunning(_ running: Bool) async throws {
        _ = try await client.post("/learning/control", body: ["running": .bool(running)])
    }

    func setMode(_ mode: String) async throws {
        _ = try await client.post("/learning/control", body: ["config": ["mode": .string(mode)]])
    }

    func decide(_ approvalId: String, _ decision: String) async throws {
        _ = try await client.post("/learning/approvals/\(approvalId)", body: ["decision": .string(decision)])
    }

    func funnels(limit: Int = 100) async throws -> [LearningFunnel] {
        let data = try await client.get("/learning/funnels", query: ["limit": .string(String(limit))])
        return data["funnels"].objectElements.map(LearningFunnel.init(json:))
    }
}
