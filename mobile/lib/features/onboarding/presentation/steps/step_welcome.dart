import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../../core/network/session.dart';
import '../../../../core/theme/app_colors.dart';
import '../../../../core/theme/app_text_styles.dart';
import '../../../../core/widgets/app_logo.dart';
import '../../../../core/widgets/common_widgets.dart';
import '../../../../core/widgets/glass_card.dart';
import '../../../../core/widgets/material_symbols.dart';

class StepWelcome extends ConsumerStatefulWidget {
  const StepWelcome({super.key});

  @override
  ConsumerState<StepWelcome> createState() => _StepWelcomeState();
}

class _StepWelcomeState extends ConsumerState<StepWelcome>
    with SingleTickerProviderStateMixin {
  late final AnimationController _pulse;

  @override
  void initState() {
    super.initState();
    _pulse = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 2),
    )..repeat(reverse: true);
  }

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final username = ref.watch(sessionProvider).username;

    return SingleChildScrollView(
      child: Column(
        children: [
          const SizedBox(height: 16),
          // Icon with pulse ring.
          Stack(
            alignment: Alignment.center,
            children: [
              AnimatedBuilder(
                animation: _pulse,
                builder: (_, _) => Container(
                  width: 96 + _pulse.value * 8,
                  height: 96 + _pulse.value * 8,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: AppColors.primary.withValues(alpha: 0.20 - _pulse.value * 0.10),
                      width: 1,
                    ),
                  ),
                ),
              ),
              const AppLogo(size: 72),
            ],
          ),
          const SizedBox(height: 24),
          // Animated title (per-word fade-in approximation).
          _AnimatedTitle(text: 'Welcome to IntelliStock'),
          const SizedBox(height: 16),
          // Greeting paragraph.
          Text(
            'Hey $username — let\'s get your autonomous trading workspace dialled in. '
            "We'll set up an LLM model, link a brokerage, and spin up your first instance.",
            textAlign: TextAlign.center,
            style: AppTextStyles.body.copyWith(color: AppColors.textMuted),
          ),
          const SizedBox(height: 28),
          // 3 feature tiles.
          for (final item in const [
            _FeatureItem(
              icon: 'memory',
              label: 'LLM Models',
              desc: 'OpenAI · Gemini · Azure · NVIDIA',
            ),
            _FeatureItem(
              icon: 'account_balance',
              label: 'Brokerages',
              desc: 'Alpaca · Robinhood',
            ),
            _FeatureItem(
              icon: 'rocket_launch',
              label: 'Instances',
              desc: 'Live or paper, fully autonomous',
            ),
          ]) ...[
            _FeatureTile(item: item),
            const SizedBox(height: 8),
          ],
          const SizedBox(height: 8),
        ],
      ),
    );
  }
}

class _FeatureItem {
  const _FeatureItem({
    required this.icon,
    required this.label,
    required this.desc,
  });

  final String icon;
  final String label;
  final String desc;
}

class _FeatureTile extends StatelessWidget {
  const _FeatureTile({required this.item});

  final _FeatureItem item;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      borderColor: AppColors.border,
      child: Row(
        children: [
          IconTile(
            icon: symbol(item.icon),
            color: AppColors.primary,
            size: 40,
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(item.label,
                    style: AppTextStyles.cardTitle
                        .copyWith(color: AppColors.textHi)),
                const SizedBox(height: 2),
                Text(item.desc,
                    style:
                        AppTextStyles.nano.copyWith(color: AppColors.textDim)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Simple per-word staggered fade-in title.
class _AnimatedTitle extends StatefulWidget {
  const _AnimatedTitle({required this.text});

  final String text;

  @override
  State<_AnimatedTitle> createState() => _AnimatedTitleState();
}

class _AnimatedTitleState extends State<_AnimatedTitle>
    with TickerProviderStateMixin {
  late final List<AnimationController> _controllers;
  late final List<Animation<double>> _anims;

  @override
  void initState() {
    super.initState();
    final words = widget.text.split(' ');
    _controllers = List.generate(
      words.length,
      (i) => AnimationController(
        vsync: this,
        duration: const Duration(milliseconds: 400),
      ),
    );
    _anims = _controllers
        .map((c) => CurvedAnimation(parent: c, curve: Curves.easeOut))
        .toList();

    for (var i = 0; i < _controllers.length; i++) {
      Future.delayed(Duration(milliseconds: 80 + i * 60), () {
        if (mounted) _controllers[i].forward();
      });
    }
  }

  @override
  void dispose() {
    for (final c in _controllers) {
      c.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final words = widget.text.split(' ');
    return Wrap(
      alignment: WrapAlignment.center,
      spacing: 6,
      children: List.generate(words.length, (i) {
        return FadeTransition(
          opacity: _anims[i],
          child: SlideTransition(
            position: Tween<Offset>(
              begin: const Offset(0, 0.3),
              end: Offset.zero,
            ).animate(_anims[i]),
            child: Text(
              words[i],
              style: AppTextStyles.h1.copyWith(
                fontSize: 26,
                fontWeight: FontWeight.w800,
                color: AppColors.textHi,
              ),
            ),
          ),
        );
      }),
    );
  }
}

// We need rocket_launch in material_symbols — map it to a close icon for now
// (the symbol() helper falls back to Icons.circle_outlined for unknown names).
// Add 'rocket_launch' to core/widgets/material_symbols.dart if needed.
