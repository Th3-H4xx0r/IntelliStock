import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:local_auth/local_auth.dart';

/// Wraps [LocalAuthentication] and exposes the three operations the lock layer
/// needs: device support check, available biometric types, and authenticate.
class BiometricService {
  BiometricService(this._auth);

  final LocalAuthentication _auth;

  /// Returns true if the device supports biometrics AND has at least one
  /// enrolled credential (fingerprint, face, etc.).
  Future<bool> canCheck() async {
    try {
      final supported = await _auth.isDeviceSupported();
      if (!supported) return false;
      final enrolled = await _auth.canCheckBiometrics;
      return enrolled;
    } catch (_) {
      return false;
    }
  }

  /// Returns the list of enrolled biometric types (face, fingerprint, iris).
  Future<List<BiometricType>> availableTypes() async {
    try {
      return await _auth.getAvailableBiometrics();
    } catch (_) {
      return [];
    }
  }

  /// Presents the system biometric prompt with [reason].
  /// Returns true if authentication succeeded.
  Future<bool> authenticate(String reason) async {
    try {
      return await _auth.authenticate(
        localizedReason: reason,
        options: const AuthenticationOptions(
          stickyAuth: true,
          biometricOnly: false,
        ),
      );
    } catch (_) {
      return false;
    }
  }
}

final biometricServiceProvider = Provider<BiometricService>(
  (ref) => BiometricService(LocalAuthentication()),
);
