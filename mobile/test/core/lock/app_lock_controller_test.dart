import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:local_auth/local_auth.dart';

import 'package:intellistock_mobile/core/lock/app_lock_controller.dart';
import 'package:intellistock_mobile/core/lock/biometric_service.dart';
import 'package:intellistock_mobile/core/network/session.dart';

// ── Fake biometric service ─────────────────────────────────────────────────────

class FakeBiometricService extends BiometricService {
  FakeBiometricService({
    required this.available,
    required this.authResult,
  }) : super(LocalAuthentication());

  final bool available;
  bool authResult;

  @override
  Future<bool> canCheck() async => available;

  @override
  Future<List<BiometricType>> availableTypes() async =>
      available ? [BiometricType.face] : [];

  @override
  Future<bool> authenticate(String reason) async => authResult;
}

// ── Fake FlutterSecureStorage ─────────────────────────────────────────────────

class FakeSecureStorage extends FlutterSecureStorage {
  FakeSecureStorage([Map<String, String>? initial]) : super() {
    if (initial != null) _store.addAll(initial);
  }

  final Map<String, String> _store = {};

  @override
  Future<void> write({
    required String key,
    required String? value,
    IOSOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    MacOsOptions? mOptions,
    WindowsOptions? wOptions,
  }) async {
    if (value == null) {
      _store.remove(key);
    } else {
      _store[key] = value;
    }
  }

  @override
  Future<String?> read({
    required String key,
    IOSOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    MacOsOptions? mOptions,
    WindowsOptions? wOptions,
  }) async =>
      _store[key];

  @override
  Future<void> delete({
    required String key,
    IOSOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    MacOsOptions? mOptions,
    WindowsOptions? wOptions,
  }) async =>
      _store.remove(key);
}

// ── Fake session that is always authenticated ─────────────────────────────────

class _FakeAuthenticatedSession extends SessionStore {
  _FakeAuthenticatedSession() : super(FakeSecureStorage());

  @override
  bool get isAuthenticated => true;

  @override
  String get username => 'testuser';
}

class _FakeUnauthenticatedSession extends SessionStore {
  _FakeUnauthenticatedSession() : super(FakeSecureStorage());

  @override
  bool get isAuthenticated => false;
}

// ── Helper ────────────────────────────────────────────────────────────────────

