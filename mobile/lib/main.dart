import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'app.dart';
import 'core/network/api_base_url.dart';
import 'core/network/session.dart';
import 'core/lock/app_lock_controller.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Read persisted auth + lock state DIRECTLY from secure storage before the
  // ProviderContainer is created, so the very first frame already reflects the
  // correct lock state (no async gap / cold-launch flash of unprotected UI).
  // Container overrides must be supplied at construction, hence the direct read.
  const storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );
  final token = await storage.read(key: 'intellistock_token');
  final lockEnabled = (await storage.read(key: 'biometric_lock_enabled')) == 'true';
  final lockTimeout =
      LockTimeout.fromStorageString(await storage.read(key: 'lock_timeout'));
  final isAuthed = token != null && token.isNotEmpty;
  final seedState = AppLockState(
    enabled: lockEnabled,
    locked: lockEnabled && isAuthed,
    timeout: lockTimeout,
  );

  final container = ProviderContainer(
    overrides: [appLockSeedProvider.overrideWithValue(seedState)],
  );
  // Load the configured API base URL first so the very first router redirect
  // sees the correct configured/unconfigured state and the session's widget
  // sync mirrors the right URL.
  await container.read(apiBaseUrlProvider).load();
  // Populate the in-memory SessionStore for the router's initial redirect.
  await container.read(sessionProvider).load();

  runApp(
    UncontrolledProviderScope(
      container: container,
      child: const IntelliStockApp(),
    ),
  );
}
