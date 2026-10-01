import Foundation
import Observation
import Testing
@testable import IntelliStock

/// Ported from test/core/charts/chart_geometry_test.dart.
struct ChartGeometryTests {
    private func close(_ a: Double, _ b: Double) -> Bool { abs(a - b) < 1e-9 }

    @Test func fractionToIndexEmptyOrSingle() {
        #expect(fractionToIndex(0.5, 0) == 0)
        #expect(fractionToIndex(0.5, 1) == 0)
    }

    @Test func fractionToIndexEnds() {
        #expect(fractionToIndex(0.0, 5) == 0)
        #expect(fractionToIndex(1.0, 5) == 4)
    }

    @Test func fractionToIndexRoundsToNearest() {
        #expect(fractionToIndex(0.5, 5) == 2)
        #expect(fractionToIndex(0.6, 5) == 2) // 2.4 → 2
        #expect(fractionToIndex(0.7, 5) == 3) // 2.8 → 3
    }

    @Test func fractionToIndexClamps() {
        #expect(fractionToIndex(-0.3, 5) == 0)
        #expect(fractionToIndex(1.9, 5) == 4)
    }

    @Test func indexToFractionEmptyOrSingle() {
        #expect(indexToFraction(0, 0) == 0)
        #expect(indexToFraction(3, 1) == 0)
    }

    @Test func indexToFractionEndsAndMiddle() {
        #expect(close(indexToFraction(0, 5), 0))
        #expect(close(indexToFraction(4, 5), 1))
        #expect(close(indexToFraction(2, 5), 0.5))
    }

    @Test func indexToFractionRoundTrips() {
        for i in 0..<7 {
            #expect(fractionToIndex(indexToFraction(i, 7), 7) == i)
        }
    }

    @Test func indexToFractionClamps() {
        #expect(indexToFraction(-2, 5) == 0)
        #expect(indexToFraction(99, 5) == 1)
    }

    @Test func paddedBoundsExpandsBySpanFraction() {
        let b = paddedBounds([10.0, 20.0], padFraction: 0.1)
        #expect(close(b.min, 9))
        #expect(close(b.max, 21))
    }

    @Test func paddedBoundsFlatSeriesKeepsPositiveHeight() {
        let b = paddedBounds([5.0, 5.0, 5.0])
        #expect(b.max > b.min)
        let small = paddedBounds([0.5, 0.5])
        #expect(small == ChartBounds(min: -0.5, max: 1.5))
    }

    @Test func paddedBoundsEmptyIsAUnitBand() {
        let b = paddedBounds([Double]())
        #expect(b == ChartBounds(min: 0, max: 1))
    }

    private let base = Date(timeIntervalSince1970: 1_767_225_600)
    /// Unevenly spaced: 0 h, 1 h, 5 h (so index fraction ≠ time fraction).
    private var ts: [Date] { [base, base + 3600, base + 5 * 3600] }

    @Test func timeMappingEmptyOrSingle() {
        #expect(nearestIndexByTime([], 0.5) == 0)
        #expect(nearestIndexByTime([base], 0.9) == 0)
        #expect(timeFractionOf([], 0) == 0)
        #expect(timeFractionOf([base], 0) == 0)
    }

    @Test func timeMappingEnds() {
        #expect(nearestIndexByTime(ts, 0) == 0)
        #expect(nearestIndexByTime(ts, 1) == 2)
    }

    @Test func mapsByTimeNotIndex() {
        #expect(nearestIndexByTime(ts, 0.7) == 2) // 3.5 h → closer to 5 h
        #expect(nearestIndexByTime(ts, 0.1) == 0) // 0.5 h → closer to 0 h
    }

    @Test func timeFractionOfPoints() {
        #expect(close(timeFractionOf(ts, 0), 0))
        #expect(close(timeFractionOf(ts, 1), 0.2))
        #expect(close(timeFractionOf(ts, 2), 1))
    }

    @Test func timeMappingRoundTrips() {
        for i in ts.indices {
            #expect(nearestIndexByTime(ts, timeFractionOf(ts, i)) == i)
        }
    }

    @Test func valueToYMapsMaxToTopAndMinToBottom() {
        #expect(close(valueToY(20, 10, 20, 100), 0))
        #expect(close(valueToY(10, 10, 20, 100), 100))
    }

