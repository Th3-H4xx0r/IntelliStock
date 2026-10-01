import Foundation
import SwiftUI
import Testing
@testable import IntelliStock

/// Ported from test/core/lock/app_lock_controller_test.dart, plus the
/// scene-phase sequences the native lifecycle produces (Review Focus 4).
@MainActor
struct AppLockTests {
    /// Mirrors the Dart `_makeContainer`: fake storage seeded with the stored
    /// prefs, the lock seeded exactly as `main()` seeds it.
    private func makeLock(
        biometrics: FakeBiometrics,
        storedEnabled: String? = nil,
        storedTimeout: String? = nil,
        authenticated: Bool = false,
        now: @escaping () -> Date = Date.init
    ) -> (AppLock, InMemorySecureStorage) {
        var initial: [String: String] = [:]
        if let storedEnabled { initial[AppLock.enabledKey] = storedEnabled }
        if let storedTimeout { initial[AppLock.timeoutKey] = storedTimeout }
        let storage = InMemorySecureStorage(initial)
        let seed = AppLock.seed(storage: storage, isAuthenticated: authenticated)
        let lock = AppLock(seed: seed, storage: storage, biometrics: biometrics, isAuthenticated: { authenticated }, now: now)
        return (lock, storage)
    }

    // MARK: enable() requires auth

    @Test func enableReturnsTrueAndSetsEnabledWhenAuthSucceeds() async {
        let (lock, storage) = makeLock(biometrics: FakeBiometrics(available: true, authResult: true))
        let ok = await lock.enable()
        #expect(ok)
        #expect(lock.enabled)
        #expect(storage.read(AppLock.enabledKey) == "true")
    }

    @Test func enableReturnsFalseAndLeavesDisabledWhenAuthFails() async {
        let (lock, storage) = makeLock(biometrics: FakeBiometrics(available: true, authResult: false))
        let ok = await lock.enable()
        #expect(!ok)
        #expect(!lock.enabled)
        #expect(storage.read(AppLock.enabledKey) == nil)
    }

    @Test func enableReturnsFalseWhenBiometricsAreUnavailable() async {
        let bio = FakeBiometrics(available: false, authResult: true)
        let (lock, _) = makeLock(biometrics: bio)
        let ok = await lock.enable()
        #expect(!ok)
        #expect(!lock.enabled)
        #expect(bio.reasons.isEmpty)
    }

    @Test func enableUsesTheDartReason() async {
        let bio = FakeBiometrics(available: true, authResult: true)
        let (lock, _) = makeLock(biometrics: bio)
        await lock.enable()
        #expect(bio.reasons == ["Authenticate to enable biometric lock"])
    }

    // MARK: App start

    @Test func locksOnStartWhenEnabledAndAuthenticated() {
        let (lock, _) = makeLock(
            biometrics: FakeBiometrics(available: true, authResult: true),
            storedEnabled: "true",
            authenticated: true
        )
        #expect(lock.locked)
    }

    @Test func doesNotLockOnStartWhenDisabled() {
        let (lock, _) = makeLock(
            biometrics: FakeBiometrics(available: true, authResult: true),
            storedEnabled: "false",
            authenticated: true
        )
        #expect(!lock.locked)
    }

    @Test func doesNotLockOnStartWhenSignedOut() {
        let (lock, _) = makeLock(
            biometrics: FakeBiometrics(available: true, authResult: true),
            storedEnabled: "true",
            authenticated: false
        )
        #expect(lock.enabled)
        #expect(!lock.locked)
    }

    @Test func seedReadsTheStoredTimeout() {
        let (lock, _) = makeLock(biometrics: FakeBiometrics(available: true, authResult: true), storedTimeout: "300")
        #expect(lock.timeout == .fiveMinutes)
    }

    // MARK: Lifecycle

    @Test func resumeAfterElapsedBeyondTimeoutLocks() {
        let (lock, _) = makeLock(
            biometrics: FakeBiometrics(available: true, authResult: true),
            storedEnabled: "true",
            storedTimeout: "60",
            authenticated: true
        )
        lock.state = lock.state.copyWith(enabled: true, locked: false)
        lock.pausedAt = Date().addingTimeInterval(-120)
        lock.resumed()
        #expect(lock.locked)
    }

