import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../dashboard/application/dashboard_controller.dart';
import '../../dashboard/data/dashboard_repository.dart';
import '../data/kalshi_repository.dart';

/// Compact Kalshi glance card for the dashboard. Self-hides when no Kalshi
/// account is linked. Tapping opens the Kalshi tab. Styled to match the
/// dashboard's other sections (SectionHeader + liquid GlassCard).
class KalshiDashboardCard extends ConsumerWidget {
  const KalshiDashboardCard({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final accts = ref.watch(brokeragesProvider).value ?? const <BrokerageAccount>[];
    final kalshi = accts.where((a) => a.brokerageType == 'kalshi').toList();
    if (kalshi.isEmpty) return const SizedBox.shrink();

    final bid = kalshi.first.id;
    final async = ref.watch(kalshiPortfolioProvider(bid));
    final positions = ref.watch(kalshiPositionsProvider(bid)).value?.length ?? 0;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(symbol('sports_soccer'), color: AppColors.primary, size: 20),
            const SizedBox(width: 8),
            Text('Kalshi', style: AppTextStyles.h2),
            const Spacer(),
            GestureDetector(
              onTap: () => context.go('/kalshi'),
              child: Row(
                children: [
                  Text('Open', style: AppTextStyles.meta.copyWith(color: AppColors.primary, fontWeight: FontWeight.w600)),
                  const SizedBox(width: 2),
                  Icon(Icons.arrow_forward, size: 14, color: AppColors.primary),
                ],
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        GlassCard(
          liquid: true,
          onTap: () => context.go('/kalshi'),
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(kalshi.first.accountName.toUpperCase(), style: AppTextStyles.eyebrow),
              const SizedBox(height: 8),
              async.when(
                loading: () => Text('Loading…', style: AppTextStyles.body.copyWith(color: AppColors.textDim)),
                error: (e, _) => Text('—', style: AppTextStyles.value.copyWith(fontSize: 22, color: AppColors.textDim)),
                data: (p) {
                  final positive = p.dayChange >= 0;
                  final color = positive ? AppColors.success : AppColors.danger;
                  return Row(
                    crossAxisAlignment: CrossAxisAlignment.baseline,
                    textBaseline: TextBaseline.alphabetic,
                    children: [
                      Text('\$${p.value.toStringAsFixed(2)}',
                          style: AppTextStyles.value.copyWith(fontSize: 24, color: AppColors.textHi)),
                      const SizedBox(width: 8),
                      Icon(positive ? Icons.trending_up : Icons.trending_down, size: 15, color: color),
                      const SizedBox(width: 2),
                      Text('${positive ? '+' : '-'}\$${p.dayChange.abs().toStringAsFixed(2)}',
                          style: AppTextStyles.meta.copyWith(color: color, fontWeight: FontWeight.bold)),
                    ],
                  );
                },
              ),
              const SizedBox(height: 8),
              Text('$positions open position${positions == 1 ? '' : 's'}',
                  style: AppTextStyles.meta.copyWith(color: AppColors.textDim)),
            ],
          ),
        ),
      ],
    );
  }
}
