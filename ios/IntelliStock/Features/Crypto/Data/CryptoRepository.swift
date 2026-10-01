import Foundation

/// Data layer for crypto (kind='crypto') instances, ported from
/// features/crypto/data/crypto_repository.dart.
///
/// Crypto instances run through the SAME `/instances` API as equity instances
/// (kind-gated on the backend), so create/edit carry a `crypto_config` blob
/// (band + per-coin allocations) plus a fixed symbol universe;
/// start/stop/delete reuse the shared instance endpoints.
nonisolated struct CryptoRepository: Sendable {
    let client: ApiClient

    /// GET /instances → only the crypto (`kind == 'crypto'`) instances.
    func listInstances() async throws -> [Instance] {
        let data = try await client.get("/instances")
        return data["instances"].objectElements
            .filter { $0["kind"] == "crypto" }
            .map(Instance.init(json:))
    }

    /// GET /instances/:id → a single crypto instance (detail screen).
    func getInstance(_ id: String) async throws -> Instance {
        Self.unwrapInstance(try await client.get("/instances/\(id)"))
    }

    /// GET /instances/:id/backtests → this instance's backtests, newest first.
    func instanceBacktests(_ id: String) async throws -> [InstanceBacktestRow] {
        let data = try await client.get(
            "/instances/\(id)/backtests",
            query: ["page": "1", "per_page": "20", "sort_by": "completed_at", "sort_order": "desc"]
        )
        return data["backtests"].objectElements.map(InstanceBacktestRow.init(json:))
    }

    /// POST /instances — create a crypto instance. `body` is the fully formed
    /// payload (id, name, granularity, run_command, kind, brokerage_id,
    /// strategy_id, stocks, crypto_config) built by the sheet.
    func createInstance(_ body: [String: JSON]) async throws -> Instance {
        Self.unwrapInstance(try await client.post("/instances", body: .object(body)))
    }

    /// PATCH /instances/:id — edit an existing crypto instance's allocation
    /// (crypto_config + stocks).
    func updateInstance(_ id: String, _ body: [String: JSON]) async throws -> Instance {
        Self.unwrapInstance(try await client.patch("/instances/\(id)", body: .object(body)))
    }

    /// POST /backtests — backtest a crypto instance's configured allocation.
    /// The backend runs crypto instances through the same broker.py as live
    /// (v1beta3 historical bars + taker-fee fills + synthetic strategy from
    /// crypto_config), so this is the equity backtest payload with the
    /// instance's slash-pairs. Returns the created row (used for its `id` to
    /// open the result view).
    func createBacktest(
        instanceId: String,
        stocks: [String],
        startDate: String,
        endDate: String,
        granularity: String = "900",
        initialCash: Double = 10_000,
        emulateFeeVenue: String = "default"
    ) async throws -> [String: JSON] {
        try await client.post(
            "/backtests",
            body: [
                "instance_id": .string(instanceId),
                "stocks": .array(stocks.map(JSON.string)),
                "start_date": .string(startDate),
                "end_date": .string(endDate),
                "granularity": .string(granularity),
                "initial_cash": .double(initialCash),
                "emulate_fee_venue": .string(emulateFeeVenue),
            ]
        ).objectValue
    }

    // MARK: Lifecycle (shared instance endpoints)

    func startInstance(_ id: String) async throws {
        _ = try await client.post("/instances/\(id)/start")
    }

    func stopInstance(_ id: String) async throws {
        _ = try await client.post("/instances/\(id)/stop")
    }

    func deleteInstance(_ id: String, force: Bool = false) async throws {
        _ = try await client.delete("/instances/\(id)", query: force ? ["force": "true"] : [:])
    }

    // MARK: Selectors used by the create/edit sheet

    /// GET /brokerages → raw account maps (id / account_name / brokerage_type).
    func brokerages() async throws -> [[String: JSON]] {
        try await client.get("/brokerages")["accounts"].objectElements.map(\.objectValue)
    }

    /// GET /strategies → existing strategy docs (used to resolve the chosen
    /// dynamic-strategy name → its integer strategy_id).
    func strategies() async throws -> [[String: JSON]] {
        try await client.get("/strategies")["strategies"].objectElements.map(\.objectValue)
    }

    /// Account equity for a brokerage: uninvested cash + Σ position market
    /// value. Drives the %↔$ conversion in the allocation editor. Returns 0
    /// when the balance is unavailable.
    func accountEquity(_ brokerageId: String) async -> Double {
        guard let data = try? await client.get("/brokerages/\(brokerageId)/positions") else { return 0 }
        let cash = data["cash"].doubleOr(0)
        let invested = data["positions"].objectElements.reduce(0.0) { $0 + $1["marketValue"].doubleOr(0) }
        return cash + invested
    }

    /// `data['instance'] as Map? ?? data`.
    private static func unwrapInstance(_ data: JSON) -> Instance {
        Instance(json: data["instance"].isObject ? data["instance"] : data)
    }
}
