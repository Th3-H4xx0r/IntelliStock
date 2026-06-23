import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/charts/scrubbable_area_chart.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/common_widgets.dart';
import '../data/kalshi_repository.dart';

/// Dashboard-grade portfolio hero: a gradient "crown" behind an odometer-rolling
/// value + day P&L, with a scrubbable equity chart (pulsing live end-dot). Shared
/// by the Kalshi overview + the instance detail so both match the IntelliStock
/// dashboard's look.
class KalshiPortfolioHero extends StatelessWidget {
  const KalshiPortfolioHero({
    super.key,
    required this.title,
    required this.async,
    required this.scrubIdx,
    required this.onRetry,
  });

  final String title;
  final AsyncValue<KalshiPortfolio> async;
  final ValueNotifier<int?> scrubIdx;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(20),
      child: DecoratedBox(
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: const Color(0x1FFFFFFF)),
          boxShadow: [
            BoxShadow(
              color: const Color(0xFF000000).withValues(alpha: 0.30),
              blurRadius: 26,
              spreadRadius: -8,
              offset: const Offset(0, 14),
            ),
          ],
        ),
        child: Stack(children: [
          // Gradient crown: deep violet at the top fading to the canvas, plus a
          // soft lavender bloom — the dashboard hero treatment.
          Positioned.fill(
            child: DecoratedBox(
              decoration: const BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [Color(0xFF3D1C82), Color(0xFF21123F), Color(0xFF130C24)],
                  stops: [0.0, 0.42, 1.0],
                ),
              ),
            ),
          ),
          Positioned(
            top: -50,
            right: -40,
            child: Container(
              width: 220,
              height: 220,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: RadialGradient(colors: [
                  const Color(0xFFB794FF).withValues(alpha: 0.16),
                  const Color(0xFFB794FF).withValues(alpha: 0.0),
                ]),
              ),
            ),
          ),
          // Top specular hairline.
          Positioned(
            top: 0, left: 0, right: 0,
            child: Container(
              height: 1,
              decoration: BoxDecoration(
                gradient: LinearGradient(colors: [
                  Colors.white.withValues(alpha: 0.0),
                  Colors.white.withValues(alpha: 0.22),
                  Colors.white.withValues(alpha: 0.0),
                ]),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(18, 16, 18, 16),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: AppTextStyles.eyebrow.copyWith(color: AppColors.textMuted)),
              const SizedBox(height: 14),
              async.when(
                loading: () => const SizedBox(height: 110, child: LoadingState()),
                error: (e, _) => ErrorBanner(message: '$e', onRetry: onRetry),
                data: (p) {
                  final hasSeries = p.series.length > 1;
                  final baseline = p.series.isNotEmpty ? p.series.first : 0.0;
                  return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    ValueListenableBuilder<int?>(
                      valueListenable: scrubIdx,
                      builder: (_, idx, __) {
                        final scrubbing = idx != null && idx >= 0 && idx < p.series.length;
                        final v = scrubbing ? p.series[idx] : p.value;
                        final change = scrubbing ? (v - baseline) : p.dayChange;
                        final positive = change >= 0;
                        final color = positive ? AppColors.success : AppColors.danger;
                        return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          // Odometer: rolls 0 -> value (and value -> scrubbed value).
                          TweenAnimationBuilder<double>(
                            tween: Tween<double>(begin: 0, end: v),
                            duration: const Duration(milliseconds: 500),
                            curve: Curves.easeOutCubic,
                            builder: (_, val, __) => Text(
                              '\$${val.toStringAsFixed(2)}',
                              style: AppTextStyles.value.copyWith(
                                  fontSize: 34, color: AppColors.textHi, fontWeight: FontWeight.w800, letterSpacing: -0.5),
                            ),
                          ),
                          const SizedBox(height: 5),
                          Row(children: [
                            Icon(positive ? Icons.trending_up : Icons.trending_down, size: 16, color: color),
                            const SizedBox(width: 4),
                            Text('${positive ? '+' : '-'}\$${change.abs().toStringAsFixed(2)}',
                                style: AppTextStyles.body.copyWith(color: color, fontWeight: FontWeight.w700)),
                          ]),
                        ]);
                      },
                    ),
                    if (hasSeries) ...[
                      const SizedBox(height: 18),
                      ScrubbableAreaChart(
                        timestamps: p.seriesTs,
                        values: p.series,
                        lineColor: p.dayChange >= 0 ? AppColors.success : AppColors.danger,
                        height: 168,
                        baseline: baseline,
                        indexed: true,
                        pulsingEndDot: true,
                        onScrub: (i) => scrubIdx.value = i,
                      ),
                    ] else
                      Padding(
                        padding: const EdgeInsets.only(top: 10),
                        child: Text('Equity curve appears once the engine records snapshots.',
                            style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
                      ),
                  ]);
                },
              ),
            ]),
          ),
        ]),
      ),
    );
  }
}
