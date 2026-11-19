import Foundation

// What the approvals screens show, worked out apart from the views so tests
// can pin it down: the device countdown, and the list and detail labels
// (docs/superpowers/plans/2026-11-19-mcp-ios-approvals.md, Tasks 5 and 6).

/// "m:ss" for a countdown, rounded up to whole seconds, so it reads "0:00"
/// only once the moment has come: 572.2 s → "9:33", 0.4 s → "0:01".
nonisolated func approvalCountdown(_ remaining: TimeInterval) -> String {
    let seconds = remaining > 0 ? Int(remaining.rounded(.up)) : 0
    let rest = seconds % 60
    return "\(seconds / 60):" + (rest < 10 ? "0\(rest)" : "\(rest)")
}

/// The status a request shows: a pending one past its expiry reads
/// "expired" before the server gets round to marking it.
nonisolated func approvalDisplayStatus(_ a: McpApproval, now: Date) -> String {
    a.isPending && a.isExpired(at: now) ? "expired" : a.status
}

/// A status in words; an unknown one reads as the server sent it.
nonisolated func approvalStatusLabel(_ status: String) -> String {
    switch status {
    case "pending": "Waiting for you"
    case "approved": "Approved · running"
    case "executed": "Done"
    case "failed": "Failed"
    case "denied": "Denied"
    case "expired": "Expired"
    default: status
    }
}

/// A status's dot: amber while it waits for the operator, live while the
/// server runs it, gain once it ran, loss when it failed.
nonisolated func approvalStatusTone(_ status: String) -> DSStatusTone {
    switch status {
    case "pending": .warn
    case "approved": .live
    case "executed": .positive
    case "failed": .error
    default: .off
    }
}

/// What the request puts at stake; an unknown value reads as sent.
nonisolated func moneyStateLabel(_ state: String) -> String {
    switch state {
    case "live": "Live money"
    case "unknown": "Unknown account · treated as live"
    case "protected": "Protected paper target"
    case "irreversible": "Can't be undone"
    default: state
    }
}

nonisolated func moneyStateTone(_ state: String) -> DSStatusTone {
    switch state {
    case "live", "irreversible": .error
    case "unknown", "protected": .warn
    default: .off
    }
}

/// "Expires in m:ss" while time is left (never "0:00"), then "Expired".
nonisolated func expiryLabel(remaining: TimeInterval) -> String {
    remaining <= 0 ? "Expired" : "Expires in " + approvalCountdown(remaining)
}

/// No parameters: JSON null or an empty object.
nonisolated func approvalParametersAreEmpty(_ json: JSON) -> Bool {
    json.isNull || json.orderedObject?.isEmpty == true
}

/// A request's query, body or result as 2-space pretty JSON in the
/// server's key order; "None" when there is nothing.
nonisolated func approvalParametersText(_ json: JSON) -> String {
    if approvalParametersAreEmpty(json) { return "None" }
    return (try? json.dartEncoded(indent: "  ")) ?? json.dartDescription
}
