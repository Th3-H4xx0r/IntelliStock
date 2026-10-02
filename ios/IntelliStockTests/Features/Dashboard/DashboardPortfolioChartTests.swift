import Foundation
import Testing
@testable import IntelliStock

/// Ported from test/features/dashboard/portfolio_chart_helpers_test.dart.
struct DashboardPortfolioChartHelperTests {
    private func makeHistory(values: [Double], openValue: Double? = nil, currentValue: Double? = nil) -> PortfolioHistory {
        let start = DartDateTime.tryParse("2026-01-01")!
        return PortfolioHistory(
            timestamps: values.indices.map { start.addingTimeInterval(Double($0) * 3600) },
            values: values,
            currentValue: currentValue,
            openValue: openValue
        )
    }

    @Test func computeChangeReturnsNilsForEmptyHistory() {
        let (a, p) = computeChange(makeHistory(values: []))
        #expect(a == nil)
        #expect(p == nil)
    }

    @Test func positiveChangeUsesOpenValueAsBaseline() {
        let (a, p) = computeChange(makeHistory(values: [100, 110, 120], openValue: 100, currentValue: 120))
        #expect(close(a, 20, 0.001))
        #expect(close(p, 20, 0.001))
    }

    @Test func negativeChangeIsComputedCorrectly() {
        let (a, p) = computeChange(makeHistory(values: [200, 190, 180], openValue: 200, currentValue: 180))
        #expect(close(a, -20, 0.001))
        #expect(close(p, -10, 0.001))
    }

    @Test func scrubIndexOverridesCurrentValue() {
        let (a, p) = computeChange(makeHistory(values: [100, 105, 110, 115], openValue: 100), scrubIndex: 1)
        #expect(close(a, 5, 0.001))
        #expect(close(p, 5, 0.001))
    }

    @Test func fallsBackToFirstValueAsBaselineWithoutOpenValue() {
        let (a, p) = computeChange(makeHistory(values: [50, 60, 70]))
        #expect(close(a, 20, 0.001))
        #expect(close(p, 40, 0.001))
    }

    @Test func returnsNilPctWhenBaselineIsZero() {
        let (a, p) = computeChange(makeHistory(values: [0, 10], openValue: 0))
        #expect(close(a, 10, 0.001))
        #expect(p == nil)
    }

    private var fiveHours: [Date] {
        let base = DartDateTime.tryParse("2026-01-01")!
        return (0..<5).map { base.addingTimeInterval(Double($0) * 3600) }
    }

    @Test func nearestIndexAtTheEndsAndMiddle() {
        #expect(nearestIndex(fiveHours, 0) == 0)
        #expect(nearestIndex(fiveHours, 1) == 4)
        #expect(nearestIndex(fiveHours, 0.5) == 2)
    }

    @Test func nearestIndexSlightlyBelowMidpointChoosesLowerIndex() {
        // 0.375 is 1.5h into the 4h span → 1h is closer than 2h.
        #expect(nearestIndex(fiveHours, 0.375) == 1)
    }

    @Test func nearestIndexIsZeroForEmptyAndSingleLists() {
        #expect(nearestIndex([], 0.5) == 0)
        #expect(nearestIndex([Date()], 0.9) == 0)
    }

    @Test func sinceLocalMidnightBaselinesAtMidnightAndTrimsToTheDay() {
        let now = Date()
        let midnight = Calendar.current.startOfDay(for: now)
        let h = PortfolioHistory(
            timestamps: [-7200, -3600, 3600, 7200].map { midnight.addingTimeInterval($0) },
            values: [100, 110, 120, 130],
            currentValue: 130
        )
        let r = h.sinceLocalMidnight(now: now)
        #expect(r.openValue == 110)
        #expect(r.timestamps.first == midnight)
        #expect(r.values == [110, 120, 130])
        #expect(close(r.changeAbs, 20, 0.001))
        #expect(close(r.changePct, 20.0 / 110.0 * 100, 0.001))
    }

