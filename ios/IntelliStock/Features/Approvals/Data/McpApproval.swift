import Foundation

// The MCP approvals wire shapes: contract §5 of
// docs/superpowers/specs/2026-11-19-intellistock-mcp-contract.md, which lands
// with the API branch (feat/mcp-api-gate). The plan is
// docs/superpowers/plans/2026-11-19-mcp-ios-approvals.md.
//
// Jarvis asks the API to perform a live-money or irreversible action; the API
// holds it as an approval until the operator approves or denies it from this
// app with a Secure Enclave signature, or it expires (15 minutes).

/// One action Jarvis asked to take, held for the operator's decision.
nonisolated struct McpApproval: Hashable, Sendable, Identifiable {
    let id: String
    /// pending | approved | denied | expired | executed | failed.
    let status: String
    let summary: String
    /// live | unknown | protected | irreversible.
    let moneyState: String
    let client: String
    let method: String
    let path: String
    /// The server's fingerprint of {method, path, query, body}; the decision
    /// signs it, so the app only signs what it can recompute.
    let bodySha256: String
    let createdAt: Date?
    let expiresAt: Date?
    let decidedAt: Date?
    /// Exactly as received (a missing key is `.null`): the fingerprint must
    /// hash what the server sent, not a normalised copy.
    let query: JSON
    let body: JSON
    /// The executed call's response, unredacted. Capped at 256 KB by the
    /// server, with `"truncated": true` when cut.
    let result: JSON
    let resultStatus: Int?
    /// The device that decided it; nil when absent or empty.
    let deviceId: String?

    init(json j: JSON) {
        id = j["id"].string ?? ""
        status = j["status"].string ?? ""
        summary = j["summary"].string ?? ""
        moneyState = j["money_state"].string ?? ""
        client = j["client"].string ?? ""
        method = j["method"].string ?? ""
        path = j["path"].string ?? ""
        bodySha256 = j["body_sha256"].string ?? ""
        createdAt = DartDateTime.tryParse(j["created_at"].string)
        expiresAt = DartDateTime.tryParse(j["expires_at"].string)
        decidedAt = DartDateTime.tryParse(j["decided_at"].string)
        query = j["query"]
        body = j["body"]
        result = j["result"]
        resultStatus = j["result_status"].int
        let device = j["device_id"].string ?? ""
        deviceId = device.isEmpty ? nil : device
    }

    var isPending: Bool { status == "pending" }

    /// Nothing more will happen to it. `approved` is not terminal: the
    /// server is still executing the call.
    var isTerminal: Bool { Self.terminalStatuses.contains(status) }

    var resultTruncated: Bool { result["truncated"].bool }

    /// At or past its expiry; an approval with no expiry counts as expired,
    /// so the app never offers to sign one.
    func isExpired(at now: Date) -> Bool {
        guard let expiresAt else { return true }
        return now >= expiresAt
    }

    private static let terminalStatuses: Set<String> = ["denied", "expired", "executed", "failed"]
}

/// A registered signing device: `{"device_id", "name", "created_at", "active_at"}`.
nonisolated struct McpApprovalDevice: Hashable, Sendable, Identifiable {
    let id: String
    let name: String
    let createdAt: Date?
    /// The server accepts its signatures from this instant (registration + 10 minutes).
    let activeAt: Date?

    init(json j: JSON) {
        let deviceId = j["device_id"].string ?? ""
        id = deviceId.isEmpty ? (j["id"].string ?? "") : deviceId
        name = j["name"].string ?? ""
        createdAt = DartDateTime.tryParse(j["created_at"].string)
        activeAt = DartDateTime.tryParse(j["active_at"].string)
    }

    func isActive(at now: Date) -> Bool {
        guard let activeAt else { return false }
        return now >= activeAt
    }
}

/// The 201 body of `POST /mcp/approval-devices`.
nonisolated struct ApprovalDeviceReceipt: Hashable, Sendable {
    let deviceId: String
    let activeAt: Date?

    init(json j: JSON) {
        deviceId = j["device_id"].string ?? ""
        activeAt = DartDateTime.tryParse(j["active_at"].string)
    }
}

nonisolated enum ApprovalDecision: String, Sendable {
    case approve, deny
}

/// `GET /mcp/approvals?status=…`.
nonisolated enum ApprovalListFilter: String, Sendable {
    case pending, all
}

/// The body of `POST /mcp/approvals/{id}/decision`.
nonisolated struct ApprovalDecisionBody: Hashable, Sendable {
    let decision: ApprovalDecision
    let bodySha256: String
    /// Unix seconds; the server accepts |now - ts| <= 60.
    let ts: Int
    let deviceId: String
    /// DER ECDSA P-256 SHA-256 over `ApprovalMessage.bytes`. Never log it.
    let signature: Data

    /// The contract's keys, in its order, with the signature in standard base64.
    var json: JSON {
        .object(JSONObject([
            ("decision", .string(decision.rawValue)),
            ("body_sha256", .string(bodySha256)),
            ("ts", .int(ts)),
            ("device_id", .string(deviceId)),
            ("signature", .string(signature.base64EncodedString())),
        ]))
    }
}

/// Approval and device ids go into URL paths, so the app checks them against
/// the contract's `^[A-Za-z0-9_-]{1,128}$` before building one.
nonisolated enum ApprovalIds {
    static func isValid(_ id: String) -> Bool {
        let bytes = id.utf8
        guard (1...128).contains(bytes.count) else { return false }
        return bytes.allSatisfy { byte in
            switch byte {
            case UInt8(ascii: "A")...UInt8(ascii: "Z"), UInt8(ascii: "a")...UInt8(ascii: "z"),
                 UInt8(ascii: "0")...UInt8(ascii: "9"), UInt8(ascii: "_"), UInt8(ascii: "-"):
                true
            default:
                false
            }
        }
    }
}
