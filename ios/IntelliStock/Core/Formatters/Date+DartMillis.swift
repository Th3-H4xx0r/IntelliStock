import Foundation

// Dart `DateTime` parsing lives in Core/Models/DartDateTime.swift.

nonisolated extension Date {
    /// Dart's `DateTime.millisecondsSinceEpoch` (floored to whole milliseconds).
    var dartMillis: Double {
        (timeIntervalSince1970 * 1000).rounded(.down)
    }
}

nonisolated extension DartDateTime {
    /// The device's Gregorian calendar in its current time zone — what Dart's
    /// local `DateTime` fields (`.hour`, `.day`) read.
    static var localCalendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = .autoupdatingCurrent
        return calendar
    }
}
