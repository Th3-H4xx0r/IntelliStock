import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/network/api_error.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/glass_card.dart';
import '../application/swing_controller.dart';
import '../data/swing_repository.dart';

/// "2.0% ITM" / "3.0% OTM" / "—".
String fmtItm(double? itmPct) {
  if (itmPct == null) return '—';
  return itmPct > 0
      ? '${itmPct.toStringAsFixed(1)}% ITM'
      : '${itmPct.abs().toStringAsFixed(1)}% OTM';
}

/// The wheel lane's open cash-secured puts and its latest scans. Read-only:
/// the lane buys puts back by itself, and a red ITM figure means the 15:45 ET
/// monitor will buy that put back on its next pass.
class WheelCard extends ConsumerWidget {
  const WheelCard({super.key, required this.instanceId});

  final String instanceId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(wheelSnapshotProvider(instanceId));
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Wheel', style: AppTextStyles.cardTitle),
          const SizedBox(height: 10),
          async.when(
            loading: () => Text('Loading…', style: AppTextStyles.meta),
            error: (err, _) => ErrorBanner(
              message: err is ApiError && err.statusCode == 404
                  ? 'This API build has no wheel endpoint yet.'
                  : err.toString(),
              onRetry: () => ref.invalidate(wheelSnapshotProvider(instanceId)),
            ),
            data: (w) => _WheelBody(wheel: w),
          ),
        ],
      ),
    );
  }
}

class _WheelBody extends StatelessWidget {
  const _WheelBody({required this.wheel});

  final WheelSnapshot wheel;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            _Stat(label: 'OPEN PUTS', value: '${wheel.openPuts.length}'),
            _Stat(label: 'COLLATERAL', value: fmtMoney(wheel.collateralTotal)),
            _Stat(label: 'CASH', value: fmtMoney(wheel.cash)),
          ],
        ),
        const SizedBox(height: 12),
        if (wheel.openPuts.isEmpty)
          Text('No open puts.',
              style: AppTextStyles.meta.copyWith(fontStyle: FontStyle.italic))
        else
          for (final put in wheel.openPuts) _PutRow(put: put),
        const SizedBox(height: 12),
        Text('RECENT SCANS',
            style: AppTextStyles.nano
                .copyWith(color: AppColors.textDim, letterSpacing: 0.8)),
        const SizedBox(height: 6),
        if (wheel.recentScans.isEmpty)
          Text('No scans recorded yet.',
              style: AppTextStyles.meta.copyWith(fontStyle: FontStyle.italic))
        else
          for (final scan in wheel.recentScans.take(5)) _ScanRow(scan: scan),
      ],
    );
  }
}

class _PutRow extends StatelessWidget {
  const _PutRow({required this.put});

  final WheelPut put;

  Color get _itmColor {
    if (put.itmPct == null) return AppColors.textDim;
    if (put.monitorWillBuyBack) return AppColors.danger;
    return put.itmPct! > 0 ? AppColors.warning : AppColors.success;
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '${put.underlying} ${fmtMoney(put.strike)} P · ${put.expiry}',
            style: AppTextStyles.cardTitle.copyWith(color: AppColors.textHi),
          ),
          Text(put.contract,
              style: AppTextStyles.mono(10, color: AppColors.textFaint)),
          const SizedBox(height: 6),
          Row(
            children: [
              _Stat(label: 'QTY', value: put.qty?.toString() ?? '—'),
              _Stat(label: 'ENTRY', value: fmtMoney(put.avgEntryPrice)),
              _Stat(label: 'MARK', value: fmtMoney(put.currentPrice)),
            ],
          ),
          const SizedBox(height: 4),
          Row(
            children: [
              _Stat(
                  label: 'ITM', value: fmtItm(put.itmPct), color: _itmColor),
              _Stat(label: 'DTE', value: put.dte?.toString() ?? '—'),
              _Stat(
                label: 'P&L',
                value: fmtPnl(put.unrealizedPl),
                color: put.unrealizedPl == null
                    ? AppColors.textDim
                    : pnlColor(put.unrealizedPl),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _ScanRow extends StatelessWidget {
  const _ScanRow({required this.scan});

  final WheelScan scan;

  Color get _statusColor => switch (scan.status) {
        'placed' => AppColors.success,
        'pending' => AppColors.warning,
        'rejected' => AppColors.danger,
        _ => AppColors.textMuted,
      };

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${scan.symbol} ${fmtMoney(scan.strike)} P · ${scan.expiry.isEmpty ? '—' : scan.expiry}',
                  style: AppTextStyles.micro.copyWith(color: AppColors.textMd),
                ),
                if (scan.skipReason.isNotEmpty)
                  Text(scan.skipReason,
                      style: AppTextStyles.nano
                          .copyWith(color: AppColors.textFaint)),
              ],
            ),
          ),
          const SizedBox(width: 8),
          AppBadge(
              label: scan.status.isEmpty ? '—' : scan.status,
              color: _statusColor),
        ],
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.value, this.color});

  final String label;
  final String value;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label,
              style: AppTextStyles.nano
                  .copyWith(color: AppColors.textDim, letterSpacing: 0.4)),
          const SizedBox(height: 2),
          Text(
            value,
            overflow: TextOverflow.ellipsis,
            style: AppTextStyles.micro.copyWith(
                color: color ?? AppColors.textMd, fontWeight: FontWeight.w700),
          ),
        ],
      ),
    );
  }
}
