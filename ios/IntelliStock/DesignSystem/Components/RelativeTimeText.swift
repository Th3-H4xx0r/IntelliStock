import SwiftUI

/// "Just now", "5m ago", … for `timestamp`, re-rendered every `tick` so the
/// label stays honest with no parent update — `RelativeTimeText` in
/// `relative_time_text.dart`. Shows nothing for a nil timestamp. Pass `clock`
/// to control "now" (tests, previews). Style it with `.font` and
/// `.foregroundStyle` like any `Text`.
struct RelativeTimeText: View {
    let timestamp: Date?
    var tick: TimeInterval = 20
    var clock: (() -> Date)?

    var body: some View {
        if let timestamp {
            TimelineView(.periodic(from: .now, by: tick)) { context in
                Text(Self.label(for: timestamp, now: clock?() ?? context.date))
            }
        }
    }

    /// The text shown for `timestamp` at `now`.
    static func label(for timestamp: Date, now: Date) -> String {
        fmtRelative(timestamp, now: now)
    }
}
