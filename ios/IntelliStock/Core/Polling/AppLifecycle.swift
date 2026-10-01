import Foundation
import Observation
import SwiftUI

/// Whether the app is in the foreground, so pollers can pause in the
/// background — `AppLifecycleNotifier` in `poller.dart`. `AppServices` feeds
/// it every `scenePhase` change.
@Observable
final class AppLifecycle {
    private(set) var isForeground = true
    /// When the app last left `.active`.
    private(set) var lastPausedAt: Date?

    @ObservationIgnored private var subscribers: [UUID: AsyncStream<Bool>.Continuation] = [:]
    @ObservationIgnored private let now: () -> Date

    init(isForeground: Bool = true, now: @escaping () -> Date = Date.init) {
        self.isForeground = isForeground
        self.now = now
    }

    func handle(_ phase: ScenePhase) {
        switch phase {
        case .active: setForeground(true)
        case .inactive, .background: setForeground(false)
        @unknown default: break
        }
    }

    /// Dart records `lastPausedAt` on both `paused` and `inactive`.
    func setForeground(_ foreground: Bool) {
        if !foreground { lastPausedAt = now() }
        guard foreground != isForeground else { return }
        isForeground = foreground
        for continuation in subscribers.values { continuation.yield(foreground) }
    }

    /// Every later foreground change. The stream ends when the consuming task
    /// is cancelled.
    func changes() -> AsyncStream<Bool> {
        let id = UUID()
        let (stream, continuation) = AsyncStream.makeStream(of: Bool.self, bufferingPolicy: .bufferingNewest(1))
        subscribers[id] = continuation
        continuation.onTermination = { [weak self] _ in
            Task { @MainActor in self?.subscribers[id] = nil }
        }
        return stream
    }
}
