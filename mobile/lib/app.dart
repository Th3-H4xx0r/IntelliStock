import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'core/router/router.dart';
import 'core/theme/app_theme.dart';
import 'core/lock/app_lock_controller.dart';
import 'core/lock/lock_screen.dart';
import 'features/chatbot/presentation/chatbot_dock.dart';

class IntelliStockApp extends ConsumerWidget {
  const IntelliStockApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final router = ref.watch(goRouterProvider);
    return MaterialApp.router(
      title: 'IntelliStock',
      debugShowCheckedModeBanner: false,
      theme: buildAppTheme(),
      themeMode: ThemeMode.dark,
      routerConfig: router,
      builder: (context, child) {
        // Biometric lock gate covers everything when locked.
        final locked = ref.watch(appLockControllerProvider).locked;
        if (locked) return const LockScreen();
        // Global chatbot dock overlays the whole authenticated app
        // (self-hides when not authenticated).
        return Stack(
          children: [
            if (child != null) child,
            const ChatbotDock(),
          ],
        );
      },
    );
  }
}
