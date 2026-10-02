import Foundation
import Observation

// Ported from features/dashboard/presentation/portfolio_chart.dart: the
// `_HistoryNotifier` family and the pure helpers.

/// The chart's ranges (`_ranges`).
nonisolated let dashboardChartRanges = ["1D", "1W", "1M", "3M", "YTD", "1Y", "ALL"]

/// Absolute and percent change vs the baseline (`openValue`, else the first
/// point). The active value is the scrubbed point when `scrubIndex` is in
/// range, else `currentValue`, else the last point. (nil, nil) when empty;
/// pct is nil when the baseline is 0.
nonisolated func computeChange(_ history: PortfolioHistory, scrubIndex: Int? = nil) -> (abs: Double?, pct: Double?) {
    if history.isEmpty { return (nil, nil) }
    let baseline = history.openValue ?? history.values[0]
    let active: Double
    if let scrubIndex, scrubIndex >= 0, scrubIndex < history.values.count {
        active = history.values[scrubIndex]
    } else {
        active = history.currentValue ?? history.values[history.values.count - 1]
    }
    let abs = active - baseline
    if baseline == 0 { return (abs, nil) }
    return (abs, (abs / baseline) * 100)
}

/// The figure the hero shows: the scrubbed point when `scrubIndex` is in
/// range, else `currentValue`, else the last point, else 0. The portfolio
/// sheet reads its balances through the same function.
nonisolated func dashboardHeroValue(_ history: PortfolioHistory, scrubIndex: Int? = nil) -> Double {
    let values = history.values
    if let scrubIndex, scrubIndex >= 0, scrubIndex < values.count { return values[scrubIndex] }
    return history.currentValue ?? values.last ?? 0
}

/// The nearest data-point index by timestamp for a fraction across [0, 1]
/// (not clamped, as in Dart). 0 for an empty or single-point list.
nonisolated func nearestIndex(_ timestamps: [Date], _ fraction: Double) -> Int {
    if timestamps.count <= 1 { return 0 }
    let first = timestamps[0].dartMillis
    let target = first + fraction * (timestamps[timestamps.count - 1].dartMillis - first)
    var lo = 0
    var hi = timestamps.count - 1
    while lo < hi {
        let mid = (lo + hi) / 2
        if timestamps[mid].dartMillis < target {
            lo = mid + 1
        } else {
            hi = mid
        }
    }
    if lo <= 0 { return 0 }
    let prev = lo - 1
    return abs(timestamps[lo].dartMillis - target) < abs(timestamps[prev].dartMillis - target) ? lo : prev
}

/// The chart area's x mapping and labels (`_ChartArea.build`).
nonisolated enum DashboardChartGeometry {
    /// 1D plots against a fixed full-day axis of 1440 minutes.
    static let dayMinutes = 24.0 * 60

    /// Minutes since the point's own local midnight (`_minuteOfDay`):
    /// whole seconds (Dart `inSeconds`) over 60.
    static func minuteOfDay(_ ts: Date, calendar: Calendar = DartDateTime.localCalendar) -> Double {
        let start = calendar.startOfDay(for: ts)
        let seconds = Int(ts.timeIntervalSince(start).rounded(.towardZero))
        return Double(seconds) / 60
    }

    /// 1D: minute of day per point; otherwise the index (gapless).
    static func xs(_ history: PortfolioHistory, range: String, calendar: Calendar = DartDateTime.localCalendar) -> [Double] {
        if range == "1D" { return history.timestamps.map { minuteOfDay($0, calendar: calendar) } }
        return (0..<history.values.count).map(Double.init)
    }

    /// The x-axis domain: [0, 1440] on 1D, [0, n − 1] otherwise.
    static func domain(range: String, count n: Int) -> ClosedRange<Double> {
        if range == "1D" { return 0...dayMinutes }
        return 0...Double(max(n - 1, 1))
    }

    /// The labels under the plot.
    static func labels(_ history: PortfolioHistory, range: String) -> [String] {
        if range == "1D" { return [0, 8, 16, 24].map(hourAmPm) }
        let n = history.values.count
        return evenlySpacedLabelIndices(n, 4).compactMap { i in
            i < history.timestamps.count ? formatChartDate(history.timestamps[i], range) : nil
        }
    }

    /// The snapped index for a selection at plot x `selection` (`onDrag`):
    /// 1D → the point nearest in minutes; otherwise `fractionToIndex`.
    static func scrubIndex(selection: Double, xs: [Double], range: String) -> Int {
        let n = xs.count
        if n == 0 { return 0 }
        if range == "1D" {
            let target = min(max(selection, 0), dayMinutes)
            var idx = 0
            var best = Double.infinity
            for i in 0..<n {
                let d = abs(xs[i] - target)
                if d < best {
                    best = d
                    idx = i
                }
            }
            return idx
        }
        let span = Double(max(n - 1, 1))
        return fractionToIndex(min(max(selection / span, 0), 1), n)
    }

    /// What names the plotted series for `chartDrawIn`: the account and the
    /// range. Switching either draws the chart in again; a poll, which only
    /// appends or updates points, keeps the key and the chart still.
    static func drawInKey(accountId: String, range: String) -> AnyHashable {
        AnyHashable([accountId, range])
    }
}

