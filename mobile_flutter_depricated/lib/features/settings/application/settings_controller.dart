import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:package_info_plus/package_info_plus.dart';
import '../../../core/lock/app_lock_controller.dart';

/// Thin controller that exposes:
///  • lock prefs (delegates entirely to [AppLockController])
///  • app version from [PackageInfo]
///
/// The lock toggle / timeout picker in SettingsScreen read and write through
/// this controller rather than directly touching AppLockController so the
/// Settings feature layer has one clean seam.
class SettingsController {
  const SettingsController(this._ref);

  final Ref _ref;

  AppLockState get lockState => _ref.read(appLockControllerProvider);

  /// Enable biometric lock (triggers auth prompt internally).
  /// Returns false if auth fails or biometrics are unavailable.
  Future<bool> enableLock() =>
      _ref.read(appLockControllerProvider.notifier).enable();

  /// Disable biometric lock.
  Future<void> disableLock() =>
      _ref.read(appLockControllerProvider.notifier).disable();

  /// Change auto-lock timeout.
  Future<void> setLockTimeout(LockTimeout timeout) =>
      _ref.read(appLockControllerProvider.notifier).setTimeout(timeout);
}

final settingsControllerProvider = Provider<SettingsController>(
  (ref) => SettingsController(ref),
);

/// Exposes the app's version + build number from package_info_plus.
///
/// Loaded once; cached for the lifetime of the provider.
final appVersionProvider = FutureProvider<String>((ref) async {
  final info = await PackageInfo.fromPlatform();
  final build = info.buildNumber.isNotEmpty ? '+${info.buildNumber}' : '';
  return '${info.version}$build';
});
