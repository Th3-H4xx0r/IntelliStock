import Foundation
import Observation

/// `kPlaybackSpeeds`.
nonisolated let backtestPlaybackSpeeds: [Double] = [0.5, 1, 2, 5, 10]

/// The backtest replay — `BacktestPlaybackController`: steps through the
/// playback events one frame at a time at the chosen speed.
@Observable
final class BacktestPlaybackModel {
    private(set) var events: [PlaybackEvent] = []
    private(set) var metadata = PlaybackMetadata()
    private(set) var frameIndex = -1
    private(set) var isPlaying = false
    /// Default 1x.
    private(set) var speedIndex = 1
    private(set) var loading = true
    private(set) var error: String?

    @ObservationIgnored private let repository: () -> BacktestRepository
    @ObservationIgnored private let sleep: PollingSleep
    @ObservationIgnored private var frameTask: Task<Void, Never>?
    /// The portfolio events' parsed dates, built once per load.
    @ObservationIgnored private var portfolioPoints: [PortfolioPoint] = []

    private struct PortfolioPoint {
        let index: Int
        let time: Date
        let value: Double?
    }

    init(repository: @escaping () -> BacktestRepository, sleep: @escaping PollingSleep = realPollingSleep) {
        self.repository = repository
        self.sleep = sleep
    }

    // MARK: Derived (BacktestPlaybackState getters)

    /// Nothing loaded yet (a first load cut off by leaving stays `loading`),
    /// or the load failed: the screen's `.task` loads again on appear.
    var needsLoad: Bool { loading || error != nil }

    var speed: Double { backtestPlaybackSpeeds[speedIndex] }
    var isFinished: Bool { frameIndex >= events.count - 1 }
    var isEmpty: Bool { !loading && events.isEmpty }

    /// `(1000 / speed).round()` milliseconds.
    static func frameDelayMs(_ speed: Double) -> Int { Int((1000 / speed).rounded()) }

    /// "1x", "0.5x" (`speed % 1 == 0 ? toInt() : speed`).
    var speedLabel: String {
        speed.truncatingRemainder(dividingBy: 1) == 0 ? "\(Int(speed))x" : "\(JSON.dartDoubleString(speed))x"
    }

    /// Events up to and including the current frame.
    var visibleEvents: [PlaybackEvent] {
        frameIndex >= 0 ? Array(events.prefix(frameIndex + 1)) : []
    }

    /// The last portfolio event at or before the frame.
    var currentPortfolioEvent: PlaybackEvent? {
        guard frameIndex >= 0 else { return nil }
        for i in stride(from: min(frameIndex, events.count - 1), through: 0, by: -1) where events[i].type == "portfolio" {
            return events[i]
        }
        return nil
    }

    var currentPortfolioValue: Num {
        currentPortfolioEvent?.portfolioValue ?? metadata.initialCash ?? .int(100000)
    }

    var currentHoldings: [PlaybackHolding] { currentPortfolioEvent?.holdings ?? [] }

    /// "label — time" of the latest date event, or "—".
    var currentDateLabel: String {
        guard frameIndex >= 0 else { return "—" }
        for i in stride(from: min(frameIndex, events.count - 1), through: 0, by: -1) where events[i].type == "date" {
            return "\(events[i].label ?? "") — \(events[i].time ?? "")"
        }
        return "—"
    }

    /// Portfolio points from the start up to the frame (from the dates parsed
    /// once at load, not on every frame).
    var portfolioHistory: [(time: Date, value: Double)] {
        portfolioPoints.prefix { $0.index <= frameIndex }.compactMap { p in
            p.value.map { (p.time, $0) }
        }
    }

    /// The full x range: first − 1 day (else now − 7 days) … last (else now).
    func xRange(now: Date = Date()) -> (min: Date, max: Date) {
        let first = portfolioPoints.first?.time
        let last = portfolioPoints.last?.time
        return (first.map { $0.addingTimeInterval(-86400) } ?? now.addingTimeInterval(-7 * 86400), last ?? now)
    }

    /// Every portfolio event with a parseable date, in order: its index, date
    /// and value (nil when it had none, which still counts for the x range).
    private static func parsePortfolioPoints(_ events: [PlaybackEvent]) -> [PortfolioPoint] {
        events.enumerated().compactMap { i, ev in
            guard ev.type == "portfolio", let d = ev.date, let dt = DartDateTime.tryParse(d) else { return nil }
            return PortfolioPoint(index: i, time: dt, value: ev.portfolioValue?.double)
        }
    }

    /// The chart series: the initial-cash anchor at the range start, then the
    /// history.
    func chartPoints(now: Date = Date()) -> [(time: Date, value: Double)] {
        [(xRange(now: now).min, metadata.initialCash?.double ?? 0)] + portfolioHistory
    }

    // MARK: Load

    func load(_ id: String) async {
        do {
            let data = try await repository().playbackData(id)
            portfolioPoints = Self.parsePortfolioPoints(data.events)
            events = data.events
            metadata = data.metadata
            frameIndex = -1
            loading = false
            error = nil
        } catch {
            if error.isCancellation { return }
            loading = false
            self.error = KalshiFormat.errorText(error)
        }
    }

    // MARK: Controls

    private func cancelTimer() {
        frameTask?.cancel()
        frameTask = nil
    }

    private func scheduleFrame() {
        cancelTimer()
        let delay = Duration.milliseconds(Self.frameDelayMs(speed))
        let sleep = sleep
        frameTask = Task { [weak self] in
            do { try await sleep(delay) } catch { return }
            guard !Task.isCancelled else { return }
            self?.advance()
        }
    }

    func advance() {
        guard isPlaying else { return }
        if isFinished {
            isPlaying = false
            return
        }
        frameIndex += 1
        scheduleFrame()
    }

    func togglePlay() {
        if isPlaying {
            cancelTimer()
            isPlaying = false
        } else {
            if isFinished {
                frameIndex = events.isEmpty ? -1 : 0
            }
            isPlaying = true
            scheduleFrame()
        }
    }

    func reset() {
        cancelTimer()
        frameIndex = events.isEmpty ? -1 : 0
        isPlaying = false
    }

    func cycleSpeed() {
        let wasPlaying = isPlaying
        if wasPlaying { cancelTimer() }
        speedIndex = (speedIndex + 1) % backtestPlaybackSpeeds.count
        if wasPlaying { scheduleFrame() }
    }

    /// Stops the frame timer (the view went away). Playback pauses, so the
    /// screen never comes back saying "playing" with no timer behind it.
    func stop() {
        cancelTimer()
        isPlaying = false
    }
}
