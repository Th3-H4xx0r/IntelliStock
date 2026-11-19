import Foundation
import Testing
@testable import IntelliStock

/// The approvals and approval-devices routes of contract §5, through
/// `DataStub`: exact method, path, query and body bytes, and the errors a
/// decision can end in.
struct ApprovalsRepositoryTests {
    private func encode(_ v: JSON) -> String { (try? v.dartEncoded()) ?? "" }

    @Test func decisionPostsTheExactContractBody() async throws {
        let stub = DataStub(json: encode(ApprovalWireTests.full))
        let body = ApprovalDecisionBody(decision: .deny, bodySha256: kVectorAHash, ts: 1_795_068_030, deviceId: "dev_1", signature: Data([0x30, 0x01]))
        let a = try await ApprovalsRepository(client: stub.client).decide("ap_7f3c", body)
        let r = try #require(stub.last)
        #expect(r.method == "POST" && r.path == "/mcp/approvals/ap_7f3c/decision" && r.queryPairs.isEmpty)
        #expect(r.value(forHTTPHeaderField: "Content-Type") == "application/json")
        #expect(r.bodyBytes == #"{"decision":"deny","body_sha256":"15d67143989b33dd3b4423b0f683824b4394e9af80191b336c6d79c15cec3d5d","ts":1795068030,"device_id":"dev_1","signature":"MAE="}"#)
        #expect(a.status == "executed")
    }