    @Test func sinceLocalMidnightFallsBackToFirstValueWithoutAPreMidnightSample() {
        let now = Date()
        let midnight = Calendar.current.startOfDay(for: now)
        let h = PortfolioHistory(
            timestamps: [3600, 7200].map { midnight.addingTimeInterval($0) },
            values: [200, 210],
            currentValue: 210
        )
        let r = h.sinceLocalMidnight(now: now)
        #expect(r.openValue == 200)
        #expect(r.timestamps.first == midnight)
        #expect(r.values == [200, 200, 210])
        #expect(close(r.changeAbs, 10, 0.001))
    }

    @Test func sinceLocalMidnightIsANoOpForEmptyHistory() {
        #expect(PortfolioHistory(timestamps: [], values: []).sinceLocalMidnight().isEmpty)
    }
}

/// The chart area's 1D minute axis, labels and scrub snapping (`_ChartArea`).
struct DashboardChartGeometryTests {
    private var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "America/New_York")!
        return c
    }

    @Test func minuteOfDayCountsWholeSecondsFromLocalMidnight() {
        let midnight = calendar.startOfDay(for: Date(timeIntervalSince1970: 1_780_000_000))
        #expect(DashboardChartGeometry.minuteOfDay(midnight, calendar: calendar) == 0)
        #expect(DashboardChartGeometry.minuteOfDay(midnight.addingTimeInterval(90.9), calendar: calendar) == 1.5)
        #expect(DashboardChartGeometry.minuteOfDay(midnight.addingTimeInterval(13 * 3600), calendar: calendar) == 780)
    }

    @Test func oneDayPlotsMinutesOtherRangesPlotIndices() {
        let midnight = calendar.startOfDay(for: Date(timeIntervalSince1970: 1_780_000_000))
        let h = PortfolioHistory(timestamps: [midnight, midnight.addingTimeInterval(600)], values: [1, 2])
        #expect(DashboardChartGeometry.xs(h, range: "1D", calendar: calendar) == [0, 10])
        #expect(DashboardChartGeometry.xs(h, range: "1W", calendar: calendar) == [0, 1])
        #expect(DashboardChartGeometry.domain(range: "1D", count: 2) == 0...1440)
        #expect(DashboardChartGeometry.domain(range: "1M", count: 5) == 0...4)
    }

    @Test func oneDayLabelsAreTheFixedHourTicks() {
        let h = PortfolioHistory(timestamps: [Date()], values: [1])
        #expect(DashboardChartGeometry.labels(h, range: "1D") == ["12AM", "6AM", "12PM", "6PM", "12AM"])
    }

    @Test func longerRangesLabelFourEvenlySpacedPoints() {
        let start = DartDateTime.tryParse("2026-06-01T12:00:00")!
        let h = PortfolioHistory(
            timestamps: (0..<10).map { start.addingTimeInterval(Double($0) * 86_400) },
            values: Array(repeating: 1, count: 10)
        )
        #expect(DashboardChartGeometry.labels(h, range: "1M") == ["Jun 1", "Jun 4", "Jun 7", "Jun 10"])
    }

    @Test func oneDayScrubSnapsToTheNearestMinute() {
        let xs: [Double] = [0, 30, 95, 400]
        #expect(DashboardChartGeometry.scrubIndex(selection: 60, xs: xs, range: "1D") == 1)
        #expect(DashboardChartGeometry.scrubIndex(selection: 70, xs: xs, range: "1D") == 2)
        // Past the last sample (the empty rest of the day) → the last point.
        #expect(DashboardChartGeometry.scrubIndex(selection: 1300, xs: xs, range: "1D") == 3)
    }

    @Test func indexScrubRoundsToTheNearestPoint() {
        let xs: [Double] = [0, 1, 2, 3, 4]
        #expect(DashboardChartGeometry.scrubIndex(selection: 1.4, xs: xs, range: "1M") == 1)
        #expect(DashboardChartGeometry.scrubIndex(selection: 1.6, xs: xs, range: "1M") == 2)
        #expect(DashboardChartGeometry.scrubIndex(selection: 9, xs: xs, range: "1M") == 4)
    }
}

