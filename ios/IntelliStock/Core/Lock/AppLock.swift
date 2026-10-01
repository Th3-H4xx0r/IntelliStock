import Foundation
import Observation
import SwiftUI

/// The auto-lock timeout options, persisted as seconds — `LockTimeout` in
/// `app_lock_controller.dart`.
nonisolated enum LockTimeout: CaseIterable, Hashable, Sendable {
    case immediately
    case oneMinute
    case fiveMinutes

    /// Seconds before a backgrounded app locks; zero means every time.
    var seconds: Int {
        switch self {
        case .immediately: 0
        case .oneMinute: 60
        case .fiveMinutes: 300
        }
    }

    var label: String {
        switch self {
        case .immediately: "Immediately"
        case .oneMinute: "1 minute"
        case .fiveMinutes: "5 minutes"
        }
    }

    /// The string persisted under `lock_timeout`.
    func toStorageString() -> String { String(seconds) }

    /// Unknown or missing values fall back to `immediately`.
    static func fromStorageString(_ s: String?) -> LockTimeout {
        let seconds = JSON.parseInt(s ?? "") ?? 0
        return allCases.first { $0.seconds == seconds } ?? .immediately
    }
}

/// `AppLockState`: the persisted preference, whether the gate is up, and
/// the timeout.
nonisolated struct AppLockState: Equatable, Sendable {
    var enabled = false
    var locked = false
    var timeout: LockTimeout = .immediately

    init(enabled: Bool = false, locked: Bool = false, timeout: LockTimeout = .immediately) {
        self.enabled = enabled
        self.locked = locked
        self.timeout = timeout
    }

    func copyWith(enabled: Bool? = nil, locked: Bool? = nil, timeout: LockTimeout? = nil) -> AppLockState {
        AppLockState(enabled: enabled ?? self.enabled, locked: locked ?? self.locked, timeout: timeout ?? self.timeout)
    }
}

/// The biometric app lock — `AppLockController` in `app_lock_controller.dart`.
///
/// The seed is read synchronously from the keychain before the first frame,
/// so a locked app never flashes its content. `AppServices` forwards every
/// `scenePhase` change: leaving `.active` records when the app was paused,
/// returning to `.active` locks when the timeout has elapsed. The lock only
/// ever protects a session: it never rises while signed out.
@Observable
final class AppLock {
    static let enabledKey = "biometric_lock_enabled"
    static let timeoutKey = "lock_timeout"

    /// The full state. Feature code reads `enabled`/`locked`/`timeout` and
    /// changes them through the methods below; tests set it directly, as the
    /// Dart tests set `notifier.state`.
    var state: AppLockState

    var enabled: Bool { state.enabled }
    var locked: Bool { state.locked }
    var timeout: LockTimeout { state.timeout }

    /// When the app last left the foreground. Exposed for tests, as in Dart.
    @ObservationIgnored var pausedAt: Date?

    @ObservationIgnored private let storage: any SecureStorage
    @ObservationIgnored private let biometrics: any BiometricAuthenticating
    @ObservationIgnored private let isAuthenticated: () -> Bool
    @ObservationIgnored private let now: () -> Date

    init(
        seed: AppLockState,
        storage: any SecureStorage,
        biometrics: any BiometricAuthenticating,
        isAuthenticated: @escaping () -> Bool,
        now: @escaping () -> Date = Date.init
    ) {
        state = seed
        self.storage = storage
        self.biometrics = biometrics
        self.isAuthenticated = isAuthenticated
        self.now = now
    }

    /// The first-frame state, read straight from storage as `main.dart` did:
    /// locked when the preference is on and someone is signed in.
    static func seed(storage: any SecureStorage, isAuthenticated: Bool) -> AppLockState {
        let enabled = storage.read(enabledKey) == "true"
        return AppLockState(
            enabled: enabled,
            locked: enabled && isAuthenticated,
            timeout: LockTimeout.fromStorageString(storage.read(timeoutKey))
        )
    }

    // MARK: Lifecycle

    /// Maps a scene phase onto the Dart lifecycle: `.inactive`/`.background`
    /// are `paused`/`inactive`, `.active` is `resumed`.
    func handle(_ phase: ScenePhase) {
        switch phase {
        case .active: resumed()
        case .inactive, .background: paused()
        @unknown default: break
        }
    }

    /// The app left the foreground. Records the start of the absence; a
    /// later `.background` → `.inactive` on the way back must not restart it,
    /// or a 1- or 5-minute timeout could never elapse.
    func paused() {
        if pausedAt == nil { pausedAt = now() }
    }

    /// The app is in the foreground again: lock when enabled, signed in, and
    /// the timeout has elapsed (zero = always).
    func resumed() {
        defer { pausedAt = nil }
        if !state.enabled { return }
        // The lock protects a SESSION. Signing out keeps the preference, so
        // without this the gate would rise over the login screen.
        if !isAuthenticated() { return }
        // Never backgrounded (e.g. a trailing resume after an unlock).
        guard let paused = pausedAt else { return }
        let elapsed = now().timeIntervalSince(paused)
        let timeout = TimeInterval(state.timeout.seconds)
        if timeout == 0 || elapsed >= timeout {
            state = state.copyWith(locked: true)
        }
    }

    // MARK: Actions

    /// Whether the gate should be up right now: locked AND a session behind it.
    var shouldGate: Bool { state.locked && isAuthenticated() }

    /// Presents the biometric prompt; unlocks on success.
    @discardableResult
    func unlock() async -> Bool {
        let ok = await biometrics.authenticate("Unlock IntelliStock to access your portfolio")
        if ok {
            state = state.copyWith(locked: false)
            // A trailing resume after the prompt must not re-lock.
            pausedAt = nil
        }
        return ok
    }

    /// Turns the lock on after a successful authentication. False when
    /// biometrics are unavailable or the person cancels.
    @discardableResult
    func enable() async -> Bool {
        guard await biometrics.canCheck() else { return false }
        guard await biometrics.authenticate("Authenticate to enable biometric lock") else { return false }
        // The prompt itself backgrounds the scene; that absence is not one
        // the timeout should count.
        pausedAt = nil
        guard (try? storage.write(Self.enabledKey, "true")) != nil else { return false }
        state = state.copyWith(enabled: true)
        return true
    }

    /// Turns the lock off and persists the preference.
    func disable() async {
        try? storage.write(Self.enabledKey, "false")
        state = state.copyWith(enabled: false, locked: false)
    }

    /// Opens the gate WITHOUT changing the preference — "Log out" from the
    /// lock screen, so Login can show while the lock stays armed for the next
    /// sign-in.
    func releaseLock() {
        state = state.copyWith(locked: false)
        pausedAt = nil
    }

    /// Changes and persists the auto-lock timeout.
    func setTimeout(_ timeout: LockTimeout) async {
        try? storage.write(Self.timeoutKey, timeout.toStorageString())
        state = state.copyWith(timeout: timeout)
    }
}
