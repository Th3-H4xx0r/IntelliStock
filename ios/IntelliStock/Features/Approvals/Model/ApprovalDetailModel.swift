import Foundation
import Observation

/// Where a decision is: Face ID up, the POST in flight, or re-reading the
/// approval after an answer the app couldn't trust.
nonisolated enum ApprovalDecisionPhase: Hashable, Sendable {
    case idle
    case authorizing(ApprovalDecision)
    case sending(ApprovalDecision)
    case checking
}

nonisolated enum ApprovalDecisionOutcome: Hashable, Sendable {
    /// 200: the state is the returned approval.
    case decided
    /// Face ID was dismissed; nothing was sent and the message is "".
    case cancelled
    /// Refused on the phone (gate, signer error, expiry during Face ID); nothing was sent.
    case blocked
    /// 400 or 403: still pending; the message is the server's reason.
    case rejected
    /// 409 or 410: re-read, so the state shows the real status.
    case notPending
    /// Transport error, 5xx or an unexpected body: re-read; the message is `kApprovalUncertain`.
    case uncertain
    /// A second tap while a decision runs.
    case ignored
}

nonisolated struct ApprovalDecisionResult: Hashable, Sendable {
    let outcome: ApprovalDecisionOutcome
    let message: String
}

/// One approval's detail screen: the approval, polled at a cadence that
/// follows its status, and the decision.
///
/// A decision runs Face ID, takes its timestamp afterwards (the server
/// accepts |now - ts| <= 60), signs the contract message over the server's
/// `body_sha256`, and sends it once. It is never retried: after any answer
/// the app can't read, it re-reads the approval and shows where it stands,
/// and the operator decides again with a fresh signature if it is still
/// pending.
@Observable
final class ApprovalDetailModel {
    static let pendingPoll: Duration = .seconds(10)
    /// Status "approved" (the server is executing the call), or phase `.checking`.
    static let runningPoll: Duration = .seconds(3)
    static let settledPoll: Duration = .seconds(60)
    /// Summary characters the Face ID reason carries before it is cut.
    static let reasonSummaryLimit = 150

    let approvalId: String
    private(set) var state: Loadable<McpApproval> = .loading
    private(set) var phase: ApprovalDecisionPhase = .idle
    /// The loaded approval's fields hash to its `body_sha256`.
    private(set) var fingerprintMatches = false
    /// The last refresh's failure while an older approval stays on screen.
    private(set) var refreshError: String?

    @ObservationIgnored private let source: () -> any ApprovalsSource
    @ObservationIgnored private let signer: any ApprovalSigner
    @ObservationIgnored private let devices: ApprovalDeviceStore
    @ObservationIgnored private let baseURL: () -> String
    @ObservationIgnored private let clock: () -> Date

    init(approvalId: String, source: @escaping () -> any ApprovalsSource, signer: any ApprovalSigner,
         devices: ApprovalDeviceStore, baseURL: @escaping () -> String, clock: @escaping () -> Date = Date.init) {
        self.approvalId = approvalId
        self.source = source
        self.signer = signer
        self.devices = devices
        self.baseURL = baseURL
        self.clock = clock
    }

    /// This iPhone's device record for the current server.
    var device: ApprovalDeviceRecord? { devices.record(for: baseURL()) }

    var isBusy: Bool { phase != .idle }

    var pollInterval: Duration {
        if phase == .checking || state.value?.status == "approved" { return Self.runningPoll }
        if state.value?.isPending == true { return Self.pendingPoll }
        return Self.settledPoll
    }

    /// `closed("")` until an approval is loaded.
    func gate(now: Date) -> ApprovalGate {
        guard let approval = state.value else { return .closed("") }
        return approvalGate(approval, now: now, device: device, fingerprintMatches: fingerprintMatches)
    }

    // MARK: Loading

    /// An invalid id fails without a request.
    func load() async {
        state = await Loadable.capture { try await fetch() }
        fingerprintMatches = state.value.map(ApprovalFingerprint.matches) ?? false
        refreshError = nil
    }

    /// Like `load`, but a failure keeps the last approval and sets
    /// `refreshError`; a cancelled refresh changes nothing.
    func refresh() async {
        do {
            show(try await fetch())
        } catch where error.isCancellation {
            return
        } catch {
            if state.value == nil {
                state = .failed(error)
            } else {
                refreshError = Self.text(error)
            }
        }
    }

    /// Loads if nothing is loaded, then refreshes every `pollInterval`
    /// until the calling task is cancelled, pausing in the background.
    func poll(lifecycle: AppLifecycle?, sleep: @escaping PollingSleep = realPollingSleep) async {
        if state.value == nil { await load() }
        guard !Task.isCancelled else { return }
        await PollingLoop(interval: { [weak self] in self?.pollInterval ?? Self.settledPoll }, sleep: sleep) { [weak self] in
            await self?.refresh()
        }
        .run(lifecycle: lifecycle)
    }

