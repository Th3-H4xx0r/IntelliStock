import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/charts/scrubbable_area_chart.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/common_widgets.dart';
import '../data/kalshi_repository.dart';

/// Portfolio hero: BORDERLESS — it sits directly on the screen's gradient crown
/// (like the dashboard hero), with an odometer-rolling value + day P&L and a
/// scrubbable equity chart (pulsing live end-dot). Shared by the Kalshi overview
/// + the instance detail.
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
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 4, 4, 4),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Builder(builder: (_) {
          final paper = async.value?.isPaper ?? false;
          return Row(children: [
            Text((paper ? 'Paper P&L · progress' : title).toUpperCase(),
                style: AppTextStyles.eyebrow.copyWith(color: AppColors.textMuted, letterSpacing: 1.0)),
            if (paper) ...[
              const SizedBox(width: 8),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(color: AppColors.fill(AppColors.warning), borderRadius: BorderRadius.circular(4)),
                child: Text('MOCK', style: AppTextStyles.nano.copyWith(color: AppColors.warning, fontWeight: FontWeight.bold)),
              ),
            ],
          ]);
        }),
        const SizedBox(height: 10),
        async.when(
          loading: () => const SizedBox(height: 110, child: LoadingState()),
          error: (e, _) => ErrorBanner(message: '$e', onRetry: onRetry),
          data: (p) {
            // Paper instances: show the paper P&L progress curve (the broker value is a
            // static demo balance). Real instances: the portfolio value curve.
            final vals = p.isPaper ? p.paperSeries : p.series;
            final tss = p.isPaper ? p.paperSeriesTs : p.seriesTs;
            final headline = p.isPaper ? (p.paperPnl ?? 0.0) : p.value;
            final dayChg = p.isPaper ? (vals.length >= 2 ? vals.last - vals.first : 0.0) : p.dayChange;
            final hasSeries = vals.length > 1;
            final baseline = vals.isNotEmpty ? vals.first : 0.0;
            return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              ValueListenableBuilder<int?>(
                valueListenable: scrubIdx,
                builder: (_, idx, __) {
                  final scrubbing = idx != null && idx >= 0 && idx < vals.length;
                  final v = scrubbing ? vals[idx] : headline;
                  final change = scrubbing ? (v - baseline) : dayChg;
                  final positive = change >= 0;
                  final color = positive ? AppColors.success : AppColors.danger;
                  return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    // Odometer: rolls to the value (and value -> scrubbed value). Paper
                    // P&L can be negative, so render the sign before the $.
                    TweenAnimationBuilder<double>(
                      tween: Tween<double>(begin: 0, end: v),
                      duration: const Duration(milliseconds: 500),
                      curve: Curves.easeOutCubic,
                      builder: (_, val, __) => Text(
                        '${val < 0 ? '-' : ''}\$${val.abs().toStringAsFixed(2)}',
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
                  timestamps: tss,
                  values: vals,
                  lineColor: dayChg >= 0 ? AppColors.success : AppColors.danger,
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
    );
  }
}

/// A Kalshi-style open-position tile: team crest + match, "Yes · Pick" + max
/// payout, and the current value with unrealized P&L + live odds. Shared by the
/// overview Open Positions card + the instance detail.
Widget kalshiPositionTile(KalshiPosition p) {
  final title = p.match.isNotEmpty ? p.match : p.marketTicker;
  final pick = (p.pickLabel.isNotEmpty ? p.pickLabel : p.side).replaceAll(' to win', '');
  // unrealized_cents arrives in cents -> dollars for the $X.XX P&L.
  final u = p.unrealizedCents == null ? null : p.unrealizedCents! / 100.0;
  final positive = (u ?? 0) >= 0;
  final pnlColor = u == null ? AppColors.textDim : (positive ? AppColors.success : AppColors.danger);
  return Container(
    margin: const EdgeInsets.only(bottom: 10),
    padding: const EdgeInsets.all(12),
    decoration: BoxDecoration(
      color: AppColors.surface.withValues(alpha: 0.4),
      borderRadius: BorderRadius.circular(12),
      border: Border.all(color: AppColors.border),
    ),
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        _posCrest(p.pickLogo, pick),
        const SizedBox(width: 10),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title, maxLines: 1, overflow: TextOverflow.ellipsis,
              style: AppTextStyles.body.copyWith(color: AppColors.textHi, fontWeight: FontWeight.w600)),
          const SizedBox(height: 2),
          Text('Yes · $pick', maxLines: 1, overflow: TextOverflow.ellipsis,
              style: AppTextStyles.nano.copyWith(color: AppColors.primary, fontWeight: FontWeight.w500)),
        ])),
        Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
          Text(p.currentValue == null ? '—' : '\$${p.currentValue!.toStringAsFixed(2)}',
              style: AppTextStyles.value.copyWith(color: AppColors.textHi, fontSize: 16, fontWeight: FontWeight.w700)),
          const SizedBox(height: 2),
          Text(u == null ? '' : '${positive ? '+' : ''}\$${u.toStringAsFixed(2)}',
              style: AppTextStyles.nano.copyWith(color: pnlColor, fontWeight: FontWeight.w600)),
        ]),
      ]),
      const SizedBox(height: 8),
      Row(children: [
        _posChip('${p.contracts}×'),
        const SizedBox(width: 6),
        _posChip('Buy ${p.oddsPct == null ? '—' : '${p.oddsPct!.toStringAsFixed(0)}%'}'),
        const Spacer(),
        Text('\$${p.maxPayout.toStringAsFixed(0)} max payout',
            style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
      ]),
    ]),
  );
}

