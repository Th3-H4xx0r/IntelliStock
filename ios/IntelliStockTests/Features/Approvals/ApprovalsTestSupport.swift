import Foundation
@testable import IntelliStock

// Shared fakes and fixtures for the approvals suites (Tasks 4 to 7 of
// docs/superpowers/plans/2026-11-19-mcp-ios-approvals.md). Each fake guards
// its state with a lock and never holds it across an await.

/// 2026-11-19T06:00:00Z: `ApprovalWireTests.full` was created at this instant.
nonisolated let kT0 = Date(timeIntervalSince1970: 1_795_068_000)

/// A device the server has accepted signatures from for an hour at `kT0`.
nonisolated let kActiveDevice = ApprovalDeviceRecord(baseURL: "https://api.test", deviceId: "dev_1", name: "iPhone", activeAt: kT0 - 3600)

/// `ApprovalWireTests.full` as a request still waiting: no result, no
/// decision, no deciding device. The id stays `ap_7f3c` and the summary
/// "BUY 10 TQQQ on alpaca-main — LIVE"; `result_status` is dropped when nil.
func approvalA(status: String = "pending", expiresAt: Date = kT0 + 900, bodySha256: String = kVectorAHash,
               resultStatus: Int? = nil) -> McpApproval {
    var fields = ApprovalWireTests.full.objectValue
    for key in ["result", "decided_at", "device_id", "result_status"] { fields.removeValue(forKey: key) }
    fields["status"] = .string(status)
    fields["expires_at"] = .string(expiresAt.formatted(.iso8601))
    fields["body_sha256"] = .string(bodySha256)
    if let resultStatus { fields["result_status"] = .int(resultStatus) }
    return McpApproval(json: .object(fields))
}

/// A settable wall clock; a model reads it through its `clock` closure.
nonisolated final class ApprovalTestClock: @unchecked Sendable {
    private let lock = NSLock()
    private var _now: Date

    init(_ start: Date) { _now = start }

    var now: Date {
        get { lock.withLock { _now } }
        set { lock.withLock { _now = newValue } }
    }

    func advance(by seconds: TimeInterval) {
        lock.withLock { _now += seconds }
    }
}

/// Holds whoever calls `wait()` until `open()`; after that, `wait()` returns at once.
nonisolated final class ApprovalTestGate: @unchecked Sendable {
    private let lock = NSLock()
    private var isOpen = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    init() {}

    func wait() async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            let resumeNow = lock.withLock { () -> Bool in
                if isOpen { return true }
                waiters.append(continuation)
                return false
            }
            if resumeNow { continuation.resume() }
        }
    }

    func open() {
        let waiting = lock.withLock { () -> [CheckedContinuation<Void, Never>] in
            isOpen = true
            defer { waiters = [] }
            return waiters
        }
        for continuation in waiting { continuation.resume() }
    }
}