    // MARK: Deciding

    func decide(_ decision: ApprovalDecision) async -> ApprovalDecisionResult {
        guard phase == .idle else { return Self.result(.ignored) }
        guard let approval = state.value else { return Self.result(.blocked) }
        let device = device
        let gate = approvalGate(approval, now: clock(), device: device, fingerprintMatches: fingerprintMatches)
        guard let device, decision == .approve ? gate.canApprove : gate.canDeny else {
            return Self.result(.blocked, gate.reason ?? "")
        }

        phase = .authorizing(decision)
        defer { phase = .idle }

        let grant: any ApprovalSigningGrant
        do {
            grant = try await signer.authorize(reason: Self.reason(decision, approval.summary))
        } catch {
            return Self.signerFailure(error)
        }

        // Face ID can outlast the request; the timestamp is taken after it.
        guard !approval.isExpired(at: clock()) else { return Self.result(.blocked, kApprovalExpired) }
        let ts = Int(clock().timeIntervalSince1970)
        let signature: Data
        do {
            signature = try grant.sign(ApprovalMessage.bytes(id: approvalId, bodySha256: approval.bodySha256, decision: decision, ts: ts))
        } catch {
            return Self.signerFailure(error)
        }

        phase = .sending(decision)
        let body = ApprovalDecisionBody(decision: decision, bodySha256: approval.bodySha256, ts: ts,
                                        deviceId: device.deviceId, signature: signature)
        do {
            let decided = try await source().decide(approvalId, body)
            show(decided)
            return Self.result(.decided, Self.decidedText(decision, decided))
        } catch let error as ApiError where error.statusCode == 409 || error.statusCode == 410 {
            await refresh()
            let message = Self.detail(error) ?? approvalNotPendingText(state.value?.status ?? "")
            return Self.result(.notPending, message)
        } catch let error as ApiError where error.statusCode == 403 {
            return Self.result(.rejected, Self.detail(error) ?? "This iPhone isn't an active approver.")
        } catch let error as ApiError where error.statusCode == 400 {
            return Self.result(.rejected, (Self.detail(error) ?? "The server refused the decision.") + kApprovalClockHint)
        } catch {
            // No status, a 5xx, a body without an approval, or the task
            // cancelled mid-send: the server may have acted, so read it back.
            phase = .checking
            await refresh()
            return Self.result(.uncertain, kApprovalUncertain)
        }
    }

    // MARK: Helpers

    private func fetch() async throws -> McpApproval {
        guard ApprovalIds.isValid(approvalId) else { throw kInvalidApprovalId }
        return try await source().approval(approvalId)
    }

    private func show(_ approval: McpApproval) {
        state = .loaded(approval)
        fingerprintMatches = ApprovalFingerprint.matches(approval)
        refreshError = nil
    }

    private static func result(_ outcome: ApprovalDecisionOutcome, _ message: String = "") -> ApprovalDecisionResult {
        ApprovalDecisionResult(outcome: outcome, message: message)
    }

    /// The Face ID prompt's text: "Approve: <summary>", the summary cut to
    /// 149 characters and "…" past `reasonSummaryLimit`.
    private static func reason(_ decision: ApprovalDecision, _ summary: String) -> String {
        let cut = summary.count > reasonSummaryLimit ? summary.prefix(reasonSummaryLimit - 1) + "…" : summary
        return (decision == .approve ? "Approve: " : "Deny: ") + cut
    }

    private static func signerFailure(_ error: any Error) -> ApprovalDecisionResult {
        switch error as? ApprovalSignerError {
        case .cancelled: result(.cancelled)
        case .noKey: result(.blocked, kApprovalNoKey)
        case .keyInvalidated: result(.blocked, kApprovalKeyInvalidated)
        case .unavailable: result(.blocked, "Couldn't sign the decision: no Secure Enclave")
        case .failed(let cause): result(.blocked, "Couldn't sign the decision: \(cause)")
        case nil: error.isCancellation ? result(.cancelled) : result(.blocked, "Couldn't sign the decision: \(text(error))")
        }
    }

    private static func decidedText(_ decision: ApprovalDecision, _ approval: McpApproval) -> String {
        guard decision == .approve else { return "Denied. The request will not run." }
        switch approval.status {
        case "executed": return "Approved. The request ran."
        case "failed": return "Approved, but the request failed (HTTP \(approval.resultStatus.map(String.init) ?? "?"))."
        default: return "Approved. The request is running."
        }
    }

    /// The server's `detail`; nil when the error carries only the client's
    /// fallback for the status (no detail was sent) or is blank.
    private static func detail(_ error: ApiError) -> String? {
        let fallback = ApiError.from(status: error.statusCode, body: nil, transport: nil).message
        let trimmed = error.message.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty || error.message == fallback ? nil : error.message
    }

    private static func text(_ error: any Error) -> String {
        (error as? ApiError)?.message ?? error.localizedDescription
    }
}
