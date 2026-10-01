import Foundation

/// Data access for /brokerages endpoints, ported from
/// features/brokerages/data/brokerage_repository.dart.
nonisolated struct BrokerageRepository: Sendable {
    let client: ApiClient

    // MARK: Read

    /// GET /brokerages → {accounts: [...]}
    func list() async throws -> [Brokerage] {
        let data = try await client.get("/brokerages")
        return data["accounts"].objectElements.map(Brokerage.init(json:))
    }

    // MARK: Mutations

    /// POST /brokerages — link a new account.
    func link(_ body: [String: JSON]) async throws -> [String: JSON] {
        try await client.post("/brokerages", body: .object(body)).objectValue
    }

    /// PUT /brokerages/{id} — edit an existing account.
    func edit(_ id: String, _ body: [String: JSON]) async throws -> [String: JSON] {
        try await client.put("/brokerages/\(id)", body: .object(body)).objectValue
    }

    /// DELETE /brokerages/{id}
    func remove(_ id: String) async throws {
        _ = try await client.delete("/brokerages/\(id)")
    }

    /// POST /brokerages/test-alpaca — diagnostic probe, does NOT save.
    func testAlpaca(_ body: [String: JSON]) async throws -> [String: JSON] {
        try await client.post("/brokerages/test-alpaca", body: .object(body)).objectValue
    }
}