    @Test func registerDevicePostsNameAndBase64PublicKey() async throws {
        let pub = Data(base64Encoded: "BPr89QgQ9Ywp13lwYgOI0R9yI5AQggFqAh/0hDhGoqOuyhBMVLcMfHdUbK9OXP+OZk5Z0gntyHv1t6eZnKsrxVU=")!
        let stub = DataStub(status: 201, json: #"{"device_id": "dev_1", "active_at": "2026-11-19T06:10:00Z"}"#)
        let receipt = try await ApprovalsRepository(client: stub.client).registerDevice(name: "Pranav's iPhone", publicKey: pub)
        #expect(stub.last?.method == "POST" && stub.last?.path == "/mcp/approval-devices")
        #expect(stub.last?.bodyBytes == #"{"name":"Pranav's iPhone","public_key":"BPr89QgQ9Ywp13lwYgOI0R9yI5AQggFqAh/0hDhGoqOuyhBMVLcMfHdUbK9OXP+OZk5Z0gntyHv1t6eZnKsrxVU="}"#)
        #expect(receipt == ApprovalDeviceReceipt(json: ["device_id": "dev_1", "active_at": "2026-11-19T06:10:00Z"]))
    }

    /// The receipt's id becomes the stored device record and later goes into
    /// a DELETE path and every decision body, so a 2xx without a usable one
    /// is refused rather than saved.
    @Test func aRegistrationWithoutAUsableDeviceIdIsUnexpected() async {
        let stub = DataStub(status: 201, json: "{}")
        let repo = ApprovalsRepository(client: stub.client)
        await #expect(throws: kUnexpectedApprovalResponse) { _ = try await repo.registerDevice(name: "iPhone", publicKey: Data([4])) }
        stub.respond(status: 201, json: #"{"device_id": "../x", "active_at": "2026-11-19T06:10:00Z"}"#)
        await #expect(throws: kUnexpectedApprovalResponse) { _ = try await repo.registerDevice(name: "iPhone", publicKey: Data([4])) }
    }

    @Test func invalidIdsNeverReachTheNetwork() async {
        let stub = DataStub()
        let repo = ApprovalsRepository(client: stub.client)
        let body = ApprovalDecisionBody(decision: .approve, bodySha256: "x", ts: 1, deviceId: "d", signature: Data())
        await #expect(throws: kInvalidApprovalId) { _ = try await repo.approval("../brokerages") }
        await #expect(throws: kInvalidApprovalId) { _ = try await repo.decide("a/b", body) }
        await #expect(throws: kInvalidApprovalId) { try await repo.deleteDevice("") }
        #expect(stub.requests.isEmpty)
    }

    @Test func listAsksForAllWithLimit50() async throws {
        let stub = DataStub(json: encode(["approvals": [ApprovalWireTests.full, ["id": ""], "junk"]]))
        let rows = try await ApprovalsRepository(client: stub.client).approvals(status: .all, limit: ApprovalsRepository.listLimit)
        #expect(stub.last?.method == "GET" && stub.last?.path == "/mcp/approvals")
        #expect(stub.last?.queryItems == ["status": "all", "limit": "50"])
        #expect(rows.map(\.id) == ["ap_7f3c"])
    }

    @Test func pendingFilterSendsStatusPending() async throws {
        let stub = DataStub(json: #"{"approvals": []}"#)
        let rows = try await ApprovalsRepository(client: stub.client).approvals(status: .pending, limit: ApprovalsRepository.listLimit)
        #expect(stub.last?.queryItems["status"] == "pending")
        #expect(rows.isEmpty)
    }

    @Test func aBareArrayIsReadAsTheList() async throws {
        let stub = DataStub(json: encode([ApprovalWireTests.full, ["status": "pending"]]))
        let rows = try await ApprovalsRepository(client: stub.client).approvals(status: .all, limit: 50)
        #expect(rows.map(\.id) == ["ap_7f3c"])
    }

    @Test func detailGetsOneApproval() async throws {
        let stub = DataStub(json: encode(ApprovalWireTests.full))
        let a = try await ApprovalsRepository(client: stub.client).approval("ap_7f3c")
        #expect(stub.last?.method == "GET" && stub.last?.path == "/mcp/approvals/ap_7f3c")
        #expect(stub.last?.queryPairs.isEmpty == true)
        #expect(a.summary == "BUY 10 TQQQ on alpaca-main — LIVE")
    }

    @Test(arguments: [(400, "ts outside window"), (403, "bad signature"), (409, "not pending"), (410, "expired")])
    func decisionErrorsCarryTheirStatusAndDetail(status: Int, detail: String) async {
        let stub = DataStub(status: status, json: encode(["detail": .string(detail)]))
        let body = ApprovalDecisionBody(decision: .approve, bodySha256: kVectorAHash, ts: 1_795_068_030, deviceId: "dev_1", signature: Data([0x30, 0x01]))
        await #expect(throws: ApiError(message: detail, statusCode: status)) {
            _ = try await ApprovalsRepository(client: stub.client).decide("ap_7f3c", body)
        }
    }

    @Test func aSuccessWithoutAnIdIsUnexpected() async {
        let stub = DataStub(json: "{}")
        let repo = ApprovalsRepository(client: stub.client)
        let body = ApprovalDecisionBody(decision: .approve, bodySha256: kVectorAHash, ts: 1_795_068_030, deviceId: "dev_1", signature: Data([0x30, 0x01]))
        await #expect(throws: kUnexpectedApprovalResponse) { _ = try await repo.approval("ap_1") }
        await #expect(throws: kUnexpectedApprovalResponse) { _ = try await repo.decide("ap_1", body) }
        #expect(kUnexpectedApprovalResponse.statusCode == nil)
    }

    /// The row a 2xx carries must be the approval that was asked for: an
    /// answer about another one confirms nothing about this one.
    @Test func anAnswerForAnotherApprovalIsUnexpected() async {
        let stub = DataStub(json: encode(ApprovalWireTests.full))                // id "ap_7f3c"
        let repo = ApprovalsRepository(client: stub.client)
        let body = ApprovalDecisionBody(decision: .approve, bodySha256: kVectorAHash, ts: 1_795_068_030, deviceId: "dev_1", signature: Data([0x30, 0x01]))
        await #expect(throws: kUnexpectedApprovalResponse) { _ = try await repo.approval("ap_other") }
        await #expect(throws: kUnexpectedApprovalResponse) { _ = try await repo.decide("ap_other", body) }
        await #expect(throws: kUnexpectedApprovalResponse) { _ = try await repo.approval("AP_7F3C") }   // ids are exact
        #expect(stub.requests.map(\.path) == ["/mcp/approvals/ap_other", "/mcp/approvals/ap_other/decision", "/mcp/approvals/AP_7F3C"])
    }

    @Test func listsAndDeletesDevices() async throws {
        let stub = DataStub(json: #"{"devices": [{"device_id": "dev_1", "name": "iPhone"}, "junk", {"name": "no id"}]}"#)
        let repo = ApprovalsRepository(client: stub.client)
        let devices = try await repo.devices()
        #expect(stub.last?.method == "GET" && stub.last?.path == "/mcp/approval-devices")
        #expect(devices.map(\.id) == ["dev_1"])

        stub.respond(status: 204, json: "")
        try await repo.deleteDevice("dev_1")
        #expect(stub.last?.method == "DELETE" && stub.last?.path == "/mcp/approval-devices/dev_1")
        #expect(stub.requests.count == 2)
    }
}
