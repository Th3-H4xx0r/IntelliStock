import 'package:flutter/material.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/status_pill.dart';
import '../../../core/widgets/app_button.dart';

/// A reusable engine control card.
///
/// Displays an [IconTile] header with [title], [subtitle], a [StatusPill],
/// optional [stats] grid, and action [buttons].
class ServiceCard extends StatelessWidget {
  const ServiceCard({
    super.key,
    required this.icon,
    required this.iconColor,
    required this.title,
    required this.subtitle,
    required this.status,
    this.stats = const [],
    this.buttons = const [],
  });

  final IconData icon;
  final Color iconColor;
  final String title;
  final String subtitle;
  final String status;

  /// Optional 1–4 [StatTile] widgets rendered in a 2-col grid.
  final List<Widget> stats;

  /// Action buttons (typically [AppButton.semantic]).
  final List<Widget> buttons;

  @override
  Widget build(BuildContext context) {
    final pillColor = StatusPill.colorForStatus(status);
    final isRunning = status.toLowerCase() == 'running';

    return GlassCard(
      liquid: true,
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // ── Header row ─────────────────────────────────────────────────────
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              IconTile(icon: icon, color: iconColor),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title,
                        style: AppTextStyles.cardTitle,
                        overflow: TextOverflow.ellipsis),
                    const SizedBox(height: 2),
                    Text(subtitle,
                        style: AppTextStyles.micro
                            .copyWith(color: AppColors.textFaint)),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              StatusPill(
                label: _pillLabel(status),
                color: pillColor,
                pulsing: isRunning,
              ),
            ],
          ),

          // ── Stats grid ─────────────────────────────────────────────────────
          if (stats.isNotEmpty) ...[
            const SizedBox(height: 16),
            _StatsGrid(children: stats),
          ],

          // ── Buttons ────────────────────────────────────────────────────────
          if (buttons.isNotEmpty) ...[
            const SizedBox(height: 16),
            Row(
              children: buttons
                  .expand((btn) => [Expanded(child: btn), const SizedBox(width: 8)])
                  .toList()
                ..removeLast(),
            ),
          ],
        ],
      ),
    );
  }

  static String _pillLabel(String s) {
    if (s.isEmpty) return 'Stopped';
    return '${s[0].toUpperCase()}${s.substring(1)}';
  }
}

class _StatsGrid extends StatelessWidget {
  const _StatsGrid({required this.children});
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    if (children.length == 1) {
      return SizedBox(width: double.infinity, child: children.first);
    }
    // 2-column wrap
    final rows = <Widget>[];
    for (var i = 0; i < children.length; i += 2) {
      final right = i + 1 < children.length ? children[i + 1] : null;
      rows.add(Row(
        children: [
          Expanded(child: children[i]),
          if (right != null) ...[const SizedBox(width: 8), Expanded(child: right)],
        ],
      ));
      if (i + 2 < children.length) rows.add(const SizedBox(height: 8));
    }
    return Column(children: rows);
  }
}

/// A compact stat cell matching the web `.rounded-lg bg-surface/50` pattern,
/// used inside service cards.
class ServiceStatCell extends StatelessWidget {
  const ServiceStatCell({super.key, required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label,
              style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
          const SizedBox(height: 4),
          Text(value,
              style: AppTextStyles.mono(11, color: AppColors.textMd),
              overflow: TextOverflow.ellipsis),
        ],
      ),
    );
  }
}

/// Progress bar cell — used by Nexus card.
class NexusProgressCell extends StatelessWidget {
  const NexusProgressCell({
    super.key,
    required this.progressPct,
    this.phase,
  });

  final int progressPct;
  final String? phase;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text('Build progress',
                  style:
                      AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
            ),
            Text(
              '$progressPct%',
              style: AppTextStyles.mono(11,
                  color: AppColors.textMd,
                  weight: FontWeight.w600),
            ),
          ],
        ),
        const SizedBox(height: 6),
        ClipRRect(
          borderRadius: BorderRadius.circular(4),
          child: LinearProgressIndicator(
            value: progressPct / 100,
            backgroundColor: AppColors.surface,
            valueColor:
                const AlwaysStoppedAnimation<Color>(AppColors.primary),
            minHeight: 6,
          ),
        ),
        if (phase != null) ...[
          const SizedBox(height: 6),
          Text(
            phase!,
            style: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ],
    );
  }
}
