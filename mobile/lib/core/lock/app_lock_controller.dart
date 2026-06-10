import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import '../network/session.dart';
import 'biometric_service.dart';

// ── Storage keys ──────────────────────────────────────────────────────────────

const _kLockEnabled = 'biometric_lock_enabled';
const _kLockTimeout = 'lock_timeout';

// ── Timeout options ───────────────────────────────────────────────────────────

/// Canonical auto-lock timeout options.
enum LockTimeout {
  immediately(Duration.zero),
  oneMinute(Duration(minutes: 1)),
  fiveMinutes(Duration(minutes: 5));

  const LockTimeout(this.duration);

  final Duration duration;

  String get label {
    switch (this) {
      case LockTimeout.immediately:
        return 'Immediately';
      case LockTimeout.oneMinute:
        return '1 minute';
      case LockTimeout.fiveMinutes:
        return '5 minutes';
    }
  }

  /// Serialise to the string persisted in secure storage.
  String toStorageString() => duration.inSeconds.toString();

  static LockTimeout fromStorageString(String? s) {
    final seconds = int.tryParse(s ?? '') ?? 0;
    for (final opt in LockTimeout.values) {
      if (opt.duration.inSeconds == seconds) return opt;
    }
    return LockTimeout.immediately;
  }
}

// ── State ─────────────────────────────────────────────────────────────────────

class AppLockState {
  const AppLockState({
    this.enabled = false,
    this.locked = false,
    this.timeout = LockTimeout.immediately,
  });

  final bool enabled;
  final bool locked;
  final LockTimeout timeout;

  AppLockState copyWith({
    bool? enabled,
    bool? locked,
    LockTimeout? timeout,
  }) =>
      AppLockState(
        enabled: enabled ?? this.enabled,
        locked: locked ?? this.locked,
        timeout: timeout ?? this.timeout,
      );
}

// ── Controller ────────────────────────────────────────────────────────────────

/// Manages app biometric lock lifecycle.
///
/// Reads/writes [enabled] and [timeout] from [FlutterSecureStorage].
/// Implements [WidgetsBindingObserver] to detect app background → foreground
/// transitions and lock automatically when the elapsed time exceeds [timeout].
class AppLockController extends Notifier<AppLockState>
    with WidgetsBindingObserver {
  /// Exposed as `@visibleForTesting` so unit tests can inject a custom pause
  /// time without relying on real wall-clock delays.
  @visibleForTesting
  DateTime? pausedAt;

  FlutterSecureStorage get _storage => ref.read(secureStorageProvider);
  BiometricService get _biometrics => ref.read(biometricServiceProvider);
  SessionStore get _session => ref.read(sessionProvider);

  @override
  AppLockState build() {
    WidgetsBinding.instance.addObserver(this);
    ref.onDispose(() => WidgetsBinding.instance.removeObserver(this));
    _init();
    return const AppLockState();
  }

  // ── Lifecycle ──────────────────────────────────────────────────────────────

  Future<void> _init() async {
    final enabledStr = await _storage.read(key: _kLockEnabled);
    final timeoutStr = await _storage.read(key: _kLockTimeout);
    final enabled = enabledStr == 'true';
    final timeout = LockTimeout.fromStorageString(timeoutStr);

    // Lock immediately on start if enabled and the user is authenticated.
    final shouldLock = enabled && _session.isAuthenticated;
    state = AppLockState(
      enabled: enabled,
      locked: shouldLock,
      timeout: timeout,
    );
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState lifecycle) {
    if (lifecycle == AppLifecycleState.paused) {
      pausedAt = DateTime.now();
    } else if (lifecycle == AppLifecycleState.resumed) {
      if (!state.enabled) return;
      final paused = pausedAt;
      if (paused == null) {
        // No recorded pause time — lock to be safe.
        state = state.copyWith(locked: true);
        return;
      }
      final elapsed = DateTime.now().difference(paused);
      final timeout = state.timeout.duration;
      // Duration.zero means "immediately", so any elapsed time triggers lock.
      if (timeout == Duration.zero || elapsed >= timeout) {
        state = state.copyWith(locked: true);
      }
      pausedAt = null;
    }
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /// Presents biometric prompt; unlocks on success.
  Future<bool> unlock() async {
    final ok = await _biometrics.authenticate(
      'Unlock IntelliStock to access your portfolio',
    );
    if (ok) {
      state = state.copyWith(locked: false);
    }
    return ok;
  }

  /// Enable biometric lock. Requires a successful auth first.
  /// Returns false if auth fails or biometrics are unavailable.
  Future<bool> enable() async {
    final canUse = await _biometrics.canCheck();
    if (!canUse) return false;

    final ok = await _biometrics.authenticate(
      'Authenticate to enable biometric lock',
    );
    if (!ok) return false;

    await _storage.write(key: _kLockEnabled, value: 'true');
    state = state.copyWith(enabled: true);
    return true;
  }

  /// Disable biometric lock and persist the preference.
  Future<void> disable() async {
    await _storage.write(key: _kLockEnabled, value: 'false');
    state = state.copyWith(enabled: false, locked: false);
  }

  /// Change the auto-lock timeout and persist.
  Future<void> setTimeout(LockTimeout timeout) async {
    await _storage.write(
        key: _kLockTimeout, value: timeout.toStorageString());
    state = state.copyWith(timeout: timeout);
  }
}

final appLockControllerProvider =
    NotifierProvider<AppLockController, AppLockState>(
  AppLockController.new,
);
