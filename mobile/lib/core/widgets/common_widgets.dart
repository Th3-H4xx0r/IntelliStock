import 'package:flutter/material.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import 'app_button.dart';
import 'glass_card.dart';

/// Eyebrow + title section header.
class SectionHeader extends StatelessWidget {
  const SectionHeader({
    super.key,
    required this.title,
    this.eyebrow,
    this.subtitle,
    this.trailing,
  });

  final String title;
  final String? eyebrow;
  final String? subtitle;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (eyebrow != null) ...[
                Text(eyebrow!.toUpperCase(), style: AppTextStyles.eyebrow),
                const SizedBox(height: 4),
              ],
              Text(title, style: AppTextStyles.h1),
              if (subtitle != null) ...[
                const SizedBox(height: 4),
                Text(subtitle!,
                    style: AppTextStyles.body.copyWith(color: AppColors.textDim)),
              ],
            ],
          ),
        ),
        if (trailing != null) trailing!,
      ],
    );
  }
}

/// A small labelled value tile (`rounded-lg bg-surface/50`).
class StatTile extends StatelessWidget {
  const StatTile({
    super.key,
    required this.label,
    required this.value,
    this.valueColor = AppColors.textHi,
    this.sub,
  });

  final String label;
  final String value;
  final Color valueColor;
  final String? sub;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
          const SizedBox(height: 4),
          Text(value,
              style: AppTextStyles.value.copyWith(color: valueColor)),
          if (sub != null) Text('$sub', style: AppTextStyles.nano),
        ],
      ),
    );
  }
}

/// Colored badge / tag (`text-[10px] font-bold uppercase`).
class AppBadge extends StatelessWidget {
  const AppBadge({super.key, required this.label, required this.color});

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: AppColors.fill(color),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: AppColors.stroke(color)),
      ),
      child: Text(
        label.toUpperCase(),
        style: AppTextStyles.nano.copyWith(
          color: color,
          fontWeight: FontWeight.w700,
          letterSpacing: 0.5,
        ),
      ),
    );
  }
}

/// Full-screen / inline loading state.
class LoadingState extends StatelessWidget {
  const LoadingState({super.key, this.label = 'Loading…'});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const SizedBox(
            width: 18,
            height: 18,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
          const SizedBox(width: 12),
          Text(label, style: AppTextStyles.body),
        ],
      ),
    );
  }
}

/// Big empty state: muted icon + headline + subtext + optional CTA.
class EmptyState extends StatelessWidget {
  const EmptyState({
    super.key,
    required this.icon,
    required this.title,
    this.subtitle,
    this.actionLabel,
    this.onAction,
  });

  final IconData icon;
  final String title;
  final String? subtitle;
  final String? actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      borderColor: AppColors.border,
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 48),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 48, color: AppColors.textFaint),
          const SizedBox(height: 16),
          Text(title,
              textAlign: TextAlign.center,
              style: AppTextStyles.cardTitle.copyWith(color: AppColors.textMd)),
          if (subtitle != null) ...[
            const SizedBox(height: 8),
            Text(subtitle!,
                textAlign: TextAlign.center,
                style: AppTextStyles.meta.copyWith(color: AppColors.textDim)),
          ],
          if (actionLabel != null && onAction != null) ...[
            const SizedBox(height: 20),
            AppButton.primary(label: actionLabel!, onPressed: onAction),
          ],
        ],
      ),
    );
  }
}

/// Inline error banner.
class ErrorBanner extends StatelessWidget {
  const ErrorBanner({super.key, required this.message, this.onRetry});

  final String message;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.fill(AppColors.danger),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.stroke(AppColors.danger)),
      ),
      child: Row(
        children: [
          const Icon(Icons.error_outline, color: AppColors.danger, size: 20),
          const SizedBox(width: 12),
          Expanded(
            child: Text(message,
                style: AppTextStyles.body.copyWith(color: AppColors.danger)),
          ),
          if (onRetry != null)
            TextButton(onPressed: onRetry, child: const Text('Retry')),
        ],
      ),
    );
  }
}

/// A small colored icon tile used in card headers.
class IconTile extends StatelessWidget {
  const IconTile({
    super.key,
    required this.icon,
    this.color = AppColors.primary,
    this.size = 40,
  });

  final IconData icon;
  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: AppColors.fill(color),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.stroke(color)),
      ),
      child: Icon(icon, color: color, size: size * 0.5),
    );
  }
}