/// `_HistoryNotifier` + `_PortfolioChartState`: cadence, re-basing, held
/// data across a range switch, and the freshness stamp.
struct DashboardPortfolioChartModelTests {
    private final class FetchLog {
        var calls: [(String, String)] = []
        var fail = false
    }

    private func history(_ values: [Double], at now: Date) -> PortfolioHistory {
        let midnight = Calendar.current.startOfDay(for: now)
        return PortfolioHistory(
            timestamps: values.indices.map { midnight.addingTimeInterval(Double($0 + 1) * 60) },
            values: values,
            currentValue: values.last
        )
    }

    @Test func oneDayIsRebasedToLocalMidnightAndStampsUpdated() async {
        let now = Date()
        let log = FetchLog()
        var stamps = 0
        let model = DashboardPortfolioChartModel(
            accountId: "b1",
            fetch: { id, range in
                log.calls.append((id, range))
                return self.history([100, 110], at: now)
            },
            onUpdated: { stamps += 1 },
            now: { now }
        )
        await model.load()
        #expect(log.calls.map(\.1) == ["1D"])
        #expect(log.calls.map(\.0) == ["b1"])
        // Re-based: a baseline point planted at midnight.
        #expect(model.state.value?.values == [100, 100, 110])
        #expect(stamps == 1)
        #expect(model.interval == .seconds(5))
    }

    @Test func longerRangesPollEveryThirtySecondsAndAreNotRebased() async {
        let now = Date()
        let model = DashboardPortfolioChartModel(accountId: "b1", fetch: { _, _ in self.history([1, 2, 3], at: now) }, now: { now })
        model.setRange("1Y")
        #expect(model.interval == .seconds(30))
        await model.load()
        #expect(model.state.value?.values == [1, 2, 3])
    }

    @Test func aFailedPollKeepsTheLastGoodData() async {
        let now = Date()
        let log = FetchLog()
        let model = DashboardPortfolioChartModel(
            accountId: "b1",
            fetch: { _, _ in
                if log.fail { throw ApiError(message: "down") }
                return self.history([5, 6], at: now)
            },
            now: { now }
        )
        await model.load()
        log.fail = true
        await model.refresh()
        #expect(model.state.value != nil)
    }

    @Test func aFirstLoadFailureIsAnError() async {
        let model = DashboardPortfolioChartModel(accountId: "b1", fetch: { _, _ in throw ApiError(message: "nope") })
        await model.load()
        #expect(model.state.errorMessage == "nope")
        #expect(model.valueHistory == nil)
    }

    @Test func aRangeSwitchHoldsThePreviousHistoryUntilTheNewOneLands() async {
        let now = Date()
        let model = DashboardPortfolioChartModel(accountId: "b1", fetch: { _, _ in self.history([1, 2], at: now) }, now: { now })
        await model.load()
        model.scrubIndex = 1
        model.setRange("1M")
        #expect(model.scrubIndex == nil)
        #expect(model.state.isLoading)
        #expect(model.lastLoadedRange == "1D")
        #expect(model.valueHistory != nil)
        await model.load()
        #expect(model.lastLoadedRange == "1M")
    }

    @Test func pollFetchesAtTheRangeCadence() async {
        let clock = ManualClock()
        let now = Date()
        let log = FetchLog()
        let model = DashboardPortfolioChartModel(
            accountId: "b1",
            fetch: { id, range in
                log.calls.append((id, range))
                return self.history([1, 2], at: now)
            },
            now: { now }
        )
        let task = Task { await model.poll(lifecycle: nil, sleep: clock.sleep) }
        await clock.advance(by: .seconds(11))
        task.cancel()
        // First fetch, then two 5 s ticks.
        #expect(log.calls.count == 3)
        #expect(clock.requested.prefix(2) == [.seconds(5), .seconds(5)])
    }
}