    @Test func valueToYMidpoint() { #expect(close(valueToY(15, 10, 20, 100), 50)) }

    @Test func valueToYClamps() {
        #expect(close(valueToY(30, 10, 20, 100), 0))
        #expect(close(valueToY(0, 10, 20, 100), 100))
    }

    @Test func valueToYDegenerateRangeIsMidHeight() {
        #expect(close(valueToY(5, 5, 5, 100), 50))
    }
}

/// Ported from test/core/charts/scrub_controller_test.dart. Dart counted
/// `ValueNotifier` notifications; here an observation of `value` stands in.
@MainActor
struct ScrubControllerTests {
    /// Whether `mutate` changes `controller.value` as Observation sees it.
    private func notifies(_ controller: ScrubController, _ mutate: () -> Void) -> Bool {
        let fired = ObservationFlag()
        withObservationTracking { _ = controller.value } onChange: { fired.set() }
        mutate()
        return fired.value
    }

    @Test func startsEmpty() {
        #expect(ScrubController(onTick: {}).value == nil)
    }

    @Test func ticksOnlyWhenTheSnappedIndexChanges() {
        var ticks = 0
        let c = ScrubController(onTick: { ticks += 1 })
        c.update(0, 0.0)  // first touch → tick
        c.update(0, 0.04) // same index → no tick
        c.update(1, 0.25) // new index → tick
        c.update(2, 0.5)  // new index → tick
        c.update(2, 0.55) // same index → no tick
        #expect(ticks == 3)
    }

    @Test func exposesTheLatestSample() {
        let c = ScrubController(onTick: {})
        c.update(3, 0.72)
        #expect(c.value?.index == 3)
        #expect(abs((c.value?.fraction ?? 0) - 0.72) < 1e-9)
    }

    @Test func clearResetsAndNotifiesOnce() {
        let c = ScrubController(onTick: {})
        #expect(notifies(c) { c.update(1, 0.3) })
        #expect(notifies(c) { c.clear() })
        #expect(c.value == nil)
    }

    @Test func clearOnAnEmptyControllerDoesNotNotify() {
        let c = ScrubController(onTick: {})
        #expect(!notifies(c) { c.clear() })
    }

    @Test func notifiesOnEveryUpdateSoTheHairlineFollows() {
        let c = ScrubController(onTick: {})
        #expect(notifies(c) { c.update(0, 0.0) })
        #expect(notifies(c) { c.update(0, 0.1) })
        #expect(!notifies(c) { c.update(0, 0.1) })
    }
}

/// The label helpers from chart_decorations.dart.
struct ChartLabelTests {
    private func local(_ y: Int, _ m: Int, _ d: Int, _ h: Int = 0, _ min: Int = 0) -> Date {
        DartDateTime.localCalendar.date(from: DateComponents(year: y, month: m, day: d, hour: h, minute: min))!
    }

    @Test func hourAmPmLabels() {
        #expect(hourAmPm(0) == "12AM")
        #expect(hourAmPm(9) == "9AM")
        #expect(hourAmPm(12) == "12PM")
        #expect(hourAmPm(14) == "2PM")
        #expect(hourAmPm(24) == "12AM")
    }

    @Test func formatChartDateByRange() {
        let ts = local(2026, 6, 10, 14) // a Wednesday
        #expect(formatChartDate(ts, "1D") == "2PM")
        #expect(formatChartDate(ts, "1W") == "Wed")
        #expect(formatChartDate(local(2026, 6, 14), "1W") == "Sun")
        #expect(formatChartDate(local(2026, 6, 15), "1W") == "Mon")
        #expect(formatChartDate(ts, "1M") == "Jun 10")
        #expect(formatChartDate(ts, "YTD") == "Jun 10")
        #expect(formatChartDate(ts, "1Y") == "Jun '26")
    }

    @Test func formatChartDateBySpanScalesWithTheWindow() {
        let ts = local(2026, 6, 10, 9, 5)
        #expect(formatChartDateBySpan(ts, 2 * 86_400) == "09:05")
        #expect(formatChartDateBySpan(ts, 30 * 86_400) == "Jun 10")
        #expect(formatChartDateBySpan(ts, 400 * 86_400) == "Jun '26")
    }

    @Test func evenlySpacedIndices() {
        #expect(evenlySpacedLabelIndices(0, 4) == [])
        #expect(evenlySpacedLabelIndices(1, 4) == [0])
        #expect(evenlySpacedLabelIndices(2, 4) == [0, 1])
        #expect(evenlySpacedLabelIndices(10, 4) == [0, 3, 6, 9])
        #expect(evenlySpacedLabelIndices(3, 4) == [0, 1, 2])
    }
}

/// A thread-safe "did it fire" flag for Observation's `@Sendable` onChange.
nonisolated final class ObservationFlag: Sendable {
    private let lock = NSLock()
    nonisolated(unsafe) private var fired = false

    func set() { lock.withLock { fired = true } }
    var value: Bool { lock.withLock { fired } }
}
