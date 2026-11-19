import Foundation

// What the approvals screens show, worked out apart from the views so tests
// can pin it down. Task 6 of docs/superpowers/plans/2026-11-19-mcp-ios-approvals.md
// adds the list and detail labels here.

/// "m:ss" for a countdown, rounded up to whole seconds, so it reads "0:00"
/// only once the moment has come: 572.2 s → "9:33", 0.4 s → "0:01".
nonisolated func approvalCountdown(_ remaining: TimeInterval) -> String {
    let seconds = remaining > 0 ? Int(remaining.rounded(.up)) : 0
    let rest = seconds % 60
    return "\(seconds / 60):" + (rest < 10 ? "0\(rest)" : "\(rest)")
}
