import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import '../theme/app_colors.dart';
import '../widgets/app_background.dart';
import '../widgets/material_symbols.dart';
import 'more_sheet.dart';

/// The authenticated app shell: a 5-tab bottom NavigationBar + a global
/// chatbot FAB, over the app background. Detail/fullscreen routes push over
/// this shell (no bottom bar), matching the web's `fullscreenMode`.
class AppShell extends StatelessWidget {
  const AppShell({super.key, required this.navigationShell});

  final StatefulNavigationShell navigationShell;

  static const _destinations = [
    (_DestKind.branch, 'dashboard', 'Dashboard', 0),
    (_DestKind.branch, 'memory', 'Instances', 1),
    (_DestKind.branch, 'analytics', 'Backtests', 2),
    (_DestKind.branch, 'schema', 'Strategies', 3),
    (_DestKind.more, 'menu', 'More', 4),
  ];

  void _onTap(BuildContext context, int index) {
    if (index == 4) {
      showMoreSheet(context);
      return;
    }
    navigationShell.goBranch(
      index,
      initialLocation: index == navigationShell.currentIndex,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.canvas,
      extendBody: true,
      body: AppBackground(
        child: SafeArea(bottom: false, child: navigationShell),
      ),
      floatingActionButton: const ChatbotFabSlot(),
      bottomNavigationBar: NavigationBar(
        selectedIndex:
            navigationShell.currentIndex.clamp(0, _destinations.length - 1),
        onDestinationSelected: (i) => _onTap(context, i),
        destinations: [
          for (final d in _destinations)
            NavigationDestination(
              icon: Icon(symbol(d.$2)),
              label: d.$3,
            ),
        ],
      ),
    );
  }
}

enum _DestKind { branch, more }

/// Placeholder for the global chatbot FAB; the chatbot feature swaps this in.
class ChatbotFabSlot extends StatelessWidget {
  const ChatbotFabSlot({super.key});

  @override
  Widget build(BuildContext context) => const SizedBox.shrink();
}