/// Scripted approvals and approval-devices routes that record every call.
nonisolated final class FakeApprovalsSource: ApprovalsSource, ApprovalDevicesSource, @unchecked Sendable {
    private let lock = NSLock()

    private var _rows: [McpApproval]
    private var _details: [McpApproval]
    private var _listError: (any Error)?
    private var _detailError: (any Error)?
    private var _decideResult: McpApproval?
    private var _decideError: (any Error)?
    private var _decideGate: ApprovalTestGate?
    private var _detailGate: ApprovalTestGate?
    private var _receipt = ApprovalDeviceReceipt(json: ["device_id": "dev_1"])
    private var _registerError: (any Error)?
    private var _devicesList: [McpApprovalDevice] = []
    private var _devicesError: (any Error)?
    private var _deleteError: (any Error)?

    private var _listCalls: [(ApprovalListFilter, Int)] = []
    private var _approvalCalls: [String] = []
    private var _decideCalls: [(id: String, body: ApprovalDecisionBody)] = []
    private var _registerCalls: [(name: String, publicKey: Data)] = []
    private var _deleteCalls: [String] = []

    init(details: [McpApproval] = [], rows: [McpApproval] = []) {
        _details = details
        _rows = rows
    }

    // MARK: Script

    /// What `approvals(status:limit:)` returns.
    var rows: [McpApproval] { get { locked { _rows } } set { locked { _rows = newValue } } }
    /// Each `approval(_:)` call takes the next one; the last one repeats.
    var details: [McpApproval] { get { locked { _details } } set { locked { _details = newValue } } }
    var listError: (any Error)? { get { locked { _listError } } set { locked { _listError = newValue } } }
    var detailError: (any Error)? { get { locked { _detailError } } set { locked { _detailError = newValue } } }
    var decideResult: McpApproval? { get { locked { _decideResult } } set { locked { _decideResult = newValue } } }
    var decideError: (any Error)? { get { locked { _decideError } } set { locked { _decideError = newValue } } }
    /// When set, `decide` records its call, then waits for the gate to open.
    var decideGate: ApprovalTestGate? { get { locked { _decideGate } } set { locked { _decideGate = newValue } } }
    /// When set, `approval(_:)` records its call and takes its answer, then
    /// waits for the gate to open before returning it.
    var detailGate: ApprovalTestGate? { get { locked { _detailGate } } set { locked { _detailGate = newValue } } }
    var receipt: ApprovalDeviceReceipt { get { locked { _receipt } } set { locked { _receipt = newValue } } }
    var registerError: (any Error)? { get { locked { _registerError } } set { locked { _registerError = newValue } } }
    var devicesList: [McpApprovalDevice] { get { locked { _devicesList } } set { locked { _devicesList = newValue } } }
    var devicesError: (any Error)? { get { locked { _devicesError } } set { locked { _devicesError = newValue } } }
    var deleteError: (any Error)? { get { locked { _deleteError } } set { locked { _deleteError = newValue } } }

    // MARK: Records

    var listCalls: [(ApprovalListFilter, Int)] { locked { _listCalls } }
    var approvalCalls: [String] { locked { _approvalCalls } }
    var decideCalls: [(id: String, body: ApprovalDecisionBody)] { locked { _decideCalls } }
    var registerCalls: [(name: String, publicKey: Data)] { locked { _registerCalls } }
    var deleteCalls: [String] { locked { _deleteCalls } }

    // MARK: ApprovalsSource

    func approvals(status: ApprovalListFilter, limit: Int) async throws -> [McpApproval] {
        try locked {
            _listCalls.append((status, limit))
            if let _listError { throw _listError }
            return _rows
        }
    }

    func approval(_ id: String) async throws -> McpApproval {
        let (gate, answer) = locked { () -> (ApprovalTestGate?, Result<McpApproval, any Error>) in
            _approvalCalls.append(id)
            if let _detailError { return (_detailGate, .failure(_detailError)) }
            guard let next = _details.first else {
                return (_detailGate, .failure(ApiError(message: "No approval scripted.", statusCode: 404)))
            }
            if _details.count > 1 { _details.removeFirst() }
            return (_detailGate, .success(next))
        }
        if let gate { await gate.wait() }
        return try answer.get()
    }

    func decide(_ id: String, _ body: ApprovalDecisionBody) async throws -> McpApproval {
        let gate = locked { () -> ApprovalTestGate? in
            _decideCalls.append((id: id, body: body))
            return _decideGate
        }
        if let gate { await gate.wait() }
        return try locked {
            if let _decideError { throw _decideError }
            guard let _decideResult else { throw ApiError(message: "No decision scripted.", statusCode: 500) }
            return _decideResult
        }
    }

    // MARK: ApprovalDevicesSource

    func registerDevice(name: String, publicKey: Data) async throws -> ApprovalDeviceReceipt {
        try locked {
            _registerCalls.append((name: name, publicKey: publicKey))
            if let _registerError { throw _registerError }
            return _receipt
        }
    }

    func devices() async throws -> [McpApprovalDevice] {
        try locked {
            if let _devicesError { throw _devicesError }
            return _devicesList
        }
    }

    func deleteDevice(_ deviceId: String) async throws {
        try locked {
            _deleteCalls.append(deviceId)
            if let _deleteError { throw _deleteError }
        }
    }

    private func locked<T>(_ body: () throws -> T) rethrows -> T {
        try lock.withLock(body)
    }
}

/// A signer with a fixed signature that records each Face ID reason and each
/// signed message. No Secure Enclave, no prompt.
nonisolated final class FakeApprovalSigner: ApprovalSigner, @unchecked Sendable {
    /// A minimal DER SEQUENCE, so a test can tell it went into the body.
    let signature = Data([0x30, 0x03, 0x02, 0x01, 0x00])
    /// What `createKey` returns: `0x04` then 64 × `0x01`.
    static let createdKey = Data([0x04] + [UInt8](repeating: 0x01, count: 64))

    private let lock = NSLock()
    private var _authorizeError: ApprovalSignerError?
    private var _signError: ApprovalSignerError?
    private var _onAuthorize: (@Sendable () -> Void)?
    private var _reasons: [String] = []
    private var _messages: [Data] = []
    private var _key: Data?

    init() {}

    /// Thrown by `authorize`, after the reason is recorded (Face ID shown, then refused).
    var authorizeError: ApprovalSignerError? {
        get { lock.withLock { _authorizeError } }
        set { lock.withLock { _authorizeError = newValue } }
    }

    /// Thrown by the grant's `sign`; nothing is recorded then.
    var signError: ApprovalSignerError? {
        get { lock.withLock { _signError } }
        set { lock.withLock { _signError = newValue } }
    }

    /// Runs inside `authorize`, so a test can move the clock as if Face ID took time.
    var onAuthorize: (@Sendable () -> Void)? {
        get { lock.withLock { _onAuthorize } }
        set { lock.withLock { _onAuthorize = newValue } }
    }

    var reasons: [String] { lock.withLock { _reasons } }
    var messages: [Data] { lock.withLock { _messages } }

    var isHardwareBacked: Bool { true }

    func publicKey() -> Data? { lock.withLock { _key } }

    func createKey() throws -> Data {
        lock.withLock { _key = Self.createdKey }
        return Self.createdKey
    }

    func deleteKey() {
        lock.withLock { _key = nil }
    }

    func authorize(reason: String) async throws -> any ApprovalSigningGrant {
        let hook = lock.withLock { () -> (@Sendable () -> Void)? in
            _reasons.append(reason)
            return _onAuthorize
        }
        hook?()
        if let error = authorizeError { throw error }
        return Grant(signer: self)
    }

    fileprivate func sign(_ message: Data) throws -> Data {
        try lock.withLock {
            if let _signError { throw _signError }
            _messages.append(message)
            return signature
        }
    }

    private struct Grant: ApprovalSigningGrant {
        let signer: FakeApprovalSigner
        func sign(_ message: Data) throws -> Data { try signer.sign(message) }
    }
}
