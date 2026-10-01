import Foundation

/// Dart `DateTime` parsing and epoch arithmetic, so dates read exactly as the
/// Flutter app read them.
nonisolated enum DartDateTime {
    /// Dart's `DateTime.tryParse` grammar (dart:core `DateTime.parse`):
    ///
    ///     ([+-]?\d{4,6})-?(\d\d)-?(\d\d)
    ///     (?:[ T](\d\d)(?::?(\d\d)(?::?(\d\d)(?:[.,](\d+))?)?)?
    ///        ( ?[zZ]| ?([-+])(\d\d)(?::?(\d\d))?)?)?
    ///
    /// No time zone → local time; `Z` or an offset → UTC. Fractions keep six
    /// digits (microseconds). Out-of-range fields roll over, as in Dart.
    static func tryParse(_ s: String) -> Date? {
        let ns = s as NSString
        guard let m = pattern.firstMatch(in: s, range: NSRange(location: 0, length: ns.length)) else { return nil }

        func group(_ i: Int) -> String? {
            let r = m.range(at: i)
            return r.location == NSNotFound ? nil : ns.substring(with: r)
        }
        func int(_ i: Int) -> Int { group(i).flatMap { Int($0) } ?? 0 }

        let year = int(1)
        let month = int(2)
        let day = int(3)
        let hour = int(4)
        let minute = int(5)
        let second = int(6)
        var micros = 0
        if let fraction = group(7) {
            let digits = Array(fraction.utf8)
            for i in 0..<6 {
                micros *= 10
                if i < digits.count { micros += Int(digits[i] ^ 0x30) }
            }
        }

        let isUtc = group(8) != nil
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = isUtc ? TimeZone(identifier: "UTC")! : .current
        var components = DateComponents()
        components.year = year
        components.month = month
        components.day = day
        components.hour = hour
        components.minute = minute
        components.second = second
        guard var date = calendar.date(from: components) else { return nil }
        date += Double(micros) / 1_000_000

        if isUtc, let sign = group(9) {
            let offsetMinutes = int(10) * 60 + int(11)
            let direction: Double = sign == "-" ? -1 : 1
            date -= direction * Double(offsetMinutes) * 60
        }
        return date
    }

    private static let pattern = try! NSRegularExpression(
        pattern: #"^([+-]?\d{4,6})-?(\d\d)-?(\d\d)(?:[ T](\d\d)(?::?(\d\d)(?::?(\d\d)(?:[.,](\d+))?)?)?( ?[zZ]| ?([-+])(\d\d)(?::?(\d\d))?)?)?$"#
    )

    /// Dart's `DateTime` is always proleptic Gregorian in local time, whatever
    /// calendar the person picked in Settings.
    static var localCalendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = .autoupdatingCurrent
        return calendar
    }

    /// Dart `DateTime.fromMillisecondsSinceEpoch(ms)`.
    static func fromMillisecondsSinceEpoch(_ ms: Int) -> Date {
        Date(timeIntervalSince1970: Double(ms) / 1000)
    }
}

nonisolated extension Date {
    /// Dart's `DateTime.millisecondsSinceEpoch` (floored to whole milliseconds).
    var dartMillis: Double {
        (timeIntervalSince1970 * 1000).rounded(.down)
    }
}
