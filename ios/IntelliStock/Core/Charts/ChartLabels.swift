import Foundation

// X-axis label helpers from `core/charts/chart_decorations.dart`. The charts
// hide their own axes and print these labels in a row under the plot.

nonisolated private let chartMonths = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

/// A bare 12-hour label with meridiem and no minutes: `9AM`, `2PM`, `12AM`.
/// Accepts 0–24 (24 wraps to `12AM`, the next midnight).
nonisolated func hourAmPm(_ hour24: Int) -> String {
    let h = dartMod(hour24, 24)
    let period = h < 12 ? "AM" : "PM"
    var h12 = h % 12
    if h12 == 0 { h12 = 12 }
    return "\(h12)\(period)"
}

/// A timestamp label scaled to a named range: `9AM` for 1D, `Mon` for 1W,
/// `Jun 10` for 1M/3M/YTD, `Jun '26` otherwise.
nonisolated func formatChartDate(_ ts: Date, _ range: String) -> String {
    let c = DartDateTime.localCalendar.dateComponents([.year, .month, .day, .hour, .weekday], from: ts)
    switch range {
    case "1D":
        return hourAmPm(c.hour ?? 0)
    case "1W":
        let days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        // Dart weekday: Monday = 1 … Sunday = 7. Calendar: Sunday = 1.
        let dartWeekday = ((c.weekday ?? 1) + 5) % 7 + 1
        return days[min(max(dartWeekday - 1, 0), 6)]
    case "1M", "3M", "YTD":
        return "\(chartMonths[(c.month ?? 1) - 1]) \(c.day ?? 1)"
    default:
        return "\(chartMonths[(c.month ?? 1) - 1]) '\(twoDigitYear(c.year ?? 0))"
    }
}

/// A timestamp label scaled to the visible span — for charts whose range is
/// not a named preset (backtests over arbitrary windows).
nonisolated func formatChartDateBySpan(_ ts: Date, _ span: TimeInterval) -> String {
    let c = DartDateTime.localCalendar.dateComponents([.year, .month, .day, .hour, .minute], from: ts)
    // Dart's Duration.inDays truncates toward zero.
    let days = Int((span / 86_400).rounded(.towardZero))
    if days <= 2 {
        return String(format: "%02d:%02d", c.hour ?? 0, c.minute ?? 0)
    }
    if days <= 120 {
        return "\(chartMonths[(c.month ?? 1) - 1]) \(c.day ?? 1)"
    }
    return "\(chartMonths[(c.month ?? 1) - 1]) '\(twoDigitYear(c.year ?? 0))"
}

/// Up to `slots` evenly spaced indices to label (always the first and last).
nonisolated func evenlySpacedLabelIndices(_ count: Int, _ slots: Int) -> [Int] {
    if count <= 0 { return [] }
    if count == 1 { return [0] }
    let n = min(max(slots, 2), count)
    return (0..<n).map { s in Int((Double(s) / Double(n - 1) * Double(count - 1)).rounded()) }
}

/// `year.toString().substring(2)`.
nonisolated private func twoDigitYear(_ year: Int) -> String {
    String(String(year).dropFirst(2))
}
