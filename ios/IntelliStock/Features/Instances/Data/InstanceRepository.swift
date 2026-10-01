import Foundation

/// Data layer for all instance-related API calls, ported from
/// features/instances/data/instance_repository.dart.
nonisolated struct InstanceRepository: Sendable {
    let client: ApiClient

    // MARK: Instances

    func listInstances() async throws -> [Instance] {
        let data = try await client.get("/instances")
        return data["instances"].objectElements
            // Kalshi + crypto bots each have their own dedicated screen, so
            // they're excluded from the general equity instances list.
            .filter { $0["kind"] != "kalshi" && $0["kind"] != "crypto" }
            .map(Instance.init(json:))
    }

    func getInstance(_ id: String) async throws -> Instance {
        Instance(json: try await client.get("/instances/\(id)"))
    }

    func createInstance(
        id: String,
        name: String? = nil,
        granularity: String? = nil,
        runCommand: Bool = false,
        brokerageId: String? = nil,
        maxUsage: Double? = nil,
        strategyId: String? = nil
    ) async throws -> Instance {
        var body: [String: JSON] = ["id": .string(id)]
        if let name, !name.isEmpty { body["name"] = .string(name) }
        body["granularity"] = .string(granularity ?? "60")
        body["run_command"] = .bool(runCommand)
        if let brokerageId, !brokerageId.isEmpty { body["brokerage_id"] = .string(brokerageId) }
        if let maxUsage { body["max_usage"] = .double(maxUsage) }
        if let strategyId, !strategyId.isEmpty { body["strategy_id"] = .string(strategyId) }
        let data = try await client.post("/instances", body: .object(body))
        return Instance(json: data["instance"].isObject ? data["instance"] : data)
    }

    func patchInstance(_ id: String, _ patch: [String: JSON]) async throws -> Instance {
        let data = try await client.patch("/instances/\(id)", body: .object(patch))
        return Instance(json: data["instance"].isObject ? data["instance"] : data)
    }

    func deleteInstance(_ id: String, force: Bool = false) async throws {
        _ = try await client.delete("/instances/\(id)", query: force ? ["force": "true"] : [:])
    }

    func startInstance(_ id: String) async throws {
        _ = try await client.post("/instances/\(id)/start")
    }

    func stopInstance(_ id: String) async throws {
        _ = try await client.post("/instances/\(id)/stop")
    }

    func clearState(_ id: String, _ scope: String, apply: Bool = false, confirm: String? = nil) async throws {
        var body: [String: JSON] = ["scope": .string(scope), "apply": .bool(apply)]
        if let confirm { body["confirm"] = .string(confirm) }
        _ = try await client.post("/instances/\(id)/clear-state", body: .object(body))
    }

    func previewClearState(_ id: String, _ scope: String) async throws -> [String: JSON] {
        try await client.post(
            "/instances/\(id)/clear-state",
            body: ["scope": .string(scope), "apply": false]
        ).objectValue
    }

    func applyClearState(_ id: String, _ scope: String) async throws -> [String: JSON] {
        try await client.post(
            "/instances/\(id)/clear-state",
            body: ["scope": .string(scope), "apply": true, "confirm": .string(id)]
        ).objectValue
    }

    // MARK: Stock management

    func addStock(_ id: String, _ symbol: String) async throws {
        _ = try await client.post("/instances/\(id)/stocks", body: ["symbol": .string(symbol)])
    }

    func removeStock(_ id: String, _ symbol: String) async throws {
        _ = try await client.delete("/instances/\(id)/stocks/\(symbol)")
    }

    // MARK: Link / unlink brokerage & strategy

    func linkBrokerage(_ id: String, _ brokerageId: String) async throws {
        _ = try await client.post("/instances/\(id)/link-brokerage", body: ["brokerage_id": .string(brokerageId)])
    }

    func unlinkBrokerage(_ id: String) async throws {
        _ = try await client.patch("/instances/\(id)", body: ["brokerage_id": ""])
    }

    /// `brokerageId` nil sends `{"brokerage_id": null}` (unlink), as Dart did.
    func linkDataBrokerage(_ id: String, _ brokerageId: String?) async throws {
        _ = try await client.post("/instances/\(id)/link-data-brokerage", body: ["brokerage_id": JSON(brokerageId)])
    }

    /// Sends the id as an int when it parses as one, else as the string.
    func linkStrategy(_ id: String, _ strategyId: String) async throws {
        let value: JSON = JSON.parseInt(strategyId).map(JSON.int) ?? .string(strategyId)
        _ = try await client.post("/instances/\(id)/link-strategy", body: ["strategy_id": value])
    }

    func unlinkStrategy(_ id: String) async throws {
        _ = try await client.post("/instances/\(id)/unlink-strategy")
    }

    // MARK: Backtests

    func listBacktests(
        _ instanceId: String,
        page: Int = 1,
        perPage: Int = 15,
        sortBy: String = "completed_at",
        sortOrder: String = "desc"
    ) async throws -> [String: JSON] {
        try await client.get(
            "/instances/\(instanceId)/backtests",
            query: [
                "page": .string(String(page)),
                "per_page": .string(String(perPage)),
                "sort_by": .string(sortBy),
                "sort_order": .string(sortOrder),
            ]
        ).objectValue
    }

    func createBacktest(
        instanceId: String,
        stocks: [String],
        startDate: String,
        endDate: String,
        granularity: String = "60",
        initialCash: Double = 100_000
    ) async throws {
        _ = try await client.post(
            "/backtests",
            body: [
                "instance_id": .string(instanceId),
                "stocks": .array(stocks.map(JSON.string)),
                "start_date": .string(startDate),
                "end_date": .string(endDate),
                "granularity": .string(granularity),
                "initial_cash": .double(initialCash),
            ]
        )
    }

    func getBacktestStatus(_ backtestId: String) async throws -> [String: JSON] {
        try await client.get("/backtests/\(backtestId)/status").objectValue
    }

    // MARK: Selectors

    func listBrokerages() async throws -> [[String: JSON]] {
        try await client.get("/brokerages")["accounts"].objectElements.map(\.objectValue)
    }

    func listStrategies() async throws -> [[String: JSON]] {
        try await client.get("/strategies")["strategies"].objectElements.map(\.objectValue)
    }
}