Widget _posChip(String text) => Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
      decoration: BoxDecoration(
        color: AppColors.fill(AppColors.primary),
        borderRadius: BorderRadius.circular(5),
      ),
      child: Text(text, style: AppTextStyles.nano.copyWith(color: AppColors.primary, fontWeight: FontWeight.w600)),
    );

Widget _posCrest(String url, String fallbackName) {
  final init = fallbackName.replaceAll(RegExp(r'[^A-Za-z ]'), '').split(' ')
      .where((w) => w.isNotEmpty).map((w) => w[0]).take(2).join();
  final fb = Container(
    width: 36, height: 36, color: AppColors.surface, alignment: Alignment.center,
    child: Text(init, style: AppTextStyles.nano.copyWith(color: AppColors.textDim, fontWeight: FontWeight.bold)),
  );
  return ClipOval(
    child: url.isNotEmpty
        ? Image.network(url, width: 36, height: 36, fit: BoxFit.contain, errorBuilder: (_, __, ___) => fb)
        : fb,
  );
}

/// The screen's gradient "crown" backdrop — deep violet under the status bar
/// fading to the canvas, with a soft lavender bloom + specular sheen. Sits behind
/// the Kalshi scroll content so the hero + frosted cards read on it (matches the
/// dashboard). Place inside a Stack as the first child (Positioned top).
class KalshiCrown extends StatelessWidget {
  const KalshiCrown({super.key, this.height = 460});
  final double height;

  @override
  Widget build(BuildContext context) {
    return IgnorePointer(
      child: SizedBox(
        height: height,
        width: double.infinity,
        child: Stack(children: [
          Positioned.fill(
            child: DecoratedBox(
              decoration: const BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [Color(0xFF5A28BE), Color(0xFF34176E), Color(0xFF1A0E38), Color(0x0004040C)],
                  stops: [0.0, 0.28, 0.6, 1.0],
                ),
              ),
            ),
          ),
          // Diagonal specular sheen.
          Positioned.fill(
            child: DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.bottomLeft,
                  end: Alignment.topRight,
                  colors: [
                    Colors.white.withValues(alpha: 0.0),
                    Colors.white.withValues(alpha: 0.0),
                    Colors.white.withValues(alpha: 0.08),
                    Colors.white.withValues(alpha: 0.0),
                  ],
                  stops: const [0.0, 0.52, 0.70, 0.88],
                ),
              ),
            ),
          ),
          // Lavender bloom, upper-right.
          Positioned(
            top: -70,
            right: -60,
            child: Container(
              width: 280,
              height: 280,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: RadialGradient(colors: [
                  const Color(0xFFB794FF).withValues(alpha: 0.16),
                  const Color(0xFFB794FF).withValues(alpha: 0.0),
                ]),
              ),
            ),
          ),
        ]),
      ),
    );
  }
}