/// One account's portfolio history for the selected range, kept live — the
/// `_historyProvider` family plus the chart's range and scrub state
/// (`_PortfolioChartState`).
///
/// - 1D is re-based to local midnight; polls every 5 s on 1D, 30 s otherwise.
/// - A failed poll keeps the last good data; each success stamps the
///   dashboard's `portfolioUpdatedAt`.
/// - Switching range keeps the previous history on screen (value and curve)
///   until the new range lands.
@Observable
final class DashboardPortfolioChartModel {
    let accountId: String
    private(set) var range = "1D"
    /// The current range's history (`histAsync`).
    private(set) var state: Loadable<PortfolioHistory> = .loading
    /// The last successfully loaded history (`_lastHistory`).
    private(set) var lastHistory: PortfolioHistory?
    /// The range `lastHistory` was loaded for (`_lastLoadedRange`).
    private(set) var lastLoadedRange = "1D"
    /// The scrubbed data index, nil when not scrubbing.
    var scrubIndex: Int?

    @ObservationIgnored private let fetch: (String, String) async throws -> PortfolioHistory
    @ObservationIgnored private let onUpdated: () -> Void
    @ObservationIgnored private let now: () -> Date

    init(
        accountId: String,
        fetch: @escaping (String, String) async throws -> PortfolioHistory,
        onUpdated: @escaping () -> Void = {},
        now: @escaping () -> Date = Date.init
    ) {
        self.accountId = accountId
        self.fetch = fetch
        self.onUpdated = onUpdated
        self.now = now
    }

    /// The history the value row reads: fresh, else the held one.
    var valueHistory: PortfolioHistory? { state.value ?? lastHistory }

    /// The hero's own figures while it shows 1D, for this account's row in
    /// the portfolio sheet; nil on any other range.
    var daySummary: DashboardAccountSummary? {
        guard lastLoadedRange == "1D", let lastHistory else { return nil }
        return DashboardAccountSummary(history: lastHistory)
    }

    /// Poll cadence: the 1D curve grows continuously; longer ranges barely move.
    var interval: Duration { range == "1D" ? .seconds(5) : .seconds(30) }

    /// `_setRange`: clears the scrub and starts a fresh load for `r`.
    func setRange(_ r: String) {
        guard r != range else { return }
        scrubIndex = nil
        range = r
        state = .loading
    }

    /// `_HistoryNotifier._fetch`.
    private func history(_ range: String) async throws -> PortfolioHistory {
        let h = try await fetch(accountId, range)
        // 1D is shown relative to the device's local midnight (overnight view).
        return range == "1D" ? h.sinceLocalMidnight(now: now()) : h
    }

    /// The first fetch for the current range.
    func load() async {
        let r = range
        do {
            let h = try await history(r)
            guard r == range else { return }
            apply(h, range: r)
        } catch {
            guard r == range, state.value == nil, !error.isCancellationOrTaskCancelled else { return }
            state = .failed(error)
        }
    }

    /// One poll tick: keeps the last good data on failure.
    func refresh() async {
        let r = range
        do {
            let h = try await history(r)
            guard r == range else { return }
            apply(h, range: r)
        } catch {
            // keep the last good data on a transient poll failure
        }
    }

    private func apply(_ h: PortfolioHistory, range r: String) {
        state = .loaded(h)
        lastHistory = h
        lastLoadedRange = r
        onUpdated()
    }

    /// First fetch for the range (unless already loaded), then the range's
    /// cadence until cancelled. Restart it when the range changes.
    func poll(lifecycle: AppLifecycle?, sleep: @escaping PollingSleep = realPollingSleep) async {
        if state.value == nil { await load() }
        await PollingLoop(interval: { [weak self] in self?.interval ?? .seconds(30) }, sleep: sleep) { [weak self] in
            await self?.refresh()
        }
        .run(lifecycle: lifecycle)
    }
}
