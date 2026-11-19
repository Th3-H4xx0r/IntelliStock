import Foundation

// Who may decide an approval, and the copy the detail screen shows when it
// can't. Plan: docs/superpowers/plans/2026-11-19-mcp-ios-approvals.md, Task 4.

nonisolated let kApprovalRegisterFirst = "Register this iPhone in Settings › Jarvis approvals to decide requests."
nonisolated let kApprovalExpired = "This request expired."
nonisolated let kApprovalFingerprintMismatch = "The parameters shown don't match this request's fingerprint, so it can't be approved here. Deny it, or check the server."
nonisolated let kApprovalNoKey = "This iPhone has no approval key. Register it again in Settings › Jarvis approvals."
nonisolated let kApprovalKeyInvalidated = "Face ID changed since this iPhone was registered, so its approval key no longer works. Register it again in Settings › Jarvis approvals."
nonisolated let kApprovalUncertain = "Couldn't confirm the server got the decision. Showing where the request stands now."
/// Appended to a 400's message: the usual cause is a phone clock outside the server's 60 s window.
nonisolated let kApprovalClockHint = " If this keeps happening, check that Date & Time is set automatically."

/// What the operator may do with an approval right now.
nonisolated enum ApprovalGate: Hashable, Sendable {
    case open
    /// Approve is blocked for the reason; Deny is allowed.
    case denyOnly(String)
    case closed(String)

    var canApprove: Bool { self == .open }

    var canDeny: Bool {
        switch self {
        case .open, .denyOnly: true
        case .closed: false
        }
    }

    var reason: String? {
        switch self {
        case .open: nil
        case .denyOnly(let reason), .closed(let reason): reason
        }
    }
}

/// The rules, first match wins: not pending, expired, no device registered
/// for this server, device not active yet, then a fingerprint that doesn't
/// match (which still allows Deny: refusing a request needs no trust in it).
nonisolated func approvalGate(_ a: McpApproval, now: Date, device: ApprovalDeviceRecord?, fingerprintMatches: Bool) -> ApprovalGate {
    guard a.isPending else { return .closed(approvalNotPendingText(a.status)) }
    if a.isExpired(at: now) { return .closed(kApprovalExpired) }
    guard let device else { return .closed(kApprovalRegisterFirst) }
    if now < device.activeAt {
        let minutes = max(1, Int((device.activeAt.timeIntervalSince(now) / 60).rounded(.up)))
        return .closed("This iPhone becomes an approver in \(minutes) min.")
    }
    guard fingerprintMatches else { return .denyOnly(kApprovalFingerprintMismatch) }
    return .open
}

/// A status as the not-pending copy says it: "executed" reads "done";
/// approved, failed, denied, expired and anything unknown read as they are.
nonisolated func approvalStatusWord(_ status: String) -> String {
    status == "executed" ? "done" : status
}

/// "This request is done — there's nothing to decide."
nonisolated func approvalNotPendingText(_ status: String) -> String {
    "This request is \(approvalStatusWord(status)) — there's nothing to decide."
}
