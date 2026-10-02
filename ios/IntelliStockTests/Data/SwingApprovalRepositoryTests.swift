import Foundation
import Testing
@testable import IntelliStock

/// The reads behind an approval's outcome (swing-approvals fix, 2026-10-02):
/// the decision's command id, the live command's status, the broker's claim
/// time on a returned signal, and the unfiltered signal list the wheel's
/// scan rows link to.
private func encode(_ value: JSON) -> String { (try? value.dartEncoded()) ?? "" }

struct SwingApprovalRepositoryTests {
    @Test func approveSendsExactlyTheDartBytesToTheDecisionRoute() async throws {
        let stub = DataStub(json: #"{"signal": {}, "command_id": "c1"}"#)
        _ = try await SwingRepository(client: stub.client).decide("swing-paper", "a29ea0d7", "approve")
        let request = try #require(stub.last)
        #expect(request.method == "POST")
        #expect(request.path == "/instances/swing-paper/swing/signals/a29ea0d7/decision")
        #expect(request.queryPairs.isEmpty)
        #expect(request.value(forHTTPHeaderField: "Content-Type") == "application/json")
        // Dart's jsonEncode({'decision': 'approve'}), byte for byte.
        #expect(request.bodyBytes == #"{"decision":"approve"}"#)
    }

    @Test func aRecordedDecisionCarriesItsCommandId() async throws {
        let stub = DataStub(json: #"{"signal": {"status": "approved"}, "command_id": "4130d0f5"}"#)
        let receipt = try await SwingRepository(client: stub.client).decide("i1", "a1", "approve")
        #expect(!receipt.uncertain)
        #expect(receipt.commandId == "4130d0f5")
        stub.respond(json: #"{"signal": {}, "command_id": null}"#)
        #expect(try await SwingRepository(client: stub.client).decide("i1", "a1", "reject").commandId == nil)
        stub.respond(json: "")
        #expect(try await SwingRepository(client: stub.client).decide("i1", "a1", "approve").commandId == nil)
        #expect(DecisionReceipt(json: ["command_id": ""]).commandId == nil)
    }

    @Test func commandStatusReadsTheLiveCommand() async throws {
        let stub = DataStub(json: encode([
            "id": "4130d0f5",
            "type": "submit_order",
            "status": "failed",
            "error": "order gate blocked: dependency.watchdog.unhealthy,dependency.watchdog.stale — approve again",
            "result": nil,
        ]))
        let status = try await SwingRepository(client: stub.client).commandStatus("4130d0f5")
        #expect(stub.last?.method == "GET")
        #expect(stub.last?.path == "/live-commands/4130d0f5")
        #expect(status.id == "4130d0f5")
        #expect(status.status == "failed")
        #expect(status.isTerminal)
        #expect(status.error == "order gate blocked: dependency.watchdog.unhealthy,dependency.watchdog.stale — approve again")

        stub.respond(json: #"{"id": "c2", "status": "running"}"#)
        let running = try await SwingRepository(client: stub.client).commandStatus("c2")
        #expect(!running.isTerminal)
        #expect(running.error == "")
        #expect(SwingCommandStatus(json: ["status": "completed"]).isTerminal)
        #expect(SwingCommandStatus(json: [:]).status == "pending")
    }

    @Test func recentSignalsReadsEveryStatusWithNoFilter() async throws {
        let stub = DataStub(json: encode(["signals": [
            ["id": "w1", "lane": "wheel", "symbol": "WDAY", "status": "rejected"],
            ["id": "w2", "lane": "wheel", "symbol": "QCOM", "status": "pending"],
            ["id": "", "status": "pending"],
            "junk",
        ]]))
        let rows = try await SwingRepository(client: stub.client).recentSignals("swing-paper")
        #expect(rows.map(\.id) == ["w1", "w2"])
        #expect(rows.map(\.status) == ["rejected", "pending"])
        #expect(stub.last?.method == "GET")
        #expect(stub.last?.path == "/instances/swing-paper/swing/signals")
        #expect(stub.last?.queryItems == ["limit": "200"])
    }

    @Test func aPendingSignalWithAClaimWasReturnedByTheBroker() throws {
        // QCOM on swing-paper, 2026-10-02: the broker claimed it at 18:39:55
        // and put it back to pending a second later.
        let returned = SwingSignal(json: [
            "id": "a29ea0d7", "lane": "wheel", "symbol": "QCOM", "status": "pending",
            "decided_at": nil, "claimed_at": "2026-10-02T18:39:55.796582+00:00",
            "context": ["stock_price": 188.67, "otm_pct": 2.4],
        ])
        let claimed = try #require(returned.claimedAt)
        #expect(abs(claimed.timeIntervalSince(swingUTC(2026, 10, 2, 18, 39, 55)) - 0.796582) < 0.001)
        #expect(returned.returnedByBroker)
        #expect(returned.stockPrice == 188.67)
        #expect(returned.otmPct == 2.4)

        let fresh = SwingSignal(json: ["id": "x", "status": "pending"])
        #expect(fresh.claimedAt == nil)
        #expect(!fresh.returnedByBroker)
        #expect(fresh.stockPrice == nil)
        let failed = SwingSignal(json: ["id": "u", "status": "failed", "claimed_at": "2026-10-01T15:11:26+00:00"])
        #expect(!failed.returnedByBroker)
    }
}

nonisolated extension URLRequest {
    /// The raw body as UTF-8 text (streamed bodies included).
    var bodyBytes: String {
        guard let httpBody else { return "" }
        return String(decoding: httpBody, as: UTF8.self)
    }
}
