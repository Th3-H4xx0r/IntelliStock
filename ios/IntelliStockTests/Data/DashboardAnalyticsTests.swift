import Foundation
import Testing
@testable import IntelliStock

/// Ported from test/features/dashboard/portfolio_analytics_test.dart and
/// market_hours_test.dart.
struct AggregateBySectorTests {
    @Test func groupsBySectorSortsDescComputesPct() {
        let slices = aggregateBySector(
            [("AAPL", 60), ("MSFT", 30), ("JPM", 10)],
            ["AAPL": "Technology", "MSFT": "Technology", "JPM": "Financials"]
        )
        #expect(slices.first?.sector == "Technology")
        #expect(slices.first?.value == 90)
        #expect(close(slices.first?.pct, 90, 0.001))
        #expect(slices.last?.sector == "Financials")
        #expect(close(slices.last?.pct, 10, 0.001))
    }

    @Test func blankOrUnknownSectorFoldsIntoOtherNonPositiveIgnored() {
        let slices = aggregateBySector(
            [("AAPL", 50), ("XYZ", 50), ("ZERO", 0)],
            ["AAPL": "Technology", "XYZ": nil]
        )
        #expect(Set(slices.map(\.sector)) == ["Technology", "Other"])
        #expect(slices.allSatisfy { $0.value == 50 })
        // Equal values keep first-appearance order, as the Dart map did.
        #expect(slices.map(\.sector) == ["Technology", "Other"])
    }

    @Test func optionContractsAreExcludedLongOrShort() {
        let slices = aggregateBySector(
            [("AAPL", 60), ("APH261002P00130000", -85), ("SPY261218C00612500", 400)],
            ["AAPL": "Technology", "APH261002P00130000": "Technology", "SPY261218C00612500": "Other"]
        )
        #expect(slices.count == 1)
        #expect(slices.first?.sector == "Technology")
        #expect(slices.first?.value == 60)
        #expect(close(slices.first?.pct, 100, 0.001))
    }

    @Test func emptyInputIsEmptyList() {
        #expect(aggregateBySector([], [:]).isEmpty)
        #expect(aggregateBySector([("A", 0)], ["A": "Tech"]).isEmpty)
    }
}

struct ConcentrationTests {
    @Test func evenSplitHighDiversificationLowTopWeight() {
        let s = concentration([25, 25, 25, 25])
        #expect(s.count == 4)
        #expect(close(s.topWeight, 25, 0.001))
        #expect(close(s.hhi, 0.25, 0.001))
        #expect(s.score == 75)
    }

    @Test func singleHoldingIsConcentrated() {
        let s = concentration([1000])
        #expect(s.count == 1)
        #expect(close(s.topWeight, 100, 0.001))
        #expect(close(s.hhi, 1.0, 0.001))
        #expect(s.score == 0)
    }

    @Test func ignoresNonPositiveAndHandlesEmpty() {
        #expect(concentration([0, -5]).isEmpty)
        #expect(concentration([]).isEmpty)
        #expect(concentration([100, 0]).count == 1)
    }
}

struct TodaysMoversTests {
    @Test func sortsByPctDescendingGainersFirst() {
        let m = todaysMovers([("A", -2.0), ("B", 5.0), ("C", 1.0)])
        #expect(m.map(\.symbol) == ["B", "C", "A"])
        #expect(m.first?.pct == 5.0)
        #expect(m.last?.pct == -2.0)
    }

    @Test func emptyIsEmpty() {
        #expect(todaysMovers([]).isEmpty)
    }

    @Test func tiesKeepInputOrder() {
        #expect(todaysMovers([("X", 0), ("Y", 0), ("Z", 0)]).map(\.symbol) == ["X", "Y", "Z"])
    }
}

struct PctChangeOfTests {
    @Test func computesFirstToLastPercentChange() {
        #expect(close(pctChangeOf([100, 110]), 10.0, 0.001))
        #expect(close(pctChangeOf([100, 90, 95]), -5.0, 0.001))
    }

    @Test func nilWhenNotComputable() {
        #expect(pctChangeOf([100]) == nil)
        #expect(pctChangeOf([]) == nil)
        #expect(pctChangeOf([0, 50]) == nil)
    }
}

struct RiskMetricsTests {
    @Test func flatCurveZeroVolZeroDrawdownNilSharpe() {
        let r = riskMetrics([100, 100, 100, 100])
        #expect(r.volatility == 0)
        #expect(r.maxDrawdown == 0)
        #expect(r.sharpe == nil)
    }

    @Test func maxDrawdownIsTheLargestPeakToTroughDrop() {
        #expect(close(riskMetrics([100, 120, 90, 110]).maxDrawdown, 25.0, 0.001))
    }

    @Test func risingCurvePositiveSharpeNoDrawdown() {
        let r = riskMetrics([100, 101, 102, 103, 104])
        #expect((r.sharpe ?? 0) > 0)
        #expect(r.maxDrawdown == 0)
    }

    @Test func tooFewPointsIsEmpty() {
        #expect(riskMetrics([100]).isEmpty)
        #expect(riskMetrics([]).isEmpty)
    }

    @Test func aFundingJumpDoesNotBlowUpVolatility() {
        // A ~10x deposit (a +900% single-period jump) must be ignored, not
        // produce a garbage ~1000% annualized volatility.
        #expect(riskMetrics([100, 1000, 1010, 1005, 1015, 1012]).volatility < 200)
    }
}

struct MarketHoursTests {
    // Inputs are wall-clock ET (the caller converts via etFromUtc).
    @Test func openDuringTheRegularWeekdaySession() {
        #expect(isMarketOpenAtEt(etWallClock(2026, 6, 18, 10, 0))) // Thu 10:00
        #expect(isMarketOpenAtEt(etWallClock(2026, 6, 18, 9, 30)))
        #expect(isMarketOpenAtEt(etWallClock(2026, 6, 18, 15, 59)))
    }

    @Test func closedBeforeOpenAtOrAfterCloseAndOnWeekends() {
        #expect(!isMarketOpenAtEt(etWallClock(2026, 6, 18, 9, 29)))
        #expect(!isMarketOpenAtEt(etWallClock(2026, 6, 18, 16, 0)))
        #expect(!isMarketOpenAtEt(etWallClock(2026, 6, 20, 12, 0))) // Sat
        #expect(!isMarketOpenAtEt(etWallClock(2026, 6, 21, 12, 0))) // Sun
    }

    @Test func etFromUtcSubtracts4hInSummer() {
        // 18:00 UTC in June → 14:00 ET.
        let et = etFromUtc(etWallClock(2026, 6, 18, 18, 0))
        #expect(etWallClockCalendar.component(.hour, from: et) == 14)
    }

    @Test func etFromUtcSubtracts5hInWinter() {
        let et = etFromUtc(etWallClock(2026, 1, 6, 18, 0))
        #expect(etWallClockCalendar.component(.hour, from: et) == 13)
    }
}
