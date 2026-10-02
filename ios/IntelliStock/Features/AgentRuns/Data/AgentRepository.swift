import Foundation

// Ported from features/agent_runs/data/agent_repository.dart.

nonisolated struct AgentStage: Hashable, Sendable {
    let label: String
    let status: String
    let stocks: [String]
    let pnl: Num?
    let pnlPct: Num?
    let details: String?

    init(json: JSON) {
        label = json["label"].stringOr("")
        status = json["status"].stringOr("pending")
        // Dart `.cast<String>()`; a non-string element threw there and is
        // printed here.
        stocks = json["stocks"].stringElements
        pnl = json["pnl"].num
        pnlPct = json["pnl_pct"].num
        details = json["details"].string
    }
}

nonisolated struct AgentRun: Hashable, Sendable, Identifiable {
    let id: String
    let status: String
    let cycleId: String?
    let name: String?
    let createdAt: Date?
    let stages: [AgentStage]
    let finalResult: String?

    init(json: JSON) {
        id = json["id"].stringOr("")
        status = json["status"].stringOr("stopped")
        cycleId = json["cycle_id"].string
        name = json["name"].string
        createdAt = json["created_at"].isNull ? nil : DartDateTime.tryParse(json["created_at"].dartDescription)
        stages = json["stages"].objectElements.map(AgentStage.init(json:))
        finalResult = json["final_result"].string
    }
}

nonisolated struct AgentRunsPage: Hashable, Sendable {
    let runs: [AgentRun]
    let total: Int
    let totalPages: Int
    let page: Int

    init(json: JSON) {
        runs = json["runs"].objectElements.map(AgentRun.init(json:))
        total = json["total"].intOr(0)
        totalPages = json["total_pages"].intOr(1)
        page = json["page"].intOr(1)
    }
}

nonisolated struct AgentControl: Hashable, Sendable {
    let running: Bool
    let paused: Bool

    init(running: Bool = false, paused: Bool = false) {
        self.running = running
        self.paused = paused
    }

    init(json: JSON) {
        self.init(running: json["running"].bool, paused: json["paused"].bool)
    }

    var isRunning: Bool { running && !paused }
    var isPaused: Bool { running && paused }
    var isStopped: Bool { !running }
}

nonisolated struct AgentRepository: Sendable {
    let client: ApiClient

    func runs(page: Int = 1, perPage: Int = 20) async throws -> AgentRunsPage {
        let data = try await client.get(
            "/agent/runs",
            query: ["page": .string(String(page)), "per_page": .string(String(perPage))]
        )
        return AgentRunsPage(json: data)
    }

    func control() async throws -> AgentControl {
        AgentControl(json: try await client.get("/agent/control"))
    }

    func setControl(running: Bool? = nil, paused: Bool? = nil, specialRequest: String? = nil) async throws {
        var body: JSONObject = [:]
        if let running { body["running"] = .bool(running) }
        if let paused { body["paused"] = .bool(paused) }
        if let specialRequest, !specialRequest.isEmpty { body["special_request"] = .string(specialRequest) }
        _ = try await client.post("/agent/control", body: .object(body))
    }

    func forceStop(_ logId: String) async throws {
        _ = try await client.post("/agent/runs/\(logId)/force-stop")
    }
}