    @Test func resumeWithinTimeoutDoesNotLock() {
        let (lock, _) = makeLock(
            biometrics: FakeBiometrics(available: true, authResult: true),
            storedEnabled: "true",
            storedTimeout: "300",
            authenticated: true
        )
        lock.state = lock.state.copyWith(enabled: true, locked: false)
        lock.pausedAt = Date().addingTimeInterval(-30)
        lock.resumed()
        #expect(!lock.locked)
    }

    @Test func immediatelyTimeoutAlwaysLocksOnResume() {
        let (lock, _) = makeLock(
            biometrics: FakeBiometrics(available: true, authResult: true),
            storedEnabled: "true",
            storedTimeout: "0",
            authenticated: true
        )
        lock.state = lock.state.copyWith(enabled: true, locked: false)
        lock.pausedAt = Date().addingTimeInterval(-1)
        lock.resumed()
        #expect(lock.locked)
    }

    /// Logging out keeps the preference, so `enabled` is still true on Login;
    /// backgrounding there must not raise the gate over the login screen.
    @Test func resumeWithNoSessionDoesNotLock() {
        let (lock, _) = makeLock(
            biometrics: FakeBiometrics(available: true, authResult: true),
            storedEnabled: "true",
            storedTimeout: "0",
            authenticated: false
        )
        lock.state = lock.state.copyWith(enabled: true, locked: false)
        lock.pausedAt = Date().addingTimeInterval(-120)
        lock.resumed()
        #expect(!lock.locked)
        #expect(!lock.shouldGate)
    }

    @Test func releaseLockClearsTheLockAndKeepsThePreference() {
        let (lock, _) = makeLock(
            biometrics: FakeBiometrics(available: true, authResult: true),
            storedEnabled: "true",
            storedTimeout: "0",
            authenticated: false
        )
        lock.state = lock.state.copyWith(enabled: true, locked: true)
        lock.releaseLock()
        #expect(!lock.locked)
        // The preference survives, so the next real sign-in is still protected.
        #expect(lock.enabled)
    }

    @Test func noBiometricsEnableFailsAndResumeNeverLocks() async {
        let (lock, _) = makeLock(biometrics: FakeBiometrics(available: false, authResult: false))
        let ok = await lock.enable()
        #expect(!ok)
        #expect(!lock.enabled)
        lock.pausedAt = Date().addingTimeInterval(-600)
        lock.resumed()
        #expect(!lock.locked)
    }

    @Test func resumeWithoutAPauseDoesNotLock() {
        let (lock, _) = makeLock(
            biometrics: FakeBiometrics(available: true, authResult: true),
            storedEnabled: "true",
            storedTimeout: "0",
            authenticated: true
        )
        lock.state = lock.state.copyWith(locked: false)
        lock.resumed()
        #expect(!lock.locked)
    }

    // MARK: Scene phases (native)

    /// iOS returns `.background` → `.inactive` → `.active`. The absence is
    /// measured from when the app first left `.active`, so a 1-minute
    /// timeout locks after two minutes away.
    @Test func sceneSequenceAfterTwoMinutesLocksAOneMinuteTimeout() {
        var clock = Date(timeIntervalSince1970: 1_000_000)
        let (lock, _) = makeLock(
            biometrics: FakeBiometrics(available: true, authResult: true),
            storedEnabled: "true",
            storedTimeout: "60",
            authenticated: true,
            now: { clock }
        )
        lock.state = lock.state.copyWith(locked: false)
        lock.handle(.inactive)
        lock.handle(.background)
        clock += 120
        lock.handle(.inactive)
        lock.handle(.active)
        #expect(lock.locked)
        #expect(lock.pausedAt == nil)
    }

