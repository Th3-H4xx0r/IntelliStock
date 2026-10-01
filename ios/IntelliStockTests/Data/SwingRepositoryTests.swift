import Foundation
import Testing
@testable import IntelliStock

/// Ported from test/features/swing/swing_repository_test.dart.

private func swingJson(id: String = "a1", status: String = "pending", createdAt: String = "2026-09-24T13:15:02Z") -> [String: JSON] {
    [
        "id": .string(id),
        "instance_id": "swing-paper",
        "lane": "swing",
        "symbol": "AAPL",
        "session": "2026-09-24",
        "created_at": .string(createdAt),
        "score": 62,
        "recommendation": "REVIEW",
        "reasoning": "Pullback to the 50-day in an uptrend.",
        "key_risks": ["earnings in 9 days", "", "  sector rotation  "],
        "size_adjustment": 1.0,
        "proposal": ["entry": 200.0, "stop": 188.0, "target": 218.0, "shares": 6],
        "status": .string(status),
    ]
}

private let wheelJson: [String: JSON] = [
    "id": "w1",
    "lane": "wheel",
    "symbol": "APH",
    "session": "2026-09-21",
    "created_at": "2026-09-21T14:30:00Z",
    "score": 55,
    "recommendation": "REVIEW",
    "reasoning": "IV rank is high.",
    "key_risks": [],
    "size_adjustment": 1.0,
    "proposal": [
        "contract": "APH261002P00130000",
        "strike": 130,
        "expiry": "2026-10-02",
        "qty": 1,
        "limit_price": 1.23,
        "premium_est": 1.3,
        "delta": -0.24,
    ],
    "status": "pending",
]

private func encode(_ value: JSON) -> String { value.dartEncoded() }

struct SwingSignalFromJsonTests {
    @Test func swingProposalFieldsAndRisks() {
        let s = SwingSignal(json: .object(swingJson()))
        #expect(!s.isWheel)
        #expect(s.allowsHalf)
        #expect(s.entry == 200.0)
        #expect(s.stop == 188.0)
        #expect(s.target == 218.0)
        #expect(s.shares == 6)
        #expect(s.keyRisksText == "earnings in 9 days · sector rotation")
    }

    @Test func wheelProposalFieldsCreditAndCollateral() {
        let s = SwingSignal(json: .object(wheelJson))
        #expect(s.isWheel)
        #expect(!s.allowsHalf)
        #expect(s.contract == "APH261002P00130000")
        #expect(s.strike == 130.0)
        #expect(s.qty == 1)
        #expect(s.limitPrice == 1.23)
        #expect(close(s.creditEst, 130.0))
        #expect(s.collateral == 13000.0)
    }

    @Test func missingFieldsDoNotThrow() {
        let s = SwingSignal(json: ["id": "x"])
        #expect(s.lane == "swing")
        #expect(s.status == "pending")
        #expect(s.score == nil)
        #expect(s.entry == nil)
        #expect(s.creditEst == nil)
        #expect(s.keyRisksText == "")
    }

    @Test func orderClientIdIsReadAbsentOrEmptyIsNil() {
        #expect(SwingSignal(json: ["id": "x", "order_client_id": "k-0"]).orderClientId == "k-0")
        #expect(SwingSignal(json: ["id": "x", "order_client_id": ""]).orderClientId == nil)
        #expect(SwingSignal(json: ["id": "x"]).orderClientId == nil)
    }
}

struct SwingRepositoryTests {
    @Test func pendingSignalsGetsWithStatusPendingKeepsPendingOnlyNewestFirst() async throws {
        let stub = DataStub(json: encode([
            "signals": [
                .object(swingJson(id: "old", createdAt: "2026-09-23T13:15:00Z")),
                .object(swingJson(id: "done", status: "approved")),
                .object(swingJson(id: "new", createdAt: "2026-09-24T13:15:00Z")),
                "junk",
            ],
        ]))
        let rows = try await SwingRepository(client: stub.client).pendingSignals("swing-paper")
        #expect(rows.map(\.id) == ["new", "old"])
        #expect(stub.requests.count == 1)
        #expect(stub.last?.method == "GET")
        #expect(stub.last?.path == "/instances/swing-paper/swing/signals")
        #expect(stub.last?.queryItems == ["status": "pending"])
    }

    @Test func pendingSignalsAlsoAcceptsABareList() async throws {
        let stub = DataStub(json: encode([.object(wheelJson)]))
        let rows = try await SwingRepository(client: stub.client).pendingSignals("i1")
        #expect(rows.map(\.id) == ["w1"])
    }

    @Test func decidePostsTheDecisionAndTheReasonOnlyWhenGiven() async throws {
        let stub = DataStub(json: "")
        let repo = SwingRepository(client: stub.client)
        _ = try await repo.decide("i1", "a1", "approve_half")
        _ = try await repo.decide("i1", "a1", "reject", reason: "  too close to earnings ")
        _ = try await repo.decide("i1", "a1", "reject", reason: "   ")
        let calls = stub.requests
        #expect(calls.count == 3)
        #expect(calls[0].method == "POST")
        #expect(calls[0].path == "/instances/i1/swing/signals/a1/decision")
        #expect(calls[0].jsonBody == ["decision": "approve_half"])
        #expect(calls[1].jsonBody == ["decision": "reject", "reason": "too close to earnings"])
        #expect(calls[2].jsonBody == ["decision": "reject"])
    }

