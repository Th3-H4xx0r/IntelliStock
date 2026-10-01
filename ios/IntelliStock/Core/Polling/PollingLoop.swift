import Foundation

/// How a poller waits between cycles. Production sleeps for real; tests pass
/// a manual clock so cadence assertions run instantly.
typealias PollingSleep = (Duration) async throws -> Void

/// The default `PollingSleep`: `Task.sleep`, which throws on cancellation.
let realPollingSleep: PollingSleep = { try await Task.sleep(for: $0) }

/// A self-scheduling interval poller — `IntervalPoller` plus the lifecycle
/// half of `PollingNotifier` in `poller.dart`.
///
/// - The caller performs the first fetch; the loop drives the later ones.
/// - `interval` is re-read every cycle, so cadence can follow state (3 s while
///   trading, 10 s idle).
/// - A failing fetch never stops the loop; the model surfaces its own errors.
/// - `pause()` cancels the pending tick; `resume()` schedules a fresh full
///   interval. A fetch already in flight is allowed to finish.
///
/// Typical use from a screen's model:
///
///     func poll(lifecycle: AppLifecycle) async {
///         await load()                                   // first fetch
///         await PollingLoop(interval: { self.interval }) { await self.refresh() }
///             .run(lifecycle: lifecycle)                 // until the view goes
///     }
///
/// and in the view: `.task { await model.poll(lifecycle: services.lifecycle) }`.
final class PollingLoop {
    private let interval: () -> Duration
    private let fetch: () async throws -> Void
    private let sleep: PollingSleep

    private var timer: Task<Void, Never>?
    private var paused = false
    private var disposed = false
    private var running = false

    init(
        interval: @escaping () -> Duration,
        sleep: @escaping PollingSleep = realPollingSleep,
        fetch: @escaping () async throws -> Void
    ) {
        self.interval = interval
        self.sleep = sleep
        self.fetch = fetch
    }

    var isPaused: Bool { paused }

    func start() { schedule() }

    func pause() {
        paused = true
        timer?.cancel()
        timer = nil
    }

    func resume() {
        guard paused else { return }
        paused = false
        schedule()
    }

    func dispose() {
        disposed = true
        timer?.cancel()
        timer = nil
    }

    /// Runs until the calling task is cancelled (use from `.task {}`), pausing
    /// while `lifecycle` reports the background and resuming on foreground.
    /// Disposes on exit.
    func run(lifecycle: AppLifecycle?) async {
        if let lifecycle {
            if lifecycle.isForeground { start() } else { pause() }
            for await foreground in lifecycle.changes() {
                if foreground { resume() } else { pause() }
            }
        } else {
            start()
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(3600))
            }
        }
        dispose()
    }

    private func schedule() {
        timer?.cancel()
        timer = nil
        guard !disposed, !paused else { return }
        let delay = interval()
        let sleep = sleep
        timer = Task { [weak self] in
            do { try await sleep(delay) } catch { return }
            guard !Task.isCancelled else { return }
            await self?.tick()
        }
    }

    private func tick() async {
        if disposed || paused || running {
            if !disposed, !paused { schedule() }
            return
        }
        running = true
        // Detach from the timer so a pause cannot cancel the fetch mid-flight.
        timer = nil
        do {
            try await fetch()
        } catch {
            // Keep polling; the model surfaces errors via its own state.
        }
        running = false
        schedule()
    }
}
