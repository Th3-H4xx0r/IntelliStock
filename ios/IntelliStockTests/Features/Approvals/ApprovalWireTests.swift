import Foundation
import Testing
@testable import IntelliStock

/// The approval wire shapes of contract §5. `full` is the fixture every
/// later approvals suite reuses. 1_795_068_000 is 2026-11-19T06:00:00Z.
struct ApprovalWireTests {
    static let full: JSON = [
        "id": "ap_7f3c", "created_at": "2026-11-19T06:00:00Z", "expires_at": "2026-11-19T06:15:00Z",
        "status": "executed", "summary": "BUY 10 TQQQ on alpaca-main — LIVE", "money_state": "live",
        "client": "jarvis", "method": "POST", "path": "/brokerages/b1/orders", "query": [:],
        "body": ["symbol": "TQQQ", "qty": 10, "side": "buy", "type": "market", "limit_price": nil],
        "body_sha256": .string(kVectorAHash), "result_status": 200, "result": ["order_id": "o1"],
        "decided_at": "2026-11-19T06:02:00Z", "device_id": "dev_1",
    ]

    @Test func decisionBodyEncodesTheContractKeysInOrder() throws {
        let body = ApprovalDecisionBody(decision: .approve, bodySha256: kVectorAHash, ts: 1_763_538_000, deviceId: "dev_1", signature: Data([1, 2, 3]))
        #expect(try body.json.dartEncoded() == #"{"decision":"approve","body_sha256":"15d67143989b33dd3b4423b0f683824b4394e9af80191b336c6d79c15cec3d5d","ts":1763538000,"device_id":"dev_1","signature":"AQID"}"#)
    }

    @Test func decodesEveryContractField() {
        let a = McpApproval(json: Self.full)
        #expect(a.id == "ap_7f3c")
        #expect(a.createdAt == Date(timeIntervalSince1970: 1_795_068_000))
        #expect(a.expiresAt == Date(timeIntervalSince1970: 1_795_068_900))
        #expect(a.status == "executed")
        #expect(a.summary == "BUY 10 TQQQ on alpaca-main — LIVE")
        #expect(a.moneyState == "live")
        #expect(a.client == "jarvis")
        #expect(a.method == "POST")
        #expect(a.path == "/brokerages/b1/orders")
        #expect(a.query == .object(JSONObject()))
        #expect(a.body == Self.full["body"])
        #expect(a.bodySha256 == kVectorAHash)
        #expect(a.resultStatus == 200)
        #expect(a.result["order_id"] == "o1")
        #expect(a.decidedAt == Date(timeIntervalSince1970: 1_795_068_120))
        #expect(a.deviceId == "dev_1")
        #expect(a.isTerminal && !a.isPending)
        #expect(ApprovalFingerprint.matches(a))
    }

    @Test func missingFieldsAreEmptyAndAMissingExpiryCountsAsExpired() {
        let a = McpApproval(json: ["id": "ap_1", "status": "pending"])
        #expect(a.isPending && !a.isTerminal)
        #expect(a.summary == "" && a.method == "" && a.bodySha256 == "")
        #expect(a.createdAt == nil && a.expiresAt == nil && a.decidedAt == nil)
        #expect(a.resultStatus == nil)
        #expect(a.deviceId == nil)
        #expect(a.query == .null && a.body == .null && a.result == .null)
        #expect(a.isExpired(at: Date(timeIntervalSince1970: 0)))
        #expect(McpApproval(json: ["id": "ap_1", "device_id": ""]).deviceId == nil)

        let full = McpApproval(json: Self.full)
        #expect(!full.isExpired(at: Date(timeIntervalSince1970: 1_795_068_899)))
        #expect(full.isExpired(at: Date(timeIntervalSince1970: 1_795_068_900)))
    }

    @Test func truncatedResultsAreFlagged() {
        #expect(McpApproval(json: ["id": "a", "result": ["truncated": true]]).resultTruncated)
        #expect(!McpApproval(json: Self.full).resultTruncated)
    }

    @Test func devicesReadDeviceIdOrIdAndActivation() {
        let registered: JSON = ["device_id": "dev_1", "name": "iPhone", "active_at": "2026-11-19T06:10:00Z"]
        let first = McpApprovalDevice(json: registered)
        let second = McpApprovalDevice(json: ["id": "dev_2"])
        #expect(first.id == "dev_1")
        #expect(first.name == "iPhone")
        #expect(second.id == "dev_2")
        #expect(!first.isActive(at: Date(timeIntervalSince1970: 1_795_068_599)))
        #expect(first.isActive(at: Date(timeIntervalSince1970: 1_795_068_600)))
        #expect(second.activeAt == nil)
        #expect(!second.isActive(at: .distantFuture))

        let receipt = ApprovalDeviceReceipt(json: registered)
        #expect(receipt.deviceId == "dev_1")
        #expect(receipt.activeAt == Date(timeIntervalSince1970: 1_795_068_600))
    }
}
