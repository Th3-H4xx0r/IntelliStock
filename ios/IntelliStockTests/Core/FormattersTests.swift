import Foundation
import SwiftUI
import Testing
@testable import IntelliStock

/// Ported from test/formatters_test.dart, plus the Dart rounding and parsing
/// rules the formatters must reproduce byte for byte.
struct FormattersTests {
    // MARK: fmtMoney

    @Test func moneyPositive() { #expect(fmtMoney(1234.5) == "$1,234.50") }
    @Test func moneyNegative() { #expect(fmtMoney(-12) == "-$12.00") }
    @Test func moneyNil() { #expect(fmtMoney(nil) == "—") }

    @Test func moneyGroupsLargeValues() {
        #expect(fmtMoney(1_234_567.891) == "$1,234,567.89")
        #expect(fmtMoney(0) == "$0.00")
        #expect(fmtMoney(999.999) == "$1,000.00")
    }

    /// intl rounds `fraction × 100` half away from zero; NumberFormatter's
    /// half-even would print $0.12.
    @Test func moneyRoundsTiesUpLikeIntl() {
        #expect(fmtMoney(0.125) == "$0.13")
        #expect(fmtMoney(2.5 as Double) == "$2.50")
    }

    // MARK: fmtPnl

    @Test func pnlPositiveHasPlus() { #expect(fmtPnl(1234.5) == "+$1,234.50") }
    @Test func pnlNegative() { #expect(fmtPnl(-12) == "-$12.00") }
    @Test func pnlZeroIsPlus() { #expect(fmtPnl(0) == "+$0.00") }
    @Test func pnlNil() { #expect(fmtPnl(nil) == "—") }

    // MARK: fmtPct

    @Test func pctRoundsTo2dp() { #expect(fmtPct(12.345) == "+12.35%") }
    @Test func pctNegative() { #expect(fmtPct(-5) == "-5.00%") }
    @Test func pctNil() { #expect(fmtPct(nil) == "—") }
    @Test func pctNaN() { #expect(fmtPct(Double.nan) == "—") }

    /// Dart `toStringAsFixed` rounds the exact value half up: 0.125 → 0.13,
    /// while 1.005 (stored as 1.00499…) → 1.00.
    @Test func pctUsesDartToStringAsFixed() {
        #expect(fmtPct(0.125) == "+0.13%")
        #expect(fmtPct(1.005) == "+1.00%")
        #expect(fmtPct(-0.004) == "-0.00%")
    }

    // MARK: fmtUsdCost

    @Test func usdCostUnderADollarUses4dp() { #expect(fmtUsdCost(0.0004) == "$0.0004") }
    @Test func usdCostOverADollarUses2dp() { #expect(fmtUsdCost(2) == "$2.00") }
    @Test func usdCostZero() { #expect(fmtUsdCost(0) == "$0.00") }
    @Test func usdCostNegativeKeepsSignAfterDollar() { #expect(fmtUsdCost(-0.5) == "$-0.5000") }

    // MARK: fmtTokens

    @Test func tokensMillions() { #expect(fmtTokens(1_200_000) == "1.2M") }
    @Test func tokensThousands() { #expect(fmtTokens(3400) == "3.4k") }
    @Test func tokensRaw() { #expect(fmtTokens(950) == "950") }

    /// `num.toString()`: an int prints bare, a double keeps its `.0`.
    @Test func tokensRawFollowsDartNumToString() {
        #expect(fmtTokens(950 as Int) == "950")
        #expect(fmtTokens(950.0 as Double) == "950.0")
        #expect(fmtTokens(Int?.none) == "—")
    }

    @Test func tokensRoundTiesUp() {
        #expect(fmtTokens(1250) == "1.3k")
        #expect(fmtTokens(2_250_000) == "2.3M")
    }

    // MARK: fmtElapsed

    @Test func elapsedSeconds() { #expect(fmtElapsed(45) == "45s") }
    @Test func elapsedMinutes() { #expect(fmtElapsed(95) == "1m 35s") }
    @Test func elapsedHours() { #expect(fmtElapsed(3661) == "1h 1m 1s") }
    @Test func elapsedDays() { #expect(fmtElapsed(90061) == "1d 1h 1m") }
    @Test func elapsedFloorsDoubles() { #expect(fmtElapsed(59.9) == "59s") }

    // MARK: fmtDuration

    @Test func durationSubMinuteInteger() { #expect(fmtDuration(3) == "3s") }
    @Test func durationSubMinuteFractional() { #expect(fmtDuration(1.5) == "1.5s") }
    @Test func durationMinutes() { #expect(fmtDuration(200) == "3m 20s") }
    @Test func durationHours() { #expect(fmtDuration(7500) == "2h 5m") }
    @Test func durationDays() { #expect(fmtDuration(90000) == "1d 1h") }
    @Test func durationNil() { #expect(fmtDuration(Double?.none) == "—") }

    // MARK: parseDateTime

    @Test func parseEpochSeconds() throws {
        let dt = try #require(parseDateTime(1_700_000_000))
        #expect(Calendar(identifier: .gregorian).component(.year, from: dt) == 2023)
    }

    @Test func parseNil() { #expect(parseDateTime(nil) == nil) }

    @Test func parseEpochMillisecondsAndNumericStrings() {
        let seconds = parseDateTime(1_700_000_000)
        #expect(parseDateTime(1_700_000_000_000) == seconds)
        #expect(parseDateTime("1700000000") == seconds)
        #expect(parseDateTime(JSON.int(1_700_000_000)) == seconds)
        #expect(parseDateTime(1_700_000_000.5)?.timeIntervalSince1970 == 1_700_000_000.5)
    }

    @Test func parseIsoStrings() {
        let utc = parseDateTime("2026-06-10T14:14:00Z")
        #expect(utc?.timeIntervalSince1970 == 1_781_100_840)
        #expect(parseDateTime("not a date") == nil)
        #expect(parseDateTime(JSON.bool(true)) == nil)
    }

    // MARK: Dates

    @Test func dateTimeFormatsLocalTime() {
        #expect(fmtDateTime("2026-06-10 14:14:00") == "Jun 10, 2026, 2:14 PM")
        #expect(fmtDateTime("2026-06-10 00:05:00") == "Jun 10, 2026, 12:05 AM")
        #expect(fmtDateTime(nil) == "—")
    }

    @Test func dateFormatsMedium() {
        #expect(fmtDate("2026-01-03 09:00:00") == "Jan 3, 2026")
        #expect(fmtDate("garbage") == "—")
    }

    @Test func relativeTime() {
        let now = Date(timeIntervalSince1970: 1_750_000_000)
        #expect(fmtRelative(now.addingTimeInterval(-5), now: now) == "Just now")
        #expect(fmtRelative(now.addingTimeInterval(30), now: now) == "Just now")
        #expect(fmtRelative(now.addingTimeInterval(-120), now: now) == "2m ago")
        #expect(fmtRelative(now.addingTimeInterval(-3 * 3600), now: now) == "3h ago")
        #expect(fmtRelative(now.addingTimeInterval(-2 * 86400 - 5), now: now) == "2d ago")
        #expect(fmtRelative(nil, now: now) == "—")
    }

    // MARK: Colour

    @Test func pnlColorIsSuccessWhenNonNegative() {
        #expect(pnlColor(0) == DS.Palette.success)
        #expect(pnlColor(12.5) == DS.Palette.success)
        #expect(pnlColor(-0.01) == DS.Palette.danger)
        #expect(pnlColor(Double?.none) == DS.Palette.success)
    }
}

/// Dart's `DateTime.tryParse` grammar.
struct DartDateTimeTests {
    private func local(_ date: Date?) -> DateComponents? {
        date.map { DartDateTime.localCalendar.dateComponents([.year, .month, .day, .hour, .minute, .second, .nanosecond], from: $0) }
    }

    @Test func noZoneIsLocalTime() {
        let c = local(DartDateTime.tryParse("2026-01-01 12:00:00"))
        #expect(c?.year == 2026)
        #expect(c?.month == 1)
        #expect(c?.hour == 12)
        #expect(c?.minute == 0)
    }

    @Test func dateOnlyIsLocalMidnight() {
        let c = local(DartDateTime.tryParse("2026-03-04"))
        #expect(c?.day == 4)
        #expect(c?.hour == 0)
    }

    @Test func zuluAndOffsetsAreUtc() {
        #expect(DartDateTime.tryParse("2026-01-01T00:00:00Z")?.timeIntervalSince1970 == 1_767_225_600)
        #expect(DartDateTime.tryParse("20260101T000000Z")?.timeIntervalSince1970 == 1_767_225_600)
        // +05:30 means the instant is 5h30m before the same wall clock in UTC.
        #expect(DartDateTime.tryParse("2026-01-01T05:30:00+05:30")?.timeIntervalSince1970 == 1_767_225_600)
        #expect(DartDateTime.tryParse("2025-12-31T19:00:00-0500")?.timeIntervalSince1970 == 1_767_225_600)
    }

    @Test func fractionKeepsMicroseconds() throws {
        let d = try #require(DartDateTime.tryParse("2026-01-01T00:00:00.1234567Z"))
        #expect(abs(d.timeIntervalSince1970 - 1_767_225_600.123456) < 1e-6)
    }

    @Test func rejectsNonDates() {
        #expect(DartDateTime.tryParse("") == nil)
        #expect(DartDateTime.tryParse("hello") == nil)
        #expect(DartDateTime.tryParse("2026/01/01") == nil)
        #expect(DartDateTime.tryParse(" 2026-01-01") == nil)
    }
}

/// intl `NumberFormat` and Dart `toStringAsFixed`.
struct DartNumberFormatTests {
    @Test func toStringAsFixedMatchesDart() {
        #expect(DartNumberFormat.toStringAsFixed(2.5, 0) == "3")
        #expect(DartNumberFormat.toStringAsFixed(0.125, 2) == "0.13")
        #expect(DartNumberFormat.toStringAsFixed(1.005, 2) == "1.00")
        #expect(DartNumberFormat.toStringAsFixed(9.999, 2) == "10.00")
        #expect(DartNumberFormat.toStringAsFixed(-1.25, 1) == "-1.3")
        #expect(DartNumberFormat.toStringAsFixed(5, 2) == "5.00")
    }

    @Test func groupedMatchesIntl() {
        #expect(DartNumberFormat.grouped(1234.5, fractionDigits: 2) == "1,234.50")
        // Stored as 999999.99499999…: rounds down, as intl does.
        #expect(DartNumberFormat.grouped(999_999.995, fractionDigits: 2) == "999,999.99")
        #expect(DartNumberFormat.grouped(999_999.999, fractionDigits: 2) == "1,000,000.00")
        #expect(DartNumberFormat.grouped(0.00005, fractionDigits: 4) == "0.0001")
        #expect(DartNumberFormat.grouped(-3, fractionDigits: 2) == "-3.00")
    }
}