ProviderContainer _makeContainer({
  required FakeBiometricService biometrics,
  String? storedEnabled,
  String? storedTimeout,
  bool authenticated = false,
}) {
  final initial = <String, String>{};
  if (storedEnabled != null) initial['biometric_lock_enabled'] = storedEnabled;
  if (storedTimeout != null) initial['lock_timeout'] = storedTimeout;

  final storage = FakeSecureStorage(initial);

  // Mirror what main() does: seed the lock state from persisted prefs so the
  // controller's first state is correct (the controller reads appLockSeedProvider).
  final seed = AppLockState(
    enabled: storedEnabled == 'true',
    locked: storedEnabled == 'true' && authenticated,
    timeout: LockTimeout.fromStorageString(storedTimeout),
  );

  return ProviderContainer(
    overrides: [
      biometricServiceProvider.overrideWithValue(biometrics),
      secureStorageProvider.overrideWithValue(storage),
      appLockSeedProvider.overrideWithValue(seed),
      sessionProvider.overrideWith(
        (ref) => authenticated
            ? _FakeAuthenticatedSession()
            : _FakeUnauthenticatedSession(),
      ),
    ],
  );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

void main() {
  WidgetsFlutterBinding.ensureInitialized();

  // ── enable() requires auth ─────────────────────────────────────────────────

  group('enable()', () {
    test('returns true and sets enabled=true when auth succeeds', () async {
      final bio = FakeBiometricService(available: true, authResult: true);
      final container = _makeContainer(biometrics: bio);
      addTearDown(container.dispose);

      // Wait for _init to complete.
      await Future.delayed(const Duration(milliseconds: 50));

      final ok = await container
          .read(appLockControllerProvider.notifier)
          .enable();

      expect(ok, isTrue);
      expect(container.read(appLockControllerProvider).enabled, isTrue);
    });

    test('returns false and leaves enabled=false when auth fails', () async {
      final bio = FakeBiometricService(available: true, authResult: false);
      final container = _makeContainer(biometrics: bio);
      addTearDown(container.dispose);

      await Future.delayed(const Duration(milliseconds: 50));

      final ok = await container
          .read(appLockControllerProvider.notifier)
          .enable();

      expect(ok, isFalse);
      expect(container.read(appLockControllerProvider).enabled, isFalse);
    });

    test('returns false when biometrics are unavailable', () async {
      final bio = FakeBiometricService(available: false, authResult: true);
      final container = _makeContainer(biometrics: bio);
      addTearDown(container.dispose);

      await Future.delayed(const Duration(milliseconds: 50));

      final ok = await container
          .read(appLockControllerProvider.notifier)
          .enable();

      expect(ok, isFalse);
      expect(container.read(appLockControllerProvider).enabled, isFalse);
    });
  });

  // ── App start behaviour ────────────────────────────────────────────────────

  group('app start', () {
    test('locks on start when enabled=true and session is authenticated',
        () async {
      final bio = FakeBiometricService(available: true, authResult: true);
      final container = _makeContainer(
        biometrics: bio,
        storedEnabled: 'true',
        authenticated: true,
      );
      addTearDown(container.dispose);

      // Read the provider to trigger build().
      container.read(appLockControllerProvider);
      await Future.delayed(const Duration(milliseconds: 50));

      expect(container.read(appLockControllerProvider).locked, isTrue);
    });

    test('does not lock on start when enabled=false', () async {
      final bio = FakeBiometricService(available: true, authResult: true);
      final container = _makeContainer(
        biometrics: bio,
        storedEnabled: 'false',
        authenticated: true,
      );
      addTearDown(container.dispose);

      container.read(appLockControllerProvider);
      await Future.delayed(const Duration(milliseconds: 50));

      expect(container.read(appLockControllerProvider).locked, isFalse);
    });
  });

  // ── resume after timeout ───────────────────────────────────────────────────

  group('lifecycle', () {
    test('resumes after elapsed > timeout → locks', () async {
      final bio = FakeBiometricService(available: true, authResult: true);
      final container = _makeContainer(
        biometrics: bio,
        storedEnabled: 'true',
        storedTimeout: '60', // 1 minute
      );
      addTearDown(container.dispose);

      container.read(appLockControllerProvider);
      await Future.delayed(const Duration(milliseconds: 50));

      final notifier = container.read(appLockControllerProvider.notifier);

      // Manually set enabled (avoids dependency on _init authenticated state).
      notifier.state = notifier.state.copyWith(enabled: true, locked: false);

      // Simulate app going to background 2 minutes ago.
      notifier.pausedAt = DateTime.now().subtract(const Duration(minutes: 2));

      // Simulate foreground.
      notifier.didChangeAppLifecycleState(AppLifecycleState.resumed);

      expect(container.read(appLockControllerProvider).locked, isTrue);
    });

    test('resumes within timeout → does NOT lock', () async {
      final bio = FakeBiometricService(available: true, authResult: true);
      final container = _makeContainer(
        biometrics: bio,
        storedEnabled: 'true',
        storedTimeout: '300', // 5 minutes
      );
      addTearDown(container.dispose);

      container.read(appLockControllerProvider);
      await Future.delayed(const Duration(milliseconds: 50));

      final notifier = container.read(appLockControllerProvider.notifier);
      notifier.state = notifier.state.copyWith(locked: false, enabled: true);

      // Simulate background 30 seconds ago (within 5-minute window).
      notifier.pausedAt =
          DateTime.now().subtract(const Duration(seconds: 30));

      notifier.didChangeAppLifecycleState(AppLifecycleState.resumed);

      expect(container.read(appLockControllerProvider).locked, isFalse);
    });

    test('immediately timeout always locks on resume', () async {
      final bio = FakeBiometricService(available: true, authResult: true);
      final container = _makeContainer(
        biometrics: bio,
        storedEnabled: 'true',
        storedTimeout: '0', // Immediately
      );
      addTearDown(container.dispose);

      container.read(appLockControllerProvider);
      await Future.delayed(const Duration(milliseconds: 50));

      final notifier = container.read(appLockControllerProvider.notifier);
      notifier.state = notifier.state.copyWith(locked: false, enabled: true);

      // Even 1 second is enough with "immediately".
      notifier.pausedAt = DateTime.now().subtract(const Duration(seconds: 1));
      notifier.didChangeAppLifecycleState(AppLifecycleState.resumed);

      expect(container.read(appLockControllerProvider).locked, isTrue);
    });

    test('no-biometrics: enable() returns false and lock stays disabled',
        () async {
      final bio = FakeBiometricService(available: false, authResult: false);
      final container = _makeContainer(biometrics: bio);
      addTearDown(container.dispose);

      container.read(appLockControllerProvider);
      await Future.delayed(const Duration(milliseconds: 50));

      final ok = await container
          .read(appLockControllerProvider.notifier)
          .enable();

      expect(ok, isFalse);
      expect(container.read(appLockControllerProvider).enabled, isFalse);

      // Since lock is not enabled, resume should NOT lock.
      final notifier = container.read(appLockControllerProvider.notifier);
      notifier.pausedAt =
          DateTime.now().subtract(const Duration(minutes: 10));
      notifier.didChangeAppLifecycleState(AppLifecycleState.resumed);

      expect(container.read(appLockControllerProvider).locked, isFalse);
    });
  });
}
