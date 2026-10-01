import Foundation
import LocalAuthentication

/// The biometric kinds `local_auth` reports on iOS.
nonisolated enum BiometricType: Hashable, Sendable {
    case face
    case fingerprint
}

/// The three operations the lock layer needs — `BiometricService` in
/// `biometric_service.dart`. Tests inject a fake.
protocol BiometricAuthenticating: AnyObject {
    /// Device supports owner authentication AND has biometric hardware
    /// (enrolled or not), as `local_auth`'s `isDeviceSupported() &&
    /// canCheckBiometrics`.
    func canCheck() async -> Bool
    /// Enrolled biometric types.
    func availableTypes() async -> [BiometricType]
    /// Presents the system prompt with `reason`; true on success. Falls back
    /// to the passcode (`biometricOnly: false`).
    func authenticate(_ reason: String) async -> Bool
}

/// `LocalAuthentication` implementation, ported from `local_auth_darwin`'s
/// checks so the same devices qualify.
final class BiometricService: BiometricAuthenticating {
    func canCheck() async -> Bool {
        isDeviceSupported() && deviceCanSupportBiometrics()
    }

    func availableTypes() async -> [BiometricType] {
        let context = LAContext()
        guard context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: nil) else { return [] }
        switch context.biometryType {
        case .faceID: return [.face]
        case .touchID: return [.fingerprint]
        default: return []
        }
    }

    func authenticate(_ reason: String) async -> Bool {
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else { return false }
        do {
            return try await context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason)
        } catch {
            return false
        }
    }

    /// `isDeviceSupported`: owner authentication (biometrics or passcode).
    private func isDeviceSupported() -> Bool {
        LAContext().canEvaluatePolicy(.deviceOwnerAuthentication, error: nil)
    }

    /// `deviceCanSupportBiometrics`: biometrics usable, or present but not
    /// enrolled, or present but permission denied.
    private func deviceCanSupportBiometrics() -> Bool {
        let context = LAContext()
        var error: NSError?
        if context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &error), error == nil {
            return true
        }
        if let error {
            if error.code == LAError.biometryNotEnrolled.rawValue { return true }
            if error.code == LAError.biometryNotAvailable.rawValue, context.biometryType != .none { return true }
        }
        return false
    }
}
