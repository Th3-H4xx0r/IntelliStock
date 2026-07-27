import 'package:flutter/material.dart';
import '../../../../core/theme/app_colors.dart';
import '../../../../core/theme/app_text_styles.dart';
import '../../../../core/widgets/common_widgets.dart';
import '../../../../core/widgets/glass_card.dart';
import '../../../../core/widgets/material_symbols.dart';

class StepAbout extends StatelessWidget {
  const StepAbout({super.key});

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('WHAT IS INTELLISTOCK', style: AppTextStyles.eyebrow),
          const SizedBox(height: 8),
          Text(
            'AI-powered autonomous trading.',
            style: AppTextStyles.h2.copyWith(color: AppColors.textHi),
          ),
          const SizedBox(height: 8),
          Text(
            'IntelliStock runs LLM-driven strategies on a schedule, makes buy/sell decisions, '
            'and executes through your linked brokerage — all without manual intervention.',
            style: AppTextStyles.body.copyWith(color: AppColors.textMuted),
          ),
          const SizedBox(height: 20),
          // 2×2 feature cards.
          GridView.count(
            crossAxisCount: 2,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            mainAxisSpacing: 10,
            crossAxisSpacing: 10,
            childAspectRatio: 1.4,
            children: const [
              _FeatureCard(
                icon: 'memory',
                color: AppColors.primary,
                title: 'LLM Models',
                desc:
                    'Plug in any provider — Gemini, OpenAI, Azure, NVIDIA, Ollama, Bedrock.',
              ),
              _FeatureCard(
                icon: 'tune',
                color: AppColors.info,
                title: 'Strategies',
                desc:
                    'Choose from the catalog or write your own Python strategy.',
              ),
              _FeatureCard(
                icon: 'rocket_launch',
                color: AppColors.success,
                title: 'Instances',
                desc: 'Run live or paper on a cadence from 1 min to 1 hour.',
              ),
              _FeatureCard(
                icon: 'account_balance',
                color: AppColors.warning,
                title: 'Brokerages',
                desc: 'Alpaca for stock paper and live trading.',
              ),
            ],
          ),
          const SizedBox(height: 16),
          // Flow diagram hint.
          GlassCard(
            padding: const EdgeInsets.all(14),
            borderColor: AppColors.border,
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                for (final (icon, label) in const [
                  ('memory', 'Model'),
                  ('tune', 'Strategy'),
                  ('rocket_launch', 'Instance'),
                  ('account_balance', 'Brokerage'),
                ]) ...[
                  Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(symbol(icon), size: 20, color: AppColors.primary),
                      const SizedBox(height: 4),
                      Text(label, style: AppTextStyles.nano),
                    ],
                  ),
                  if (label != 'Brokerage') ...[
                    const SizedBox(width: 4),
                    const Icon(
                      Icons.arrow_forward,
                      size: 14,
                      color: AppColors.textFaint,
                    ),
                    const SizedBox(width: 4),
                  ],
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _FeatureCard extends StatelessWidget {
  const _FeatureCard({
    required this.icon,
    required this.color,
    required this.title,
    required this.desc,
  });

  final String icon;
  final Color color;
  final String title;
  final String desc;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: const EdgeInsets.all(12),
      borderColor: AppColors.stroke(color),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          IconTile(icon: symbol(icon), color: color, size: 32),
          const SizedBox(height: 8),
          Text(
            title,
            style: AppTextStyles.cardTitle.copyWith(color: AppColors.textHi),
          ),
          const SizedBox(height: 4),
          Expanded(
            child: Text(
              desc,
              style: AppTextStyles.nano.copyWith(color: AppColors.textDim),
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ),
    );
  }
}
