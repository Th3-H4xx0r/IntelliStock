import 'package:flutter/material.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../data/models/backtest.dart';

/// Amber banner shown only when status == 'paused_llm_critical'.
/// Mirrors BacktestLLMPauseBanner.vue exactly.
class BacktestLlmPauseBanner extends StatelessWidget {
  const BacktestLlmPauseBanner({super.key, required this.summary});

  final BacktestSummary summary;

  @override
  Widget build(BuildContext context) {
    if ((summary.status ?? '').toLowerCase() != 'paused_llm_critical') {
      return const SizedBox.shrink();
    }
    return Container(
      margin: const EdgeInsets.only(bottom: 16),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.fill(AppColors.warning),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.stroke(AppColors.warning)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.pause_circle_outline,
              color: AppColors.warning, size: 22),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Backtest paused: LLM critical failure',
                  style: AppTextStyles.bodyHi
                      .copyWith(color: AppColors.warning),
                ),
                const SizedBox(height: 8),
                _row('Bar', _fmtBar(summary.pauseBarTime)),
                _row(
                  'Reason',
                  '${summary.pauseReasonTag ?? 'unknown'}  '
                      '(${summary.pauseAttempts ?? '?'} attempts)',
                ),
                _row(
                  'Provider',
                  '${summary.pauseProvider ?? '?'}  •  '
                      'Model: ${summary.pauseModel ?? '?'}',
                ),
                _row('Call site', summary.pauseCallSite ?? 'unknown'),
                _row('Paused at', _fmtPausedAt(summary.pausedAt)),
                if (summary.pauseSample != null &&
                    summary.pauseSample!.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  Container(
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                      color: Colors.black26,
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(
                          color: AppColors.stroke(AppColors.warning)),
                    ),
                    child: SelectableText(
                      summary.pauseSample!,
                      style: AppTextStyles.mono(10,
                          color: AppColors.warning
                              .withValues(alpha: 0.7)),
                    ),
                  ),
                ],
                const SizedBox(height: 8),
                Text(
                  'Tap "Resume" above when the provider is healthy again. '
                  'The same bar will retry from the snapshot.',
                  style: AppTextStyles.meta.copyWith(
                      color:
                          AppColors.warning.withValues(alpha: 0.6)),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _row(String label, String value) => Padding(
        padding: const EdgeInsets.only(bottom: 3),
        child: RichText(
          text: TextSpan(
            style: AppTextStyles.meta
                .copyWith(color: AppColors.textMd),
            children: [
              TextSpan(
                text: '$label:  ',
                style: TextStyle(
                    color: AppColors.warning.withValues(alpha: 0.7)),
              ),
              TextSpan(text: value),
            ],
          ),
        ),
      );

  static String _fmtBar(String? s) {
    if (s == null || s.isEmpty) return 'unknown';
    return s.length >= 10 ? s.substring(0, 10) : s;
  }

  static String _fmtPausedAt(dynamic v) {
    final dt = parseDateTime(v);
    if (dt == null) return 'unknown';
    return fmtDateTime(dt);
  }
}
