import Foundation

/// Dart's `DateTime.tryParse` and `DateTime.fromMillisecondsSinceEpoch`,
/// returning `Date`.
///
/// The models parse timestamps the way the Flutter app did, so a string the
/// Dart parser accepted (or rejected) is accepted (or rejected) here:
///
/// - `2026-06-10`, `20260610`, `2026-06-10T12:00`, `2026-06-10 12:00:00.123456`;
/// - a `Z` or `±HH[:MM]` suffix means UTC; no suffix means device-local time;
/// - fractions keep their first six digits (microseconds);
/// - out-of-range fields roll over (`2026-02-30` is 2 March), as in Dart.
///
/// `Date` has no time-zone flag. Dart code that read `.hour`/`.year` off a
/// parsed `DateTime` read UTC fields for a suffixed string and local fields
/// otherwise; read the matching calendar where that matters.
nonisolated enum DartDateTime {
    /// Dart's `DateTime._parseFormat`, with `\d` spelled `[0-9]` (Dart's
    /// `\d` is ASCII-only; NSRegularExpression's is not).
    private static let pattern =
        #"^([+-]?[0-9]{4,6})-?([0-9]{2})-?([0-9]{2})"#
        + #"(?:[ T]([0-9]{2})(?::?([0-9]{2})(?::?([0-9]{2})(?:[.,]([0-9]+))?)?)?"#
        + #"( ?[zZ]| ?([-+])([0-9]{2})(?::?([0-9]{2}))?)?)?$"#

    private static let regex = try! NSRegularExpression(pattern: pattern)

    /// Dart's representable range: ±8.64e15 ms from the epoch.
    private static let maxMilliseconds = 8_640_000_000_000_000.0

    /// Dart `DateTime.tryParse(s)`; nil for nil or unparseable input.
    static func tryParse(_ s: String?) -> Date? {
        guard let s, !s.isEmpty else { return nil }
        let ns = s as NSString
        guard let m = regex.firstMatch(in: s, range: NSRange(location: 0, length: ns.length)) else { return nil }

        func group(_ i: Int) -> String? {
            let r = m.range(at: i)
            return r.location == NSNotFound ? nil : ns.substring(with: r)
        }
        func int(_ i: Int) -> Int { group(i).flatMap { Int($0) } ?? 0 }

        guard let year = group(1).flatMap({ Int($0) }) else { return nil }
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
                if i < digits.count { micros += Int(digits[i] - UInt8(ascii: "0")) }
            }
        }

        // Normalise the month first, then let days/hours/minutes roll over.
        let monthIndex = month - 1
        let normYear = year + Int((Double(monthIndex) / 12).rounded(.down))
        let normMonth = ((monthIndex % 12) + 12) % 12 + 1
        let days = daysFromCivil(year: normYear, month: normMonth, day: 1) + (day - 1)
        var seconds = Double(days) * 86_400 + Double(hour * 3_600 + minute * 60 + second)
        seconds += Double(micros) / 1_000_000

        let isUtc = group(8) != nil
        if isUtc {
            if let sign = group(9) {
                let offset = Double(int(10) * 3_600 + int(11) * 60)
                seconds -= sign == "-" ? -offset : offset
            }
        } else {
            seconds = localToUTC(wallClockSeconds: seconds)
        }
        guard abs(seconds * 1_000) <= maxMilliseconds else { return nil }
        return Date(timeIntervalSince1970: seconds)
    }

    /// Dart `DateTime.fromMillisecondsSinceEpoch(ms)`.
    static func fromMillisecondsSinceEpoch(_ ms: Int) -> Date {
        Date(timeIntervalSince1970: Double(ms) / 1_000)
    }

    /// Dart `millisecondsSinceEpoch` (truncates toward zero, as Dart's int
    /// storage does).
    static func millisecondsSinceEpoch(_ date: Date) -> Int {
        Int((date.timeIntervalSince1970 * 1_000).rounded(.towardZero))
    }

    /// Days from 1970-01-01 to a proleptic-Gregorian civil date
    /// (Howard Hinnant's algorithm).
    static func daysFromCivil(year: Int, month: Int, day: Int) -> Int {
        let y = month <= 2 ? year - 1 : year
        let era = (y >= 0 ? y : y - 399) / 400
        let yoe = y - era * 400
        let mp = (month + 9) % 12
        let doy = (153 * mp + 2) / 5 + day - 1
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy
        return era * 146_097 + doe - 719_468
    }

    /// The UTC instant whose local wall clock reads `wallClockSeconds`
    /// (seconds since the epoch, read as if UTC).
    private static func localToUTC(wallClockSeconds t: Double) -> Double {
        let zone = TimeZone.current
        let firstGuess = t - Double(zone.secondsFromGMT(for: Date(timeIntervalSince1970: t)))
        let offset = zone.secondsFromGMT(for: Date(timeIntervalSince1970: firstGuess))
        return t - Double(offset)
    }
}