    @Test func sceneSequenceWithinTimeoutStaysUnlocked() {
        var clock = Date(timeIntervalSince1970: 1_000_000)
        let (lock, _) = makeLock(
            biometrics: FakeBiometrics(available: true, authResult: true),
            storedEnabled: "true",
            storedTimeout: "300",
            authenticated: true,
            now: { clock }
        )
        lock.state = lock.state.copyWith(locked: false)
        lock.handle(.inactive)
        lock.handle(.background)
        clock += 240
        lock.handle(.inactive)
        lock.handle(.active)
        #expect(!lock.locked)
    }

    /// Pulling down Control Center (`.inactive` → `.active`) with
    /// "Immediately" locks, as Flutter's inactive → resumed did.
    @Test func inactiveOnlyWithImmediatelyLocks() {
        let (lock, _) = makeLock(
            biometrics: FakeBiometrics(available: true, authResult: true),
            storedEnabled: "true",
            storedTimeout: "0",
            authenticated: true
        )
        lock.state = lock.state.copyWith(locked: false)
        lock.handle(.inactive)
        lock.handle(.active)
        #expect(lock.locked)
    }

    /// A trailing `.active` after the Face ID sheet must not re-lock.
    @Test func successfulUnlockClearsPausedAt() async {
        let (lock, _) = makeLock(
            biometrics: FakeBiometrics(available: true, authResult: true),
            storedEnabled: "true",
            storedTimeout: "0",
            authenticated: true
        )
        #expect(lock.locked)
        lock.handle(.inactive) // the Face ID sheet
        let ok = await lock.unlock()
        lock.handle(.active)
        #expect(ok)
        #expect(!lock.locked)
    }

    @Test func failedUnlockStaysLocked() async {
        let bio = FakeBiometrics(available: true, authResult: false)
        let (lock, _) = makeLock(biometrics: bio, storedEnabled: "true", authenticated: true)
        let ok = await lock.unlock()
        #expect(!ok)
        #expect(lock.locked)
        #expect(bio.reasons == ["Unlock IntelliStock to access your portfolio"])
    }

    /// Enabling from Settings backgrounds the scene for the prompt; that must
    /// not lock the app the moment it comes back.
    @Test func enablingDoesNotLockOnTheTrailingResume() async {
        let (lock, _) = makeLock(
            biometrics: FakeBiometrics(available: true, authResult: true),
            storedTimeout: "0",
            authenticated: true
        )
        lock.handle(.inactive)
        await lock.enable()
        lock.handle(.active)
        #expect(lock.enabled)
        #expect(!lock.locked)
    }

    // MARK: Persistence

    @Test func disablePersistsFalseAndUnlocks() async {
        let (lock, storage) = makeLock(
            biometrics: FakeBiometrics(available: true, authResult: true),
            storedEnabled: "true",
            authenticated: true
        )
        await lock.disable()
        #expect(!lock.enabled)
        #expect(!lock.locked)
        #expect(storage.read(AppLock.enabledKey) == "false")
    }

    @Test func setTimeoutPersistsSeconds() async {
        let (lock, storage) = makeLock(biometrics: FakeBiometrics(available: true, authResult: true))
        await lock.setTimeout(.oneMinute)
        #expect(lock.timeout == .oneMinute)
        #expect(storage.read(AppLock.timeoutKey) == "60")
    }
}

struct LockTimeoutTests {
    @Test func labelsAndStorageStrings() {
        #expect(LockTimeout.immediately.label == "Immediately")
        #expect(LockTimeout.oneMinute.label == "1 minute")
        #expect(LockTimeout.fiveMinutes.label == "5 minutes")
        #expect(LockTimeout.allCases.map { $0.toStorageString() } == ["0", "60", "300"])
    }

    @Test func fromStorageStringFallsBackToImmediately() {
        #expect(LockTimeout.fromStorageString("60") == .oneMinute)
        #expect(LockTimeout.fromStorageString("300") == .fiveMinutes)
        #expect(LockTimeout.fromStorageString("0") == .immediately)
        #expect(LockTimeout.fromStorageString("42") == .immediately)
        #expect(LockTimeout.fromStorageString("abc") == .immediately)
        #expect(LockTimeout.fromStorageString(nil) == .immediately)
    }
}
