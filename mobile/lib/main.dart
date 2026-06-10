import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'app.dart';
import 'core/network/session.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  final container = ProviderContainer();
  // Load any persisted session before the first frame so the router's initial
  // redirect sees the right auth state.
  await container.read(sessionProvider).load();

  runApp(
    UncontrolledProviderScope(
      container: container,
      child: const IntelliStockApp(),
    ),
  );
}
