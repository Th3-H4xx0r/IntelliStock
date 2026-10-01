import Foundation
import SwiftUI

/// A log line's severity, from keyword classification.
nonisolated enum LogLevel: Hashable, Sendable {
    case error, warn, success, info, normal
}

/// One parsed log line — `LogLine` in `log_tailer.dart`.
nonisolated struct LogLine: Identifiable, Hashable, Sendable {
    /// A stable sequence number assigned by `LogTailer`, so rows keep their
    /// identity when the 10 000-line cap trims the front.
    var id: Int
    let raw: String
    let ts: Date?
    let message: String
    let level: LogLevel

    init(id: Int = 0, raw: String, ts: Date?, message: String, level: LogLevel) {
        self.id = id
        self.raw = raw
        self.ts = ts
        self.message = message
        self.level = level
    }

    var color: Color {
        switch level {
        case .error: DS.Palette.danger
        case .warn: DS.Palette.warning
        case .success: DS.Palette.success
        case .info: DS.Palette.info
        case .normal: Color.primary
        }
    }
}

/// Parses a `[timestamp] message` line and classifies its level.
nonisolated func parseLogLine(_ raw: String) -> LogLine {
    var ts: Date?
    var message = raw
    let ns = raw as NSString
    if let match = logLinePattern.firstMatch(in: raw, range: NSRange(location: 0, length: ns.length)) {
        ts = DartDateTime.tryParse(ns.substring(with: match.range(at: 1)).trimmingCharacters(in: .whitespacesAndNewlines))
        let body = match.range(at: 2)
        message = body.location == NSNotFound ? "" : ns.substring(with: body)
    }
    return LogLine(raw: raw, ts: ts, message: message, level: classifyLogLine(raw))
}

nonisolated private let logLinePattern = try! NSRegularExpression(
    pattern: #"^\[([^\]]+)\]\s?(.*)$"#,
    options: [.dotMatchesLineSeparators]
)

nonisolated private func classifyLogLine(_ line: String) -> LogLevel {
    let l = line.lowercased()
    if l.contains("error") || l.contains("traceback") || l.contains("failed") || l.contains("exception") {
        return .error
    }
    if l.contains("warn") || l.contains("retry") || l.contains("skip") {
        return .warn
    }
    if l.contains("completed") || l.contains("success") || l.contains("passed") || l.contains(" ok") {
        return .success
    }
    if l.contains("broker") { return .info }
    return .normal
}
