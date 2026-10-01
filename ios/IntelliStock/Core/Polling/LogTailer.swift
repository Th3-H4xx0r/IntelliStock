import Foundation
import Observation

/// The tailer's view of the log — `LogTailerState` in `log_tailer.dart`.
nonisolated struct LogTailerState: Sendable {
    var lines: [LogLine] = []
    var nextLine = 0
    var totalLines = 0
    var truncated = false
    var finalStatus: String?
    var source = "file"
    var buildId: String?
    var loading = true
    var error: (any Error)?

    /// Dart's `copyWith`: a nil argument keeps the current value (so a
    /// response without `final_status` or `id` keeps the previous one).
    func copyWith(
        lines: [LogLine]? = nil,
        nextLine: Int? = nil,
        totalLines: Int? = nil,
        truncated: Bool? = nil,
        finalStatus: String? = nil,
        source: String? = nil,
        buildId: String? = nil,
        loading: Bool? = nil,
        error: (any Error)? = nil,
        clearError: Bool = false
    ) -> LogTailerState {
        var next = self
        next.lines = lines ?? self.lines
        next.nextLine = nextLine ?? self.nextLine
        next.totalLines = totalLines ?? self.totalLines
        next.truncated = truncated ?? self.truncated
        next.finalStatus = finalStatus ?? self.finalStatus
        next.source = source ?? self.source
        next.buildId = buildId ?? self.buildId
        next.loading = loading ?? self.loading
        next.error = clearError ? nil : (error ?? self.error)
        return next
    }
}

/// Cursor-based log tailer matching the web `live-logs?since_line=` pattern —
/// `LogTailer` in `log_tailer.dart`.
///
/// Polls every `runningInterval` (5 s) while the build is running and
/// `idleInterval` (15 s) otherwise, re-polls immediately on `truncated`,
/// backs off 2/5/10/30 s after errors, and keeps at most 10 000 lines.
@Observable
final class LogTailer {
    static let maxLines = 10_000
    static let backoffSeconds = [2, 5, 10, 30]

    private(set) var state = LogTailerState()

    @ObservationIgnored private let client: ApiClient
    @ObservationIgnored private let pathBuilder: (Int) -> String
    @ObservationIgnored private let runningInterval: Duration
    @ObservationIgnored private let idleInterval: Duration
    @ObservationIgnored private let sleep: PollingSleep

    @ObservationIgnored private var timer: Task<Void, Never>?
    @ObservationIgnored private var paused = false
    @ObservationIgnored private var disposed = false
    @ObservationIgnored private var polling = false
    @ObservationIgnored private var errorStreak = 0
    @ObservationIgnored private var nextLineId = 0

    /// `pathBuilder` builds the request path from the cursor, query included,
    /// e.g. `{ "/instances/\(id)/live-logs?since_line=\($0)" }`.
    init(
        client: ApiClient,
        pathBuilder: @escaping (Int) -> String,
        runningInterval: Duration = .seconds(5),
        idleInterval: Duration = .seconds(15),
        sleep: @escaping PollingSleep = realPollingSleep
    ) {
        self.client = client
        self.pathBuilder = pathBuilder
        self.runningInterval = runningInterval
        self.idleInterval = idleInterval
        self.sleep = sleep
    }

    var isPaused: Bool { paused }

    func start() {
        Task { await poll() }
    }

    func pause() {
        paused = true
        timer?.cancel()
        timer = nil
    }

    /// Unpauses and polls at once.
    func resume() {
        guard paused else { return }
        paused = false
        Task { await poll() }
    }

    func dispose() {
        disposed = true
        timer?.cancel()
        timer = nil
    }

    // MARK: Polling

    private func poll() async {
        if disposed || paused || polling { return }
        polling = true
        do {
            let data = try await client.get(pathBuilder(state.nextLine))
            guard data.object != nil else { throw LogTailerFormatError() }
            errorStreak = 0

            let source = data["source"].string ?? "file"
            let buildId = data["id"].string

            // A new build / source swap reseeds the cursor.
            var lines = state.lines
            var nextLine = state.nextLine
            if buildId != state.buildId, state.buildId != nil {
                lines = []
                nextLine = 0
            }

            var parsed: [LogLine] = []
            for item in data["logs"].arrayValue {
                // Dart's `.cast<String>()` throws on any non-string entry.
                guard case .string(let raw) = item else { throw LogTailerFormatError() }
                var line = parseLogLine(raw)
                line.id = nextLineId
                nextLineId += 1
                parsed.append(line)
            }
            var merged = lines + parsed
            if merged.count > Self.maxLines {
                merged = Array(merged.suffix(Self.maxLines))
            }

            let truncated = data["truncated"].bool
            let cursor = try Self.dartInt(data["next_line"])
            let total = try Self.dartInt(data["total_lines"])
            state = state.copyWith(
                lines: merged,
                nextLine: cursor ?? nextLine,
                totalLines: total ?? merged.count,
                truncated: truncated,
                finalStatus: data["final_status"].string,
                source: source,
                buildId: buildId,
                loading: false,
                clearError: true
            )
            polling = false
            scheduleNext(truncated ? .zero : activeInterval())
        } catch {
            errorStreak += 1
            state = state.copyWith(loading: false, error: error)
            let index = min(max(errorStreak - 1, 0), Self.backoffSeconds.count - 1)
            polling = false
            scheduleNext(.seconds(Self.backoffSeconds[index]))
        }
    }

    /// Dart `(x as num?)?.toInt()`: nil for null, throws for a non-number.
    private static func dartInt(_ j: JSON) throws -> Int? {
        switch j {
        case .null: return nil
        case .int, .double: return j.int
        default: throw LogTailerFormatError()
        }
    }

    private func activeInterval() -> Duration {
        let s = state.finalStatus?.lowercased()
        let running = s == nil || s == "running" || s == "building"
        return running ? runningInterval : idleInterval
    }

    private func scheduleNext(_ delay: Duration) {
        timer?.cancel()
        timer = nil
        if disposed || paused { return }
        let sleep = sleep
        timer = Task { [weak self] in
            do { try await sleep(delay) } catch { return }
            guard !Task.isCancelled, let self else { return }
            // Detach so a pause cannot cancel the request mid-flight.
            self.timer = nil
            await self.poll()
        }
    }
}

nonisolated private struct LogTailerFormatError: Error {}
