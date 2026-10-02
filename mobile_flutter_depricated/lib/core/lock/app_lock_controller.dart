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

  AppLockState copyWith({bool? enabled, bool? locked, LockTimeout? timeout}) =>
      AppLockState(
        enabled: enabled ?? this.enabled,
        locked: locked ?? this.locked,
        timeout: timeout ?? this.timeout,
      );
}

// ── Seed provider (overridden in main() before runApp) ────────────────────────

/// Holds the lock state pre-computed in [main] so the first frame rendered by
/// [AppLockController] is already correct (no async gap / cold-launch flash).
///
/// Override this provider on the root [ProviderContainer] before [runApp]:
///   container.updateOverrides([
///     appLockSeedProvider.overrideWithValue(AppLockState(...)),
///   ]);
final appLockSeedProvider = Provider<AppLockState>((_) => const AppLockState());

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

  @override
  AppLockState build() {
    WidgetsBinding.instance.addObserver(this);
    ref.onDispose(() => WidgetsBinding.instance.removeObserver(this));
    // Use the seed state pre-computed in main() — this is the synchronous first
    // state so the lock gate is correct with no async gap.
    return ref.read(appLockSeedProvider);
  }

  // ── Lifecycle ──────────────────────────────────────────────────────────────

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Record pausedAt on BOTH inactive AND paused so iOS (which fires
    // inactive→resumed without a paused event during a biometric prompt)
    // does not see a null pausedAt after unlock, which would cause an
    // immediate re-lock.
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.inactive) {
      pausedAt = DateTime.now();
    } else if (state == AppLifecycleState.resumed) {
      if (!this.state.enabled) return;
      // The lock protects a SESSION. Logging out deliberately keeps the
      // preference, so `enabled` stays true on /login — without this the
      // gate would raise itself over the login screen and demand Face ID
      // from someone who is not signed in.
      if (!ref.read(sessionProvider).isAuthenticated) return;
      final paused = pausedAt;
      // pausedAt == null means we never backgrounded (e.g. trailing resumed
      // after a successful unlock cleared it) — do NOT lock in that case.
      if (paused == null) return;
      final elapsed = DateTime.now().difference(paused);
      final timeout = this.state.timeout.duration;
      // Duration.zero means "immediately", so any elapsed time triggers lock.
      if (timeout == Duration.zero || elapsed >= timeout) {
        this.state = this.state.copyWith(locked: true);
      }
      pausedAt = null;
    }
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /// Whether the gate should actually be raised right now: the user asked
  /// for it, the app locked, and there is a session behind it to protect.
  bool get shouldGate =>
      state.locked && ref.read(sessionProvider).isAuthenticated;

  /// Presents biometric prompt; unlocks on success.
  Future<bool> unlock() async {
    final ok = await _biometrics.authenticate(
      'Unlock IntelliStock to access your portfolio',
    );
    if (ok) {
      state = state.copyWith(locked: false);
      // Clear pausedAt so a trailing `resumed` lifecycle event (common on iOS
      // after the biometric prompt) does not immediately re-lock the app.
      pausedAt = null;
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

  /// Opens the gate WITHOUT changing the user's lock setting — used when the
  /// user chooses "log in with password" / "log out" from the lock screen, so
  /// the router can show /login while biometric lock stays enabled for next
  /// launch.
  void releaseLock() {
    state = state.copyWith(locked: false);
    pausedAt = null;
  }

  /// Change the auto-lock timeout and persist.
  Future<void> setTimeout(LockTimeout timeout) async {
    await _storage.write(key: _kLockTimeout, value: timeout.toStorageString());
    state = state.copyWith(timeout: timeout);
  }
}

final appLockControllerProvider =
    NotifierProvider<AppLockController, AppLockState>(AppLockController.new);
