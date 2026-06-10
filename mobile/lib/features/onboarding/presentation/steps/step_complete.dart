import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../../core/theme/app_colors.dart';
import '../../../../core/theme/app_text_styles.dart';
import '../../../../core/widgets/glass_card.dart';
import '../../../../core/widgets/material_symbols.dart';
import '../../application/onboarding_controller.dart';

/// Complete step: animated checkmark + ray burst + count tiles.
class StepComplete extends ConsumerStatefulWidget {
  const StepComplete({super.key});

  @override
  ConsumerState<StepComplete> createState() => _StepCompleteState();
}

class _StepCompleteState extends ConsumerState<StepComplete>
    with TickerProviderStateMixin {
  late final AnimationController _checkCtrl;
  late final AnimationController _raysCtrl;

  @override
  void initState() {
    super.initState();
    _checkCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 600),
    )..forward();
    _raysCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..forward();
  }

  @override
  void dispose() {
    _checkCtrl.dispose();
    _raysCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(onboardingControllerProvider);

    return SingleChildScrollView(
      child: Column(
        children: [
          const SizedBox(height: 24),
          // Ray burst + checkmark.
          SizedBox(
            width: 160,
            height: 160,
            child: Stack(
              alignment: Alignment.center,
              children: [
                // 14 rays.
                AnimatedBuilder(
                  animation: _raysCtrl,
                  builder: (_, _) {
                    return CustomPaint(
                      size: const Size(160, 160),
                      painter: _RayBurstPainter(
                        progress: _raysCtrl.value,
                        color: AppColors.primary,
                        rayCount: 14,
                      ),
                    );
                  },
                ),
                // Check circle.
                ScaleTransition(
                  scale: CurvedAnimation(
                    parent: _checkCtrl,
                    curve: Curves.elasticOut,
                  ),
                  child: Container(
                    width: 80,
                    height: 80,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: AppColors.fill(AppColors.primary),
                      border: Border.all(
                        color: AppColors.primary.withValues(alpha: 0.6),
                        width: 2,
                      ),
                      boxShadow: [
                        BoxShadow(
                          color: AppColors.primary.withValues(alpha: 0.3),
                          blurRadius: 32,
                          spreadRadius: 4,
                        ),
                      ],
                    ),
                    child: Icon(
                      symbol('check_circle'),
                      color: AppColors.primary,
                      size: 44,
                    ),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 24),
          Text(
            "You're all set.",
            textAlign: TextAlign.center,
            style: AppTextStyles.h1.copyWith(
              fontSize: 30,
              fontWeight: FontWeight.w800,
              color: AppColors.textHi,
            ),
          ),
          const SizedBox(height: 12),
          Text(
            'IntelliStock is configured and ready. Open the dashboard to monitor '
            'portfolios, queue backtests, or jump straight into your live instance.',
            textAlign: TextAlign.center,
            style: AppTextStyles.body.copyWith(color: AppColors.textMuted),
          ),
          const SizedBox(height: 28),
          // Count tiles.
          Row(
            children: [
              Expanded(
                child: _CountTile(
                  icon: 'memory',
                  label: 'Models',
                  value: state.modelCount,
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: _CountTile(
                  icon: 'account_balance',
                  label: 'Brokerages',
                  value: state.brokerageCount,
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: _CountTile(
                  icon: 'rocket_launch',
                  label: 'Instances',
                  value: state.instanceCount,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
        ],
      ),
    );
  }
}

class _CountTile extends StatelessWidget {
  const _CountTile({
    required this.icon,
    required this.label,
    required this.value,
  });

  final String icon;
  final String label;
  final int value;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 14),
      borderColor: AppColors.border,
      child: Column(
        children: [
          Icon(symbol(icon), size: 20, color: AppColors.primary),
          const SizedBox(height: 6),
          Text(
            '$value',
            style: AppTextStyles.valueLg.copyWith(color: AppColors.textHi),
          ),
          const SizedBox(height: 4),
          Text(
            label.toUpperCase(),
            style: AppTextStyles.nano.copyWith(
              color: AppColors.textDim,
              letterSpacing: 1.0,
            ),
          ),
        ],
      ),
    );
  }
}

/// Paints 14 short rays radiating from the center as [progress] goes 0→1.
class _RayBurstPainter extends CustomPainter {
  _RayBurstPainter({
    required this.progress,
    required this.color,
    required this.rayCount,
  });

  final double progress;
  final Color color;
  final int rayCount;

  @override
  void paint(Canvas canvas, Size size) {
    final cx = size.width / 2;
    final cy = size.height / 2;
    final paint = Paint()
      ..color = color.withValues(alpha: progress * 0.5)
      ..strokeWidth = 1.5
      ..strokeCap = StrokeCap.round
      ..style = PaintingStyle.stroke;

    const innerR = 44.0;
    const outerR = 76.0;

    for (var i = 0; i < rayCount; i++) {
      final angle = (2 * math.pi / rayCount) * i - math.pi / 2;
      final delay = (i % 4) * 0.06;
      final t =
          ((progress - delay / 1.2) / (1 - delay / 1.2)).clamp(0.0, 1.0);
      if (t <= 0) continue;

      final r = innerR + (outerR - innerR) * t;
      final x = cx + r * math.cos(angle);
      final y = cy + r * math.sin(angle);
      final x0 = cx + innerR * math.cos(angle);
      final y0 = cy + innerR * math.sin(angle);
      canvas.drawLine(Offset(x0, y0), Offset(x, y), paint);
    }
  }

  @override
  bool shouldRepaint(_RayBurstPainter old) =>
      old.progress != progress || old.color != color;
}
