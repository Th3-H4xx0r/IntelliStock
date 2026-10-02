import Foundation

// Ported from features/dashboard/application/market_hours.dart.
//
// Dart passed wall-clock ET around as a UTC `DateTime` shifted by the ET
// offset and read its `.weekday`/`.hour` fields. Here that wall clock is a
// `Date` whose fields are read in GMT: `etFromUtc` produces one, and
// `isMarketOpenAtEt` reads one. Build test inputs with `etWallClock(...)`.

/// GMT Gregorian calendar used to read and build ET wall-clock dates.
nonisolated let etWallClockCalendar: Calendar = {
    var c = Calendar(identifier: .gregorian)
    c.timeZone = TimeZone(secondsFromGMT: 0)!
    return c
}()

/// True when `etNow` (wall-clock America/New_York) is within the US equity
/// regular session: Mon–Fri, 09:30–16:00. Holidays are not modeled (a known
/// limitation; acceptable for a "live" status hint).
nonisolated func isMarketOpenAtEt(_ etNow: Date) -> Bool {
    let c = etWallClockCalendar.dateComponents([.weekday, .hour, .minute], from: etNow)
    // Calendar weekday: 1 = Sunday, 7 = Saturday.
    if c.weekday == 1 || c.weekday == 7 { return false }
    let minutes = (c.hour ?? 0) * 60 + (c.minute ?? 0)
    let open = 9 * 60 + 30
    let close = 16 * 60
    return minutes >= open && minutes < close
}

/// Convert a UTC instant to wall-clock ET. ET = UTC-4 (EDT) Apr–Oct, UTC-5
/// (EST) otherwise — approximated by month (no exact DST-boundary handling,
/// which is fine for an open/closed hint).
nonisolated func etFromUtc(_ utc: Date) -> Date {
    let month = etWallClockCalendar.component(.month, from: utc)
    let isDst = month > 3 && month < 11 // Apr–Oct → EDT
    return utc.addingTimeInterval(-Double((isDst ? 4 : 5) * 3_600))
}

/// An ET wall-clock date with the given fields (Dart `DateTime(y, m, d, h, mi)`
/// as `isMarketOpenAtEt` reads it).
nonisolated func etWallClock(_ year: Int, _ month: Int, _ day: Int, _ hour: Int = 0, _ minute: Int = 0) -> Date {
    etWallClockCalendar.date(from: DateComponents(year: year, month: month, day: day, hour: hour, minute: minute))!
}
