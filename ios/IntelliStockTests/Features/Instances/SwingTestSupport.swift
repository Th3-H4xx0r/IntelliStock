import Foundation
@testable import IntelliStock

// Ported from test/features/swing/swing_fakes.dart.

/// A one-shot async gate — Dart's `Completer<void>` as the fakes used it.
nonisolated final class SwingTestGate: @unchecked Sendable {
    private let lock = NSLock()
    private var isOpen = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
            let resumeNow = lock.withLock { () -> Bool in
                if isOpen { return true }
                waiters.append(c)
                return false
            }
            if resumeNow { c.resume() }
        }
    }

    func open() {
        let pending = lock.withLock { () -> [CheckedContinuation<Void, Never>] in
            isOpen = true
            defer { waiters.removeAll() }
            return waiters
        }
        for c in pending { c.resume() }
    }
}

/// Test double for `SwingRepository` (`FakeSwingRepo`).
nonisolated class SwingFakeSource: SwingSignalsSource, @unchecked Sendable {
    var pending: [SwingSignal]
    /// What `?status=approved` and `?status=approved_half` answer.
    var approved: [SwingSignal]
    var wheelSnapshot: WheelSnapshot
    var decideCalls: [String] = []
    var resendCalls: [String] = []
    var decideError: (any Error)?
    var resendError: (any Error)?
    var resendReceipt = DecisionReceipt.recorded
    var listError: (any Error)?
    var wheelError: (any Error)?
    /// When set, decide() and resend() wait on it.
    var gate: SwingTestGate?
    /// Fails only the pending list (follow-up 4); `listError` fails both.
    var pendingError: (any Error)?
    /// What a 2xx decision answers; FW-api-I1's 202 is `uncertain`.
    var decideReceipt = DecisionReceipt.recorded
    /// What `?status=submitted` and `?status=failed` answer.
    var submitted: [SwingSignal] = []
    var failed: [SwingSignal] = []
    var statusReads: [String] = []
    /// Fails only the approved lists; `listError` fails both.
    var approvedError: (any Error)?
    var wheelCalls = 0

    init(_ pending: [SwingSignal], wheelSnapshot: WheelSnapshot = .empty, approved: [SwingSignal] = []) {
        self.pending = pending
        self.approved = approved
        self.wheelSnapshot = wheelSnapshot
    }

    func pendingSignals(_ instanceId: String) async throws -> [SwingSignal] {
        if let pendingError { throw pendingError }
        if let listError { throw listError }
        return pending
    }

    func decide(_ instanceId: String, _ signalId: String, _ decision: String, reason: String?) async throws -> DecisionReceipt {
        decideCalls.append("\(signalId):\(decision)")
        if let gate { await gate.wait() }
        if let decideError { throw decideError }
        return decideReceipt
    }

    /// The submitted and failed reads run in parallel: guard the log.
    private let statusLock = NSLock()

    func signalsWithStatus(_ instanceId: String, _ status: String) async throws -> [SwingSignal] {
        statusLock.withLock { statusReads.append(status) }
        if let listError { throw listError }
        switch status {
        case "submitted": return submitted
        case "failed": return failed
        case "pending": return pending
        default: return approved.filter { $0.status == status }
        }
    }

    func approvedSignals(_ instanceId: String) async throws -> [SwingSignal] {
        if let approvedError { throw approvedError }
        if let listError { throw listError }
        return approved
    }

    func resend(_ instanceId: String, _ signalId: String) async throws -> DecisionReceipt {
        resendCalls.append(signalId)
        if let gate { await gate.wait() }
        if let resendError { throw resendError }
        return resendReceipt
    }

    func wheel(_ instanceId: String) async throws -> WheelSnapshot {
        wheelCalls += 1
        if let wheelError { throw wheelError }
        return wheelSnapshot
    }
}

