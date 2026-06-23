import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../network/session.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import '../widgets/material_symbols.dart';

/// The "More" bottom sheet — overflow destinations + account actions.
void showMoreSheet(BuildContext context) {
  showModalBottomSheet(
    context: context,
    backgroundColor: AppColors.panel,
    showDragHandle: true,
    builder: (_) => const _MoreSheet(),
  );
}

class _MoreSheet extends ConsumerWidget {
  const _MoreSheet();

  static const _items = [
    ('analytics', 'Backtests', '/backtests'),
    ('account_balance', 'Brokerages', '/brokerages'),
    ('smart_toy', 'Agent Runs', '/agent-runs'),
    ('hub', 'Nexus Graph', '/nexus'),
    ('psychology', 'Models', '/models'),
    ('payments', 'Token Usage', '/token-usage'),
    ('settings', 'Settings', '/settings'),
  ];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final session = ref.watch(sessionProvider);
    return SafeArea(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          for (final item in _items)
            ListTile(
              leading: Icon(symbol(item.$1), color: AppColors.textMuted),
              title: Text(item.$2, style: AppTextStyles.bodyHi),
              onTap: () {
                Navigator.pop(context);
                context.push(item.$3);
              },
            ),
          const Divider(height: 1, color: AppColors.border),
          ListTile(
            leading: const CircleAvatar(
              radius: 16,
              backgroundColor: AppColors.surface,
              child: Icon(Icons.person_outline,
                  size: 18, color: AppColors.textMuted),
            ),
            title: Text(session.username, style: AppTextStyles.bodyHi),
            trailing: IconButton(
              icon: Icon(symbol('logout'), color: AppColors.textDim),
              onPressed: () async {
                Navigator.pop(context);
                await session.clear();
              },
            ),
          ),
        ],
      ),
    );
  }
}
