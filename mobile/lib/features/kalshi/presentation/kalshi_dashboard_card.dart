import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/theme/app_colors.dart';
import '../../dashboard/application/dashboard_controller.dart';
import '../../dashboard/data/dashboard_repository.dart';
import '../data/kalshi_repository.dart';

/// Compact Kalshi glance card for the dashboard. Self-hides when no Kalshi
/// account is linked. Tapping opens the Kalshi tab.
class KalshiDashboardCard extends ConsumerWidget {
  const KalshiDashboardCard({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final accts = ref.watch(brokeragesProvider).value ?? const <BrokerageAccount>[];
    final kalshi = accts.where((a) => a.brokerageType == 'kalshi').toList();
    if (kalshi.isEmpty) return const SizedBox.shrink();

    final bid = kalshi.first.id;
    final async = ref.watch(kalshiPortfolioProvider(bid));

    return GestureDetector(
      onTap: () => context.go('/kalshi'),
      child: Container(
        margin: const EdgeInsets.only(top: 12),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: const Color(0xFF111C30),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: const Color(0xFF0E7490).withValues(alpha: 0.6)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: const [
                Text('⚽ Kalshi portfolio',
                    style: TextStyle(color: AppColors.info, fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 0.4)),
                Text('Open →', style: TextStyle(color: AppColors.info, fontSize: 10, fontWeight: FontWeight.bold)),
              ],
            ),
            const SizedBox(height: 6),
            async.when(
              loading: () => const Text('Loading…', style: TextStyle(color: AppColors.textDim, fontSize: 13)),
              error: (e, _) => const Text('—', style: TextStyle(color: AppColors.textDim, fontSize: 20, fontWeight: FontWeight.w800)),
              data: (p) {
                final positive = p.dayChange >= 0;
                return Row(
                  crossAxisAlignment: CrossAxisAlignment.baseline,
                  textBaseline: TextBaseline.alphabetic,
                  children: [
                    Text('\$${p.value.toStringAsFixed(2)}',
                        style: const TextStyle(color: AppColors.textHi, fontSize: 22, fontWeight: FontWeight.w800)),
                    const SizedBox(width: 8),
                    Text('${positive ? '▲' : '▼'} \$${p.dayChange.abs().toStringAsFixed(2)}',
                        style: TextStyle(color: positive ? AppColors.success : AppColors.danger, fontWeight: FontWeight.bold, fontSize: 12)),
                  ],
                );
              },
            ),
            const SizedBox(height: 4),
            Text('${kalshi.first.accountName} · ${kalshi.length} account${kalshi.length == 1 ? '' : 's'}',
                style: const TextStyle(color: AppColors.textDim, fontSize: 11)),
          ],
        ),
      ),
    );
  }
}