/// Holds every pendingSignals() call on `listGate` and counts the calls.
/// `nextListGate`, when set, holds only the next poll: its pending list
/// answers with the rows as they were when it began, and its approved list
/// with the rows as they are when the gate opens (`_GatedListRepo`).
nonisolated final class SwingGatedSource: SwingFakeSource, @unchecked Sendable {
    let listGate = SwingTestGate()
    var nextListGate: SwingTestGate?
    private var approvedHold: SwingTestGate?
    private var pendingStarted = 0
    private var approvedStarted = 0
    var listCalls = 0

    override func pendingSignals(_ instanceId: String) async throws -> [SwingSignal] {
        let snapshot = try await super.pendingSignals(instanceId)
        let hold = nextListGate
        nextListGate = nil
        approvedHold = hold
        pendingStarted += 1
        listCalls += 1
        await listGate.wait()
        if let hold { await hold.wait() }
        return snapshot
    }

    override func approvedSignals(_ instanceId: String) async throws -> [SwingSignal] {
        approvedStarted += 1
        let mine = approvedStarted
        // After pendingSignals began (Dart's Future.delayed(Duration.zero)).
        while pendingStarted < mine { await Task.yield() }
        let hold = approvedHold
        approvedHold = nil
        if let hold { await hold.wait() }
        return try await super.approvedSignals(instanceId)
    }
}

/// A clock the tests move.
final class SwingTestClock {
    var now: Date

    init(_ now: Date) {
        self.now = now
    }
}

func swingUTC(_ y: Int, _ mo: Int, _ d: Int, _ h: Int = 0, _ mi: Int = 0, _ s: Int = 0) -> Date {
    var c = Calendar(identifier: .gregorian)
    c.timeZone = TimeZone(secondsFromGMT: 0)!
    return c.date(from: DateComponents(year: y, month: mo, day: d, hour: h, minute: mi, second: s))!
}

func swingTestSignal(
    _ id: String,
    symbol: String = "AAPL",
    createdAt: String = "2026-09-24T13:15:00Z",
    reasoning: String = "Pullback to the 50-day in an uptrend."
) -> SwingSignal {
    SwingSignal(json: [
        "id": .string(id),
        "lane": "swing",
        "symbol": .string(symbol),
        "session": "2026-09-24",
        "created_at": .string(createdAt),
        "score": 62,
        "recommendation": "REVIEW",
        "reasoning": .string(reasoning),
        "key_risks": ["earnings in 9 days"],
        "proposal": ["entry": 200.0, "stop": 188.0, "target": 218.0, "shares": 6],
        "status": "pending",
    ])
}

/// A swing signal that reads approved (or approved_half) since `decidedAt`.
func approvedTestSignal(
    _ id: String,
    _ decidedAt: String,
    status: String = "approved",
    symbol: String = "AAPL",
    session: String = "2026-09-25"
) -> SwingSignal {
    SwingSignal(json: [
        "id": .string(id),
        "lane": "swing",
        "symbol": .string(symbol),
        "session": .string(session),
        "created_at": "2026-09-25T13:15:00Z",
        "score": 62,
        "recommendation": "REVIEW",
        "reasoning": "r",
        "key_risks": [],
        "proposal": ["entry": 200.0, "stop": 188.0, "target": 218.0, "shares": 6],
        "status": .string(status),
        "decided_by": "pranav",
        "decided_at": .string(decidedAt),
    ])
}

/// `s` as the server would list it with `status` (and the order key the
/// broker writes once it sent the order).
func withTestStatus(_ s: SwingSignal, _ status: String, orderClientId: String? = nil) -> SwingSignal {
    var json: JSONObject = [
        "id": .string(s.id),
        "lane": .string(s.lane),
        "symbol": .string(s.symbol),
        "session": .string(s.session),
        "created_at": .string(s.createdAt),
        "score": JSON(s.score),
        "recommendation": .string(s.recommendation),
        "reasoning": .string(s.reasoning),
        "key_risks": .array(s.keyRisks.map(JSON.string)),
        "proposal": .object(s.proposal),
        "status": .string(status),
    ]
    if let at = s.decidedAt { json["decided_at"] = .string(ISO8601DateFormatter().string(from: at)) }
    if let orderClientId { json["order_client_id"] = .string(orderClientId) }
    return SwingSignal(json: .object(json))
}

func wheelTestSignal(_ id: String) -> SwingSignal {
    SwingSignal(json: [
        "id": .string(id),
        "lane": "wheel",
        "symbol": "APH",
        "session": "2026-09-21",
        "created_at": "2026-09-21T14:30:00Z",
        "score": 55,
        "recommendation": "REVIEW",
        "reasoning": "IV rank is high.",
        "key_risks": [],
        "proposal": [
            "contract": "APH261002P00130000",
            "strike": 130,
            "expiry": "2026-10-02",
            "qty": 1,
            "limit_price": 1.23,
            "premium_est": 1.3,
        ],
        "status": "pending",
    ])
}
