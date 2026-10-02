import Foundation
import Testing
@testable import IntelliStock

/// The backtest row's date range (Wave R2 redesign: "#554589 · Jul 7 – Sep 18,
/// 2026"), with the raw Dart "start → end" kept for anything unparseable.
struct BacktestRowFormatTests {
    @Test func sameYearShowsTheYearOnce() {
        #expect(BacktestRowFormat.dateRange("2026-07-07", "2026-09-18") == "Jul 7 – Sep 18, 2026")
    }

    @Test func acrossYearsShowsBothYears() {
        #expect(BacktestRowFormat.dateRange("2025-12-01", "2026-02-01") == "Dec 1, 2025 – Feb 1, 2026")
    }

    @Test func aTimestampSuffixIsIgnored() {
        #expect(BacktestRowFormat.dateRange("2026-01-05T00:00:00Z", "2026-01-31T23:59:59Z") == "Jan 5 – Jan 31, 2026")
    }

    @Test func missingOrUnparseableFallsBackToTheDartText() {
        #expect(BacktestRowFormat.dateRange(nil, nil) == "? → ?")
        #expect(BacktestRowFormat.dateRange("2026-07-07", nil) == "2026-07-07 → ?")
        #expect(BacktestRowFormat.dateRange("soon", "later") == "soon → later")
    }
}
