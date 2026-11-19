import Foundation

// The approvals and approval-devices routes: contract §5 of
// docs/superpowers/specs/2026-11-19-intellistock-mcp-contract.md. Every id
// that goes into a path is checked against the contract's id pattern first,
// so a bad id never reaches the network.

/// The approval reads and the decision, as the approvals screens use them.
nonisolated protocol ApprovalsSource: Sendable {
    func approvals(status: ApprovalListFilter, limit: Int) async throws -> [McpApproval]
    func approval(_ id: String) async throws -> McpApproval
    func decide(_ id: String, _ body: ApprovalDecisionBody) async throws -> McpApproval
}

/// The signing devices registered for the signed-in user.
nonisolated protocol ApprovalDevicesSource: Sendable {
    func registerDevice(name: String, publicKey: Data) async throws -> ApprovalDeviceReceipt
    func devices() async throws -> [McpApprovalDevice]
    func deleteDevice(_ deviceId: String) async throws
}

/// Thrown before any request when an id fails ApprovalIds.isValid.
nonisolated let kInvalidApprovalId = ApiError(message: "Not a valid approval id.")
/// A 2xx whose body has no "id", or another approval's (statusCode nil, so
/// callers read it as "unconfirmed").
nonisolated let kUnexpectedApprovalResponse = ApiError(message: "The server sent an unexpected response.")

/// Data access for `/mcp/approvals` and `/mcp/approval-devices`. Non-2xx
/// answers throw the `ApiError` the client builds, carrying the status and
/// the server's `detail` (a decision: 400 bad input, 403 bad signature or
/// device, 409 not pending, 410 expired).
nonisolated struct ApprovalsRepository: ApprovalsSource, ApprovalDevicesSource {
    /// The contract's page size for `GET /mcp/approvals`.
    static let listLimit = 50

    let client: ApiClient

    /// `GET /mcp/approvals?status=…&limit=…`: the object rows that carry an id.
    func approvals(status: ApprovalListFilter, limit: Int) async throws -> [McpApproval] {
        let data = try await client.get("/mcp/approvals", query: ["status": .string(status.rawValue), "limit": JSON(limit)])
        let rows = data.isArray ? data : data["approvals"]
        return rows.objectElements.map(McpApproval.init(json:)).filter { !$0.id.isEmpty }
    }

    /// `GET /mcp/approvals/{id}`.
    func approval(_ id: String) async throws -> McpApproval {
        try Self.requireValid(id)
        let data = try await client.get("/mcp/approvals/\(dartEncodeComponent(id))")
        return try Self.confirmed(data, id: id)
    }

    /// `POST /mcp/approvals/{id}/decision`. An approval answers after the
    /// server has executed the call, with the result on the row.
    func decide(_ id: String, _ body: ApprovalDecisionBody) async throws -> McpApproval {
        try Self.requireValid(id)
        let data = try await client.post("/mcp/approvals/\(dartEncodeComponent(id))/decision", body: body.json)
        return try Self.confirmed(data, id: id)
    }

    /// `POST /mcp/approval-devices` with the 65-byte X9.62 public key in
    /// standard base64. The receipt's id becomes the stored device record, so
    /// a 2xx without a valid one throws `kUnexpectedApprovalResponse`.
    func registerDevice(name: String, publicKey: Data) async throws -> ApprovalDeviceReceipt {
        let body = JSONObject([("name", .string(name)), ("public_key", .string(publicKey.base64EncodedString()))])
        let data = try await client.post("/mcp/approval-devices", body: .object(body))
        let receipt = ApprovalDeviceReceipt(json: data)
        guard ApprovalIds.isValid(receipt.deviceId) else { throw kUnexpectedApprovalResponse }
        return receipt
    }

    /// `GET /mcp/approval-devices`: the object rows that carry an id.
    func devices() async throws -> [McpApprovalDevice] {
        let data = try await client.get("/mcp/approval-devices")
        return data["devices"].objectElements.map(McpApprovalDevice.init(json:)).filter { !$0.id.isEmpty }
    }

    /// `DELETE /mcp/approval-devices/{device_id}` (204).
    func deleteDevice(_ deviceId: String) async throws {
        try Self.requireValid(deviceId)
        _ = try await client.delete("/mcp/approval-devices/\(dartEncodeComponent(deviceId))")
    }

    private static func requireValid(_ id: String) throws {
        guard ApprovalIds.isValid(id) else { throw kInvalidApprovalId }
    }

    /// The approval a 2xx carried. One without an id, or with an id other
    /// than the one requested, is not a confirmation.
    private static func confirmed(_ data: JSON, id: String) throws -> McpApproval {
        let approval = McpApproval(json: data)
        guard !approval.id.isEmpty, approval.id == id else { throw kUnexpectedApprovalResponse }
        return approval
    }
}
