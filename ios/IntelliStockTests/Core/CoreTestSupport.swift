import Foundation
@testable import IntelliStock

/// A manual clock for poller tests — the Swift stand-in for Dart's
/// `fakeAsync`. Pass `clock.sleep` as a poller's `PollingSleep`; `advance`
/// fires every sleeper whose deadline falls inside the step, in order,
/// letting the main actor run between them.
@MainActor
final class ManualClock {
    private struct Sleeper {
        let id: Int
        let deadline: Duration
        let continuation: CheckedContinuation<Void, any Error>
    }

    private(set) var now: Duration = .zero
    /// Every duration a poller asked to sleep for, in order.
    private(set) var requested: [Duration] = []
    private var sleepers: [Sleeper] = []
    private var nextID = 0

    var pendingCount: Int { sleepers.count }

    var sleep: PollingSleep {
        { [weak self] duration in
            guard let self else { throw CancellationError() }
            try await self.sleep(for: duration)
        }
    }

    func sleep(for duration: Duration) async throws {
        requested.append(duration)
        try Task.checkCancellation()
        if duration <= .zero { return }
        let id = nextID
        nextID += 1
        let deadline = now + duration
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, any Error>) in
                if Task.isCancelled {
                    continuation.resume(throwing: CancellationError())
                    return
                }
                sleepers.append(Sleeper(id: id, deadline: deadline, continuation: continuation))
            }
        } onCancel: {
            Task { @MainActor in self.cancel(id) }
        }
    }

    private func cancel(_ id: Int) {
        guard let index = sleepers.firstIndex(where: { $0.id == id }) else { return }
        sleepers.remove(at: index).continuation.resume(throwing: CancellationError())
    }

    func advance(by step: Duration) async {
        let target = now + step
        await settle()
        while let next = sleepers.filter({ $0.deadline <= target }).min(by: { $0.deadline < $1.deadline }) {
            now = next.deadline
            sleepers.removeAll { $0.id == next.id }
            next.continuation.resume()
            await settle()
        }
        now = target
        await settle()
    }

    /// Lets queued main-actor work run.
    func settle() async {
        for _ in 0..<50 { await Task.yield() }
    }
}

/// Waits (real time, up to `timeout`) for `condition` — for work that crosses
/// URLSession and so cannot be stepped by `ManualClock`.
@MainActor
func eventually(timeout: Duration = .seconds(3), _ condition: () -> Bool) async -> Bool {
    let deadline = ContinuousClock.now + timeout
    while ContinuousClock.now < deadline {
        if condition() { return true }
        try? await Task.sleep(for: .milliseconds(10))
    }
    return condition()
}

/// A `BiometricAuthenticating` with scripted answers.
final class FakeBiometrics: BiometricAuthenticating {
    var available: Bool
    var authResult: Bool
    var types: [BiometricType]
    private(set) var reasons: [String] = []

    init(available: Bool, authResult: Bool, types: [BiometricType] = [.face]) {
        self.available = available
        self.authResult = authResult
        self.types = types
    }

    func canCheck() async -> Bool { available }
    func availableTypes() async -> [BiometricType] { available ? types : [] }
    func authenticate(_ reason: String) async -> Bool {
        reasons.append(reason)
        return authResult
    }
}

/// A `PushRegistering` that records calls.
final class FakePushRegistrar: PushRegistering {
    var grant: Bool
    private(set) var authorizationRequests = 0
    private(set) var registrations = 0

    init(grant: Bool) { self.grant = grant }

    func requestAuthorization() async -> Bool {
        authorizationRequests += 1
        return grant
    }

    func registerForRemoteNotifications() { registrations += 1 }
}

/// A `WidgetSync` over a throwaway App Group suite, recording reloads.
@MainActor
final class WidgetSyncProbe {
    let suiteName = "test.widget.\(UUID().uuidString)"
    let defaults: UserDefaults
    private(set) var reloads: [String] = []
    private(set) var sync: WidgetSync!

    init(now: Date = Date(timeIntervalSince1970: 1_750_000_000)) {
        defaults = UserDefaults(suiteName: suiteName)!
        // Weak: services under test may outlive the probe that built them.
        sync = WidgetSync(defaults: defaults, reload: { [weak self] in self?.reloads.append($0) }, now: { now })
    }

    func json(_ key: String) -> JSON? {
        guard let raw = defaults.string(forKey: key) else { return nil }
        return try? JSON(data: Data(raw.utf8))
    }

    deinit {
        UserDefaults().removePersistentDomain(forName: suiteName)
    }
}