    @Test func decideReadsTheUncertain202BodyAnyOtherBodyIsRecorded() async throws {
        let stub = DataStub(json: "")
        let repo = SwingRepository(client: stub.client)
        #expect(try await repo.decide("i1", "a1", "approve").uncertain == false)
        stub.respond(json: #"{"signal": {}, "command_id": "c1"}"#)
        #expect(try await repo.decide("i1", "a1", "approve").uncertain == false)
        stub.respond(json: encode([
            "signal": [:],
            "command_id": nil,
            "uncertain": true,
            "detail": "  approval received — the order may be in flight  ",
        ]))
        let r = try await repo.decide("i1", "a1", "approve")
        #expect(r.uncertain)
        #expect(r.detail == "approval received — the order may be in flight")
    }

    @Test func approvedSignalsReadsApprovedAndApprovedHalfApprovedRowsOnly() async throws {
        var approvedA1 = swingJson(id: "a1", status: "approved")
        approvedA1["decided_at"] = "2026-09-25T13:20:00+00:00"
        var halfH1 = swingJson(id: "h1", status: "approved_half")
        halfH1["decided_at"] = "2026-09-25T13:25:00+00:00"
        let byStatus: [String: String] = [
            "approved": encode(["signals": [.object(approvedA1), .object(swingJson(id: "p1"))]]),
            "approved_half": encode([.object(halfH1)]),
        ]
        let stub = DataStub()
        stub.handler = { request in (200, byStatus[request.queryItems["status"] ?? ""] ?? "null") }

        let rows = try await SwingRepository(client: stub.client).approvedSignals("swing-paper")
        #expect(rows.map(\.id) == ["h1", "a1"])
        let expected = DateComponents(calendar: Calendar(identifier: .gregorian), timeZone: TimeZone(secondsFromGMT: 0),
                                      year: 2026, month: 9, day: 25, hour: 13, minute: 25).date
        #expect(rows.first?.decidedAt == expected)
        // Dart's Future.wait issued both GETs in order; here they run
        // concurrently, so the order of arrival is not fixed.
        #expect(Set(stub.requests.map { $0.queryItems["status"] ?? "" }) == ["approved", "approved_half"])
        #expect(stub.requests.count == 2)
        #expect(SwingSignal(json: ["id": "x"]).decidedAt == nil)
    }

    @Test func resendPostsToResendWithNoBodyAndReadsTheReceipt() async throws {
        let stub = DataStub(json: #"{"signal": {}, "command_id": "c1"}"#)
        let repo = SwingRepository(client: stub.client)
        #expect(try await repo.resend("i1", "a1").uncertain == false)
        #expect(stub.last?.method == "POST")
        #expect(stub.last?.path == "/instances/i1/swing/signals/a1/resend")
        #expect(stub.last?.jsonBody == .null)
        stub.respond(json: #"{"uncertain": true, "detail": "re-send received — x"}"#)
        #expect(try await repo.resend("i1", "a1").detail == "re-send received — x")
    }

    @Test func wheelParsesTheAddendumShapeAndToleratesNulls() async throws {
        let stub = DataStub(json: encode([
            "open_puts": [
                [
                    "contract": "APH261002P00130000",
                    "underlying": "APH",
                    "strike": 130,
                    "expiry": "2026-10-02",
                    "qty": 1,
                    "avg_entry_price": 1.23,
                    "current_price": nil,
                    "underlying_price": 127.4,
                    "itm_pct": 2.0,
                    "dte": 8,
                    "collateral": 13000,
                    "unrealized_pl": nil,
                ],
            ],
            "collateral_total": 13000,
            "cash": 25000,
            "recent_scans": [
                ["id": "s1", "session": "2026-09-21", "symbol": "APH", "strike": 130,
                 "expiry": "2026-10-02", "score": 55, "status": "pending", "skip_reason": nil],
            ],
        ]))
        let before = Date()
        let w = try await SwingRepository(client: stub.client).wheel("i1")
        #expect(stub.last?.path == "/instances/i1/wheel")
        // FW item 4 (M-3): the snapshot carries when it was fetched.
        let fetchedAt = try #require(w.fetchedAt)
        #expect(fetchedAt >= before)
        #expect(w.openPuts.count == 1)
        #expect(w.openPuts.first?.currentPrice == nil)
        #expect(w.openPuts.first?.monitorWillBuyBack == false)
        #expect(w.collateralTotal == 13000.0)
        #expect(w.recentScans.first?.skipReason == "")
    }

    @Test func wheelPutMonitorWillBuyBackMirrorsThe1545MonitorRules() {
        func put(_ itm: Double?, _ dte: Int?) -> WheelPut {
            WheelPut(contract: "c", underlying: "u", expiry: "", itmPct: itm, dte: dte)
        }
        #expect(put(10, 20).monitorWillBuyBack)
        #expect(put(5, 2).monitorWillBuyBack)
        #expect(put(0.4, 0).monitorWillBuyBack)
        #expect(!put(5, 3).monitorWillBuyBack)
        #expect(!put(-3, 0).monitorWillBuyBack)
        #expect(!put(nil, 0).monitorWillBuyBack)
    }
}
